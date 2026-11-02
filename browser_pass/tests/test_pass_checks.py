"""Grouped suite: the verdicts (emulation read back, findings, planted faults, and settling)."""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_checks  # noqa: E402 - the tool's private src/, via the insert above
from pass_targets import Rendering  # noqa: E402 - the tool's private src/, via the insert above

_PHONE = Rendering("webkit", "/", 390, 844, "dark", "off")

#: What a faithful page reports back for `_PHONE`.
_FAITHFUL = pass_checks.PageState(
    inner_width=390,
    inner_height=844,
    client_width=390,
    scripting_none=True,
    dark=True,
    landscape=False,
    reduced_motion=False,
)

_CLEAN = pass_checks.Observation(
    overflow_px=0,
    past_right_edge=(),
    hidden_rendered=(),
    small_targets=(),
    console_errors=(),
    page_errors=(),
    http_errors=(),
    csp_violations=(),
)


class PageStateTests(unittest.TestCase):
    """Reading the state the page reports back."""

    def test_reads_the_probe_record(self) -> None:
        raw = {
            "innerWidth": 390,
            "innerHeight": 844,
            "clientWidth": 390,
            "scriptingNone": True,
            "dark": True,
            "landscape": False,
            "reducedMotion": False,
        }
        self.assertEqual(pass_checks.page_state_from(raw), _FAITHFUL)

    def test_refuses_a_record_missing_a_field(self) -> None:
        with self.assertRaisesRegex(pass_checks.ProbeError, "innerHeight"):
            pass_checks.page_state_from({"innerWidth": 390})

    def test_refuses_a_field_of_the_wrong_type(self) -> None:
        raw = {
            "innerWidth": "390",
            "innerHeight": 844,
            "clientWidth": 390,
            "scriptingNone": True,
            "dark": True,
            "landscape": False,
            "reducedMotion": False,
        }
        with self.assertRaisesRegex(pass_checks.ProbeError, "innerWidth"):
            pass_checks.page_state_from(raw)

    def test_refuses_a_flag_that_is_not_true_or_false(self) -> None:
        raw = {
            "innerWidth": 390,
            "innerHeight": 844,
            "clientWidth": 390,
            "scriptingNone": 1,
            "dark": True,
            "landscape": False,
            "reducedMotion": False,
        }
        with self.assertRaisesRegex(pass_checks.ProbeError, "scriptingNone"):
            pass_checks.page_state_from(raw)

    def test_refuses_a_record_that_is_not_a_mapping(self) -> None:
        with self.assertRaisesRegex(pass_checks.ProbeError, "state"):
            pass_checks.page_state_from([390, 844])


class EmulationFaultTests(unittest.TestCase):
    """Every requested state is read back from inside the page before anything is measured."""

    def test_a_faithful_page_has_no_faults(self) -> None:
        self.assertEqual(pass_checks.emulation_faults(_PHONE, _FAITHFUL), [])

    def test_a_viewport_of_the_wrong_size_is_a_fault(self) -> None:
        faults = pass_checks.emulation_faults(
            _PHONE,
            replace(_FAITHFUL, inner_width=375, client_width=375, inner_height=800),
        )
        self.assertEqual(len(faults), 2)
        self.assertIn("375px wide, asked for 390px", faults[0])
        self.assertIn("800px tall, asked for 844px", faults[1])

    def test_a_classic_scrollbar_is_a_fault(self) -> None:
        faults = pass_checks.emulation_faults(
            _PHONE, replace(_FAITHFUL, client_width=375)
        )
        self.assertEqual(len(faults), 1)
        self.assertIn("scrollbar", faults[0])

    def test_scripts_off_not_honored_is_a_fault(self) -> None:
        faults = pass_checks.emulation_faults(
            _PHONE, replace(_FAITHFUL, scripting_none=False)
        )
        self.assertEqual(len(faults), 1)
        self.assertIn("(scripting: none) does not match", faults[0])

    def test_scripting_reported_off_with_scripts_on_is_a_fault(self) -> None:
        scripts_on = replace(_PHONE, scripts="on")
        faults = pass_checks.emulation_faults(scripts_on, _FAITHFUL)
        self.assertEqual(len(faults), 1)
        self.assertIn("(scripting: none) matches", faults[0])

    def test_the_wrong_color_scheme_is_a_fault(self) -> None:
        faults = pass_checks.emulation_faults(_PHONE, replace(_FAITHFUL, dark=False))
        self.assertEqual(faults, ["the color scheme reads light, asked for dark"])

    def test_the_wrong_orientation_is_a_fault(self) -> None:
        faults = pass_checks.emulation_faults(
            _PHONE, replace(_FAITHFUL, landscape=True)
        )
        self.assertEqual(
            faults, ["the orientation reads landscape for a 390x844 viewport"]
        )

    def test_reduced_motion_is_a_fault(self) -> None:
        faults = pass_checks.emulation_faults(
            _PHONE, replace(_FAITHFUL, reduced_motion=True)
        )
        self.assertEqual(len(faults), 1)
        self.assertIn("prefers-reduced-motion", faults[0])


class ObservationTests(unittest.TestCase):
    """Turning what a page showed into findings, one list per detector."""

    def test_the_detectors_are_the_seven_the_passes_measured_then_csp(self) -> None:
        self.assertEqual(
            pass_checks.DETECTORS,
            (
                "page_overflow",
                "past_right_edge",
                "hidden_rendered",
                "small_target",
                "console_error",
                "page_error",
                "http_error",
                "csp_violation",
            ),
        )

    def test_builds_an_observation_from_the_layout_probe_and_the_listeners(
        self,
    ) -> None:
        layout = {
            "overflow": 12,
            "pastRightEdge": ["body>div:1 right edge at 402px"],
            "hiddenRendered": [],
            "smallTargets": ["body>button:1 10x10"],
        }
        observation = pass_checks.observation_from(
            layout,
            console_errors=["boom"],
            page_errors=[],
            http_errors=["404 /x.png"],
            csp_violations=["script-src blocked eval on /"],
        )
        self.assertEqual(observation.overflow_px, 12)
        self.assertEqual(observation.csp_violations, ("script-src blocked eval on /",))
        self.assertEqual(observation.small_targets, ("body>button:1 10x10",))
        self.assertEqual(observation.http_errors, ("404 /x.png",))

    def test_refuses_a_layout_record_of_the_wrong_shape(self) -> None:
        with self.assertRaisesRegex(pass_checks.ProbeError, "overflow"):
            pass_checks.observation_from(
                {"overflow": "12"},
                console_errors=[],
                page_errors=[],
                http_errors=[],
                csp_violations=[],
            )
        with self.assertRaisesRegex(pass_checks.ProbeError, "smallTargets"):
            pass_checks.observation_from(
                {
                    "overflow": 0,
                    "pastRightEdge": [],
                    "hiddenRendered": [],
                    "smallTargets": [3],
                },
                console_errors=[],
                page_errors=[],
                http_errors=[],
                csp_violations=[],
            )
        with self.assertRaisesRegex(pass_checks.ProbeError, "layout"):
            pass_checks.observation_from(
                "nothing",
                console_errors=[],
                page_errors=[],
                http_errors=[],
                csp_violations=[],
            )

    def test_a_clean_page_has_no_findings(self) -> None:
        self.assertEqual(pass_checks.findings_of(_CLEAN), {})

    def test_reports_each_detector_that_fired_and_only_those(self) -> None:
        observation = replace(
            _CLEAN, overflow_px=286, console_errors=("Failed to load resource",)
        )
        self.assertEqual(
            pass_checks.findings_of(observation),
            {
                "page_overflow": ("the page is 286px wider than the viewport",),
                "console_error": ("Failed to load resource",),
            },
        )


class PlantVerdictTests(unittest.TestCase):
    """Proof mode: each detector must catch the fault planted for it."""

    def _caught_by_all(self) -> pass_checks.Observation:
        mark = pass_checks.PLANT_MARK
        return replace(
            _CLEAN,
            overflow_px=100,
            past_right_edge=(f"body>div:9{mark}past_right_edge right edge at 420px",),
            hidden_rendered=(f"body>div:10{mark}hidden_rendered",),
            small_targets=(f"body>button:2{mark}small_target 10x10",),
            console_errors=(pass_checks.CONSOLE_PLANT_TEXT,),
            page_errors=(f"Error: {pass_checks.PAGE_ERROR_PLANT_TEXT}",),
            http_errors=(f"404 {pass_checks.MISSING_PLANT_PATH}",),
        )

    _ALL_TOOK = dict.fromkeys(pass_checks.DETECTORS, True)

    def test_every_detector_catches_its_plant(self) -> None:
        verdicts = pass_checks.plant_verdicts(
            _CLEAN,
            self._caught_by_all(),
            self._ALL_TOOK,
            scripts_on=True,
            csp_caught=True,
        )
        self.assertEqual(verdicts, dict.fromkeys(pass_checks.DETECTORS, "caught"))

    def test_a_detector_that_stays_silent_misses(self) -> None:
        verdicts = pass_checks.plant_verdicts(
            _CLEAN, _CLEAN, self._ALL_TOOK, scripts_on=True, csp_caught=False
        )
        self.assertEqual(verdicts, dict.fromkeys(pass_checks.DETECTORS, "missed"))

    def test_an_overflow_already_present_must_grow_to_count_as_caught(self) -> None:
        before = replace(_CLEAN, overflow_px=100)
        verdicts = pass_checks.plant_verdicts(
            before,
            self._caught_by_all(),
            self._ALL_TOOK,
            scripts_on=True,
            csp_caught=True,
        )
        self.assertEqual(verdicts["page_overflow"], "missed")

    def test_an_element_finding_without_the_plant_mark_does_not_count(self) -> None:
        after = replace(self._caught_by_all(), small_targets=("body>button:1 10x10",))
        verdicts = pass_checks.plant_verdicts(
            _CLEAN, after, self._ALL_TOOK, scripts_on=True, csp_caught=True
        )
        self.assertEqual(verdicts["small_target"], "missed")

    def test_a_plant_that_did_not_take_is_not_planted_rather_than_missed(self) -> None:
        took = {**self._ALL_TOOK, "hidden_rendered": False}
        verdicts = pass_checks.plant_verdicts(
            _CLEAN, _CLEAN, took, scripts_on=True, csp_caught=False
        )
        self.assertEqual(verdicts["hidden_rendered"], "not planted")
        self.assertEqual(verdicts["small_target"], "missed")

    def test_a_page_error_cannot_be_raised_with_scripts_off(self) -> None:
        verdicts = pass_checks.plant_verdicts(
            _CLEAN,
            self._caught_by_all(),
            self._ALL_TOOK,
            scripts_on=False,
            csp_caught=True,
        )
        self.assertEqual(verdicts["page_error"], "n/a")
        self.assertEqual(verdicts["console_error"], "caught")

    def test_listener_plants_have_arrived_once_all_three_are_seen(self) -> None:
        self.assertTrue(
            pass_checks.listener_plants_arrived(self._caught_by_all(), scripts_on=True)
        )
        self.assertFalse(pass_checks.listener_plants_arrived(_CLEAN, scripts_on=True))
        no_page_error = replace(self._caught_by_all(), page_errors=())
        self.assertTrue(
            pass_checks.listener_plants_arrived(no_page_error, scripts_on=False)
        )
        self.assertFalse(
            pass_checks.listener_plants_arrived(no_page_error, scripts_on=True)
        )


class PlantTookTests(unittest.TestCase):
    """Reading which plants took, as the probe checked them independently of the detectors."""

    def test_reads_one_flag_per_detector(self) -> None:
        raw = dict.fromkeys(pass_checks.DETECTORS, True)
        self.assertEqual(pass_checks.plant_took_from(raw), raw)

    def test_a_missing_or_non_boolean_flag_counts_as_not_taken(self) -> None:
        took = pass_checks.plant_took_from(
            {"page_overflow": "yes", "small_target": True}
        )
        self.assertEqual(took["small_target"], True)
        self.assertEqual(took["page_overflow"], False)
        self.assertEqual(took["http_error"], False)

    def test_refuses_a_record_that_is_not_a_mapping(self) -> None:
        with self.assertRaisesRegex(pass_checks.ProbeError, "plant"):
            pass_checks.plant_took_from(None)


class DisplayUrlTests(unittest.TestCase):
    """A request named compactly: a path on the measured origin, the full url elsewhere."""

    def test_a_request_to_the_measured_origin_is_named_by_path(self) -> None:
        self.assertEqual(
            pass_checks.display_url(
                "http://127.0.0.1:5000/shared/a.css?v=2", "http://127.0.0.1:5000"
            ),
            "/shared/a.css?v=2",
        )

    def test_a_request_elsewhere_keeps_its_whole_url(self) -> None:
        url = "https://fonts.example/a.woff2"
        self.assertEqual(pass_checks.display_url(url, "http://127.0.0.1:5000"), url)


class _FakeClock:
    """A clock that advances only when paused on, so a poll loop runs without real time."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def pause(self, seconds: float) -> None:
        self.now += seconds


def _reader(
    values: list[object], clock: _FakeClock, step: float
) -> Callable[[], object]:
    """Reads `values` in sequence, one per `step` seconds of fake time; the last one repeats."""

    def read() -> object:
        index = min(int(round(clock.now / step)), len(values) - 1)
        return values[index]

    return read


class PollTests(unittest.TestCase):
    """Waiting on a signal: a quiet window, never a fixed sleep."""

    def test_settles_once_the_signature_stays_the_same_for_the_quiet_window(
        self,
    ) -> None:
        clock = _FakeClock()
        read = _reader([1, 2, 3, 3], clock, step=0.1)
        settled = pass_checks.wait_until_settled(
            read, pause=clock.pause, clock=clock, quiet=1.0, within=10.0, interval=0.1
        )
        self.assertTrue(settled)
        self.assertAlmostEqual(clock.now, 1.2, places=5)

    def test_reports_unsettled_when_the_signature_keeps_changing(self) -> None:
        clock = _FakeClock()
        read = _reader(list(range(1000)), clock, step=0.1)
        settled = pass_checks.wait_until_settled(
            read, pause=clock.pause, clock=clock, quiet=1.0, within=3.0, interval=0.1
        )
        self.assertFalse(settled)
        self.assertGreaterEqual(clock.now, 3.0)

    def test_poll_until_returns_as_soon_as_the_condition_holds(self) -> None:
        clock = _FakeClock()
        self.assertTrue(
            pass_checks.poll_until(
                lambda: clock.now >= 0.5,
                pause=clock.pause,
                clock=clock,
                within=2.0,
                interval=0.1,
            )
        )
        self.assertAlmostEqual(clock.now, 0.5, places=5)

    def test_poll_until_gives_up_at_the_deadline(self) -> None:
        clock = _FakeClock()
        self.assertFalse(
            pass_checks.poll_until(
                lambda: False, pause=clock.pause, clock=clock, within=1.0, interval=0.25
            )
        )
        self.assertGreaterEqual(clock.now, 1.0)


if __name__ == "__main__":
    unittest.main()
