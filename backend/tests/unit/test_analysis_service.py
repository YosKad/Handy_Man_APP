"""Confidence gate, clarification loop, and answer coercion."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

import pytest

from app.features.knowledge.reference.materials import WallMaterial
from app.features.vision.application.analysis_service import (
    MAX_GATE_ROUNDS,
    REQUIRED_FIELDS,
    AnalysisBuilder,
    AnalysisService,
    Decision,
)
from app.features.vision.domain.entities import (
    REMEDY_FOR_FIELD,
    Analysis,
    AnalysisStatus,
    Field,
    Finding,
    FindingSource,
)
from app.shared.values import Confidence, Workflow


class AnalysisFactory(Protocol):
    def __call__(
        self, values: dict[Field, Any], *, confidence: float = ..., project_id: Any = ...
    ) -> Analysis: ...


@pytest.fixture
def service() -> AnalysisService:
    return AnalysisService()


def _complete_install() -> dict[Field, Any]:
    return {
        Field.PRODUCT_CATEGORY: "television",
        Field.PRODUCT_WEIGHT_KG: 15.4,
        Field.WALL_MATERIAL: WallMaterial.DRYWALL.value,
        Field.WALL_OBSTACLES: [],
    }


# ------------------------------------------------------------------ gate


def test_complete_analysis_proceeds(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    result = service.evaluate(analysis_factory(_complete_install()), Workflow.INSTALL)

    assert result.decision is Decision.PROCEED
    assert result.blocks_generation is False
    assert result.unresolved_safety_fields == ()


def test_missing_field_requests_a_specific_photo_with_a_reason(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    values = _complete_install()
    del values[Field.WALL_MATERIAL]

    result = service.evaluate(analysis_factory(values), Workflow.INSTALL)

    assert result.decision is Decision.NEED_MEDIA
    request = next(r for r in result.media_requests if r.field is Field.WALL_MATERIAL)
    assert request.prompt
    assert "anchor" in request.reason.lower()


def test_low_confidence_on_a_safety_field_blocks_even_when_present(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    """0.6 confidence on the weight is below its 0.80 threshold."""
    result = service.evaluate(
        analysis_factory(_complete_install(), confidence=0.6), Workflow.INSTALL
    )

    assert result.decision is not Decision.PROCEED
    fields = {request.field for request in result.media_requests}
    assert Field.PRODUCT_WEIGHT_KG in fields


def test_gate_requests_at_most_three_photos(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    """A wall of photo requests is a chore, not a conversation."""
    result = service.evaluate(
        analysis_factory({Field.PRODUCT_CATEGORY: "tv"}, confidence=0.2), Workflow.INSTALL
    )
    assert len(result.media_requests) <= 3


def test_after_the_round_limit_it_asks_a_direct_question(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    """An unbounded 'one more photo' loop is worse than asking outright."""
    values = _complete_install()
    del values[Field.WALL_MATERIAL]
    analysis = analysis_factory(values)
    analysis.gate_rounds = MAX_GATE_ROUNDS

    result = service.evaluate(analysis, Workflow.INSTALL)

    assert result.decision is Decision.NEED_ANSWER
    question = next(c for c in result.clarifications if c.field is Field.WALL_MATERIAL)
    assert "hollow" in question.question.lower()
    assert question.options


def test_gate_never_asks_the_same_photo_twice(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    values = _complete_install()
    del values[Field.WALL_MATERIAL]
    analysis = analysis_factory(values)

    first = service.evaluate(analysis, Workflow.INSTALL)
    service.apply(analysis, first)
    second = service.evaluate(analysis, Workflow.INSTALL)

    assert second.decision is Decision.NEED_ANSWER


def test_at_most_three_clarifications(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    """docs/01 F-4: at most three questions before a guide."""
    analysis = analysis_factory({}, confidence=0.1)
    analysis.gate_rounds = MAX_GATE_ROUNDS

    result = service.evaluate(analysis, Workflow.INSTALL)

    assert len(result.clarifications) <= 3


def test_unanswerable_safety_fields_are_handed_to_the_classifier(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    """When nothing is left to ask, escalate — never quietly assume a value."""
    analysis = analysis_factory(
        {
            Field.PRODUCT_CATEGORY: "television",
            Field.PRODUCT_WEIGHT_KG: 15.0,
            Field.WALL_MATERIAL: WallMaterial.DRYWALL.value,
            Field.WALL_OBSTACLES: [],
            # Safety-critical, weak, and has neither a photo remedy nor a question.
            Field.TASK_INVOLVES_STRUCTURAL: False,
        }
    )
    analysis.findings[Field.TASK_INVOLVES_STRUCTURAL] = Finding(
        field=Field.TASK_INVOLVES_STRUCTURAL, value=False, confidence=Confidence(0.2)
    )
    analysis.gate_rounds = MAX_GATE_ROUNDS

    result = service.evaluate(analysis, Workflow.INSTALL)

    assert result.decision is Decision.PROCEED
    assert Field.TASK_INVOLVES_STRUCTURAL in result.unresolved_safety_fields


def test_advise_workflow_needs_far_less(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    """No wall, no weight: nothing is being fixed to anything yet."""
    result = service.evaluate(
        analysis_factory({Field.PRODUCT_CATEGORY: "tv_wall_mount"}), Workflow.ADVISE
    )
    assert result.decision is Decision.PROCEED


def test_every_required_field_can_be_resolved_somehow() -> None:
    """A required field with no photo remedy and no question would deadlock."""
    from app.features.vision.application.analysis_service import QUESTION_FOR_FIELD

    for workflow, fields in REQUIRED_FIELDS.items():
        for field_key in fields:
            resolvable = field_key in REMEDY_FOR_FIELD or field_key in QUESTION_FOR_FIELD
            # Category and damage description come from the intake itself.
            exempt = field_key in {
                Field.PRODUCT_CATEGORY,
                Field.DAMAGE_PRESENT,
                Field.DAMAGE_DESCRIPTION,
            }
            assert resolvable or exempt, f"{workflow.value}/{field_key.value} cannot be resolved"


# ------------------------------------------------------------------ apply


def test_apply_records_status_and_increments_rounds(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    values = _complete_install()
    del values[Field.WALL_MATERIAL]
    analysis = analysis_factory(values)

    service.apply(analysis, service.evaluate(analysis, Workflow.INSTALL))

    assert analysis.status is AnalysisStatus.NEEDS_MEDIA
    assert analysis.gate_rounds == 1
    assert analysis.media_requests


def test_apply_marks_complete_when_proceeding(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    analysis = analysis_factory(_complete_install())
    service.apply(analysis, service.evaluate(analysis, Workflow.INSTALL))

    assert analysis.status is AnalysisStatus.COMPLETE
    assert analysis.gate_rounds == 0


# ------------------------------------------------------------------ answers


def _ask(service: AnalysisService, analysis: Analysis, field_key: Field) -> uuid.UUID:
    analysis.gate_rounds = MAX_GATE_ROUNDS
    result = service.evaluate(analysis, Workflow.INSTALL)
    service.apply(analysis, result)
    return next(c.id for c in analysis.clarifications if c.field is field_key)


def test_hollow_answer_becomes_drywall(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    values = _complete_install()
    del values[Field.WALL_MATERIAL]
    analysis = analysis_factory(values)
    question_id = _ask(service, analysis, Field.WALL_MATERIAL)

    finding = service.record_answer(analysis, question_id, "Hollow")

    assert finding is not None
    assert finding.value == WallMaterial.DRYWALL.value
    assert finding.source is FindingSource.USER
    assert finding.confidence.value == 1.0


def test_not_sure_records_the_answer_but_creates_no_value(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    """ "Not sure" is information, not a value — it must not become one."""
    values = _complete_install()
    del values[Field.WALL_MATERIAL]
    analysis = analysis_factory(values)
    question_id = _ask(service, analysis, Field.WALL_MATERIAL)

    finding = service.record_answer(analysis, question_id, "Not sure")

    assert finding is None
    assert analysis.value_of(Field.WALL_MATERIAL) is None
    answered = next(c for c in analysis.clarifications if c.id == question_id)
    assert answered.answer == "Not sure"


def test_weight_answer_in_pounds_is_converted(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    """Treating pounds as kilograms would understate the load by 2.2x."""
    values = _complete_install()
    del values[Field.PRODUCT_WEIGHT_KG]
    analysis = analysis_factory(values)
    question_id = _ask(service, analysis, Field.PRODUCT_WEIGHT_KG)

    finding = service.record_answer(analysis, question_id, "34 lb")

    assert finding is not None
    assert finding.value == pytest.approx(15.42, abs=0.02)


def test_weight_answer_with_units_is_parsed(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    values = _complete_install()
    del values[Field.PRODUCT_WEIGHT_KG]
    analysis = analysis_factory(values)
    question_id = _ask(service, analysis, Field.PRODUCT_WEIGHT_KG)

    finding = service.record_answer(analysis, question_id, "about 22 kg")

    assert finding is not None
    assert finding.value == pytest.approx(22.0)


def test_unparseable_weight_answer_creates_nothing(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    values = _complete_install()
    del values[Field.PRODUCT_WEIGHT_KG]
    analysis = analysis_factory(values)
    question_id = _ask(service, analysis, Field.PRODUCT_WEIGHT_KG)

    assert service.record_answer(analysis, question_id, "quite heavy") is None


def test_yes_no_answers_become_booleans(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    values = _complete_install()
    del values[Field.WALL_OBSTACLES]
    analysis = analysis_factory(values)
    question_id = _ask(service, analysis, Field.WALL_OBSTACLES)

    finding = service.record_answer(analysis, question_id, "Yes")

    assert finding is not None
    assert finding.value == ["Yes"]


def test_unknown_clarification_id_is_ignored(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    analysis = analysis_factory(_complete_install())
    assert service.record_answer(analysis, uuid.uuid4(), "Hollow") is None


def test_answering_resolves_the_gate(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    """End to end: missing wall -> photo request -> question -> answer -> proceed."""
    values = _complete_install()
    del values[Field.WALL_MATERIAL]
    analysis = analysis_factory(values)

    first = service.evaluate(analysis, Workflow.INSTALL)
    service.apply(analysis, first)
    assert first.decision is Decision.NEED_MEDIA

    second = service.evaluate(analysis, Workflow.INSTALL)
    service.apply(analysis, second)
    assert second.decision is Decision.NEED_ANSWER

    question_id = next(c.id for c in analysis.clarifications if c.field is Field.WALL_MATERIAL)
    service.record_answer(analysis, question_id, "Solid")

    final = service.evaluate(analysis, Workflow.INSTALL)
    assert final.decision is Decision.PROCEED


# ------------------------------------------------------------------ prompt input


def test_material_hints_come_from_the_reference_table(service: AnalysisService) -> None:
    hints = service.material_hints()
    assert any("drywall" in hint for hint in hints)
    assert not any("unknown" in hint.split(" ")[0] for hint in hints)


def test_requested_fields_put_required_ones_first(service: AnalysisService) -> None:
    fields = service.requested_fields(Workflow.INSTALL)
    assert fields[0] == Field.PRODUCT_CATEGORY.value
    assert len(fields) == len(set(fields))
    assert len(fields) == len(list(Field))


def test_facts_for_guide_excludes_unreliable_findings(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    """An unreliable reading must not reach the guide as though it were solid."""
    analysis = analysis_factory(_complete_install(), confidence=0.95)
    analysis.findings[Field.MOUNT_VESA] = Finding(
        field=Field.MOUNT_VESA, value="400x400", confidence=Confidence(0.3)
    )

    facts = service.facts_for_guide(analysis)

    assert Field.PRODUCT_WEIGHT_KG.value in facts
    assert Field.MOUNT_VESA.value not in facts


def test_facts_for_guide_keeps_units(
    service: AnalysisService, analysis_factory: AnalysisFactory
) -> None:
    analysis = Analysis(id=uuid.uuid4(), project_id=uuid.uuid4())
    analysis.add(
        Finding(
            field=Field.PRODUCT_WEIGHT_KG,
            value=15.4,
            confidence=Confidence(0.95),
            unit="kg",
        )
    )
    assert service.facts_for_guide(analysis)[Field.PRODUCT_WEIGHT_KG.value] == "15.4 kg"


# ------------------------------------------------------------------ builder


def test_builder_normalises_a_material_synonym() -> None:
    analysis = (
        AnalysisBuilder(project_id=uuid.uuid4())
        .add_findings(
            [
                Finding(
                    field=Field.WALL_MATERIAL,
                    value="Plasterboard",
                    confidence=Confidence(0.9),
                )
            ]
        )
        .normalise()
        .build()
    )
    assert analysis.value_of(Field.WALL_MATERIAL) == WallMaterial.DRYWALL.value


def test_builder_nulls_an_unmappable_material() -> None:
    """The anchor table trusts this field, so a wrong value is worse than a gap."""
    analysis = (
        AnalysisBuilder(project_id=uuid.uuid4())
        .add_findings(
            [
                Finding(
                    field=Field.WALL_MATERIAL,
                    value="shimmering unobtainium",
                    confidence=Confidence(0.95),
                )
            ]
        )
        .normalise()
        .build()
    )
    finding = analysis.get(Field.WALL_MATERIAL)
    assert finding is not None
    assert finding.value is None
    assert finding.confidence.value == 0.0
    assert finding.note is not None
    assert "unobtainium" in finding.note


def test_builder_does_not_renormalise_a_user_value() -> None:
    builder = AnalysisBuilder(project_id=uuid.uuid4())
    analysis = builder.build()
    analysis.correct(Field.WALL_MATERIAL, "concrete")

    builder.normalise()

    assert analysis.value_of(Field.WALL_MATERIAL) == "concrete"


def test_builder_records_provenance() -> None:
    analysis = (
        AnalysisBuilder(project_id=uuid.uuid4())
        .add_findings([Finding(field=Field.PRODUCT_CATEGORY, value="tv", confidence=Confidence(1))])
        .with_provenance("fake", "fake-vision", "vision_analyze.v1+abcd1234", "partial")
        .build()
    )
    assert analysis.provider == "fake"
    assert analysis.prompt_version is not None
    assert analysis.prompt_version.startswith("vision_analyze.v1+")
    assert analysis.knowledge_coverage == "partial"
