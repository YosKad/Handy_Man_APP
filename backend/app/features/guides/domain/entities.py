"""Guide domain: the artefact the user actually follows.

Guides are immutable once started. Regeneration produces a new revision; the
revision the user is halfway through never changes under them
(``docs/01-technical-specification.md`` §8.3).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from app.shared.values import Difficulty, SafetyClass

SCHEMA_VERSION = 1


class StepState(StrEnum):
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ToolOwnership(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    ALTERNATIVE = "alternative"


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    ownership: ToolOwnership = ToolOwnership.REQUIRED
    why: str | None = None
    substitute: str | None = None


@dataclass(frozen=True, slots=True)
class Material:
    """A material specified as a product *class* plus attributes, never a SKU.

    This is what lets retailer and affiliate integrations attach SKUs later
    without touching the domain model — and keeps advice retailer-neutral today.
    """

    product_class: str
    display_name: str
    quantity: int
    attributes: dict[str, str] = field(default_factory=dict)
    citation: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class SafetyCheck:
    """A pre-flight check the user acknowledges before starting."""

    code: str
    title: str
    detail: str
    is_blocking: bool = False


@dataclass(frozen=True, slots=True)
class Annotation:
    """A drawing instruction for the deterministic overlay renderer.

    The vision model returns geometry and labels; we draw them ourselves. See
    ``docs/06-ai-pipeline.md`` §8 for why this is the default over generative
    editing.
    """

    kind: str  # arrow | box | dot | measure | label
    label: str
    alt_text: str
    points: tuple[tuple[float, float], ...]  # normalised image coordinates
    color_token: str = "accent.primary"  # noqa: S105 - design token name


@dataclass(frozen=True, slots=True)
class GuideStep:
    ordinal: int
    title: str
    body: str
    verification: str
    duration_minutes: int
    tools: tuple[str, ...] = ()
    safety_note: str | None = None
    is_optional: bool = False
    source_media_id: uuid.UUID | None = None
    annotated_media_id: uuid.UUID | None = None
    annotations: tuple[Annotation, ...] = ()
    #: Reserved for AR: pose/plane anchors keyed by name. Populated in v2.
    spatial_anchors: dict[str, object] = field(default_factory=dict)
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        if self.ordinal < 1:
            raise ValueError("step ordinals start at 1")
        if not self.verification.strip():
            raise ValueError(f"step {self.ordinal} has no verification: how does the user know?")


@dataclass(slots=True)
class Guide:
    id: uuid.UUID
    project_id: uuid.UUID
    analysis_id: uuid.UUID
    title: str
    summary: str
    steps: tuple[GuideStep, ...]
    safety_class: SafetyClass
    safety_rationale: str
    difficulty: Difficulty = Difficulty.MODERATE
    tools: tuple[Tool, ...] = ()
    materials: tuple[Material, ...] = ()
    safety_checks: tuple[SafetyCheck, ...] = ()
    prerequisites: tuple[str, ...] = ()
    common_mistakes: tuple[str, ...] = ()
    final_verification: tuple[str, ...] = ()
    revision: int = 1
    schema_version: int = SCHEMA_VERSION
    is_immutable: bool = False
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def estimated_minutes(self) -> int:
        return sum(step.duration_minutes for step in self.steps)

    @property
    def is_read_only(self) -> bool:
        """Red-classified guides are informational: no step-by-step execution."""
        return self.safety_class is SafetyClass.RED

    @property
    def blocking_checks(self) -> tuple[SafetyCheck, ...]:
        return tuple(check for check in self.safety_checks if check.is_blocking)

    def step(self, step_id: uuid.UUID) -> GuideStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def freeze(self) -> None:
        """Called the moment the user completes their first step."""
        self.is_immutable = True

    def next_revision(self, steps: Sequence[GuideStep]) -> Guide:
        """A regenerated guide is a sibling revision, never a mutation."""
        return Guide(
            id=uuid.uuid4(),
            project_id=self.project_id,
            analysis_id=self.analysis_id,
            title=self.title,
            summary=self.summary,
            steps=tuple(steps),
            safety_class=self.safety_class,
            safety_rationale=self.safety_rationale,
            difficulty=self.difficulty,
            tools=self.tools,
            materials=self.materials,
            safety_checks=self.safety_checks,
            prerequisites=self.prerequisites,
            common_mistakes=self.common_mistakes,
            final_verification=self.final_verification,
            revision=self.revision + 1,
        )


@dataclass(slots=True)
class StepProgress:
    project_id: uuid.UUID
    step_id: uuid.UUID
    state: StepState = StepState.PENDING
    evidence_media_id: uuid.UUID | None = None
    validation_verdict: str | None = None
    validation_detail: str | None = None
    completed_at: datetime | None = None

    def mark(self, state: StepState) -> None:
        self.state = state
        self.completed_at = (
            datetime.now(UTC) if state in (StepState.DONE, StepState.SKIPPED) else None
        )
