"""Grouped suite: how long a Firefox state may take, counted step by step from named constants.

A state runs inside one watchdog bound, so the bound must cover every step a healthy state
can take: each WebDriver call at its own bound, which is `TIMED_CALL_BOUND_FACTOR` times the
client's transport timeout, itself a named margin above the run's timeout; each wait and
poll; and the two process listings a session makes outside every bound of the driver's. Each
term case restates one term of the sum from the constants it is built on, so a term changed
in the sum fails a case. The count case runs the fullest state through the scripted session
and counts the WebDriver calls it makes, so a call the state runner adds or drops that the
budget does not count fails it, rather than shrinking the bound unseen.

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

import gecko_budget  # noqa: E402 - the tool's private src/, via the insert above
from _gecko_state_fixtures import StateCase, webdriver_error  # noqa: E402 - beside it
from _print_engine_fixtures import VIEWPORT, state, target  # noqa: E402 - as above
from gecko_launch import (  # noqa: E402 - the tool's private src/
    LISTING_OVERRUN_SECONDS,
    LISTING_TIMEOUT_SECONDS,
)
from print_engine_state import (  # noqa: E402 - as above
    SETTLE_POLL_MS,
    TIMED_CALL_BOUND_FACTOR,
)
from print_targets import Action  # noqa: E402 - as above

#: The timeout every case counts with.
_T = 5.0

#: Calls every state makes, whatever it holds: New Session and Delete Session; the window
#: set and the viewport read, then each once more to correct it; the page's navigation, the
#: last probe and the Resource Timing read.
_FIXED_CALLS = 9

#: Calls a state that stores anything adds: the seed page's navigation, its check, the store.
_STORAGE_CALLS = 3

#: Calls one look of a click makes: the visibility script, Find Element, Element Click.
_CLICK_CALLS = 3

#: Calls a `set_attribute` makes: its one script, which never looks again.
_SET_ATTRIBUTE_CALLS = 1

#: A `set_attribute` on the root, as the budget counts it.
_SET_ATTRIBUTE = Action("set_attribute", ":root", None, ("data-x", ""))


def _call() -> float:
    """One WebDriver call's bound under `_T`."""
    return TIMED_CALL_BOUND_FACTOR * (_T + gecko_budget.TRANSPORT_MARGIN_SECONDS)


def _poll() -> float:
    """The page poll, in seconds."""
    return SETTLE_POLL_MS / 1000


def _plain(prints: int = 1) -> float:
    """A state holding nothing, printing `prints` PDFs: calls, settling, listings, slack."""
    settle = _T + _poll() + _call()
    listings = 2 * (LISTING_TIMEOUT_SECONDS + LISTING_OVERRUN_SECONDS)
    return (_FIXED_CALLS + prints) * _call() + settle + listings + _T


class TermTests(unittest.TestCase):
    """Each named term, from the constants it is derived from."""

    def test_the_transport_timeout_is_a_margin_above_the_runs(self) -> None:
        self.assertGreater(gecko_budget.TRANSPORT_MARGIN_SECONDS, 0)
        self.assertEqual(
            gecko_budget.client_timeout(_T), _T + gecko_budget.TRANSPORT_MARGIN_SECONDS
        )

    def test_a_call_is_bounded_at_the_clients_factor_of_its_timeout(self) -> None:
        self.assertEqual(gecko_budget.call_seconds(_T), _call())

    def test_the_page_poll_is_the_chromium_engines(self) -> None:
        self.assertEqual(gecko_budget.PAGE_POLL_SECONDS, _poll())

    def test_a_listing_is_its_timeout_and_its_overrun(self) -> None:
        self.assertEqual(
            gecko_budget.LISTING_SECONDS,
            LISTING_TIMEOUT_SECONDS + LISTING_OVERRUN_SECONDS,
        )


class StateTests(unittest.TestCase):
    """A state's bound: the plain sum, and what each thing a state holds adds to it."""

    def test_a_plain_state_counts_its_calls_settling_both_listings_and_slack(
        self,
    ) -> None:
        self.assertAlmostEqual(
            gecko_budget.state_seconds(state(), timeout_s=_T, prints=1), _plain()
        )

    def test_each_print_adds_a_call(self) -> None:
        self.assertAlmostEqual(
            gecko_budget.state_seconds(state(), timeout_s=_T, prints=2),
            _plain(prints=2),
        )

    def test_storage_adds_the_seed_pages_calls(self) -> None:
        stored = state(storage=(("a", "1"), ("b", "2")))
        self.assertAlmostEqual(
            gecko_budget.state_seconds(stored, timeout_s=_T, prints=1),
            _plain() + _STORAGE_CALLS * _call(),
        )

    def test_each_action_adds_its_wait(self) -> None:
        cases = (
            (Action(kind="wait_for", selector="#a", ms=None), _T + _poll() + _call()),
            (
                Action(kind="click", selector="#a", ms=None),
                _T + _poll() + _CLICK_CALLS * _call(),
            ),
            (Action(kind="wait_ms", selector=None, ms=1500), 1.5 + _poll()),
            # One script, bounded as a call, with no wait window around it.
            (_SET_ATTRIBUTE, _SET_ATTRIBUTE_CALLS * _call()),
        )
        for action, added in cases:
            with self.subTest(kind=action.kind):
                acting = state(actions=(action, action))
                self.assertAlmostEqual(
                    gecko_budget.state_seconds(acting, timeout_s=_T, prints=1),
                    _plain() + 2 * added,
                )

    def test_a_print_moment_adds_its_wait(self) -> None:
        timed = state(print_at_ms=2500)
        self.assertAlmostEqual(
            gecko_budget.state_seconds(timed, timeout_s=_T, prints=1),
            _plain() + 2.5 + _poll(),
        )

    def test_the_bound_grows_with_the_timeout(self) -> None:
        short = gecko_budget.state_seconds(state(), timeout_s=1.0, prints=1)
        self.assertLess(
            short, gecko_budget.state_seconds(state(), timeout_s=_T, prints=1)
        )


#: The names a scripted session and client log a WebDriver call under; the rest of the log
#: is the watchdog's, the sleeps, and the driver's listings.
_WEBDRIVER_CALLS = frozenset(
    {
        "new_session",
        "set_window_size",
        "navigate",
        "execute viewport",
        "execute seed",
        "execute store",
        "execute visible",
        "execute probe",
        "execute timing",
        "find_css",
        "click",
        "print",
        "delete",
    }
)


class CallCountTests(StateCase):
    """The calls the budget counts are the calls a state makes."""

    def test_the_fullest_state_makes_the_calls_the_budget_counts(self) -> None:
        click = Action(kind="click", selector="#go", ms=None)
        fullest = state(
            storage=(("theme", "dark"),),
            actions=(
                Action(kind="wait_for", selector="#menu", ms=None),
                click,
                Action(kind="wait_ms", selector=None, ms=100),
            ),
            print_at_ms=1,
        )
        refused = webdriver_error("click", "element not interactable", "zero size")
        asked = [VIEWPORT.width, VIEWPORT.height]
        session = self.session(
            viewports=[[VIEWPORT.width, VIEWPORT.height - 124], asked],
            clicks=[refused, None],
        )
        job = self.job(the_target=target(states=(fullest,)), backgrounds="both")
        record = self.run_state(session, job)
        self.assertEqual(len(record.pdfs), 2)
        made = [entry for entry in self.log if entry[0] in _WEBDRIVER_CALLS]
        # The click's one retry is a second look, made inside the click's own wait.
        budgeted = gecko_budget.state_calls(fullest, prints=2)
        self.assertEqual(len(made), budgeted + gecko_budget.look_calls(click))

    def test_each_action_counts_one_look(self) -> None:
        looks = {
            Action(kind="click", selector="#a", ms=None): _CLICK_CALLS,
            Action(kind="wait_for", selector="#a", ms=None): 1,
            Action(kind="wait_ms", selector=None, ms=1500): 0,
            _SET_ATTRIBUTE: _SET_ATTRIBUTE_CALLS,
        }
        for action, calls in looks.items():
            with self.subTest(kind=action.kind):
                self.assertEqual(gecko_budget.look_calls(action), calls)

    def test_a_set_attribute_makes_the_one_call_the_budget_counts(self) -> None:
        setting = state(actions=(_SET_ATTRIBUTE,))
        # A first viewport off by the window's chrome, so the correcting calls are made too.
        asked = [VIEWPORT.width, VIEWPORT.height]
        session = self.session(
            viewports=[[VIEWPORT.width, VIEWPORT.height - 124], asked]
        )
        job = self.job(the_target=target(states=(setting,)))
        record = self.run_state(session, job)
        self.assertEqual(len(record.pdfs), 1)
        counted = _WEBDRIVER_CALLS | {"execute set_attribute"}
        made = [entry for entry in self.log if entry[0] in counted]
        self.assertEqual(len(made), gecko_budget.state_calls(setting, prints=1))


if __name__ == "__main__":
    unittest.main()
