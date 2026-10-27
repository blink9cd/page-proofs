"""Grouped suite: a Firefox state's actions, its settling, its print moment and its read-back.

The actions mean what they mean under the Chromium engine, with its problem texts: `wait_for`
waits for its selector to be visible as Playwright defines it, `click` waits the same way and
then clicks, retrying a click Firefox says is not interactable or is intercepted, and
`wait_ms` waits in this process. The first action that does not complete stops the state with
one finding, as a probe that fails does; a stopped state prints nothing, but its network is
still read and still refused if it left loopback. Settling polls the probe as the Chromium
engine does, at its interval, until the page has settled or the timeout since navigation has
passed; the print moment is waited for, or warned of when it had passed. Every wait is a poll:
`TickClock` moves only when the state sleeps, so a case reads each wait's every sleep.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

from _gecko_state_fixtures import (  # noqa: E402 - beside this module
    ELEMENT,
    StateCase,
    TickClock,
    timing,
    webdriver_error,
)
from _print_engine_fixtures import BASE_URL, state, target  # noqa: E402 - as above
from _print_playwright_fixtures import call, image_dict, probe_dict  # noqa: E402
from gecko_launch import IsolationBreach  # noqa: E402 - the tool's private src/
from gecko_state import StateJob  # noqa: E402 - as above
from print_record import EngineError, Problem, StateRecord  # noqa: E402 - as above
from print_targets import Action, Read  # noqa: E402 - as above

#: The settle poll, as the Chromium engine's `SETTLE_POLL_MS` gives it, in seconds.
_POLL = 0.1

#: A timeout a case waits out: no multiple of the poll, so float sums cannot land on it.
#: Looks fall at 0, 0.1, 0.2 and 0.3 s, and the last is past it.
_SHORT = 0.25
_LOOKS = 4

#: A probe of a page whose one image has not arrived yet.
_UNSETTLED = probe_dict(images=[image_dict("/a.png", complete=False, natural_width=0)])

#: What Firefox says of a click on an element something else covers.
_INTERCEPTED = webdriver_error("click", "element click intercepted", "covered")


def _wait_for(selector: str) -> Action:
    """A `wait_for` action on `selector`."""
    return Action(kind="wait_for", selector=selector, ms=None)


def _click(selector: str) -> Action:
    """A `click` action on `selector`."""
    return Action(kind="click", selector=selector, ms=None)


def _wait_ms(ms: int) -> Action:
    """A `wait_ms` action of `ms`."""
    return Action(kind="wait_ms", selector=None, ms=ms)


class ActionCase(StateCase):
    """A state's run on a clock that moves only when it sleeps."""

    def setUp(self) -> None:
        """Give the case its clock, logging each sleep into the shared log."""
        super().setUp()
        self.tick = TickClock(self.log)

    def ticking(
        self, *actions: Action, timeout_s: float = 5.0, **changes: object
    ) -> StateJob:
        """The job of a state running `actions`, on this case's clock, under `timeout_s`."""
        acting = state(actions=actions, **changes)  # type: ignore[arg-type]
        return self.job(
            acting, clock=self.tick, sleep=self.tick.sleep, timeout_s=timeout_s
        )

    def page_calls(self) -> list[tuple[str, ...]]:
        """The calls made after the page loaded and before the session ended, sleeps kept."""
        start = self.log.index(call("navigate", f"{BASE_URL}/")) + 1
        end = self.log.index(call("record_helpers"))
        return [entry for entry in self.log[start:end] if entry[0] != "unbound"]

    def assert_stopped(self, record: StateRecord, what: str) -> None:
        """The state stopped with the one finding `what`, read back nothing and printed nothing."""
        self.assertEqual(record.problems, (Problem("finding", what),))
        self.assertIsNone(record.probe)
        self.assertIsNone(record.printed_at_ms)
        self.assertEqual(record.pdfs, ())
        self.assertNotIn("print", [entry[0] for entry in self.log])
        self.assertEqual(self.calls()[-2:], [call("record_helpers"), call("delete")])


class WaitForTests(ActionCase):
    """`wait_for` polls until its selector is visible, or stops the state at the timeout."""

    def test_a_visible_selector_is_waited_for_with_one_look(self) -> None:
        self.run_state(job=self.ticking(_wait_for("#menu")))
        self.assertEqual(self.page_calls()[0], call("execute visible", "#menu"))
        self.assertEqual(self.page_calls()[1][0], "execute probe")

    def test_a_selector_not_visible_yet_is_polled_until_it_is(self) -> None:
        session = self.session(visible={"#menu": [False, None, True]})
        self.run_state(session, self.ticking(_wait_for("#menu")))
        look = call("execute visible", "#menu")
        self.assertEqual(
            self.page_calls()[:5],
            [look, call("sleep", _POLL), look, call("sleep", _POLL), look],
        )

    def test_a_selector_never_visible_stops_the_state_at_the_timeout(self) -> None:
        session = self.session(visible={"#menu": [False]})
        record = self.run_state(
            session, self.ticking(_wait_for("#menu"), timeout_s=_SHORT)
        )
        self.assert_stopped(record, 'action did not complete: wait_for "#menu"')
        looks = self.page_calls().count(call("execute visible", "#menu"))
        self.assertEqual(looks, _LOOKS)

    def test_a_selector_the_page_cannot_read_stops_the_state(self) -> None:
        refused = webdriver_error("execute", "javascript error", "not a valid selector")
        session = self.session(fail={"execute visible": refused})
        record = self.run_state(session, self.ticking(_wait_for("##")))
        self.assert_stopped(record, 'action did not complete: wait_for "##"')

    def test_a_stopped_state_still_reads_its_network(self) -> None:
        session = self.session(
            visible={"#menu": [False]},
            timing_reply=timing([f"{BASE_URL}/missing.png", 404]),
        )
        record = self.run_state(
            session, self.ticking(_wait_for("#menu"), timeout_s=_SHORT)
        )
        self.assertEqual(record.network.http_errors, ("404 /missing.png",))

    def test_a_stopped_state_that_left_loopback_is_still_refused(self) -> None:
        session = self.session(
            visible={"#menu": [False]},
            timing_reply=timing(["http://192.0.2.1/x.png", 200]),
        )
        with self.assertRaises(EngineError) as caught:
            self.run_state(session, self.ticking(_wait_for("#menu"), timeout_s=_SHORT))
        self.assertIn("the page left loopback", str(caught.exception))


class ClickTests(ActionCase):
    """`click` waits for its selector to be visible, then clicks, retrying a covered click."""

    def test_a_visible_element_is_found_and_clicked(self) -> None:
        self.run_state(job=self.ticking(_click("#go")))
        self.assertEqual(
            self.page_calls()[:3],
            [
                call("execute visible", "#go"),
                call("find_css", "#go"),
                call("click", ELEMENT),
            ],
        )

    def test_an_intercepted_click_is_retried_until_it_lands(self) -> None:
        session = self.session(clicks=[_INTERCEPTED, None])
        record = self.run_state(session, self.ticking(_click("#go")))
        clicks = [
            entry for entry in self.page_calls() if entry[0] in ("click", "sleep")
        ]
        self.assertEqual(
            clicks[:3],
            [call("click", ELEMENT), call("sleep", _POLL), call("click", ELEMENT)],
        )
        self.assertEqual(record.problems, ())

    def test_an_element_never_interactable_stops_the_state_at_the_timeout(self) -> None:
        stuck = webdriver_error("click", "element not interactable", "zero size")
        session = self.session(clicks=[stuck])
        record = self.run_state(session, self.ticking(_click("#go"), timeout_s=_SHORT))
        self.assert_stopped(record, 'action did not complete: click "#go"')
        self.assertEqual(self.page_calls().count(call("click", ELEMENT)), _LOOKS)

    def test_any_other_click_error_stops_the_state_at_once(self) -> None:
        stale = webdriver_error("click", "stale element reference", "gone")
        session = self.session(clicks=[stale])
        record = self.run_state(session, self.ticking(_click("#go")))
        self.assert_stopped(record, 'action did not complete: click "#go"')
        self.assertEqual(self.page_calls().count(call("click", ELEMENT)), 1)

    def test_a_click_waits_while_the_element_is_hidden(self) -> None:
        session = self.session(visible={"#go": [False, True]})
        self.run_state(session, self.ticking(_click("#go")))
        self.assertEqual(
            self.page_calls()[:4],
            [
                call("execute visible", "#go"),
                call("sleep", _POLL),
                call("execute visible", "#go"),
                call("find_css", "#go"),
            ],
        )

    def test_an_element_gone_before_its_click_is_looked_for_again(self) -> None:
        session = self.session(elements={"#go": [None, ELEMENT]})
        self.run_state(session, self.ticking(_click("#go")))
        found = [
            entry for entry in self.page_calls() if entry[0] in ("find_css", "click")
        ]
        self.assertEqual(
            found,
            [call("find_css", "#go"), call("find_css", "#go"), call("click", ELEMENT)],
        )

    def test_the_actions_run_in_order_and_the_first_failure_stops_the_rest(
        self,
    ) -> None:
        session = self.session(visible={"#a": [True], "#b": [False]})
        actions = (_click("#a"), _wait_for("#b"), _click("#c"))
        record = self.run_state(session, self.ticking(*actions, timeout_s=_SHORT))
        self.assert_stopped(record, 'action did not complete: wait_for "#b"')
        self.assertNotIn(call("execute visible", "#c"), self.log)


class WaitMsTests(ActionCase):
    """`wait_ms` waits in this process, in polls no longer than the settle poll."""

    def test_a_wait_sleeps_its_length_in_polls(self) -> None:
        self.run_state(job=self.ticking(_wait_ms(250)))
        sleeps = [entry for entry in self.page_calls() if entry[0] == "sleep"]
        self.assertEqual(
            sleeps, [call("sleep", _POLL), call("sleep", _POLL), call("sleep", 0.05)]
        )

    def test_a_wait_of_nothing_sleeps_not_at_all(self) -> None:
        self.run_state(job=self.ticking(_wait_ms(0)))
        self.assertNotIn("sleep", [entry[0] for entry in self.log])

    def test_an_action_no_targets_file_can_hold_is_refused_and_the_session_ended(
        self,
    ) -> None:
        # A wait with no length is a programming error, never the page's: no finding names
        # it, and the session it was handed to is still ended.
        malformed = Action(kind="wait_ms", selector=None, ms=None)
        with self.assertRaisesRegex(
            ValueError, "not an action a targets file can hold"
        ):
            self.run_state(job=self.ticking(malformed))
        self.assertEqual(self.calls()[-2:], [call("record_helpers"), call("delete")])


class SettleTests(ActionCase):
    """The probe is polled until the page has settled, or the timeout has passed."""

    def test_an_unsettled_page_is_probed_again_after_each_poll(self) -> None:
        session = self.session(probes=[_UNSETTLED, _UNSETTLED, probe_dict()])
        record = self.run_state(session, self.ticking())
        probes = [
            entry[0] for entry in self.page_calls() if entry[0] != "execute timing"
        ]
        self.assertEqual(
            probes[:6],
            [
                "execute probe",
                "sleep",
                "execute probe",
                "sleep",
                "execute probe",
                "execute probe",
            ],
        )
        self.assertEqual(record.problems, ())

    def test_a_page_that_never_settles_is_printed_at_the_timeout(self) -> None:
        session = self.session(probes=[_UNSETTLED])
        record = self.run_state(session, self.ticking(timeout_s=_SHORT))
        self.assertEqual(self.page_calls().count(call("sleep", _POLL)), _LOOKS - 1)
        self.assertEqual(len(record.pdfs), 1)
        self.assertIsNotNone(record.probe)

    def test_a_probe_that_fails_stops_the_state(self) -> None:
        broken = webdriver_error("execute", "javascript error", "probe threw")
        session = self.session(fail={"execute probe": broken})
        record = self.run_state(session, self.ticking())
        self.assert_stopped(
            record, "probe failed: execute: javascript error: probe threw"
        )

    def test_a_probe_result_the_parser_refuses_stops_the_state(self) -> None:
        session = self.session(probes=[{"title": "no other key"}])
        record = self.run_state(session, self.ticking())
        self.assertEqual(len(record.problems), 1)
        self.assertTrue(record.problems[0].what.startswith("probe failed: "))
        self.assertIsNone(record.probe)

    def test_the_probe_reads_what_the_target_asks(self) -> None:
        read = Read(name="title", selector="h1", kind="text", key=None, all=False)
        reading = target(reads=(read,), storage_reads=("mode",))
        self.run_state(job=self.job(the_target=reading))
        argument = {
            "reads": [
                {
                    "name": "title",
                    "selector": "h1",
                    "kind": "text",
                    "key": None,
                    "all": False,
                }
            ],
            "storageKeys": ["mode"],
            "maxText": 1000,
            "maxItems": 5000,
        }
        self.assertIn(call("execute probe", argument), self.log)


class PrintMomentTests(ActionCase):
    """`print_at_ms` is waited for after navigation began, or warned of when it had passed."""

    def test_the_print_waits_for_its_moment(self) -> None:
        record = self.run_state(job=self.ticking(print_at_ms=250))
        sleeps = [entry for entry in self.page_calls() if entry[0] == "sleep"]
        self.assertEqual(
            sleeps, [call("sleep", _POLL), call("sleep", _POLL), call("sleep", 0.05)]
        )
        self.assertEqual(record.printed_at_ms, 250)

    def test_a_moment_already_passed_is_warned_of(self) -> None:
        session = self.session(probes=[_UNSETTLED, _UNSETTLED, probe_dict()])
        record = self.run_state(session, self.ticking(print_at_ms=100))
        self.assertEqual(
            record.problems[0],
            Problem("warning", "printed at 200 ms, later than the 100 ms asked"),
        )
        self.assertEqual(len(record.pdfs), 1)

    def test_a_warning_found_before_a_stop_stands_beside_its_finding(self) -> None:
        session = self.session(
            probes=[_UNSETTLED, _UNSETTLED, probe_dict(), {"title": "refused"}]
        )
        record = self.run_state(session, self.ticking(print_at_ms=100))
        self.assertEqual(
            record.problems[0],
            Problem("warning", "printed at 200 ms, later than the 100 ms asked"),
        )
        self.assertTrue(record.problems[1].what.startswith("probe failed: "))

    def test_the_moment_is_counted_from_just_before_the_navigation(self) -> None:
        session = self.session(on_navigate=lambda url: self.tick.sleep(0.3))
        record = self.run_state(session, self.ticking(print_at_ms=500))
        self.assertEqual(record.load_ms, 300)
        self.assertEqual(record.printed_at_ms, 500)


class BreachDuringActionsTests(ActionCase):
    """A browser the non-local guard ended mid-action is reported as the breach it was."""

    def test_a_breach_that_ended_an_action_is_the_states_error(self) -> None:
        gone = webdriver_error("execute", "unknown error", "Browsing context discarded")
        session = self.session(
            fail={"execute visible": gone, "execute timing": gone, "delete": gone},
            on_navigate=lambda url: self.driver.write(
                "FATAL ERROR: Non-local network connections are disabled and a connection "
                "attempt to 192.0.2.7 (192.0.2.7) was made.\n"
            ),
        )
        with self.assertRaises(IsolationBreach) as caught:
            self.run_state(session, self.ticking(_wait_for("#menu")))
        self.assertIn("192.0.2.7", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
