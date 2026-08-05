"""JSON schemas for provider structured-output modes.

These constrain what a provider is allowed to return. They are generated from the
domain vocabulary rather than hand-listed, so adding a ``Field`` cannot leave the
schema behind — that drift is exactly how a confidence threshold or safety rule
gets silently bypassed.

Written for OpenAI's strict ``json_schema`` mode (every property required,
``additionalProperties: false``); other providers accept the same shape.
"""

from __future__ import annotations

from typing import Any

from app.features.vision.domain.entities import Field
from app.shared.values import Difficulty

# ------------------------------------------------------------------ analysis

_FINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "field",
        "value",
        "unit",
        "confidence",
        "evidence_role",
        "bbox",
        "annotate",
        "label",
        "alt_text",
        "note",
    ],
    "properties": {
        "field": {
            "type": "string",
            # The controlled vocabulary, straight from the domain enum.
            "enum": [field.value for field in Field],
        },
        # Deliberately untyped: values are strings, numbers, booleans or lists
        # depending on the field. Normalisation happens in the adapter, and an
        # explicit null is a valid, expected answer.
        "value": {
            "anyOf": [
                {"type": "string"},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "array", "items": {"type": "string"}},
                {"type": "null"},
            ]
        },
        "unit": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_role": {"type": ["string", "null"]},
        "bbox": {
            "anyOf": [
                {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0, "maximum": 1},
                    "minItems": 4,
                    "maxItems": 4,
                },
                {"type": "null"},
            ]
        },
        "annotate": {"type": "boolean"},
        "label": {"type": ["string", "null"]},
        "alt_text": {"type": ["string", "null"]},
        "note": {"type": ["string", "null"]},
    },
}

ANALYSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings", "notes"],
    "properties": {
        "findings": {"type": "array", "items": _FINDING_SCHEMA, "minItems": 1},
        "notes": {"type": ["string", "null"]},
    },
}

# ------------------------------------------------------------------ guide

_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "ordinal",
        "title",
        "body",
        "verification",
        "duration_minutes",
        "tools",
        "safety_note",
        "is_optional",
    ],
    "properties": {
        "ordinal": {"type": "integer", "minimum": 1},
        "title": {"type": "string", "minLength": 3},
        "body": {"type": "string", "minLength": 10},
        # Required by the domain: a step without a check is not a step.
        "verification": {"type": "string", "minLength": 5},
        "duration_minutes": {"type": "integer", "minimum": 1, "maximum": 480},
        "tools": {"type": "array", "items": {"type": "string"}},
        "safety_note": {"type": ["string", "null"]},
        "is_optional": {"type": "boolean"},
    },
}

_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "ownership", "why", "substitute"],
    "properties": {
        "name": {"type": "string"},
        "ownership": {"type": "string", "enum": ["required", "optional", "alternative"]},
        "why": {"type": ["string", "null"]},
        "substitute": {"type": ["string", "null"]},
    },
}

_MATERIAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["product_class", "display_name", "quantity", "attributes", "citation", "note"],
    "properties": {
        # A product class plus attributes, never a SKU: retailer integrations
        # attach SKUs later without changing this contract.
        "product_class": {"type": "string"},
        "display_name": {"type": "string"},
        "quantity": {"type": "integer", "minimum": 1},
        "attributes": {"type": "object", "additionalProperties": {"type": "string"}},
        "citation": {"type": ["string", "null"]},
        "note": {"type": ["string", "null"]},
    },
}

GUIDE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "summary",
        "difficulty",
        "steps",
        "tools",
        "materials",
        "prerequisites",
        "common_mistakes",
        "final_verification",
    ],
    "properties": {
        "title": {"type": "string", "minLength": 3, "maxLength": 80},
        "summary": {"type": "string", "minLength": 20},
        "difficulty": {"type": "string", "enum": [level.value for level in Difficulty]},
        "steps": {"type": "array", "items": _STEP_SCHEMA, "minItems": 2, "maxItems": 20},
        "tools": {"type": "array", "items": _TOOL_SCHEMA},
        "materials": {"type": "array", "items": _MATERIAL_SCHEMA},
        "prerequisites": {"type": "array", "items": {"type": "string"}},
        "common_mistakes": {"type": "array", "items": {"type": "string"}},
        "final_verification": {"type": "array", "items": {"type": "string"}},
    },
}

# ------------------------------------------------------------------ step validation

STEP_VALIDATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "observations", "issues", "confidence"],
    "properties": {
        # "unsure" is a first-class outcome, not a failure mode.
        "verdict": {"type": "string", "enum": ["ok", "unsure", "problem"]},
        "observations": {"type": "array", "items": {"type": "string"}},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "description", "fix"],
                "properties": {
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "description": {"type": "string"},
                    "fix": {"type": "string"},
                },
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}
