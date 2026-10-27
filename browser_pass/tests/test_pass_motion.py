"""Grouped suite: the motion a settle stills -- its record, its sum, and how a run reports it.

Also the settle's wiring in `pass_engine.measure`, through a page that answers the probe with
scripted records: that the read stills before it signs, and that the rendering keeps the motion
tallied over every poll, a settle that runs out of time included. Whether an engine stills what
the probe asks is the live suite's question (`test_pass_live.py`).
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_engine  # noqa: E402 - the tool's private src/, via the insert above
import pass_motion  # noqa: E402 - the tool's private src/, via the insert above
from pass_checks import ProbeError  # noqa: E402 - the tool's private src/
from pass_motion import NO_MOTION, Motion  # noqa: E402 - the tool's private src/
from pass_report import RenderingResult  # noqa: E402 - the tool's private src/
from pass_targets import Rendering  # noqa: E402 - the tool's private src/

_HOME = Rendering("chromium", "/", 390, 844, "light", "on")


@dataclass(frozen=True)
class _Result:
    """A rendering and the motion each load's settle stilled: all a motion report reads."""

    rendering: Rendering
    motion: Motion
    late_motion: Motion = NO_MOTION


#: The late-script load's part of the section, which a run whose late loads stilled nothing omits.
_LATE_PART = "**The late-script load**"


class MotionFromTests(unittest.TestCase):
    """Reading the probe's `stillMotion` record, refusing any field of the wrong type."""

    def test_reads_the_probe_record(self) -> None:
        raw = {"finished": 2, "paused": 1, "running": 0}
        self.assertEqual(pass_motion.motion_from(raw), Motion(2, 1, 0))

    def test_refuses_a_record_that_is_not_one(self) -> None:
        with self.assertRaisesRegex(ProbeError, "motion"):
            pass_motion.motion_from([2, 1, 0])

    def test_refuses_a_count_missing_or_of_the_wrong_type_never_coercing_it(
        self,
    ) -> None:
        for bad in ({"finished": 2}, {"finished": 2, "paused": 1.5}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ProbeError, "paused"):
                pass_motion.motion_from({"running": 0, **bad})
        for bad_value in (True, "3"):
            with self.subTest(running=bad_value):
                with self.assertRaisesRegex(ProbeError, "running"):
                    pass_motion.motion_from(
                        {"finished": 0, "paused": 0, "running": bad_value}
                    )


class TallyTests(unittest.TestCase):
    """Stilled animations add up across polls; what still runs is the latest poll's."""

    def test_stilled_counts_sum_and_running_is_the_latest_poll(self) -> None:
        total = NO_MOTION
        for poll in (Motion(3, 1, 2), Motion(1, 0, 2), Motion(0, 2, 1)):
            total = pass_motion.tally(total, poll)
        self.assertEqual(total, Motion(finished=4, paused=3, running=1))

    def test_a_settle_with_nothing_in_motion_tallies_to_none(self) -> None:
        self.assertEqual(pass_motion.tally(NO_MOTION, NO_MOTION), NO_MOTION)
        self.assertFalse(NO_MOTION.has_motion)
        self.assertTrue(Motion(0, 0, 1).has_motion)

    def test_the_json_record_names_each_count(self) -> None:
        self.assertEqual(
            pass_motion.motion_record(Motion(2, 1, 0)),
            {"finished": 2, "paused": 1, "running": 0},
        )


class MotionSectionTests(unittest.TestCase):
    """The report's section: present only with motion, and grouped by page."""

    def test_no_section_when_nothing_was_stilled_or_left_running(self) -> None:
        results = [_Result(_HOME, NO_MOTION), _Result(_HOME, NO_MOTION)]
        self.assertEqual(pass_motion.motion_section(results), "")
        self.assertEqual(pass_motion.motion_section([]), "")

    def test_sums_each_page_over_the_renderings_that_had_motion(self) -> None:
        dark = Rendering("chromium", "/", 390, 844, "dark", "on")
        results = [
            _Result(_HOME, Motion(2, 1, 0)),
            _Result(dark, Motion(2, 1, 0)),
            _Result(
                Rendering("webkit", "/archive/", 390, 844, "light", "on"), NO_MOTION
            ),
        ]
        text = pass_motion.motion_section(results)
        self.assertTrue(text.startswith("## Motion\n"))
        # What each class means is said, a paused one above all: one frame, its iteration start.
        self.assertIn("iteration start", text)
        self.assertIn(
            "- `/`, 2 renderings: 4 finished, 2 paused, 0 still running", text
        )
        self.assertNotIn("/archive/", text)
        self.assertNotIn(_LATE_PART, text)

    def test_names_the_renderings_still_running_when_the_settle_ended(self) -> None:
        scroll = [
            Rendering("webkit", "/scroll/", 390, 844, mode, "on")
            for mode in ("light", "dark")
        ]
        results = [
            _Result(scroll[0], Motion(0, 0, 1)),
            _Result(scroll[1], Motion(1, 0, 0)),
        ]
        text = pass_motion.motion_section(results)
        self.assertIn(
            "- `/scroll/`, 2 renderings: 1 finished, 0 paused, 1 still running when "
            f"the settle ended, in 1 rendering: {scroll[0].label}",
            text,
        )

    def test_a_single_rendering_is_not_counted_in_the_plural(self) -> None:
        text = pass_motion.motion_section([_Result(_HOME, Motion(0, 1, 0))])
        self.assertIn("- `/`, 1 rendering: 0 finished, 1 paused, 0 still running", text)

    def test_the_late_load_s_motion_is_said_apart_and_never_added_to_the_page_load_s(
        self,
    ) -> None:
        # Both loads still the same animations, so a sum would count each one twice.
        text = pass_motion.motion_section(
            [_Result(_HOME, Motion(1, 1, 0), late_motion=Motion(0, 1, 1))]
        )
        loaded = "- `/`, 1 rendering: 1 finished, 1 paused, 0 still running"
        late = (
            "- `/`, 1 rendering: 0 finished, 1 paused, 1 still running when the "
            f"settle ended, in 1 rendering: {_HOME.label}"
        )
        self.assertLess(text.index(loaded), text.index(_LATE_PART))
        self.assertLess(text.index(_LATE_PART), text.index(late))
        # What the late load stills, and why that is safe, is said where its counts are.
        late_part = text[text.index(_LATE_PART) :]
        self.assertIn("layout-inert", late_part)
        self.assertIn("LAYOUT_INERT_PROPERTIES", late_part)
        self.assertNotIn("1 finished, 2 paused", text)

    def test_late_motion_alone_still_makes_the_section(self) -> None:
        text = pass_motion.motion_section(
            [_Result(_HOME, NO_MOTION, late_motion=Motion(0, 0, 2))]
        )
        self.assertTrue(text.startswith("## Motion\n"))
        late_part = text[text.index(_LATE_PART) :]
        self.assertIn(
            "- `/`, 1 rendering: 0 finished, 0 paused, 2 still running", late_part
        )
        self.assertEqual(text.count("- `/`"), 1)


class MotionLineTests(unittest.TestCase):
    """The console's closing lines: one per load, naming each page motion was stilled or left in."""

    def test_no_line_when_nothing_was_stilled_or_left_running(self) -> None:
        self.assertEqual(pass_motion.motion_lines([_Result(_HOME, NO_MOTION)]), [])

    def test_the_late_load_has_a_line_of_its_own_after_the_page_load_s(self) -> None:
        both = _Result(_HOME, Motion(2, 1, 0), late_motion=Motion(0, 1, 1))
        self.assertEqual(
            pass_motion.motion_lines([both]),
            [
                "motion stilled or left running while settling: "
                "/ (2 finished, 1 paused, 0 still running)",
                "late-script load: motion stilled or left running while settling: "
                "/ (0 finished, 1 paused, 1 still running)",
            ],
        )
        late_only = _Result(_HOME, NO_MOTION, late_motion=Motion(1, 0, 0))
        self.assertEqual(
            pass_motion.motion_lines([late_only]),
            [
                "late-script load: motion stilled or left running while settling: "
                "/ (1 finished, 0 paused, 0 still running)"
            ],
        )

    def test_names_each_page_with_its_counts_and_skips_the_still_ones(self) -> None:
        results = [
            _Result(_HOME, Motion(2, 1, 0)),
            _Result(
                Rendering("chromium", "/archive/", 390, 844, "light", "on"), NO_MOTION
            ),
            _Result(
                Rendering("webkit", "/scroll/", 390, 844, "light", "on"),
                Motion(0, 0, 3),
            ),
        ]
        self.assertEqual(
            pass_motion.motion_lines(results),
            [
                "motion stilled or left running while settling: "
                "/ (2 finished, 1 paused, 0 still running); "
                "/scroll/ (0 finished, 0 paused, 3 still running)"
            ],
        )


#: A faithful read-back for `_HOME`, so a settled rendering goes on to be measured.
_HOME_STATE: Mapping[str, object] = {
    "innerWidth": 390,
    "innerHeight": 844,
    "clientWidth": 390,
    "scriptingNone": False,
    "dark": False,
    "landscape": False,
    "reducedMotion": False,
}

_NO_LAYOUT = {
    "overflow": 0,
    "pastRightEdge": [],
    "hiddenRendered": [],
    "smallTargets": [],
}


class _ScriptedProbe:
    """Answers the probe's operations from scripted records, logging each operation asked."""

    def __init__(self, polls: Sequence[Motion], state: Mapping[str, object]) -> None:
        """`polls` is what each `stillMotion` in turn reports; `state` the page's read-back."""
        self._polls: Iterator[Motion] = iter(polls)
        self._state = state
        self.asked: list[str] = []

    def __call__(self, page: object, operation: str, **extra: object) -> object:
        """One operation's scripted record."""
        self.asked.append(operation)
        if operation == "stillMotion":
            return pass_motion.motion_record(next(self._polls))
        records: dict[str, object] = {
            "signature": [390, 844, 3, "loaded", 0],
            "state": self._state,
            "layout": _NO_LAYOUT,
            "digest": [],
        }
        return records[operation]


def _settle_polling(times: int, *, settles: bool) -> Callable[..., bool]:
    """A settle that reads the page `times` times, then settles or runs out of time."""

    def settle(read: Callable[[], object], **_timing: object) -> bool:
        for _ in range(times):
            read()
        return settles

    return settle


class SettleTests(unittest.TestCase):
    """`pass_engine.measure` stills before every read, and keeps what it tallied."""

    def _measure(
        self,
        polls: Sequence[Motion],
        *,
        settles: bool = True,
        state: Mapping[str, object] | None = None,
    ) -> tuple[RenderingResult, list[str]]:
        probe = _ScriptedProbe(polls, state or _HOME_STATE)
        page = mock.Mock()
        with (
            mock.patch.object(pass_engine, "probe", probe),
            mock.patch.object(
                pass_engine,
                "wait_until_settled",
                _settle_polling(len(polls), settles=settles),
            ),
        ):
            result = pass_engine.measure(
                page,
                pass_engine.Listeners(page, "http://127.0.0.1:0"),
                _HOME,
                "http://127.0.0.1:0",
                timeout=5.0,
                prove=False,
            )
        return result, probe.asked

    def test_each_read_stills_first_and_the_rendering_keeps_the_tally(self) -> None:
        polls = (Motion(2, 1, 1), Motion(1, 0, 1), NO_MOTION)
        result, asked = self._measure(polls)
        self.assertEqual(result.faults, ())
        self.assertEqual(result.motion, Motion(finished=3, paused=1, running=0))
        self.assertEqual(asked[:6], ["stillMotion", "signature"] * 3)

    def test_a_settle_that_runs_out_of_time_keeps_what_it_stilled(self) -> None:
        result, _ = self._measure((Motion(1, 0, 0), Motion(0, 2, 1)), settles=False)
        self.assertIn("still changing", result.faults[0])
        self.assertEqual(result.motion, Motion(1, 2, 1))

    def test_a_read_back_that_disagrees_keeps_what_the_settle_stilled(self) -> None:
        wrong = {**_HOME_STATE, "innerWidth": 375}
        result, _ = self._measure((Motion(0, 1, 0),), state=wrong)
        self.assertIn("375px wide", result.faults[0])
        self.assertEqual(result.motion, Motion(0, 1, 0))


if __name__ == "__main__":
    unittest.main()
