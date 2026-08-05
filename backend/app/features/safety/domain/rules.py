"""Deterministic safety classification.

This module is the reason the product can be trusted. The model never decides
whether a job is safe: it supplies facts, and the rules here adjudicate. See
``docs/07-safety-policy.md`` for the policy this implements.

Design constraints, all enforced by tests:

* A rule can only ever *escalate*. Nothing in this file can lower severity.
* An unknown safety-critical input is treated as dangerous, never as absent.
* Every verdict carries a human-readable rationale and the rule codes that fired,
  so a guide can always be explained after the fact.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.features.knowledge.reference.materials import (
    WallMaterial,
    best_fastener_for,
    max_pullout_for,
    parse_material,
)
from app.features.vision.domain.entities import Analysis, Field
from app.shared.values import SafetyClass

#: Applied to every load comparison, in code, so no prompt can talk it away.
LOAD_SAFETY_FACTOR = 1.25

#: Above this, an unidentified wall is a red condition rather than a caution.
UNKNOWN_MATERIAL_LOAD_LIMIT_KG = 10.0

#: Ladder work above this height is referred out.
MAX_DIY_WORKING_HEIGHT_M = 2.0

#: A mount must be rated for the product weight plus the safety factor.
MOUNT_RATING_MARGIN = LOAD_SAFETY_FACTOR


class RuleCode(StrEnum):
    """Stable codes. They appear in audit records, so they are never renamed."""

    ELECTRICAL_MAINS = "electrical_mains"
    GAS_WORK = "gas_work"
    PLUMBING_MAINS = "plumbing_mains"
    STRUCTURAL = "structural"
    WORKING_AT_HEIGHT = "working_at_height"
    OVERHEAD_LOAD = "overhead_load"
    ASBESTOS_RISK = "asbestos_risk"
    LOAD_EXCEEDS_MATERIAL = "load_exceeds_material"
    LOAD_NEAR_MATERIAL_LIMIT = "load_near_material_limit"
    UNKNOWN_MATERIAL_HEAVY = "unknown_material_heavy"
    UNKNOWN_MATERIAL_LIGHT = "unknown_material_light"
    UNKNOWN_WEIGHT = "unknown_weight"
    MOUNT_UNDERRATED = "mount_underrated"
    MISSING_LOAD_BEARING_HARDWARE = "missing_load_bearing_hardware"
    DRILLING_NEAR_SERVICES = "drilling_near_services"
    MASONRY_OR_TILE_DRILLING = "masonry_or_tile_drilling"
    NO_KNOWLEDGE_COVERAGE = "no_knowledge_coverage"
    UNCERTAIN_SAFETY_FIELD = "uncertain_safety_field"
    RENTAL_IRREVERSIBLE = "rental_irreversible"
    MINOR_ACCOUNT = "minor_account"


@dataclass(frozen=True, slots=True)
class RuleHit:
    code: RuleCode
    classification: SafetyClass
    rationale: str
    citation: str | None = None
    #: True for the hard red lines in ``docs/07`` §2, which also withhold the
    #: step-by-step flow rather than merely warning.
    is_red_line: bool = False


@dataclass(frozen=True, slots=True)
class SafetyContext:
    """Non-analysis inputs the classifier needs."""

    knowledge_coverage: str = "none"
    site_is_rental: bool = False
    user_is_minor: bool = False
    involves_tools: bool = True


@dataclass(frozen=True, slots=True)
class SafetyVerdict:
    classification: SafetyClass
    hits: tuple[RuleHit, ...]
    #: Fastener the rules selected, when one applies. The guide generator may
    #: phrase this; it may not choose a different one.
    recommended_fastener_key: str | None = None
    material: WallMaterial = WallMaterial.UNKNOWN
    effective_load_kg: float | None = None

    @property
    def is_red_line(self) -> bool:
        return any(hit.is_red_line for hit in self.hits)

    @property
    def rationale(self) -> str:
        """One paragraph the user can read, most severe first."""
        if not self.hits:
            return "Nothing here needs special precautions beyond normal care with tools."
        ordered = sorted(self.hits, key=lambda hit: hit.classification.severity, reverse=True)
        return " ".join(hit.rationale for hit in ordered)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(hit.code.value for hit in self.hits)

    def hits_at(self, classification: SafetyClass) -> tuple[RuleHit, ...]:
        return tuple(hit for hit in self.hits if hit.classification is classification)


# ------------------------------------------------------------------ rules

Rule = Callable[[Analysis, SafetyContext], RuleHit | None]


def _rule_electrical(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    if not analysis.flag_of(Field.TASK_INVOLVES_ELECTRICAL):
        return None
    return RuleHit(
        code=RuleCode.ELECTRICAL_MAINS,
        classification=SafetyClass.RED,
        rationale=(
            "This involves mains electrical work. A qualified electrician should do it — "
            "wiring mistakes cause fires and shocks, and in most places this work is "
            "regulated."
        ),
        is_red_line=True,
    )


def _rule_gas(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    if not analysis.flag_of(Field.TASK_INVOLVES_GAS):
        return None
    return RuleHit(
        code=RuleCode.GAS_WORK,
        classification=SafetyClass.RED,
        rationale=(
            "This involves a gas appliance or gas pipework. Only a registered gas engineer "
            "may work on it. Do not attempt any part of this yourself."
        ),
        is_red_line=True,
    )


def _rule_plumbing(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    if not analysis.flag_of(Field.TASK_INVOLVES_PLUMBING):
        return None
    return RuleHit(
        code=RuleCode.PLUMBING_MAINS,
        classification=SafetyClass.RED,
        rationale=(
            "This touches mains water or a pressurised heating system. A plumber should do "
            "it — a failure here floods the property rather than just going wrong."
        ),
        is_red_line=True,
    )


def _rule_structural(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    if not analysis.flag_of(Field.TASK_INVOLVES_STRUCTURAL):
        return None
    return RuleHit(
        code=RuleCode.STRUCTURAL,
        classification=SafetyClass.RED,
        rationale=(
            "This affects something structural — a load-bearing wall, joist or beam. That "
            "needs a structural engineer or builder, not a DIY guide."
        ),
        is_red_line=True,
    )


def _rule_height(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    height = analysis.number_of(Field.TASK_WORKING_HEIGHT_M)
    if height is None or height <= MAX_DIY_WORKING_HEIGHT_M:
        return None
    return RuleHit(
        code=RuleCode.WORKING_AT_HEIGHT,
        classification=SafetyClass.RED,
        rationale=(
            f"The work is about {height:.1f} m up. Falls from a ladder are the most common "
            "serious DIY injury, so this should be done by someone with proper access "
            "equipment."
        ),
        is_red_line=True,
    )


def _rule_overhead(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    if not analysis.flag_of(Field.TASK_IS_OVERHEAD):
        return None
    weight = analysis.number_of(Field.PRODUCT_WEIGHT_KG)
    if weight is not None and weight < 3:
        return None
    return RuleHit(
        code=RuleCode.OVERHEAD_LOAD,
        classification=SafetyClass.RED,
        rationale=(
            "This hangs something heavy overhead. If an overhead fixing fails it lands on "
            "whoever is underneath, so it should be installed by a professional."
        ),
        is_red_line=True,
    )


def _rule_asbestos(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    obstacles = analysis.value_of(Field.WALL_OBSTACLES) or []
    finish = str(analysis.value_of(Field.WALL_FINISH) or "").lower()
    markers = ("asbestos", "artex", "textured coating", "popcorn ceiling")
    haystack = f"{finish} {' '.join(str(item).lower() for item in obstacles)}"
    if not any(marker in haystack for marker in markers):
        return None
    return RuleHit(
        code=RuleCode.ASBESTOS_RISK,
        classification=SafetyClass.RED,
        rationale=(
            "The surface looks like an older textured coating, which can contain asbestos. "
            "Do not drill, sand or scrape it. Have it tested first."
        ),
        is_red_line=True,
    )


def _rule_minor_account(_: Analysis, context: SafetyContext) -> RuleHit | None:
    if not (context.user_is_minor and context.involves_tools):
        return None
    return RuleHit(
        code=RuleCode.MINOR_ACCOUNT,
        classification=SafetyClass.RED,
        rationale="An adult should carry out any work involving power tools.",
        is_red_line=True,
    )


def _effective_load_kg(analysis: Analysis) -> float | None:
    weight = analysis.number_of(Field.PRODUCT_WEIGHT_KG)
    return None if weight is None else weight * LOAD_SAFETY_FACTOR


def _rule_load_vs_material(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    material = parse_material(analysis.value_of(Field.WALL_MATERIAL))
    load = _effective_load_kg(analysis)
    if load is None or not material.is_known:
        return None

    capacity = max_pullout_for(material)
    if capacity <= 0:
        return None

    if load > capacity:
        return RuleHit(
            code=RuleCode.LOAD_EXCEEDS_MATERIAL,
            classification=SafetyClass.RED,
            rationale=(
                f"With a safety margin, this needs to hold about {load:.0f} kg, and the best "
                f"fixing available in this wall type carries roughly {capacity:.0f} kg. The "
                "wall itself is the limit here, so it needs a different fixing method or a "
                "professional."
            ),
            citation="fasteners reference table",
            is_red_line=True,
        )
    if load > capacity * 0.6:
        return RuleHit(
            code=RuleCode.LOAD_NEAR_MATERIAL_LIMIT,
            classification=SafetyClass.YELLOW,
            rationale=(
                f"This is a heavy load for this wall — around {load:.0f} kg against a "
                f"practical limit near {capacity:.0f} kg. Use exactly the fixing we specify, "
                "in every hole, and fix into a stud if you can find one."
            ),
            citation="fasteners reference table",
        )
    return None


def _rule_unknown_material(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    material = parse_material(analysis.value_of(Field.WALL_MATERIAL))
    if material.is_known:
        return None

    weight = analysis.number_of(Field.PRODUCT_WEIGHT_KG)
    if weight is None or weight > UNKNOWN_MATERIAL_LOAD_LIMIT_KG:
        return RuleHit(
            code=RuleCode.UNKNOWN_MATERIAL_HEAVY,
            classification=SafetyClass.RED,
            rationale=(
                "We could not tell what this wall is made of, and the load is too heavy to "
                "guess. The right anchor depends entirely on the wall, so we won't recommend "
                "one blind."
            ),
            is_red_line=True,
        )
    return RuleHit(
        code=RuleCode.UNKNOWN_MATERIAL_LIGHT,
        classification=SafetyClass.YELLOW,
        rationale=(
            "We could not confirm the wall type. The load is light, but check the wall "
            "before drilling — tap it and see whether it sounds hollow or solid."
        ),
    )


def _rule_unknown_weight(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    if analysis.number_of(Field.PRODUCT_WEIGHT_KG) is not None:
        return None
    material = parse_material(analysis.value_of(Field.WALL_MATERIAL))
    if not material.is_known:
        return None  # already covered, more severely, by the unknown-material rule
    return RuleHit(
        code=RuleCode.UNKNOWN_WEIGHT,
        classification=SafetyClass.RED,
        rationale=(
            "We could not establish how heavy this is, and weight decides which anchor is "
            "safe. Find the weight on the label or in the manual before fixing anything to "
            "the wall."
        ),
        is_red_line=True,
    )


def _rule_mount_rating(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    rating = analysis.number_of(Field.MOUNT_MAX_LOAD_KG)
    weight = analysis.number_of(Field.PRODUCT_WEIGHT_KG)
    if rating is None or weight is None:
        return None
    if rating >= weight * MOUNT_RATING_MARGIN:
        return None
    return RuleHit(
        code=RuleCode.MOUNT_UNDERRATED,
        classification=SafetyClass.RED,
        rationale=(
            f"The mount is rated for about {rating:.0f} kg and the product weighs roughly "
            f"{weight:.0f} kg. That leaves no margin. Use a mount rated well above the "
            "product's weight."
        ),
        citation="mount manufacturer rating vs. product weight",
        is_red_line=True,
    )


def _rule_missing_hardware(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    missing = analysis.value_of(Field.MOUNT_MISSING_PARTS) or []
    if not missing:
        return None
    return RuleHit(
        code=RuleCode.MISSING_LOAD_BEARING_HARDWARE,
        classification=SafetyClass.RED,
        rationale=(
            "Parts that carry the load appear to be missing: "
            f"{', '.join(str(item) for item in missing)}. Get the correct parts from the "
            "manufacturer before starting — substitutes are the usual reason these fail."
        ),
        is_red_line=True,
    )


def _rule_services(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    obstacles = [str(item).lower() for item in (analysis.value_of(Field.WALL_OBSTACLES) or [])]
    markers = ("cable", "wire", "wiring", "pipe", "conduit", "socket", "switch", "outlet")
    if not any(marker in item for item in obstacles for marker in markers):
        return None
    return RuleHit(
        code=RuleCode.DRILLING_NEAR_SERVICES,
        classification=SafetyClass.YELLOW,
        rationale=(
            "There may be cables or pipes in this wall. Scan the area with a detector before "
            "you drill, and stay clear of the zones directly above and beside sockets and "
            "switches."
        ),
    )


def _rule_masonry_drilling(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    material = parse_material(analysis.value_of(Field.WALL_MATERIAL))
    if material not in (
        WallMaterial.TILE_OVER_MASONRY,
        WallMaterial.TILE_OVER_DRYWALL,
        WallMaterial.CONCRETE,
        WallMaterial.BRICK,
    ):
        return None
    is_tile = material in (WallMaterial.TILE_OVER_MASONRY, WallMaterial.TILE_OVER_DRYWALL)
    return RuleHit(
        code=RuleCode.MASONRY_OR_TILE_DRILLING,
        classification=SafetyClass.YELLOW,
        rationale=(
            "Drilling tile takes care: low speed, no hammer action until you are through the "
            "glaze, and tape over the spot so the bit cannot wander. A cracked tile is hard "
            "to put right."
            if is_tile
            else "Drilling masonry needs a hammer drill and a dust mask. Take it slowly and "
            "clear the hole before setting the anchor."
        ),
    )


def _rule_knowledge_coverage(analysis: Analysis, context: SafetyContext) -> RuleHit | None:
    if context.knowledge_coverage != "none":
        return None
    weight = analysis.number_of(Field.PRODUCT_WEIGHT_KG)
    if weight is None or weight < 5:
        return None
    return RuleHit(
        code=RuleCode.NO_KNOWLEDGE_COVERAGE,
        classification=SafetyClass.YELLOW,
        rationale=(
            "We have no manufacturer documentation for this specific product, so treat our "
            "figures as general guidance and follow the instructions in the box where they "
            "differ."
        ),
    )


def _rule_uncertain_fields(analysis: Analysis, _: SafetyContext) -> RuleHit | None:
    uncertain = analysis.uncertain_safety_fields()
    if not uncertain:
        return None
    names = ", ".join(field_key.value for field_key in uncertain)
    return RuleHit(
        code=RuleCode.UNCERTAIN_SAFETY_FIELD,
        classification=SafetyClass.YELLOW,
        rationale=(
            "We are not fully certain about some safety-relevant details "
            f"({names}). Double-check them yourself before you start."
        ),
    )


def _rule_rental(analysis: Analysis, context: SafetyContext) -> RuleHit | None:
    if not context.site_is_rental:
        return None
    weight = analysis.number_of(Field.PRODUCT_WEIGHT_KG)
    if weight is None or weight < 5:
        return None
    return RuleHit(
        code=RuleCode.RENTAL_IRREVERSIBLE,
        classification=SafetyClass.YELLOW,
        rationale=(
            "This is a rented property and the fixing leaves holes that need making good. "
            "Check with your landlord first, and ask us about reversible alternatives."
        ),
    )


#: Evaluation order is irrelevant to the outcome — the most severe hit wins — but
#: it is kept stable so rationale text reads consistently.
RULES: tuple[Rule, ...] = (
    _rule_electrical,
    _rule_gas,
    _rule_plumbing,
    _rule_structural,
    _rule_height,
    _rule_overhead,
    _rule_asbestos,
    _rule_minor_account,
    _rule_load_vs_material,
    _rule_unknown_material,
    _rule_unknown_weight,
    _rule_mount_rating,
    _rule_missing_hardware,
    _rule_services,
    _rule_masonry_drilling,
    _rule_knowledge_coverage,
    _rule_uncertain_fields,
    _rule_rental,
)


@dataclass(slots=True)
class SafetyClassifier:
    """Evaluates every rule and returns the most severe outcome.

    Stateless and pure: same inputs, same verdict, always. That is what makes the
    regression fixtures in ``tests/`` meaningful.
    """

    rules: Sequence[Rule] = field(default_factory=lambda: RULES)

    def classify(self, analysis: Analysis, context: SafetyContext | None = None) -> SafetyVerdict:
        ctx = context or SafetyContext()
        hits = tuple(hit for rule in self.rules if (hit := rule(analysis, ctx)) is not None)

        classification = SafetyClass.most_severe(hit.classification for hit in hits)
        material = parse_material(analysis.value_of(Field.WALL_MATERIAL))
        load = _effective_load_kg(analysis)

        fastener_key: str | None = None
        # Only recommend a fixing when the situation is actually resolvable.
        if classification is not SafetyClass.RED and material.is_known and load is not None:
            fastener = best_fastener_for(material, load)
            fastener_key = fastener.key if fastener is not None else None

        return SafetyVerdict(
            classification=classification,
            hits=hits,
            recommended_fastener_key=fastener_key,
            material=material,
            effective_load_kg=load,
        )
