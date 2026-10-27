"""The motion a settle stilled: what the probe did to each running animation, and how it is told.

A rendering is measured once its layout holds still for a quiet window, and a window alone reads
PACED motion -- steps separated by pauses longer than it -- in its first pause, and ENDLESS
motion never at rest at all. Lengthening the window cures neither: an endless animation never
holds still, so a page carrying one would time out instead. So before every poll the probe's
`stillMotion` brings each animation still running on the document's timeline to rest, in the
document or in any OPEN shadow root (a closed one is out of the page's reach) -- one that ends
is finished, one that never ends is paused at an iteration start -- and reports what it did.
Seeking inside every poll catches an animation that starts mid-settle, and a seek that moves a
box changes the layout signature, so the quiet window restarts from there.

The late-script load settles again, in a load of its own, and its `stillMotion` stills only
motion PROVABLY layout-inert: a seek of it moves no layout, so nothing is charged to the scripts.
Any other animation is left running there, and counted so. It stills the same animations as the page
load did, so its counts are kept and said APART, never added to the page load's: a sum would
count each animation twice.

This module reads that report, sums it over a settle, and says it in the report and on the
console, one load at a time. Motion never changes the exit code: it is how a page was brought to
rest before it was measured, not something the page did wrong.

Entry point assumptions: pure functions over the probe's records and finished results; no
browser here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Protocol

from pass_checks import record_mapping, record_whole
from pass_markdown import code_span, rendering_label
from pass_targets import Rendering, name_renderings


@dataclass(frozen=True)
class Motion:
    """What a rendering's settle did to its animations, counted in animations.

    `finished` and `paused` count what was stilled, summed over every poll. `running` counts
    what was still running when the settle ended: an animation on another timeline, or at a
    zero rate, which does not advance with time -- and in the late-script load's settle, one
    not provably layout-inert, which it leaves to run.
    """

    finished: int
    paused: int
    running: int

    @property
    def has_motion(self) -> bool:
        """Whether anything was stilled, or left running."""
        return bool(self.finished or self.paused or self.running)


#: A settle that found nothing in motion, and a rendering never settled.
NO_MOTION = Motion(finished=0, paused=0, running=0)


class _Settled(Protocol):
    """What a page line reads of one load's settle: which rendering, and its motion."""

    @property
    def rendering(self) -> Rendering:
        """The rendering the result is of."""

    @property
    def motion(self) -> Motion:
        """What its settle stilled, or left running."""


class Moved(_Settled, Protocol):
    """What the report and the console read of a result: both loads' settles."""

    @property
    def late_motion(self) -> Motion:
        """What the late-script load's settle stilled, or left running."""


@dataclass(frozen=True)
class _LateSettle:
    """A result as its late-script load's settle left it, read like any settle."""

    rendering: Rendering
    motion: Motion


def _late(results: Sequence[Moved]) -> list[_LateSettle]:
    """Each result's late-script load, as a settle of its own."""
    return [_LateSettle(result.rendering, result.late_motion) for result in results]


def motion_from(raw: object) -> Motion:
    """Read one `stillMotion` record, refusing any count of the wrong type."""
    record = record_mapping(raw, "motion")
    return Motion(
        finished=record_whole(record, "finished"),
        paused=record_whole(record, "paused"),
        running=record_whole(record, "running"),
    )


def tally(total: Motion, poll: Motion) -> Motion:
    """`total` with one more poll's record added.

    What was stilled SUMS: each animation is stilled once, and is no longer running afterwards,
    so no poll counts it twice. What was left running is the LATEST poll's, since the animations
    left running are counted again on every poll, and what matters is what ran when the settle
    ended.
    """
    return Motion(
        finished=total.finished + poll.finished,
        paused=total.paused + poll.paused,
        running=poll.running,
    )


def motion_record(motion: Motion) -> Mapping[str, object]:
    """One rendering's motion for the JSON report."""
    return asdict(motion)


def _counts(motion: Motion) -> str:
    """`motion`'s three counts, as the report and the console say them."""
    return (
        f"{motion.finished} finished, {motion.paused} paused, "
        f"{motion.running} still running"
    )


def _by_page(results: Sequence[_Settled]) -> dict[str, list[_Settled]]:
    """Each page's results that stilled or left motion, pages in the order first met."""
    pages: dict[str, list[_Settled]] = {}
    for result in results:
        if result.motion.has_motion:
            pages.setdefault(result.rendering.page, []).append(result)
    return pages


def _summed(results: Sequence[_Settled]) -> Motion:
    """Every count in `results`, added up."""
    return Motion(
        finished=sum(r.motion.finished for r in results),
        paused=sum(r.motion.paused for r in results),
        running=sum(r.motion.running for r in results),
    )


#: What the section says each count means, above the pages it lists.
_SECTION_HEAD = (
    "## Motion\n\nBefore each poll of the page load's settle, every animation still running "
    "on the document's timeline, in the document or in any open shadow root, was brought to "
    "rest, so a page with paced or endless motion is measured at rest rather than in its first "
    "pause. **Finished**: an animation that ends, brought to its end. **Paused**: one that "
    "never ends, paused at the start of an iteration, so one frame of it -- its iteration "
    "start -- is what was measured. **Still running**: one on another timeline (a "
    "scroll-driven one) or at a zero rate, which does not advance with time, left as it stood. "
    "The counts are animations, summed over the renderings of each page that had any; a "
    "rendering refused before it was measured counts what its settle had stilled by then. They "
    "can differ between engines: an animation with no fill that ended before the first poll is "
    "already gone. Motion a script moves frame by frame, or that runs inside a closed shadow "
    "root, is not stilled: the quiet window is its only guard, and that window reads no box "
    "inside a shadow tree."
)


#: What the section says of the late-script load, above the pages its settle had motion in.
_LATE_HEAD = (
    "**The late-script load** settles again, in a load of its own, and before each poll "
    "stills only motion **provably layout-inert**: an animation naming at least one property, "
    "every one of them what `LAYOUT_INERT_PROPERTIES` in `src/page_probe_motion.js` names, which "
    "moves or fades a box without laying it out again -- a CSS animation's names read from the "
    "`@keyframes` rule Chromium's style resolver applies to it. A seek of one moves no layout, "
    "so the Layout Instability API reports nothing for it and the scripts are charged with "
    "nothing. Any other animation, or one whose rule is unknown, is left as it stood and counted "
    "**still running**, and a page whose layout never "
    "stops moving is refused as never settling. These counts are its own, never added to those "
    "above: both loads still the same animations."
)


def _page_line(page: str, results: Sequence[_Settled]) -> str:
    """One page's line: its renderings with motion, the sums, and where some still ran."""
    count = len(results)
    line = (
        f"- {code_span(page)}, {count} rendering{'s' if count != 1 else ''}: "
        f"{_counts(_summed(results))}"
    )
    running = [rendering_label(r.rendering) for r in results if r.motion.running]
    return (
        f"{line} when the settle ended, in {name_renderings(running)}"
        if running
        else line
    )


def _lines(pages: Mapping[str, Sequence[_Settled]]) -> str:
    """One line per page; empty when no page had motion."""
    return "\n".join(_page_line(page, settled) for page, settled in pages.items())


def motion_section(results: Sequence[Moved]) -> str:
    """The report's section on motion, by load and page; empty when neither load had any."""
    loaded, late = _by_page(results), _by_page(_late(results))
    if not (loaded or late):
        return ""
    late_part = f"{_LATE_HEAD}\n\n{_lines(late)}" if late else ""
    return "\n\n".join(
        part for part in (_SECTION_HEAD, _lines(loaded), late_part) if part
    )


def _named(pages: Mapping[str, Sequence[_Settled]]) -> str:
    """Each page with its sums, as the console names them."""
    return "; ".join(f"{page} ({_counts(_summed(s))})" for page, s in pages.items())


def motion_lines(results: Sequence[Moved]) -> list[str]:
    """The console's lines, one per load that stilled or left motion, naming each such page."""
    loads = (("", _by_page(results)), ("late-script load: ", _by_page(_late(results))))
    return [
        f"{load}motion stilled or left running while settling: {_named(pages)}"
        for load, pages in loads
        if pages
    ]
