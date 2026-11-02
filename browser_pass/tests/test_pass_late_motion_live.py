"""Grouped suite: endless motion in the late-script load, LIVE in the pinned Chromium.

The late load stills an animation only when it is PROVABLY layout-inert: a seek of one moving
layout would itself be a layout shift, charged to the scripts. What a CSS animation moves is read
from the `@keyframes` rule the cascade applies to it, which Chromium's style resolver names over
the load's DevTools session (`pass_inert`), since `getKeyframes()` leaves custom properties out
and the rule may sit where no page script can read it. Each page here carries one kind of endless
motion, served alone, and the load must measure it at rest or refuse it as still changing. One
browser serves the module, each test in a fresh context; `_live_fixtures.py` holds the setup.
"""

from __future__ import annotations

import unittest
from unittest import mock

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ and the
# shared loopback server on sys.path for every import after it.
from _live_fixtures import TIMEOUT, LiveSite, SharedEngines, served_late_load

import pass_inert
import pass_late_load
import pass_shift
from pass_motion import Motion
from playwright.sync_api import CDPSession, Page

#: The engine launches this module may make (`LiveSite` fails it past them): one Chromium,
#: shared by every test, as the tool itself shares one across its renderings.
_LAUNCH_BUDGET = {"chromium": 1}
_SITE = LiveSite(launch_budget=_LAUNCH_BUDGET)
_ENGINES = SharedEngines()

#: How long a page that must be refused is given to settle: enough to show it never does.
_REFUSAL_TIMEOUT = 3.0

#: A page whose one box wags forever, `infinite alternate`, moving nothing but its `transform`.
#: Its bounding box changes every frame, so a settle that left it running would never settle,
#: although nothing in the page's layout moves. Its first keyframe carries a timing function of
#: its own, which the `@keyframes` rule lists among its declarations although it moves nothing.
_WAGGING_PAGE = """<!doctype html><html lang="en"><head><title>wagging</title>
<style>
@keyframes wag {
  from { transform: rotate(-8deg); animation-timing-function: ease-in; }
  to { transform: rotate(8deg); }
}
#wag { width: 80px; height: 40px; background: #9cf; animation: wag 0.4s ease-in-out infinite alternate; }
</style></head>
<body><p>A box that wags forever.</p><div id="wag"></div><p>Text below it.</p></body></html>
"""

#: The same box moving forever by its `margin-top` instead, which moves the layout beneath it.
#: Its neighbors have no margins: a paragraph's would collapse with it, and hold the box still
#: while its own margin was the smaller: long enough, measured, to read as settled.
_BOBBING_PAGE = """<!doctype html><html lang="en"><head><title>bobbing</title>
<style>
p { margin: 0; }
@keyframes bob { from { margin-top: 0; } to { margin-top: 40px; } }
#bob { width: 80px; height: 40px; background: #9cf; animation: bob 0.4s ease-in-out infinite alternate; }
</style></head>
<body><p>A box that bobs forever.</p><div id="bob"></div><p>Text below it.</p></body></html>
"""

#: The same box moved forever by a CUSTOM property its `margin-top` reads, so the layout beneath
#: it moves as the bob's does. Chromium leaves custom properties out of a CSS animation's
#: `getKeyframes()`, so only the `@keyframes` rule says what it moves. Registered, so it moves
#: every frame rather than flipping halfway; the neighbors' margins are zeroed as the bob's are.
_DRIFTING_PAGE = """<!doctype html><html lang="en"><head><title>drifting</title>
<style>
@property --m { syntax: "<length>"; inherits: false; initial-value: 0px; }
p { margin: 0; }
@keyframes drift { from { --m: 0px; } to { --m: 40px; } }
#drift { width: 80px; height: 40px; background: #9cf; margin-top: var(--m); animation: drift 0.4s ease-in-out infinite alternate; }
</style></head>
<body><p>A box a custom property moves forever.</p><div id="drift"></div><p>Text below it.</p>
</body></html>
"""

#: What every DECOY page shares: the registered `--m`, neighbors with no margins, and a document
#: rule called `drift` moving only `transform`, the rule a search of the page's own sheets finds
#: first, while the cascade applies another `drift`, moving `--m`, from where that search cannot
#: see. BOX is the moving box's declarations, APPLIED the rule that truly moves it.
_DECOY_HEAD = """<style>
@property --m { syntax: "<length>"; inherits: false; initial-value: 0px; }
@keyframes drift { to { transform: rotate(2deg); } }
p { margin: 0; }
</style>"""
_BOX = (
    "width: 80px; height: 40px; background: #9cf; margin-top: var(--m);"
    " animation: drift 0.4s ease-in-out infinite alternate;"
)
_APPLIED = "@keyframes drift { from { --m: 0px; } to { --m: 40px; } }"

#: A custom element whose shadow root, MODE open or closed, moves the element itself: the `:host`
#: rule's `drift` is the shadow root's own.
_HOST_PAGE = """<!doctype html><html lang="en"><head><title>host</title>DECOY</head>
<body><p>A box its own shadow root moves forever.</p><x-box></x-box><p>Text below it.</p>
<script>
customElements.define("x-box", class extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "MODE" }).innerHTML =
      "<style>:host { display: block; BOX } APPLIED</style>";
  }
});
</script></body></html>
"""

#: A box in the page's own markup, moved by the CLOSED shadow root it is slotted into.
_SLOTTED_PAGE = """<!doctype html><html lang="en"><head><title>slotted</title>DECOY</head>
<body><p>A box a closed shadow root moves forever.</p><x-slot><div>box</div></x-slot>
<p>Text below it.</p>
<script>
customElements.define("x-slot", class extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "closed" }).innerHTML =
      "<style>:host { display: block; } ::slotted(div) { BOX } APPLIED</style><slot></slot>";
  }
});
</script></body></html>
"""

#: A box moved by a rule in a sheet the page `@import`s AFTER the decoy, so the cascade applies
#: the imported one: imported first, the decoy would be the one that applies (measured).
_IMPORTED_PAGE = """<!doctype html><html lang="en"><head><title>imported</title>DECOY
<style>@import url("drift.css");</style><style>#drift { BOX }</style></head>
<body><p>A box an imported rule moves forever.</p><div id="drift"></div><p>Text below it.</p>
</body></html>
"""


def _decoyed(page: str, mode: str = "") -> str:
    """`page` with the decoy, the moving box and the rule that truly moves it filled in."""
    return (
        page.replace("DECOY", _DECOY_HEAD)
        .replace("MODE", mode)
        .replace("BOX", _BOX)
        .replace("APPLIED", _APPLIED)
    )


#: A box whose `::before` wags forever, moving nothing but its `transform`. Its animation targets
#: the box with a pseudo-element, whose own node holds the rule; the box applies none.
_WAGGING_BEFORE_PAGE = """<!doctype html><html lang="en"><head><title>wagging before</title>
<style>
@keyframes wag { from { transform: rotate(-8deg); } to { transform: rotate(8deg); } }
#wag::before { content: ""; display: block; width: 80px; height: 40px; background: #9cf; animation: wag 0.4s ease-in-out infinite alternate; }
</style></head>
<body><p>A box whose ::before wags forever.</p><div id="wag"></div><p>Text below it.</p>
</body></html>
"""

#: A CSS animation whose rule moves only `transform`, until the page's own script gives it
#: keyframes moving `margin-top` instead: the rule still reads as before, and getKeyframes()
#: reports what the animation now moves.
_REKEYED_PAGE = """<!doctype html><html lang="en"><head><title>rekeyed</title>
<style>
p { margin: 0; }
@keyframes wag { from { transform: rotate(-8deg); } to { transform: rotate(8deg); } }
#rekeyed { width: 80px; height: 40px; background: #9cf; animation: wag 0.4s ease-in-out infinite alternate; }
</style></head>
<body><p>A box a script moves forever through its CSS animation.</p><div id="rekeyed"></div>
<p>Text below it.</p>
<script>
for (const animation of document.getAnimations()) {
  animation.effect.setKeyframes([{ marginTop: "0px" }, { marginTop: "40px" }]);
}
</script></body></html>
"""

#: A page with no motion at all: what a poll costs with nothing to judge.
_STILL_PAGE = """<!doctype html><html lang="en"><head><title>still</title></head>
<body><p>Nothing here moves.</p></body></html>
"""

#: The calls that place an animation's target, or the pseudo-element of it, and read its rule.
_RESOLVING = frozenset(
    {
        "DOM.requestNode",
        "DOM.describeNode",
        "DOM.pushNodesByBackendIdsToFrontend",
        "CSS.getMatchedStylesForNode",
    }
)


def setUpModule() -> None:  # noqa: N802 - unittest's own hook name
    """Point Playwright at the pinned builds and start the shared engine.

    Cleanups rather than a `tearDownModule`, so a start that fails part way still undoes
    what did start: the shared engine first, then the site, whose stop checks the budget.
    """
    _SITE.start()
    unittest.addModuleCleanup(_SITE.stop)
    _ENGINES.start()
    unittest.addModuleCleanup(_ENGINES.stop)


def _served(
    case: unittest.TestCase,
    files: dict[str, str],
    *,
    timeout: float = _REFUSAL_TIMEOUT,
    prove: bool = False,
) -> pass_late_load.LateOutcome:
    """The late-script load of the first of `files`, in the module's Chromium."""
    return served_late_load(case, _ENGINES, files, timeout=timeout, prove=prove)


class _RecordedSession:
    """A DevTools session that notes the method of each call sent through it, then sends it."""

    def __init__(self, session: CDPSession, sent: list[str]) -> None:
        self._session = session
        self._sent = sent

    def send(self, method: str, params: dict[str, object] | None = None) -> object:
        """Note `method`, then send the call on."""
        self._sent.append(method)
        return self._session.send(method, params)

    def detach(self) -> None:
        """End the session."""
        self._session.detach()


def _served_polls(
    case: unittest.TestCase, files: dict[str, str], *, timeout: float
) -> tuple[pass_late_load.LateOutcome, set[tuple[str, ...]]]:
    """The late-script load of `files`, and each distinct poll's calls over its session.

    A poll begins by asking for the document; the calls before the first opened the session.
    """
    sent: list[str] = []
    opened = pass_inert.open_session

    def recorded(page: Page) -> _RecordedSession:
        session = opened(page)
        assert session is not None, "Chromium offers a DevTools session"
        return _RecordedSession(session, sent)

    with mock.patch.object(pass_inert, "open_session", recorded):
        outcome = _served(case, files, timeout=timeout)
    polls: list[list[str]] = []
    for method in sent:
        if method == "DOM.getDocument":
            polls.append([])
        if polls:
            polls[-1].append(method)
    return outcome, {tuple(poll) for poll in polls}


def _assert_refused(
    case: unittest.TestCase, outcome: pass_late_load.LateOutcome
) -> None:
    """`outcome` refused as still changing, its one animation left running."""
    # The status first: the defects these guard against MEASURE the page, and it says so here.
    case.assertEqual(outcome.late.status, pass_shift.NOT_MEASURED, outcome.late)
    (fault,) = outcome.faults
    case.assertIn(f"still changing after {_REFUSAL_TIMEOUT:g}s", fault)
    case.assertEqual(outcome.motion, Motion(finished=0, paused=0, running=1))


class StilledMotionTests(unittest.TestCase):
    """Endless motion that moves no layout is stilled, and the page measured at rest.

    A seek of an animation moving only layout-inert properties moves no layout, so the Layout
    Instability API reports nothing for it and the scripts are charged with nothing.
    """

    def _assert_measured_at_rest(self, outcome: pass_late_load.LateOutcome) -> None:
        self.assertEqual(outcome.faults, ())
        self.assertEqual(outcome.late.status, pass_shift.MEASURED)
        # Pausing and seeking it moved nothing a reader would see move.
        self.assertEqual(pass_shift.late_findings(outcome.late), ())
        self.assertEqual(outcome.late.score, 0.0)
        self.assertEqual(outcome.motion, Motion(finished=0, paused=1, running=0))

    def test_an_endless_transform_is_stilled_and_the_page_measured(self) -> None:
        # THE REGRESSION THIS STILLING EXISTS FOR. Measured in the pinned Chromium on a real
        # page: every poll read a new signature while no layout shift was recorded and no box
        # moved, so the page was refused as still changing. Stilled, it is measured, at zero.
        outcome = _served(self, {"wagging.html": _WAGGING_PAGE}, timeout=TIMEOUT)
        self._assert_measured_at_rest(outcome)

    def test_a_plant_beside_a_stilled_endless_transform_is_still_caught(self) -> None:
        # The other direction: stilling the wag must not blind the detector to what DOES move.
        outcome = _served(
            self, {"wagging.html": _WAGGING_PAGE}, timeout=TIMEOUT, prove=True
        )

        self.assertEqual(outcome.faults, ())
        self.assertEqual(outcome.verdict, "caught")

    def test_an_endless_transform_on_a_pseudo_element_is_stilled(self) -> None:
        # The rule is read from the `::before`'s own node: its box applies no animation, so a
        # reading that stopped there would find no rule, and leave the wag running. The layout
        # signature reads no pseudo-element's box, so it is the count that says which happened.
        outcome = _served(
            self, {"wagging-before.html": _WAGGING_BEFORE_PAGE}, timeout=TIMEOUT
        )
        self._assert_measured_at_rest(outcome)


class RefusedMotionTests(unittest.TestCase):
    """Endless motion not provably layout-inert is left running, and the page refused.

    A reader would feel it move, and a seek of it would be a shift charged to the scripts, so it
    is neither stilled nor measured past.
    """

    def test_an_endless_margin_animation_is_left_running_and_the_page_refused(
        self,
    ) -> None:
        _assert_refused(self, _served(self, {"bobbing.html": _BOBBING_PAGE}))

    def test_an_endless_custom_property_moving_layout_is_left_running_and_refused(
        self,
    ) -> None:
        # THE REGRESSION FOR READING THE RULE. getKeyframes() names no property for this
        # animation, so a probe reading that alone took it to move nothing, stilled it, and
        # charged its seek's layout shift to the scripts as a late-shift finding. Its rule
        # names `--m`, which is not layout-inert, so it is left running and the page refused.
        _assert_refused(self, _served(self, {"drifting.html": _DRIFTING_PAGE}))

    def test_keyframes_a_script_gave_a_css_animation_count_beside_its_rule(
        self,
    ) -> None:
        # Its rule moves `transform` alone; what it moves now is `margin-top`.
        _assert_refused(self, _served(self, {"rekeyed.html": _REKEYED_PAGE}))


class AppliedRuleTests(unittest.TestCase):
    """The rule that APPLIES decides, wherever the cascade found it.

    THE REGRESSIONS FOR READING THE APPLIED RULE. Each page's document holds a decoy `drift`
    moving `transform` alone, while the cascade applies a `drift` moving `--m`, which drives the
    box's `margin-top`, from where a search of the page's own sheets cannot reach. A probe that
    took the decoy for proof stilled the box and charged its seek to the scripts.
    """

    def test_a_rule_in_an_open_shadow_roots_host_rule(self) -> None:
        _assert_refused(
            self, _served(self, {"host.html": _decoyed(_HOST_PAGE, "open")})
        )

    def test_a_rule_in_a_closed_shadow_roots_host_rule(self) -> None:
        _assert_refused(
            self, _served(self, {"host.html": _decoyed(_HOST_PAGE, "closed")})
        )

    def test_a_rule_in_the_closed_shadow_root_a_box_is_slotted_into(self) -> None:
        _assert_refused(self, _served(self, {"slotted.html": _decoyed(_SLOTTED_PAGE)}))

    def test_a_rule_in_a_sheet_imported_after_the_decoy(self) -> None:
        files = {"imported.html": _decoyed(_IMPORTED_PAGE), "drift.css": _APPLIED}
        _assert_refused(self, _served(self, files))


class CostTests(unittest.TestCase):
    """A poll asks Chromium about an animation only when its rule could change its verdict."""

    def test_motion_its_keyframes_leave_running_costs_a_poll_no_call_of_its_own(
        self,
    ) -> None:
        # Its getKeyframes() names `margin-top`, so it is left running whatever its rule
        # declares (the rule's names only add to those), and reading the rule decides
        # nothing. Read anyway, every such animation cost every poll a resolution of its own:
        # measured on a real page, seconds a poll for a few hundred of them.
        bobbing, bobbing_polls = _served_polls(
            self, {"bobbing.html": _BOBBING_PAGE}, timeout=_REFUSAL_TIMEOUT
        )
        _assert_refused(self, bobbing)
        sent = {method for poll in bobbing_polls for method in poll}
        self.assertEqual(sent & _RESOLVING, set(), bobbing_polls)
        # Each of its polls costs what one costs on a page with no motion at all.
        still, still_polls = _served_polls(
            self, {"still.html": _STILL_PAGE}, timeout=TIMEOUT
        )
        self.assertEqual(still.faults, ())
        self.assertEqual(bobbing_polls, still_polls)


if __name__ == "__main__":
    unittest.main()
