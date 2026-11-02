"""Grouped suite: launching an engine, and a page that will not load, through a fake Playwright.

`pass_engine.launch` takes the started Playwright as an argument, so a stand-in for it reaches
every launch path with no engine build installed: what each engine is launched with (Firefox's
per-user data redirected into the run's scratch folder, never the real profile store), and a
launch that fails, which must surface as an `EngineError` naming the engine. A page whose load
fails is driven the same way, through a page that raises as Playwright does. The live suites
launch the real builds; where none is installed they skip, and this suite still runs.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pass_engine  # noqa: E402 - the tool's private src/, via the insert above
from pass_motion import NO_MOTION  # noqa: E402 - the tool's private src/
from pass_probe import MS_PER_SECOND  # noqa: E402 - the tool's private src/
from pass_targets import Rendering  # noqa: E402 - the tool's private src/
from playwright.sync_api import Error as PlaywrightError  # noqa: E402 - after the insert

_TIMEOUT = 7.0

_RENDERING = Rendering("chromium", "/", 390, 844, "light", "on")


def _fake_playwright(*, fails_with: str | None = None) -> mock.Mock:
    """A started Playwright whose every engine launches a stand-in, or fails with `fails_with`."""
    playwright = mock.Mock()
    for engine in ("chromium", "firefox", "webkit"):
        browser_type = getattr(playwright, engine)
        if fails_with is not None:
            browser_type.launch.side_effect = PlaywrightError(fails_with)
    return playwright


class LaunchTests(unittest.TestCase):
    """Each engine launches headless within the timeout; Firefox never sees a real profile."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.scratch = Path(folder.name)

    def test_chromium_launches_with_the_timeout_alone(self) -> None:
        playwright = _fake_playwright()
        browser = pass_engine.launch(
            playwright, "chromium", scratch=self.scratch, timeout=_TIMEOUT
        )
        self.assertIs(browser, playwright.chromium.launch.return_value)
        playwright.chromium.launch.assert_called_once_with(
            timeout=_TIMEOUT * MS_PER_SECOND
        )
        # Nothing of Firefox's is made for any other engine.
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_firefox_launches_with_its_data_folders_in_the_scratch(self) -> None:
        playwright = _fake_playwright()
        before = dict(os.environ)
        browser = pass_engine.launch(
            playwright, "firefox", scratch=self.scratch, timeout=_TIMEOUT
        )
        self.assertIs(browser, playwright.firefox.launch.return_value)
        options = playwright.firefox.launch.call_args.kwargs
        self.assertEqual(options["timeout"], _TIMEOUT * MS_PER_SECOND)
        self.assertEqual(options["firefox_user_prefs"], {"ui.useOverlayScrollbars": 1})
        for name in ("MOZ_APP_DATA", "MOZ_LOCAL_APP_DATA"):
            folder = Path(options["env"][name])
            self.assertEqual(folder.parent, self.scratch)
            # Made before the launch, since Firefox reads them at startup.
            self.assertTrue(folder.is_dir(), name)
        # The rest of the environment is passed through, and the process's own is untouched.
        self.assertEqual(
            {k: v for k, v in options["env"].items() if not k.startswith("MOZ_")},
            {k: v for k, v in before.items() if not k.startswith("MOZ_")},
        )
        self.assertEqual(dict(os.environ), before)

    def test_a_launch_that_fails_names_the_engine_and_its_first_line(self) -> None:
        playwright = _fake_playwright(
            fails_with="Executable doesn't exist at <browsers>/webkit\nInstall it with ..."
        )
        with self.assertRaises(pass_engine.EngineError) as raised:
            pass_engine.launch(
                playwright, "webkit", scratch=self.scratch, timeout=_TIMEOUT
            )
        self.assertEqual(
            str(raised.exception),
            "cannot launch webkit: Executable doesn't exist at <browsers>/webkit",
        )
        self.assertIsInstance(raised.exception.__cause__, PlaywrightError)

    def test_a_firefox_launch_that_fails_is_refused_the_same_way(self) -> None:
        # A message opening on a blank line still names its first line of text.
        playwright = _fake_playwright(fails_with="\n  Browser closed.\nCall log: ...")
        with self.assertRaises(pass_engine.EngineError) as raised:
            pass_engine.launch(
                playwright, "firefox", scratch=self.scratch, timeout=_TIMEOUT
            )
        self.assertEqual(
            str(raised.exception), "cannot launch firefox: Browser closed."
        )


class FirefoxEnvTests(unittest.TestCase):
    """Firefox's per-user data folders are redirected, and nothing else is changed."""

    def test_redirects_both_folders_and_keeps_the_rest(self) -> None:
        scratch = Path("scratch")
        env = pass_engine.firefox_env({"PATH": "bin", "MOZ_APP_DATA": "real"}, scratch)
        self.assertEqual(
            env,
            {
                "PATH": "bin",
                "MOZ_APP_DATA": str(scratch / "firefox-app-data"),
                "MOZ_LOCAL_APP_DATA": str(scratch / "firefox-local-app-data"),
            },
        )


class PlaywrightVersionTests(unittest.TestCase):
    """The version reported is the installed Playwright package's own."""

    def test_reads_the_playwright_package_metadata(self) -> None:
        with mock.patch.object(
            pass_engine.metadata, "version", return_value="0.0.1"
        ) as version:
            self.assertEqual(pass_engine.playwright_version(), "0.0.1")
        version.assert_called_once_with("playwright")


class PageLoadFailureTests(unittest.TestCase):
    """A page that does not load is the rendering's fault, and nothing is read from it."""

    def test_a_failed_navigation_is_a_fault_with_its_first_line(self) -> None:
        page = mock.Mock()
        page.goto.side_effect = PlaywrightError(
            "net::ERR_CONNECTION_REFUSED at http://127.0.0.1:0/\nCall log: ..."
        )
        listeners = mock.Mock()
        result = pass_engine.measure(
            page,
            listeners,
            _RENDERING,
            "http://127.0.0.1:0",
            timeout=_TIMEOUT,
            prove=True,
        )
        self.assertEqual(
            result.faults,
            (
                "the page did not load: "
                "net::ERR_CONNECTION_REFUSED at http://127.0.0.1:0/",
            ),
        )
        self.assertEqual(result.findings, {})
        self.assertEqual(result.verdicts, {})
        self.assertEqual(result.motion, NO_MOTION)
        # Neither probed nor proved: the page is never evaluated, the listeners never read.
        page.evaluate.assert_not_called()
        self.assertEqual(listeners.mock_calls, [])
        page.goto.assert_called_once_with(
            "http://127.0.0.1:0/", wait_until="load", timeout=_TIMEOUT * MS_PER_SECOND
        )


if __name__ == "__main__":
    unittest.main()
