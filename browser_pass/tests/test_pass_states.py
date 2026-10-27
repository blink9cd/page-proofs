"""Grouped suite: a state's steps, and how the report keeps a state apart from its page.

The steps: its actions run in order, each through its own Playwright call, and the first that
does not complete is a fault naming the state and the action, counted from one; its stored
values go to the served origin; its watchdog bound grows with its steps; and no late-script load
runs for it. The engine's handling of a state is `test_pass_engine_states.py`'s, and the real
engines reach one in `test_pass_states_live.py`.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_engine  # noqa: E402 - the tool's private src/, via the insert above
import pass_report  # noqa: E402 - the tool's private src/, via the insert above
import pass_shift  # noqa: E402 - the tool's private src/, via the insert above
import pass_states  # noqa: E402 - the tool's private src/, via the insert above
from pass_digest import Box  # noqa: E402 - the tool's private src/
from pass_motion import NO_MOTION  # noqa: E402 - the tool's private src/
from pass_targets import ENGINES, Action, Rendering, State  # noqa: E402 - the tool's private src/
from playwright.sync_api import Error as PlaywrightError  # noqa: E402 - after the insert
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: E402 - after the insert

_BASE_URL = "http://127.0.0.1:8123"

_STATE = State(
    name="after-start",
    page="/?panel=1",
    storage=(("mode", "dark"), ("seen", "1")),
    actions=(
        Action("click", "#start", None),
        Action("wait_for", "#grid", None),
        Action("wait_ms", None, 300),
    ),
)

_OPENED = Rendering("chromium", "/?panel=1", 390, 844, "light", "on", "after-start")
_PLAIN = Rendering("chromium", "/", 390, 844, "light", "on")


class _FakePage:
    """Answers the three calls an action makes, logging each, and raising where told to."""

    def __init__(self, failures: dict[int, Exception] | None = None) -> None:
        """`failures` maps a call's number, counted from one, to what it raises."""
        self.log: list[tuple[object, ...]] = []
        self._failures = failures or {}

    def _call(self, *entry: object) -> None:
        self.log.append(entry)
        failure = self._failures.get(len(self.log))
        if failure is not None:
            raise failure

    def click(self, selector: str, *, timeout: float) -> None:
        self._call("click", selector, timeout)

    def wait_for_selector(self, selector: str, *, timeout: float) -> None:
        self._call("wait_for_selector", selector, timeout)

    def wait_for_timeout(self, timeout: float) -> None:
        self._call("wait_for_timeout", timeout)


class RunActionsTests(unittest.TestCase):
    """Each action through its own call, in order; the first that fails is the fault."""

    def test_runs_each_action_in_order_through_its_own_call(self) -> None:
        page = _FakePage()
        self.assertIsNone(pass_states.run_actions(page, _STATE, timeout=2.0))
        self.assertEqual(
            page.log,
            [
                ("click", "#start", 2000.0),
                ("wait_for_selector", "#grid", 2000.0),
                ("wait_for_timeout", 300),
            ],
        )

    def test_a_timeout_is_a_fault_naming_the_state_and_the_action(self) -> None:
        state = replace(
            _STATE,
            actions=(
                Action("wait_for", "#grid", None),
                Action("click", "#x", None),
                Action("wait_ms", None, 5),
            ),
        )
        page = _FakePage({2: PlaywrightTimeoutError("Timeout 2000ms exceeded.")})
        fault = pass_states.run_actions(page, state, timeout=2.0)
        # Counted from one: the second action is action 2. The third never runs.
        self.assertEqual(fault, 'state after-start: action 2 (click "#x") timed out')
        self.assertEqual(len(page.log), 2)

    def test_any_other_failure_is_a_fault_quoting_its_first_line(self) -> None:
        error = PlaywrightError("Element is not attached to the DOM\nCall log: ...")
        page = _FakePage({1: error})
        self.assertEqual(
            pass_states.run_actions(page, _STATE, timeout=2.0),
            'state after-start: action 1 (click "#start") failed: '
            "Element is not attached to the DOM",
        )

    def test_a_state_without_actions_has_nothing_to_fail(self) -> None:
        page = _FakePage()
        state = replace(_STATE, actions=())
        self.assertIsNone(pass_states.run_actions(page, state, timeout=2.0))
        self.assertEqual(page.log, [])

    def test_an_action_is_described_as_the_targets_file_writes_it(self) -> None:
        self.assertEqual(
            [pass_states.describe(action) for action in _STATE.actions],
            ['click "#start"', 'wait_for "#grid"', 'wait_ms "300"'],
        )

    def test_an_action_no_targets_file_can_hold_is_a_programming_error(self) -> None:
        state = replace(_STATE, actions=(Action("click", None, None),))
        with self.assertRaisesRegex(ValueError, "not an action a targets file"):
            pass_states.run_actions(_FakePage(), state, timeout=2.0)


class StorageStateTests(unittest.TestCase):
    """Stored values go in through the context's storage state, for the served origin."""

    def test_stored_values_go_to_the_served_origin_in_file_order(self) -> None:
        self.assertEqual(
            pass_states.storage_state(_STATE, _BASE_URL),
            {
                "cookies": [],
                "origins": [
                    {
                        "origin": _BASE_URL,
                        "localStorage": [
                            {"name": "mode", "value": "dark"},
                            {"name": "seen", "value": "1"},
                        ],
                    }
                ],
            },
        )

    def test_the_origin_leaves_out_any_path_the_base_url_carries(self) -> None:
        stored = pass_states.storage_state(_STATE, "https://example.org/site/")
        assert stored is not None
        self.assertEqual(stored["origins"][0]["origin"], "https://example.org")

    def test_nothing_stored_is_no_storage_state(self) -> None:
        empty = replace(_STATE, storage=())
        self.assertIsNone(pass_states.storage_state(empty, _BASE_URL))
        self.assertIsNone(pass_states.storage_state(None, _BASE_URL))


class BoundTests(unittest.TestCase):
    """A state's steps lengthen its rendering's watchdog bound; a plain load's adds nothing."""

    def test_a_plain_load_adds_nothing(self) -> None:
        self.assertEqual(pass_states.state_bound(None, 10.0), 0.0)

    def test_each_action_and_the_settle_after_them_add_a_timeout(self) -> None:
        # Three actions and the second settle, a timeout each, and the wait its own length.
        self.assertEqual(pass_states.state_bound(_STATE, 10.0), 4 * 10.0 + 0.3)

    def test_a_state_s_rendering_is_bounded_for_its_steps_too(self) -> None:
        self.assertEqual(
            pass_engine.rendering_bound(10.0, _STATE),
            pass_engine.rendering_bound(10.0) + pass_states.state_bound(_STATE, 10.0),
        )


class StateForTests(unittest.TestCase):
    """Which state a rendering is reached in."""

    def test_a_plain_rendering_is_reached_in_none(self) -> None:
        self.assertIsNone(pass_states.state_for(_PLAIN, {}))

    def test_a_state_s_rendering_is_reached_in_its_own(self) -> None:
        self.assertIs(pass_states.state_for(_OPENED, {_STATE.name: _STATE}), _STATE)

    def test_a_state_the_targets_never_named_is_a_programming_error(self) -> None:
        with self.assertRaises(KeyError):
            pass_states.state_for(_OPENED, {})


class NoLateLoadTests(unittest.TestCase):
    """The late-script load measures a page's load, and a state is reached after it."""

    def test_no_late_script_load_runs_for_a_state_in_any_engine(self) -> None:
        for engine in ENGINES:
            with self.subTest(engine=engine):
                late = pass_shift.not_measured(replace(_OPENED, engine=engine))
                assert late is not None
                self.assertEqual(late.status, pass_shift.NOT_APPLICABLE)
                self.assertIn("state", late.reason)


def _result(rendering: Rendering, **changes: object) -> pass_report.RenderingResult:
    """A clean result for `rendering`, with `changes` applied."""
    clean = pass_report.RenderingResult(
        rendering=rendering,
        faults=(),
        findings={},
        verdicts={},
        digest={"body>main:1": Box(True, 0.0, 68.0, 390.0, 900.0, 16.0)},
        late_shift=pass_shift.not_measured(rendering)
        or pass_shift.LateShift(pass_shift.MEASURED),
        motion=NO_MOTION,
    )
    return replace(clean, **changes)  # type: ignore[arg-type] # changes name real fields


_FACTS = pass_report.RunFacts(
    started="20261006T000000Z",
    commit="abc1234",
    tree_dirty=False,
    served="src",
    base_url=_BASE_URL,
    playwright="1.63.0",
    engines={"chromium": "153.0.8010.12", "webkit": "26.6"},
    prove=False,
    require_csp=False,
)


class StateReportTests(unittest.TestCase):
    """The report keeps a state apart from its page: in the record, the pairing and the tables."""

    def setUp(self) -> None:
        shifted = Box(True, 0.0, 70.92, 390.0, 900.0, 16.0)
        home = replace(_OPENED, page="/")
        self.results = [
            _result(_PLAIN),
            _result(replace(_PLAIN, engine="webkit"), digest={"body>main:1": shifted}),
            _result(home, findings={"small_target": ("body>button:1 16x16",)}),
            _result(replace(home, engine="webkit"), digest={"body>main:1": shifted}),
        ]

    def test_each_rendering_record_names_its_state(self) -> None:
        document = pass_report.report_document(self.results, [], _FACTS)
        states = [record["state"] for record in document["renderings"]]  # type: ignore[attr-defined,index] # a list of dicts
        self.assertEqual(states, [None, None, "after-start", "after-start"])

    def test_a_state_is_compared_only_with_the_same_state(self) -> None:
        comparisons = pass_report.cross_engine(self.results, reference="chromium")
        self.assertEqual(
            [comparison.key[:2] for comparison in comparisons],
            [("/", None), ("/", "after-start")],
        )
        document = pass_report.report_document(self.results, comparisons, _FACTS)
        states = [record["state"] for record in document["cross_engine"]]  # type: ignore[attr-defined,index] # a list of dicts
        self.assertEqual(states, [None, "after-start"])

    def test_the_tables_name_a_state_beside_its_page(self) -> None:
        comparisons = pass_report.cross_engine(self.results, reference="chromium")
        text = pass_report.report_markdown(self.results, comparisons, _FACTS)
        self.assertIn("| / | webkit | vertical | 1 |", text)
        self.assertIn("| / [after-start] | webkit | vertical | 1 |", text)
        self.assertIn(
            "- `body>button:1 16x16` in 1 rendering: "
            "chromium / [after-start] 390x844 light scripts-on",
            text,
        )

    def test_a_late_shift_column_of_states_alone_reads_n_a(self) -> None:
        states_only = [self.results[2], self.results[3]]
        text = pass_report.report_markdown(states_only, [], _FACTS)
        self.assertIn("| late_shift | n/a | n/a |", text)


if __name__ == "__main__":
    unittest.main()
