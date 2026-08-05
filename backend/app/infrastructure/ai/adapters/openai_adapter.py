"""OpenAI adapter.

The only module in the codebase permitted to import the OpenAI SDK. Everything
it returns is a domain object that has already been validated against the schema
the domain expects; anything else raises ``ProviderContractError`` so the gateway
can repair or fall back.

**Verification status:** the request/response mapping here has not been exercised
against the live API in this repository — CI runs the ``fake`` adapter. Before
enabling ``AI_PROVIDER=openai`` in staging, run the nightly live-provider suite
described in ``docs/06-ai-pipeline.md`` §5 and pin the model ids in ``.env``.

Model ids are configuration, never constants in code: providers deprecate models
faster than we ship releases.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.core.errors import ProviderContractError, ProviderUnavailable
from app.core.logging import get_logger
from app.features.guides.domain.entities import (
    Annotation,
    GuideStep,
    Material,
    Tool,
    ToolOwnership,
)
from app.features.vision.domain.entities import Field, Finding, FindingSource
from app.infrastructure.ai.models import (
    Capability,
    ChatChunk,
    ChatRequest,
    ChunkKind,
    GuideDraft,
    GuideRequest,
    ImageInput,
    StepIssue,
    StepValidationRequest,
    StepValidationResult,
    Tier,
    Usage,
    Verdict,
    VisionRequest,
    VisionResult,
)
from app.infrastructure.ai.prompts import load_prompt
from app.infrastructure.ai.schemas import (
    ANALYSIS_JSON_SCHEMA,
    GUIDE_JSON_SCHEMA,
    STEP_VALIDATION_JSON_SCHEMA,
)
from app.shared.values import BoundingBox, Confidence, Difficulty

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openai import AsyncOpenAI

logger = get_logger(__name__)

#: USD per 1M tokens, per model, so the ledger records real spend rather than an
#: estimate. Overridden by configuration in deployments where pricing differs.
DEFAULT_PRICING: Mapping[str, tuple[Decimal, Decimal]] = {
    "default": (Decimal("2.50"), Decimal("10.00")),
}


class OpenAIAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        vision_model: str | None,
        text_model: str | None,
        embedding_model: str | None,
        max_edge_px: int = 1568,
        pricing: Mapping[str, tuple[Decimal, Decimal]] | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if not (vision_model and text_model):
            raise ProviderUnavailable(
                log_detail="OPENAI_VISION_MODEL and OPENAI_TEXT_MODEL must be configured"
            )
        self._vision_model = vision_model
        self._text_model = text_model
        self._embedding_model = embedding_model
        self._max_edge_px = max_edge_px
        self._pricing = pricing or DEFAULT_PRICING
        self._client = client or self._build_client(api_key)

    @staticmethod
    def _build_client(api_key: str) -> AsyncOpenAI:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderUnavailable(
                log_detail="the 'openai' extra is not installed; pip install -e '.[openai]'"
            ) from exc
        return AsyncOpenAI(api_key=api_key, max_retries=0)  # gateway owns retries

    @property
    def name(self) -> str:
        return "openai"

    @property
    def capabilities(self) -> frozenset[Capability]:
        caps = {Capability.VISION, Capability.STRUCTURED_OUTPUT, Capability.STREAMING}
        if self._embedding_model:
            caps.add(Capability.EMBEDDING)
        return frozenset(caps)

    def model_for(self, capability: Capability, tier: Tier) -> str:
        if capability is Capability.VISION:
            return self._vision_model
        if capability is Capability.EMBEDDING:
            return self._embedding_model or ""
        return self._text_model

    # -------------------------------------------------------------- vision

    async def analyze(self, request: VisionRequest) -> tuple[VisionResult, Usage]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": load_prompt("vision_analyze").format(
                    workflow=request.workflow.value,
                    intent=request.intent_text,
                    unit_system=request.unit_system.value,
                    material_hints="\n".join(f"- {hint}" for hint in request.material_hints),
                    requested_fields="\n".join(f"- {name}" for name in request.requested_fields),
                ),
            }
        ]
        for image in request.images:
            content.append({"type": "text", "text": f"Image role: {image.role.value}"})
            content.append(
                {"type": "image_url", "image_url": {"url": _data_url(image), "detail": "high"}}
            )

        payload, usage = await self._structured_call(
            model=self._vision_model,
            system=load_prompt("vision_system"),
            content=content,
            schema_name="analysis",
            schema=ANALYSIS_JSON_SCHEMA,
            image_count=len(request.images),
        )
        media_by_role = {image.role.value: image.media_id for image in request.images}
        return _parse_analysis(payload, media_by_role, request.images[0].media_id), usage

    async def validate_step(
        self, request: StepValidationRequest
    ) -> tuple[StepValidationResult, Usage]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": load_prompt("validate_step").format(
                    step_title=request.step_title,
                    step_body=request.step_body,
                    verification=request.step_verification,
                    facts=json.dumps(request.facts, default=str),
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": _data_url(request.evidence), "detail": "high"},
            },
        ]
        payload, usage = await self._structured_call(
            model=self._vision_model,
            system=load_prompt("vision_system"),
            content=content,
            schema_name="step_validation",
            schema=STEP_VALIDATION_JSON_SCHEMA,
            image_count=1,
        )
        return _parse_step_validation(payload), usage

    # -------------------------------------------------------------- guide

    async def generate_guide(self, request: GuideRequest) -> tuple[GuideDraft, Usage]:
        knowledge = "\n\n".join(
            f'[{snippet.citation}] "{snippet.content}"' for snippet in request.knowledge
        )
        fastener = (
            json.dumps(
                {
                    "display_name": request.fastener.display_name,
                    "requires_pilot_hole": request.fastener.requires_pilot_hole,
                    "pilot_diameter_mm": request.fastener.pilot_diameter_mm,
                    "notes": request.fastener.notes,
                    "citation": request.fastener.citation,
                }
            )
            if request.fastener is not None
            else "none - tell the user to follow the manufacturer's own fixing instructions"
        )
        prompt = load_prompt("generate_guide").format(
            workflow=request.workflow.value,
            intent=request.intent_text,
            facts=json.dumps(request.facts, indent=2, default=str),
            safety_class=request.safety_class.value,
            safety_rationale=request.safety_rationale,
            fastener=fastener,
            knowledge=knowledge or "none available",
            unit_system=request.unit_system.value,
            locale=request.locale,
            uncertain_fields=", ".join(request.uncertain_fields) or "none",
        )
        payload, usage = await self._structured_call(
            model=self._text_model,
            system=load_prompt("guide_system"),
            content=[{"type": "text", "text": prompt}],
            schema_name="guide",
            schema=GUIDE_JSON_SCHEMA,
            image_count=0,
        )
        return _parse_guide(payload), usage

    # -------------------------------------------------------------- chat

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": load_prompt("chat_system")},
            {"role": "system", "content": request.system_context},
        ]
        for turn in request.history:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": _turn_content(request.message)})

        try:
            stream = await self._client.chat.completions.create(
                model=self._text_model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=request.max_output_tokens,
                stream=True,
            )
            async for event in stream:
                delta = event.choices[0].delta.content if event.choices else None
                if delta:
                    yield ChatChunk(kind=ChunkKind.DELTA, text=delta)
        except Exception as exc:
            raise ProviderUnavailable(log_detail=f"openai stream failed: {exc}") from exc
        yield ChatChunk(kind=ChunkKind.DONE)

    # -------------------------------------------------------------- embeddings

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[tuple[float, ...], ...], Usage]:
        if not self._embedding_model:
            raise ProviderUnavailable(log_detail="OPENAI_EMBEDDING_MODEL is not configured")
        started = time.perf_counter()
        try:
            response = await self._client.embeddings.create(
                model=self._embedding_model, input=list(texts)
            )
        except Exception as exc:
            raise ProviderUnavailable(log_detail=f"openai embeddings failed: {exc}") from exc

        vectors = tuple(tuple(item.embedding) for item in response.data)
        usage = Usage(
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            cost_usd=Decimal("0"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return vectors, usage

    # -------------------------------------------------------------- internals

    async def _structured_call(
        self,
        *,
        model: str,
        system: str,
        content: list[dict[str, Any]],
        schema_name: str,
        schema: Mapping[str, Any],
        image_count: int,
    ) -> tuple[Mapping[str, Any], Usage]:
        """One structured-output call, returning parsed JSON and real usage."""
        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],  # type: ignore[arg-type]
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": dict(schema),
                    },
                },
            )
        except Exception as exc:
            raise ProviderUnavailable(log_detail=f"openai call failed: {exc}") from exc

        choice = response.choices[0] if response.choices else None
        if choice is None or choice.message.content is None:
            raise ProviderContractError(log_detail="openai returned no content")
        if choice.finish_reason == "length":
            # A truncated JSON body is a contract violation, not a partial success.
            raise ProviderContractError(log_detail="openai response was truncated")

        try:
            payload = json.loads(choice.message.content)
        except json.JSONDecodeError as exc:
            raise ProviderContractError(log_detail=f"openai returned invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ProviderContractError(log_detail="openai returned a non-object payload")

        input_price, output_price = self._pricing.get(model, DEFAULT_PRICING["default"])
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            image_count=image_count,
            cost_usd=(Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price)
            / Decimal(1_000_000),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return payload, usage


# ------------------------------------------------------------------ parsing
# Parsers are module-level and pure so they can be unit-tested against recorded
# payloads without an SDK or a network.


def _data_url(image: ImageInput) -> str:
    encoded = base64.b64encode(image.data).decode()
    return f"data:{image.content_type};base64,{encoded}"


def _turn_content(turn: Any) -> Any:
    if not turn.images:
        return turn.content
    parts: list[dict[str, Any]] = [{"type": "text", "text": turn.content}]
    parts.extend(
        {"type": "image_url", "image_url": {"url": _data_url(image)}} for image in turn.images
    )
    return parts


def _parse_analysis(
    payload: Mapping[str, Any],
    media_by_role: Mapping[str, Any],
    default_media_id: Any,
) -> VisionResult:
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raise ProviderContractError(log_detail="analysis payload has no findings list")

    findings: list[Finding] = []
    annotations: dict[Any, list[Annotation]] = {}

    for entry in raw_findings:
        if not isinstance(entry, dict):
            continue
        field_name = entry.get("field")
        try:
            field_key = Field(str(field_name))
        except ValueError:
            # An unknown field name means the prompt and the enum have drifted.
            logger.warning("dropping unknown analysis field", extra={"field": field_name})
            continue

        confidence_raw = entry.get("confidence", 0.0)
        try:
            confidence = Confidence(min(1.0, max(0.0, float(confidence_raw))))
        except (TypeError, ValueError) as exc:
            raise ProviderContractError(
                log_detail=f"non-numeric confidence for {field_name}: {confidence_raw!r}"
            ) from exc

        value = entry.get("value")
        media_id = media_by_role.get(str(entry.get("evidence_role", "")), default_media_id)
        bbox = _parse_bbox(entry.get("bbox"))

        findings.append(
            Finding(
                field=field_key,
                # An explicit null stays null: the contract forbids guessing, so a
                # missing value must not be coerced into something plausible.
                value=value,
                confidence=confidence if value is not None else Confidence.unknown(),
                source=FindingSource.VISION,
                unit=entry.get("unit"),
                evidence_media_id=media_id,
                bbox=bbox,
                note=entry.get("note"),
            )
        )

        if bbox is not None and entry.get("annotate"):
            annotations.setdefault(media_id, []).append(
                Annotation(
                    kind="box",
                    label=str(entry.get("label") or field_key.value),
                    alt_text=str(entry.get("alt_text") or f"Area showing {field_key.value}"),
                    points=(bbox.center,),
                )
            )

    if not findings:
        raise ProviderContractError(log_detail="analysis produced no usable findings")

    return VisionResult(
        findings=tuple(findings),
        annotations_by_media={key: tuple(value) for key, value in annotations.items()},
        notes=payload.get("notes"),
    )


def _parse_bbox(raw: Any) -> BoundingBox | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, width, height = (float(value) for value in raw)
        return BoundingBox(x, y, width, height)
    except (TypeError, ValueError):
        # A malformed box costs an annotation, not the whole analysis.
        return None


def _parse_step_validation(payload: Mapping[str, Any]) -> StepValidationResult:
    try:
        verdict = Verdict(str(payload.get("verdict")))
    except ValueError as exc:
        raise ProviderContractError(
            log_detail=f"unknown verdict {payload.get('verdict')!r}"
        ) from exc

    issues = tuple(
        StepIssue(
            severity=str(issue.get("severity", "low")),
            description=str(issue.get("description", "")),
            fix=str(issue.get("fix", "")),
        )
        for issue in payload.get("issues", [])
        if isinstance(issue, dict)
    )
    return StepValidationResult(
        verdict=verdict,
        observations=tuple(str(item) for item in payload.get("observations", [])),
        issues=issues,
        confidence=min(1.0, max(0.0, float(payload.get("confidence", 0.0)))),
    )


def _parse_guide(payload: Mapping[str, Any]) -> GuideDraft:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ProviderContractError(log_detail="guide payload has no steps")

    steps: list[GuideStep] = []
    for index, entry in enumerate(raw_steps, start=1):
        if not isinstance(entry, dict):
            raise ProviderContractError(log_detail=f"step {index} is not an object")
        verification = str(entry.get("verification", "")).strip()
        if not verification:
            # Every step must tell the user how to know it worked.
            raise ProviderContractError(log_detail=f"step {index} has no verification")
        try:
            steps.append(
                GuideStep(
                    ordinal=int(entry.get("ordinal", index)),
                    title=str(entry.get("title", "")).strip(),
                    body=str(entry.get("body", "")).strip(),
                    verification=verification,
                    duration_minutes=int(entry.get("duration_minutes", 5)),
                    tools=tuple(str(tool) for tool in entry.get("tools", [])),
                    safety_note=entry.get("safety_note"),
                    is_optional=bool(entry.get("is_optional", False)),
                )
            )
        except (TypeError, ValueError) as exc:
            raise ProviderContractError(log_detail=f"step {index} is malformed: {exc}") from exc

    ordinals = [step.ordinal for step in steps]
    if ordinals != list(range(1, len(steps) + 1)):
        raise ProviderContractError(log_detail=f"step ordinals are not contiguous: {ordinals}")

    try:
        difficulty = Difficulty(str(payload.get("difficulty", "moderate")))
    except ValueError:
        difficulty = Difficulty.MODERATE

    return GuideDraft(
        title=str(payload.get("title", "")).strip() or "Your project",
        summary=str(payload.get("summary", "")).strip(),
        difficulty=difficulty,
        steps=tuple(steps),
        tools=tuple(
            Tool(
                name=str(tool.get("name", "")),
                ownership=_tool_ownership(tool.get("ownership")),
                why=tool.get("why"),
                substitute=tool.get("substitute"),
            )
            for tool in payload.get("tools", [])
            if isinstance(tool, dict) and tool.get("name")
        ),
        materials=tuple(
            Material(
                product_class=str(material.get("product_class", "other")),
                display_name=str(material.get("display_name", "")),
                quantity=int(material.get("quantity", 1)),
                attributes={
                    str(key): str(value)
                    for key, value in (material.get("attributes") or {}).items()
                },
                citation=material.get("citation"),
                note=material.get("note"),
            )
            for material in payload.get("materials", [])
            if isinstance(material, dict) and material.get("display_name")
        ),
        prerequisites=tuple(str(item) for item in payload.get("prerequisites", [])),
        common_mistakes=tuple(str(item) for item in payload.get("common_mistakes", [])),
        final_verification=tuple(str(item) for item in payload.get("final_verification", [])),
    )


def _tool_ownership(raw: Any) -> ToolOwnership:
    try:
        return ToolOwnership(str(raw))
    except ValueError:
        return ToolOwnership.REQUIRED
