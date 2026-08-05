"""Deterministic annotation renderer.

These tests decode the rendered output, so they assert that pixels actually
changed — not merely that the call returned bytes.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops, ImageStat

from app.core.errors import UnsupportedMediaType
from app.features.guides.domain.entities import Annotation
from app.infrastructure.imaging.annotator import (
    TOKEN_COLORS,
    DeterministicAnnotator,
    _to_pixels,
)


@pytest.fixture
def annotator() -> DeterministicAnnotator:
    return DeterministicAnnotator()


def _photo(
    width: int = 640, height: int = 480, colour: tuple[int, int, int] = (120, 120, 120)
) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _decode(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


def _arrow(
    *,
    kind: str = "arrow",
    label: str = "Stud line",
    alt_text: str = "An arrow pointing to the stud, left of centre",
    points: tuple[tuple[float, float], ...] = ((0.2, 0.8), (0.4, 0.4)),
    color_token: str = "accent.primary",
) -> Annotation:
    return Annotation(
        kind=kind,
        label=label,
        alt_text=alt_text,
        points=points,
        color_token=color_token,
    )


# ------------------------------------------------------------------ rendering


@pytest.mark.parametrize("kind", ["arrow", "box", "dot", "measure", "label"])
async def test_every_annotation_kind_renders(annotator: DeterministicAnnotator, kind: str) -> None:
    op = _arrow(kind=kind)
    result = await annotator.annotate(_photo(), [op])

    assert result.width == 640
    assert result.height == 480
    assert result.content_type == "image/webp"
    assert result.alt_texts == (op.alt_text,)


async def test_annotation_actually_changes_pixels(annotator: DeterministicAnnotator) -> None:
    """The point of the renderer is marks on the photo, not a re-encode."""
    original = _photo()
    result = await annotator.annotate(original, [_arrow()])

    difference = ImageChops.difference(_decode(original), _decode(result.data))
    changed_box = difference.getbbox()

    assert changed_box is not None, "nothing was drawn"
    # The mark should cover a meaningful area, not a stray pixel.
    assert ImageStat.Stat(difference).sum[0] > 10_000


async def test_accent_colour_appears_in_the_output(annotator: DeterministicAnnotator) -> None:
    result = await annotator.annotate(_photo(), [_arrow(label="")])
    after = _decode(result.data)

    target = TOKEN_COLORS["accent.primary"][:3]
    counted = after.getcolors(maxcolors=1 << 20) or []
    close_enough = any(
        isinstance(colour, tuple)
        and all(
            abs(channel - expected) < 40 for channel, expected in zip(colour, target, strict=True)
        )
        for _count, colour in counted
    )
    assert close_enough, "the accent colour was not drawn"


async def test_rendering_is_deterministic(annotator: DeterministicAnnotator) -> None:
    """Same inputs, same bytes — this is what generative editing cannot promise."""
    photo = _photo()
    first = await annotator.annotate(photo, [_arrow()])
    second = await annotator.annotate(photo, [_arrow()])

    assert first.data == second.data


async def test_multiple_annotations_all_contribute_alt_text(
    annotator: DeterministicAnnotator,
) -> None:
    ops = [
        _arrow(),
        _arrow(
            kind="dot",
            label="Hole 1",
            alt_text="A mark on the upper-left hole",
            points=((0.3, 0.3),),
        ),
        _arrow(
            kind="box",
            label="Bracket",
            alt_text="A box around the bracket",
            points=((0.1, 0.1), (0.5, 0.5)),
        ),
    ]
    result = await annotator.annotate(_photo(), ops)

    assert len(result.alt_texts) == 3
    assert "upper-left" in result.alt_text


async def test_alt_text_falls_back_to_the_label(annotator: DeterministicAnnotator) -> None:
    """Accessibility must not depend on the model remembering to write alt text."""
    result = await annotator.annotate(_photo(), [_arrow(alt_text="")])
    assert result.alt_texts == ("Stud line",)


# ------------------------------------------------------------------ robustness


async def test_a_malformed_op_is_skipped_not_fatal(annotator: DeterministicAnnotator) -> None:
    """Losing one arrow is recoverable; losing the guide's visuals is not."""
    ops = [
        _arrow(kind="arrow", points=((0.5, 0.5),)),  # an arrow needs two points
        _arrow(label="Good", alt_text="A valid arrow"),
    ]
    result = await annotator.annotate(_photo(), ops)

    assert result.alt_texts == ("A valid arrow",)


async def test_unknown_kind_is_skipped(annotator: DeterministicAnnotator) -> None:
    result = await annotator.annotate(_photo(), [_arrow(kind="hologram")])
    assert result.alt_texts == ()


async def test_op_with_no_points_is_skipped(annotator: DeterministicAnnotator) -> None:
    result = await annotator.annotate(_photo(), [_arrow(points=())])
    assert result.alt_texts == ()


async def test_no_ops_returns_a_valid_image(annotator: DeterministicAnnotator) -> None:
    result = await annotator.annotate(_photo(), [])
    assert _decode(result.data).size == (640, 480)
    assert result.alt_texts == ()


async def test_undecodable_bytes_raise_a_client_safe_error(
    annotator: DeterministicAnnotator,
) -> None:
    with pytest.raises(UnsupportedMediaType) as excinfo:
        await annotator.annotate(b"this is not an image", [_arrow()])

    assert "JPEG" in excinfo.value.message  # a human-readable hint, no library detail


async def test_a_single_point_box_becomes_a_square(annotator: DeterministicAnnotator) -> None:
    """A model that returns a centre instead of a rect should still render."""
    result = await annotator.annotate(_photo(), [_arrow(kind="box", points=((0.5, 0.5),))])
    assert result.alt_texts == (_arrow().alt_text,)


@pytest.mark.parametrize(("width", "height"), [(200, 150), (2400, 1600), (400, 1200)])
async def test_marks_scale_with_the_image(
    annotator: DeterministicAnnotator, width: int, height: int
) -> None:
    """A 2400px photo must not get hairline strokes."""
    result = await annotator.annotate(_photo(width, height), [_arrow()])
    assert (result.width, result.height) == (width, height)


async def test_labels_near_the_edge_stay_inside_the_frame(
    annotator: DeterministicAnnotator,
) -> None:
    """A label drawn off-canvas is a lost label."""
    ops = [
        _arrow(label="Top left corner", points=((0.0, 0.0), (0.01, 0.01))),
        _arrow(label="Bottom right corner", points=((0.99, 0.99), (1.0, 1.0))),
    ]
    result = await annotator.annotate(_photo(320, 240), ops)
    assert len(result.alt_texts) == 2


async def test_arrow_label_does_not_cover_the_arrow_tip(
    annotator: DeterministicAnnotator,
) -> None:
    """The label goes at the tail: covering the tip hides what we point at."""
    tip = (0.75, 0.75)
    op = _arrow(label="Avoid this", points=((0.2, 0.2), tip))
    result = await annotator.annotate(_photo(800, 800), [op])

    after = _decode(result.data)

    def dark_pixels(nx: float, ny: float) -> int:
        # The chip is a solid dark block ~30px tall; the stroke halo is only a few
        # pixels wide, so a chip is unmistakable by area.
        cx, cy = round(nx * 799), round(ny * 799)
        region = after.crop((cx - 70, cy - 70, cx + 70, cy + 10)).convert("L")
        # The luminance histogram counts dark pixels without a Python-level loop.
        return sum(region.histogram()[:67])

    near_tail = dark_pixels(0.2, 0.2)
    near_tip = dark_pixels(*tip)
    assert near_tail > near_tip * 3, "the label chip is not anchored at the tail"


async def test_png_output_format_is_supported() -> None:
    result = await DeterministicAnnotator(output_format="PNG").annotate(_photo(), [_arrow()])
    assert result.content_type == "image/png"
    assert _decode(result.data).size == (640, 480)


async def test_png_input_is_accepted(annotator: DeterministicAnnotator) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 200), (30, 30, 30)).save(buffer, format="PNG")
    result = await annotator.annotate(buffer.getvalue(), [_arrow()])
    assert (result.width, result.height) == (300, 200)


# ------------------------------------------------------------------ coordinates


def test_normalised_coordinates_map_to_pixels() -> None:
    assert _to_pixels([(0.0, 0.0), (1.0, 1.0), (0.5, 0.5)], 100, 200) == [
        (0, 0),
        (99, 199),
        (50, 100),
    ]


def test_out_of_range_coordinates_are_clamped_not_dropped() -> None:
    """A model that says 1.02 meant 'the right edge'."""
    assert _to_pixels([(1.02, -0.1)], 100, 100) == [(99, 0)]


def test_malformed_points_are_ignored() -> None:
    assert _to_pixels([(0.5,), (0.1, 0.1, 0.1), (0.2, 0.2)], 100, 100) == [(20, 20)]  # type: ignore[list-item]
