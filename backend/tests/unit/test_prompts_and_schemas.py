"""Prompt registry, JSON schemas, and the OpenAI adapter's pure parsers.

The parsers are tested against recorded-shape payloads, so provider response
handling is covered without an SDK, a key, or a network call.
"""

from __future__ import annotations

import uuid
from string import Formatter
from typing import Any

import pytest

from app.core.errors import ProviderContractError
from app.features.vision.domain.entities import Field
from app.infrastructure.ai.adapters.openai_adapter import (
    _parse_analysis,
    _parse_bbox,
    _parse_guide,
    _parse_step_validation,
)
from app.infrastructure.ai.models import Verdict
from app.infrastructure.ai.prompts import (
    ACTIVE_VERSIONS,
    all_prompt_versions,
    load_prompt,
    prompt_version,
)
from app.infrastructure.ai.schemas import (
    ANALYSIS_JSON_SCHEMA,
    GUIDE_JSON_SCHEMA,
    STEP_VALIDATION_JSON_SCHEMA,
)
from app.shared.values import Difficulty

# ------------------------------------------------------------------ prompts


@pytest.mark.parametrize("purpose", sorted(ACTIVE_VERSIONS))
def test_every_registered_prompt_file_exists_and_is_substantial(purpose: str) -> None:
    text = load_prompt(purpose)
    assert len(text) > 200, f"{purpose} looks like a stub"


def test_unregistered_prompt_raises() -> None:
    from app.core.errors import InternalError

    with pytest.raises(InternalError):
        load_prompt("no_such_prompt")


def test_prompt_version_includes_a_content_hash() -> None:
    """The hash is what lets a guide be traced to the exact text that made it."""
    label = prompt_version("vision_system")
    assert label.startswith("vision_system.v1+")
    assert len(label.split("+")[1]) == 8
    assert prompt_version("vision_system") == label  # stable


def test_all_prompt_versions_covers_the_registry() -> None:
    assert set(all_prompt_versions()) == set(ACTIVE_VERSIONS)


@pytest.mark.parametrize(
    ("purpose", "expected_placeholders"),
    [
        (
            "vision_analyze",
            {"workflow", "intent", "unit_system", "material_hints", "requested_fields"},
        ),
        (
            "generate_guide",
            {
                "workflow",
                "intent",
                "facts",
                "safety_class",
                "safety_rationale",
                "fastener",
                "knowledge",
                "unit_system",
                "locale",
                "uncertain_fields",
            },
        ),
        ("validate_step", {"step_title", "step_body", "verification", "facts"}),
    ],
)
def test_prompt_placeholders_match_what_the_adapter_supplies(
    purpose: str, expected_placeholders: set[str]
) -> None:
    """A renamed placeholder would raise KeyError at request time, in production."""
    found = {name for _, name, _, _ in Formatter().parse(load_prompt(purpose)) if name}
    assert found == expected_placeholders


@pytest.mark.parametrize("purpose", ["vision_system", "guide_system", "chat_system"])
def test_system_prompts_state_the_injection_stance(purpose: str) -> None:
    """Model input derived from user media or documents is data, never instructions."""
    text = load_prompt(purpose).lower()
    assert "instruction" in text
    assert "data" in text or "content to describe" in text or "content to be described" in text


def test_vision_prompt_forbids_guessing() -> None:
    text = load_prompt("vision_system").lower()
    assert "never guess" in text
    assert "null" in text


def test_guide_prompt_forbids_overriding_the_safety_class() -> None:
    text = load_prompt("guide_system").lower()
    assert "do not choose the fixing" in text
    assert "contradict" in text


def test_chat_prompt_permits_admitting_uncertainty() -> None:
    # Collapse wrapping so the assertion is about wording, not line breaks.
    text = " ".join(load_prompt("chat_system").lower().split())
    assert "can't tell from this photo" in text


def test_validate_step_prompt_makes_unsure_a_first_class_answer() -> None:
    assert "use this freely" in load_prompt("validate_step").lower()


# ------------------------------------------------------------------ schemas


def test_analysis_schema_enumerates_the_whole_field_vocabulary() -> None:
    """Schema and domain enum must not drift: a missing field silently disables
    its confidence threshold and any safety rule that reads it."""
    schema_fields = set(
        ANALYSIS_JSON_SCHEMA["properties"]["findings"]["items"]["properties"]["field"]["enum"]
    )
    assert schema_fields == {field.value for field in Field}


def test_analysis_schema_allows_an_explicit_null_value() -> None:
    value_schema = ANALYSIS_JSON_SCHEMA["properties"]["findings"]["items"]["properties"]["value"]
    assert {"type": "null"} in value_schema["anyOf"]


def test_guide_schema_requires_verification_on_every_step() -> None:
    step = GUIDE_JSON_SCHEMA["properties"]["steps"]["items"]
    assert "verification" in step["required"]
    assert step["properties"]["verification"]["minLength"] >= 5


def test_guide_schema_difficulty_matches_the_domain_enum() -> None:
    assert set(GUIDE_JSON_SCHEMA["properties"]["difficulty"]["enum"]) == {
        level.value for level in Difficulty
    }


@pytest.mark.parametrize(
    "schema", [ANALYSIS_JSON_SCHEMA, GUIDE_JSON_SCHEMA, STEP_VALIDATION_JSON_SCHEMA]
)
def test_schemas_are_closed_for_strict_mode(schema: dict[str, Any]) -> None:
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_step_validation_schema_offers_the_unsure_verdict() -> None:
    assert "unsure" in STEP_VALIDATION_JSON_SCHEMA["properties"]["verdict"]["enum"]


# ------------------------------------------------------------------ parsers


def _analysis_payload(**overrides: Any) -> dict[str, Any]:
    finding = {
        "field": "product.weight_kg",
        "value": 15.4,
        "unit": "kg",
        "confidence": 0.86,
        "evidence_role": "label",
        "bbox": [0.1, 0.1, 0.2, 0.2],
        "annotate": True,
        "label": "Weight label",
        "alt_text": "The label showing the weight",
        "note": None,
    }
    finding.update(overrides)
    return {"findings": [finding], "notes": "clear photos"}


def test_parse_analysis_maps_fields_and_evidence() -> None:
    media_id = uuid.uuid4()
    result = _parse_analysis(_analysis_payload(), {"label": media_id}, uuid.uuid4())

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.field is Field.PRODUCT_WEIGHT_KG
    assert finding.value == 15.4
    assert finding.evidence_media_id == media_id
    assert result.annotations_by_media[media_id][0].alt_text


def test_parse_analysis_zeroes_confidence_on_a_null_value() -> None:
    """A confident null is still a null — it must never pass the gate."""
    result = _parse_analysis(_analysis_payload(value=None, confidence=0.99), {}, uuid.uuid4())
    finding = result.findings[0]
    assert finding.value is None
    assert finding.confidence.value == 0.0
    assert finding.meets_threshold is False


def test_parse_analysis_drops_unknown_field_names() -> None:
    payload = {
        "findings": [
            _analysis_payload()["findings"][0],
            {**_analysis_payload()["findings"][0], "field": "product.colour_vibe"},
        ],
        "notes": None,
    }
    result = _parse_analysis(payload, {}, uuid.uuid4())
    assert len(result.findings) == 1


def test_parse_analysis_rejects_an_empty_result() -> None:
    with pytest.raises(ProviderContractError):
        _parse_analysis({"findings": [], "notes": None}, {}, uuid.uuid4())


def test_parse_analysis_rejects_a_missing_findings_list() -> None:
    with pytest.raises(ProviderContractError):
        _parse_analysis({"notes": "nothing"}, {}, uuid.uuid4())


def test_parse_analysis_rejects_non_numeric_confidence() -> None:
    with pytest.raises(ProviderContractError):
        _parse_analysis(_analysis_payload(confidence="high"), {}, uuid.uuid4())


def test_parse_analysis_clamps_out_of_range_confidence() -> None:
    result = _parse_analysis(_analysis_payload(confidence=1.4), {}, uuid.uuid4())
    assert result.findings[0].confidence.value == 1.0


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [0.1, 0.2],  # too few values
        "0,0,1,1",  # not a list
        [0.5, 0.5, 0.9, 0.9],  # extends past the image edge
        ["a", 0, 0, 0],  # non-numeric
        [-0.1, 0.0, 0.5, 0.5],  # negative origin
    ],
)
def test_parse_bbox_rejects_bad_geometry_without_failing_the_analysis(raw: Any) -> None:
    """A malformed box costs an annotation, never the whole analysis."""
    assert _parse_bbox(raw) is None


def test_parse_bbox_accepts_valid_geometry() -> None:
    box = _parse_bbox([0.1, 0.2, 0.3, 0.4])
    assert box is not None
    assert box.center == pytest.approx((0.25, 0.4))


def _guide_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Mount the TV",
        "summary": "A five-step wall installation for your specific wall.",
        "difficulty": "moderate",
        "steps": [
            {
                "ordinal": 1,
                "title": "Mark the holes",
                "body": "Hold the bracket up and mark the hole centres.",
                "verification": "The marks are level.",
                "duration_minutes": 10,
                "tools": ["pencil"],
                "safety_note": None,
                "is_optional": False,
            },
            {
                "ordinal": 2,
                "title": "Drill",
                "body": "Drill the marked holes to depth.",
                "verification": "The holes are square to the wall.",
                "duration_minutes": 10,
                "tools": ["drill"],
                "safety_note": "Eye protection.",
                "is_optional": False,
            },
        ],
        "tools": [{"name": "Drill", "ownership": "required", "why": "holes", "substitute": None}],
        "materials": [
            {
                "product_class": "wall_fastener",
                "display_name": "Spring toggle",
                "quantity": 4,
                "attributes": {"key": "drywall_metal_toggle"},
                "citation": "reference table",
                "note": None,
            }
        ],
        "prerequisites": ["Read the product instructions."],
        "common_mistakes": ["Skipping the level check."],
        "final_verification": ["Pull firmly; no movement."],
    }
    payload.update(overrides)
    return payload


def test_parse_guide_builds_a_draft() -> None:
    draft = _parse_guide(_guide_payload())

    assert draft.title == "Mount the TV"
    assert len(draft.steps) == 2
    assert draft.materials[0].attributes["key"] == "drywall_metal_toggle"
    assert draft.difficulty is Difficulty.MODERATE


def test_parse_guide_rejects_a_step_without_verification() -> None:
    payload = _guide_payload()
    payload["steps"][1]["verification"] = "   "
    with pytest.raises(ProviderContractError) as excinfo:
        _parse_guide(payload)

    assert "verification" in (excinfo.value.log_detail or "")
    assert "verification" not in excinfo.value.message


def test_parse_guide_rejects_non_contiguous_ordinals() -> None:
    payload = _guide_payload()
    payload["steps"][1]["ordinal"] = 5
    with pytest.raises(ProviderContractError) as excinfo:
        _parse_guide(payload)

    assert "contiguous" in (excinfo.value.log_detail or "")


def test_parse_guide_rejects_an_empty_step_list() -> None:
    with pytest.raises(ProviderContractError) as excinfo:
        _parse_guide(_guide_payload(steps=[]))

    assert "no steps" in (excinfo.value.log_detail or "")


def test_parse_guide_falls_back_on_an_unknown_difficulty() -> None:
    draft = _parse_guide(_guide_payload(difficulty="terrifying"))
    assert draft.difficulty is Difficulty.MODERATE


def test_parse_step_validation_reads_issues() -> None:
    result = _parse_step_validation(
        {
            "verdict": "problem",
            "observations": ["The bracket is tilted."],
            "issues": [
                {"severity": "high", "description": "Not level", "fix": "Loosen and re-level"}
            ],
            "confidence": 0.8,
        }
    )
    assert result.verdict is Verdict.PROBLEM
    assert result.blocks_progress is True


def test_parse_step_validation_rejects_an_unknown_verdict() -> None:
    with pytest.raises(ProviderContractError) as excinfo:
        _parse_step_validation({"verdict": "probably fine", "confidence": 0.5})

    assert "verdict" in (excinfo.value.log_detail or "")
