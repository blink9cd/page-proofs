"""Suite: one state from opening to print -- actions, settle, the print moment, PDFs, problems.

Each case runs the engine over one target against the fake Playwright and reads the record it
returns, or the log of its calls, where every Playwright call sits inside a watchdog bound.
The clock moves one second per reading and reads zero as navigation begins, so a state that
settles at once is loaded at 1000 ms and printed at 2000 ms; each case that depends on more
readings says how many it takes. Finding texts are compared whole, since a
later live suite asserts on them. What the page asks the network for is the sibling
`test_print_engine_network.py`'s.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_engine  # noqa: E402 - the tool's private src/, via the insert above
import print_judge  # noqa: E402 - as above
import print_probe  # noqa: E402 - as above
import print_targets  # noqa: E402 - as above
from _print_engine_fixtures import (  # noqa: E402 - beside this module
    BASE_URL,
    TIMEOUT_MS,
    TIMEOUT_S,
    FakeRun,
    StateTestCase,
    engine_settings,
    state,
    target,
    targets_file,
    timed_bound,
)
from _print_playwright_fixtures import (  # noqa: E402 - as above
    FakeScript,
    call,
    image_dict,
    probe_dict,
)
from print_targets import Action, Read  # noqa: E402 - the tool's private src/

#: The margin each Chromium PDF records it was printed with: the one `page.pdf` is handed.
_MARGIN = print_engine.MARGIN_INCHES

_Problem = print_engine.Problem

#: A probe that has not settled: an image still loading, and fonts not yet in.
_LOADING = probe_dict(
    fontsStatus="loading",
    images=[image_dict("/late.png", complete=False, natural_width=0)],
)


def _finding(what: str) -> print_engine.Problem:
    return _Problem("finding", what)


def _warning(what: str) -> print_engine.Problem:
    return _Problem("warning", what)


#: The default state, as each watchdog bound in it is named after its call.
_WHERE = "home/default"

#: The name the watchdog's bound on a wait carries in the default state.
_WAIT_BOUND = f"page.wait_for_timeout {_WHERE}"

#: The page events the engine listens to, in the order it adds the listeners.
_LISTENED = ("pageerror", "requestfailed", "response")


def _wait_bound(waited_s: float) -> tuple[tuple[str, ...], ...]:
    """A wait's bound as the log shows it around the wait: the wait plus the timeout."""
    entered = call("bound", _WAIT_BOUND, TIMEOUT_S + waited_s)
    return entered, call("unbound", _WAIT_BOUND)


def _bounded(name: str, *calls: tuple[str, ...]) -> list[tuple[str, ...]]:
    """`calls` inside one bound at the timeout, named `name` in the default state."""
    what = f"{name} {_WHERE}"
    return [call("bound", what, TIMEOUT_S), *calls, call("unbound", what)]


class OpenAndLoadTests(StateTestCase):
    """The page is opened and loaded with each call inside a watchdog bound of its own."""

    def test_each_call_opening_the_page_is_bounded_under_its_own_name(self) -> None:
        run, _ = self.run_one()
        opening = [
            *_bounded(
                "context.set_default_timeout",
                call("context.set_default_timeout", TIMEOUT_MS),
            ),
            *_bounded("context.route", call("context.route")),
            *_bounded("context.route_web_socket", call("context.route_web_socket")),
            *_bounded("context.new_page", call("context.new_page")),
            *_bounded(
                "page.on",
                *(call("page.on", event) for event in _LISTENED),
            ),
        ]
        start = run.calls.index(opening[0])
        self.assertEqual(run.calls[start : start + len(opening)], opening)

    def test_the_load_is_bounded_at_twice_its_timeout(self) -> None:
        run, _ = self.run_one()
        loaded = call(
            "page.goto", BASE_URL + "/", wait_until="load", timeout=TIMEOUT_MS
        )
        self.assertEqual(run.around(loaded), timed_bound(f"page.goto {_WHERE}"))


class BackgroundsTests(StateTestCase):
    """Backgrounds off, on, or both from ONE load of the page."""

    def run_backgrounds(
        self, backgrounds: str
    ) -> tuple[FakeRun, print_engine.StateRecord]:
        run = FakeRun(self.pdf_dir)
        (record,) = run.run(targets_file(backgrounds=backgrounds))
        return run, record

    def test_both_prints_off_then_on_from_one_load(self) -> None:
        run, record = self.run_backgrounds("both")
        self.assertEqual(len(run.named("page.goto")), 1)
        self.assertEqual(
            record.pdfs,
            (
                print_engine.MadePdf(
                    False, self.pdf_dir / "home--default--bg-off.pdf", _MARGIN
                ),
                print_engine.MadePdf(
                    True, self.pdf_dir / "home--default--bg-on.pdf", _MARGIN
                ),
            ),
        )
        (page,) = run.pages()
        self.assertEqual(
            page.pdf_calls,
            [
                {
                    "path": pdf.path,
                    **print_engine.print_options(
                        backgrounds=pdf.backgrounds, header_footer=False
                    ),
                }
                for pdf in record.pdfs
            ],
        )
        # The print moment is taken once, at the first PDF.
        self.assertEqual(record.printed_at_ms, 2000)

    def test_on_prints_one_pdf_with_backgrounds(self) -> None:
        _, record = self.run_backgrounds("on")
        self.assertEqual(
            record.pdfs,
            (
                print_engine.MadePdf(
                    True, self.pdf_dir / "home--default--bg-on.pdf", _MARGIN
                ),
            ),
        )

    def test_the_header_and_footer_as_asked(self) -> None:
        run, _ = self.run_states(state(), header_footer=True)
        (page,) = run.pages()
        self.assertIs(page.pdf_calls[0]["display_header_footer"], True)


class SettleTests(StateTestCase):
    """The probe is polled until the page settles, or the timeout passes."""

    def test_polls_until_settled_then_reads_back_once_more(self) -> None:
        final = probe_dict(title="final")
        script = FakeScript(probes=[_LOADING, _LOADING, probe_dict(), final])
        run, record = self.run_one(script=script)
        self.assertEqual(len(run.named("page.evaluate")), 4)
        self.assertEqual(
            run.named("page.wait_for_timeout"),
            [call("page.wait_for_timeout", print_engine.SETTLE_POLL_MS)] * 2,
        )
        self.assertEqual(record.probe, print_probe.parse_probe_result(final))
        self.assertEqual(record.problems, ())
        # Readings: 0 navigation, 1 loaded, 2 and 3 in the loop, 4 at the print.
        self.assertEqual(record.printed_at_ms, 4000)

    def test_each_poll_wait_is_bounded_at_itself_plus_the_timeout(self) -> None:
        run, _ = self.run_one(script=FakeScript(probes=[_LOADING, probe_dict()]))
        waited = call("page.wait_for_timeout", print_engine.SETTLE_POLL_MS)
        self.assertEqual(run.around(waited), _wait_bound(0.1))
        self.assertEqual(run.unbounded(), [])

    def test_a_page_that_never_settles_is_still_printed_with_findings(self) -> None:
        run, record = self.run_one(script=FakeScript(probes=[_LOADING]))
        # Readings 2 to 5 are within the 5 s timeout, each followed by a poll; 6 is past it.
        self.assertEqual(len(run.named("page.wait_for_timeout")), 4)
        self.assertEqual(len(record.pdfs), 1)
        self.assertEqual(
            record.problems,
            (
                _finding("image not complete at print: /late.png"),
                _warning("fonts not loaded at print: loading"),
            ),
        )

    def test_the_read_back_is_the_targets_reads_and_storage(self) -> None:
        read = Read("mode attribute", ":root", "attribute", "data-mode", False)
        run = FakeRun(self.pdf_dir)
        the_target = target(reads=(read,), storage_reads=("mode",))
        run.run(targets_file(the_target))
        (page,) = run.pages()
        expected = print_probe.probe_argument((read,), ("mode",))
        self.assertEqual(page.evaluate_args, [expected, expected])


class PrintMomentTests(StateTestCase):
    """`print_at_ms`: wait out the rest of it, or warn that it had passed."""

    def test_waits_the_remainder(self) -> None:
        # Reading 2 is the check, 2000 ms after navigation: 1400 ms are left.
        run, record = self.run_one(state(print_at_ms=3400))
        self.assertEqual(
            run.named("page.wait_for_timeout"), [call("page.wait_for_timeout", 1400)]
        )
        self.assertEqual(record.problems, ())
        self.assertEqual(record.printed_at_ms, 3000)

    def test_the_wait_is_bounded_at_itself_plus_the_timeout(self) -> None:
        run, _ = self.run_one(state(print_at_ms=3400))
        waited = call("page.wait_for_timeout", 1400)
        self.assertEqual(run.around(waited), _wait_bound(1.4))

    def test_a_late_warning_survives_a_failed_read_back(self) -> None:
        script = FakeScript(
            probes=[probe_dict(), RuntimeError("Execution context was destroyed")]
        )
        _, record = self.run_one(state(print_at_ms=1500), script)
        self.assertEqual(
            record.problems,
            (
                _warning("printed at 2000 ms, later than the 1500 ms asked"),
                _finding("probe failed: Execution context was destroyed"),
            ),
        )
        self.assertEqual((record.pdfs, record.probe), ((), None))

    def test_warns_when_the_moment_had_passed(self) -> None:
        run, record = self.run_one(state(print_at_ms=1500))
        self.assertEqual(run.named("page.wait_for_timeout"), [])
        self.assertEqual(
            record.problems,
            (_warning("printed at 2000 ms, later than the 1500 ms asked"),),
        )

    def test_neither_waits_nor_warns_on_the_moment_itself(self) -> None:
        run, record = self.run_one(state(print_at_ms=2000))
        self.assertEqual(run.named("page.wait_for_timeout"), [])
        self.assertEqual(record.problems, ())

    def test_the_longest_waits_a_file_holds_keep_bounds_the_watchdog_keeps(
        self,
    ) -> None:
        # A wait's bound is its length plus the timeout. At the longest timeout the engine
        # takes, half what a lock can wait, a day's wait still leaves the bound inside it,
        # which the watchdog's stand-in checks as the watchdog does.
        longest = print_targets.MAX_MS
        waits = state(actions=(Action("wait_ms", None, longest),), print_at_ms=longest)
        settings = engine_settings(self.pdf_dir, timeout_s=threading.TIMEOUT_MAX / 2)
        (record,) = FakeRun(self.pdf_dir).run(
            targets_file(target(states=(waits,))), settings
        )
        self.assertEqual((record.problems, len(record.pdfs)), ((), 1))

    def test_a_wait_the_watchdog_could_not_keep_is_refused_as_it_would_be(self) -> None:
        # No targets file holds such a moment; a bound past what a lock can wait is the
        # engine's programming error, raised whole, never a finding.
        run = FakeRun(self.pdf_dir)
        with self.assertRaises(ValueError):
            run.run(targets_file(target(states=(state(print_at_ms=10**15),))))
        self.assertEqual(run.named("page.wait_for_timeout"), [])


class ActionTests(StateTestCase):
    """Actions run in order; one that does not complete stops its state with a finding."""

    def test_each_kind_in_order_with_the_timeout(self) -> None:
        actions = (
            Action("click", "#start", None),
            Action("wait_for", "#grid", None),
            Action("wait_ms", None, 250),
        )
        run, record = self.run_one(state(actions=actions))
        acted = [
            entry
            for entry in run.calls
            if entry[0]
            in {"page.click", "page.wait_for_selector", "page.wait_for_timeout"}
        ]
        self.assertEqual(
            acted,
            [
                call("page.click", "#start", timeout=TIMEOUT_MS),
                call("page.wait_for_selector", "#grid", timeout=TIMEOUT_MS),
                call("page.wait_for_timeout", 250),
            ],
        )
        self.assertEqual(record.actions, actions)
        self.assertEqual(record.problems, ())

    def test_a_failed_click_stops_the_state_and_the_next_one_runs(self) -> None:
        stopped = state("after-start", actions=(Action("click", "#start", None),))
        script = FakeScript(failing_selectors=frozenset({"#start"}))
        run, (first, second) = self.run_states(stopped, state(), script=script)
        self.assertEqual(
            first.problems, (_finding('action did not complete: click "#start"'),)
        )
        self.assertEqual(
            (first.pdfs, first.probe, first.printed_at_ms), ((), None, None)
        )
        self.assertEqual(first.load_ms, 1000)
        self.assertEqual(len(second.pdfs), 1)
        # Only the second state was probed: twice, to settle and to read back.
        self.assertEqual(len(run.named("page.evaluate")), 2)

    def test_a_click_and_a_wait_for_are_each_bounded_at_twice_the_timeout(
        self,
    ) -> None:
        actions = (Action("click", "#start", None), Action("wait_for", "#grid", None))
        run, _ = self.run_one(state(actions=actions))
        clicked = call("page.click", "#start", timeout=TIMEOUT_MS)
        waited = call("page.wait_for_selector", "#grid", timeout=TIMEOUT_MS)
        self.assertEqual(run.around(clicked), timed_bound(f"page.click {_WHERE}"))
        self.assertEqual(
            run.around(waited), timed_bound(f"page.wait_for_selector {_WHERE}")
        )

    def test_a_failed_click_is_bounded_and_still_a_finding(self) -> None:
        script = FakeScript(failing_selectors=frozenset({"#start"}))
        run, record = self.run_one(
            state(actions=(Action("click", "#start", None),)), script
        )
        clicked = call("page.click", "#start", timeout=TIMEOUT_MS)
        self.assertEqual(run.around(clicked), timed_bound(f"page.click {_WHERE}"))
        self.assertEqual(
            record.problems, (_finding('action did not complete: click "#start"'),)
        )

    def test_the_actions_after_a_failed_one_do_not_run(self) -> None:
        actions = (Action("wait_for", "#grid", None), Action("click", "#start", None))
        script = FakeScript(failing_selectors=frozenset({"#grid"}))
        run, record = self.run_one(state(actions=actions), script)
        self.assertEqual(run.named("page.click"), [])
        self.assertEqual(
            record.problems, (_finding('action did not complete: wait_for "#grid"'),)
        )

    def test_a_wait_is_bounded_at_itself_plus_the_timeout(self) -> None:
        run, _ = self.run_one(state(actions=(Action("wait_ms", None, 250),)))
        self.assertEqual(
            run.around(call("page.wait_for_timeout", 250)), _wait_bound(0.25)
        )

    def test_a_failed_wait_names_its_time(self) -> None:
        script = FakeScript(failing_waits=frozenset({250}))
        _, record = self.run_one(state(actions=(Action("wait_ms", None, 250),)), script)
        self.assertEqual(
            record.problems, (_finding('action did not complete: wait_ms "250"'),)
        )

    def test_a_malformed_action_is_a_programming_error_not_a_finding(self) -> None:
        run = FakeRun(self.pdf_dir)
        broken = state(actions=(Action("click", None, None),))
        with self.assertRaises(ValueError):
            run.run(targets_file(target(states=(broken,))))
        self.assertEqual(run.named("page.click"), [])
        self.assertEqual(len(run.named("context.close")), 1)
        self.assertEqual(len(run.named("browser.close")), 1)


class ProbeFailureTests(StateTestCase):
    """A probe that raises stops its state with a finding; the next state still runs."""

    def test_while_settling(self) -> None:
        error = RuntimeError("SyntaxError: ':::' is not a valid selector\n    at probe")
        script = FakeScript(probes=[error, probe_dict()])
        run, (first, second) = self.run_states(state("a"), state("b"), script=script)
        self.assertEqual(
            first.problems,
            (_finding("probe failed: SyntaxError: ':::' is not a valid selector"),),
        )
        self.assertEqual(
            (first.pdfs, first.probe, first.printed_at_ms), ((), None, None)
        )
        self.assertEqual(len(second.pdfs), 1)
        self.assertEqual(run.unbounded(), [])

    def test_at_the_read_back(self) -> None:
        script = FakeScript(
            probes=[probe_dict(), RuntimeError("Execution context was destroyed")]
        )
        run, record = self.run_one(script=script)
        self.assertEqual(
            record.problems,
            (_finding("probe failed: Execution context was destroyed"),),
        )
        self.assertEqual(record.pdfs, ())
        self.assertEqual(run.named("page.pdf"), [])


class ReadBackProblemTests(StateTestCase):
    """What the read-back shows: broken images, running animations, the color scheme."""

    def test_a_broken_image(self) -> None:
        probe = probe_dict(
            images=[image_dict("/broken.png", natural_width=0), image_dict("/ok.png")]
        )
        _, record = self.run_one(script=FakeScript(probes=[probe]))
        self.assertEqual(record.problems, (_finding("image broken: /broken.png"),))

    def test_a_carousel_slide_with_no_src_is_never_requested_not_broken(self) -> None:
        # Read through the parse, the settle and the judge as a page hands it back: a slide
        # left without a source is neither broken nor waited for, beside one that is.
        unrequested = image_dict("", has_src=False, natural_width=0)
        probe = probe_dict(
            images=[unrequested, image_dict("/broken.png", natural_width=0)]
        )
        _, record = self.run_one(script=FakeScript(probes=[probe]))
        self.assertEqual(record.problems, (_finding("image broken: /broken.png"),))

    def test_running_animations_are_a_warning(self) -> None:
        animations = [
            {"name": "spin", "target": "div.spinner"},
            {"name": "fade", "target": "p"},
        ]
        _, record = self.run_one(
            script=FakeScript(probes=[probe_dict(animations=animations)])
        )
        self.assertEqual(
            record.problems,
            (_warning(print_judge.running_animations_warning(("spin", "fade"))),),
        )

    def test_emulated_dark_carries_its_warning(self) -> None:
        _, record = self.run_one(state("dark", color_scheme="dark"))
        self.assertTrue(record.color_scheme_emulated)
        self.assertEqual(
            record.problems, (_warning(print_engine.EMULATED_DARK_WARNING),)
        )

    def test_emulated_light_is_flagged_but_not_warned(self) -> None:
        _, record = self.run_one(state("light", color_scheme="light"))
        self.assertTrue(record.color_scheme_emulated)
        self.assertEqual(record.problems, ())

    def test_native_dark_reading_light_is_a_finding(self) -> None:
        _, record = self.run_one(state("dark", color_scheme="native-dark"))
        self.assertFalse(record.color_scheme_emulated)
        self.assertEqual(
            record.problems,
            (_finding("native dark not honored: the page reads light"),),
        )

    def test_reduced_motion_is_recorded_as_emulated(self) -> None:
        _, record = self.run_one(state("still", reduced_motion="reduce"))
        self.assertEqual(
            (record.reduced_motion, record.reduced_motion_emulated), ("reduce", True)
        )


class StorageTests(StateTestCase):
    """Stored values go in through the context, and are recorded as set."""

    def test_storage_state(self) -> None:
        stored = state("stored-dark", storage=(("mode", "dark"),))
        run, record = self.run_one(stored)
        self.assertEqual(record.storage, (("mode", "dark"),))
        (context,) = run.playwright.chromium.browsers[0].contexts
        self.assertEqual(
            context.options["storage_state"],
            {
                "cookies": [],
                "origins": [
                    {
                        "origin": BASE_URL,
                        "localStorage": [{"name": "mode", "value": "dark"}],
                    }
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
