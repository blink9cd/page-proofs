"""Suite: a state's `set_attribute` action, as a targets file holds it and as each engine runs it.

A `set_attribute` sets one attribute on the first element its selector matches, without
waiting and without printing: the step a page's own "print this way" button takes before it
calls `window.print()`, which one engine's WebDriver click cannot survive. Its table is
parsed whole, every key checked; the report names it with its attribute; a selector that
matches nothing is the same finding a failed click is, under either engine, and stops the
state.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_actions  # noqa: E402 - the tool's private src/, via the insert above
from _gecko_state_fixtures import StateCase  # noqa: E402 - beside this module
from _print_engine_fixtures import (  # noqa: E402 - as above
    StateTestCase,
    state,
    timed_bound,
)
from _print_playwright_fixtures import FakeScript, call  # noqa: E402 - as above
from _print_targets_fixtures import state_doc  # noqa: E402 - as above
from print_actions import SET_ATTRIBUTE_SOURCE, Action  # noqa: E402 - src/
from print_record import Problem  # noqa: E402 - as above
from print_targets import TargetsError, parse_targets  # noqa: E402 - as above

#: The path to the first action of the first state, as a refusal names it.
_A0 = "target[0].state[0].actions[0]"

#: A bare attribute set on the root, as a page's own "print this way" button might set it.
_THEME = Action("set_attribute", ":root", None, ("data-print-theme", ""))

#: Text holding a control character: a newline, a return, a tab, NUL, DEL, and one of C1.
_CONTROLLED = ("#a\nb", "#a\r", "#a\tb", "\x00", "#a\x7f", "#a\x85b")

#: The finding a `set_attribute` whose selector matches nothing stops its state with.
_UNMATCHED = 'action did not complete: set_attribute "#gone"'


def _parsed(action: object) -> Action:
    """The one action of a targets file whose one state runs `action`."""
    (target,) = parse_targets(state_doc(actions=[action])).targets
    ((parsed,),) = (s.actions for s in target.states)
    return parsed


class ParseTests(unittest.TestCase):
    """A `set_attribute` table: a selector, an attribute name, and an optional value."""

    def test_a_name_and_selector_set_an_empty_value(self) -> None:
        held = {"set_attribute": {"selector": ":root", "name": "data-print-theme"}}
        self.assertEqual(_parsed(held), _THEME)

    def test_a_value_is_kept_as_written(self) -> None:
        held = {"set_attribute": {"selector": "main", "name": "lang", "value": "en"}}
        self.assertEqual(
            _parsed(held), Action("set_attribute", "main", None, ("lang", "en"))
        )

    def refused(self, action: object, where: str) -> None:
        """A state whose one action is `action` is refused, its message opening `where`."""
        with self.assertRaises(TargetsError) as caught:
            _parsed(action)
        self.assertTrue(
            str(caught.exception).startswith(f"{where}:"), str(caught.exception)
        )

    def test_a_set_attribute_that_is_not_a_table_is_refused(self) -> None:
        self.refused({"set_attribute": ":root"}, f"{_A0}.set_attribute")

    def test_each_required_key_is_required(self) -> None:
        for key in ("selector", "name"):
            held = {"selector": ":root", "name": "data-x"}
            del held[key]
            with self.subTest(key=key):
                self.refused({"set_attribute": held}, f"{_A0}.set_attribute.{key}")

    def test_an_unknown_key_is_refused_by_name(self) -> None:
        held = {"selector": ":root", "name": "data-x", "values": ""}
        self.refused({"set_attribute": held}, f"{_A0}.set_attribute.values")

    def test_a_name_no_page_could_set_is_refused(self) -> None:
        # `setAttribute` throws on a name outside XML's Name production: refused here, by
        # its key, rather than as a finding once the run has started.
        for name in ("", "1st", "data x", "data=x", "<b>", 3):
            with self.subTest(name=name):
                held = {"selector": ":root", "name": name}
                self.refused({"set_attribute": held}, f"{_A0}.set_attribute.name")

    def test_a_value_that_is_not_a_string_is_refused(self) -> None:
        held = {"selector": ":root", "name": "data-x", "value": True}
        self.refused({"set_attribute": held}, f"{_A0}.set_attribute.value")

    def test_an_empty_selector_is_refused(self) -> None:
        held = {"selector": "", "name": "data-x"}
        self.refused({"set_attribute": held}, f"{_A0}.set_attribute.selector")

    def refused_control(self, action: object, where: str) -> None:
        """`action` is refused at `where`, its message naming the control character."""
        self.refused(action, where)
        with self.assertRaises(TargetsError) as caught:
            _parsed(action)
        self.assertIn("control character", str(caught.exception))

    def test_a_control_character_in_a_selector_is_refused(self) -> None:
        # A newline or other control character would split or disguise the report's line.
        for kind in ("click", "wait_for"):
            for selector in _CONTROLLED:
                with self.subTest(kind=kind, selector=selector):
                    self.refused_control({kind: selector}, f"{_A0}.{kind}")
        for selector in _CONTROLLED:
            with self.subTest(kind="set_attribute", selector=selector):
                held = {"selector": selector, "name": "data-x"}
                where = f"{_A0}.set_attribute.selector"
                self.refused_control({"set_attribute": held}, where)

    def test_a_control_character_in_a_value_is_refused(self) -> None:
        for value in _CONTROLLED:
            with self.subTest(value=value):
                held = {"selector": ":root", "name": "data-x", "value": value}
                where = f"{_A0}.set_attribute.value"
                self.refused_control({"set_attribute": held}, where)

    def test_printable_text_beyond_ascii_is_kept(self) -> None:
        held = {"selector": "[lang=fr]", "name": "title", "value": "café –"}
        parsed = _parsed({"set_attribute": held})
        self.assertEqual(parsed.attribute, ("title", "café –"))


class TextTests(unittest.TestCase):
    """How the report writes each action: its kind, then what it acts on."""

    def test_each_kind_reads_as_the_report_writes_it(self) -> None:
        cases = {
            Action("click", "#start", None): "click #start",
            Action("wait_for", "#ready", None): "wait_for #ready",
            Action("wait_ms", None, 300): "wait_ms 300",
            _THEME: 'set_attribute :root data-print-theme=""',
            Action("set_attribute", "main", None, ("lang", "en")): (
                'set_attribute main lang="en"'
            ),
            # The value is written as a JSON string: a quote in it cannot end it early.
            Action("set_attribute", "main", None, ("title", 'a" b="c')): (
                r'set_attribute main title="a\" b=\"c"'
            ),
            # A non-ASCII value stays readable rather than escaped.
            Action("set_attribute", "main", None, ("title", "café ✓")): (
                'set_attribute main title="café ✓"'
            ),
        }
        for action, text in cases.items():
            with self.subTest(text=text):
                self.assertEqual(action.text(), text)

    def test_a_failure_names_the_kind_and_its_selector_or_time(self) -> None:
        self.assertEqual(
            Action("wait_ms", None, 250).failure(),
            'action did not complete: wait_ms "250"',
        )
        self.assertEqual(
            _THEME.failure(), 'action did not complete: set_attribute ":root"'
        )


class _Page:
    """A page that records each `evaluate` and answers it with `answer`."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[tuple[str, object]] = []

    def evaluate(self, expression: str, arg: object = None) -> object:
        self.calls.append((expression, arg))
        return self.answer


class SetAttributeTests(unittest.TestCase):
    """`set_attribute` hands the page its script and refuses anything but a plain `true`."""

    def test_the_script_is_handed_the_selector_name_and_value(self) -> None:
        page = _Page(True)
        print_actions.set_attribute(page, _THEME)
        self.assertEqual(
            page.calls, [(SET_ATTRIBUTE_SOURCE, [":root", "data-print-theme", ""])]
        )

    def test_anything_but_true_is_an_unmatched_selector(self) -> None:
        for answer in (False, None, 1, "true"):
            with self.subTest(answer=answer):
                with self.assertRaises(print_actions.ActionError):
                    print_actions.set_attribute(_Page(answer), _THEME)

    def test_an_action_of_another_kind_is_a_programming_error(self) -> None:
        with self.assertRaises(ValueError):
            print_actions.set_attribute(_Page(True), Action("click", "#go", None))


class ChromiumTests(StateTestCase):
    """Under Playwright, the script runs in the page, bounded at the timeout."""

    def test_the_attribute_is_set_in_the_page_inside_its_bound(self) -> None:
        run, record = self.run_one(state(actions=(_THEME,)))
        evaluated = call("page.evaluate", "set_attribute", ":root", "data-print-theme")
        self.assertEqual(run.named("page.evaluate")[0], evaluated)
        before, after = run.around(evaluated)
        self.assertEqual(before[:2], ("bound", "page.evaluate home/default"))
        self.assertEqual(after, ("unbound", "page.evaluate home/default"))
        self.assertNotEqual(before, timed_bound("page.evaluate home/default")[0])
        self.assertEqual(record.problems, ())
        self.assertEqual(len(record.pdfs), 1)

    def test_a_selector_matching_nothing_stops_the_state(self) -> None:
        script = FakeScript(failing_selectors=frozenset({"#gone"}))
        gone = Action("set_attribute", "#gone", None, ("data-x", ""))
        _, record = self.run_one(state(actions=(gone,)), script)
        self.assertEqual(record.problems, (Problem("finding", _UNMATCHED),))
        self.assertEqual(record.pdfs, ())


class FirefoxTests(StateCase):
    """Under Firefox, the script runs as one synchronous WebDriver script, with no look."""

    def test_the_attribute_is_set_with_one_script_and_no_look(self) -> None:
        record = self.run_state(job=self.job(state(actions=(_THEME,))))
        names = [entry[0] for entry in self.log]
        self.assertIn(
            call("execute set_attribute", [":root", "data-print-theme", ""]), self.log
        )
        self.assertNotIn("execute visible", names)
        self.assertEqual(record.problems, ())

    def test_a_selector_matching_nothing_stops_the_state(self) -> None:
        session = self.session(unmatched=frozenset({"#gone"}))
        gone = Action("set_attribute", "#gone", None, ("data-x", ""))
        record = self.run_state(session, self.job(state(actions=(gone,))))
        self.assertEqual(record.problems, (Problem("finding", _UNMATCHED),))
        self.assertEqual(record.pdfs, ())


if __name__ == "__main__":  # pragma: no cover - run by discovery or the aggregator
    unittest.main()
