"""Grouped suite: the late-script load's failures at the browser, driven through a scripted page.

A real engine will not fail its navigation, its wait for `load`, or a planted shift on demand,
so the live suites are the only other thing that reaches these paths, and on a runner with no
engine build they skip. Here the scripted page of `_late_fixtures` raises or refuses where a
browser would, and each failure must end as the fault or verdict it names, with nothing after
it run. The hold that keeps scripts back until the first paint is checked on its own as well:
only scripts are held, and only until the release.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pass_late_load  # noqa: E402 - the tool's private src/, via the insert above
import pass_shift  # noqa: E402 - the tool's private src/, via the insert above
from _late_fixtures import (  # noqa: E402 - beside this file, after the insert
    ROOMY_TIMEOUT,
    FakePage,
    FakeRoute,
    measure,
)
from playwright.sync_api import Error as PlaywrightError  # noqa: E402 - after the insert


class _FailingPage(FakePage):
    """A scripted page whose navigation, or whose wait for `load`, fails as a browser's can."""

    def __init__(self, *, goto_fails: bool = False, load_fails: bool = False) -> None:
        super().__init__({"painted": True, "fontsLoaded": True, "blocked": False})
        self._goto_fails = goto_fails
        self._load_fails = load_fails

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
        """Fail the navigation with Playwright's own error, first line first."""
        if self._goto_fails:
            raise PlaywrightError("net::ERR_CONNECTION_REFUSED at /\nCall log: ...")

    def wait_for_load_state(self, state: str, *, timeout: float) -> None:
        """Fail the wait for `load` with Playwright's own error, first line first."""
        if self._load_fails:
            raise PlaywrightError("Timeout 50ms exceeded.\nCall log: ...")
        super().wait_for_load_state(state, timeout=timeout)


class LoadFailureTests(unittest.TestCase):
    """A navigation or a load that fails is the load's fault, named, never a crash."""

    def test_a_navigation_that_fails_is_a_fault_naming_its_first_line(self) -> None:
        page = _FailingPage(goto_fails=True)
        outcome = measure(page)
        self.assertEqual(
            outcome.faults,
            ("late_shift: the page did not load: net::ERR_CONNECTION_REFUSED at /",),
        )
        self.assertEqual(outcome.late.status, pass_shift.NOT_MEASURED)
        # Nothing after the navigation runs: no hold is read, no load waited for.
        self.assertEqual(page.asked, [])
        self.assertFalse(page.load_waited)

    def test_a_load_that_never_finishes_is_a_fault_before_any_settle(self) -> None:
        page = _FailingPage(load_fails=True)
        with mock.patch.object(pass_late_load, "wait_until_settled") as settle:
            outcome = measure(page)
        self.assertEqual(
            outcome.faults,
            ("late_shift: the page did not finish loading: Timeout 50ms exceeded.",),
        )
        self.assertEqual(outcome.late.status, pass_shift.NOT_MEASURED)
        settle.assert_not_called()


def _route(url: str, resource_type: str) -> mock.Mock:
    """A request's route of `resource_type`, recording whether it was passed on."""
    route = mock.Mock()
    route.request.url = url
    route.request.resource_type = resource_type
    return route


class ScriptHoldTests(unittest.TestCase):
    """Only scripts are held, and only until the release; everything else goes straight on."""

    def test_a_non_script_request_passes_through_while_scripts_are_held(self) -> None:
        hold = pass_late_load.ScriptHold()
        script = _route("http://127.0.0.1:0/late.js", "script")
        sheet = _route("http://127.0.0.1:0/site.css", "stylesheet")
        hold.route(script)
        hold.route(sheet)
        sheet.continue_.assert_called_once_with()
        script.continue_.assert_not_called()
        self.assertEqual(hold.release(), ["http://127.0.0.1:0/late.js"])
        script.continue_.assert_called_once_with()

    def test_a_script_asked_for_after_the_release_is_not_held(self) -> None:
        hold = pass_late_load.ScriptHold()
        self.assertEqual(hold.release(), [])
        late = _route("http://127.0.0.1:0/later.js", "script")
        hold.route(late)
        late.continue_.assert_called_once_with()
        self.assertEqual(hold.release(), [])


class _UnplantablePage(FakePage):
    """A presentable page whose planted shift never takes."""

    def evaluate(self, source: str, argument: list[object]) -> object:
        """Refuse the plant; answer everything else as the scripted page does."""
        if argument[0] == "plantShift":
            self.asked.append(("plantShift", {}))
            return False
        return super().evaluate(source, argument)


class UnplantedProofTests(unittest.TestCase):
    """A plant that does not take is `not planted`, and nothing is waited on for it."""

    def test_a_plant_that_does_not_take_is_not_planted(self) -> None:
        page = _UnplantablePage(
            {"painted": False, "fontsLoaded": True, "blocked": True},
            asks_for=FakeRoute("http://127.0.0.1:0/swap.js"),
        )
        with mock.patch.object(pass_late_load, "wait_until_settled", return_value=True):
            outcome = measure(page, timeout=ROOMY_TIMEOUT, prove=True)
        self.assertEqual(outcome.faults, ())
        self.assertEqual(outcome.late.status, pass_shift.MEASURED)
        self.assertEqual(outcome.verdict, "not planted")
        # Refused at the plant: its movement is never asked about.
        self.assertNotIn("plantMoved", [operation for operation, _ in page.asked])


if __name__ == "__main__":
    unittest.main()
