"""Value objects shared by more than one feature.

Pure Python: no framework, no I/O, no database. Everything here is immutable and
validated at construction, so an invalid value cannot exist deeper in the system.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Self


class Workflow(StrEnum):
    INSTALL = "install"
    REPAIR = "repair"
    ADVISE = "advise"


class SafetyClass(StrEnum):
    """Ordered by severity — see ``docs/07-safety-policy.md``."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"

    @property
    def severity(self) -> int:
        return _SEVERITY[self]

    def escalate_to(self, other: SafetyClass) -> SafetyClass:
        """Return the more severe of the two. Severity never decreases."""
        return other if other.severity > self.severity else self

    @classmethod
    def most_severe(cls, classes: Iterable[SafetyClass]) -> SafetyClass:
        result = cls.GREEN
        for candidate in classes:
            result = result.escalate_to(candidate)
        return result


_SEVERITY: dict[SafetyClass, int] = {
    SafetyClass.GREEN: 0,
    SafetyClass.YELLOW: 1,
    SafetyClass.RED: 2,
}


class Difficulty(StrEnum):
    EASY = "easy"
    MODERATE = "moderate"
    ADVANCED = "advanced"


class UnitSystem(StrEnum):
    METRIC = "metric"
    IMPERIAL = "imperial"


class LoadClass(StrEnum):
    """Coarse load bands used to select fasteners."""

    LIGHT = "light"  # <= 5 kg
    MEDIUM = "medium"  # <= 15 kg
    HEAVY = "heavy"  # <= 40 kg
    VERY_HEAVY = "very_heavy"  # > 40 kg

    @classmethod
    def for_kilograms(cls, kilograms: float) -> LoadClass:
        if kilograms <= 5:
            return cls.LIGHT
        if kilograms <= 15:
            return cls.MEDIUM
        if kilograms <= 40:
            return cls.HEAVY
        return cls.VERY_HEAVY


@dataclass(frozen=True, slots=True)
class Confidence:
    """A model's confidence in a single extracted fact, in [0, 1]."""

    value: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {self.value}")

    @classmethod
    def certain(cls) -> Self:
        """Facts supplied by the user, not inferred."""
        return cls(1.0)

    @classmethod
    def unknown(cls) -> Self:
        return cls(0.0)

    def meets(self, threshold: float) -> bool:
        return self.value >= threshold

    @property
    def percent(self) -> int:
        return round(self.value * 100)


@dataclass(frozen=True, slots=True)
class Measurement:
    """A physical quantity that keeps the unit it was measured in."""

    value: float
    unit: str

    def __post_init__(self) -> None:
        if self.unit not in _UNIT_TO_SI:
            raise ValueError(f"unsupported unit {self.unit!r}")

    def to_si(self) -> Measurement:
        factor, si_unit = _UNIT_TO_SI[self.unit]
        return Measurement(round(self.value * factor, 6), si_unit)

    def format_for(self, system: UnitSystem) -> str:
        si = self.to_si()
        if system is UnitSystem.METRIC:
            return f"{_trim(si.value)} {si.unit}"
        if si.unit == "mm":
            return f"{_trim(si.value / 25.4)} in"
        if si.unit == "kg":
            return f"{_trim(si.value * 2.20462)} lb"
        return f"{_trim(si.value)} {si.unit}"


# unit -> (factor to SI, SI unit)
_UNIT_TO_SI: dict[str, tuple[float, str]] = {
    "mm": (1.0, "mm"),
    "cm": (10.0, "mm"),
    "m": (1000.0, "mm"),
    "in": (25.4, "mm"),
    "ft": (304.8, "mm"),
    "kg": (1.0, "kg"),
    "g": (0.001, "kg"),
    "lb": (0.453592, "kg"),
    "nm": (1.0, "nm"),  # torque, newton-metres
}


def _trim(value: float) -> str:
    """Render a float without trailing zeros: 15.0 -> '15', 15.25 -> '15.25'."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Normalised box in [0, 1] image space, origin top-left.

    Normalised rather than pixel-based so it survives the resizing that happens
    between capture, storage and annotation.
    """

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        for name, value in (
            ("x", self.x),
            ("y", self.y),
            ("width", self.width),
            ("height", self.height),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be normalised to [0, 1], got {value}")
        if self.x + self.width > 1.0001 or self.y + self.height > 1.0001:
            raise ValueError("bounding box extends beyond the image")

    def to_pixels(self, image_width: int, image_height: int) -> tuple[int, int, int, int]:
        return (
            round(self.x * image_width),
            round(self.y * image_height),
            round((self.x + self.width) * image_width),
            round((self.y + self.height) * image_height),
        )

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)


@dataclass(frozen=True, slots=True)
class Cost:
    """Money as Decimal — never float. Used for the AI spend ledger."""

    amount_usd: Decimal

    def __post_init__(self) -> None:
        if self.amount_usd < 0:
            raise ValueError("cost cannot be negative")

    @classmethod
    def zero(cls) -> Self:
        return cls(Decimal("0"))

    def __add__(self, other: Cost) -> Cost:
        return Cost(self.amount_usd + other.amount_usd)

    def exceeds(self, ceiling_usd: float) -> bool:
        return self.amount_usd > Decimal(str(ceiling_usd))
