"""Grouped suite: the late-script layout-shift detector's verdicts -- pure, no browser.

What the in-page probe recorded is turned into a score here: only shifts AFTER the held scripts
were released count, grouped into session windows the way Cumulative Layout Shift groups them.
A rendering this detector cannot measure says why, and never reads as a clean zero.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_shift  # noqa: E402 - the tool's private src/, via the insert above
from pass_checks import PLANT_MARK, ProbeError  # noqa: E402 - the tool's private src/
from pass_targets import Rendering  # noqa: E402 - the tool's private src/

_PLANT = f"body>div:9{PLANT_MARK}{pass_shift.LATE_SHIFT}"


def _shift(start: float, score: float, *sources: str) -> pass_shift.Shift:
    """A late shift starting at `start` ms, moving `sources`."""
    return pass_shift.Shift(start, score, sources or ("body>p:1",))


def _measured(*shifts: pass_shift.Shift) -> pass_shift.LateShift:
    """A measured rendering that recorded `shifts`."""
    return pass_shift.LateShift(pass_shift.MEASURED, shifts=shifts)


def _entry(
    start: float, value: float, *, recent_input: bool = False, sources: object = None
) -> dict[str, object]:
    """One layout-shift entry as the probe reports it."""
    return {
        "start": start,
        "value": value,
        "recentInput": recent_input,
        "sources": ["body>p:1"] if sources is None else sources,
    }


def _record(**changes: object) -> dict[str, object]:
    """A probe record from a page that painted, then shifted once its scripts ran."""
    record: dict[str, object] = {
        "supported": True,
        "released": 100.0,
        "paints": [60.0],
        "presentableAt": 60.0,
        "blockingHeld": False,
        "entries": [_entry(150.0, 0.2)],
    }
    record.update(changes)
    return record


class ApplicabilityTests(unittest.TestCase):
    """Which renderings get their own late-script load, and what the rest report instead."""

    def test_only_chromium_reports_layout_shifts(self) -> None:
        self.assertEqual(pass_shift.SHIFT_ENGINES, ("chromium",))

    def test_chromium_with_scripts_is_measured(self) -> None:
        rendering = Rendering("chromium", "/", 390, 844, "light", "on")
        self.assertIsNone(pass_shift.not_measured(rendering))

    def test_scripts_off_is_not_applicable(self) -> None:
        rendering = Rendering("chromium", "/", 390, 844, "light", "off")
        late = pass_shift.not_measured(rendering)
        assert late is not None
        self.assertEqual(late.status, pass_shift.NOT_APPLICABLE)
        self.assertIn("scripts off", late.reason)

    def test_another_engine_is_not_measured_and_says_why(self) -> None:
        rendering = Rendering("webkit", "/", 390, 844, "dark", "on")
        late = pass_shift.not_measured(rendering)
        assert late is not None
        self.assertEqual(late.status, pass_shift.NOT_MEASURED)
        self.assertIn("webkit", late.reason)
        self.assertEqual(pass_shift.late_findings(late), ())


class HoldStateTests(unittest.TestCase):
    """The scripts are released once the page has painted, or once a held script blocks it."""

    def _state(self, **flags: bool) -> pass_shift.HoldState:
        record = {"painted": False, "fontsLoaded": False, "blocked": False, **flags}
        return pass_shift.hold_state_from(record)

    def test_a_paint_with_its_fonts_in_ends_the_hold(self) -> None:
        self.assertTrue(self._state(painted=True, fontsLoaded=True).ended)

    def test_a_paint_still_waiting_on_a_font_keeps_holding(self) -> None:
        # A face that swaps in after the release would be counted as the scripts' doing.
        self.assertFalse(self._state(painted=True).ended)

    def test_a_held_script_that_blocks_rendering_ends_the_hold(self) -> None:
        self.assertTrue(self._state(blocked=True).ended)

    def test_nothing_yet_keeps_holding(self) -> None:
        self.assertFalse(self._state(fontsLoaded=True).ended)

    def test_a_malformed_record_is_a_probe_error(self) -> None:
        with self.assertRaises(ProbeError):
            pass_shift.hold_state_from(
                {"painted": "yes", "fontsLoaded": True, "blocked": False}
            )


class ShiftRecordTests(unittest.TestCase):
    """Reading the probe's record: only what happened after the release is a late shift."""

    def test_a_shift_after_the_release_is_kept(self) -> None:
        record = pass_shift.shift_record_from(_record())
        self.assertEqual(record.shifts, (_shift(150.0, 0.2),))
        self.assertTrue(record.supported)
        self.assertTrue(record.painted_first)
        self.assertFalse(record.blocking_held)

    def test_a_shift_before_the_release_is_not_the_scripts_doing(self) -> None:
        record = pass_shift.shift_record_from(
            _record(entries=[_entry(80.0, 0.003), _entry(150.0, 0.2)])
        )
        self.assertEqual(record.shifts, (_shift(150.0, 0.2),))

    def test_a_shift_after_input_is_not_counted(self) -> None:
        record = pass_shift.shift_record_from(
            _record(entries=[_entry(150.0, 0.2, recent_input=True)])
        )
        self.assertEqual(record.shifts, ())

    def test_a_paint_at_the_release_counts_as_first(self) -> None:
        record = pass_shift.shift_record_from(_record(paints=[100.0]))
        self.assertTrue(record.painted_first)

    def test_a_paint_only_after_the_release_is_not_first(self) -> None:
        record = pass_shift.shift_record_from(_record(paints=[140.0]))
        self.assertFalse(record.painted_first)

    def test_no_paint_at_all_is_not_first(self) -> None:
        record = pass_shift.shift_record_from(_record(paints=[]))
        self.assertFalse(record.painted_first)

    def test_whole_numbers_are_accepted_as_times(self) -> None:
        record = pass_shift.shift_record_from(
            _record(released=100, paints=[60], entries=[_entry(150, 0.2)])
        )
        self.assertEqual(record.shifts, (_shift(150.0, 0.2),))

    def test_the_flags_are_passed_through(self) -> None:
        record = pass_shift.shift_record_from(
            _record(supported=False, blockingHeld=True)
        )
        self.assertFalse(record.supported)
        self.assertTrue(record.blocking_held)

    def test_malformed_records_are_probe_errors(self) -> None:
        malformed = [
            "not a record",
            _record(released=None),
            _record(released=True),
            _record(paints="60"),
            _record(paints=[False]),
            _record(entries={}),
            _record(entries=["not an entry"]),
            _record(entries=[_entry(150.0, 0.2, sources="body>p:1")]),
            _record(entries=[_entry(150.0, 0.2, sources=[1])]),
            _record(entries=[{**_entry(150.0, 0.2), "recentInput": "no"}]),
            _record(supported="yes"),
        ]
        for raw in malformed:
            with self.subTest(raw=raw), self.assertRaises(ProbeError):
                pass_shift.shift_record_from(raw)


class PresentableTests(unittest.TestCase):
    """Counting starts once the page is PRESENTABLE, which is not always the release.

    The hold normally ends on the page having painted in its own fonts, so the release IS the
    moment it became presentable and the two are the same instant. They come apart on the one
    path that releases early: a held script the page cannot paint without (`blocking="render"`)
    is let through at once, because no paint will come until it runs -- so the release precedes
    the first paint, and a web font swapping in after that paint would be charged to the
    scripts. That is exactly what waiting for the fonts exists to prevent, and it lapsed on this
    path alone.

    Found by the fix it was built to check: making a site's entry point render-blocking left
    every home-page rendering with a new window at 73-77ms naming header and heading TEXT,
    where the two pages whose entry points were untouched scored identically to the run before.
    """

    def test_a_shift_between_an_early_release_and_the_first_paint_is_not_counted(
        self,
    ) -> None:
        # The artifact itself: released at once at 10ms, presentable at 80ms, and a font
        # swapping in at 90ms. Only the 90ms shift is after the paint, and the 40ms one is a
        # page the reader had not yet been shown moving.
        record = pass_shift.shift_record_from(
            _record(
                released=10.0,
                paints=[80.0],
                presentableAt=80.0,
                blockingHeld=True,
                entries=[_entry(40.0, 0.5), _entry(90.0, 0.004)],
            )
        )

        self.assertEqual(record.shifts, (_shift(90.0, 0.004),))

    def test_the_release_still_decides_when_it_is_the_later_of_the_two(self) -> None:
        # The ordinary path, unchanged: the hold waited for paint and fonts, so the page was
        # presentable at 60ms and released at 100ms. A shift at 80ms is before the release and
        # is not the scripts' doing, exactly as it was.
        record = pass_shift.shift_record_from(
            _record(entries=[_entry(80.0, 0.003), _entry(150.0, 0.2)])
        )

        self.assertEqual(record.shifts, (_shift(150.0, 0.2),))

    def test_a_page_that_never_became_presentable_records_no_such_moment(self) -> None:
        record = pass_shift.shift_record_from(_record(presentableAt=None))

        self.assertIsNone(record.presentable_at)

    def test_a_malformed_presentable_moment_is_a_probe_error(self) -> None:
        with self.assertRaises(pass_shift.ProbeError):
            pass_shift.shift_record_from(_record(presentableAt="soon"))


class RecordFaultTests(unittest.TestCase):
    """A late shift is only measurable when the page painted before its scripts ran."""

    def _faults(self, **changes: object) -> list[str]:
        return pass_shift.record_faults(
            pass_shift.shift_record_from(_record(**changes))
        )

    def test_a_page_that_painted_first_is_measured(self) -> None:
        self.assertEqual(self._faults(), [])

    def test_a_blocked_page_that_never_became_presentable_is_refused(self) -> None:
        # The page could not paint until its scripts ran, and then never finished its fonts --
        # so every shift recorded sits in the window where a face swapping in cannot be told
        # from a script moving the page. Refused rather than measured against that risk, which
        # is the choice the fonts half of the hold already makes on the other path.
        (fault,) = self._faults(
            released=10.0, paints=[80.0], presentableAt=None, blockingHeld=True
        )

        self.assertIn("fonts", fault)

    def test_an_engine_build_without_layout_shifts_is_a_fault(self) -> None:
        (fault,) = self._faults(supported=False)
        self.assertIn("does not report layout shifts", fault)

    def test_a_page_whose_held_script_blocks_its_first_paint_is_measured(self) -> None:
        # Nothing was on screen before the scripts ran, so nothing on screen could move.
        self.assertEqual(self._faults(paints=[], blockingHeld=True), [])

    def test_a_page_that_never_painted_with_nothing_blocking_it_is_a_fault(
        self,
    ) -> None:
        (fault,) = self._faults(paints=[140.0], blockingHeld=False)
        self.assertIn("did not paint", fault)


class AwaitsPresentableTests(unittest.TestCase):
    """Which records lack only the moment the page became presentable, which frames still bring.

    That moment is marked in the page by its frames -- the first paint, then two animation frames
    after the fonts are in -- so on a loaded machine it can come well after the page has loaded
    and its layout has stopped changing. A record waiting on it is not yet a verdict.
    """

    def _awaits(self, **changes: object) -> bool:
        return pass_shift.awaits_presentable(
            pass_shift.shift_record_from(_record(**changes))
        )

    def test_a_blocked_page_whose_frames_have_not_shown_it_yet_awaits_them(
        self,
    ) -> None:
        # Measured on a stalled compositor: at `load` the timeline held no paint at all.
        self.assertTrue(self._awaits(paints=[], presentableAt=None, blockingHeld=True))

    def test_a_page_that_painted_first_but_is_not_yet_marked_presentable_awaits_it(
        self,
    ) -> None:
        self.assertTrue(self._awaits(presentableAt=None))

    def test_a_presentable_page_awaits_nothing(self) -> None:
        self.assertFalse(self._awaits())

    def test_an_engine_build_without_layout_shifts_awaits_nothing(self) -> None:
        # Refused whatever comes, so waiting would only spend the bound on a known fault.
        self.assertFalse(self._awaits(supported=False, presentableAt=None))

    def test_a_page_that_never_painted_with_nothing_blocking_it_awaits_nothing(
        self,
    ) -> None:
        self.assertFalse(
            self._awaits(paints=[140.0], presentableAt=None, blockingHeld=False)
        )


class SessionWindowTests(unittest.TestCase):
    """Late shifts grouped the way Cumulative Layout Shift groups them, and the score."""

    def test_no_shifts_scores_zero(self) -> None:
        late = _measured()
        self.assertEqual(late.windows, ())
        self.assertEqual(late.score, 0.0)

    def test_shifts_under_a_second_apart_share_a_window(self) -> None:
        late = _measured(_shift(100.0, 0.05, "a"), _shift(900.0, 0.02, "b", "a"))
        (window,) = late.windows
        self.assertAlmostEqual(window.score, 0.07)
        self.assertEqual(window.start_ms, 100.0)
        self.assertEqual(window.sources, ("a", "b"))

    def test_a_gap_of_a_whole_second_starts_a_new_window(self) -> None:
        late = _measured(_shift(100.0, 0.05), _shift(1100.0, 0.02))
        self.assertEqual([w.score for w in late.windows], [0.05, 0.02])

    def test_no_window_spans_five_seconds(self) -> None:
        shifts = [_shift(900.0 * step, 0.01) for step in range(7)]
        late = _measured(*shifts)
        # 0..4500ms fits in one window; the shift at 5400ms would stretch it past 5s.
        self.assertEqual(len(late.windows), 2)
        self.assertEqual(late.windows[1].start_ms, 5400.0)

    def test_the_score_is_the_largest_window(self) -> None:
        late = _measured(
            _shift(100.0, 0.02), _shift(2000.0, 0.09), _shift(4000.0, 0.01)
        )
        self.assertEqual(late.score, 0.09)

    def test_windows_follow_start_time_whatever_order_the_shifts_arrive_in(
        self,
    ) -> None:
        late = _measured(_shift(2000.0, 0.09), _shift(100.0, 0.02))
        self.assertEqual([w.start_ms for w in late.windows], [100.0, 2000.0])


class FindingTests(unittest.TestCase):
    """Each window above the threshold is one finding naming its score and what moved."""

    def test_a_window_above_the_threshold_is_a_finding(self) -> None:
        late = _measured(_shift(150.0, 0.0962, "body>figure:1", "body>p:2"))
        self.assertEqual(
            pass_shift.late_findings(late, threshold=0.01),
            ("0.0962 from body>figure:1, body>p:2",),
        )

    def test_a_window_at_the_threshold_is_not(self) -> None:
        late = _measured(_shift(150.0, 0.01))
        self.assertEqual(pass_shift.late_findings(late, threshold=0.01), ())

    def test_the_default_threshold_is_the_module_constant(self) -> None:
        just_over = _measured(_shift(150.0, pass_shift.THRESHOLD * 1.01))
        just_under = _measured(_shift(150.0, pass_shift.THRESHOLD * 0.99))
        self.assertEqual(len(pass_shift.late_findings(just_over)), 1)
        self.assertEqual(pass_shift.late_findings(just_under), ())

    def test_every_window_above_the_threshold_is_its_own_finding(self) -> None:
        late = _measured(_shift(150.0, 0.2, "a"), _shift(3000.0, 0.3, "b"))
        self.assertEqual(
            pass_shift.late_findings(late, threshold=0.01),
            ("0.2000 from a", "0.3000 from b"),
        )

    def test_a_long_list_of_sources_is_cut_short(self) -> None:
        names = [f"body>p:{n}" for n in range(1, 8)]
        late = _measured(_shift(150.0, 0.2, *names))
        (finding,) = pass_shift.late_findings(late, threshold=0.01)
        self.assertIn("body>p:5", finding)
        self.assertNotIn("body>p:6", finding)
        self.assertTrue(finding.endswith("and 2 more"))

    def test_a_rendering_not_measured_finds_nothing(self) -> None:
        late = pass_shift.LateShift(pass_shift.NOT_APPLICABLE, reason="scripts off")
        self.assertEqual(late.score, 0.0)
        self.assertEqual(pass_shift.late_findings(late), ())


class PlantVerdictTests(unittest.TestCase):
    """The planted shift: caught only when a window naming it scores above the threshold."""

    def test_a_plant_in_a_window_above_the_threshold_is_caught(self) -> None:
        after = _measured(_shift(150.0, 0.05), _shift(3000.0, 0.5, _PLANT))
        self.assertEqual(
            pass_shift.plant_verdict(took=True, moved=True, after=after), "caught"
        )

    def test_a_plant_scoring_under_the_threshold_is_missed(self) -> None:
        after = _measured(_shift(3000.0, pass_shift.THRESHOLD / 2, _PLANT))
        self.assertEqual(
            pass_shift.plant_verdict(took=True, moved=True, after=after), "missed"
        )

    def test_a_plant_that_moved_but_was_never_reported_is_missed(self) -> None:
        after = _measured(_shift(150.0, 0.5, "body>p:1"))
        self.assertEqual(
            pass_shift.plant_verdict(took=True, moved=True, after=after), "missed"
        )

    def test_the_plant_is_named_by_any_shift_whatever_its_score(self) -> None:
        # What the proof waits on before judging: the move has been reported at all.
        self.assertTrue(
            pass_shift.names_plant(_measured(_shift(3000.0, 0.0001, _PLANT)))
        )
        self.assertFalse(pass_shift.names_plant(_measured(_shift(150.0, 0.5))))

    def test_a_plant_that_did_not_take_or_did_not_move_was_not_planted(self) -> None:
        after = _measured(_shift(3000.0, 0.5, _PLANT))
        for took, moved in ((False, False), (True, False)):
            with self.subTest(took=took, moved=moved):
                self.assertEqual(
                    pass_shift.plant_verdict(took=took, moved=moved, after=after),
                    "not planted",
                )


if __name__ == "__main__":
    unittest.main()
