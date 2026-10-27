"""One Firefox state of one target, printed: a session of its own, the page loaded, printed, recorded.

`run_state` is the whole of a Firefox state, and returns the same `StateRecord` a Chromium
state does. In order: a session asking for the engine's isolation and the state's own prefs,
whose Firefox is noted with the driver; the window sized until the page reads the viewport
asked, exactly; the stored values set on the served origin's storage seed page, a missing
path the loopback server answers with its own script-free error page, so the page's scripts
never run before them; the page loaded; the actions, settling and print moment
(`gecko_actions`); the last read-back; Resource Timing and the state's slice of the log read
back; and one PDF or two from that one load, written where the record says.

A page whose isolation did not hold is refused before it prints: a request answered off its
origin, a document off it, or a document that is not on the served origin -- `about:blank`,
`data:`, `blob:`. So is a Resource Timing read that is not plain rows. Each is an `EngineError` (exit 2). The scratch folder's path is
taken out of the log before anything of it is recorded, as a cheap safeguard: no measured
path carries it into the record, since only the page's own `JavaScript error:` lines are
kept -- its scripts on the served origin, or written into it inline -- and of each only its
first line. Whatever happens, the session's crash helper is recorded while its Firefox still
runs, and then the session is deleted.

Every error names the state. One that ended a state is first checked against the log for
the guard, which ends the browser at its refusal: a breach is reported as itself,
`IsolationBreach`, never as whatever call the dying browser then failed. A state that
succeeded is never checked: the guard would have ended it, and the page's console writes to
the same log, so the sentence there may be the page's own. The whole state runs inside one
watchdog bound, `gecko_budget.state_seconds`, and the client bounds each call.

Entry point assumptions: `gecko_job`'s protocols stand for the client, its session and the
driver; the rest is the tool's own.
"""

from __future__ import annotations

import math
from typing import Final

from gecko_actions import (
    Stopped,
    run_actions,
    settle,
    take_probe,
    wait_for_print_moment,
)
from gecko_budget import state_seconds
from gecko_client import WebDriverError
from gecko_job import ClientLike, DriverLike, SessionLike
from gecko_job import StateJob as StateJob
from gecko_launch import (
    MAX_LOG_READ_BYTES,
    IsolationBreach,
    capabilities,
    isolation_breach,
    isolation_prefs,
    state_prefs,
)
from gecko_network import (
    TimingEntry,
    isolation_failures,
    network_record,
    timing_problems,
)
from gecko_page import (
    SEED_PATH,
    SEED_SCRIPT,
    STORE_SCRIPT,
    TIMING_SCRIPT,
    VIEWPORT_SCRIPT,
    is_served,
    scratch_forms,
    seed_confirmed,
    timing_rows,
    viewport_size,
    without_scratch,
)
from gecko_print import MARGIN_INCHES, print_parameters
from print_engine_state import BACKGROUND_PASSES
from print_judge import state_problems, warning
from print_playwright import MS_PER_SECOND
from print_probe import ProbeResult
from print_record import (
    FIREFOX,
    EngineError,
    MadePdf,
    NetworkRecord,
    Problem,
    StateRecord,
    first_line,
    pdf_name,
)
from print_targets import state_margin_inches

#: The W3C error code New Session answers with when no browser could be started.
SESSION_NOT_CREATED: Final = "session not created"

#: What a session that was not created adds: Firefox missing is the likeliest cause.
INSTALL_FIREFOX: Final = (
    "if Firefox is not installed, install it: brew install --cask firefox, "
    "or Mozilla's installer"
)

# What each state's watchdog bound is named, before the state's own name.
_BOUND_NAME: Final = "firefox state"


def run_state(client: ClientLike, driver: DriverLike, job: StateJob) -> StateRecord:
    """Print one state in a session of its own, and record it.

    Raises:
        IsolationBreach: Firefox's non-local guard refused a connection, naming the state.
        EngineError: anything else ended the state, or its page left loopback, or is not
            the served page; naming the state.
    """
    offset = driver.log_offset()
    passes = len(BACKGROUND_PASSES[job.settings.backgrounds])
    seconds = state_seconds(job.state, timeout_s=job.settings.timeout_s, prints=passes)
    with job.bounder.bound(seconds, f"{_BOUND_NAME} {job.where}"):
        try:
            record = _in_session(client, driver, job, offset)
        except EngineError as error:
            raise _named(job, driver.log_since(offset), error) from error
    return record


def _named(job: StateJob, log_text: str, error: EngineError) -> EngineError:
    """The error a state ends with: the guard's breach if the log names one, else `error`."""
    breach = isolation_breach(log_text)
    if breach is not None:
        return IsolationBreach(f"{job.where}: {breach}")
    return EngineError(f"{job.where}: {first_line(error)}")


def _in_session(
    client: ClientLike, driver: DriverLike, job: StateJob, offset: int
) -> StateRecord:
    """Open the state's session, run the state in it, and end it whatever happened."""
    session = _open_session(client, job)
    try:
        _note_firefox(session, driver)
        record = _run_page(session, driver, job, offset)
    except BaseException as failure:
        _end_after(session, driver, failure)
        raise
    _end(session, driver)
    return record


def _open_session(client: ClientLike, job: StateJob) -> SessionLike:
    """A session with the isolation's prefs and the state's, its timeouts at the run's."""
    prefs = isolation_prefs(job.dead_port, served=job.base_url) | state_prefs(
        color_scheme=job.state.color_scheme, reduced_motion=job.state.reduced_motion
    )
    timeout_ms = math.ceil(job.settings.timeout_s * MS_PER_SECOND)
    try:
        return client.new_session(capabilities(prefs=prefs, timeout_ms=timeout_ms))
    except WebDriverError as error:
        if error.error != SESSION_NOT_CREATED:
            raise
        raise EngineError(
            f"Firefox did not start: {first_line(error)}; {INSTALL_FIREFOX}"
        ) from error


def _note_firefox(session: SessionLike, driver: DriverLike) -> None:
    """Note the session's Firefox with the driver, which records its crash helper."""
    if session.process_id is None:
        raise EngineError("the session named no Firefox process (moz:processID)")
    driver.note_firefox(session.process_id)


def _end(session: SessionLike, driver: DriverLike) -> None:
    """Record the session's crash helper while its Firefox runs, then delete the session."""
    try:
        driver.record_helpers()
    finally:
        session.delete()


def _end_after(
    session: SessionLike, driver: DriverLike, failure: BaseException
) -> None:
    """End the session after `failure`; an end that fails too is named beside it."""
    try:
        _end(session, driver)
    except EngineError as also:
        raise EngineError(
            f"{first_line(failure)}; and the session could not be ended: "
            f"{first_line(also)}"
        ) from failure


def _run_page(
    session: SessionLike, driver: DriverLike, job: StateJob, offset: int
) -> StateRecord:
    """Size, seed and load the page, take it as far as it goes, read it back, and print."""
    _size_viewport(session, job)
    if job.state.storage:
        _seed_storage(session, job)
    started = job.clock()
    _load(session, job)
    load_ms = job.elapsed_ms(started)
    found: list[Problem] = []
    probe: ProbeResult | None = None
    try:
        run_actions(session, job)
        settle(session, job, started)
        found.extend(wait_for_print_moment(job, started))
        probe = take_probe(session, job)  # the read-back the print sees
    except Stopped as stopped:
        found.append(stopped.problem)
    entries, network = _read_network(session, driver, job, offset)
    printed_at_ms = None if probe is None else job.elapsed_ms(started)
    pdfs = () if probe is None else _print_all(session, job)
    found.extend((*timing_problems(entries), *_log_problems(driver, offset)))
    return _record(job, session, load_ms, probe, network, printed_at_ms, pdfs, found)


def _log_problems(driver: DriverLike, offset: int) -> tuple[Problem, ...]:
    """A warning when the state's slice of the log was longer than one read takes."""
    if driver.log_offset() - offset <= MAX_LOG_READ_BYTES:
        return ()
    return (
        warning(
            f"page errors may be incomplete: only the last {MAX_LOG_READ_BYTES:,} bytes "
            "of the state's log were read"
        ),
    )


def _size_viewport(session: SessionLike, job: StateJob) -> None:
    """Size the window so the page reads the viewport asked: corrected once, then exact."""
    asked = job.settings.viewport
    session.set_window_size(asked.width, asked.height)
    width, height = viewport_size(session.execute(VIEWPORT_SCRIPT))
    if (width, height) == (asked.width, asked.height):
        return
    session.set_window_size(2 * asked.width - width, 2 * asked.height - height)
    width, height = viewport_size(session.execute(VIEWPORT_SCRIPT))
    if (width, height) != (asked.width, asked.height):
        raise EngineError(
            f"the viewport is {width}x{height} after one correction, "
            f"not the {asked.width}x{asked.height} asked"
        )


def _seed_storage(session: SessionLike, job: StateJob) -> None:
    """Set the state's stored values on the seed page, where no script of the site runs."""
    session.navigate(job.base_url + SEED_PATH)
    if not seed_confirmed(session.execute(SEED_SCRIPT)):
        raise EngineError(
            f"{SEED_PATH} on the served origin is not the loopback server's own "
            "script-free error page, so storage cannot be set before the page's scripts run"
        )
    session.execute(STORE_SCRIPT, [list(pair) for pair in job.state.storage])


def _load(session: SessionLike, job: StateJob) -> None:
    """Navigate to the target's page and wait for its load event."""
    try:
        session.navigate(job.base_url + job.target.page)
    except WebDriverError as error:
        raise EngineError(
            f"{job.target.page} did not load: {first_line(error)}"
        ) from error


def _read_network(
    session: SessionLike, driver: DriverLike, job: StateJob, offset: int
) -> tuple[tuple[TimingEntry, ...], NetworkRecord]:
    """Read Resource Timing and the log back, refuse a page that left loopback, record.

    Raises:
        IsolationBreach: the log names a connection Firefox's non-local guard refused.
        EngineError: the read is malformed, the page left loopback, or the document is not
            on the served origin.
    """
    document, entries = timing_rows(session.execute(TIMING_SCRIPT))
    log_text = driver.log_since(offset)
    failures = isolation_failures(entries, document, job.base_url)
    if failures:
        raise EngineError(f"the page left loopback: {'; '.join(failures)}")
    if not is_served(document["name"], job.base_url):
        raise EngineError(f"the document is not the served page: {document['name']}")
    kept = without_scratch(log_text, scratch_forms(job.scratch_root))
    return entries, network_record(entries, document, job.base_url, kept)


def _print_all(session: SessionLike, job: StateJob) -> tuple[MadePdf, ...]:
    """Print each backgrounds side the settings ask for, in order, from the one load."""
    return tuple(
        _print(session, job, backgrounds)
        for backgrounds in BACKGROUND_PASSES[job.settings.backgrounds]
    )


def _print(session: SessionLike, job: StateJob, backgrounds: bool) -> MadePdf:
    """Print one PDF of the page as it stands, and write it where the record says.

    The state's margin is computed once, and that one value is both sent in the Print body
    and carried by the record, so the PDF is measured against the margin it was printed with.
    """
    path = job.settings.pdf_dir / pdf_name(job.target.name, job.state.name, backgrounds)
    margin = state_margin_inches(job.state, MARGIN_INCHES)
    parameters = print_parameters(backgrounds=backgrounds, margin_inches=margin)
    try:
        pdf = session.print_pdf(parameters)
    except WebDriverError as error:
        raise EngineError(f"print failed: {first_line(error)}") from error
    try:
        path.write_bytes(pdf)
    except OSError as error:
        raise EngineError(
            f"{path} could not be written: {first_line(error)}"
        ) from error
    return MadePdf(backgrounds=backgrounds, path=path, margin_inches=margin)


def _record(
    job: StateJob,
    session: SessionLike,
    load_ms: int,
    probe: ProbeResult | None,
    network: NetworkRecord,
    printed_at_ms: int | None,
    pdfs: tuple[MadePdf, ...],
    found: list[Problem],
) -> StateRecord:
    """The state's record: Firefox's, with nothing emulated, and every problem found."""
    state = job.state
    judged = state_problems(
        state,
        probe,
        network,
        expected_hosts=job.expected_hosts,
        color_scheme_emulated=False,
    )
    return StateRecord(
        target=job.target.name,
        state=state.name,
        page=job.target.page,
        engine=FIREFOX,
        browser_version=session.browser_version,
        viewport=job.settings.viewport,
        color_scheme=state.color_scheme,
        color_scheme_emulated=False,
        reduced_motion=state.reduced_motion,
        reduced_motion_emulated=False,
        storage=state.storage,
        actions=state.actions,
        print_at_ms=state.print_at_ms,
        page_width=state.page_width,
        margin_inches=state_margin_inches(state, MARGIN_INCHES),
        probe=probe,
        network=network,
        load_ms=load_ms,
        printed_at_ms=printed_at_ms,
        pdfs=pdfs,
        problems=(*found, *judged),
    )
