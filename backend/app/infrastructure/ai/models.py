"""Provider-agnostic request and response types.

These are the *only* types that cross the boundary between features and AI
adapters. No feature ever sees a provider SDK object, and no adapter ever sees a
database row.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from app.features.guides.domain.entities import Annotation, GuideStep, Material, SafetyCheck, Tool
from app.features.vision.domain.entities import Finding, MediaRole
from app.shared.values import Difficulty, SafetyClass, UnitSystem, Workflow


class Capability(StrEnum):
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    IMAGE_EDIT = "image_edit"
    EMBEDDING = "embedding"


class Tier(StrEnum):
    """Quality/price tier. Lets deployments route cheap work to cheap models."""

    FAST = "fast"
    BALANCED = "balanced"
    DEEP = "deep"


@dataclass(frozen=True, slots=True)
class ImageInput:
    """An image on its way to a provider.

    ``data`` is already preprocessed: EXIF stripped, downscaled, re-encoded
    (``docs/06-ai-pipeline.md`` §1). Adapters must not resize or re-read metadata.
    """

    media_id: uuid.UUID
    data: bytes
    content_type: str
    role: MediaRole
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class VisionRequest:
    workflow: Workflow
    images: tuple[ImageInput, ...]
    intent_text: str
    requested_fields: tuple[str, ...]
    unit_system: UnitSystem = UnitSystem.METRIC
    #: Cues from the materials reference table, so identification is grounded.
    material_hints: tuple[str, ...] = ()
    tier: Tier = Tier.BALANCED

    def __post_init__(self) -> None:
        if not self.images:
            raise ValueError("vision requires at least one image")


@dataclass(frozen=True, slots=True)
class VisionResult:
    findings: tuple[Finding, ...]
    annotations_by_media: Mapping[uuid.UUID, tuple[Annotation, ...]] = field(default_factory=dict)
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeSnippet:
    """Retrieved context, always quoted with a citation the guide can cite back."""

    content: str
    citation: str
    similarity: float


@dataclass(frozen=True, slots=True)
class FastenerDirective:
    """The fixing the *rules* chose. The model phrases it; it does not pick it."""

    key: str
    display_name: str
    pullout_kg_typical: float
    requires_pilot_hole: bool
    pilot_diameter_mm: float | None
    notes: str
    citation: str


@dataclass(frozen=True, slots=True)
class GuideRequest:
    workflow: Workflow
    intent_text: str
    #: Confirmed findings rendered as ``field -> value`` for the prompt.
    facts: dict[str, Any]
    #: Supplied by the deterministic classifier. The model may not contradict it.
    safety_class: SafetyClass
    safety_rationale: str
    safety_checks: tuple[SafetyCheck, ...] = ()
    fastener: FastenerDirective | None = None
    knowledge: tuple[KnowledgeSnippet, ...] = ()
    unit_system: UnitSystem = UnitSystem.METRIC
    locale: str = "en-US"
    uncertain_fields: tuple[str, ...] = ()
    tier: Tier = Tier.DEEP


@dataclass(frozen=True, slots=True)
class GuideDraft:
    """Validated model output. Constructing one implies the schema check passed."""

    title: str
    summary: str
    difficulty: Difficulty
    steps: tuple[GuideStep, ...]
    tools: tuple[Tool, ...]
    materials: tuple[Material, ...]
    prerequisites: tuple[str, ...] = ()
    common_mistakes: tuple[str, ...] = ()
    final_verification: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatTurn:
    role: str  # "user" | "assistant"
    content: str
    images: tuple[ImageInput, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """Assembled server-side. The client never supplies a system prompt.

    ``system_context`` is built from the project's analysis, guide and retrieved
    knowledge, in the priority order in ``docs/06-ai-pipeline.md`` §3.
    """

    system_context: str
    history: tuple[ChatTurn, ...]
    message: ChatTurn
    safety_class: SafetyClass = SafetyClass.GREEN
    max_output_tokens: int = 1024
    tier: Tier = Tier.BALANCED


class ChunkKind(StrEnum):
    DELTA = "delta"
    REFERENCE = "reference"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class ChatChunk:
    kind: ChunkKind
    text: str = ""
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class StepValidationRequest:
    """ "Does this look right?" — the in-progress check on the user's own work."""

    step_title: str
    step_body: str
    step_verification: str
    evidence: ImageInput
    facts: dict[str, Any]


class Verdict(StrEnum):
    OK = "ok"
    UNSURE = "unsure"
    PROBLEM = "problem"


@dataclass(frozen=True, slots=True)
class StepIssue:
    severity: str  # low | medium | high
    description: str
    fix: str


@dataclass(frozen=True, slots=True)
class StepValidationResult:
    verdict: Verdict
    observations: tuple[str, ...]
    issues: tuple[StepIssue, ...]
    confidence: float

    @property
    def blocks_progress(self) -> bool:
        return self.verdict is Verdict.PROBLEM and any(
            issue.severity == "high" for issue in self.issues
        )


@dataclass(frozen=True, slots=True)
class Usage:
    """What a single provider call consumed. One ledger row per call."""

    input_tokens: int = 0
    output_tokens: int = 0
    image_count: int = 0
    cost_usd: Decimal = Decimal("0")
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class AiCallRecord:
    provider: str
    model: str
    purpose: str
    prompt_version: str
    usage: Usage
    status: str
    attempt: int = 1
    error_code: str | None = None
    project_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


class AiCallLedger(Protocol):
    """Where cost and provenance are recorded. Implemented over Postgres."""

    async def record(self, call: AiCallRecord) -> None: ...


# ------------------------------------------------------------------ adapter port


class AIAdapter(Protocol):
    """What every provider adapter implements.

    An adapter is responsible for validating its own output against these domain
    types *before returning*. Returning a value implies the contract held; raising
    ``ProviderContractError`` implies it did not.
    """

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[Capability]: ...

    def model_for(self, capability: Capability, tier: Tier) -> str: ...

    async def analyze(self, request: VisionRequest) -> tuple[VisionResult, Usage]: ...

    async def generate_guide(self, request: GuideRequest) -> tuple[GuideDraft, Usage]: ...

    async def validate_step(
        self, request: StepValidationRequest
    ) -> tuple[StepValidationResult, Usage]: ...

    def chat_stream(self, request: ChatRequest) -> Any: ...

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[tuple[float, ...], ...], Usage]: ...
