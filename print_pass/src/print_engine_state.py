"""One state of one target, printed: a fresh context, the page loaded, settled, printed, judged.

`run_state` is the whole of a state, in the order Chrome's print dialog would meet the page:
a fresh context with the state's emulation and stored values, the shared CSP collector,
routes that abort every request off the served origin and keep every WebSocket there from
connecting, the page loaded, the actions run, the probe polled until images and fonts have
arrived, the print moment waited for, one last read-back, then one PDF or two (backgrounds
off, then on) from that same load. Its record says what each step saw.

A state that cannot go on stops with one finding and no PDF (an action that does not
complete, a probe that raises), and the run goes on to the next state. Any other call the
browser fails (opening or closing the context, loading the page, a wait, a print) is an
`EngineError` naming the state, as a fault in the CSP collector is: the run could not
measure it honestly.

Every Playwright call runs inside the watchdog's bound, so nothing here can hang the run. A
call that takes a timeout is handed the state's, and bounded at `TIMED_CALL_BOUND_FACTOR`
times it: on a healthy browser its own timeout fires first and fails as it always has, and
only a wedged dispatcher or driver, which never delivers that timeout, reaches the bound. A
wait is bounded at its own length plus the timeout, the context's close as a timed call is,
and every other call at the timeout. The socket route's handler calls nothing on Playwright.

Entry point assumptions: `print_playwright`'s protocols stand for Playwright, which is never
imported here, nor by the shared collector `print_csp` installs; what a state records is
`print_record`'s, and what counts as a problem in it `print_judge`'s; `print_actions`,
`print_probe`, `print_targets` and `print_watchdog` are the tool's own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Final

from print_playwright import (
    MARGIN_INCHES,
    MS_PER_SECOND,
    BrowserLike,
    ContextLike,
    PageLike,
    RequestLike,
    ResponseLike,
    RouteLike,
    WebSocketRouteLike,
    color_scheme_emulated,
    context_options,
    is_loopback,
    print_options,
    reduced_motion_emulated,
    request_label,
)
from print_actions import set_attribute
from print_csp import CspWatch
from print_judge import finding, state_problems, warning
from print_probe import ProbeResult, run_probe
from print_record import (
    Engine,
    EngineSettings,
    MadePdf,
    NetworkRecord,
    Problem,
    StateRecord,
    engine_errors,
    first_line,
    pdf_name,
)
from print_targets import BACKGROUNDS, Action, State, Target, state_margin_inches
from print_watchdog import Bounder

#: How long the settle loop waits between probes, in milliseconds.
SETTLE_POLL_MS: Final = 100

#: How many times its own timeout the watchdog gives a call that takes one, and every close.
#: The driver reports that timeout on the synchronous API's dispatcher, so a dispatcher
#: wedged by a handler never delivers it: only the watchdog's thread can end the call. Past
#: one, so a healthy call's own timeout fires first, with room for the report's round trip.
#: A close takes no timeout, and installed Chrome's has taken just over 30 s under load.
TIMED_CALL_BOUND_FACTOR: Final = 2

# The settings are `print_targets`' own, which lists them off, on, both; unpacking them
# fails at import if a setting is ever added there without a row below.
_OFF, _ON, _BOTH = BACKGROUNDS

#: Which PDFs each `backgrounds` setting prints, in order, from one load: either engine's.
BACKGROUND_PASSES: Final[Mapping[str, tuple[bool, ...]]] = {
    _OFF: (False,),
    _ON: (True,),
    _BOTH: (False, True),
}

# The error code a blocked request is aborted with: the browser reports it as the client's.
_BLOCKED_BY_CLIENT: Final = "blockedbyclient"

# The lowest HTTP status recorded as an error.
_HTTP_ERROR_FLOOR: Final = 400

# What a failed request is recorded with when the browser gives no reason.
_NO_REASON: Final = "no reason given"


def run_state(
    browser: BrowserLike,
    engine: Engine,
    target: Target,
    state: State,
    settings: EngineSettings,
    *,
    base_url: str,
    expected_hosts: tuple[str, ...],
    bounder: Bounder,
    clock: Callable[[], float],
) -> StateRecord:
    """Print one state of `target` in `browser`, in a context of its own, and record it.

    `expected_hosts` are the targets file's hosts off loopback, handed to the judge.

    Raises:
        EngineError: a call other than an action's or the probe's failed, naming the state.
    """
    job = _Job(
        target=target,
        state=state,
        settings=settings,
        engine=engine,
        browser_version=browser.version,
        base_url=base_url,
        expected_hosts=expected_hosts,
        bounder=bounder,
        clock=clock,
    )
    with engine_errors(job.where, _Stopped):
        with job.bound("browser.new_context"):
            context = browser.new_context(
                **context_options(state, settings.viewport, base_url)
            )
        try:
            return _run_in_context(context, job)
        finally:
            with job.bound_timed("context.close"):
                context.close()


class _Stopped(Exception):
    """A step ended its state early; `problem` is the finding that says why."""

    def __init__(self, problem: Problem) -> None:
        """Carry `problem` to the state that stops."""
        super().__init__(problem.what)
        self.problem = problem


@dataclass(frozen=True)
class _Job:
    """One state's work: what it prints, under which settings, in which browser."""

    target: Target
    state: State
    settings: EngineSettings
    engine: Engine
    browser_version: str
    base_url: str
    expected_hosts: tuple[str, ...]  # blocked still, and judged warnings
    bounder: Bounder
    clock: Callable[[], float]

    @property
    def where(self) -> str:
        """The state, named as a report and a bound name it: `home/default`."""
        return f"{self.target.name}/{self.state.name}"

    @property
    def timeout_ms(self) -> float:
        """The per-page timeout in Playwright's milliseconds."""
        return self.settings.timeout_s * MS_PER_SECOND

    def bound(self, call: str, *, waits_ms: float = 0) -> AbstractContextManager[None]:
        """The watchdog's bound on a call of this state that takes no timeout, not a close.

        The bound is the timeout, plus `waits_ms` for a call that is itself a wait that long,
        so a wait is never abandoned for waiting as asked.
        """
        seconds = self.settings.timeout_s + waits_ms / MS_PER_SECOND
        return self.bounder.bound(seconds, f"{call} {self.where}")

    def bound_timed(self, call: str) -> AbstractContextManager[None]:
        """The bound on a timed call, or a close: `TIMED_CALL_BOUND_FACTOR` times the timeout."""
        seconds = self.settings.timeout_s * TIMED_CALL_BOUND_FACTOR
        return self.bounder.bound(seconds, f"{call} {self.where}")

    def elapsed_ms(self, started: float) -> int:
        """Whole milliseconds since `started`, a reading of this job's clock."""
        return round((self.clock() - started) * MS_PER_SECOND)


@dataclass
class _Network:
    """What the page's listeners, the route and the CSP collector have seen so far."""

    csp: CspWatch  # on the context before its page opens, so the first load is heard
    served: str  # the served origin's base URL, which a request is labeled against
    blocked: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    http_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)

    def record(self) -> NetworkRecord:
        """What has been seen, frozen; a collector fault raises (`CspWatch.lines`)."""
        return NetworkRecord(
            failed=tuple(self.failed),
            http_errors=tuple(self.http_errors),
            blocked=tuple(self.blocked),
            page_errors=tuple(self.page_errors),
            csp_violations=self.csp.lines(),
        )


@dataclass(frozen=True)
class _Outcome:
    """How far a state got after loading: its read-back, print moment, PDFs and step problems."""

    probe: ProbeResult | None
    printed_at_ms: int | None
    pdfs: tuple[MadePdf, ...]
    problems: tuple[Problem, ...]


def _run_in_context(context: ContextLike, job: _Job) -> StateRecord:
    """Load the page in `context`, take it as far as it goes, and record what it did."""
    csp = CspWatch(context, job.bound, served=job.base_url)
    network = _Network(csp, job.base_url)
    page = _open_page(context, job, network)
    started = job.clock()
    _load(page, job)
    load_ms = job.elapsed_ms(started)
    found: list[Problem] = []
    try:
        outcome = _settle_and_print(page, job, started, found)
    except _Stopped as stopped:
        # What the steps before the stop found stands beside the finding that stopped it.
        outcome = _Outcome(
            probe=None,
            printed_at_ms=None,
            pdfs=(),
            problems=(*found, stopped.problem),
        )
    return _record(job, load_ms, network.record(), outcome)


def _open_page(context: ContextLike, job: _Job, network: _Network) -> PageLike:
    """Give `context` its timeout and its routes off its origin, and open a listened-to page.

    Both routes are in place before the page opens: Playwright routes only the WebSockets
    opened after `route_web_socket`, and a request route never sees one. Each call has a
    bound of its own, naming it if it overruns; the three `page.on` registrations share one.
    """
    with job.bound("context.set_default_timeout"):
        context.set_default_timeout(job.timeout_ms)
    with job.bound("context.route"):
        context.route(_leaves_origin(job.base_url), _blocker(job, network))
    with job.bound("context.route_web_socket"):
        context.route_web_socket(_leaves_origin(job.base_url), _socket_blocker(network))
    with job.bound("context.new_page"):
        page = context.new_page()
    with job.bound("page.on"):
        _attach_listeners(page, network)
    return page


def _leaves_origin(served: str) -> Callable[[str], bool]:
    """The routes' matcher: every request that would leave the origin served at `served`."""
    return lambda url: not is_loopback(url, served=served)


def _blocker(job: _Job, network: _Network) -> Callable[[RouteLike], None]:
    """The route's handler: record the URL, then abort the request as the client's."""

    def block(route: RouteLike) -> None:
        network.blocked.append(route.request.url)
        with job.bound("route.abort"):
            route.abort(_BLOCKED_BY_CLIENT)

    return block


def _socket_blocker(network: _Network) -> Callable[[WebSocketRouteLike], None]:
    """The socket route's handler: record the URL, and nothing else.

    A routed WebSocket reaches its server only when the handler calls `connect_to_server`,
    so one it never calls is a mock the page talks to: Playwright opens it with no server
    behind it, and nothing leaves the machine.
    """

    def block(socket: WebSocketRouteLike) -> None:
        # Never `socket.close()`: under the synchronous API this handler runs on the
        # dispatcher greenlet, and the async close's wrapper waits on that same greenlet,
        # so it never returns and no Playwright timeout can end it.
        network.blocked.append(socket.url)

    return block


def _attach_listeners(page: PageLike, network: _Network) -> None:
    """Record page errors, failed requests and HTTP errors into `network`."""

    def on_page_error(error: Exception) -> None:
        network.page_errors.append(first_line(error))

    def on_request_failed(request: RequestLike) -> None:
        # A blocked request fails too, and is already recorded as blocked.
        if request.url not in network.blocked:
            reason = request.failure or _NO_REASON
            label = request_label(request.url, served=network.served)
            network.failed.append(f"{label} ({reason})")

    def on_response(response: ResponseLike) -> None:
        if response.status >= _HTTP_ERROR_FLOOR:
            label = request_label(response.url, served=network.served)
            network.http_errors.append(f"{response.status} {label}")

    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)


def _load(page: PageLike, job: _Job) -> None:
    """Navigate to the target's page and wait for its `load` event, in the watchdog."""
    with (
        job.bound_timed("page.goto"),
        engine_errors(f"{job.where}: {job.target.page} did not load"),
    ):
        page.goto(
            job.base_url + job.target.page, wait_until="load", timeout=job.timeout_ms
        )


def _settle_and_print(
    page: PageLike, job: _Job, started: float, found: list[Problem]
) -> _Outcome:
    """Run the actions, settle, wait for the print moment, read back, and print.

    Each problem a step finds goes into `found` as the step ends, so a later step that
    stops the state does not take it away.
    """
    _run_actions(page, job)
    _settle(page, job, started)
    found.extend(_wait_for_print_moment(page, job, started))
    probe = _probe(page, job)  # the read-back the print sees
    printed_at_ms = job.elapsed_ms(started)
    pdfs = tuple(
        _print(page, job, backgrounds)
        for backgrounds in BACKGROUND_PASSES[job.settings.backgrounds]
    )
    return _Outcome(
        probe=probe, printed_at_ms=printed_at_ms, pdfs=pdfs, problems=tuple(found)
    )


def _run_actions(page: PageLike, job: _Job) -> None:
    """Run the state's actions in order; the first that does not complete stops the state."""
    for action in job.state.actions:
        act = _action_call(page, action, job)
        try:
            act()
        except Exception as error:
            # Playwright's error class is not importable here (see `engine_errors`).
            raise _Stopped(finding(action.failure())) from error


def _action_call(page: PageLike, action: Action, job: _Job) -> Callable[[], object]:
    """The Playwright call one action makes, built before it runs.

    Built outside the call's `try`, so an action no targets file can hold (a click with no
    selector) raises here as the programming error it is, never as a finding.
    """
    match action:
        case Action(kind="click", selector=str(selector)):
            return lambda: _click(page, job, selector)
        case Action(kind="wait_for", selector=str(selector)):
            return lambda: _wait_for(page, job, selector)
        case Action(kind="wait_ms", ms=int(ms)):
            return lambda: _wait(page, job, ms)
        case Action(kind="set_attribute", attribute=(str(), str())):
            return lambda: _set_attribute(page, job, action)
        case _:
            raise ValueError(f"not an action a targets file can hold: {action!r}")


def _click(page: PageLike, job: _Job, selector: str) -> None:
    """Click `selector` under the timeout, in the watchdog; a failure raises to the action."""
    with job.bound_timed("page.click"):
        page.click(selector, timeout=job.timeout_ms)


def _wait_for(page: PageLike, job: _Job, selector: str) -> None:
    """Wait for `selector` under the timeout, in the watchdog; a failure raises to the action."""
    with job.bound_timed("page.wait_for_selector"):
        page.wait_for_selector(selector, timeout=job.timeout_ms)


def _wait(page: PageLike, job: _Job, ms: float) -> None:
    """Wait `ms` on the page, in the watchdog: a wait takes no timeout of its own."""
    with job.bound("page.wait_for_timeout", waits_ms=ms):
        page.wait_for_timeout(ms)


def _set_attribute(page: PageLike, job: _Job, action: Action) -> None:
    """Set the action's attribute once, in the watchdog; no match raises to the action."""
    with job.bound("page.evaluate"):
        set_attribute(page, action)


def _probe(page: PageLike, job: _Job) -> ProbeResult:
    """Probe the page once, in the watchdog; any error stops the state with a finding."""
    with job.bound("page.evaluate"):
        try:
            return run_probe(page, job.target.reads, job.target.storage_reads)
        except Exception as error:
            # Playwright raising (an invalid selector) or a result the parser refuses: either
            # way this state has no read-back to print against.
            raise _Stopped(finding(f"probe failed: {first_line(error)}")) from error


def _settle(page: PageLike, job: _Job, started: float) -> None:
    """Poll the probe until images and fonts have arrived, or the timeout has passed.

    An unsettled page is printed anyway, and the read-back taken just before the print says
    what was missing: the record shows what paper would get.
    """
    while (
        not _probe(page, job).settled
        and job.clock() - started <= job.settings.timeout_s
    ):
        _wait(page, job, SETTLE_POLL_MS)


def _wait_for_print_moment(
    page: PageLike, job: _Job, started: float
) -> tuple[Problem, ...]:
    """Wait until `print_at_ms` after navigation began; warn when that moment had passed."""
    asked = job.state.print_at_ms
    if asked is None:
        return ()
    now = job.elapsed_ms(started)
    if now > asked:
        return (warning(f"printed at {now} ms, later than the {asked} ms asked"),)
    if now < asked:
        _wait(page, job, asked - now)
    return ()


def _print(page: PageLike, job: _Job, backgrounds: bool) -> MadePdf:
    """Print one PDF of the page as it stands, in the watchdog, at the margin it records.

    The state's margin is computed once, and that one value is both sent and recorded.
    """
    path = job.settings.pdf_dir / pdf_name(job.target.name, job.state.name, backgrounds)
    margin = state_margin_inches(job.state, MARGIN_INCHES)
    options = print_options(
        backgrounds=backgrounds,
        header_footer=job.settings.header_footer,
        margin_inches=margin,
    )
    with job.bound("page.pdf"), engine_errors(f"{job.where}: page.pdf failed"):
        page.pdf(path=path, **options)
    return MadePdf(backgrounds=backgrounds, path=path, margin_inches=margin)


def _record(
    job: _Job, load_ms: int, network: NetworkRecord, outcome: _Outcome
) -> StateRecord:
    """The state's record: its settings, how far it got, and every problem found."""
    state, probe, hosts = job.state, outcome.probe, job.expected_hosts
    emulated = color_scheme_emulated(state.color_scheme)
    judged = state_problems(
        state, probe, network, expected_hosts=hosts, color_scheme_emulated=emulated
    )
    return StateRecord(
        target=job.target.name,
        state=state.name,
        page=job.target.page,
        engine=job.engine,
        browser_version=job.browser_version,
        viewport=job.settings.viewport,
        color_scheme=state.color_scheme,
        color_scheme_emulated=emulated,
        reduced_motion=state.reduced_motion,
        reduced_motion_emulated=reduced_motion_emulated(state.reduced_motion),
        storage=state.storage,
        actions=state.actions,
        print_at_ms=state.print_at_ms,
        page_width=state.page_width,
        margin_inches=state_margin_inches(state, MARGIN_INCHES),
        probe=probe,
        network=network,
        load_ms=load_ms,
        printed_at_ms=outcome.printed_at_ms,
        pdfs=outcome.pdfs,
        problems=(*outcome.problems, *judged),
    )
