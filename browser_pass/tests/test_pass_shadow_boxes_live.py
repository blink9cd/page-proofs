"""Grouped suite: boxes INSIDE shadow trees, read LIVE by the page-load settle and detectors.

A selector query on the document crosses into no shadow root, so the layout signature and the
element detectors read the document's elements and every OPEN root's, found recursively through
each host's `shadowRoot`, in Chromium, Firefox and WebKit alike. So a box a script moves forever
inside an open root keeps the page from settling, and a box past the right edge or a target too
small to tap inside one is found. A CLOSED root is out of the page's reach (its host's
`shadowRoot` reads null), so none of that is seen inside one: the documented limit, pinned here.
Each page is served alone on loopback; one browser per engine serves the module, each test in a
fresh context. `_live_fixtures.py` holds the setup.
"""

# gate-serves: none (temporary trees, and its own samples/site, gated with its tool)

from __future__ import annotations

import json
import unittest

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ and the
# shared loopback server on sys.path for every import after it.
from _live_fixtures import TIMEOUT, LiveSite, SharedEngines, scratch

import loopback_site
import pass_engine
from pass_listeners import Listeners
from pass_report import RenderingResult
from pass_targets import ENGINES, Rendering
from pass_watchdog import Watchdog

#: The engine launches this module may make (`LiveSite` fails it past them): one browser per
#: engine, shared by every test.
_LAUNCH_BUDGET = {"chromium": 1, "firefox": 1, "webkit": 1}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)
_ENGINES = SharedEngines()

#: Bounds each page-load settle as `SharedEngines.render` bounds a rendering: Playwright gives
#: `evaluate` no timeout of its own.
_WATCHDOG = Watchdog()

#: How long a page that must be refused is given to settle: enough to show it never does.
_REFUSAL_TIMEOUT = 3.0

#: The phone viewport each rendering here uses; the planted boxes are placed against its width.
_WIDTH = 390

#: A box a script moves forever, by a `transform` set every 50 ms: no animation the settle could
#: still, and nothing outside the root moves with it, so only a read inside the root sees it.
_WANDER = (
    "<div></div>",
    "const box = root.querySelector('div');"
    " let step = 0;"
    " setInterval(() => { step += 1;"
    " box.style.transform = `translateX(${step % 40}px)`; }, 50);",
)

#: A fixed box past the right edge: fixed, so it sits there without widening the page.
_PAST_EDGE = (
    f'<div style="position: fixed; left: {_WIDTH + 10}px; top: 0;'
    ' width: 20px; height: 20px"></div>',
    "",
)

#: A button too small to tap.
_SMALL = (
    '<button style="display: block; width: 10px; height: 10px; padding: 0;'
    ' border: 0; min-width: 0; min-height: 0"></button>',
    "",
)


def _page(mode: str, inside: tuple[str, str]) -> str:
    """A page holding one `x-host`, whose shadow root of `mode` holds `inside`'s markup.

    `inside` is the root's markup and a statement run once the root is filled, which reaches
    the root as `root`: the element's own handle, the one way a script reaches a closed root.
    """
    markup, after = inside
    return (
        '<!doctype html><html lang="en"><head><title>rooted</title></head>\n'
        "<body><p>A box inside a shadow tree.</p><x-host></x-host><p>Text below it.</p>\n"
        "<script>\n"
        'customElements.define("x-host", class extends HTMLElement {\n'
        "  constructor() {\n"
        "    super();\n"
        f'    const root = this.attachShadow({{ mode: "{mode}" }});\n'
        f"    root.innerHTML = {json.dumps(markup)};\n"
        f"    {after}\n"
        "  }\n"
        "});\n"
        "</script></body></html>\n"
    )


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the pinned builds and start the shared engines.

    Cleanups rather than a `tearDownModule`, so a start that fails part way still undoes what
    did start: the shared engines first, then the site, whose stop checks the budget.
    """
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)
    _ENGINES.start()
    unittest.addModuleCleanup(_ENGINES.stop)


def _page_load(
    case: unittest.TestCase, engine: str, page: str, timeout: float = TIMEOUT
) -> RenderingResult:
    """The page-load settle and detectors alone of `page`, scripts on, in `engine`'s browser."""
    site = scratch(case)
    (site / "rooted.html").write_text(page, encoding="utf-8", newline="\n")
    rendering = Rendering(engine, "/rooted.html", _WIDTH, 844, "light", "on")
    with loopback_site.serve_directory(site) as base_url:
        context = pass_engine.open_context(_ENGINES.browser(engine), rendering)
        try:
            with _WATCHDOG.bound(
                pass_engine.rendering_bound(timeout), f"rendering {rendering.label}"
            ):
                tab = context.new_page()
                return pass_engine.measure(
                    tab,
                    Listeners(tab, base_url),
                    rendering,
                    base_url,
                    timeout=timeout,
                    prove=False,
                )
        finally:
            # The shared browser outlives the test, so its context must not.
            context.close()


class OpenRootTests(unittest.TestCase):
    """Inside an open root, in every engine, a box is read as the document's are."""

    def test_a_box_a_script_moves_forever_keeps_the_page_from_settling(self) -> None:
        # Measured before the reads reached roots: the page settled at once, mid-motion.
        for engine in ENGINES:
            with self.subTest(engine):
                result = _page_load(
                    self, engine, _page("open", _WANDER), _REFUSAL_TIMEOUT
                )
                (fault,) = result.faults
                self.assertIn(f"still changing after {_REFUSAL_TIMEOUT:g}s", fault)

    def test_a_box_past_the_right_edge_is_found_through_its_host(self) -> None:
        for engine in ENGINES:
            with self.subTest(engine):
                result = _page_load(self, engine, _page("open", _PAST_EDGE))
                self.assertEqual(result.faults, ())
                self.assertEqual(
                    result.findings.get("past_right_edge"),
                    ("body>x-host:1>#shadow-root>div:1 right edge at 420px",),
                )

    def test_a_target_too_small_to_tap_is_found_through_its_host(self) -> None:
        for engine in ENGINES:
            with self.subTest(engine):
                result = _page_load(self, engine, _page("open", _SMALL))
                self.assertEqual(result.faults, ())
                self.assertEqual(
                    result.findings.get("small_target"),
                    ("body>x-host:1>#shadow-root>button:1 10x10",),
                )


class ClosedRootTests(unittest.TestCase):
    """Inside a CLOSED root, in every engine, nothing is read: the documented limit."""

    def test_a_box_a_script_moves_forever_goes_unseen_and_the_page_settles(
        self,
    ) -> None:
        for engine in ENGINES:
            with self.subTest(engine):
                result = _page_load(self, engine, _page("closed", _WANDER))
                self.assertEqual(result.faults, ())

    def test_neither_the_box_past_the_edge_nor_the_small_target_is_found(self) -> None:
        for engine in ENGINES:
            for inside, detector in (
                (_PAST_EDGE, "past_right_edge"),
                (_SMALL, "small_target"),
            ):
                with self.subTest(engine=engine, detector=detector):
                    result = _page_load(self, engine, _page("closed", inside))
                    self.assertEqual(result.faults, ())
                    self.assertNotIn(detector, result.findings)


if __name__ == "__main__":
    unittest.main()
