"""Grouped suite: endless motion INSIDE shadow trees, LIVE in the pinned engines, in both settles.

An engine's `document.getAnimations()` leaves out an animation whose target lives inside a shadow
root: only that root's own `getAnimations()` lists it, and a root nested in another is listed by
the inner root alone. So both settles read every OPEN root the page can reach, found recursively,
beside the document (the page-load settle in Chromium, Firefox and WebKit, and the late-script
load in Chromium), and a root's motion goes through exactly the verdicts the document's does. A
CLOSED root is out of the page's reach (its host's `shadowRoot` reads null), so its motion is
neither stilled nor counted: the documented limit, pinned here. Each page carries one endless
loop, served alone on loopback; one browser per engine serves the module, each test in a fresh
context. `_live_fixtures.py` holds the setup.
"""

# gate-serves: none (temporary trees, and its own samples/site, gated with its tool)

from __future__ import annotations

import json
import unittest

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ and the
# shared loopback server on sys.path for every import after it.
from _live_fixtures import TIMEOUT, LiveSite, SharedEngines, scratch, served_late_load

import loopback_site
import pass_engine
import pass_shift
from pass_listeners import Listeners
from pass_motion import NO_MOTION, Motion
from pass_report import RenderingResult
from pass_targets import ENGINES, Rendering
from pass_watchdog import Watchdog

#: The engine launches this module may make (`LiveSite` fails it past them): one browser per
#: engine, shared by every test, the late-script loads' Chromium among them.
_LAUNCH_BUDGET = {"chromium": 1, "firefox": 1, "webkit": 1}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)
_ENGINES = SharedEngines()

#: Bounds each page-load settle as `SharedEngines.render` bounds a rendering: Playwright gives
#: `evaluate` no timeout of its own.
_WATCHDOG = Watchdog()

#: How long a page that must be refused is given to settle: enough to show it never does.
_REFUSAL_TIMEOUT = 3.0

#: What a settle that stilled the page's one endless loop counts.
_PAUSED_ONE = Motion(finished=0, paused=1, running=0)

#: Keyframes moving nothing but a box's `transform`: a loop of them changes the box's bounding
#: box every frame while moving no layout.
_WAG = "from { transform: rotate(-8deg); } to { transform: rotate(8deg); }"

#: Keyframes moving a box's `height`, which moves its host, and the paragraph below, with it.
_GROW = "from { height: 20px; } to { height: 60px; }"


def _looping(keyframes: str) -> str:
    """A shadow root's markup: a box looping forever by `keyframes`, a rule of the root's own."""
    return (
        ":host { display: block; }"
        f" @keyframes loop {{ {keyframes} }}"
        " div { width: 80px; height: 40px; background: #9cf;"
        " animation: loop 0.4s ease-in-out infinite alternate; }"
    )


def _element(tag: str, mode: str, style: str, inside: str = "<div></div>") -> str:
    """A script defining `tag`, whose shadow root of `mode` holds `style` and then `inside`."""
    markup = json.dumps(f"<style>{style}</style>{inside}")
    return (
        f'customElements.define("{tag}", class extends HTMLElement {{\n'
        "  constructor() {\n"
        "    super();\n"
        f'    this.attachShadow({{ mode: "{mode}" }}).innerHTML = {markup};\n'
        "  }\n"
        "});\n"
    )


def _page(*elements: str) -> str:
    """A page holding one `x-outer` between two paragraphs, `elements` defining it and the rest.

    An element used inside another's root is defined first, so it is upgraded as it is parsed.
    """
    return (
        '<!doctype html><html lang="en"><head><title>rooted</title></head>\n'
        "<body><p>A box inside a shadow tree moves forever.</p><x-outer></x-outer>"
        "<p>Text below it.</p>\n<script>\n"
        + "".join(elements)
        + "</script></body></html>\n"
    )


#: The box wags forever inside an OPEN root.
_OPEN_WAG = _page(_element("x-outer", "open", _looping(_WAG)))

#: The box wags forever inside an open root nested in an open root: only the INNER root lists it.
_NESTED_WAG = _page(
    _element("x-inner", "open", _looping(_WAG)),
    _element("x-outer", "open", ":host { display: block; }", "<x-inner></x-inner>"),
)

#: The box grows and shrinks forever inside an open root, moving the layout beneath it.
_OPEN_GROW = _page(_element("x-outer", "open", _looping(_GROW)))

#: The box wags forever inside a CLOSED root, which the page cannot reach.
_CLOSED_WAG = _page(_element("x-outer", "closed", _looping(_WAG)))


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the pinned builds and start the shared engines.

    Cleanups rather than a `tearDownModule`, so a start that fails part way still undoes what
    did start: the shared engines first, then the site, whose stop checks the budget.
    """
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)
    _ENGINES.start()
    unittest.addModuleCleanup(_ENGINES.stop)


def _page_load(case: unittest.TestCase, engine: str, page: str) -> RenderingResult:
    """The page-load settle alone of `page`, scripts on, in the module's browser for `engine`.

    `pass_engine.measure` is that load, with no late-script load after it: what it reports of
    motion and faults is its settle's alone.
    """
    site = scratch(case)
    (site / "rooted.html").write_text(page, encoding="utf-8", newline="\n")
    rendering = Rendering(engine, "/rooted.html", 390, 844, "light", "on")
    with loopback_site.serve_directory(site) as base_url:
        context = pass_engine.open_context(_ENGINES.browser(engine), rendering)
        try:
            with _WATCHDOG.bound(
                pass_engine.rendering_bound(TIMEOUT), f"rendering {rendering.label}"
            ):
                tab = context.new_page()
                return pass_engine.measure(
                    tab,
                    Listeners(tab, base_url),
                    rendering,
                    base_url,
                    timeout=TIMEOUT,
                    prove=False,
                )
        finally:
            # The shared browser outlives the test, so its context must not.
            context.close()


class PageLoadSettleTests(unittest.TestCase):
    """The page-load settle stills and counts an open root's motion in every engine."""

    def _assert_in_every_engine(self, page: str, motion: Motion) -> None:
        """`page` settles in every engine, its settle having counted `motion`."""
        for engine in ENGINES:
            with self.subTest(engine):
                result = _page_load(self, engine, page)
                self.assertEqual(result.faults, ())
                self.assertEqual(result.motion, motion)

    def test_an_endless_loop_inside_an_open_root_is_stilled_and_counted(self) -> None:
        # Measured before the stilling reached roots: the page settled with nothing counted,
        # because the layout signature reads no box inside a shadow tree either.
        self._assert_in_every_engine(_OPEN_WAG, _PAUSED_ONE)

    def test_one_inside_an_open_root_nested_in_an_open_root_too(self) -> None:
        self._assert_in_every_engine(_NESTED_WAG, _PAUSED_ONE)

    def test_a_layout_moving_loop_inside_an_open_root_now_settles(self) -> None:
        # THE REGRESSION. It resizes its host every frame, so a settle that could not reach it
        # refused the page as never settling, in every engine, with nothing counted.
        self._assert_in_every_engine(_OPEN_GROW, _PAUSED_ONE)

    def test_a_loop_inside_a_closed_root_is_neither_stilled_nor_counted(self) -> None:
        # THE DOCUMENTED LIMIT: its host's `shadowRoot` reads null to the page in every engine.
        # A transform loop, so the page still settles rather than waiting out the timeout: it
        # moves no box outside the root, and the layout signature cannot read one inside a
        # closed root, since no page script reaches it.
        self._assert_in_every_engine(_CLOSED_WAG, NO_MOTION)


class LateLoadSettleTests(unittest.TestCase):
    """The late-script load, in Chromium, judges an open root's motion as the document's."""

    def test_an_open_roots_endless_transform_is_stilled_and_the_page_measured(
        self,
    ) -> None:
        # Stilled only if the rule was read: DevTools placed the box INSIDE the root as a node
        # and named the `@keyframes` rule its root's own sheet applies. Unread, the animation
        # would be left running, and counted so.
        outcome = served_late_load(
            self, _ENGINES, {"rooted.html": _OPEN_WAG}, timeout=TIMEOUT
        )
        self.assertEqual(outcome.faults, ())
        self.assertEqual(outcome.late.status, pass_shift.MEASURED)
        self.assertEqual(pass_shift.late_findings(outcome.late), ())
        self.assertEqual(outcome.motion, _PAUSED_ONE)

    def test_an_open_roots_layout_loop_is_left_running_counted_and_refused(
        self,
    ) -> None:
        # Never stilled here, as a document one is not: a seek of it would be a layout shift
        # charged to the scripts. It is counted now, where before it was not seen at all.
        outcome = served_late_load(
            self, _ENGINES, {"rooted.html": _OPEN_GROW}, timeout=_REFUSAL_TIMEOUT
        )
        self.assertEqual(outcome.late.status, pass_shift.NOT_MEASURED, outcome.late)
        (fault,) = outcome.faults
        self.assertIn(f"still changing after {_REFUSAL_TIMEOUT:g}s", fault)
        self.assertEqual(outcome.motion, Motion(finished=0, paused=0, running=1))


if __name__ == "__main__":
    unittest.main()
