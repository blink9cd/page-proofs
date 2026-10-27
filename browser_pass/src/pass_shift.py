"""The late-script layout-shift detector's verdicts: what the page recorded, turned into a score.

A page that paints first and upgrades itself afterward moves under its reader, and whether that
shows depends on a race: on a fast connection the scripts usually win it, so an ordinary load
reads zero on the very page that jumps on a slow one. The detector's own load (`pass_late_load`)
removes the race by HOLDING every script until the page has painted in its own fonts, then
releasing them. What moves after the release is what a late script moves; what moved before it
-- a web font swapping in, say -- happens whatever the scripts do, and is not counted.

Shifts after the release are grouped into session windows the way Cumulative Layout Shift groups
them, and the score is the largest window. Each window above `THRESHOLD` is a finding.

Only Chromium implements the Layout Instability API. Everywhere else the detector reports NOT
MEASURED with its reason, never a clean zero, and with scripts off there is no script to be late,
so it is not applicable.

Entry point assumptions: pure functions over records the in-page probe returned; no browser here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pass_checks import (
    PLANT_MARK,
    ProbeError,
    record_flag,
    record_mapping,
    record_texts,
)
from pass_targets import Rendering

#: The detector's name, in findings, verdicts and the report.
LATE_SHIFT = "late_shift"

#: The engines that report layout shifts (the Layout Instability API).
SHIFT_ENGINES = ("chromium",)

#: A session window scoring above this is a finding. Chosen against this detector's own
#: numbers on the site it was built for: the late upgrade it exists to catch scores
#: 0.017-0.259 on an anchored arrival, and 0.897 on a populated archive month, while the movements
#: nobody would chase -- a band barely in view, a footer control appearing -- score 0.0012-0.0097.
#: REJECTED, with the reason each was refused: 0.005, which flags that footer control; 0.025, at
#: which an anchored arrival scoring 0.0174 reads green; and 0.1, the "good" line the Core Web
#: Vitals draw for a whole page's Cumulative Layout Shift, which would pass the very fault this
#: detector was built for (0.0962).
THRESHOLD = 0.01

#: Cumulative Layout Shift's session windows: a gap of a second or more between shifts starts a
#: new window, and no window runs longer than five seconds from its first shift.
WINDOW_GAP_MS = 1000.0
WINDOW_SPAN_MS = 5000.0

#: What a rendering's late-shift record can say about itself.
MEASURED = "measured"
NOT_MEASURED = "not measured"
NOT_APPLICABLE = "n/a"

#: How many moved elements a finding names before summarizing the rest.
_NAMED_SOURCES = 5

#: Marks the planted shift's element in a source description.
_PLANT_NEEDLE = f"{PLANT_MARK}{LATE_SHIFT}"


@dataclass(frozen=True)
class Shift:
    """One layout shift after the release: when it started, its score, and what moved."""

    start_ms: float
    score: float
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Window:
    """Shifts close enough together to count as one: their summed score and everything moved."""

    start_ms: float
    score: float
    sources: tuple[str, ...]


@dataclass(frozen=True)
class LateShift:
    """One rendering's late-script shifts, or the reason it has none to report."""

    status: str
    reason: str = ""
    shifts: tuple[Shift, ...] = ()

    @property
    def windows(self) -> tuple[Window, ...]:
        """The shifts grouped into session windows, earliest first."""
        return session_windows(self.shifts)

    @property
    def score(self) -> float:
        """The largest window's score; 0.0 when nothing moved."""
        return max((window.score for window in self.windows), default=0.0)


@dataclass(frozen=True)
class HoldState:
    """How far the page got with its scripts held: painted, fonts in, or blocked on a script."""

    painted: bool
    fonts_loaded: bool
    blocked: bool

    @property
    def ended(self) -> bool:
        """Whether the held scripts can be let through.

        Either the page is on screen in its own fonts, or nothing will be until its scripts run.
        Waiting for the fonts keeps a web font from swapping in after the release, where its
        shift would be counted as the scripts' doing.
        """
        return (self.painted and self.fonts_loaded) or self.blocked


@dataclass(frozen=True)
class ShiftRecord:
    """What the late-script load recorded, read back once the page has settled."""

    supported: bool
    painted_first: bool
    blocking_held: bool
    presentable_at: float | None
    shifts: tuple[Shift, ...]


def not_measured(rendering: Rendering) -> LateShift | None:
    """Why `rendering` gets no late-script load, or None when it does."""
    if rendering.state is not None:
        return LateShift(
            NOT_APPLICABLE,
            reason="a state is reached after the page load the late-script load measures",
        )
    if rendering.scripts == "off":
        return LateShift(NOT_APPLICABLE, reason="no script runs with scripts off")
    if rendering.engine not in SHIFT_ENGINES:
        return LateShift(
            NOT_MEASURED, reason=f"{rendering.engine} does not report layout shifts"
        )
    return None


def hold_state_from(raw: object) -> HoldState:
    """Read the probe's `holdState` record."""
    record = record_mapping(raw, "hold state")
    return HoldState(
        painted=record_flag(record, "painted"),
        fonts_loaded=record_flag(record, "fontsLoaded"),
        blocked=record_flag(record, "blocked"),
    )


def shift_record_from(raw: object) -> ShiftRecord:
    """Read the probe's `lateShifts` record, keeping only the shifts the scripts can own.

    Counting starts at the LATER of two moments: the release, and the first instant the page was
    PRESENTABLE -- painted, in its own fonts. Usually they are the same instant, because that is
    what the hold waits for. They come apart on the one path that releases early: a held script
    the page cannot paint without is let through at once, since no paint will come until it runs.
    There the release precedes the first paint, and counting from it would charge the scripts
    with a web font swapping in afterwards -- the very thing waiting for the fonts prevents.
    Whichever moment is later is the one after which a shift is something a reader could see
    move, so `max` is the whole rule.

    A shift that follows input is the page answering its reader, not moving under them, and
    Cumulative Layout Shift leaves it out too.
    """
    record = record_mapping(raw, "late shifts")
    released = _number(record.get("released"), "released")
    paints = _numbers(record.get("paints"), "paints")
    presentable_at = _optional_number(record.get("presentableAt"), "presentableAt")
    counted_from = max(
        released, presentable_at if presentable_at is not None else released
    )
    entries = [_entry_from(entry) for entry in _list(record.get("entries"), "entries")]
    return ShiftRecord(
        supported=record_flag(record, "supported"),
        painted_first=any(paint <= released for paint in paints),
        blocking_held=record_flag(record, "blockingHeld"),
        presentable_at=presentable_at,
        shifts=tuple(
            shift
            for shift, recent_input in entries
            if shift.start_ms >= counted_from and not recent_input
        ),
    )


def _entry_from(raw: object) -> tuple[Shift, bool]:
    """One layout-shift entry, and whether it followed input."""
    entry = record_mapping(raw, "layout-shift entry")
    shift = Shift(
        start_ms=_number(entry.get("start"), "start"),
        score=_number(entry.get("value"), "value"),
        sources=record_texts(entry, "sources"),
    )
    return shift, record_flag(entry, "recentInput")


def _list(value: object, field: str) -> list[object]:
    """A list field."""
    if not isinstance(value, list):
        raise ProbeError(f"the probe's {field} is {value!r}, not a list")
    return value


def _optional_number(value: object, field: str) -> float | None:
    """A number field a page may legitimately never have reached, reported as null."""
    return None if value is None else _number(value, field)


def _number(value: object, field: str) -> float:
    """A number field; a bool is refused although Python counts it one."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProbeError(f"the probe's {field} is {value!r}, not a number")
    return float(value)


def _numbers(value: object, field: str) -> list[float]:
    """A list-of-numbers field."""
    return [_number(item, field) for item in _list(value, field)]


def record_faults(record: ShiftRecord) -> list[str]:
    """Why a late-shift record cannot be trusted; empty when it can.

    A page that painted before its scripts ran is measured. So is one that could not paint
    until they ran, because a held script blocks its rendering: nothing was on screen to move.
    Anything else -- no paint, nothing blocking it -- cannot tell a late shift from none.
    """
    faults = _unmeasurable(record)
    if faults:
        return faults
    if record.presentable_at is None:
        return [
            "late_shift: the page never painted in its own fonts, so a face swapping in "
            "cannot be told from a late script"
        ]
    return []


def awaits_presentable(record: ShiftRecord) -> bool:
    """Whether `record` lacks only the moment its page became presentable.

    That moment -- painted, then in its own fonts -- is marked by the page's frames, which a
    loaded machine can leave well behind its main thread, so frames still to come can supply
    it and the record is not yet a verdict. A record refused for anything else is refused
    whatever its frames bring, and waits on nothing.
    """
    return record.presentable_at is None and not _unmeasurable(record)


def _unmeasurable(record: ShiftRecord) -> list[str]:
    """Why `record` cannot be measured whatever its frames bring; empty when it can."""
    if not record.supported:
        return ["late_shift: this engine build does not report layout shifts"]
    if not record.painted_first and not record.blocking_held:
        return [
            "late_shift: the page did not paint while its scripts were held, and no held "
            "script blocks its rendering, so a late shift cannot be told from none"
        ]
    return []


def session_windows(shifts: Sequence[Shift]) -> tuple[Window, ...]:
    """Group `shifts` into Cumulative Layout Shift's session windows, earliest first."""
    windows: list[Window] = []
    last_ms = 0.0
    for shift in sorted(shifts, key=lambda shift: shift.start_ms):
        current = windows[-1] if windows else None
        if (
            current is None
            or shift.start_ms - last_ms >= WINDOW_GAP_MS
            or shift.start_ms - current.start_ms >= WINDOW_SPAN_MS
        ):
            windows.append(Window(shift.start_ms, shift.score, _unique(shift.sources)))
        else:
            windows[-1] = Window(
                current.start_ms,
                current.score + shift.score,
                _unique(current.sources + shift.sources),
            )
        last_ms = shift.start_ms
    return tuple(windows)


def _unique(names: Sequence[str]) -> tuple[str, ...]:
    """`names` with repeats dropped, first appearance kept."""
    return tuple(dict.fromkeys(names))


def late_findings(late: LateShift, *, threshold: float = THRESHOLD) -> tuple[str, ...]:
    """One finding per window scoring above `threshold`: its score and what moved."""
    return tuple(
        f"{window.score:.4f} from {_named(window.sources)}"
        for window in late.windows
        if window.score > threshold
    )


def _named(sources: Sequence[str]) -> str:
    """The moved elements, a handful by name and the rest counted."""
    named = ", ".join(sources[:_NAMED_SOURCES])
    rest = len(sources) - _NAMED_SOURCES
    return f"{named} and {rest} more" if rest > 0 else named


def names_plant(late: LateShift) -> bool:
    """Whether any shift in `late` moved the planted box, whatever it scored."""
    return any(_names_plant(shift.sources) for shift in late.shifts)


def _names_plant(sources: Sequence[str]) -> bool:
    """Whether one of `sources` is the planted box."""
    return any(_PLANT_NEEDLE in source for source in sources)


def plant_verdict(
    *, took: bool, moved: bool, after: LateShift, threshold: float = THRESHOLD
) -> str:
    """The planted shift's verdict: `caught`, `missed`, or `not planted` (an instrument fault)."""
    if not (took and moved):
        return "not planted"
    caught = any(
        window.score > threshold and _names_plant(window.sources)
        for window in after.windows
    )
    return "caught" if caught else "missed"


def late_record(late: LateShift) -> Mapping[str, object]:
    """One rendering's late-shift record for the JSON report."""
    return {
        "status": late.status,
        "reason": late.reason,
        "score": late.score,
        "windows": [
            {
                "start_ms": window.start_ms,
                "score": window.score,
                "sources": list(window.sources),
            }
            for window in late.windows
        ],
    }
