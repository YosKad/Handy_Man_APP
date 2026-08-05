"""Gateway behaviour: routing, retry, repair, fallback, cost ceiling, ledger."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal

import pytest

from app.core.errors import (
    CostCeilingExceeded,
    Forbidden,
    ProviderContractError,
    ProviderUnavailable,
)
from app.features.vision.domain.entities import Field, MediaRole
from app.infrastructure.ai.adapters.fake import FakeAdapter
from app.infrastructure.ai.gateway import AIGateway, CallContext, GatewayConfig
from app.infrastructure.ai.models import (
    AiCallRecord,
    Capability,
    ChatChunk,
    ChatRequest,
    ChatTurn,
    ChunkKind,
    FastenerDirective,
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
from app.shared.values import SafetyClass, Workflow

FAST_CONFIG = GatewayConfig(timeout_seconds=5.0, max_retries=1)


class RecordingLedger:
    def __init__(self) -> None:
        self.records: list[AiCallRecord] = []

    async def record(self, call: AiCallRecord) -> None:
        self.records.append(call)


class FlakyAdapter:
    """Fails a given number of times, then succeeds. Vision only."""

    def __init__(self, failures: int, *, error: Exception | None = None) -> None:
        self._remaining = failures
        self._error = error or ProviderUnavailable(log_detail="synthetic outage")
        self.calls = 0

    @property
    def name(self) -> str:
        return "flaky"

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.VISION, Capability.STRUCTURED_OUTPUT})

    def model_for(self, capability: Capability, tier: Tier) -> str:
        return "flaky-model"

    async def analyze(self, request: VisionRequest) -> tuple[VisionResult, Usage]:
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise self._error
        return VisionResult(findings=()), Usage(cost_usd=Decimal("0.01"))

    async def generate_guide(self, request: GuideRequest) -> tuple[GuideDraft, Usage]:
        self.calls += 1
        raise self._error

    async def validate_step(
        self, request: StepValidationRequest
    ) -> tuple[StepValidationResult, Usage]:
        raise NotImplementedError

    def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        raise NotImplementedError

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[tuple[float, ...], ...], Usage]:
        raise NotImplementedError


class SlowAdapter(FlakyAdapter):
    """Exceeds the gateway timeout."""

    async def analyze(self, request: VisionRequest) -> tuple[VisionResult, Usage]:
        self.calls += 1
        await asyncio.sleep(10)
        return VisionResult(findings=()), Usage()


def _image() -> ImageInput:
    return ImageInput(
        media_id=uuid.uuid4(),
        data=b"not-a-real-jpeg",
        content_type="image/jpeg",
        role=MediaRole.PRODUCT,
        width=1200,
        height=900,
    )


def _vision_request(intent: str = "install this TV on drywall") -> VisionRequest:
    return VisionRequest(
        workflow=Workflow.INSTALL,
        images=(_image(),),
        intent_text=intent,
        requested_fields=(Field.PRODUCT_WEIGHT_KG.value,),
    )


def _guide_request() -> GuideRequest:
    return GuideRequest(
        workflow=Workflow.INSTALL,
        intent_text="Mount the TV",
        facts={"product.weight_kg": 15.4},
        safety_class=SafetyClass.YELLOW,
        safety_rationale="Heavy for this wall.",
    )


# ------------------------------------------------------------------ happy path


async def test_vision_returns_findings_and_records_one_ledger_row() -> None:
    ledger = RecordingLedger()
    gateway = AIGateway([FakeAdapter()], config=FAST_CONFIG, ledger=ledger)

    result = await gateway.analyze(
        _vision_request(), CallContext(user_id=uuid.uuid4(), project_id=uuid.uuid4())
    )

    assert result.findings
    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.provider == "fake"
    assert record.status == "succeeded"
    assert record.usage.latency_ms > 0
    assert record.usage.cost_usd > 0


async def test_fake_adapter_is_deterministic() -> None:
    """Same intent and same media ids must produce the same analysis."""
    gateway = AIGateway([FakeAdapter()], config=FAST_CONFIG)
    request = _vision_request()

    first = await gateway.analyze(request)
    second = await gateway.analyze(request)

    def snapshot(result: VisionResult) -> list[tuple[str, object, float]]:
        return sorted((f.field.value, f.value, f.confidence.value) for f in result.findings)

    assert snapshot(first) == snapshot(second)


async def test_guide_generation_produces_verifiable_steps() -> None:
    gateway = AIGateway([FakeAdapter()], config=FAST_CONFIG)
    draft = await gateway.generate_guide(_guide_request())

    assert draft.steps
    assert [step.ordinal for step in draft.steps] == list(range(1, len(draft.steps) + 1))
    # Every step must tell the user how to know it worked.
    assert all(step.verification.strip() for step in draft.steps)


async def test_guide_uses_the_fastener_the_rules_chose() -> None:
    """The model phrases the fixing; it does not get to pick a different one."""
    request = GuideRequest(
        workflow=Workflow.INSTALL,
        intent_text="Mount the TV",
        facts={},
        safety_class=SafetyClass.YELLOW,
        safety_rationale="Heavy for this wall.",
        fastener=FastenerDirective(
            key="drywall_metal_toggle",
            display_name="Spring toggle",
            pullout_kg_typical=18.0,
            requires_pilot_hole=True,
            pilot_diameter_mm=12.0,
            notes="Spreads the load",
            citation="reference table",
        ),
    )
    gateway = AIGateway([FakeAdapter()], config=FAST_CONFIG)
    draft = await gateway.generate_guide(request)

    assert any(
        "Spring toggle" in step.title or "Spring toggle" in step.body for step in draft.steps
    )
    assert draft.materials[0].attributes["key"] == "drywall_metal_toggle"


async def test_embeddings_are_unit_normalised() -> None:
    gateway = AIGateway([FakeAdapter()], config=FAST_CONFIG)
    vectors = await gateway.embed(["drywall anchor", "drywall anchor", "concrete anchor"])

    assert len(vectors) == 3
    assert vectors[0] == vectors[1]  # deterministic on content
    assert vectors[0] != vectors[2]
    magnitude = sum(value * value for value in vectors[0]) ** 0.5
    assert magnitude == pytest.approx(1.0, abs=1e-6)


async def test_chat_streams_deltas_then_done() -> None:
    gateway = AIGateway([FakeAdapter()], config=FAST_CONFIG)
    request = ChatRequest(
        system_context="Project: mount a TV. Current step 3.",
        history=(),
        message=ChatTurn(role="user", content="Is this the right screw?"),
    )

    chunks = [chunk async for chunk in gateway.chat_stream(request)]

    assert chunks[-1].kind is ChunkKind.DONE
    assert any(chunk.kind is ChunkKind.DELTA for chunk in chunks)
    assert "".join(c.text for c in chunks if c.kind is ChunkKind.DELTA).strip()


# ------------------------------------------------------------------ resilience


async def test_transient_failure_is_retried() -> None:
    adapter = FlakyAdapter(failures=1)
    gateway = AIGateway([adapter], config=GatewayConfig(timeout_seconds=5, max_retries=2))

    await gateway.analyze(_vision_request())

    assert adapter.calls == 2


async def test_exhausted_retries_raise_provider_unavailable() -> None:
    adapter = FlakyAdapter(failures=99)
    ledger = RecordingLedger()
    gateway = AIGateway(
        [adapter], config=GatewayConfig(timeout_seconds=5, max_retries=1), ledger=ledger
    )

    with pytest.raises(ProviderUnavailable):
        await gateway.analyze(_vision_request())

    assert adapter.calls == 2
    assert all(record.status == "failed" for record in ledger.records)


async def test_falls_back_to_the_next_provider() -> None:
    broken = FlakyAdapter(failures=99)
    gateway = AIGateway([broken, FakeAdapter()], config=GatewayConfig(max_retries=0))

    result = await gateway.analyze(_vision_request())

    assert result.findings  # served by the fallback
    assert broken.calls == 1


async def test_contract_error_gets_one_repair_attempt_then_moves_on() -> None:
    """Two attempts on the offending adapter (original + repair), then fallback."""
    ledger = RecordingLedger()
    gateway = AIGateway(
        [FakeAdapter(fail_contract=True), FakeAdapter()],
        config=GatewayConfig(max_retries=3, repair_attempts=1),
        ledger=ledger,
    )

    result = await gateway.analyze(_vision_request())

    assert result.findings
    contract_failures = [r for r in ledger.records if r.status == "contract_error"]
    assert len(contract_failures) == 2


async def test_all_providers_failing_contract_raises_contract_error() -> None:
    gateway = AIGateway([FakeAdapter(fail_contract=True)], config=GatewayConfig(max_retries=2))

    with pytest.raises(ProviderContractError):
        await gateway.analyze(_vision_request())


async def test_timeout_is_recorded_and_retried() -> None:
    adapter = SlowAdapter(failures=0)
    ledger = RecordingLedger()
    gateway = AIGateway(
        [adapter], config=GatewayConfig(timeout_seconds=0.05, max_retries=1), ledger=ledger
    )

    with pytest.raises(ProviderUnavailable):
        await gateway.analyze(_vision_request())

    assert adapter.calls == 2
    assert all(record.status == "timeout" for record in ledger.records)


async def test_non_retryable_error_is_not_retried() -> None:
    adapter = FlakyAdapter(failures=99, error=Forbidden())
    gateway = AIGateway([adapter], config=GatewayConfig(max_retries=3))

    with pytest.raises(Forbidden):
        await gateway.analyze(_vision_request())

    assert adapter.calls == 1


async def test_missing_capability_raises_rather_than_guessing() -> None:
    gateway = AIGateway([FlakyAdapter(failures=0)], config=FAST_CONFIG)

    with pytest.raises(ProviderUnavailable):
        await gateway.embed(["anything"])


async def test_cost_ceiling_blocks_before_the_call() -> None:
    adapter = FlakyAdapter(failures=0)
    gateway = AIGateway(
        [adapter],
        config=GatewayConfig(cost_ceiling_usd=0.001),  # below the pre-flight estimate
    )

    with pytest.raises(CostCeilingExceeded):
        await gateway.analyze(_vision_request())

    assert adapter.calls == 0


async def test_ledger_failure_does_not_break_the_request() -> None:
    """Accounting is important, but not more important than serving the user."""

    class BrokenLedger:
        async def record(self, call: AiCallRecord) -> None:
            raise RuntimeError("ledger is down")

    gateway = AIGateway([FakeAdapter()], config=FAST_CONFIG, ledger=BrokenLedger())

    result = await gateway.analyze(_vision_request())

    assert result.findings


def test_gateway_requires_at_least_one_adapter() -> None:
    with pytest.raises(ValueError, match="at least one AI adapter"):
        AIGateway([])


# ------------------------------------------------------------------ step validation


async def test_step_validation_can_report_unsure() -> None:
    """'I can't tell from this photo' must be reachable, not just ok/problem."""
    gateway = AIGateway([FakeAdapter()], config=FAST_CONFIG)
    verdicts = set()

    for index in range(40):
        request = StepValidationRequest(
            step_title=f"Step {index}",
            step_body="Fit the bracket",
            step_verification="It does not move",
            evidence=_image(),
            facts={},
        )
        result = await gateway.validate_step(request)
        verdicts.add(result.verdict)

    assert len(verdicts) > 1


async def test_high_severity_problem_blocks_progress() -> None:
    blocking = StepValidationResult(
        verdict=Verdict.PROBLEM,
        observations=(),
        issues=(StepIssue(severity="high", description="Not level", fix="Re-level it"),),
        confidence=0.8,
    )
    assert blocking.blocks_progress is True

    minor = StepValidationResult(
        verdict=Verdict.PROBLEM,
        observations=(),
        issues=(StepIssue(severity="low", description="Slightly off-centre", fix="Nudge it"),),
        confidence=0.8,
    )
    assert minor.blocks_progress is False

    unsure = StepValidationResult(
        verdict=Verdict.UNSURE, observations=(), issues=(), confidence=0.3
    )
    assert unsure.blocks_progress is False
