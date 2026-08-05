"""Deterministic in-process adapter.

Not a bolted-on test double: this is the default provider for local development
and CI (``AI_PROVIDER=fake``). It means the whole pipeline — confidence gate,
safety classifier, annotation, validation — is exercised offline, for free, with
reproducible output.

Determinism comes from seeding on the request content, so the same photos and the
same intent always produce the same analysis. Scenario keywords in the intent
text let tests drive specific situations (a heavy TV on plasterboard, an
electrical job, an unknown wall) without fixtures on disk.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal

from app.core.errors import ProviderContractError
from app.features.guides.domain.entities import (
    Annotation,
    GuideStep,
    Material,
    Tool,
    ToolOwnership,
)
from app.features.knowledge.reference.materials import WallMaterial
from app.features.vision.domain.entities import Field, Finding, FindingSource
from app.infrastructure.ai.models import (
    Capability,
    ChatChunk,
    ChatRequest,
    ChunkKind,
    GuideDraft,
    GuideRequest,
    StepIssue,
    StepValidationRequest,
    StepValidationResult,
    Tier,
    Usage,
    Verdict,
    VisionRequest,
    VisionResult,
)
from app.shared.values import BoundingBox, Confidence, Difficulty, Workflow

EMBEDDING_DIMENSIONS = 1536


class FakeAdapter:
    """Implements ``AIAdapter`` with seeded, scenario-aware fixtures."""

    def __init__(self, *, fail_contract: bool = False, seed_salt: str = "") -> None:
        # ``fail_contract`` lets tests exercise the gateway's repair-and-fallback path.
        self._fail_contract = fail_contract
        self._seed_salt = seed_salt

    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.VISION,
                Capability.STRUCTURED_OUTPUT,
                Capability.STREAMING,
                Capability.EMBEDDING,
            }
        )

    def model_for(self, capability: Capability, tier: Tier) -> str:
        return f"fake-{capability.value}-{tier.value}"

    # -------------------------------------------------------------- vision

    async def analyze(self, request: VisionRequest) -> tuple[VisionResult, Usage]:
        if self._fail_contract:
            raise ProviderContractError(log_detail="fake adapter configured to fail")

        rng = self._rng(request.intent_text, *(str(i.media_id) for i in request.images))
        scenario = _Scenario.from_text(request.intent_text)

        findings: list[Finding] = []
        primary_media = request.images[0].media_id

        def add(field: Field, value: object, confidence: float, **kwargs: object) -> None:
            findings.append(
                Finding(
                    field=field,
                    value=value,
                    confidence=Confidence(round(confidence, 3)),
                    source=FindingSource.VISION,
                    evidence_media_id=primary_media,
                    **kwargs,  # type: ignore[arg-type]
                )
            )

        add(Field.PRODUCT_CATEGORY, scenario.category, 0.93)
        add(Field.PRODUCT_BRAND, "Samsung", 0.86)
        add(Field.PRODUCT_MODEL, "UN55TU8000", 0.88)
        add(Field.PRODUCT_SIZE_INCHES, 55, 0.90)
        add(
            Field.PRODUCT_WEIGHT_KG,
            scenario.weight_kg,
            scenario.weight_confidence,
            unit="kg",
        )

        if request.workflow is Workflow.INSTALL:
            add(Field.MOUNT_PRESENT, True, 0.95)
            add(Field.MOUNT_TYPE, "tilting", 0.84)
            add(Field.MOUNT_VESA, "400x400", 0.81)
            add(Field.MOUNT_MAX_LOAD_KG, scenario.mount_rating_kg, 0.87, unit="kg")
            add(Field.MOUNT_MISSING_PARTS, list(scenario.missing_parts), 0.78)

        add(Field.WALL_MATERIAL, scenario.material.value, scenario.material_confidence)
        add(Field.WALL_THICKNESS_MM, 12.5, 0.71, unit="mm")
        add(Field.WALL_OBSTACLES, list(scenario.obstacles), 0.74)
        add(Field.WALL_STUD_EVIDENCE, scenario.stud_evidence, 0.69)
        add(Field.SITE_ROOM_TYPE, "living_room", 0.82)
        add(Field.SITE_OUTLET_PRESENT, True, 0.77)
        add(Field.TOOLS_VISIBLE, ["drill", "spirit level"], 0.66)
        add(Field.DAMAGE_PRESENT, False, 0.80)

        add(Field.TASK_INVOLVES_ELECTRICAL, scenario.electrical, 0.90)
        add(Field.TASK_INVOLVES_GAS, scenario.gas, 0.95)
        add(Field.TASK_INVOLVES_PLUMBING, scenario.plumbing, 0.93)
        add(Field.TASK_INVOLVES_STRUCTURAL, False, 0.91)
        add(Field.TASK_IS_OVERHEAD, scenario.overhead, 0.89)
        add(Field.TASK_WORKING_HEIGHT_M, scenario.working_height_m, 0.85, unit="m")

        annotations = {
            primary_media: (
                Annotation(
                    kind="box",
                    label="Mounting area",
                    alt_text="A box around the area of wall where the mount goes",
                    points=(
                        BoundingBox(0.28, 0.22, 0.44, 0.34).center,
                        (0.72, 0.56),
                    ),
                ),
                Annotation(
                    kind="arrow",
                    label="Stud line",
                    alt_text="An arrow pointing to the likely stud position, left of centre",
                    points=((0.20, 0.80), (0.34, 0.42)),
                ),
            )
        }

        usage = Usage(
            input_tokens=800 + len(request.images) * 700,
            output_tokens=420,
            image_count=len(request.images),
            cost_usd=Decimal("0.0180") * len(request.images),
            latency_ms=rng.randint(600, 1400),
        )
        return VisionResult(findings=tuple(findings), annotations_by_media=annotations), usage

    # -------------------------------------------------------------- guide

    async def generate_guide(self, request: GuideRequest) -> tuple[GuideDraft, Usage]:
        if self._fail_contract:
            raise ProviderContractError(log_detail="fake adapter configured to fail")

        rng = self._rng(request.intent_text, request.safety_class.value)
        fixing = (
            request.fastener.display_name
            if request.fastener is not None
            else "the fixing specified in the product's own instructions"
        )
        pilot = (
            f"Drill a {request.fastener.pilot_diameter_mm:g} mm pilot hole. "
            if request.fastener is not None
            and request.fastener.requires_pilot_hole
            and request.fastener.pilot_diameter_mm is not None
            else ""
        )

        steps = (
            GuideStep(
                ordinal=1,
                title="Check what you have against the list",
                body=(
                    "Lay out every part and tool before you start. Missing hardware is the "
                    "most common reason a job stops halfway."
                ),
                verification="Every item on the tools and materials list is in front of you.",
                duration_minutes=5,
                tools=("tape measure",),
            ),
            GuideStep(
                ordinal=2,
                title="Find and mark the fixing positions",
                body=(
                    "Hold the bracket against the wall at the height you want and mark the "
                    "hole centres with a pencil. Check the marks are level before you drill "
                    "anything."
                ),
                verification="The marks are level and at the height you want.",
                duration_minutes=10,
                tools=("spirit level", "pencil"),
                safety_note="Scan for cables and pipes before you mark anything.",
                annotations=(
                    Annotation(
                        kind="dot",
                        label="Hole 1",
                        alt_text="A marker on the upper-left fixing position",
                        points=((0.34, 0.38),),
                    ),
                ),
            ),
            GuideStep(
                ordinal=3,
                title="Drill the holes",
                body=(
                    f"{pilot}Keep the drill square to the wall and let it cut at its own pace. "
                    "Stop as soon as you reach depth."
                ),
                verification="The holes are clean, square to the wall, and at the right depth.",
                duration_minutes=10,
                tools=("drill", "masonry or wood bit"),
                safety_note="Eye protection on. Clear the dust from each hole before fixing.",
            ),
            GuideStep(
                ordinal=4,
                title=f"Fit the fixings and the bracket using {fixing}",
                body=(
                    f"Fit {fixing} into each hole, then bolt the bracket on. Tighten evenly, "
                    "working diagonally across the holes rather than one side at a time."
                ),
                verification=(
                    "The bracket does not move when you pull firmly on it, and it is still level."
                ),
                duration_minutes=15,
                tools=("screwdriver or driver",),
                safety_note="Do not over-tighten — stripping the fixing loses most of its hold.",
            ),
            GuideStep(
                ordinal=5,
                title="Mount the product and check it",
                body=(
                    "With a second pair of hands, lift the product on and engage the "
                    "brackets fully. Check the catches or lock screws are home before you "
                    "let go."
                ),
                verification="It is level, sits flat, and the catches are locked.",
                duration_minutes=10,
                tools=(),
                safety_note="This is a two-person lift.",
            ),
        )

        draft = GuideDraft(
            title=f"{request.intent_text.strip()[:60] or 'Your project'}",
            summary=(
                "A step-by-step wall installation for your specific product and wall type, "
                "with the fixings checked against the load."
            ),
            difficulty=Difficulty.MODERATE,
            steps=steps,
            tools=(
                Tool(name="Drill", why="To make the fixing holes"),
                Tool(name="Spirit level", why="So it ends up straight"),
                Tool(name="Tape measure", why="To set the height"),
                Tool(
                    name="Cable and pipe detector",
                    ownership=ToolOwnership.REQUIRED,
                    why="To check what is behind the wall before drilling",
                ),
                Tool(
                    name="Stud finder",
                    ownership=ToolOwnership.OPTIONAL,
                    why="A stud fixing is stronger than any cavity anchor",
                ),
            ),
            materials=(
                Material(
                    product_class="wall_fastener",
                    display_name=fixing,
                    quantity=4,
                    attributes=(
                        {"key": request.fastener.key} if request.fastener is not None else {}
                    ),
                    citation=request.fastener.citation if request.fastener is not None else None,
                ),
            ),
            prerequisites=(
                "Check the product's own instructions for anything specific to your model.",
                "Have someone available to help with the lift.",
            ),
            common_mistakes=(
                "Fixing into the plasterboard alone when a stud was available.",
                "Over-tightening and stripping the anchor.",
                "Marking the holes without checking they are level.",
            ),
            final_verification=(
                "Pull firmly on the bracket — there should be no movement at all.",
                "Check it is level once more with the product mounted.",
                "Check the cables are not pinched or under tension.",
            ),
        )
        usage = Usage(
            input_tokens=1800,
            output_tokens=1500,
            cost_usd=Decimal("0.0900"),
            latency_ms=rng.randint(1500, 3000),
        )
        return draft, usage

    # -------------------------------------------------------------- validation

    async def validate_step(
        self, request: StepValidationRequest
    ) -> tuple[StepValidationResult, Usage]:
        rng = self._rng(request.step_title, str(request.evidence.media_id))
        roll = rng.random()

        if roll < 0.6:
            result = StepValidationResult(
                verdict=Verdict.OK,
                observations=(
                    "The bracket looks flush against the wall.",
                    "Both fixings appear fully seated.",
                ),
                issues=(),
                confidence=0.88,
            )
        elif roll < 0.85:
            # Deliberately common: an honest "I can't tell" beats a confident guess.
            result = StepValidationResult(
                verdict=Verdict.UNSURE,
                observations=("The fixing points are out of frame.",),
                issues=(),
                confidence=0.41,
            )
        else:
            result = StepValidationResult(
                verdict=Verdict.PROBLEM,
                observations=("The bracket appears to sit at a slight angle.",),
                issues=(
                    StepIssue(
                        severity="high",
                        description=(
                            "The bracket is not level, which puts uneven load on the fixings."
                        ),
                        fix="Loosen the fixings, level the bracket, and re-tighten evenly.",
                    ),
                ),
                confidence=0.79,
            )

        usage = Usage(
            input_tokens=900,
            output_tokens=200,
            image_count=1,
            cost_usd=Decimal("0.0150"),
            latency_ms=rng.randint(500, 1200),
        )
        return result, usage

    # -------------------------------------------------------------- chat

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        reply = (
            "From the photo that looks like the right fixing for this wall. Check it sits "
            "flush and does not spin when you tighten it — if it spins, the hole is oversized "
            "and you need the next size up."
        )
        for word in reply.split(" "):
            yield ChatChunk(kind=ChunkKind.DELTA, text=word + " ")
        if request.system_context:
            yield ChatChunk(kind=ChunkKind.REFERENCE, reference="guide_step:3")
        yield ChatChunk(kind=ChunkKind.DONE)

    # -------------------------------------------------------------- embeddings

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[tuple[float, ...], ...], Usage]:
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            rng = self._rng(text)
            raw = [rng.gauss(0, 1) for _ in range(EMBEDDING_DIMENSIONS)]
            norm = sum(value * value for value in raw) ** 0.5 or 1.0
            vectors.append(tuple(value / norm for value in raw))
        usage = Usage(
            input_tokens=sum(len(text) // 4 for text in texts),
            cost_usd=Decimal("0.0001") * len(texts),
        )
        return tuple(vectors), usage

    # -------------------------------------------------------------- helpers

    def _rng(self, *parts: str) -> random.Random:
        digest = hashlib.sha256("|".join((self._seed_salt, *parts)).encode()).hexdigest()
        return random.Random(int(digest[:16], 16))  # noqa: S311 - fixtures, not cryptography


class _Scenario:
    """Keyword-driven fixtures, so tests can name a situation in plain English."""

    def __init__(self, text: str) -> None:
        lowered = text.lower()
        self.category = "television" if "tv" in lowered else "wall_fixture"

        self.electrical = any(k in lowered for k in ("socket", "outlet", "wiring", "electrical"))
        self.gas = any(k in lowered for k in ("gas", "boiler", "hob"))
        self.plumbing = any(k in lowered for k in ("plumb", "water main", "radiator"))
        self.overhead = any(k in lowered for k in ("ceiling", "overhead", "projector"))

        if "heavy" in lowered or "75" in lowered:
            self.weight_kg: float | None = 32.0
        elif "light" in lowered or "shelf" in lowered:
            self.weight_kg = 4.0
        else:
            self.weight_kg = 15.4
        if "unknown weight" in lowered or "no label" in lowered:
            self.weight_kg = None
            self.weight_confidence = 0.0
        else:
            self.weight_confidence = 0.62 if "no label" in lowered else 0.86

        if "unknown wall" in lowered or "not sure what the wall" in lowered:
            self.material = WallMaterial.UNKNOWN
            self.material_confidence = 0.30
        elif "concrete" in lowered:
            self.material = WallMaterial.CONCRETE
            self.material_confidence = 0.92
        elif "brick" in lowered:
            self.material = WallMaterial.BRICK
            self.material_confidence = 0.90
        elif "tile" in lowered:
            self.material = WallMaterial.TILE_OVER_DRYWALL
            self.material_confidence = 0.87
        elif "stud" in lowered:
            self.material = WallMaterial.WOOD_STUD
            self.material_confidence = 0.88
        else:
            self.material = WallMaterial.DRYWALL
            self.material_confidence = 0.89

        self.mount_rating_kg = 5.0 if "underrated" in lowered else 45.0
        self.missing_parts: tuple[str, ...] = ("M8 bolts",) if "missing" in lowered else ()
        self.obstacles: tuple[str, ...] = (
            ("power cable above the socket",) if "socket" in lowered or "cable" in lowered else ()
        )
        self.stud_evidence = "regular dense points at 400 mm spacing"
        self.working_height_m = 3.2 if "high up" in lowered or "ladder" in lowered else 1.2

    @classmethod
    def from_text(cls, text: str) -> _Scenario:
        return cls(text)
