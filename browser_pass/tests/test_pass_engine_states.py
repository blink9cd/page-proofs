"""Grouped suite: the engine's handling of a state, through fakes.

What is checked is the ORDER of things, which a fake makes plain: a state's context holds its
stored values, its actions run between the settle after the load and a second settle, every
detector reads the page after both, nothing runs after an action that failed, no late-script load
opens for it, and `run_matrix` hands each rendering its own state and a bound that covers its
steps. The page answers the probe with scripted records, and the browser records the context it
is asked for. The real engines reach a state in `test_pass_states_live.py`.
"""

from __future__ import annotations

import contextlib
import sys
import unittest
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_engine  # noqa: E402 - the tool's private src/, via the insert above
import pass_motion  # noqa: E402 - the tool's private src/, via the insert above
import pass_report  # noqa: E402 - the tool's private src/, via the insert above
import pass_shift  # noqa: E402 - the tool's private src/, via the insert above
import pass_states  # noqa: E402 - the tool's private src/, via the insert above
from pass_motion import NO_MOTION, Motion  # noqa: E402 - the tool's private src/
from pass_targets import Action, Rendering, State  # noqa: E402 - the tool's private src/
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

#: A faithful read-back for `_OPENED`, so a settled rendering goes on to be measured.
_READ_BACK = {
    "innerWidth": 390,
    "innerHeight": 844,
    "clientWidth": 390,
    "scriptingNone": False,
    "dark": False,
    "landscape": False,
    "reducedMotion": False,
}

_NO_LAYOUT = {
    "overflow": 0,
    "pastRightEdge": [],
    "hiddenRendered": [],
    "smallTargets": [],
}


class _ScriptedProbe:
    """Answers the probe's operations from scripted records, logging each operation asked."""

    def __init__(self, polls: Sequence[Motion], log: list[object]) -> None:
        """`polls` is what each `stillMotion` in turn reports; `log` is shared with the page."""
        self._polls: Iterator[Motion] = iter(polls)
        self._log = log

    def __call__(self, page: object, operation: str, **extra: object) -> object:
        """One operation's scripted record."""
        self._log.append(operation)
        if operation == "stillMotion":
            return pass_motion.motion_record(next(self._polls))
        records: dict[str, object] = {
            "signature": [390, 844, 3, "loaded", 0],
            "state": _READ_BACK,
            "layout": _NO_LAYOUT,
            "digest": [],
        }
        return records[operation]


def _settles(*outcomes: bool) -> Callable[..., bool]:
    """A settle that reads the page once per call, and settles or not as `outcomes` say in turn."""
    remaining = iter(outcomes)

    def settle(read: Callable[[], object], **_timing: object) -> bool:
        read()
        return next(remaining)

    return settle


def _logging_page(log: list[object]) -> mock.Mock:
    """A page whose action calls go into `log`, beside the probe's operations."""
    page = mock.Mock()
    page.click.side_effect = lambda selector, **_: log.append(("click", selector))
    page.wait_for_selector.side_effect = lambda selector, **_: log.append(
        ("wait_for_selector", selector)
    )
    page.wait_for_timeout.side_effect = lambda ms: log.append(("wait_for_timeout", ms))
    return page


class EngineStateTests(unittest.TestCase):
    """The engine reaches a state between two settles, and measures only once it has."""

    def setUp(self) -> None:
        self.log: list[object] = []
        self.page = _logging_page(self.log)

    def _measure(
        self, polls: Sequence[Motion], *outcomes: bool
    ) -> pass_report.RenderingResult:
        with (
            mock.patch.object(pass_engine, "probe", _ScriptedProbe(polls, self.log)),
            mock.patch.object(pass_engine, "wait_until_settled", _settles(*outcomes)),
        ):
            return pass_engine.measure(
                self.page,
                pass_engine.Listeners(self.page, _BASE_URL),
                _OPENED,
                _BASE_URL,
                timeout=5.0,
                prove=False,
                state=_STATE,
            )

    def test_the_actions_run_between_two_settles_and_the_detectors_after_both(
        self,
    ) -> None:
        result = self._measure((Motion(2, 0, 1), Motion(1, 1, 0)), True, True)
        self.assertEqual(result.faults, ())
        self.assertEqual(
            self.log,
            [
                "stillMotion",
                "signature",
                ("click", "#start"),
                ("wait_for_selector", "#grid"),
                ("wait_for_timeout", 300),
                "stillMotion",
                "signature",
                "state",
                "layout",
                "digest",
            ],
        )
        # Both settles' stillings add up; what still ran is the second's.
        self.assertEqual(result.motion, Motion(finished=3, paused=1, running=0))

    def test_an_action_that_fails_stops_the_rendering_before_any_detector(
        self,
    ) -> None:
        self.page.click.side_effect = PlaywrightTimeoutError("Timeout 5000ms exceeded.")
        result = self._measure((Motion(1, 0, 0),), True)
        self.assertEqual(
            result.faults, ('state after-start: action 1 (click "#start") timed out',)
        )
        self.assertEqual(result.findings, {})
        self.assertEqual(self.log, ["stillMotion", "signature"])
        self.assertEqual(result.motion, Motion(1, 0, 0))

    def test_a_state_still_moving_after_its_actions_is_a_fault(self) -> None:
        result = self._measure((NO_MOTION, Motion(0, 1, 1)), True, False)
        self.assertEqual(
            result.faults,
            ("state after-start: the layout was still changing 5s after its actions",),
        )
        self.assertNotIn("layout", self.log)
        self.assertEqual(result.motion, Motion(0, 1, 1))

    def test_a_state_s_context_holds_its_stored_values_and_gets_no_late_load(
        self,
    ) -> None:
        browser = mock.Mock()
        browser.new_context.return_value.new_page.return_value = self.page
        with (
            mock.patch.object(
                pass_engine, "probe", _ScriptedProbe((NO_MOTION, NO_MOTION), self.log)
            ),
            mock.patch.object(pass_engine, "wait_until_settled", _settles(True, True)),
        ):
            result = pass_engine.render(
                browser, _OPENED, _BASE_URL, timeout=5.0, prove=False, state=_STATE
            )
        # One context: the late-script load would have opened a second.
        browser.new_context.assert_called_once()
        self.assertEqual(
            browser.new_context.call_args.kwargs["storage_state"],
            pass_states.storage_state(_STATE, _BASE_URL),
        )
        self.assertEqual(result.faults, ())
        # The state was reached, not merely named: its actions ran in that context's page.
        self.assertIn(("click", "#start"), self.log)
        self.assertEqual(result.late_shift.status, pass_shift.NOT_APPLICABLE)
        browser.new_context.return_value.close.assert_called_once()

    def test_a_plain_load_s_context_holds_no_stored_values(self) -> None:
        browser = mock.Mock()
        pass_engine.open_context(browser, _PLAIN)
        self.assertIsNone(browser.new_context.call_args.kwargs["storage_state"])


class _RecordingWatchdog:
    """Records every bound the engine asks for, and never fires."""

    def __init__(self) -> None:
        self.bounds: list[tuple[float, str]] = []

    @contextlib.contextmanager
    def bound(self, seconds: float, what: str) -> Iterator[None]:
        self.bounds.append((seconds, what))
        yield


class MatrixWiringTests(unittest.TestCase):
    """`run_matrix` hands each rendering its own state, and bounds it for that state's steps."""

    def test_each_state_s_rendering_gets_its_state_and_its_bound(self) -> None:
        rendered: list[tuple[Rendering, State | None]] = []

        def render(
            browser: object,
            rendering: Rendering,
            base_url: str,
            *,
            timeout: float,
            prove: bool,
            state: State | None = None,
            require_csp: bool = False,
            missing: bool = False,
        ) -> pass_report.RenderingResult:
            rendered.append((rendering, state))
            late = pass_shift.LateShift(pass_shift.NOT_APPLICABLE)
            return pass_report.RenderingResult(
                rendering, (), {}, {}, {}, late, NO_MOTION
            )

        plain, opened = (
            replace(_PLAIN, engine="webkit"),
            replace(_OPENED, engine="webkit"),
        )
        watchdog = _RecordingWatchdog()
        with (
            mock.patch.object(
                pass_engine,
                "sync_playwright",
                lambda: contextlib.nullcontext(mock.Mock()),
            ),
            mock.patch.object(pass_engine, "launch", return_value=mock.Mock()),
            mock.patch.object(pass_engine, "render", render),
        ):
            pass_engine.run_matrix(
                [plain, opened],
                _BASE_URL,
                timeout=10.0,
                prove=False,
                scratch=Path("unused"),
                progress=lambda _result: None,
                watchdog=watchdog,
                states=[_STATE],
            )
        self.assertEqual(rendered, [(plain, None), (opened, _STATE)])
        self.assertEqual(
            [seconds for seconds, _ in watchdog.bounds[:2]],
            [
                pass_engine.rendering_bound(10.0),
                pass_engine.rendering_bound(10.0, _STATE),
            ],
        )


if __name__ == "__main__":
    unittest.main()
