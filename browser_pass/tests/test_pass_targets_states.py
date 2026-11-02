"""Grouped suite: the targets file's `[[state]]` tables (what a state holds, and each refusal).

A state is a page reached by more than its load: values stored before it, actions run after it.
`test_pass_targets.py` covers the rest of the file and the matrix a state joins.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_targets  # noqa: E402 - the tool's private src/, via the insert above

#: A minimal valid targets document, as `tomllib` would hand it over.
_VALID: dict[str, object] = {
    "pages": ["/"],
    "viewport": [{"width": 390, "height": 844, "rotates": True}],
}

#: A state holding every key a state may: a page with a query, stored values, three actions.
_STATE: dict[str, object] = {
    "name": "after-start",
    "page": "/?panel=1",
    "storage": {"mode": "dark"},
    "actions": [{"click": "#start"}, {"wait_for": "#grid"}, {"wait_ms": 300}],
}


def _with(**changes: object) -> dict[str, object]:
    """The valid document with `changes` applied."""
    return {**_VALID, **changes}


class StateTests(unittest.TestCase):
    """The `[[state]]` tables: a page reached by stored values and actions, each rule refused."""

    def _states(self, *states: object) -> tuple[pass_targets.State, ...]:
        """The states the valid document holds with `states` as its `[[state]]` tables."""
        return pass_targets.parse_targets(_with(state=list(states))).states

    def _refused(self, pattern: str, *states: object) -> None:
        """Assert the document holding `states` is refused, naming `pattern`."""
        with self.assertRaisesRegex(pass_targets.TargetsError, pattern):
            self._states(*states)

    def test_reads_a_state_its_stored_values_and_its_actions_in_order(self) -> None:
        self.assertEqual(
            self._states(_STATE),
            (
                pass_targets.State(
                    name="after-start",
                    page="/?panel=1",
                    storage=(("mode", "dark"),),
                    actions=(
                        pass_targets.Action("click", "#start", None),
                        pass_targets.Action("wait_for", "#grid", None),
                        pass_targets.Action("wait_ms", None, 300),
                    ),
                ),
            ),
        )

    def test_a_state_needs_only_a_name_and_a_page(self) -> None:
        (state,) = self._states({"name": "plain", "page": "/archive/#latest"})
        self.assertEqual((state.storage, state.actions), ((), ()))

    def test_a_file_without_states_has_none(self) -> None:
        self.assertEqual(pass_targets.parse_targets(_VALID).states, ())

    def test_refuses_states_that_are_not_tables(self) -> None:
        with self.assertRaisesRegex(pass_targets.TargetsError, "state must be"):
            pass_targets.parse_targets(_with(state="after-start"))
        self._refused("state 1 is not a table", "after-start")

    def test_refuses_a_state_without_a_name(self) -> None:
        self._refused("state 1 name", {"page": "/"})

    def test_refuses_a_name_outside_its_alphabet(self) -> None:
        for name in ("After-start", "-start", "after_start", "after start", "", 3):
            with self.subTest(name=name):
                self._refused("state 1 name", {"name": name, "page": "/"})

    def test_refuses_a_name_with_a_trailing_newline(self) -> None:
        # Matched whole: a `$` anchor would let this one through.
        self._refused("state 1 name", {"name": "start\n", "page": "/"})

    def test_refuses_a_name_used_twice(self) -> None:
        self._refused(
            "state 2 name 'twin' is already used",
            {"name": "twin", "page": "/"},
            {"name": "twin", "page": "/archive/"},
        )

    def test_refuses_a_page_that_is_not_a_server_path(self) -> None:
        for page in ("?panel=1", "index.html", None, 3):
            with self.subTest(page=page):
                self._refused("state 1 page", {"name": "a", "page": page})

    def test_refuses_an_unknown_key_in_a_state(self) -> None:
        self._refused(
            "state 1 has unknown key.*: action",
            {"name": "a", "page": "/", "action": [{"click": "#start"}]},
        )

    def test_refuses_actions_that_are_not_an_array(self) -> None:
        self._refused(
            "state 1 actions", {"name": "a", "page": "/", "actions": {"click": "#a"}}
        )

    def test_refuses_an_action_with_two_kinds(self) -> None:
        self._refused(
            "state 1 action 2 must hold exactly one of click, wait_for, wait_ms",
            {
                "name": "a",
                "page": "/",
                "actions": [{"click": "#a"}, {"click": "#a", "wait_for": "#b"}],
            },
        )

    def test_refuses_an_action_that_holds_no_kind(self) -> None:
        not_actions: tuple[object, ...] = ({}, {"press": "#a"}, "#a")
        for action in not_actions:
            with self.subTest(action=action):
                self._refused(
                    "state 1 action 1 must hold exactly one of",
                    {"name": "a", "page": "/", "actions": [action]},
                )

    def test_refuses_a_selector_that_is_not_text(self) -> None:
        not_selectors: tuple[object, ...] = (
            {"click": ""},
            {"wait_for": 3},
            {"click": None},
        )
        for action in not_selectors:
            with self.subTest(action=action):
                self._refused(
                    "state 1 action 1 (click|wait_for) must be a non-empty selector",
                    {"name": "a", "page": "/", "actions": [action]},
                )

    def test_refuses_a_wait_outside_one_to_ten_thousand_milliseconds(self) -> None:
        not_waits: tuple[object, ...] = (0, 10001, -5, True, 1.5, "300")
        for ms in not_waits:
            with self.subTest(ms=ms):
                self._refused(
                    "state 1 action 1 wait_ms must be a whole number of milliseconds "
                    "from 1 to 10000",
                    {"name": "a", "page": "/", "actions": [{"wait_ms": ms}]},
                )

    def test_accepts_the_shortest_and_the_longest_wait(self) -> None:
        (state,) = self._states(
            {"name": "a", "page": "/", "actions": [{"wait_ms": 1}, {"wait_ms": 10000}]}
        )
        self.assertEqual([action.ms for action in state.actions], [1, 10000])

    def test_refuses_stored_values_that_are_not_strings_to_strings(self) -> None:
        not_string_tables: tuple[object, ...] = (
            {"mode": 1},
            {"mode": True},
            {3: "dark"},
            "dark",
            ["mode"],
        )
        for storage in not_string_tables:
            with self.subTest(storage=storage):
                self._refused(
                    "state 1 storage must map strings to strings",
                    {"name": "a", "page": "/", "storage": storage},
                )


class LoadStateTests(unittest.TestCase):
    """A state read from the file itself, as TOML writes it."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "targets.toml"

    def test_loads_a_state_and_its_actions_from_toml(self) -> None:
        self.path.write_text(
            'pages = ["/"]\n[[viewport]]\nwidth = 390\nheight = 844\n'
            '[[state]]\nname = "after-start"\npage = "/?panel=1"\n'
            'storage = { mode = "dark" }\n'
            'actions = [{ click = "#start" }, { wait_ms = 300 }]\n',
            encoding="utf-8",
            newline="\n",
        )
        (state,) = pass_targets.load_targets(self.path).states
        self.assertEqual(
            state.actions,
            (
                pass_targets.Action("click", "#start", None),
                pass_targets.Action("wait_ms", None, 300),
            ),
        )
        self.assertEqual(state.storage, (("mode", "dark"),))


if __name__ == "__main__":
    unittest.main()
