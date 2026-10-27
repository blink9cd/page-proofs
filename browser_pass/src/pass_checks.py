"""The verdicts: the page's own state read back, what it showed, and whether each plant was caught.

NOTHING HERE TRUSTS A SETTING; browser_pass.md records the instrument faults that bought that
rule. `emulation_faults` compares what was ASKED for with what the page
itself REPORTS, and any disagreement stops that rendering from being measured at all.
`plant_verdicts` tells a detector that missed its fault apart from a fault that was never there.

The two polls take their clock and their pause as arguments, so they are tested without real
time; both wait on a signal with a deadline, never on a fixed sleep.

Entry point assumptions: pure functions over records the probe returned; no browser here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pass_targets import Rendering

#: The detector naming what a page's Content Security Policy refused (`pass_csp`).
CSP_VIOLATION = "csp_violation"

#: The eight detectors, in report order. The first four read the page's layout from inside it;
#: the next three are what the browser reported while the page loaded, and the last is what the
#: page's policy refused, as a listener in the page reported it.
DETECTORS = (
    "page_overflow",
    "past_right_edge",
    "hidden_rendered",
    "small_target",
    "console_error",
    "page_error",
    "http_error",
    CSP_VIOLATION,
)

#: Joins a planted element's description to the detector it was planted for. The in-page probe
#: is handed these values rather than keeping its own copies, so the two cannot drift.
PLANT_MARK = "@plant="

#: The text of the planted console error, and of the planted uncaught exception.
CONSOLE_PLANT_TEXT = "browser_pass planted console error"
PAGE_ERROR_PLANT_TEXT = "browser_pass planted page error"

#: A path no site serves, so requesting it is the planted HTTP error.
MISSING_PLANT_PATH = "/__browser_pass_planted_missing__.png"

#: The detectors whose plant is observed by a listener rather than read from the layout.
_LISTENER_DETECTORS = ("console_error", "page_error", "http_error")

#: The plants only a script in the page can report: a page error needs one to throw it, and a
#: CSP violation reaches the log only through the collector's listener, an init script.
_SCRIPTED_PLANTS = ("page_error", CSP_VIOLATION)


class ProbeError(RuntimeError):
    """The in-page probe returned a record of the wrong shape: a defect in the tool itself."""


@dataclass(frozen=True)
class PageState:
    """The state the page reports from inside itself, read before anything is measured."""

    inner_width: int
    inner_height: int
    client_width: int
    scripting_none: bool
    dark: bool
    landscape: bool
    reduced_motion: bool


@dataclass(frozen=True)
class Observation:
    """Everything one rendering showed the eight detectors."""

    overflow_px: int
    past_right_edge: tuple[str, ...]
    hidden_rendered: tuple[str, ...]
    small_targets: tuple[str, ...]
    console_errors: tuple[str, ...]
    page_errors: tuple[str, ...]
    http_errors: tuple[str, ...]
    csp_violations: tuple[str, ...]


def page_state_from(raw: object) -> PageState:
    """Read the probe's `state` record, refusing any field of the wrong type."""
    record = record_mapping(raw, "state")
    return PageState(
        inner_width=record_whole(record, "innerWidth"),
        inner_height=record_whole(record, "innerHeight"),
        client_width=record_whole(record, "clientWidth"),
        scripting_none=record_flag(record, "scriptingNone"),
        dark=record_flag(record, "dark"),
        landscape=record_flag(record, "landscape"),
        reduced_motion=record_flag(record, "reducedMotion"),
    )


def record_mapping(raw: object, what: str) -> Mapping[str, object]:
    """`raw` as a mapping, or a `ProbeError` naming the record.

    Public, with `record_whole`, `record_flag` and `record_texts`, because the late-script
    load's records and the settle's motion record go through the same refusals: one probe, one
    set of shapes it may not return.
    """
    if not isinstance(raw, Mapping):
        raise ProbeError(
            f"the {what} probe returned {type(raw).__name__}, not a record"
        )
    return raw


def record_whole(record: Mapping[str, object], field: str) -> int:
    """A whole-number field; a bool is refused although Python counts it an int."""
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProbeError(f"the probe's {field} is {value!r}, not a whole number")
    return value


def record_flag(record: Mapping[str, object], field: str) -> bool:
    """A true/false field."""
    value = record.get(field)
    if not isinstance(value, bool):
        raise ProbeError(f"the probe's {field} is {value!r}, not true or false")
    return value


def record_texts(record: Mapping[str, object], field: str) -> tuple[str, ...]:
    """A list-of-text field."""
    value = record.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProbeError(f"the probe's {field} is {value!r}, not a list of text")
    return tuple(value)


def emulation_faults(rendering: Rendering, state: PageState) -> list[str]:
    """Each way the page's own view of itself differs from the rendering asked for."""
    faults: list[str] = []
    if state.inner_width != rendering.width:
        faults.append(
            f"the viewport is {state.inner_width}px wide, asked for {rendering.width}px"
        )
    if state.inner_height != rendering.height:
        faults.append(
            f"the viewport is {state.inner_height}px tall, asked for {rendering.height}px"
        )
    if state.client_width != state.inner_width:
        faults.append(
            f"a classic scrollbar takes {state.inner_width - state.client_width}px of the "
            f"width (clientWidth {state.client_width} of {state.inner_width})"
        )
    faults.extend(_scripting_faults(rendering.scripts, state.scripting_none))
    shown_mode = "dark" if state.dark else "light"
    if shown_mode != rendering.mode:
        faults.append(
            f"the color scheme reads {shown_mode}, asked for {rendering.mode}"
        )
    shown_orientation = "landscape" if state.landscape else "portrait"
    if shown_orientation != rendering.orientation:
        faults.append(
            f"the orientation reads {shown_orientation} for a "
            f"{rendering.width}x{rendering.height} viewport"
        )
    if state.reduced_motion:
        faults.append("prefers-reduced-motion reads reduce, asked for no-preference")
    return faults


def _scripting_faults(scripts: str, scripting_none: bool) -> list[str]:
    """Scripting as the PAGE sees it -- the media query a stylesheet actually reads."""
    if scripts == "off" and not scripting_none:
        return [
            "(scripting: none) does not match with scripts off: scripting is still on"
        ]
    if scripts == "on" and scripting_none:
        return ["(scripting: none) matches with scripts on"]
    return []


def observation_from(
    layout: object,
    *,
    console_errors: Sequence[str],
    page_errors: Sequence[str],
    http_errors: Sequence[str],
    csp_violations: Sequence[str],
) -> Observation:
    """Join the layout probe's record with what the listeners and the CSP collector found."""
    record = record_mapping(layout, "layout")
    return Observation(
        overflow_px=record_whole(record, "overflow"),
        past_right_edge=record_texts(record, "pastRightEdge"),
        hidden_rendered=record_texts(record, "hiddenRendered"),
        small_targets=record_texts(record, "smallTargets"),
        console_errors=tuple(console_errors),
        page_errors=tuple(page_errors),
        http_errors=tuple(http_errors),
        csp_violations=tuple(csp_violations),
    )


def findings_of(observation: Observation) -> dict[str, tuple[str, ...]]:
    """What each detector found, keyed by detector; a detector that found nothing is absent."""
    overflow = (
        (f"the page is {observation.overflow_px}px wider than the viewport",)
        if observation.overflow_px > 0
        else ()
    )
    by_detector = dict(
        zip(DETECTORS, (overflow, *_element_and_listener_lists(observation)))
    )
    return {detector: items for detector, items in by_detector.items() if items}


def _element_and_listener_lists(
    observation: Observation,
) -> tuple[tuple[str, ...], ...]:
    """The seven list-valued detectors' items, in `DETECTORS` order after the overflow."""
    return (
        observation.past_right_edge,
        observation.hidden_rendered,
        observation.small_targets,
        observation.console_errors,
        observation.page_errors,
        observation.http_errors,
        observation.csp_violations,
    )


def merged_findings(
    *parts: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    """Each detector's findings from every part in turn; empty ones dropped.

    One rendering's two loads can both find something for one detector -- the page load and
    the late-script load both watch for CSP violations -- and neither may overwrite the other.
    A violation both loads report is one finding, as it is within one load; every other
    detector keeps its findings as it made them, repeats and all.
    """
    merged: dict[str, tuple[str, ...]] = {}
    for part in parts:
        for detector, items in part.items():
            merged[detector] = (*merged.get(detector, ()), *items)
    merged[CSP_VIOLATION] = tuple(dict.fromkeys(merged.get(CSP_VIOLATION, ())))
    return {detector: items for detector, items in merged.items() if items}


def csp_watched(rendering: Rendering) -> bool:
    """Whether a CSP violation in `rendering` can be reported at all.

    Only with scripts on: the collector's listener is an init script, and an engine runs none
    with the page's scripts off, so such a rendering is not measured for it, never clean.
    """
    return rendering.scripts == "on"


def plant_verdicts(
    before: Observation,
    after: Observation,
    took: Mapping[str, bool],
    *,
    scripts_on: bool,
    csp_caught: bool,
) -> dict[str, str]:
    """Per detector: `caught`, `missed`, `not planted` (an instrument fault), or `n/a`.

    A page error needs a script to throw it, and a CSP violation the collector's listener to
    report it; with scripts off there is neither, so those plants are `n/a` there rather than a
    pass nobody earned. The CSP plant is not the probe's: `pass_csp` makes it last and judges it
    on the collector's own log, which `csp_caught` carries.
    """
    verdicts: dict[str, str] = {}
    for detector in DETECTORS:
        if detector in _SCRIPTED_PLANTS and not scripts_on:
            verdicts[detector] = "n/a"
        elif detector == CSP_VIOLATION:
            verdicts[detector] = "caught" if csp_caught else "missed"
        elif not took.get(detector, False):
            verdicts[detector] = "not planted"
        elif _caught(detector, before, after):
            verdicts[detector] = "caught"
        else:
            verdicts[detector] = "missed"
    return verdicts


def _caught(detector: str, before: Observation, after: Observation) -> bool:
    """Whether the plant for `detector` shows up in what it reported after planting."""
    if detector == "page_overflow":
        # The plant sits beyond whatever the page already overflowed by, so it must GROW.
        return after.overflow_px > before.overflow_px
    lists = dict(zip(DETECTORS[1:], _element_and_listener_lists(after)))
    needle = {
        "console_error": CONSOLE_PLANT_TEXT,
        "page_error": PAGE_ERROR_PLANT_TEXT,
        "http_error": MISSING_PLANT_PATH,
    }.get(detector, f"{PLANT_MARK}{detector}")
    return any(needle in item for item in lists[detector])


def plant_took_from(raw: object) -> dict[str, bool]:
    """Which plants took, per detector.

    Anything but a literal `true` did not take, so a malformed record can only ever turn into
    `not planted` -- an instrument fault, never a pass.
    """
    record = record_mapping(raw, "plant")
    return {detector: record.get(detector) is True for detector in DETECTORS}


def display_url(url: str, base_url: str) -> str:
    """`url` as a report names it: a path on the measured origin, the whole url elsewhere."""
    if url.startswith(base_url + "/"):
        return url[len(base_url) :]
    return url


def listener_plants_arrived(after: Observation, *, scripts_on: bool) -> bool:
    """Whether every listener-observed plant this rendering can raise has been reported."""
    expected = [d for d in _LISTENER_DETECTORS if scripts_on or d != "page_error"]
    return all(_caught(detector, after, after) for detector in expected)


def poll_until(
    condition: Callable[[], bool],
    *,
    pause: Callable[[float], None],
    clock: Callable[[], float],
    within: float,
    interval: float,
) -> bool:
    """Check `condition` every `interval` seconds until it holds (True) or `within` runs out."""
    deadline = clock() + within
    while True:
        if condition():
            return True
        if clock() >= deadline:
            return False
        pause(interval)


def wait_until_settled(
    read: Callable[[], object],
    *,
    pause: Callable[[float], None],
    clock: Callable[[], float],
    quiet: float,
    within: float,
    interval: float,
) -> bool:
    """Whether `read()` holds one value for `quiet` seconds before `within` runs out.

    A page is measured only once its layout signature has stopped changing: at `load` a
    scripted page can still be building itself, and an animation can still be moving.
    """
    previous = read()
    changed_at = clock()
    deadline = changed_at + within
    while True:
        if clock() - changed_at >= quiet:
            return True
        if clock() >= deadline:
            return False
        pause(interval)
        current = read()
        if current != previous:
            previous, changed_at = current, clock()
