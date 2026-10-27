"""How long a start or a stop of geckodriver may take: the watchdog's bound on each, and its poll.

`Geckodriver` runs its start and its stop each inside a watchdog bound, and every step inside
has a bound of its own; `watchdog_seconds` adds those up, so the watchdog fires only when one
of them failed. The sum is kept apart from the class whose steps it counts so that each can be
read whole, and so that whatever bounds a session can budget for the start and the stop
without starting anything.

Entry point assumptions: the standard library, the shared `bounded_run` (its tree kill's own
bound), found by the sibling insert below, and this folder's `gecko_process` (the listing's).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

# The shared tree-killing subprocess runner: a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place,
# and put near the front of the path, ahead of every installed distribution, so none of the
# same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run
from gecko_process import LISTING_OVERRUN_SECONDS, LISTING_TIMEOUT_SECONDS

#: How often a start looks in geckodriver's log for its port: short beside the second or two
#: a start takes, so the port is used soon after it is named.
POLL_SECONDS: Final = 0.05

#: Timeouts in one watchdog bound (`watchdog_seconds`): a stop's four waits at most -- for
#: geckodriver, for its reap after a kill, for the rest of its group, and for the group again
#: after a kill -- and one of slack.
_TIMEOUTS_PER_BOUND: Final = 5

#: Tree kills in one watchdog bound: a stop's two at most, of geckodriver and of its group.
_KILLS_PER_BOUND: Final = 2

#: Polls in one watchdog bound: a stop's two of its group, each of which may sleep one
#: `POLL_SECONDS` past its deadline before the probe that finds the deadline passed.
_POLLS_PER_BOUND: Final = 2

#: A stop's polls for crash helpers, once one was recorded: before a SIGTERM, before a tree
#: kill, and after it. Each adds a timeout, a last sleep, and a last listing to the bound.
_HELPER_POLLS: Final = 3


def watchdog_seconds(timeout_s: float, helpers: int = 0) -> float:
    """The watchdog's bound on a start or a stop of geckodriver, above every step inside.

    A stop, the longer of the two, waits up to `timeout_s` for geckodriver to end; past that
    it kills the tree, which `bounded_run` bounds at its own `KILL_TIMEOUT_SECONDS`, and reaps
    for up to `timeout_s` more. It then polls up to `timeout_s` for the rest of the group to
    end, and past that kills the tree again and polls once more: four timeouts, two kills, and
    two polls that may each sleep one `POLL_SECONDS` past their deadline. With `helpers` crash
    helpers recorded, it then polls for them three times at most, each poll's last listing
    bounded by `LISTING_TIMEOUT_SECONDS`, with a SIGTERM and then a tree kill of each helper's
    group between. A start lists every process, waits, kills and reaps once. A listing past
    its own timeout takes `LISTING_OVERRUN_SECONDS` more, then raises and ends the start or
    the stop it ran in, so that is counted once. One more `timeout_s` of slack leaves the
    watchdog to a step whose own bound failed.

    `note_firefox` and `record_helpers` list outside both bounds, inside a session, so no
    bound here counts them: `list_processes` bounds each listing alone, at
    `LISTING_TIMEOUT_SECONDS` and `LISTING_OVERRUN_SECONDS` past it, which whatever bounds
    the session must leave room for.
    """
    helper_polls = _HELPER_POLLS if helpers > 0 else 0
    return (
        (_TIMEOUTS_PER_BOUND + helper_polls) * timeout_s
        + (_KILLS_PER_BOUND + helpers) * bounded_run.KILL_TIMEOUT_SECONDS
        + (_POLLS_PER_BOUND + helper_polls) * POLL_SECONDS
        + helper_polls * LISTING_TIMEOUT_SECONDS
        + LISTING_OVERRUN_SECONDS
    )
