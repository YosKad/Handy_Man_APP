"""Vision domain: the structured, confidence-scored reading of a project's media.

An ``Analysis`` is a first-class entity rather than a chat side effect. One
``Finding`` per extracted fact is what makes three things possible:

* the confidence gate (``docs/06-ai-pipeline.md`` §5),
* the "here's what I see, is this right?" confirmation UI,
* user corrections that supersede the model without erasing what it said.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.shared.values import BoundingBox, Confidence

SCHEMA_VERSION = 1


class MediaRole(StrEnum):
    """What a photo is *of*. Drives capture prompts and the confidence gate."""

    PRODUCT = "product"
    LOCATION = "location"
    MOUNT = "mount"
    HARDWARE = "hardware"
    WIDE = "wide"
    LABEL = "label"
    STEP_EVIDENCE = "step_evidence"
    OTHER = "other"


class FindingSource(StrEnum):
    VISION = "vision"
    USER = "user"
    KNOWLEDGE = "knowledge"
    RULE = "rule"


class AnalysisStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    NEEDS_MEDIA = "needs_media"
    NEEDS_ANSWER = "needs_answer"
    FAILED = "failed"


class Field(StrEnum):
    """The controlled field vocabulary.

    A string enum rather than free text: a typo in a field name would silently
    disable a confidence threshold or a safety rule, which is exactly the class
    of bug this product cannot afford.
    """

    PRODUCT_CATEGORY = "product.category"
    PRODUCT_BRAND = "product.brand"
    PRODUCT_MODEL = "product.model"
    PRODUCT_SIZE_INCHES = "product.size_inches"
    PRODUCT_WEIGHT_KG = "product.weight_kg"
    PRODUCT_DIMENSIONS_MM = "product.dimensions_mm"

    MOUNT_PRESENT = "mount.present"
    MOUNT_BRAND = "mount.brand"
    MOUNT_MODEL = "mount.model"
    MOUNT_TYPE = "mount.type"
    MOUNT_VESA = "mount.vesa"
    MOUNT_MAX_LOAD_KG = "mount.max_load_kg"
    MOUNT_MISSING_PARTS = "mount.missing_parts"

    WALL_MATERIAL = "wall.material"
    WALL_FINISH = "wall.finish"
    WALL_THICKNESS_MM = "wall.thickness_mm"
    WALL_OBSTACLES = "wall.obstacles"
    WALL_STUD_EVIDENCE = "wall.stud_evidence"

    SITE_ROOM_TYPE = "site.room_type"
    SITE_OUTLET_PRESENT = "site.outlet_present"

    HARDWARE_ITEMS = "hardware.items"
    TOOLS_VISIBLE = "tools.visible"

    DAMAGE_PRESENT = "damage.present"
    DAMAGE_DESCRIPTION = "damage.description"
    DAMAGE_SEVERITY = "damage.severity"

    TASK_INVOLVES_ELECTRICAL = "task.involves_electrical"
    TASK_INVOLVES_GAS = "task.involves_gas"
    TASK_INVOLVES_PLUMBING = "task.involves_plumbing"
    TASK_INVOLVES_STRUCTURAL = "task.involves_structural"
    TASK_IS_OVERHEAD = "task.is_overhead"
    TASK_WORKING_HEIGHT_M = "task.working_height_m"


#: Fields a wrong answer on can hurt someone. These gate the pipeline and can
#: never be filled by inference — see ``docs/07-safety-policy.md`` §4-5.
SAFETY_CRITICAL_FIELDS: frozenset[Field] = frozenset(
    {
        Field.PRODUCT_WEIGHT_KG,
        Field.MOUNT_MAX_LOAD_KG,
        Field.WALL_MATERIAL,
        Field.WALL_OBSTACLES,
        Field.MOUNT_MISSING_PARTS,
        Field.TASK_INVOLVES_ELECTRICAL,
        Field.TASK_INVOLVES_GAS,
        Field.TASK_INVOLVES_PLUMBING,
        Field.TASK_INVOLVES_STRUCTURAL,
        Field.TASK_IS_OVERHEAD,
        Field.TASK_WORKING_HEIGHT_M,
    }
)

#: Per-field confidence thresholds. Anything absent uses ``DEFAULT_THRESHOLD``.
CONFIDENCE_THRESHOLDS: dict[Field, float] = {
    Field.PRODUCT_WEIGHT_KG: 0.80,
    Field.WALL_MATERIAL: 0.85,
    Field.MOUNT_MAX_LOAD_KG: 0.80,
    Field.MOUNT_VESA: 0.75,
    Field.PRODUCT_MODEL: 0.70,
    Field.WALL_OBSTACLES: 0.70,
    Field.MOUNT_MISSING_PARTS: 0.70,
    Field.TASK_INVOLVES_ELECTRICAL: 0.80,
    Field.TASK_INVOLVES_GAS: 0.80,
    Field.TASK_INVOLVES_PLUMBING: 0.80,
    Field.TASK_INVOLVES_STRUCTURAL: 0.80,
    Field.TASK_IS_OVERHEAD: 0.80,
}
DEFAULT_THRESHOLD = 0.50

#: Which extra photo to ask for when a field falls short, and why — the reason is
#: shown to the user verbatim, because "take another photo" without a reason is
#: the fastest way to lose them.
REMEDY_FOR_FIELD: dict[Field, tuple[MediaRole, str, str]] = {
    Field.PRODUCT_WEIGHT_KG: (
        MediaRole.LABEL,
        "The sticker on the back of the product",
        "The weight decides which anchor is safe, so we don't want to guess it.",
    ),
    Field.WALL_MATERIAL: (
        MediaRole.LOCATION,
        "A close-up of the wall where it will go",
        "Drywall, plaster and masonry each need a different anchor.",
    ),
    Field.MOUNT_MAX_LOAD_KG: (
        MediaRole.MOUNT,
        "The mount's box or spec sticker",
        "We need the mount's rated load to check it can carry your product.",
    ),
    Field.MOUNT_VESA: (
        MediaRole.PRODUCT,
        "The back of the product, showing the mounting holes",
        "The hole pattern tells us whether this mount actually fits.",
    ),
    Field.PRODUCT_MODEL: (
        MediaRole.LABEL,
        "The model label",
        "The exact model lets us look up its real specifications.",
    ),
    Field.WALL_OBSTACLES: (
        MediaRole.WIDE,
        "A wider shot of the wall and what's around it",
        "We check for cables and pipes before telling you where to drill.",
    ),
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One extracted fact with its evidence and confidence."""

    field: Field
    value: Any
    confidence: Confidence
    source: FindingSource = FindingSource.VISION
    unit: str | None = None
    evidence_media_id: uuid.UUID | None = None
    bbox: BoundingBox | None = None
    note: str | None = None

    @property
    def is_safety_critical(self) -> bool:
        return self.field in SAFETY_CRITICAL_FIELDS

    @property
    def threshold(self) -> float:
        return CONFIDENCE_THRESHOLDS.get(self.field, DEFAULT_THRESHOLD)

    @property
    def is_known(self) -> bool:
        """A null value is 'not determined', regardless of any claimed confidence."""
        return self.value is not None

    @property
    def meets_threshold(self) -> bool:
        return self.is_known and self.confidence.meets(self.threshold)

    def superseded_by_user(self, value: Any, *, unit: str | None = None) -> Finding:
        """A user correction is certain by definition."""
        return Finding(
            field=self.field,
            value=value,
            confidence=Confidence.certain(),
            source=FindingSource.USER,
            unit=unit if unit is not None else self.unit,
            evidence_media_id=self.evidence_media_id,
            bbox=self.bbox,
            note="Corrected by the user",
        )


@dataclass(frozen=True, slots=True)
class MediaRequest:
    """A specific extra photo the pipeline needs, with the reason shown to the user."""

    field: Field
    role: MediaRole
    prompt: str
    reason: str


@dataclass(frozen=True, slots=True)
class Clarification:
    """A direct question, used when another photo will not settle the matter."""

    id: uuid.UUID
    field: Field
    question: str
    reason: str
    options: tuple[str, ...] = ()
    answer: str | None = None

    @property
    def is_answered(self) -> bool:
        return self.answer is not None


@dataclass(slots=True)
class Analysis:
    """The aggregate. ``findings`` is keyed by field: the newest wins."""

    id: uuid.UUID
    project_id: uuid.UUID
    revision: int = 1
    status: AnalysisStatus = AnalysisStatus.PENDING
    schema_version: int = SCHEMA_VERSION
    findings: dict[Field, Finding] = field(default_factory=dict)
    media_requests: tuple[MediaRequest, ...] = ()
    clarifications: tuple[Clarification, ...] = ()
    gate_rounds: int = 0
    knowledge_coverage: str = "none"
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    confirmed_by_user_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # ------------------------------------------------------------- accessors
    def get(self, field_key: Field) -> Finding | None:
        return self.findings.get(field_key)

    def value_of(self, field_key: Field, default: Any = None) -> Any:
        found = self.findings.get(field_key)
        return found.value if found is not None and found.is_known else default

    def number_of(self, field_key: Field) -> float | None:
        """Numeric access for the safety rules, which must never see a string."""
        raw = self.value_of(field_key)
        if isinstance(raw, bool) or raw is None:
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        try:
            return float(str(raw))
        except ValueError:
            return None

    def flag_of(self, field_key: Field) -> bool:
        """Boolean access that treats 'unknown' as False, never as True."""
        return self.value_of(field_key) is True

    def add(self, finding: Finding) -> None:
        """Record a finding. User-sourced values are never overwritten by a model."""
        existing = self.findings.get(finding.field)
        if (
            existing is not None
            and existing.source is FindingSource.USER
            and finding.source is not FindingSource.USER
        ):
            return
        self.findings[finding.field] = finding

    def add_all(self, findings: Iterable[Finding]) -> None:
        for finding in findings:
            self.add(finding)

    def correct(self, field_key: Field, value: Any, *, unit: str | None = None) -> Finding:
        existing = self.findings.get(field_key)
        corrected = (
            existing.superseded_by_user(value, unit=unit)
            if existing is not None
            else Finding(
                field=field_key,
                value=value,
                confidence=Confidence.certain(),
                source=FindingSource.USER,
                unit=unit,
                note="Provided by the user",
            )
        )
        self.findings[field_key] = corrected
        return corrected

    # ------------------------------------------------------------- gating
    def failing_fields(self, required: Sequence[Field]) -> tuple[Field, ...]:
        """Required fields that are missing or below their threshold."""
        failing: list[Field] = []
        for field_key in required:
            found = self.findings.get(field_key)
            if found is None or not found.meets_threshold:
                failing.append(field_key)
        return tuple(failing)

    def uncertain_safety_fields(self) -> tuple[Field, ...]:
        return tuple(
            found.field
            for found in self.findings.values()
            if found.is_safety_critical and not found.meets_threshold
        )

    @property
    def overall_confidence(self) -> Confidence:
        """Mean confidence over known findings, weighted 2x for safety-critical
        fields so a shaky weight reading is not hidden by ten easy ones."""
        known = [found for found in self.findings.values() if found.is_known]
        if not known:
            return Confidence.unknown()
        weighted = sum(f.confidence.value * (2 if f.is_safety_critical else 1) for f in known)
        weights = sum(2 if f.is_safety_critical else 1 for f in known)
        return Confidence(round(weighted / weights, 3))

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_by_user_at is not None

    def confirm(self) -> None:
        self.confirmed_by_user_at = datetime.now(UTC)
