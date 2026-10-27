"""Suite: the Playwright engine refuses Firefox, which it never launches.

Firefox prints through geckodriver's WebDriver Print, on a path of its own. Handed to the
Playwright engine it would otherwise fall through to a launch of some Chromium, printing a
run recorded as Firefox's. So it is a `ValueError`, the engine's word for a programming
error, before anything launches: from `run_targets`, and from the launch call itself.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_engine  # noqa: E402 - the tool's private src/, via the insert above
from _print_engine_fixtures import (  # noqa: E402 - beside this module
    TIMEOUT_MS,
    FakeRun,
    StateTestCase,
    engine_settings,
)


class FirefoxRefusalTests(StateTestCase):
    """Firefox is refused before any browser launches."""

    def test_run_targets_refuses_firefox_before_anything_launches(self) -> None:
        run = FakeRun(self.pdf_dir)
        settings = dataclasses.replace(engine_settings(self.pdf_dir), engine="firefox")
        with self.assertRaisesRegex(ValueError, "firefox"):
            run.run(settings=settings)
        self.assertEqual(run.playwright.chromium.browsers, [])
        self.assertEqual(run.calls, [])

    def test_the_launch_call_refuses_firefox(self) -> None:
        run = FakeRun(self.pdf_dir)
        with self.assertRaisesRegex(ValueError, "firefox"):
            print_engine._launch_call(run.playwright.chromium, "firefox", TIMEOUT_MS)
        self.assertEqual(run.playwright.chromium.browsers, [])

    def test_the_launch_call_still_launches_each_playwright_browser(self) -> None:
        for key in ("shell", "chrome", "native-dark"):
            with self.subTest(key=key):
                run = FakeRun(self.pdf_dir)
                print_engine._launch_call(run.playwright.chromium, key, TIMEOUT_MS)
                self.assertEqual(len(run.playwright.chromium.browsers), 1)


if __name__ == "__main__":
    unittest.main()
