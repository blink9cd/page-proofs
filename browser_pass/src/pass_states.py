"""A state's steps: the values its page loads with, and the actions that reach it once loaded.

A `[[state]]` in the targets file names a page and how to reach what it shows: values stored in
`localStorage` before the page loads, and actions run in order once it has loaded and settled
-- a click, a wait for a selector to show, a wait of a set length. The stored values go in
through the browser context's `storage_state`, for the served origin
(a persistent context, which takes none at launch, has them set by Playwright's own setter,
`pass_extension_launch`): no script is injected to write them, so none can run late or be
blocked.

An action that does not complete is an instrument fault naming the state and the action,
counted from one: the state was never reached, so nothing measured afterwards would be a
measurement of it. A step's timeout is the run's `--timeout`, and each step lengthens the
rendering's watchdog bound by as much (`state_bound`).

Entry point assumptions: Playwright is importable, since its errors are what a failed action
raises; the page is anything with Playwright's three calls an action makes (`ActionPage`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.parse import urlsplit

from pass_probe import MS_PER_SECOND
from pass_targets import Action, Rendering, State
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import StorageState
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


class ActionPage(Protocol):
    """The part of Playwright's page an action calls -- a seam the tests replace."""

    def click(self, selector: str, *, timeout: float) -> None:
        """Click the first element `selector` matches, waiting up to `timeout` ms for it."""

    def wait_for_selector(self, selector: str, *, timeout: float) -> object:
        """Wait up to `timeout` ms for an element `selector` matches to be visible."""

    def wait_for_timeout(self, timeout: float) -> None:
        """Wait `timeout` ms."""


def run_actions(page: ActionPage, state: State, *, timeout: float) -> str | None:
    """Run `state`'s actions in order: None when all completed, else the fault for the first not.

    `timeout` is each selector action's bound, in seconds.
    """
    for number, action in enumerate(state.actions, start=1):
        act = _action_call(page, action, timeout * MS_PER_SECOND)
        where = f"state {state.name}: action {number} ({describe(action)})"
        try:
            act()
        except PlaywrightTimeoutError:
            return f"{where} timed out"
        except PlaywrightError as error:
            # Timeout's own base class: whatever else Playwright refused, quoted to the line
            # that says what, rather than the call log it appends.
            return f"{where} failed: {next(iter(str(error).splitlines()), '')}"
    return None


def describe(action: Action) -> str:
    """An action as a fault names it: `click "#start"`, `wait_ms "300"`."""
    argument = action.selector if action.selector is not None else action.ms
    return f'{action.kind} "{argument}"'


def _action_call(
    page: ActionPage, action: Action, timeout_ms: float
) -> Callable[[], object]:
    """The Playwright call one action makes, built before it runs.

    Built outside the call's `try`, so an action no targets file can hold -- a click with no
    selector -- raises here as the programming error it is, never as a fault.
    """
    match action:
        case Action(kind="click", selector=str(selector)):
            return lambda: page.click(selector, timeout=timeout_ms)
        case Action(kind="wait_for", selector=str(selector)):
            return lambda: page.wait_for_selector(selector, timeout=timeout_ms)
        case Action(kind="wait_ms", ms=int(ms)):
            return lambda: page.wait_for_timeout(ms)
        case _:
            raise ValueError(f"not an action a targets file can hold: {action!r}")


def storage_state(state: State | None, base_url: str) -> StorageState | None:
    """The context's `storage_state` holding `state`'s stored values, or None when it has none.

    The values belong to `base_url`'s ORIGIN -- its scheme and host, without any path a
    `base_url` carries -- since `localStorage` is kept per origin.
    """
    if state is None or not state.storage:
        return None
    parts = urlsplit(base_url)
    return {
        "cookies": [],
        "origins": [
            {
                "origin": f"{parts.scheme}://{parts.netloc}",
                "localStorage": [
                    {"name": key, "value": value} for key, value in state.storage
                ],
            }
        ],
    }


def state_bound(state: State | None, timeout: float) -> float:
    """What reaching `state` adds to its rendering's watchdog bound, in seconds; none for a load.

    Each action may take one `timeout` (a wait takes its own length, and is given both), and so
    may the settle after the actions.
    """
    if state is None:
        return 0.0
    waits_ms = sum(action.ms or 0 for action in state.actions)
    return timeout * (len(state.actions) + 1) + waits_ms / MS_PER_SECOND


def state_for(rendering: Rendering, states: Mapping[str, State]) -> State | None:
    """The state `rendering` is reached in, from `states` by name; None for a page as it loads.

    Raises:
        KeyError: `rendering` names a state `states` does not hold.
    """
    return None if rendering.state is None else states[rendering.state]
