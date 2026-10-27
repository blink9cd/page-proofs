"""A per-element digest of one rendering, and the difference between two engines' digests.

The digest keys every element under `<body>` by its path (tag and position among same-tag
siblings, from `<body>` down), so the same element in two engines lines up whenever the two
built the same DOM. It records whether the element is displayed, its box in page coordinates,
and its computed font size.

Two noise classes are filtered before anything is reported, both measured in the Firefox pass
this tool replaces: a font size differing by under a tenth of a pixel (a user-agent font's
rounding, invisible), and any element NEITHER engine displays (its geometry is meaningless).

Horizontal and vertical differences are kept apart because they mean different things: that
pass's headline was that no width differed at any viewport, while a cumulative ~3px vertical
drift came from line boxes. `summarize` names the FIRST divergent element in page order, since
one early difference usually shifts everything after it, and apart from it the element carrying
the LARGEST difference, which is usually somewhere else entirely.

Entry point assumptions: pure functions over the probe's rows; no browser here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pass_checks import ProbeError

#: A box shift up to this many CSS pixels is sub-pixel rounding between engines, not layout.
BOX_TOLERANCE_PX = 0.5

#: A font-size difference below this many pixels is invisible rounding: a noise class.
FONT_TOLERANCE_PX = 0.1

#: The number of values in one probe row: path, displayed, x, y, width, height, font size.
_ROW_LENGTH = 7

#: Decimal places a reported delta is rounded to.
_DELTA_PLACES = 2


@dataclass(frozen=True)
class Box:
    """One element as one engine laid it out."""

    displayed: bool
    x: float
    y: float
    width: float
    height: float
    font_size: float


@dataclass(frozen=True)
class Difference:
    """One element differing between two engines on one axis, by `delta` pixels."""

    path: str
    axis: str
    delta: float


@dataclass(frozen=True)
class AxisSummary:
    """All the differences on one axis: how many, where the first is, and where the largest is."""

    count: int
    first_path: str
    first_delta: float
    max_path: str
    max_delta: float


# fuzz-target
def parse_digest(raw: object) -> dict[str, Box]:
    """Read the probe's digest rows into boxes keyed by element path, in page order."""
    if not isinstance(raw, list):
        raise ProbeError(f"the digest probe returned {type(raw).__name__}, not rows")
    return dict(_parse_row(row) for row in raw)


def _parse_row(row: object) -> tuple[str, Box]:
    """One `[path, displayed, x, y, width, height, font_size]` row."""
    if not isinstance(row, list) or len(row) != _ROW_LENGTH:
        raise ProbeError(f"a digest row is {row!r}, not {_ROW_LENGTH} values")
    path, displayed, *numbers = row
    if not isinstance(path, str):
        raise ProbeError(f"a digest row's path is {path!r}, not text")
    if displayed not in (0, 1) or not isinstance(displayed, int):
        raise ProbeError(f"a digest row's displayed flag is {displayed!r}")
    if not all(_is_number(value) for value in numbers):
        raise ProbeError(f"a digest row holds a value that is not a number: {row!r}")
    x, y, width, height, font_size = (float(value) for value in numbers)
    return path, Box(bool(displayed), x, y, width, height, font_size)


def _is_number(value: object) -> bool:
    """A JSON number: an int or a float, but not a bool."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def compare_digests(
    reference: Mapping[str, Box], other: Mapping[str, Box]
) -> list[Difference]:
    """Every difference between two digests, in the reference's page order, noise removed."""
    paths = list(reference) + [path for path in other if path not in reference]
    differences: list[Difference] = []
    for path in paths:
        differences.extend(
            _element_differences(path, reference.get(path), other.get(path))
        )
    return differences


def _element_differences(
    path: str, first: Box | None, second: Box | None
) -> list[Difference]:
    """How one element differs between two engines; empty when it does not, or only by noise."""
    if first is None or second is None:
        present = first or second
        # An element one engine never built matters only if the other engine shows it.
        return (
            [Difference(path, "presence", 0.0)] if present and present.displayed else []
        )
    if not first.displayed and not second.displayed:
        return []
    if first.displayed != second.displayed:
        return [Difference(path, "display", 0.0)]
    found: list[Difference] = []
    horizontal = max(abs(second.x - first.x), abs(second.width - first.width))
    vertical = max(abs(second.y - first.y), abs(second.height - first.height))
    font = round(abs(second.font_size - first.font_size), 3)
    if horizontal > BOX_TOLERANCE_PX:
        found.append(Difference(path, "horizontal", round(horizontal, _DELTA_PLACES)))
    if vertical > BOX_TOLERANCE_PX:
        found.append(Difference(path, "vertical", round(vertical, _DELTA_PLACES)))
    if font >= FONT_TOLERANCE_PX:
        found.append(Difference(path, "font", round(font, _DELTA_PLACES)))
    return found


def summarize(differences: Sequence[Difference]) -> dict[str, AxisSummary]:
    """One summary per axis that differs, in the order the axes first appear."""
    by_axis: dict[str, list[Difference]] = {}
    for difference in differences:
        by_axis.setdefault(difference.axis, []).append(difference)
    summaries: dict[str, AxisSummary] = {}
    for axis, items in by_axis.items():
        # max() keeps the first of equal items, so a tie names the earliest in page order.
        largest = max(items, key=lambda item: item.delta)
        summaries[axis] = AxisSummary(
            count=len(items),
            first_path=items[0].path,
            first_delta=items[0].delta,
            max_path=largest.path,
            max_delta=largest.delta,
        )
    return summaries
