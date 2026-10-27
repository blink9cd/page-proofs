"""Grouped suite: the LIVE boundary -- the pinned engines, driven for real over the sample site.

A fake browser cannot catch the faults this tool exists to stop: an emulation option an engine
silently ignores, a scrollbar that shifts every width, a plant that never plants. So these tests
launch the real Chromium, Firefox and WebKit builds against `samples/site`, served on loopback,
and check what each engine actually reports. The late-script load has its own live suite,
`test_pass_late_live.py`; `_live_fixtures.py` holds the setup both share.
"""

from __future__ import annotations

import contextlib
import os
import socket
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ and the
# shared loopback server on sys.path for every import after it.
from _live_fixtures import (
    RESTLESS_PAGE,
    TEST_SETTLE_QUIET_SECONDS,
    TIMEOUT,
    LiveSite,
    SharedEngines,
    scratch,
)

import loopback_site
import pass_engine
import pass_facts
import pass_probe
import pass_shift
from pass_digest import Box
from pass_report import RenderingResult
from pass_targets import ENGINES, SCRIPTS, Rendering

_TIMEOUT = TIMEOUT

#: The engine launches this module may make (`LiveSite` fails it past them). Each proof, the
#: landscape read-back and the watchdog wiring go through `run_matrix`, which launches its own
#: browser -- that launch is what they test; the no-build refusal's attempt needs a driver of its
#: own, because Playwright reads the builds path once, at startup; the shared engines' own
#: watchdog wiring starts one Chromium; the refusals share one; and the motion tests share one
#: of each engine. So: Chromium 4, Firefox 2, WebKit 5.
_LAUNCH_BUDGET = {"chromium": 4, "firefox": 2, "webkit": 5}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)

#: What the faulty sample page shows each detector, with scripts on. With scripts off its
#: inline script never runs, so neither the console error it logs nor the error it throws exists.
_FAULTY_WITH_SCRIPTS = {
    "page_overflow",
    "past_right_edge",
    "hidden_rendered",
    "small_target",
    "console_error",
    "page_error",
    "http_error",
}
_FAULTY_WITHOUT_SCRIPTS = {
    "page_overflow",
    "past_right_edge",
    "hidden_rendered",
    "small_target",
    "http_error",
}


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the pinned builds and serve the sample site for the whole module.

    A cleanup rather than a `tearDownModule`, as in the late-script suite; a `start` that fails
    part way has already undone itself, so there is nothing for a cleanup to stop.
    """
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)


class ProveEveryEngineTests(unittest.TestCase):
    """In each engine: the plants are all caught, and the sample pages report what they hold."""

    def _prove(self, engine: str) -> None:
        renderings = [
            Rendering(engine, "/faulty.html", 390, 844, "light", "on"),
            Rendering(engine, "/faulty.html", 390, 844, "dark", "off"),
            Rendering(engine, "/clean.html", 390, 844, "dark", "on"),
        ]
        versions, results = _SITE.run(self, renderings, prove=True)
        self.assertTrue(versions[engine])
        with_scripts, without_scripts, clean = results
        for result in results:
            self.assertEqual(result.faults, (), result.rendering.label)
        # Only Chromium reports layout shifts; elsewhere the late shift is NOT MEASURED.
        shifts_seen = engine in pass_shift.SHIFT_ENGINES
        self.assertEqual(
            set(with_scripts.findings),
            _FAULTY_WITH_SCRIPTS | ({"late_shift"} if shifts_seen else set()),
        )
        # The page also logs a plain line; only console ERRORS are findings.
        self.assertIn("sample console error", with_scripts.findings["console_error"])
        self.assertFalse(
            any("log line" in m for m in with_scripts.findings["console_error"])
        )
        self.assertTrue(_FAULTY_WITHOUT_SCRIPTS <= set(without_scripts.findings))
        self.assertNotIn("page_error", without_scripts.findings)
        self.assertEqual(clean.findings, {})
        verdicts = dict(with_scripts.verdicts)
        self.assertEqual(
            verdicts.pop("late_shift"), "caught" if shifts_seen else "not measured"
        )
        self.assertEqual(set(verdicts.values()), {"caught"})
        self.assertEqual(without_scripts.verdicts["page_error"], "n/a")
        self.assertEqual(without_scripts.verdicts["late_shift"], "n/a")
        self.assertEqual(
            {
                v
                for d, v in without_scripts.verdicts.items()
                if d not in ("page_error", "late_shift", "csp_violation")
            },
            {"caught"},
        )
        self.assertEqual(without_scripts.late_shift.status, pass_shift.NOT_APPLICABLE)
        self.assertEqual(
            clean.late_shift.status,
            pass_shift.MEASURED if shifts_seen else pass_shift.NOT_MEASURED,
        )
        self.assertTrue(clean.digest)

    def test_chromium(self) -> None:
        self._prove("chromium")

    def test_firefox(self) -> None:
        self._prove("firefox")

    def test_webkit(self) -> None:
        self._prove("webkit")

    def test_the_engines_under_test_are_all_of_them(self) -> None:
        self.assertEqual(ENGINES, ("chromium", "firefox", "webkit"))


class OrientationTests(unittest.TestCase):
    """A phone on its side: the same page, width and height swapped."""

    def test_landscape_is_read_back_and_changes_what_overflows(self) -> None:
        _, (result,) = _SITE.run(
            self, [Rendering("webkit", "/faulty.html", 844, 390, "light", "off")]
        )
        self.assertEqual(result.faults, ())
        # 600px of content fits a phone on its side, so the page no longer overflows...
        self.assertNotIn("page_overflow", result.findings)
        # ...while the badge hung past the right edge is past it at any width.
        self.assertIn("past_right_edge", result.findings)


class RefusalTests(unittest.TestCase):
    """Every way a rendering cannot be measured honestly is a fault, never a zero.

    One Chromium serves the whole class (`SharedEngines`), so nothing here may run `run_matrix`,
    which starts a driver of its own; the engine with no build is refused in `MissingBuildTests`.
    """

    engines = SharedEngines()

    @classmethod
    def setUpClass(cls) -> None:
        """Start the class's driver; `setUpModule` has already pointed it at the builds."""
        cls.engines.start()
        cls.addClassCleanup(cls.engines.stop)

    def test_a_viewport_the_page_does_not_report_is_a_fault(self) -> None:
        asked = Rendering("chromium", "/clean.html", 375, 844, "light", "on")
        opened = Rendering("chromium", "/clean.html", 390, 844, "light", "on")
        context = pass_engine.open_context(self.engines.browser("chromium"), opened)
        try:
            page = context.new_page()
            listeners = pass_engine.Listeners(page, _SITE.base_url)
            result = pass_engine.measure(
                page,
                listeners,
                asked,
                _SITE.base_url,
                timeout=_TIMEOUT,
                prove=False,
            )
        finally:
            # The shared browser outlives this test, so its context must not.
            context.close()
        self.assertEqual(len(result.faults), 1)
        self.assertIn("390px wide, asked for 375px", result.faults[0])
        self.assertEqual(result.findings, {})

    def test_a_page_that_never_settles_is_a_fault(self) -> None:
        site = scratch(self)
        (site / "restless.html").write_text(
            RESTLESS_PAGE, encoding="utf-8", newline="\n"
        )
        with loopback_site.serve_directory(site) as base_url:
            result = self.engines.render(
                Rendering("chromium", "/restless.html", 390, 844, "light", "on"),
                base_url,
                timeout=3.0,
            )
        self.assertEqual(len(result.faults), 1)
        self.assertIn("still changing after 3s", result.faults[0])

    def test_a_page_that_does_not_load_is_a_fault(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        result = self.engines.render(
            Rendering("chromium", "/", 390, 844, "light", "on"),
            f"http://127.0.0.1:{closed_port}",
        )
        self.assertEqual(len(result.faults), 1)
        self.assertIn("did not load", result.faults[0])


class MissingBuildTests(unittest.TestCase):
    """An engine with no build is refused by name, before anything is rendered.

    A class of its own because the attempt needs a driver of its own: Playwright reads the builds
    path once, when its driver starts, so only a driver started after pointing it at an empty
    folder can find the build missing.
    """

    def test_an_engine_with_no_build_is_refused_naming_the_engine(self) -> None:
        with mock.patch.dict(os.environ, {pass_facts.BROWSERS_ENV: str(scratch(self))}):
            with self.assertRaisesRegex(pass_engine.EngineError, "webkit"):
                _SITE.run(
                    self, [Rendering("webkit", "/clean.html", 390, 844, "light", "on")]
                )


#: Where the motion sample's boxes rest, in px below the top of the stage holding them: the
#: slide at the END of its move, the endless bob at its iteration start, and the box the page
#: pauses itself at its first frame -- untouched, where a finish would have sent it to 160.
_MOTION_AT_REST = {"#slide": 120.0, "#bob": 0.0, "#held": 40.0}


def _box(result: RenderingResult, element_id: str) -> Box:
    """The digest's box for the one element carrying `element_id` (written `#id`)."""
    (box,) = [box for path, box in result.digest.items() if path.endswith(element_id)]
    return box


class MotionTests(unittest.TestCase):
    """Motion paced past the settle window is measured at rest, not in its first pause.

    The sample's slide holds still far longer than the window before it moves, so a settle that
    only waits for stillness measures it before the move. One browser per engine serves the
    class (`SharedEngines`), so nothing here may run `run_matrix`.
    """

    engines = SharedEngines()

    @classmethod
    def setUpClass(cls) -> None:
        """Start the class's driver; `setUpModule` has already pointed it at the builds."""
        cls.engines.start()
        cls.addClassCleanup(cls.engines.stop)

    def test_paced_and_endless_motion_is_measured_at_rest_in_every_engine(self) -> None:
        for engine in ENGINES:
            for scripts in SCRIPTS:
                rendering = Rendering(
                    engine, "/motion.html", 390, 844, "light", scripts
                )
                with self.subTest(rendering.label):
                    result = self.engines.render(rendering, _SITE.base_url)
                    self.assertEqual(result.faults, ())
                    stage = _box(result, "#stage")
                    rests = {
                        name: round(_box(result, name).y - stage.y, 2)
                        for name in _MOTION_AT_REST
                    }
                    self.assertEqual(rests, _MOTION_AT_REST)
                    motion = result.motion
                    self.assertEqual(
                        (motion.finished, motion.paused, motion.running), (1, 1, 0)
                    )
                    # Both moving boxes move only `transform`, so the late-script load, where
                    # it runs, stills them too: counted apart, never added to the above.
                    late = result.late_motion
                    measured = result.late_shift.status == pass_shift.MEASURED
                    stilled = (1, 1, 0) if measured else (0, 0, 0)
                    self.assertEqual(
                        (late.finished, late.paused, late.running), stilled
                    )
                    self.assertEqual(result.findings, {})


class _RecordingWatchdog:
    """Records every bound the engine asks for, and never fires."""

    def __init__(self) -> None:
        self.bounds: list[tuple[float, str]] = []

    @contextlib.contextmanager
    def bound(self, seconds: float, what: str) -> Iterator[None]:
        self.bounds.append((seconds, what))
        yield


class WatchdogWiringTests(unittest.TestCase):
    """Playwright's evaluate and close take no timeout, so the watchdog must bound them."""

    def test_every_rendering_and_every_close_runs_inside_a_bound(self) -> None:
        watchdog = _RecordingWatchdog()
        renderings = [
            Rendering("webkit", "/clean.html", 390, 844, "light", "on"),
            Rendering("webkit", "/clean.html", 390, 844, "dark", "off"),
        ]
        pass_engine.run_matrix(
            renderings,
            _SITE.base_url,
            timeout=_TIMEOUT,
            prove=False,
            scratch=scratch(self),
            progress=lambda _result: None,
            watchdog=watchdog,
        )
        self.assertEqual(
            [what for _, what in watchdog.bounds],
            [f"rendering {r.label}" for r in renderings] + ["closing webkit"],
        )
        # A rendering's bound covers two loads, each step up to `timeout` long: its load, its
        # settling and its proof; then the late-script load's navigation, its hold until the
        # first paint, the rest of its load, its settling and its proof.
        self.assertTrue(
            all(seconds > 8 * _TIMEOUT for seconds, _ in watchdog.bounds[:2])
        )

    def test_the_shared_engines_bound_every_rendering_and_every_close(self) -> None:
        # The tests that share a browser lost run_matrix's bounds with it, so the shared
        # engines give each rendering and each close the same ones.
        watchdog = _RecordingWatchdog()
        engines = SharedEngines(bounder=watchdog)
        rendering = Rendering("chromium", "/clean.html", 390, 844, "light", "off")
        engines.start()
        try:
            engines.render(rendering, _SITE.base_url)
        finally:
            engines.stop()
        self.assertEqual(
            watchdog.bounds,
            [
                (pass_engine.rendering_bound(_TIMEOUT), f"rendering {rendering.label}"),
                (pass_engine.CLOSE_BOUND_SECONDS, "closing chromium"),
            ],
        )


class SettleWindowTests(unittest.TestCase):
    """This module renders under the test settle window, and a rendering reads it when it settles."""

    def test_this_module_renders_under_the_test_settle_window(self) -> None:
        self.assertEqual(pass_probe.SETTLE_QUIET_SECONDS, TEST_SETTLE_QUIET_SECONDS)

    def test_a_rendering_reads_the_settle_window_when_it_settles(self) -> None:
        # A copy taken at import would keep every rendering paying the window the module
        # started with, whatever the live site set. The refused settle ends the rendering
        # there, so the mock page is asked for nothing past its load.
        window = TEST_SETTLE_QUIET_SECONDS / 2
        with mock.patch.object(pass_probe, "SETTLE_QUIET_SECONDS", window):
            with mock.patch.object(
                pass_engine, "wait_until_settled", return_value=False
            ) as settle:
                pass_engine.measure(
                    mock.Mock(),
                    mock.Mock(),
                    Rendering("chromium", "/", 390, 844, "light", "on"),
                    "http://127.0.0.1:0",
                    timeout=_TIMEOUT,
                    prove=False,
                )
        self.assertEqual(settle.call_args.kwargs["quiet"], window)


class FirefoxEnvironmentTests(unittest.TestCase):
    """Firefox's per-user data goes to scratch, never the real Firefox folder in the home."""

    def test_points_both_data_folders_intoscratch(self) -> None:
        scratch = Path("/scratch")
        env = pass_engine.firefox_env({"PATH": "/bin"}, scratch)
        self.assertEqual(env["PATH"], "/bin")
        self.assertTrue(Path(env["MOZ_APP_DATA"]).is_relative_to(scratch))
        self.assertTrue(Path(env["MOZ_LOCAL_APP_DATA"]).is_relative_to(scratch))
        self.assertNotEqual(env["MOZ_APP_DATA"], env["MOZ_LOCAL_APP_DATA"])


if __name__ == "__main__":
    unittest.main()
