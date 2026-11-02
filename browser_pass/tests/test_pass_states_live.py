"""Grouped suite: the live boundary for states (a page reached by a click, in the pinned engines).

`samples/site/popover.html` opens a panel from a button, with no script: the browser's own
popover. The panel holds a button too small to tap, so a load of the page reports nothing and
the same page reached by a click reports exactly that button, which is what proves the
detectors ran after the actions, in each engine, rather than on the page as it loaded. The state's
page carries a query, which the server answers with the page's own file. Beside it: an action
that cannot complete is a fault naming it, and stored values are in place on the served origin.
`_live_fixtures.py` holds the setup the live suites share.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ and the
# shared loopback server on sys.path for every import after it.
from _live_fixtures import TIMEOUT, LiveSite, SharedEngines

import unittest

import pass_engine
import pass_shift
import pass_states
from pass_probe import MS_PER_SECOND
from pass_targets import ENGINES, Action, Rendering, State

#: The engine launches this module may make (`LiveSite` fails it past them): one browser per
#: engine, shared by every test here.
_LAUNCH_BUDGET = {"chromium": 1, "firefox": 1, "webkit": 1}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)

#: The sample page's panel opened by its button, then waited for until it shows.
_OPENED = State(
    name="opened",
    page="/popover.html?from=state",
    storage=(),
    actions=(Action("click", "#open", None), Action("wait_for", "#panel", None)),
)

#: The one fault the opened panel holds, as the small-target detector names it.
_REVEALED = "body>div:1#panel>button:1 16x16"

#: Bound on each page step for a state whose action is meant to time out: short, so the wait is.
_SHORT_TIMEOUT = 3.0


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the pinned builds and serve the sample site for the whole module."""
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)


class StateLiveTests(unittest.TestCase):
    """A state in every engine: reached by its actions, then measured and proved there."""

    engines = SharedEngines()

    @classmethod
    def setUpClass(cls) -> None:
        """Start the class's driver; `setUpModule` has already pointed it at the builds."""
        cls.engines.start()
        cls.addClassCleanup(cls.engines.stop)

    def test_a_click_reveals_what_every_detector_then_reads_in_every_engine(
        self,
    ) -> None:
        for engine in ENGINES:
            loaded = Rendering(engine, "/popover.html", 390, 844, "light", "on")
            opened = Rendering(engine, _OPENED.page, 390, 844, "light", "on", "opened")
            with self.subTest(engine=engine):
                before = self.engines.render(loaded, _SITE.base_url)
                after = self.engines.render(
                    opened, _SITE.base_url, prove=True, state=_OPENED
                )
                self.assertEqual((before.faults, before.findings), ((), {}))
                self.assertEqual(after.faults, ())
                self.assertEqual(after.findings, {"small_target": (_REVEALED,)})
                verdicts = dict(after.verdicts)
                self.assertEqual(verdicts.pop("late_shift"), pass_shift.NOT_APPLICABLE)
                self.assertEqual(set(verdicts.values()), {"caught"})
                self.assertEqual(after.late_shift.status, pass_shift.NOT_APPLICABLE)

    def test_an_action_that_cannot_complete_is_a_fault_naming_it(self) -> None:
        missing = State(
            name="missing",
            page="/popover.html",
            storage=(),
            actions=(Action("click", "#open", None), Action("wait_for", "#gone", None)),
        )
        result = self.engines.render(
            Rendering("chromium", "/popover.html", 390, 844, "light", "on", "missing"),
            _SITE.base_url,
            timeout=_SHORT_TIMEOUT,
            state=missing,
        )
        self.assertEqual(
            result.faults, ('state missing: action 2 (wait_for "#gone") timed out',)
        )
        self.assertEqual(result.findings, {})

    def test_stored_values_are_in_place_on_the_served_origin(self) -> None:
        stored = State(
            name="stored", page="/clean.html", storage=(("mode", "dark"),), actions=()
        )
        for engine in ENGINES:
            rendering = Rendering(engine, "/clean.html", 390, 844, "light", "on")
            with self.subTest(engine=engine):
                context = pass_engine.open_context(
                    self.engines.browser(engine),
                    rendering,
                    storage=pass_states.storage_state(stored, _SITE.base_url),
                )
                try:
                    page = context.new_page()
                    page.goto(
                        _SITE.base_url + stored.page, timeout=TIMEOUT * MS_PER_SECOND
                    )
                    found = page.evaluate("() => localStorage.getItem('mode')")
                finally:
                    # The shared browser outlives this test, so its context must not.
                    context.close()
                self.assertEqual(found, "dark")


if __name__ == "__main__":
    unittest.main()
