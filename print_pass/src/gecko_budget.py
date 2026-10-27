"""How long a Firefox state may take: the watchdog's bound on one, counted step by step.

A Firefox state runs inside one watchdog bound, which must outlast every step a healthy
state can take, so that the watchdog fires only when a step's own bound failed. Every
WebDriver call is bounded by the client at `TIMED_CALL_BOUND_FACTOR` times its transport
timeout, and that timeout is `TRANSPORT_MARGIN_SECONDS` above the run's, so that Firefox's
own timeout error arrives first and the transport's catches only a wedged driver. A state
also waits and polls, and the driver lists the processes twice per session, outside every
bound of its own. `state_seconds` adds all of it up from those named constants: every call
`state_calls` counts at its own bound, and every wait's window. Kept apart from the state
runner, whose steps it counts, so each can be read whole.

A wait -- settling, or an action's look for its element -- is counted as its window, the
timeout and one poll past it, and then the one look that may begin just before the window
ends, whose calls run their bounds past it. Every earlier look runs inside the window, so
`state_calls` is what a state makes when each wait ends at its first look.

Entry point assumptions: the tool's own `gecko_process` (through `gecko_launch`'s
re-export), `print_engine_state`, `print_playwright` and `print_targets` only.
"""

from __future__ import annotations

from typing import Final

from gecko_launch import LISTING_OVERRUN_SECONDS, LISTING_TIMEOUT_SECONDS
from print_engine_state import SETTLE_POLL_MS, TIMED_CALL_BOUND_FACTOR
from print_playwright import MS_PER_SECOND
from print_targets import Action, State

#: How far the client's transport timeout sits above the run's `--timeout`: room for
#: Firefox's own script or page-load timeout error to arrive first, and be the one reported.
TRANSPORT_MARGIN_SECONDS: Final = 5.0

#: How often a state polls its page -- the probe while settling, an action's look for its
#: element -- and the longest single sleep of any wait: the Chromium engine's settle poll.
PAGE_POLL_SECONDS: Final = SETTLE_POLL_MS / MS_PER_SECOND

#: How long one process listing may take: its own timeout, and its overrun past that.
LISTING_SECONDS: Final = LISTING_TIMEOUT_SECONDS + LISTING_OVERRUN_SECONDS

# Listings per session, outside the driver's bounds: noting its Firefox, and recording that
# Firefox's crash helper before the session is deleted.
_LISTINGS_PER_STATE: Final = 2

# Calls every state makes: New Session and Delete Session; the window's size set and the
# viewport read, then each once more to correct it; the navigation to the page, the last
# probe, and the Resource Timing read.
_FIXED_CALLS: Final = 9

# Calls a state that stores anything adds: the seed page's navigation, its check, the store.
_STORAGE_CALLS: Final = 3

# Calls one poll of the settle makes: the probe.
_SETTLE_POLL_CALLS: Final = 1

# Calls one look of each action makes: a click's visibility, Find Element, then Element
# Click; a wait for an element's visibility alone; and a wait of a set time, none.
_LOOK_CALLS: Final = {"click": 3, "wait_for": 1, "wait_ms": 0}


def client_timeout(timeout_s: float) -> float:
    """The client's transport timeout under the run's `timeout_s`: a margin above it."""
    return timeout_s + TRANSPORT_MARGIN_SECONDS


def call_seconds(timeout_s: float) -> float:
    """The client's bound on one WebDriver call under the run's `timeout_s`."""
    return TIMED_CALL_BOUND_FACTOR * client_timeout(timeout_s)


def look_calls(action: Action) -> int:
    """The WebDriver calls one look of `action` makes, before it acts or looks again."""
    return _LOOK_CALLS[action.kind]


def state_calls(state: State, *, prints: int) -> int:
    """The WebDriver calls `state` makes, printing `prints` PDFs, when each wait ends at once.

    Each wait, the settle's and each action's, counts its one look: the look that may begin
    just before its window ends. Every call counted here is bounded at its own bound.
    """
    once = _FIXED_CALLS + prints + (_STORAGE_CALLS if state.storage else 0)
    looks = sum(look_calls(action) for action in state.actions)
    return once + _SETTLE_POLL_CALLS + looks


def state_seconds(state: State, *, timeout_s: float, prints: int) -> float:
    """The watchdog's bound on one state printing `prints` PDFs under `timeout_s`.

    Each call `state_calls` counts at its own bound; settling's window, polling the probe
    for up to `timeout_s` and one poll past it; each action's and the print moment's wait
    the same way; both listings; and one more `timeout_s` of slack, so that the watchdog is
    left to a step whose own bound failed.
    """
    calls = state_calls(state, prints=prints) * call_seconds(timeout_s)
    settle = timeout_s + PAGE_POLL_SECONDS
    actions = sum(_action_window(action, timeout_s) for action in state.actions)
    moment = 0.0 if state.print_at_ms is None else _wait_seconds(state.print_at_ms)
    listings = _LISTINGS_PER_STATE * LISTING_SECONDS
    return calls + settle + actions + moment + listings + timeout_s


def _action_window(action: Action, timeout_s: float) -> float:
    """How long one action may wait: looks polled up to `timeout_s`, or a wait of its `ms`.

    The calls of the look that may begin just before the timeout passes are `state_calls`'.
    """
    if action.kind == "wait_ms":
        return _wait_seconds(action.ms or 0)
    return timeout_s + PAGE_POLL_SECONDS


def _wait_seconds(ms: int) -> float:
    """How long a wait of `ms` may take: its length, and one poll past it."""
    return ms / MS_PER_SECOND + PAGE_POLL_SECONDS
