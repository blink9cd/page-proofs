"""Suite: a browser failing outside the probe: an EngineError naming where; every browser closes.

A browser, context or page that fails on any call but the probe's means the run could not
measure honestly: it stops with an `EngineError` (the tool's exit 2) naming the state it was
in and the first line of what failed, never a raw error, which would leave the process with
exit 1, the code for a finding. A probe that raises is its state's finding instead, which is
the sibling state suite's. Every context opened and every browser launched still closes.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_engine  # noqa: E402 - the tool's private src/, via the insert above
from _print_engine_fixtures import (  # noqa: E402 - beside this module
    FakeRun,
    StateTestCase,
    state,
    target,
    targets_file,
)
from _print_playwright_fixtures import (  # noqa: E402 - as above
    CHROME_VERSION,
    SHELL_VERSION,
    FakeScript,
    call,
    image_dict,
    probe_dict,
)
from print_targets import State  # noqa: E402 - the tool's private src/

#: What Playwright raises on a call to a browser that has gone: the fake's failing wait too.
_GONE = "Target page, context or browser has been closed"

#: The same, as Playwright words it: the first line, then a call log no report should carry.
_GONE_WITH_LOG = _GONE + "\nCall log:\n  - waiting for the browser"

#: A probe that never settles, so the settle loop waits between polls.
_LOADING = probe_dict(images=[image_dict("/late.png", complete=False, natural_width=0)])


class FailureTestCase(StateTestCase):
    """Run one target's states until the engine raises, and keep what it raised."""

    def raised_by(
        self, script: FakeScript, *states: State
    ) -> tuple[FakeRun, print_engine.EngineError]:
        run = FakeRun(self.pdf_dir, script)
        with self.assertRaises(print_engine.EngineError) as caught:
            run.run(targets_file(target(states=states)))
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertEqual(run.unbounded(), [])
        return run, caught.exception


class PageFailureTests(FailureTestCase):
    """A page call that fails stops the run, and names the state it stopped in."""

    def test_a_failed_settle_wait(self) -> None:
        script = FakeScript(
            probes=[_LOADING],
            failing_waits=frozenset({print_engine.SETTLE_POLL_MS}),
        )
        run, error = self.raised_by(script, state("a"), state("b"))
        self.assertEqual(str(error), f"home/a: {_GONE}")
        # The run stops there: the next state is never loaded, and nothing was printed.
        self.assertEqual(len(run.named("page.goto")), 1)
        self.assertEqual(run.named("page.pdf"), [])
        self.assertEqual(run.named("context.close"), [call("context.close")])
        self.assertEqual(
            run.named("browser.close"), [call("browser.close", SHELL_VERSION)]
        )

    def test_a_failed_wait_for_the_print_moment(self) -> None:
        # Reading 2 is the check, 2000 ms after navigation: the 1400 ms left are waited.
        script = FakeScript(failing_waits=frozenset({1400}))
        run, error = self.raised_by(script, state(print_at_ms=3400))
        self.assertEqual(str(error), f"home/default: {_GONE}")
        self.assertEqual(run.named("page.pdf"), [])
        self.assertEqual(len(run.named("context.close")), 1)

    def test_a_failed_print_passes_as_its_own_engine_error(self) -> None:
        script = FakeScript(pdf_error="Printing failed\nprotocol error")
        _, error = self.raised_by(script, state())
        self.assertEqual(str(error), "home/default: page.pdf failed: Printing failed")


class ContextFailureTests(FailureTestCase):
    """Opening or closing a state's context: the same EngineError, and the browser closes."""

    def test_a_failed_new_context(self) -> None:
        run, error = self.raised_by(
            FakeScript(new_context_error=_GONE_WITH_LOG), state()
        )
        self.assertEqual(str(error), f"home/default: {_GONE}")
        # No context was opened, so none is closed; the browser still is.
        self.assertEqual(run.named("context.close"), [])
        self.assertEqual(len(run.named("browser.close")), 1)

    def test_a_failed_context_close(self) -> None:
        run, error = self.raised_by(
            FakeScript(context_close_error=_GONE_WITH_LOG), state()
        )
        self.assertEqual(str(error), f"home/default: {_GONE}")
        self.assertEqual(len(run.named("page.pdf")), 1)
        self.assertEqual(len(run.named("browser.close")), 1)


class BrowserCloseFailureTests(FailureTestCase):
    """A browser that fails to close is an EngineError naming it, and the others still close."""

    def test_every_browser_is_closed_and_the_failure_named(self) -> None:
        script = FakeScript(
            probes=[probe_dict(colorScheme="dark")],
            browser_close_error="Browser has been closed\nCall log: ...",
        )
        run, error = self.raised_by(
            script, state(), state("dark", color_scheme="native-dark")
        )
        message = str(error)
        self.assertTrue(message.startswith("could not close "), message)
        self.assertTrue(message.endswith(": Browser has been closed"), message)
        self.assertEqual(
            sorted(run.named("browser.close")),
            sorted(
                [
                    call("browser.close", SHELL_VERSION),
                    call("browser.close", CHROME_VERSION),
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
