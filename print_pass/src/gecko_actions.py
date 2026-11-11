"""A Firefox state's steps between its load and its print: actions, settling, the moment, the probe.

Each step means what the Chromium engine's means, with its problem texts, so a report reads
one way under either engine. `wait_for` waits for its selector to be visible as Playwright
defines it; `click` waits the same way, then clicks, and a click Firefox says is not
interactable, or intercepted by an element over it, is retried until the timeout, as
Playwright's actionability wait would; `wait_ms` waits in this process. Settling polls the
probe at the Chromium engine's interval until images and fonts have arrived, or the timeout
since navigation has passed; the print moment is waited for, or warned of once passed. The
first action that does not complete, or a probe that fails, stops the state with one finding,
raised as `Stopped`.

Every wait is a poll in a loop: an action's look for its element, every `PAGE_POLL_SECONDS`
until the timeout, and each pause in steps of at most that. WebDriver's own calls are bounded
by the client, and the state's whole run by its watchdog bound.

Entry point assumptions: the tool's own `gecko_budget`, `gecko_client` (its error),
`gecko_job`, `gecko_page`, `print_judge`, `print_playwright`, `print_probe`, `print_record`,
`print_actions` and `print_targets` only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from gecko_budget import PAGE_POLL_SECONDS
from gecko_client import WebDriverError
from gecko_job import SessionLike, StateJob
from gecko_page import VISIBLE_SCRIPT, ProbePage
from print_judge import finding, warning
from print_playwright import MS_PER_SECOND
from print_probe import ProbeError, ProbeResult, run_probe
from print_record import Problem, first_line
from print_actions import ActionError, set_attribute
from print_targets import Action

#: The Element Click errors that mean the element cannot take a click yet (it has no box,
#: or another element covers it), which a click retries until the timeout.
RETRIED_CLICK_ERRORS: Final = frozenset(
    {"element not interactable", "element click intercepted"}
)


class Stopped(Exception):
    """A step ended its state early; `problem` is the finding that says why."""

    def __init__(self, problem: Problem) -> None:
        """Carry `problem` to the state that stops."""
        super().__init__(problem.what)
        self.problem = problem


class _NotInTime(Exception):
    """An action's element was not ready to act on within the timeout."""


def run_actions(session: SessionLike, job: StateJob) -> None:
    """Run the state's actions in order; the first that does not complete stops the state.

    Raises:
        Stopped: an action's element was not ready in time, or WebDriver failed it.
    """
    for action in job.state.actions:
        act = _action_call(session, action, job)
        try:
            act()
        except (WebDriverError, _NotInTime, ActionError) as error:
            raise Stopped(finding(action.failure())) from error


def settle(session: SessionLike, job: StateJob, started: float) -> None:
    """Poll the probe until images and fonts have arrived, or the timeout has passed.

    An unsettled page is printed anyway, and the read-back taken just before the print says
    what was missing: the record shows what paper would get.

    Raises:
        Stopped: a probe failed.
    """
    while (
        not take_probe(session, job).settled
        and job.clock() - started <= job.settings.timeout_s
    ):
        job.pause(PAGE_POLL_SECONDS)


def wait_for_print_moment(job: StateJob, started: float) -> tuple[Problem, ...]:
    """Wait until `print_at_ms` after navigation began; warn when that moment had passed."""
    asked = job.state.print_at_ms
    if asked is None:
        return ()
    now = job.elapsed_ms(started)
    if now > asked:
        return (warning(f"printed at {now} ms, later than the {asked} ms asked"),)
    job.pause((asked - now) / MS_PER_SECOND)
    return ()


def take_probe(session: SessionLike, job: StateJob) -> ProbeResult:
    """Probe the page once; a probe the page fails, or a result refused, stops the state.

    Raises:
        Stopped: the probe's script failed, or its result is not one the parser takes.
    """
    try:
        return run_probe(ProbePage(session), job.target.reads, job.target.storage_reads)
    except (WebDriverError, ProbeError) as error:
        raise Stopped(finding(f"probe failed: {first_line(error)}")) from error


def _action_call(
    session: SessionLike, action: Action, job: StateJob
) -> Callable[[], None]:
    """What one action does, built before it runs.

    Built outside the call's `try`, so an action no targets file can hold (a click with no
    selector) raises here as the programming error it is, never as a finding.
    """
    match action:
        case Action(kind="click", selector=str(selector)):
            return lambda: _click(session, job, selector)
        case Action(kind="wait_for", selector=str(selector)):
            return lambda: _wait_for(session, job, selector)
        case Action(kind="wait_ms", ms=int(ms)):
            return lambda: job.pause(ms / MS_PER_SECOND)
        case Action(kind="set_attribute", attribute=(str(), str())):
            return lambda: set_attribute(ProbePage(session), action)
        case _:
            raise ValueError(f"not an action a targets file can hold: {action!r}")


def _wait_for(session: SessionLike, job: StateJob, selector: str) -> None:
    """Look for `selector` to be visible until it is, polling, or raise at the timeout."""
    began = job.clock()
    while not _is_visible(session, selector):
        _check_in_time(job, began)
        job.sleep(PAGE_POLL_SECONDS)


def _click(session: SessionLike, job: StateJob, selector: str) -> None:
    """Click `selector` once it is visible and takes the click, polling, until the timeout."""
    began = job.clock()
    while not _clicked(session, selector):
        _check_in_time(job, began)
        job.sleep(PAGE_POLL_SECONDS)


def _clicked(session: SessionLike, selector: str) -> bool:
    """Whether one look clicked `selector`: visible, found, and the click not refused yet.

    Raises:
        WebDriverError: a script, Find Element or Element Click failed otherwise.
    """
    if not _is_visible(session, selector):
        return False
    element = session.find_css(selector)
    if element is None:
        return False  # gone between the look and the find: look again
    try:
        session.click(element)
    except WebDriverError as error:
        if error.error in RETRIED_CLICK_ERRORS:
            return False
        raise
    return True


def _is_visible(session: SessionLike, selector: str) -> bool:
    """Whether `selector`'s first match is visible; only a plain `true` from the page is."""
    return session.execute(VISIBLE_SCRIPT, selector) is True


def _check_in_time(job: StateJob, began: float) -> None:
    """Raise `_NotInTime` once the timeout has passed since `began`."""
    if job.clock() - began > job.settings.timeout_s:
        raise _NotInTime
