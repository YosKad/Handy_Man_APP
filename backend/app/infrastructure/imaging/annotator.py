"""Deterministic image annotation.

The vision model returns *coordinates and labels*; this module draws them onto
the user's own photo. That choice is deliberate and load-bearing
(``docs/06-ai-pipeline.md`` §8):

* ~100x cheaper than generative editing and much faster;
* reproducible — the same inputs always produce the same overlay;
* it physically cannot invent hardware that is not in the photograph.

It also emits alt text for every mark, so the overlay is available to a screen
reader rather than being visual-only.

Colours and geometry come from the design-system tokens in
``docs/05-design-system.md`` so annotated images look like part of the app.
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from app.core.errors import UnsupportedMediaType
from app.core.logging import get_logger
from app.features.guides.domain.entities import Annotation

logger = get_logger(__name__)

#: Design-system colours, resolved to RGBA. Annotations use the accent colour on
#: a dark translucent chip so they read against both bright and dark photos.
TOKEN_COLORS: dict[str, tuple[int, int, int, int]] = {
    "accent.primary": (61, 123, 255, 255),
    "safety.green": (34, 197, 94, 255),
    "safety.yellow": (245, 165, 36, 255),
    "safety.red": (239, 68, 68, 255),
    "text.onDark": (245, 245, 247, 255),
}
LABEL_BACKDROP = (11, 11, 15, 214)  # bg.canvas at 84%

#: Stroke and text scale with the image so a 4000px photo does not get hairlines.
#: Weighted for glanceability: the user reads this at arm's length, in a hallway,
#: holding a tool. Thin marks are useless here even if they look tidier.
_STROKE_RATIO = 0.006
_MIN_STROKE = 3
_FONT_RATIO = 0.030
_MIN_FONT = 14

_SUPPORTED_INPUT = frozenset({"JPEG", "PNG", "WEBP", "MPO"})


@dataclass(frozen=True, slots=True)
class AnnotatedImage:
    data: bytes
    content_type: str
    width: int
    height: int
    #: One sentence per mark, in drawing order. This is what a screen reader
    #: announces, and what a text-only fallback renders.
    alt_texts: tuple[str, ...]

    @property
    def alt_text(self) -> str:
        return " ".join(self.alt_texts)


class DeterministicAnnotator:
    """Draws annotation ops onto an image. Implements ``ImageAnnotatorPort``."""

    def __init__(self, *, output_format: str = "WEBP", quality: int = 85) -> None:
        self._format = output_format
        self._quality = quality

    async def annotate(self, image_bytes: bytes, ops: Sequence[Annotation]) -> AnnotatedImage:
        """Return the image with every op drawn on it.

        A single malformed op is skipped rather than failing the whole render:
        losing one arrow is recoverable, losing the guide's visuals is not.
        """
        base = self._open(image_bytes)
        canvas = base.convert("RGBA")
        # Marks are drawn on their own layer so translucent backdrops composite
        # cleanly instead of stacking onto each other.
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)

        width, height = canvas.size
        stroke = max(_MIN_STROKE, round(min(width, height) * _STROKE_RATIO))
        font = _load_font(max(_MIN_FONT, round(min(width, height) * _FONT_RATIO)))

        alt_texts: list[str] = []
        for op in ops:
            colour = TOKEN_COLORS.get(op.color_token, TOKEN_COLORS["accent.primary"])
            points = _to_pixels(op.points, width, height)
            if not points:
                logger.warning("skipping annotation with no usable points", extra={"kind": op.kind})
                continue
            try:
                self._draw_op(draw, op, points, colour, stroke, font)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "skipping malformed annotation",
                    extra={"kind": op.kind, "error": str(exc)},
                )
                continue
            alt_texts.append(op.alt_text or op.label)

        composed = Image.alpha_composite(canvas, layer)
        return AnnotatedImage(
            data=self._encode(composed),
            content_type=f"image/{self._format.lower()}",
            width=width,
            height=height,
            alt_texts=tuple(alt_texts),
        )

    # ---------------------------------------------------------------- drawing

    def _draw_op(
        self,
        draw: ImageDraw.ImageDraw,
        op: Annotation,
        points: list[tuple[int, int]],
        colour: tuple[int, int, int, int],
        stroke: int,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    ) -> None:
        kind = op.kind.lower()
        if kind == "box":
            self._draw_box(draw, points, colour, stroke)
            anchor = points[0]
        elif kind == "arrow":
            self._draw_arrow(draw, points, colour, stroke)
            # Anchor the label at the tail, never the tip: a chip drawn over the
            # arrowhead hides the exact thing the arrow is pointing at.
            anchor = points[0]
        elif kind == "dot":
            self._draw_dot(draw, points[0], colour, stroke)
            anchor = points[0]
        elif kind == "measure":
            self._draw_measure(draw, points, colour, stroke)
            anchor = points[0]
        elif kind == "label":
            anchor = points[0]
        else:
            raise ValueError(f"unknown annotation kind {op.kind!r}")

        if op.label:
            self._draw_label(draw, anchor, op.label, font, stroke)

    def _draw_box(
        self,
        draw: ImageDraw.ImageDraw,
        points: list[tuple[int, int]],
        colour: tuple[int, int, int, int],
        stroke: int,
    ) -> None:
        if len(points) < 2:
            # A single point describes a centre, not a box: draw a square around it.
            (cx, cy) = points[0]
            size = stroke * 12
            box = (cx - size, cy - size, cx + size, cy + size)
        else:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            box = (min(xs), min(ys), max(xs), max(ys))
        # A dark halo under the stroke keeps the mark readable over a light wall
        # and a dark cable alike.
        draw.rounded_rectangle(
            box, radius=stroke * 3, outline=LABEL_BACKDROP, width=stroke + max(2, stroke // 2)
        )
        draw.rounded_rectangle(box, radius=stroke * 3, outline=colour, width=stroke)

    def _draw_arrow(
        self,
        draw: ImageDraw.ImageDraw,
        points: list[tuple[int, int]],
        colour: tuple[int, int, int, int],
        stroke: int,
    ) -> None:
        if len(points) < 2:
            raise ValueError("an arrow needs a start and an end point")
        start, end = points[0], points[-1]
        draw.line([start, end], fill=LABEL_BACKDROP, width=stroke + max(2, stroke // 2))
        draw.line([start, end], fill=colour, width=stroke, joint="curve")

        # Arrowhead as a filled triangle, rotated to the line's bearing.
        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        head = stroke * 7
        spread = math.radians(24)
        draw.polygon(
            [
                end,
                (
                    round(end[0] - head * math.cos(angle - spread)),
                    round(end[1] - head * math.sin(angle - spread)),
                ),
                (
                    round(end[0] - head * math.cos(angle + spread)),
                    round(end[1] - head * math.sin(angle + spread)),
                ),
            ],
            fill=colour,
        )

    def _draw_dot(
        self,
        draw: ImageDraw.ImageDraw,
        point: tuple[int, int],
        colour: tuple[int, int, int, int],
        stroke: int,
    ) -> None:
        radius = stroke * 5
        cx, cy = point
        # Ring plus centre: reads as a drill mark rather than a blob, and stays
        # visible whatever is underneath it.
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius), outline=colour, width=stroke
        )
        inner = max(2, stroke)
        draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), fill=colour)

    def _draw_measure(
        self,
        draw: ImageDraw.ImageDraw,
        points: list[tuple[int, int]],
        colour: tuple[int, int, int, int],
        stroke: int,
    ) -> None:
        if len(points) < 2:
            raise ValueError("a measurement needs two end points")
        start, end = points[0], points[-1]
        draw.line([start, end], fill=colour, width=stroke)
        # End ticks perpendicular to the line.
        angle = math.atan2(end[1] - start[1], end[0] - start[0]) + math.pi / 2
        tick = stroke * 5
        for point in (start, end):
            draw.line(
                [
                    (
                        round(point[0] - tick * math.cos(angle)),
                        round(point[1] - tick * math.sin(angle)),
                    ),
                    (
                        round(point[0] + tick * math.cos(angle)),
                        round(point[1] + tick * math.sin(angle)),
                    ),
                ],
                fill=colour,
                width=stroke,
            )

    def _draw_label(
        self,
        draw: ImageDraw.ImageDraw,
        anchor: tuple[int, int],
        text: str,
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
        stroke: int,
    ) -> None:
        padding = max(4, stroke * 2)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_width, text_height = right - left, bottom - top

        x = anchor[0]
        y = anchor[1] - text_height - padding * 3
        # Keep the chip inside the frame: a label off the edge is a lost label.
        canvas_width, canvas_height = draw.im.size
        x = max(padding, min(x, canvas_width - text_width - padding * 3))
        y = max(padding, min(y, canvas_height - text_height - padding * 3))

        draw.rounded_rectangle(
            (x, y, x + text_width + padding * 2, y + text_height + padding * 2),
            radius=padding,
            fill=LABEL_BACKDROP,
        )
        draw.text(
            (x + padding, y + padding - top),
            text,
            font=font,
            fill=TOKEN_COLORS["text.onDark"],
        )

    # ---------------------------------------------------------------- io

    def _open(self, image_bytes: bytes) -> Image.Image:
        try:
            image = Image.open(io.BytesIO(image_bytes))
            image.load()
        except Exception as exc:
            raise UnsupportedMediaType(log_detail=f"cannot decode image: {exc}") from exc
        if image.format and image.format.upper() not in _SUPPORTED_INPUT:
            raise UnsupportedMediaType(log_detail=f"unsupported image format {image.format}")
        return image

    def _encode(self, image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        # WEBP/JPEG cannot carry alpha usefully here; flatten onto the canvas.
        if self._format in {"JPEG", "WEBP"}:
            flat = Image.new("RGB", image.size, (0, 0, 0))
            flat.paste(image, mask=image.split()[3])
            flat.save(buffer, format=self._format, quality=self._quality)
        else:
            image.save(buffer, format=self._format)
        return buffer.getvalue()


def _to_pixels(
    points: Sequence[tuple[float, float]], width: int, height: int
) -> list[tuple[int, int]]:
    """Convert normalised coordinates to pixels, clamped to the frame.

    Coordinates are normalised so they survive the resizing between capture,
    storage and annotation. Out-of-range values are clamped rather than dropped —
    a model that says 1.02 meant "the right edge".
    """
    pixels: list[tuple[int, int]] = []
    for point in points:
        # Typed as a 2-tuple, but provider payloads do not always honour that.
        if len(tuple(point)) != 2:
            continue
        x, y = point
        pixels.append(
            (
                round(min(max(float(x), 0.0), 1.0) * (width - 1)),
                round(min(max(float(y), 0.0), 1.0) * (height - 1)),
            )
        )
    return pixels


def _load_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Prefer a real font; fall back to Pillow's bitmap font.

    The fallback is legible but unscaled, so a missing font degrades the label
    size rather than failing the render.
    """
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()
