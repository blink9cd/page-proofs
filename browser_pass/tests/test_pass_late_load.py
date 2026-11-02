"""Grouped suite: the late-script load's refusals and waits, driven through a scripted page.

The live suite proves this load against real engines. What a real engine will not do on demand
is fail or lag in a particular way (a font that never arrives, a held script that cannot be
replayed, frames that trail a settled layout), so those paths are driven here through a page
that answers the probe with scripted records and raises where a browser would
(`_late_fixtures`). Each refusal must end as the fault it names, and a page measured only once
it is presentable. So is the settle's wiring: layout-inert motion stilled before every read,
and the tally kept. The page offers no DevTools session, as an engine but Chromium offers none,
so the stilling runs through the probe alone; what it reads over one is `test_pass_inert.py`'s.
A navigation, a load or a plant that fails is `test_pass_late_faults.py`'s.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pass_late_load  # noqa: E402 - the tool's private src/, via the insert above
import pass_probe  # noqa: E402 - the tool's private src/, via the insert above
import pass_shift  # noqa: E402 - the tool's private src/, via the insert above
from _late_fixtures import (  # noqa: E402 - beside this file, after the insert
    NOT_YET_PRESENTABLE,
    PRESENTABLE,
    ROOMY_TIMEOUT,
    TIMEOUT,
    UNPAINTED_UNBLOCKED,
    FakePage,
    FakeRoute,
    measure,
)
from pass_motion import Motion  # noqa: E402 - the tool's private src/


class FontTimeoutTests(unittest.TestCase):
    """A page that painted but never finished its fonts cannot be measured honestly."""

    def test_fonts_that_never_load_are_a_fault_not_a_measurement(self) -> None:
        # Releasing here would charge the face that swaps in later to the scripts:
        # the very misattribution the fonts half of the hold exists to prevent.
        page = FakePage({"painted": True, "fontsLoaded": False, "blocked": False})
        outcome = measure(page)
        (fault,) = outcome.faults
        self.assertIn("fonts", fault)
        self.assertEqual(outcome.late.status, pass_shift.NOT_MEASURED)
        self.assertFalse(page.load_waited)

    def test_a_page_that_never_paints_is_left_to_the_record(self) -> None:
        # Not this fault's business: the read-back decides, since a held script that blocks
        # rendering makes an unpainted page measurable.
        page = FakePage({"painted": False, "fontsLoaded": True, "blocked": False})
        outcome = measure(page)
        self.assertNotIn("fonts", " ".join(outcome.faults))


class ReleaseFailureTests(unittest.TestCase):
    """A held script that cannot be replayed is a fault, not a crash."""

    def test_a_route_that_refuses_to_replay_is_a_fault(self) -> None:
        # Uncaught, this would leave the run with no report at all and exit 1, the code
        # that means a detector found something.
        outcome = measure(
            FakePage(
                {"painted": True, "fontsLoaded": True, "blocked": False},
                asks_for=FakeRoute("http://127.0.0.1:0/late.js", raises=True),
            )
        )
        (fault,) = outcome.faults
        self.assertIn("could not be released", fault)
        self.assertEqual(outcome.late.status, pass_shift.NOT_MEASURED)

    def test_a_held_script_is_replayed_when_the_page_is_still_there(self) -> None:
        held = FakeRoute("http://127.0.0.1:0/late.js")
        page = FakePage(
            {"painted": True, "fontsLoaded": True, "blocked": False}, asks_for=held
        )
        measure(page)
        self.assertTrue(held.continued)
        self.assertTrue(page.load_waited)


class SettleWindowTests(unittest.TestCase):
    """The late load settles over the window `pass_probe` holds when it runs."""

    def test_reads_the_settle_window_when_it_settles(self) -> None:
        # The live suites shorten the window through pass_probe alone; a copy taken at import
        # would keep them paying production's on every late load. A page that paints, has its
        # fonts and replays its held script reaches the settle, which is refused there.
        window = pass_probe.SETTLE_QUIET_SECONDS / 4
        page = FakePage(
            {"painted": True, "fontsLoaded": True, "blocked": False},
            asks_for=FakeRoute("http://127.0.0.1:0/late.js"),
        )
        with mock.patch.object(pass_probe, "SETTLE_QUIET_SECONDS", window):
            with mock.patch.object(
                pass_late_load, "wait_until_settled", return_value=False
            ) as settle:
                measure(page)
        self.assertEqual(settle.call_args.kwargs["quiet"], window)


class PresentableWaitTests(unittest.TestCase):
    """The record is read once the page is presentable, which its frames decide, not its load.

    A page is presentable once painted and then in its own fonts, and that moment is marked by
    frames. Under a loaded machine they can lag the page's main thread, which is what the layout
    settle watches. A read that comes first would report a page that never painted in its own
    fonts, about a page that did, a moment later.
    """

    def _blocked_page(self, *late_shifts: dict[str, object]) -> FakePage:
        """A page whose held script blocks its first paint, so the hold ends at once."""
        return FakePage(
            {"painted": False, "fontsLoaded": True, "blocked": True},
            asks_for=FakeRoute("http://127.0.0.1:0/swap.js"),
            late_shifts=late_shifts,
        )

    def test_a_page_presentable_only_after_its_layout_settles_is_measured(self) -> None:
        # The live failure, scripted: loaded, still, and not yet shown by a frame when first
        # read. Reading once refuses it; waiting for the frames measures it, at zero, since
        # its face swapped in before the page became presentable.
        page = self._blocked_page(NOT_YET_PRESENTABLE, PRESENTABLE)
        with mock.patch.object(pass_late_load, "wait_until_settled", return_value=True):
            outcome = measure(page, timeout=ROOMY_TIMEOUT)

        self.assertEqual(outcome.faults, ())
        self.assertEqual(outcome.late.status, pass_shift.MEASURED)
        self.assertEqual(outcome.late.score, 0.0)

    def test_a_page_never_presentable_within_the_timeout_is_refused_unsettled(
        self,
    ) -> None:
        # Refused as a page that did not become presentable in the time it had, which is all
        # a bounded wait can know, and before the settle, which could not change that.
        page = self._blocked_page(NOT_YET_PRESENTABLE)
        with mock.patch.object(
            pass_late_load, "wait_until_settled", return_value=True
        ) as settle:
            outcome = measure(page)

        (fault,) = outcome.faults
        self.assertIn(f"had not painted in its own fonts after {TIMEOUT:g}s", fault)
        self.assertEqual(outcome.late.status, pass_shift.NOT_MEASURED)
        settle.assert_not_called()

    def test_a_record_refused_whatever_comes_is_not_waited_on(self) -> None:
        # Nothing painted and nothing held blocking it: the record's own fault, at once. A
        # wait here would spend the bound and then misname the fault.
        page = FakePage(
            {"painted": False, "fontsLoaded": True, "blocked": False},
            late_shifts=(UNPAINTED_UNBLOCKED,),
        )
        with mock.patch.object(pass_late_load, "wait_until_settled", return_value=True):
            outcome = measure(page)

        (fault,) = outcome.faults
        self.assertIn("did not paint", fault)


def _settle_reading(times: int, *, settles: bool) -> Callable[..., bool]:
    """A settle that reads the page `times` times, then settles or runs out of time."""

    def settle(read: Callable[[], object], **_timing: object) -> bool:
        for _ in range(times):
            read()
        return settles

    return settle


class SettleMotionTests(unittest.TestCase):
    """The late settle stills layout-inert motion before each read, and keeps what it stilled.

    Only layout-inert motion: a seek of it moves no layout, so nothing is charged to the
    scripts, while any other animation is left to run and keeps a page from settling.
    """

    def _measure(
        self, polls: tuple[Motion, ...], *, settles: bool
    ) -> tuple[pass_late_load.LateOutcome, FakePage]:
        """The late load of a page whose settle reads once per poll in `polls`."""
        page = FakePage(
            {"painted": True, "fontsLoaded": True, "blocked": False},
            asks_for=FakeRoute("http://127.0.0.1:0/late.js"),
            motions=polls,
        )
        with mock.patch.object(
            pass_late_load,
            "wait_until_settled",
            _settle_reading(len(polls), settles=settles),
        ):
            return measure(page, timeout=ROOMY_TIMEOUT), page

    def test_each_read_stills_inert_motion_first_and_the_outcome_keeps_the_tally(
        self,
    ) -> None:
        polls = (Motion(1, 1, 1), Motion(0, 1, 1), Motion(0, 0, 1))
        outcome, page = self._measure(polls, settles=True)
        settle = [
            asked for asked in page.asked if asked[0] in ("stillMotion", "signature")
        ]
        self.assertEqual(
            settle, [("stillMotion", {"layoutInertOnly": True}), ("signature", {})] * 3
        )
        self.assertEqual(outcome.faults, ())
        self.assertEqual(outcome.late.status, pass_shift.MEASURED)
        # Stilled counts sum over the polls; what ran is the last poll's (`pass_motion.tally`).
        self.assertEqual(outcome.motion, Motion(finished=1, paused=2, running=1))

    def test_a_settle_that_runs_out_of_time_keeps_what_it_stilled(self) -> None:
        # The layout-moving animation left running is what kept the page from settling, and
        # the report says so beside the fault.
        outcome, _ = self._measure((Motion(0, 1, 1), Motion(0, 0, 1)), settles=False)
        (fault,) = outcome.faults
        self.assertIn("still changing", fault)
        self.assertEqual(outcome.motion, Motion(finished=0, paused=1, running=1))


if __name__ == "__main__":
    unittest.main()
