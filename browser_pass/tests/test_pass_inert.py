"""Grouped suite: the late-script load's stilling, reading each CSS animation's APPLIED rule.

Chromium's own style resolver says which `@keyframes` rule a CSS animation's keyframes come from
(`CSS.getMatchedStylesForNode`), wherever the rule sits: a shadow root, closed or open, an
imported sheet. The late load's settle (`pass_late_load._settled`) asks it over a DevTools
session, and these tests drive that settle through a page whose session answers with scripted
records and refuses where a browser would. What they read is what the stilling was handed: one
entry per listed animation, the names its applied rule declares, or None where they are unknown
or the listing asked nothing about it -- which the probe then leaves running, unless it has no
rule at all (`test_page_probe_motion_rules.mjs` pins that verdict, and
`test_pass_late_motion_live.py` proves the whole of it in the pinned Chromium) -- and what each
poll cost. The page-load settle must open no session at all.
"""

from __future__ import annotations

import unittest
from unittest import mock

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ on sys.path
# for every import after it.
from _inert_fixtures import (
    LISTED,
    LISTING,
    TIMEOUT,
    FakePage,
    FakeSession,
    Listed,
    late_settle,
    pseudo_node,
    reading,
    rule,
)

import pass_engine
import pass_probe
from pass_checks import ProbeError
from pass_motion import Motion
from playwright.sync_api import Error as PlaywrightError


class _SessionCase(unittest.TestCase):
    """Reads what one late settle handed the stilling, through a scripted session."""

    def _declared(self, session: FakeSession) -> list[object]:
        """The names handed to the one stilling call, one entry per listed animation."""
        late_settle(FakePage(session))
        return self._stillings(session)[0]

    def _stillings(self, session: FakeSession) -> list[list[object]]:
        """What each stilling over the session was handed, in order."""
        stillings = [
            params
            for method, params in session.calls
            if method == "Runtime.callFunctionOn"
        ]
        self.assertTrue(
            stillings, f"nothing was stilled over the session: {session.calls}"
        )
        declared = []
        for params in stillings:
            arguments = params["arguments"]
            assert isinstance(arguments, list)
            operation, config = arguments[0]["value"]
            self.assertEqual(operation, "stillMotion")
            # The shared configuration, the late load's option, and the names: nothing else.
            self.assertEqual(
                config,
                {
                    **pass_probe.PROBE_CONFIG,
                    "layoutInertOnly": True,
                    "declared": config.get("declared"),
                },
            )
            declared.append(config["declared"])
        return declared


class AppliedRuleTests(_SessionCase):
    """Each CSS animation is judged by the names the rule the cascade applies to it declares."""

    def test_every_keyframe_of_the_applied_rule_counts_the_middle_one_included(
        self,
    ) -> None:
        # `--m` in the 50% keyframe alone: a reading that took the first or the last keyframe
        # would see `transform` only, and still a custom property moving layout.
        session = FakeSession(
            (Listed("drift"),),
            rules={1: [rule("drift", ("transform",), ("--m",), ("transform",))]},
        )
        self.assertEqual(self._declared(session), [["transform", "--m", "transform"]])

    def test_only_the_rule_of_the_animations_own_name_answers_for_it(self) -> None:
        # The resolver lists one rule per animation on the node; each answers for its own name.
        session = FakeSession(
            (Listed("wag"),),
            rules={1: [rule("drift", ("--m",)), rule("wag", ("rotate",))]},
        )
        self.assertEqual(self._declared(session), [["rotate"]])

    def test_no_rule_of_its_name_returned_leaves_it_unknown(self) -> None:
        for rules in ([], [rule("other", ("transform",))]):
            with self.subTest(rules=rules):
                session = FakeSession((Listed("drift"),), rules={1: rules})
                self.assertEqual(self._declared(session), [None])

    def test_a_pseudo_elements_own_rule_is_read_not_its_elements(self) -> None:
        # The element and its `::after` each apply a `wag` moving `margin-top`, and only the
        # `::before`'s own moves `transform` alone: a reading that took the element's rule, or
        # the first pseudo-element it was described with, would leave the `::before` running.
        session = FakeSession(
            (Listed("wag", pseudo="::before"),),
            rules={
                1: [rule("wag", ("margin-top",))],
                pseudo_node(1, "after"): [rule("wag", ("margin-top",))],
                pseudo_node(1, "before"): [rule("wag", ("transform",))],
            },
            pseudos={1: ("after", "before")},
        )
        self.assertEqual(self._declared(session), [["transform"]])

    def test_a_pseudo_element_that_cannot_be_found_leaves_it_unknown(self) -> None:
        session = FakeSession(
            (Listed("wag", pseudo="::before"),),
            rules={1: [rule("wag", ("transform",))]},
            pseudos={1: ("after",)},
        )
        self.assertEqual(self._declared(session), [None])

    def test_a_target_that_cannot_be_placed_leaves_it_unknown(self) -> None:
        # No target at all (a script took the effect away), or one the protocol gives no node.
        for listed in (Listed(node=None), Listed(node=0)):
            with self.subTest(listed=listed):
                session = FakeSession(
                    (listed,), rules={0: [rule("drift", ("rotate",))]}
                )
                self.assertEqual(self._declared(session), [None])

    def test_an_animation_the_listing_asks_nothing_about_costs_no_call_of_its_own(
        self,
    ) -> None:
        # A transition, a CSS animation that does not advance, or one whose getKeyframes()
        # already names a property that leaves it running: however many run, a poll makes the
        # calls it makes with none running at all.
        rules = {1: [rule("drift", ("rotate",))]}
        crowded = FakeSession((Listed(name=None),) * 3, rules=rules)
        empty = FakeSession((), rules=rules)
        self.assertEqual(self._declared(crowded), [None] * 3)
        self.assertEqual(self._declared(empty), [])
        self.assertEqual(
            [method for method, _ in crowded.calls],
            [method for method, _ in empty.calls],
        )

    def test_a_refused_call_leaves_that_animation_unknown_and_the_next_still_read(
        self,
    ) -> None:
        # Two animations on two nodes, each resolved the same way: refusing any step of the
        # first's leaves it unknown, and never the second.
        steps = {
            "its ask": ("Runtime.getProperties", "ask:0"),
            "its node": ("DOM.requestNode", "target:0"),
            "its pseudo-element": ("DOM.describeNode", 1),
            "the pseudo-element's node": (
                "DOM.pushNodesByBackendIdsToFrontend",
                pseudo_node(1, "before"),
            ),
            "its rule": ("CSS.getMatchedStylesForNode", pseudo_node(1, "before")),
        }
        for step, refused in steps.items():
            with self.subTest(refused=step):
                session = FakeSession(
                    (Listed("wag", node=1, pseudo="::before"), Listed("wag", node=2)),
                    rules={
                        pseudo_node(1, "before"): [rule("wag", ("transform",))],
                        2: [rule("wag", ("opacity",))],
                    },
                    pseudos={1: ("before",)},
                    refuses=frozenset({refused}),
                )
                self.assertEqual(self._declared(session), [None, ["opacity"]])


class ListingTests(_SessionCase):
    """The stilling runs over the very list the rules were read for, and each poll reads anew."""

    def test_the_probe_lists_and_stills_the_same_array_its_rules_were_read_from(
        self,
    ) -> None:
        session = FakeSession((Listed("wag"),), rules={1: [rule("wag", ("rotate",))]})
        late_settle(FakePage(session))
        calls = dict(session.calls)
        self.assertIn("Runtime.evaluate", calls, "nothing was listed over the session")
        self.assertEqual(calls["Runtime.evaluate"]["expression"], LISTING)
        self.assertIs(calls["Runtime.evaluate"]["returnByValue"], False)
        group = calls["Runtime.evaluate"]["objectGroup"]
        stilling = calls["Runtime.callFunctionOn"]
        # The probe's own source, called on the listed array and handed it back: no second
        # listing, and no copy of the probe's logic.
        self.assertEqual(stilling["functionDeclaration"], pass_probe.PROBE_SOURCE)
        self.assertEqual(stilling["objectId"], LISTED)
        arguments = stilling["arguments"]
        assert isinstance(arguments, list)
        self.assertEqual(arguments[1], {"objectId": LISTED})
        self.assertIs(stilling["returnByValue"], True)
        # Only once it has stilled are the poll's handles let go.
        methods = [method for method, _ in session.calls]
        self.assertEqual(methods[-1], "Runtime.releaseObjectGroup")
        self.assertEqual(session.calls[-1][1], {"objectGroup": group})

    def test_each_poll_reads_the_page_anew_over_one_session_closed_at_the_end(
        self,
    ) -> None:
        # A script can insert a `@keyframes` rule mid-load, so nothing read is kept.
        session = FakeSession(
            (Listed("wag"),),
            rules={1: [rule("wag", ("rotate",))]},
            stilled=Motion(0, 1, 0),
        )
        page = FakePage(session)
        settled, motion = late_settle(page, polls=3)
        self.assertTrue(settled)
        self.assertEqual(motion, Motion(finished=0, paused=3, running=0))
        self.assertEqual(self._stillings(session), [[["rotate"]]] * 3)
        methods = [method for method, _ in session.calls]
        self.assertEqual(methods[:2], ["DOM.enable", "CSS.enable"])
        for method in (
            "DOM.getDocument",
            "Runtime.evaluate",
            "CSS.getMatchedStylesForNode",
        ):
            self.assertEqual(methods.count(method), 3, method)
        self.assertEqual((page.context.opened, session.detached), (1, 1))
        # Every poll still reads the layout, through the page.
        self.assertEqual(page.asked, [("signature", {})] * 3)

    def test_a_probe_that_throws_over_the_session_fails_the_run_as_any_probe_call_does(
        self,
    ) -> None:
        for operation in ("listMotion", "stillMotion"):
            with self.subTest(operation=operation):
                session = FakeSession(
                    (Listed("wag"),),
                    rules={1: [rule("wag", ("rotate",))]},
                    throws=operation,
                )
                with self.assertRaisesRegex(
                    ProbeError, f"{operation}.*Error: {operation}"
                ):
                    late_settle(FakePage(session))

    def test_a_poll_that_fails_still_lets_its_handles_go_and_the_session_ends(
        self,
    ) -> None:
        # A probe that throws, or a listing or a stilling the protocol refuses, fails the run --
        # and neither the poll's handles nor the session may outlive it.
        wag = (Listed("wag"),)
        rules = {1: [rule("wag", ("rotate",))]}
        failures = {
            "the listing throws": (
                FakeSession(wag, rules=rules, throws="listMotion"),
                ProbeError,
            ),
            "the stilling throws": (
                FakeSession(wag, rules=rules, throws="stillMotion"),
                ProbeError,
            ),
            "the listing is refused": (
                FakeSession(
                    wag, rules=rules, refuses=frozenset({("Runtime.evaluate", None)})
                ),
                PlaywrightError,
            ),
            "the stilling is refused": (
                FakeSession(
                    wag,
                    rules=rules,
                    refuses=frozenset({("Runtime.callFunctionOn", LISTED)}),
                ),
                PlaywrightError,
            ),
        }
        for failure, (session, raised) in failures.items():
            with self.subTest(failure=failure):
                with self.assertRaises(raised):
                    late_settle(FakePage(session))
                # The last call lets the poll's handles go, and the session ended once.
                last, given = session.calls[-1]
                self.assertEqual(
                    (last, session.detached), ("Runtime.releaseObjectGroup", 1)
                )
                group = dict(session.calls)["Runtime.evaluate"]["objectGroup"]
                self.assertEqual(given, {"objectGroup": group})

    def test_a_listing_that_hands_back_no_list_fails_the_run(self) -> None:
        # Nothing at all, or an object holding neither the list nor the asks.
        for returned in ({"type": "undefined"}, {"type": "object", "objectId": "x"}):
            with self.subTest(returned=returned):
                session = FakeSession((), rules={})
                with mock.patch.object(
                    session,
                    "_returning",
                    lambda operation, result, given=returned: {"result": given},
                ):
                    with self.assertRaisesRegex(ProbeError, "listMotion"):
                        late_settle(FakePage(session))


class NoSessionTests(unittest.TestCase):
    """Where no DevTools session can be had, nothing is read; the page-load settle never asks."""

    def test_an_engine_without_a_session_stills_through_the_probe_with_no_rule_read(
        self,
    ) -> None:
        # Firefox and WebKit: every CSS animation's rule is unknown, so the probe leaves it running.
        page = FakePage(None)
        late_settle(page, polls=2)
        self.assertEqual(page.context.opened, 1)
        self.assertEqual(
            page.asked,
            [("stillMotion", {"layoutInertOnly": True}), ("signature", {})] * 2,
        )

    def test_the_page_load_settle_opens_no_session_and_sends_nothing(self) -> None:
        # It stills every animation that advances, whatever it moves, so no rule is its question.
        session = FakeSession((Listed("wag"),), rules={1: [rule("wag", ("rotate",))]})
        page = FakePage(session)
        with mock.patch.object(pass_engine, "wait_until_settled", reading(2)):
            pass_engine._settle(
                page,  # type: ignore[arg-type] # a scripted stand-in for the page
                timeout=TIMEOUT,
            )
        self.assertEqual((page.context.opened, session.calls), (0, []))
        self.assertEqual(page.asked, [("stillMotion", {}), ("signature", {})] * 2)


if __name__ == "__main__":
    unittest.main()
