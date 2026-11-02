"""Grouped suite: the late-script load, live (scripts held until the first paint, for real).

The race this load removes only exists in a real engine, so these tests drive the pinned
Chromium (and, once, Firefox) against `samples/site` and against pages written for one refusal
each, all of them in one browser per engine for the whole module, each test in a fresh context,
as the tool itself runs its renderings. `_live_fixtures.py` holds the setup shared with the other
live suites; how this load stills endless motion is `test_pass_late_motion_live.py`'s.
"""

# gate-serves: none (temporary trees, and its own samples/site, gated with its tool)

from __future__ import annotations

import socket
import time
import unittest
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
    late_load,
    scratch,
    served_late_load,
)

import loopback_site
import pass_late_load
import pass_probe
import pass_shift
from pass_targets import Rendering
from playwright.sync_api import Page

#: The engine launches this module may make (`LiveSite` fails it past them): one browser per
#: engine, shared by every test, as the tool itself shares one across its renderings.
_LAUNCH_BUDGET = {"chromium": 1, "firefox": 1}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)
_ENGINES = SharedEngines()

#: A page with nothing to paint: its root is not displayed, so no first paint ever comes.
_UNPAINTED_PAGE = """<!doctype html><html lang="en"><head><title>unpainted</title>
<style>html { display: none; }</style></head><body><p>never shown</p></body></html>
"""

#: This suite's own directory, for the one test that serves the web face kept beside it.
_HERE = Path(__file__).resolve().parent

#: A page that paints (its root has a background) but whose <body> holds no box, so nothing
#: appended to it (the late-shift plant included) can be drawn.
_BODILESS_PAGE = """<!doctype html><html lang="en"><head><title>bodiless</title>
<style>html { background: #abc; } body { display: none; }</style></head>
<body><p>never shown</p></body></html>
"""

#: A page whose only face is fetched from PORT, where something accepts and never answers, so
#: `document.fonts.status` never leaves "loading". The block paints at once, so the page is
#: painted-but-fontless: exactly the state the hold must refuse rather than release into.
_FONTLESS_PAGE = """<!doctype html><html lang="en"><head><title>fontless</title>
<style>
@font-face { font-family: Never; src: url("http://127.0.0.1:PORT/never.woff2") format("woff2"); }
body { font-family: Never, serif; }
</style></head>
<body><div style="width: 50px; height: 50px; background: #abc"></div><p>waiting for a face</p>
<script type="module" src="never-runs.js"></script></body></html>
"""

#: A page that cannot paint until its script runs, and whose text only THEN starts loading a web
#: face (the script injects the `@font-face` and applies it), so the fetch cannot begin before
#: the paint. `font-display: swap` makes the face replace the fallback when it lands, which moves
#: every line that had already been painted.
#:
#: This is the shape that exposed the counting window: released at once (no paint can come while a
#: render-blocking script is held), the swap lands after the release and would be charged to the
#: script, although the script had finished before anything was on screen. Counting from the
#: moment the page is presentable (painted, then in its own fonts) is what leaves it out.
_BLOCKING_SWAP_PAGE = """<!doctype html><html lang="en"><head><title>blocking swap</title>
<style>body { margin: 0; font: 16px/1.5 monospace; }</style>
<script blocking="render" src="swap.js"></script></head>
<body><p>One line of text.</p><p>Another line, longer than the first one above it.</p>
<p>A third line, so a face with other metrics moves something.</p></body></html>
"""

#: The render-blocking script itself: it runs before the first paint, and the only thing it does
#: is ask for a face the page will paint without and then swap in.
_BLOCKING_SWAP_SCRIPT = """document.head.insertAdjacentHTML(
  "beforeend",
  '<style>@font-face { font-family: Late; font-display: swap;' +
    ' src: url("late_face.ttf") format("truetype"); }' +
    ' body { font-family: Late, monospace; }</style>',
);
"""

#: A page waiting on an image from `{port}`, where something accepts and never answers.
_STALLED_PAGE = """<!doctype html><html lang="en"><head><title>stalled</title></head>
<body><p>waiting</p><img src="http://127.0.0.1:{port}/never.png" alt="" width="8" height="8">
</body></html>
"""


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the pinned builds, serve the sample site, start the shared engines.

    Cleanups rather than a `tearDownModule`, so a start that fails part way still undoes
    what did start: the shared engines first, then the site, whose stop checks the budget.
    """
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)
    _ENGINES.start()
    unittest.addModuleCleanup(_ENGINES.stop)


def _late_load(
    rendering: Rendering,
    *,
    base_url: str | None = None,
    timeout: float = TIMEOUT,
    prove: bool = False,
) -> pass_late_load.LateOutcome:
    """The late-script load alone, in the module's browser for `rendering`'s engine."""
    return late_load(
        _ENGINES,
        rendering,
        base_url or _SITE.base_url,
        timeout=timeout,
        prove=prove,
    )


def _served_late_load(
    case: unittest.TestCase,
    name: str,
    page: str,
    *,
    timeout: float,
    prove: bool = False,
) -> pass_late_load.LateOutcome:
    """The late-script load of `page`, served alone from `case`'s scratch folder as `name`."""
    return served_late_load(case, _ENGINES, {name: page}, timeout=timeout, prove=prove)


class _HoldTimer:
    """The late-script load's hold, timed alone: from when it starts waiting until it releases.

    Stands in for `pass_late_load._hold_until_painted` and calls straight through to it, so the
    hold is the real one. Only the hold is timed, because only the hold is what its bound
    governs: a hold that waits the bound out takes at least that long on any machine, while the
    engine's launch and the page-load detectors before it take whatever the machine can spare.
    """

    def __init__(self) -> None:
        """Nothing timed yet; the real hold is captured now, before anything patches it."""
        self.seconds: list[float] = []
        self._hold = pass_late_load._hold_until_painted

    def __call__(
        self, page: Page, hold: pass_late_load.ScriptHold, *, timeout: float
    ) -> tuple[list[str], bool, pass_shift.HoldState]:
        """Run the real hold and record how long it took."""
        started = time.monotonic()
        outcome = self._hold(page, hold, timeout=timeout)
        self.seconds.append(time.monotonic() - started)
        return outcome


class SettleWindowTests(unittest.TestCase):
    """This module renders under the test settle window its live site sets."""

    def test_this_module_renders_under_the_test_settle_window(self) -> None:
        self.assertEqual(pass_probe.SETTLE_QUIET_SECONDS, TEST_SETTLE_QUIET_SECONDS)


class LateShiftTests(unittest.TestCase):
    """The late-script load: scripts held until the page first paints, then every shift after."""

    def test_a_late_script_that_moves_painted_content_is_a_finding(self) -> None:
        result = _ENGINES.render(
            Rendering("chromium", "/faulty.html", 390, 844, "light", "on"),
            _SITE.base_url,
        )
        self.assertEqual(result.late_shift.status, pass_shift.MEASURED)
        self.assertGreater(result.late_shift.score, pass_shift.THRESHOLD)
        # Which node a shift names is the engine's attribution (here, measured: <body>), so
        # only the finding's shape is pinned: its score, then what moved.
        (finding,) = result.findings["late_shift"]
        self.assertRegex(finding, r"^\d\.\d{4} from body")

    def test_a_late_script_that_fills_its_reserve_moves_nothing(self) -> None:
        result = _ENGINES.render(
            Rendering("chromium", "/clean.html", 390, 844, "light", "on"),
            _SITE.base_url,
        )
        self.assertEqual(result.late_shift.status, pass_shift.MEASURED)
        self.assertEqual(result.late_shift.score, 0.0)

    def test_the_same_script_blocking_render_moves_nothing_and_is_not_waited_out(
        self,
    ) -> None:
        # No paint can come while a render-blocking script is held, so the hold ends when the
        # page shows one rather than after the whole bound. Only the HOLD is timed: timing the
        # whole run put the engine's launch under the same bound, so a loaded machine read as a
        # hold that waited. A hold that does wait its bound out cannot come in under it, on a
        # machine of any speed.
        timeout = 10.0
        hold = _HoldTimer()
        with mock.patch.object(pass_late_load, "_hold_until_painted", hold):
            result = _ENGINES.render(
                Rendering("chromium", "/blocked.html", 390, 844, "light", "on"),
                _SITE.base_url,
                timeout=timeout,
            )
        (held_for,) = hold.seconds
        self.assertLess(held_for, timeout)
        self.assertEqual(result.faults, ())
        self.assertEqual(result.late_shift.status, pass_shift.MEASURED)
        self.assertEqual(result.late_shift.score, 0.0)

    def test_a_face_swapping_in_after_a_blocked_paint_is_not_the_scripts_doing(
        self,
    ) -> None:
        # THE REGRESSION THIS WINDOW EXISTS FOR, measured end to end. The page's own script
        # blocks the first paint, so the hold releases at once and the release is before any
        # paint, which is precisely where the fonts half of the hold would stop protecting
        # were counting to start at the release.
        # The face arrives after the paint and moves three painted lines; none of it is the
        # script's doing, and the score must be zero rather than a finding.
        #
        # MEASURED rather than refused is half the assertion: a page whose presentable moment
        # the probe failed to record is now refused, so reading a real zero here is also the
        # proof that `paint, then document.fonts.ready` was observed on a page that could not
        # paint until its script ran.
        site = scratch(self)
        (site / "blocking-swap.html").write_text(
            _BLOCKING_SWAP_PAGE, encoding="utf-8", newline="\n"
        )
        (site / "swap.js").write_text(
            _BLOCKING_SWAP_SCRIPT, encoding="utf-8", newline="\n"
        )
        # The face is this suite's own: one box glyph 1.5em wide, mapped from every printable
        # ASCII character, generated for this test with fontTools' FontBuilder and carrying no
        # name but its family. The width is the point: every line wraps anew when it lands, so
        # the later paragraphs move a whole line or more. A face as narrow as monospace moved
        # nothing, and this test then passed with its counting window deleted. It owes no
        # license and reads nothing of any site.
        face = _HERE / "late_face.ttf"
        (site / face.name).write_bytes(face.read_bytes())
        with loopback_site.serve_directory(site) as base_url:
            outcome = _late_load(
                Rendering("chromium", "/blocking-swap.html", 390, 844, "light", "on"),
                base_url=base_url,
            )

        self.assertEqual(outcome.faults, ())
        self.assertEqual(outcome.late.status, pass_shift.MEASURED)
        self.assertEqual(outcome.late.score, 0.0)

    def test_a_plant_on_a_render_blocking_page_is_still_caught(self) -> None:
        # The other direction, and the one that matters if the window is drawn too late: a
        # counting start that overshot would drop real shifts and the detector would go quietly
        # blind on every page carrying the fix. The plant moves a painted box, after the paint
        # and after the fonts, so it must still be reported.
        outcome = _late_load(
            Rendering("chromium", "/blocked.html", 390, 844, "light", "on"),
            prove=True,
        )

        self.assertEqual(outcome.faults, ())
        self.assertEqual(outcome.verdict, "caught")


class LateRefusalTests(unittest.TestCase):
    """Every way the late-script load cannot be measured honestly is a fault, never a zero."""

    def _assert_fault(self, outcome: pass_late_load.LateOutcome, text: str) -> None:
        (fault,) = outcome.faults
        self.assertIn(text, fault)
        self.assertEqual(outcome.late.status, pass_shift.NOT_MEASURED)

    def test_a_page_that_never_paints_while_its_scripts_are_held(self) -> None:
        outcome = _served_late_load(
            self, "unpainted.html", _UNPAINTED_PAGE, timeout=3.0
        )
        self._assert_fault(outcome, "did not paint")

    def test_an_engine_without_layout_shifts(self) -> None:
        # The tool only runs this load in Chromium; driving it in Firefox proves the page's
        # own answer is checked, so an engine build that stops reporting shifts cannot read 0.
        outcome = _late_load(
            Rendering("firefox", "/faulty.html", 390, 844, "light", "on")
        )
        self._assert_fault(outcome, "does not report layout shifts")

    def test_a_page_that_cannot_be_reached(self) -> None:
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            port = closed.getsockname()[1]
        outcome = _late_load(
            Rendering("chromium", "/", 390, 844, "light", "on"),
            base_url=f"http://127.0.0.1:{port}",
        )
        self._assert_fault(outcome, "did not load")

    def test_a_page_that_never_finishes_loading(self) -> None:
        with socket.socket() as silent:
            # Listening, so the image's connection is accepted; never read, so never answered.
            silent.bind(("127.0.0.1", 0))
            silent.listen()
            page = _STALLED_PAGE.format(port=silent.getsockname()[1])
            outcome = _served_late_load(self, "stalled.html", page, timeout=3.0)
        self._assert_fault(outcome, "did not finish loading")

    def test_a_page_whose_fonts_never_arrive(self) -> None:
        with socket.socket() as silent:
            # Listening, so the face's connection is accepted; never read, so never answered.
            silent.bind(("127.0.0.1", 0))
            silent.listen()
            page = _FONTLESS_PAGE.replace("PORT", str(silent.getsockname()[1]))
            outcome = _served_late_load(self, "fontless.html", page, timeout=3.0)
        self._assert_fault(outcome, "fonts had not loaded")

    def test_a_page_that_never_settles(self) -> None:
        outcome = _served_late_load(self, "restless.html", RESTLESS_PAGE, timeout=3.0)
        self._assert_fault(outcome, "still changing after 3s")

    def test_a_plant_that_cannot_be_drawn_was_not_planted(self) -> None:
        outcome = _served_late_load(
            self, "bodiless.html", _BODILESS_PAGE, timeout=5.0, prove=True
        )
        self.assertEqual(outcome.faults, ())
        self.assertEqual(outcome.verdict, "not planted")


if __name__ == "__main__":
    unittest.main()
