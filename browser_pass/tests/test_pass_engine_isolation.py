"""Grouped suite: the engine under `--loopback-only` and with an extension, through fakes.

`pass_engine.launch` is the one seam an isolation passes through: guarded, each engine is launched
with its egress guard; with an extension, the engine's renderings get a `PersistentBrowser`
instead of a browser, and every context `render` opens comes from it. An extension that did not
load is the rendering's instrument fault, in the page load and in the late-script load alike, and
`run_matrix` hands the isolation to every launch and widens each rendering's bound for its
launches. The real engines are driven in `test_pass_extension_live.py` and
`test_pass_loopback_live.py`.
"""

from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pass_engine  # noqa: E402 - the tool's private src/, via the insert above
import pass_extension  # noqa: E402 - the tool's private src/, via the insert above
import pass_report  # noqa: E402 - the tool's private src/, via the insert above
import pass_shift  # noqa: E402 - the tool's private src/, via the insert above
from _extension_fixtures import unpacked, xpi  # noqa: E402 - beside this suite
from pass_extension import Extension, Isolation  # noqa: E402 - the tool's private src/
from pass_extension_launch import (  # noqa: E402 - the tool's private src/
    ExtensionFault,
    PersistentBrowser,
)
from pass_motion import NO_MOTION  # noqa: E402 - the tool's private src/
from pass_probe import MS_PER_SECOND  # noqa: E402 - the tool's private src/
from pass_targets import Rendering  # noqa: E402 - the tool's private src/

_TIMEOUT = 7.0
_BASE_URL = "http://127.0.0.1:8123"
_CHROMIUM = Rendering("chromium", "/", 390, 844, "light", "on")
_GUARDED = Isolation(loopback_only=True)


class _Case(unittest.TestCase):
    """A scratch folder, with an extension for each engine that can load one."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        chromium = pass_extension.read_unpacked(unpacked(self.root / "ext"))
        firefox = pass_extension.read_xpi(xpi(self.root / "fixture.xpi"))
        assert isinstance(chromium, Extension) and isinstance(firefox, Extension)
        self.chromium, self.firefox = chromium, firefox
        self.playwright = mock.Mock()


class GuardedLaunchTests(_Case):
    """`--loopback-only` without an extension: the usual launch, with each engine's guard."""

    def _launch(self, engine: str, isolation: Isolation) -> object:
        return pass_engine.launch(
            self.playwright,
            engine,
            scratch=self.root,
            timeout=_TIMEOUT,
            isolation=isolation,
        )

    def test_chromium_runs_the_full_build_resolving_nothing_off_loopback(self) -> None:
        browser = self._launch("chromium", _GUARDED)
        self.assertIs(browser, self.playwright.chromium.launch.return_value)
        self.playwright.chromium.launch.assert_called_once_with(
            timeout=_TIMEOUT * MS_PER_SECOND,
            channel="chromium",
            args=list(pass_extension.CHROMIUM_GUARD_ARGS),
        )

    def test_firefox_keeps_its_own_prefs_and_redirect_beside_the_guard(self) -> None:
        self._launch("firefox", _GUARDED)
        options = self.playwright.firefox.launch.call_args.kwargs
        self.assertEqual(
            options["firefox_user_prefs"],
            {
                "ui.useOverlayScrollbars": 1,
                **pass_extension.guard_options("firefox", _GUARDED)[
                    "firefox_user_prefs"
                ],
            },
        )
        self.assertEqual(Path(options["env"]["MOZ_APP_DATA"]).parent, self.root)

    def test_webkit_goes_through_the_refusing_proxy(self) -> None:
        self._launch("webkit", _GUARDED)
        self.playwright.webkit.launch.assert_called_once_with(
            timeout=_TIMEOUT * MS_PER_SECOND,
            proxy={"server": "http://127.0.0.1:9", "bypass": "127.0.0.1"},
        )

    def test_unguarded_launches_are_unchanged(self) -> None:
        self._launch("webkit", pass_extension.NO_ISOLATION)
        self.playwright.webkit.launch.assert_called_once_with(
            timeout=_TIMEOUT * MS_PER_SECOND
        )


class ExtensionLaunchTests(_Case):
    """An engine with an extension: nothing launches until a context is asked for."""

    def test_an_engine_with_an_extension_gets_a_persistent_browser(self) -> None:
        isolation = Isolation(
            loopback_only=True, extensions=(self.chromium, self.firefox)
        )
        for engine in ("chromium", "firefox"):
            with self.subTest(engine=engine):
                browser = pass_engine.launch(
                    self.playwright,
                    engine,
                    scratch=self.root,
                    timeout=_TIMEOUT,
                    isolation=isolation,
                )
                self.assertIsInstance(browser, PersistentBrowser)
                getattr(self.playwright, engine).launch.assert_not_called()

    def test_its_launch_options_carry_the_guard(self) -> None:
        isolation = Isolation(loopback_only=True, extensions=(self.chromium,))
        context = mock.Mock(pages=[], browser=None)
        context.service_workers = [
            mock.Mock(
                url="chrome-extension://abcdefghijklmnopabcdefghijklmnop/bg/worker.js"
            )
        ]
        self.playwright.chromium.launch_persistent_context.return_value = context
        browser = pass_engine.launch(
            self.playwright,
            "chromium",
            scratch=self.root,
            timeout=_TIMEOUT,
            isolation=isolation,
        )
        self.assertIs(pass_engine.open_context(browser, _CHROMIUM), context)
        options = self.playwright.chromium.launch_persistent_context.call_args.kwargs
        self.assertEqual(options["channel"], "chromium")
        guard = list(pass_extension.CHROMIUM_GUARD_ARGS)
        self.assertEqual(options["args"][: len(guard)], guard)
        self.assertEqual(options["viewport"], {"width": 390, "height": 844})
        self.assertIs(options["java_script_enabled"], True)
        self.assertEqual(options["device_scale_factor"], 1)
        self.assertEqual(options["color_scheme"], "light")
        self.assertEqual(options["reduced_motion"], "no-preference")


class ExtensionFaultTests(unittest.TestCase):
    """An extension that did not load is a fault naming it, never a clean measurement."""

    _FAULT = "chromium: the extension Fixture 2.5.1 did not load within 7s"

    def test_in_the_page_load_it_stops_the_rendering(self) -> None:
        browser = mock.Mock()
        browser.new_context.side_effect = ExtensionFault(self._FAULT)
        result = pass_engine.render(
            browser, _CHROMIUM, _BASE_URL, timeout=_TIMEOUT, prove=False
        )
        self.assertEqual(result.faults, (self._FAULT,))
        self.assertEqual(result.findings, {})
        self.assertEqual(result.motion, NO_MOTION)
        self.assertEqual(pass_report.exit_code([result]), 2)
        browser.new_context.assert_called_once()

    def test_in_the_late_script_load_it_is_the_late_load_s_fault(self) -> None:
        browser = mock.Mock()
        browser.new_context.side_effect = [mock.Mock(), ExtensionFault(self._FAULT)]
        clean = pass_report.RenderingResult(
            _CHROMIUM,
            (),
            {},
            {},
            {},
            pass_shift.LateShift(pass_shift.NOT_MEASURED),
            NO_MOTION,
        )
        with mock.patch.object(pass_engine, "measure", return_value=clean):
            result = pass_engine.render(
                browser, _CHROMIUM, _BASE_URL, timeout=_TIMEOUT, prove=False
            )
        self.assertEqual(result.faults, (f"late_shift: {self._FAULT}",))
        self.assertEqual(result.late_shift.status, pass_shift.NOT_MEASURED)
        self.assertEqual(result.late_shift.reason, f"late_shift: {self._FAULT}")
        self.assertEqual(pass_report.exit_code([result]), 2)


class _RecordingWatchdog:
    """Records every bound the engine asks for, and never fires."""

    def __init__(self) -> None:
        self.bounds: list[tuple[float, str]] = []

    @contextlib.contextmanager
    def bound(self, seconds: float, what: str) -> Iterator[None]:
        self.bounds.append((seconds, what))
        yield


class MatrixWiringTests(_Case):
    """`run_matrix` launches each engine with the isolation, bounds for it, and reads versions."""

    def test_the_isolation_reaches_every_launch_and_widens_the_bound(self) -> None:
        isolation = Isolation(loopback_only=True, extensions=(self.chromium,))
        launched: list[Isolation] = []
        persistent = PersistentBrowser(
            mock.Mock(), self.chromium, scratch=self.root, timeout=_TIMEOUT, options={}
        )

        def launch(_playwright: object, engine: str, **options: object) -> object:
            launched.append(options["isolation"])  # type: ignore[arg-type] # the keyword is an Isolation
            return persistent

        def render(
            _browser: object, rendering: Rendering, *_a: object, **_o: object
        ) -> object:
            # The version a persistent browser has is the one its first context gave it.
            persistent.version = "153.0.1"
            late = pass_shift.LateShift(pass_shift.NOT_APPLICABLE)
            return pass_report.RenderingResult(
                rendering, (), {}, {}, {}, late, NO_MOTION
            )

        watchdog = _RecordingWatchdog()
        with (
            mock.patch.object(
                pass_engine,
                "sync_playwright",
                lambda: contextlib.nullcontext(mock.Mock()),
            ),
            mock.patch.object(pass_engine, "launch", launch),
            mock.patch.object(pass_engine, "render", render),
        ):
            versions, _ = pass_engine.run_matrix(
                [_CHROMIUM],
                _BASE_URL,
                timeout=_TIMEOUT,
                prove=False,
                scratch=self.root,
                progress=lambda _result: None,
                watchdog=watchdog,
                isolation=isolation,
            )
        self.assertEqual(launched, [isolation])
        self.assertEqual(versions, {"chromium": "153.0.1"})
        self.assertEqual(
            watchdog.bounds[0][0],
            pass_engine.rendering_bound(_TIMEOUT) + persistent.bound(_TIMEOUT),
        )


if __name__ == "__main__":
    unittest.main()
