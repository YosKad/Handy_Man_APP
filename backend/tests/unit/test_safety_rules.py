"""Safety classifier tests.

This is the most important test file in the repository. Per
``docs/07-safety-policy.md`` §10 every rule needs a case that matches *and* a
near-miss that must not, plus a property test asserting no input combination can
produce green while a hard red line is present.
"""

from __future__ import annotations

import itertools
from typing import Any, Protocol

import pytest

from app.features.knowledge.reference.materials import (
    WallMaterial,
    best_fastener_for,
    max_pullout_for,
    parse_material,
)
from app.features.safety.domain.rules import (
    LOAD_SAFETY_FACTOR,
    RuleCode,
    SafetyClassifier,
    SafetyContext,
)
from app.features.vision.domain.entities import Analysis, Field
from app.shared.values import SafetyClass


class AnalysisFactory(Protocol):
    def __call__(
        self, values: dict[Field, Any], *, confidence: float = ..., project_id: Any = ...
    ) -> Analysis: ...


@pytest.fixture
def classifier() -> SafetyClassifier:
    return SafetyClassifier()


def _safe_install(**overrides: Any) -> dict[Field, Any]:
    """A green baseline: light TV, timber stud, nothing hazardous."""
    base: dict[Field, Any] = {
        Field.PRODUCT_WEIGHT_KG: 8.0,
        Field.WALL_MATERIAL: WallMaterial.WOOD_STUD.value,
        Field.MOUNT_MAX_LOAD_KG: 40.0,
        Field.MOUNT_MISSING_PARTS: [],
        Field.WALL_OBSTACLES: [],
        Field.TASK_INVOLVES_ELECTRICAL: False,
        Field.TASK_INVOLVES_GAS: False,
        Field.TASK_INVOLVES_PLUMBING: False,
        Field.TASK_INVOLVES_STRUCTURAL: False,
        Field.TASK_IS_OVERHEAD: False,
        Field.TASK_WORKING_HEIGHT_M: 1.2,
    }
    base.update({Field(key): value for key, value in overrides.items()})
    return base


# ------------------------------------------------------------------ baseline


def test_baseline_install_is_green(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(_safe_install()),
        SafetyContext(knowledge_coverage="partial"),
    )
    assert verdict.classification is SafetyClass.GREEN
    assert verdict.hits == ()
    assert verdict.is_red_line is False


def test_green_verdict_recommends_a_fastener(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(_safe_install()),
        SafetyContext(knowledge_coverage="partial"),
    )
    assert verdict.recommended_fastener_key == "wood_stud_screw"
    assert verdict.material is WallMaterial.WOOD_STUD


def test_red_verdict_never_recommends_a_fastener(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    """A red job must not hand over a fixing that implies 'go ahead'."""
    verdict = classifier.classify(
        analysis_factory(_safe_install(**{Field.TASK_INVOLVES_ELECTRICAL: True}))
    )
    assert verdict.classification is SafetyClass.RED
    assert verdict.recommended_fastener_key is None


# ------------------------------------------------------------------ red lines


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({Field.TASK_INVOLVES_ELECTRICAL: True}, RuleCode.ELECTRICAL_MAINS),
        ({Field.TASK_INVOLVES_GAS: True}, RuleCode.GAS_WORK),
        ({Field.TASK_INVOLVES_PLUMBING: True}, RuleCode.PLUMBING_MAINS),
        ({Field.TASK_INVOLVES_STRUCTURAL: True}, RuleCode.STRUCTURAL),
        ({Field.TASK_WORKING_HEIGHT_M: 3.5}, RuleCode.WORKING_AT_HEIGHT),
        ({Field.TASK_IS_OVERHEAD: True}, RuleCode.OVERHEAD_LOAD),
    ],
)
def test_hard_red_lines_classify_red(
    classifier: SafetyClassifier,
    analysis_factory: AnalysisFactory,
    overrides: dict[Field, Any],
    expected_code: RuleCode,
) -> None:
    verdict = classifier.classify(analysis_factory(_safe_install(**overrides)))
    assert verdict.classification is SafetyClass.RED
    assert expected_code.value in verdict.codes
    assert verdict.is_red_line is True


@pytest.mark.parametrize(
    "overrides",
    [
        {Field.TASK_INVOLVES_ELECTRICAL: False},
        {Field.TASK_WORKING_HEIGHT_M: 2.0},  # exactly at the limit, not above
    ],
)
def test_near_misses_do_not_trigger_red_lines(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory, overrides: dict[Field, Any]
) -> None:
    verdict = classifier.classify(
        analysis_factory(_safe_install(**overrides)),
        SafetyContext(knowledge_coverage="partial"),
    )
    assert verdict.classification is SafetyClass.GREEN


def test_unknown_boolean_flag_is_not_treated_as_true(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    """A missing hazard flag must not read as 'hazard present' — or every job is red."""
    values = _safe_install()
    del values[Field.TASK_INVOLVES_GAS]
    verdict = classifier.classify(
        analysis_factory(values), SafetyContext(knowledge_coverage="partial")
    )
    assert RuleCode.GAS_WORK.value not in verdict.codes


def test_asbestos_marker_in_wall_finish_is_red(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    values = _safe_install()
    values[Field.WALL_FINISH] = "Textured coating, likely Artex"
    verdict = classifier.classify(analysis_factory(values))
    assert verdict.classification is SafetyClass.RED
    assert RuleCode.ASBESTOS_RISK.value in verdict.codes


def test_minor_account_with_tools_is_red(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(_safe_install()),
        SafetyContext(user_is_minor=True, involves_tools=True),
    )
    assert verdict.classification is SafetyClass.RED
    assert RuleCode.MINOR_ACCOUNT.value in verdict.codes


# ------------------------------------------------------------------ load rules


def test_load_beyond_material_capacity_is_red(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    """A 30 kg TV on tiled plasterboard exceeds anything that wall can hold."""
    verdict = classifier.classify(
        analysis_factory(
            _safe_install(
                **{
                    Field.PRODUCT_WEIGHT_KG: 30.0,
                    Field.WALL_MATERIAL: WallMaterial.TILE_OVER_DRYWALL.value,
                }
            )
        )
    )
    assert verdict.classification is SafetyClass.RED
    assert RuleCode.LOAD_EXCEEDS_MATERIAL.value in verdict.codes


def test_load_near_material_limit_is_yellow(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    capacity = max_pullout_for(WallMaterial.DRYWALL)
    # Between 60% and 100% of capacity once the safety factor is applied.
    weight = (capacity * 0.75) / LOAD_SAFETY_FACTOR
    verdict = classifier.classify(
        analysis_factory(
            _safe_install(
                **{
                    Field.PRODUCT_WEIGHT_KG: weight,
                    Field.WALL_MATERIAL: WallMaterial.DRYWALL.value,
                }
            )
        ),
        SafetyContext(knowledge_coverage="partial"),
    )
    assert verdict.classification is SafetyClass.YELLOW
    assert RuleCode.LOAD_NEAR_MATERIAL_LIMIT.value in verdict.codes


def test_safety_factor_is_applied_to_the_load(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(_safe_install(**{Field.PRODUCT_WEIGHT_KG: 10.0}))
    )
    assert verdict.effective_load_kg == pytest.approx(10.0 * LOAD_SAFETY_FACTOR)


def test_unknown_material_with_heavy_load_is_red(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(
            _safe_install(
                **{
                    Field.WALL_MATERIAL: "something I don't recognise",
                    Field.PRODUCT_WEIGHT_KG: 25.0,
                }
            )
        )
    )
    assert verdict.classification is SafetyClass.RED
    assert RuleCode.UNKNOWN_MATERIAL_HEAVY.value in verdict.codes


def test_unknown_material_with_light_load_is_yellow(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(
            _safe_install(
                **{Field.WALL_MATERIAL: WallMaterial.UNKNOWN.value, Field.PRODUCT_WEIGHT_KG: 3.0}
            )
        ),
        SafetyContext(knowledge_coverage="partial"),
    )
    assert verdict.classification is SafetyClass.YELLOW
    assert RuleCode.UNKNOWN_MATERIAL_LIGHT.value in verdict.codes


def test_unknown_weight_on_a_known_wall_is_red(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    values = _safe_install()
    del values[Field.PRODUCT_WEIGHT_KG]
    verdict = classifier.classify(analysis_factory(values))
    assert verdict.classification is SafetyClass.RED
    assert RuleCode.UNKNOWN_WEIGHT.value in verdict.codes


def test_underrated_mount_is_red(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(
            _safe_install(**{Field.PRODUCT_WEIGHT_KG: 20.0, Field.MOUNT_MAX_LOAD_KG: 22.0})
        )
    )
    assert verdict.classification is SafetyClass.RED
    assert RuleCode.MOUNT_UNDERRATED.value in verdict.codes


def test_mount_with_adequate_margin_passes(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(
            _safe_install(**{Field.PRODUCT_WEIGHT_KG: 20.0, Field.MOUNT_MAX_LOAD_KG: 25.0})
        ),
        SafetyContext(knowledge_coverage="partial"),
    )
    assert RuleCode.MOUNT_UNDERRATED.value not in verdict.codes


def test_missing_load_bearing_hardware_is_red(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(_safe_install(**{Field.MOUNT_MISSING_PARTS: ["M8 bolts"]}))
    )
    assert verdict.classification is SafetyClass.RED
    assert RuleCode.MISSING_LOAD_BEARING_HARDWARE.value in verdict.codes
    assert "M8 bolts" in verdict.rationale


# ------------------------------------------------------------------ cautions


def test_services_in_the_wall_is_yellow(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(_safe_install(**{Field.WALL_OBSTACLES: ["power cable near socket"]})),
        SafetyContext(knowledge_coverage="partial"),
    )
    assert verdict.classification is SafetyClass.YELLOW
    assert RuleCode.DRILLING_NEAR_SERVICES.value in verdict.codes


def test_no_knowledge_coverage_is_at_least_yellow(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(_safe_install()), SafetyContext(knowledge_coverage="none")
    )
    assert verdict.classification is SafetyClass.YELLOW
    assert RuleCode.NO_KNOWLEDGE_COVERAGE.value in verdict.codes


def test_uncertain_safety_field_is_at_least_yellow(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    """A weight read at 0.4 confidence is below its 0.80 threshold."""
    analysis = analysis_factory(_safe_install(), confidence=0.40)
    verdict = classifier.classify(analysis, SafetyContext(knowledge_coverage="partial"))
    assert verdict.classification.severity >= SafetyClass.YELLOW.severity
    assert RuleCode.UNCERTAIN_SAFETY_FIELD.value in verdict.codes


def test_rental_site_warns_about_irreversible_fixings(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    verdict = classifier.classify(
        analysis_factory(_safe_install()),
        SafetyContext(knowledge_coverage="partial", site_is_rental=True),
    )
    assert RuleCode.RENTAL_IRREVERSIBLE.value in verdict.codes


# ------------------------------------------------------------------ properties


@pytest.mark.parametrize(
    ("electrical", "gas", "plumbing", "structural", "overhead"),
    list(itertools.product([True, False], repeat=5)),
)
def test_any_red_line_forces_red_regardless_of_other_inputs(
    classifier: SafetyClassifier,
    analysis_factory: AnalysisFactory,
    electrical: bool,
    gas: bool,
    plumbing: bool,
    structural: bool,
    overhead: bool,
) -> None:
    """The property that matters: no combination can dilute a red line to green."""
    values = _safe_install(
        **{
            Field.TASK_INVOLVES_ELECTRICAL: electrical,
            Field.TASK_INVOLVES_GAS: gas,
            Field.TASK_INVOLVES_PLUMBING: plumbing,
            Field.TASK_INVOLVES_STRUCTURAL: structural,
            Field.TASK_IS_OVERHEAD: overhead,
        }
    )
    verdict = classifier.classify(
        analysis_factory(values), SafetyContext(knowledge_coverage="partial")
    )
    any_red_line = electrical or gas or plumbing or structural or overhead
    if any_red_line:
        assert verdict.classification is SafetyClass.RED
        assert verdict.is_red_line is True
    else:
        assert verdict.classification is SafetyClass.GREEN


def test_classification_is_deterministic(
    classifier: SafetyClassifier, analysis_factory: AnalysisFactory
) -> None:
    values = _safe_install(**{Field.PRODUCT_WEIGHT_KG: 14.0})
    first = classifier.classify(analysis_factory(values))
    second = classifier.classify(analysis_factory(values))
    assert first.classification is second.classification
    assert first.codes == second.codes


def test_severity_never_decreases_across_escalations() -> None:
    assert SafetyClass.GREEN.escalate_to(SafetyClass.RED) is SafetyClass.RED
    assert SafetyClass.RED.escalate_to(SafetyClass.GREEN) is SafetyClass.RED
    assert SafetyClass.most_severe([SafetyClass.GREEN, SafetyClass.YELLOW]) is SafetyClass.YELLOW
    assert SafetyClass.most_severe([]) is SafetyClass.GREEN


# ------------------------------------------------------------------ reference data


def test_unrecognised_material_string_never_guesses() -> None:
    """Mapping to the nearest known material would silently pick a wrong anchor."""
    assert parse_material("brushed unobtainium") is WallMaterial.UNKNOWN
    assert parse_material(None) is WallMaterial.UNKNOWN
    assert parse_material(42) is WallMaterial.UNKNOWN


@pytest.mark.parametrize(
    ("synonym", "expected"),
    [
        ("plasterboard", WallMaterial.DRYWALL),
        ("Sheetrock", WallMaterial.DRYWALL),
        ("cinder block", WallMaterial.HOLLOW_BLOCK),
        ("timber stud", WallMaterial.WOOD_STUD),
        ("AAC", WallMaterial.AERATED_BLOCK),
    ],
)
def test_material_synonyms_normalise(synonym: str, expected: WallMaterial) -> None:
    assert parse_material(synonym) is expected


def test_best_fastener_picks_the_least_invasive_sufficient_option() -> None:
    light = best_fastener_for(WallMaterial.DRYWALL, 3.0)
    heavier = best_fastener_for(WallMaterial.DRYWALL, 15.0)
    assert light is not None
    assert heavier is not None
    assert light.pullout_kg_typical < heavier.pullout_kg_typical


def test_best_fastener_returns_none_when_nothing_is_adequate() -> None:
    """None means 'refer', not 'use something bigger'."""
    assert best_fastener_for(WallMaterial.TILE_OVER_DRYWALL, 500.0) is None


def test_unknown_material_has_no_fasteners() -> None:
    assert max_pullout_for(WallMaterial.UNKNOWN) == 0.0
