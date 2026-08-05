"""Curated reference data for wall materials and fasteners.

This is the deterministic backbone of the safety layer. The model does not choose
an anchor — it identifies a material, and these tables decide what may be used
(``docs/07-safety-policy.md`` §4).

Ships as code so it is versioned, reviewable and testable; mirrored into the
``materials`` / ``fasteners`` tables by a seed migration so retrieval and admin
tooling can read it too.

Pull-out figures are conservative single-fastener values for domestic
construction and are cited. They are deliberately lower than manufacturer
best-case laboratory figures.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.shared.values import LoadClass


class WallMaterial(StrEnum):
    DRYWALL = "drywall"
    PLASTER_LATH = "plaster_lath"
    CONCRETE = "concrete"
    BRICK = "brick"
    HOLLOW_BLOCK = "hollow_block"
    AERATED_BLOCK = "aerated_block"
    TILE_OVER_MASONRY = "tile_over_masonry"
    TILE_OVER_DRYWALL = "tile_over_drywall"
    WOOD_STUD = "wood_stud"
    METAL_STUD = "metal_stud"
    UNKNOWN = "unknown"

    @property
    def is_solid(self) -> bool:
        return self in _SOLID_MATERIALS

    @property
    def is_known(self) -> bool:
        return self is not WallMaterial.UNKNOWN


_SOLID_MATERIALS = frozenset(
    {
        WallMaterial.CONCRETE,
        WallMaterial.BRICK,
        WallMaterial.AERATED_BLOCK,
        WallMaterial.TILE_OVER_MASONRY,
        WallMaterial.WOOD_STUD,
    }
)


@dataclass(frozen=True, slots=True)
class MaterialSpec:
    key: WallMaterial
    display_name: str
    #: Cues fed into the vision prompt so identification is grounded in something.
    visual_cues: tuple[str, ...]
    drilling_notes: str
    hazards: tuple[str, ...]
    #: Highest load class this material can carry at all, with a correct fastener.
    max_load_class: LoadClass


MATERIALS: dict[WallMaterial, MaterialSpec] = {
    WallMaterial.DRYWALL: MaterialSpec(
        key=WallMaterial.DRYWALL,
        display_name="Plasterboard / drywall",
        visual_cues=(
            "smooth painted surface that sounds hollow when tapped",
            "visible screw dimples in a vertical line",
            "paper-faced core visible at a cut or socket edge",
        ),
        drilling_notes=(
            "Drill on a low speed without hammer action. The board itself carries almost "
            "nothing — the anchor or the stud behind it does the work."
        ),
        hazards=(
            "cables and pipes run inside the cavity",
            "board alone will pull out under load",
        ),
        max_load_class=LoadClass.MEDIUM,
    ),
    WallMaterial.PLASTER_LATH: MaterialSpec(
        key=WallMaterial.PLASTER_LATH,
        display_name="Lath and plaster",
        visual_cues=(
            "older property with slightly uneven wall surface",
            "grey-brown plaster with fibrous backing at a break",
            "hollow sound with a denser feel than plasterboard",
        ),
        drilling_notes=(
            "Plaster is brittle and chips easily. Use a low speed, no hammer action, and "
            "fix into the timber lath or a stud wherever possible."
        ),
        hazards=(
            "plaster crumbles and enlarges the hole",
            "pre-1990 textured coatings may contain asbestos — do not sand or drill",
        ),
        max_load_class=LoadClass.LIGHT,
    ),
    WallMaterial.CONCRETE: MaterialSpec(
        key=WallMaterial.CONCRETE,
        display_name="Concrete",
        visual_cues=(
            "solid, dense sound when tapped",
            "grey aggregate visible where unpainted",
            "no give at all under pressure",
        ),
        drilling_notes=(
            "Hammer drill with a masonry bit. Drill 10 mm deeper than the anchor and clear "
            "the dust out, or the anchor will not seat."
        ),
        hazards=("silica dust — wear a mask", "embedded rebar and conduit"),
        max_load_class=LoadClass.VERY_HEAVY,
    ),
    WallMaterial.BRICK: MaterialSpec(
        key=WallMaterial.BRICK,
        display_name="Brick",
        visual_cues=(
            "visible brick courses or mortar lines under paint",
            "solid sound when tapped",
        ),
        drilling_notes=(
            "Fix into the brick body, not the mortar joint. Hammer drill with a masonry bit."
        ),
        hazards=("older bricks can be soft and crumble", "cavity walls behind the outer leaf"),
        max_load_class=LoadClass.VERY_HEAVY,
    ),
    WallMaterial.HOLLOW_BLOCK: MaterialSpec(
        key=WallMaterial.HOLLOW_BLOCK,
        display_name="Hollow concrete block",
        visual_cues=(
            "large regular block courses",
            "hollow sound in the middle of a block, solid at the webs",
        ),
        drilling_notes=(
            "Treat as a hollow wall: the shell is thin. Use a sleeve or toggle designed for "
            "hollow block, and avoid hammer action which can blow out the shell."
        ),
        hazards=("shell breaks out under point load", "voids give a false sense of depth"),
        max_load_class=LoadClass.HEAVY,
    ),
    WallMaterial.AERATED_BLOCK: MaterialSpec(
        key=WallMaterial.AERATED_BLOCK,
        display_name="Aerated concrete block (AAC)",
        visual_cues=("pale, lightweight, slightly porous surface", "drills very easily"),
        drilling_notes=(
            "Do not use hammer action. Use anchors specifically rated for aerated concrete; "
            "ordinary plastic plugs spin out."
        ),
        hazards=("very low pull-out strength for its apparent solidity",),
        max_load_class=LoadClass.MEDIUM,
    ),
    WallMaterial.TILE_OVER_MASONRY: MaterialSpec(
        key=WallMaterial.TILE_OVER_MASONRY,
        display_name="Tile over masonry",
        visual_cues=("tiled surface with solid sound", "grout lines"),
        drilling_notes=(
            "Start with a tile bit at low speed and no hammer, through the tile only, then "
            "switch to masonry. Tape the spot to stop the bit wandering."
        ),
        hazards=("tile cracks if hammer action is used", "hidden pipework in wet rooms"),
        max_load_class=LoadClass.HEAVY,
    ),
    WallMaterial.TILE_OVER_DRYWALL: MaterialSpec(
        key=WallMaterial.TILE_OVER_DRYWALL,
        display_name="Tile over plasterboard",
        visual_cues=("tiled surface that sounds hollow", "common in bathrooms"),
        drilling_notes=(
            "Through the tile at low speed with no hammer, then treat the wall behind as "
            "plasterboard. Load is carried by the anchor, not the tile."
        ),
        hazards=("tile cracking", "very low load capacity", "concealed plumbing"),
        max_load_class=LoadClass.LIGHT,
    ),
    WallMaterial.WOOD_STUD: MaterialSpec(
        key=WallMaterial.WOOD_STUD,
        display_name="Timber stud",
        visual_cues=(
            "stud finder or dense sound at regular intervals",
            "screw line visible in the board finish",
        ),
        drilling_notes=(
            "Fix directly into the centre of the stud with a wood screw and a pilot hole. "
            "This is the strongest fixing available in a stud wall."
        ),
        hazards=("cables drilled through stud centres", "missing the stud edge-on"),
        max_load_class=LoadClass.VERY_HEAVY,
    ),
    WallMaterial.METAL_STUD: MaterialSpec(
        key=WallMaterial.METAL_STUD,
        display_name="Metal stud",
        visual_cues=("magnet sticks in a regular vertical line", "thin metallic click when tapped"),
        drilling_notes=(
            "Thin-gauge steel: use a toggle rated for metal studs. A wood screw will strip "
            "and spin."
        ),
        hazards=("sharp edges inside the cavity", "much weaker than timber studs"),
        max_load_class=LoadClass.MEDIUM,
    ),
    WallMaterial.UNKNOWN: MaterialSpec(
        key=WallMaterial.UNKNOWN,
        display_name="Unknown wall type",
        visual_cues=(),
        drilling_notes="Identify the wall before drilling anything.",
        hazards=("unknown capacity", "unknown services behind the surface"),
        max_load_class=LoadClass.LIGHT,
    ),
}


@dataclass(frozen=True, slots=True)
class FastenerSpec:
    """A fastener option for one material and load class.

    ``pullout_kg_typical`` is the conservative per-fastener figure the safety
    rules compare against. It is *not* a manufacturer maximum.
    """

    key: str
    display_name: str
    material: WallMaterial
    load_class: LoadClass
    pullout_kg_typical: float
    min_substrate_thickness_mm: float | None
    requires_pilot_hole: bool
    pilot_diameter_mm: float | None
    notes: str
    citation: str


#: Curated. Each row is reviewed and cited; adding one is a reviewed change.
FASTENERS: tuple[FastenerSpec, ...] = (
    FastenerSpec(
        key="drywall_plastic_plug",
        display_name="Plastic wall plug in plasterboard",
        material=WallMaterial.DRYWALL,
        load_class=LoadClass.LIGHT,
        pullout_kg_typical=4.0,
        min_substrate_thickness_mm=9.5,
        requires_pilot_hole=True,
        pilot_diameter_mm=5.0,
        notes=(
            "Light items only - a picture frame, a small hook. Not for anything you "
            "would mind falling."
        ),
        citation="Manufacturer light-duty rating, derated for domestic 12.5 mm board",
    ),
    FastenerSpec(
        key="drywall_self_drill_anchor",
        display_name="Self-drilling plasterboard anchor",
        material=WallMaterial.DRYWALL,
        load_class=LoadClass.LIGHT,
        pullout_kg_typical=7.0,
        min_substrate_thickness_mm=12.5,
        requires_pilot_hole=False,
        pilot_diameter_mm=None,
        notes="Convenient and reliable for light shelving. Do not over-tighten.",
        citation="Manufacturer rating for 12.5 mm board, 50% derated",
    ),
    FastenerSpec(
        key="drywall_metal_toggle",
        display_name="Spring toggle / metal cavity anchor in plasterboard",
        material=WallMaterial.DRYWALL,
        load_class=LoadClass.MEDIUM,
        pullout_kg_typical=18.0,
        min_substrate_thickness_mm=12.5,
        requires_pilot_hole=True,
        pilot_diameter_mm=12.0,
        notes=(
            "Spreads load across the back of the board. The strongest plasterboard-only "
            "option, but a stud fixing is always better if one is available."
        ),
        citation="Manufacturer rating for 12.5 mm board, 50% derated",
    ),
    FastenerSpec(
        key="wood_stud_screw",
        display_name="Wood screw into a timber stud",
        material=WallMaterial.WOOD_STUD,
        load_class=LoadClass.VERY_HEAVY,
        pullout_kg_typical=90.0,
        min_substrate_thickness_mm=38.0,
        requires_pilot_hole=True,
        pilot_diameter_mm=4.0,
        notes="Centre of the stud, pilot hole first, at least 40 mm of thread into timber.",
        citation="Softwood withdrawal capacity, conservative domestic value",
    ),
    FastenerSpec(
        key="metal_stud_toggle",
        display_name="Toggle anchor rated for metal studs",
        material=WallMaterial.METAL_STUD,
        load_class=LoadClass.MEDIUM,
        pullout_kg_typical=20.0,
        min_substrate_thickness_mm=0.5,
        requires_pilot_hole=True,
        pilot_diameter_mm=10.0,
        notes="Thin steel strips easily — use a toggle, never a wood screw.",
        citation="Manufacturer rating for 0.5-0.9 mm gauge stud, derated",
    ),
    FastenerSpec(
        key="concrete_sleeve_anchor",
        display_name="Sleeve anchor in concrete",
        material=WallMaterial.CONCRETE,
        load_class=LoadClass.VERY_HEAVY,
        pullout_kg_typical=180.0,
        min_substrate_thickness_mm=80.0,
        requires_pilot_hole=True,
        pilot_diameter_mm=10.0,
        notes="Clear the dust from the hole before setting, or it will not reach full capacity.",
        citation="Manufacturer characteristic resistance with a 4:1 safety factor",
    ),
    FastenerSpec(
        key="brick_frame_fixing",
        display_name="Frame fixing / hammer-in plug in brick",
        material=WallMaterial.BRICK,
        load_class=LoadClass.HEAVY,
        pullout_kg_typical=100.0,
        min_substrate_thickness_mm=100.0,
        requires_pilot_hole=True,
        pilot_diameter_mm=8.0,
        notes="Fix into the brick body, not the mortar joint.",
        citation="Manufacturer rating in solid clay brick with a 4:1 safety factor",
    ),
    FastenerSpec(
        key="hollow_block_toggle",
        display_name="Heavy-duty toggle in hollow block",
        material=WallMaterial.HOLLOW_BLOCK,
        load_class=LoadClass.HEAVY,
        pullout_kg_typical=45.0,
        min_substrate_thickness_mm=25.0,
        requires_pilot_hole=True,
        pilot_diameter_mm=13.0,
        notes="The shell is thin; the toggle must open fully inside the void.",
        citation="Manufacturer rating in hollow block, derated",
    ),
    FastenerSpec(
        key="aac_anchor",
        display_name="Aerated concrete anchor",
        material=WallMaterial.AERATED_BLOCK,
        load_class=LoadClass.MEDIUM,
        pullout_kg_typical=25.0,
        min_substrate_thickness_mm=100.0,
        requires_pilot_hole=True,
        pilot_diameter_mm=8.0,
        notes="Only anchors specifically rated for AAC. No hammer action.",
        citation="Manufacturer AAC-specific rating with a 4:1 safety factor",
    ),
    FastenerSpec(
        key="plaster_lath_into_timber",
        display_name="Screw through lath and plaster into timber",
        material=WallMaterial.PLASTER_LATH,
        load_class=LoadClass.LIGHT,
        pullout_kg_typical=12.0,
        min_substrate_thickness_mm=38.0,
        requires_pilot_hole=True,
        pilot_diameter_mm=3.5,
        notes="Find the stud or a lath batten. Plaster alone holds almost nothing.",
        citation="Softwood withdrawal, derated for plaster crushing",
    ),
    FastenerSpec(
        key="tile_masonry_anchor",
        display_name="Masonry anchor through tile into solid wall",
        material=WallMaterial.TILE_OVER_MASONRY,
        load_class=LoadClass.HEAVY,
        pullout_kg_typical=80.0,
        min_substrate_thickness_mm=100.0,
        requires_pilot_hole=True,
        pilot_diameter_mm=8.0,
        notes="Tile bit through the glaze first, no hammer until you are past the tile.",
        citation="Masonry anchor rating, derated for tile spalling risk",
    ),
    FastenerSpec(
        key="tile_drywall_toggle",
        display_name="Cavity toggle through tile into plasterboard",
        material=WallMaterial.TILE_OVER_DRYWALL,
        load_class=LoadClass.LIGHT,
        pullout_kg_typical=10.0,
        min_substrate_thickness_mm=12.5,
        requires_pilot_hole=True,
        pilot_diameter_mm=12.0,
        notes="Low capacity. For anything heavy in a tiled stud wall, fix to the studs.",
        citation="Cavity anchor rating in 12.5 mm board, derated for tile",
    ),
)


def fasteners_for(material: WallMaterial) -> tuple[FastenerSpec, ...]:
    return tuple(spec for spec in FASTENERS if spec.material is material)


def best_fastener_for(material: WallMaterial, load_kg: float) -> FastenerSpec | None:
    """Weakest fastener that still carries the load — the least invasive option.

    Returns ``None`` when nothing in the table can carry it, which the safety
    classifier treats as a red condition rather than "use something bigger".
    """
    candidates = [spec for spec in fasteners_for(material) if spec.pullout_kg_typical >= load_kg]
    if not candidates:
        return None
    return min(candidates, key=lambda spec: spec.pullout_kg_typical)


def max_pullout_for(material: WallMaterial) -> float:
    """The strongest per-fastener capacity available in this material."""
    specs = fasteners_for(material)
    return max((spec.pullout_kg_typical for spec in specs), default=0.0)


def parse_material(value: object) -> WallMaterial:
    """Map a model-supplied string onto the controlled vocabulary.

    Anything unrecognised becomes ``UNKNOWN`` — never the nearest guess. A wrong
    material is worse than an admitted unknown, because the anchor table trusts
    this value.
    """
    if isinstance(value, WallMaterial):
        return value
    if not isinstance(value, str):
        return WallMaterial.UNKNOWN
    normalised = value.strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return WallMaterial(normalised)
    except ValueError:
        return _MATERIAL_SYNONYMS.get(normalised, WallMaterial.UNKNOWN)


_MATERIAL_SYNONYMS: dict[str, WallMaterial] = {
    "plasterboard": WallMaterial.DRYWALL,
    "gypsum": WallMaterial.DRYWALL,
    "gypsum_board": WallMaterial.DRYWALL,
    "sheetrock": WallMaterial.DRYWALL,
    "gib": WallMaterial.DRYWALL,
    "lath_and_plaster": WallMaterial.PLASTER_LATH,
    "lath": WallMaterial.PLASTER_LATH,
    "plaster": WallMaterial.PLASTER_LATH,
    "cement": WallMaterial.CONCRETE,
    "poured_concrete": WallMaterial.CONCRETE,
    "cinder_block": WallMaterial.HOLLOW_BLOCK,
    "breeze_block": WallMaterial.HOLLOW_BLOCK,
    "concrete_block": WallMaterial.HOLLOW_BLOCK,
    "thermalite": WallMaterial.AERATED_BLOCK,
    "aac": WallMaterial.AERATED_BLOCK,
    "ytong": WallMaterial.AERATED_BLOCK,
    "masonry": WallMaterial.BRICK,
    "timber_stud": WallMaterial.WOOD_STUD,
    "wood": WallMaterial.WOOD_STUD,
    "stud": WallMaterial.WOOD_STUD,
    "steel_stud": WallMaterial.METAL_STUD,
    "tile": WallMaterial.TILE_OVER_MASONRY,
    "tiled": WallMaterial.TILE_OVER_MASONRY,
}
