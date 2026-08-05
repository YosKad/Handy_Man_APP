"""Analysis orchestration: the confidence gate and the clarification loop.

This is the service that decides whether we know enough to give someone
instructions. It implements ``docs/06-ai-pipeline.md`` §3-§5 and is deliberately
free of I/O: it takes an analysis and returns a decision, so every branch is
unit-testable without a provider, a database, or a network.

The rule it exists to enforce: **a safety-critical field is never filled by
inference.** When one is missing we ask for a specific photo, then ask the user
directly, and if both fail we escalate the safety classification rather than
guessing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.logging import get_logger
from app.features.knowledge.reference.materials import (
    MATERIALS,
    WallMaterial,
    parse_material,
)
from app.features.vision.domain.entities import (
    REMEDY_FOR_FIELD,
    Analysis,
    AnalysisStatus,
    Clarification,
    Field,
    Finding,
    FindingSource,
    MediaRequest,
)
from app.shared.values import Confidence, Workflow

logger = get_logger(__name__)

#: A user asked for a third photo of the same wall has already lost patience.
#: After this many rounds we ask them the question directly instead.
MAX_GATE_ROUNDS = 2

#: Fields each workflow genuinely needs before a guide can be written.
REQUIRED_FIELDS: dict[Workflow, tuple[Field, ...]] = {
    Workflow.INSTALL: (
        Field.PRODUCT_CATEGORY,
        Field.PRODUCT_WEIGHT_KG,
        Field.WALL_MATERIAL,
        Field.WALL_OBSTACLES,
    ),
    Workflow.REPAIR: (
        Field.PRODUCT_CATEGORY,
        Field.DAMAGE_PRESENT,
        Field.DAMAGE_DESCRIPTION,
    ),
    Workflow.ADVISE: (Field.PRODUCT_CATEGORY,),
}

#: Direct questions used when another photo will not settle the matter — either
#: because the fact is not visible at all, or because we have already asked.
QUESTION_FOR_FIELD: dict[Field, tuple[str, str, tuple[str, ...]]] = {
    Field.PRODUCT_WEIGHT_KG: (
        "How heavy is it? It's usually on the box or in the manual.",
        "The weight decides which anchor is safe — we won't guess it.",
        (),
    ),
    Field.WALL_MATERIAL: (
        "Tap the wall firmly. Does it sound hollow or solid?",
        "Hollow means a cavity wall needing an anchor; solid means masonry.",
        ("Hollow", "Solid", "Not sure"),
    ),
    Field.MOUNT_MAX_LOAD_KG: (
        "What weight is the mount rated for?",
        "We check the mount can carry your product with margin to spare.",
        (),
    ),
    Field.WALL_OBSTACLES: (
        "Are there sockets, switches or pipes near where this is going?",
        "We route the drilling away from anything buried in the wall.",
        ("Yes", "No", "Not sure"),
    ),
    Field.SITE_OUTLET_PRESENT: (
        "Is there a power outlet behind or near the mounting position?",
        "It changes the cable-management step.",
        ("Yes", "No"),
    ),
}

#: Free-text answers mapped onto the controlled material vocabulary. Anything
#: else stays unknown — see ``parse_material``.
_SOUND_TO_MATERIAL: dict[str, WallMaterial] = {
    "hollow": WallMaterial.DRYWALL,
    "solid": WallMaterial.BRICK,
}


class Decision(StrEnum):
    """What the pipeline should do next."""

    PROCEED = "proceed"
    NEED_MEDIA = "need_media"
    NEED_ANSWER = "need_answer"


@dataclass(frozen=True, slots=True)
class GateResult:
    decision: Decision
    media_requests: tuple[MediaRequest, ...] = ()
    clarifications: tuple[Clarification, ...] = ()
    #: Safety-critical fields still unresolved when we proceed anyway. The safety
    #: classifier reads these and escalates; they are named in the guide.
    unresolved_safety_fields: tuple[Field, ...] = ()

    @property
    def blocks_generation(self) -> bool:
        return self.decision is not Decision.PROCEED


@dataclass(slots=True)
class AnalysisService:
    """Pure orchestration over an ``Analysis``. No I/O, no framework."""

    max_gate_rounds: int = MAX_GATE_ROUNDS

    # ---------------------------------------------------------------- gate

    def evaluate(self, analysis: Analysis, workflow: Workflow) -> GateResult:
        """Decide whether we know enough, and if not, what to ask for.

        Round 1-2 ask for specific photos. After that we ask the user directly,
        because an unbounded "one more photo" loop is a worse failure than a
        question.
        """
        required = REQUIRED_FIELDS.get(workflow, ())
        failing = analysis.failing_fields(required)
        # A field can be non-required but still safety-critical and shaky — e.g. a
        # mount rating the model read at low confidence.
        failing = tuple(dict.fromkeys([*failing, *analysis.uncertain_safety_fields()]))

        if not failing:
            return GateResult(decision=Decision.PROCEED)

        if analysis.gate_rounds < self.max_gate_rounds:
            requests = self._media_requests_for(failing, analysis)
            if requests:
                return GateResult(decision=Decision.NEED_MEDIA, media_requests=requests)

        questions = self._questions_for(failing, analysis)
        if questions:
            return GateResult(decision=Decision.NEED_ANSWER, clarifications=questions)

        # Nothing left to ask: proceed, but hand the unresolved safety-critical
        # fields to the classifier so it can escalate rather than pretending.
        unresolved = tuple(f for f in failing if f in _safety_critical_set())
        if unresolved:
            logger.info(
                "proceeding with unresolved safety fields",
                extra={"fields": [f.value for f in unresolved]},
            )
        return GateResult(decision=Decision.PROCEED, unresolved_safety_fields=unresolved)

    def apply(self, analysis: Analysis, result: GateResult) -> Analysis:
        """Record a gate outcome on the analysis."""
        analysis.media_requests = result.media_requests
        analysis.clarifications = result.clarifications
        if result.decision is Decision.NEED_MEDIA:
            analysis.status = AnalysisStatus.NEEDS_MEDIA
            analysis.gate_rounds += 1
        elif result.decision is Decision.NEED_ANSWER:
            analysis.status = AnalysisStatus.NEEDS_ANSWER
        else:
            analysis.status = AnalysisStatus.COMPLETE
        return analysis

    def _media_requests_for(
        self, failing: Sequence[Field], analysis: Analysis
    ) -> tuple[MediaRequest, ...]:
        """One request per field that a photo could plausibly answer.

        Skipped when we already asked for that role and the field is still weak —
        a second identical request will not produce a better photo.
        """
        already_requested = {request.field for request in analysis.media_requests}
        requests: list[MediaRequest] = []
        for field_key in failing:
            remedy = REMEDY_FOR_FIELD.get(field_key)
            if remedy is None or field_key in already_requested:
                continue
            role, prompt, reason = remedy
            requests.append(MediaRequest(field=field_key, role=role, prompt=prompt, reason=reason))
        # Three photo requests at once is a chore, not a conversation.
        return tuple(requests[:3])

    def _questions_for(
        self, failing: Sequence[Field], analysis: Analysis
    ) -> tuple[Clarification, ...]:
        asked = {clarification.field for clarification in analysis.clarifications}
        questions: list[Clarification] = []
        for field_key in failing:
            entry = QUESTION_FOR_FIELD.get(field_key)
            if entry is None or field_key in asked:
                continue
            question, reason, options = entry
            questions.append(
                Clarification(
                    id=uuid.uuid4(),
                    field=field_key,
                    question=question,
                    reason=reason,
                    options=options,
                )
            )
        # docs/01 F-4: at most three questions before generating a guide.
        return tuple(questions[:3])

    # ---------------------------------------------------------------- answers

    def record_answer(
        self, analysis: Analysis, clarification_id: uuid.UUID, answer: str
    ) -> Finding | None:
        """Fold a user's answer into the analysis as a certain finding.

        Returns the finding created, or ``None`` if the answer was "not sure" —
        which is information, not a value, and must not become one.
        """
        clarification = next((c for c in analysis.clarifications if c.id == clarification_id), None)
        if clarification is None:
            return None

        analysis.clarifications = tuple(
            Clarification(
                id=c.id,
                field=c.field,
                question=c.question,
                reason=c.reason,
                options=c.options,
                answer=answer if c.id == clarification_id else c.answer,
            )
            for c in analysis.clarifications
        )

        value = self._coerce_answer(clarification.field, answer)
        if value is None:
            return None
        return analysis.correct(clarification.field, value)

    def _coerce_answer(self, field_key: Field, answer: str) -> object | None:
        """Map a free-text answer onto the field's type, or refuse."""
        cleaned = answer.strip().lower()
        if not cleaned or cleaned in {"not sure", "unsure", "i don't know", "dunno"}:
            return None

        if field_key is Field.WALL_MATERIAL:
            mapped = _SOUND_TO_MATERIAL.get(cleaned) or parse_material(cleaned)
            return None if mapped is WallMaterial.UNKNOWN else mapped.value

        if field_key in _NUMERIC_FIELDS:
            digits = "".join(ch for ch in cleaned if ch.isdigit() or ch == ".")
            try:
                number = float(digits)
            except ValueError:
                return None
            # "kg" vs "lb": convert rather than silently treating pounds as kilos.
            if "lb" in cleaned or "pound" in cleaned:
                number *= 0.453592
            return round(number, 2) if number > 0 else None

        if field_key in _BOOLEAN_FIELDS:
            if cleaned in {"yes", "y", "true"}:
                return True
            if cleaned in {"no", "n", "false"}:
                return False
            return None

        if field_key is Field.WALL_OBSTACLES:
            return [answer.strip()]

        return answer.strip()

    # ---------------------------------------------------------------- prompt input

    def material_hints(self) -> tuple[str, ...]:
        """Visual cues from the reference table, fed to the vision prompt.

        Grounding identification in curated cues is what keeps `wall.material`
        from being a vibe — and that field selects the anchor.
        """
        hints: list[str] = []
        for spec in MATERIALS.values():
            if spec.key is WallMaterial.UNKNOWN or not spec.visual_cues:
                continue
            cues = "; ".join(spec.visual_cues)
            hints.append(f"{spec.key.value} ({spec.display_name}): {cues}")
        return tuple(hints)

    def requested_fields(self, workflow: Workflow) -> tuple[str, ...]:
        """Every field the vision call should attempt, required ones first."""
        required = REQUIRED_FIELDS.get(workflow, ())
        ordered = [*required, *(f for f in Field if f not in required)]
        return tuple(field_key.value for field_key in ordered)

    def facts_for_guide(self, analysis: Analysis) -> dict[str, object]:
        """Confirmed facts, flattened for the guide prompt.

        Only findings that met their threshold or came from the user: an
        unreliable reading must not reach the guide as though it were solid.
        """
        facts: dict[str, object] = {}
        for finding in analysis.findings.values():
            if finding.source is FindingSource.USER or finding.meets_threshold:
                facts[finding.field.value] = (
                    f"{finding.value} {finding.unit}" if finding.unit else finding.value
                )
        return facts


_NUMERIC_FIELDS = frozenset(
    {
        Field.PRODUCT_WEIGHT_KG,
        Field.MOUNT_MAX_LOAD_KG,
        Field.PRODUCT_SIZE_INCHES,
        Field.WALL_THICKNESS_MM,
        Field.TASK_WORKING_HEIGHT_M,
    }
)

_BOOLEAN_FIELDS = frozenset(
    {
        Field.SITE_OUTLET_PRESENT,
        Field.MOUNT_PRESENT,
        Field.DAMAGE_PRESENT,
        Field.TASK_INVOLVES_ELECTRICAL,
        Field.TASK_INVOLVES_GAS,
        Field.TASK_INVOLVES_PLUMBING,
        Field.TASK_INVOLVES_STRUCTURAL,
        Field.TASK_IS_OVERHEAD,
    }
)


def _safety_critical_set() -> frozenset[Field]:
    from app.features.vision.domain.entities import SAFETY_CRITICAL_FIELDS

    return SAFETY_CRITICAL_FIELDS


@dataclass(slots=True)
class AnalysisBuilder:
    """Assembles an ``Analysis`` from a provider result.

    Kept separate from the gate so provider-shaped input is normalised in exactly
    one place.
    """

    project_id: uuid.UUID
    revision: int = 1
    _analysis: Analysis = field(init=False)

    def __post_init__(self) -> None:
        self._analysis = Analysis(
            id=uuid.uuid4(), project_id=self.project_id, revision=self.revision
        )

    def add_findings(self, findings: Sequence[Finding]) -> AnalysisBuilder:
        self._analysis.add_all(findings)
        return self

    def normalise(self) -> AnalysisBuilder:
        """Map free text onto controlled vocabularies.

        An unmappable value becomes unknown with zero confidence rather than the
        nearest match: the anchor table trusts ``wall.material``, so a wrong
        value there is worse than an admitted gap.
        """
        raw = self._analysis.get(Field.WALL_MATERIAL)
        if raw is not None and raw.is_known and raw.source is not FindingSource.USER:
            material = parse_material(raw.value)
            self._analysis.findings[Field.WALL_MATERIAL] = Finding(
                field=Field.WALL_MATERIAL,
                value=None if material is WallMaterial.UNKNOWN else material.value,
                confidence=(
                    Confidence.unknown() if material is WallMaterial.UNKNOWN else raw.confidence
                ),
                source=raw.source,
                evidence_media_id=raw.evidence_media_id,
                bbox=raw.bbox,
                note=(
                    f"Could not map {raw.value!r} to a known wall type"
                    if material is WallMaterial.UNKNOWN
                    else raw.note
                ),
            )
        return self

    def with_provenance(
        self, provider: str, model: str, prompt_version: str, knowledge_coverage: str
    ) -> AnalysisBuilder:
        self._analysis.provider = provider
        self._analysis.model = model
        self._analysis.prompt_version = prompt_version
        self._analysis.knowledge_coverage = knowledge_coverage
        return self

    def build(self) -> Analysis:
        return self._analysis
