"""Analysis aggregate: confidence gating, user corrections, overall confidence."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

import pytest

from app.features.vision.domain.entities import (
    CONFIDENCE_THRESHOLDS,
    REMEDY_FOR_FIELD,
    SAFETY_CRITICAL_FIELDS,
    Analysis,
    Field,
    Finding,
    FindingSource,
)
from app.shared.values import Confidence


class AnalysisFactory(Protocol):
    def __call__(
        self, values: dict[Field, Any], *, confidence: float = ..., project_id: Any = ...
    ) -> Analysis: ...


def _finding(field: Field, value: Any, confidence: float) -> Finding:
    return Finding(field=field, value=value, confidence=Confidence(confidence))


# ------------------------------------------------------------------ thresholds


def test_safety_critical_fields_all_have_an_explicit_threshold() -> None:
    """A safety-critical field on the default 0.50 threshold would be a silent gap."""
    missing = [
        field.value
        for field in SAFETY_CRITICAL_FIELDS
        if field not in CONFIDENCE_THRESHOLDS and field is not Field.TASK_WORKING_HEIGHT_M
    ]
    assert missing == []


def test_every_gate_remedy_names_a_reason() -> None:
    """ "Take another photo" without a reason loses the user."""
    for field, (_role, prompt, reason) in REMEDY_FOR_FIELD.items():
        assert prompt.strip(), field
        assert reason.strip(), field
        assert len(reason) > 20, field


def test_finding_below_threshold_fails_the_gate() -> None:
    weak = _finding(Field.PRODUCT_WEIGHT_KG, 15.0, 0.70)  # threshold is 0.80
    strong = _finding(Field.PRODUCT_WEIGHT_KG, 15.0, 0.85)

    assert weak.meets_threshold is False
    assert strong.meets_threshold is True


def test_null_value_never_meets_the_threshold() -> None:
    """A confident null is still a null — the contract says do not guess."""
    claimed = _finding(Field.WALL_MATERIAL, None, 0.99)
    assert claimed.is_known is False
    assert claimed.meets_threshold is False


def test_failing_fields_reports_missing_and_weak(analysis_factory: AnalysisFactory) -> None:
    analysis = analysis_factory({Field.PRODUCT_WEIGHT_KG: 15.0}, confidence=0.5)
    failing = analysis.failing_fields([Field.PRODUCT_WEIGHT_KG, Field.WALL_MATERIAL])
    assert set(failing) == {Field.PRODUCT_WEIGHT_KG, Field.WALL_MATERIAL}


def test_uncertain_safety_fields_lists_only_safety_critical_ones() -> None:
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    analysis.add(_finding(Field.PRODUCT_WEIGHT_KG, 15.0, 0.4))  # critical, weak
    analysis.add(_finding(Field.SITE_ROOM_TYPE, "kitchen", 0.2))  # not critical

    assert analysis.uncertain_safety_fields() == (Field.PRODUCT_WEIGHT_KG,)


# ------------------------------------------------------------------ corrections


def test_user_correction_is_certain_and_marked() -> None:
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    analysis.add(_finding(Field.PRODUCT_MODEL, "UN55TU7000", 0.55))

    corrected = analysis.correct(Field.PRODUCT_MODEL, "UN55TU8000")

    assert corrected.value == "UN55TU8000"
    assert corrected.source is FindingSource.USER
    assert corrected.confidence.value == 1.0
    assert analysis.value_of(Field.PRODUCT_MODEL) == "UN55TU8000"


def test_model_output_cannot_overwrite_a_user_correction() -> None:
    """Re-analysis must not silently undo what the user told us."""
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    analysis.correct(Field.WALL_MATERIAL, "concrete")

    analysis.add(_finding(Field.WALL_MATERIAL, "drywall", 0.99))

    assert analysis.value_of(Field.WALL_MATERIAL) == "concrete"


def test_correcting_an_absent_field_creates_it() -> None:
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    analysis.correct(Field.PRODUCT_WEIGHT_KG, 22.0, unit="kg")

    finding = analysis.get(Field.PRODUCT_WEIGHT_KG)
    assert finding is not None
    assert finding.unit == "kg"
    assert finding.source is FindingSource.USER


# ------------------------------------------------------------------ accessors


def test_number_of_coerces_strings_and_rejects_nonsense() -> None:
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    analysis.add(_finding(Field.PRODUCT_WEIGHT_KG, "15.4", 0.9))
    analysis.add(_finding(Field.WALL_THICKNESS_MM, "about 12mm", 0.9))

    assert analysis.number_of(Field.PRODUCT_WEIGHT_KG) == pytest.approx(15.4)
    assert analysis.number_of(Field.WALL_THICKNESS_MM) is None
    assert analysis.number_of(Field.MOUNT_MAX_LOAD_KG) is None


def test_number_of_rejects_booleans() -> None:
    """True must not read as 1.0 kg."""
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    analysis.add(_finding(Field.PRODUCT_WEIGHT_KG, True, 0.9))
    assert analysis.number_of(Field.PRODUCT_WEIGHT_KG) is None


def test_flag_of_treats_unknown_as_false() -> None:
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    analysis.add(_finding(Field.TASK_INVOLVES_GAS, None, 0.0))

    assert analysis.flag_of(Field.TASK_INVOLVES_GAS) is False
    assert analysis.flag_of(Field.TASK_INVOLVES_ELECTRICAL) is False


# ------------------------------------------------------------------ aggregate


def test_overall_confidence_weights_safety_critical_fields_higher() -> None:
    """A shaky weight reading must not be hidden by ten confident easy fields."""
    with_weak_critical = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    with_weak_critical.add(_finding(Field.PRODUCT_WEIGHT_KG, 15.0, 0.2))
    with_weak_critical.add(_finding(Field.SITE_ROOM_TYPE, "lounge", 1.0))

    with_weak_trivial = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    with_weak_trivial.add(_finding(Field.PRODUCT_WEIGHT_KG, 15.0, 1.0))
    with_weak_trivial.add(_finding(Field.SITE_ROOM_TYPE, "lounge", 0.2))

    assert with_weak_critical.overall_confidence.value < with_weak_trivial.overall_confidence.value


def test_overall_confidence_of_an_empty_analysis_is_zero() -> None:
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    assert analysis.overall_confidence.value == 0.0


def test_unknown_findings_are_excluded_from_overall_confidence() -> None:
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    analysis.add(_finding(Field.PRODUCT_MODEL, "X", 0.9))
    analysis.add(_finding(Field.WALL_MATERIAL, None, 0.0))

    assert analysis.overall_confidence.value == pytest.approx(0.9)


def test_confirmation_is_recorded() -> None:
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    # mypy narrows a property across a mutating call, so read through bool()
    # rather than asserting on the same property twice.
    assert bool(analysis.is_confirmed) is False
    assert analysis.confirmed_by_user_at is None

    analysis.confirm()

    assert bool(analysis.is_confirmed) is True
    assert analysis.confirmed_by_user_at is not None
