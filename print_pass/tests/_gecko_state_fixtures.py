"""Shared by the Gecko state suites: a scripted session, a driver's log, a client, and one state.

`FakeSession` answers each WebDriver command as its arguments script it, telling the page
scripts apart by `gecko_page`'s own constants, and raises what a case asks a command to raise.
`FakeDriver` stands in for `Geckodriver` as a state sees it: a log with a slice written during
the state, and the two listings. `FakeClient` opens the one session. All three write one call
log beside the watchdog's stand-in, so a case reads the whole order of what a state did.
`StateCase` builds a state's job in one line and runs it. Nothing here reaches a browser.

Entry point assumptions: imported by a suite under this folder, after its bootstrap; the
insert below reaches the tool's private `src/` from this file's own location. The watchdog's
stand-in, the clock and the probe's reply are `_print_playwright_fixtures`', shared with the
Chromium engine's suites.
"""

from __future__ import annotations

import dataclasses
import sys
from functools import partial
import tempfile
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_page  # noqa: E402 - the tool's private src/, via the insert above
import gecko_state  # noqa: E402 - as above
import print_actions  # noqa: E402 - as above
from _print_engine_fixtures import (  # noqa: E402 - beside this module
    BASE_URL,
    VIEWPORT,
    engine_settings,
    state,
    target,
)
from _print_playwright_fixtures import (  # noqa: E402 - as above
    CallLog,
    FakeBounder,
    FakeClock,
    call,
    probe_dict,
)
from gecko_client import WebDriverError  # noqa: E402 - the tool's private src/
from print_record import EngineSettings, StateRecord  # noqa: E402 - as above
from print_targets import State, Target  # noqa: E402 - as above

#: The Firefox a session names, and the version it reports.
FIREFOX_PID: Final = 4321
VERSION: Final = "157.0"

#: The element reference every Find Element answers with, unless a case says otherwise.
ELEMENT: Final = "8f9e0d1c-2b3a-4c5d-8e6f-7a8b9c0d1e2f"

#: The loopback port the dead proxy holds for the run.
DEAD_PORT: Final = 40001

#: What a print hands back: the start of a PDF.
PDF: Final = b"%PDF-1.7\nfake\n"

#: The document's own navigation row for the default page, as the timing script returns it.
DOCUMENT_ROW: Final = [f"{BASE_URL}/", 200]

#: Firefox's own error when geckodriver cannot find it, as measured with Firefox hidden.
NO_FIREFOX: Final = (
    "Expected browser binary location, but unable to find binary in default location, no "
    "'moz:firefoxOptions.binary' capability provided, and no binary flag set on the "
    "command line"
)

#: The non-local guard's refusal, as Firefox writes it to geckodriver's log (measured).
REFUSAL: Final = (
    "FATAL ERROR: Non-local network connections are disabled and a connection attempt "
    "to 192.0.2.1 (192.0.2.1) was made.\n"
)


def timing(*rows: list[object], document: list[object] | None = None) -> list[object]:
    """What the timing script returns: the navigation row, then each resource row."""
    return [[DOCUMENT_ROW if document is None else document], list(rows)]


def firefox_settings(pdf_dir: Path, **changes: object) -> EngineSettings:
    """The command line's defaults under `--engine firefox`, with `changes` made."""
    settings = dataclasses.replace(engine_settings(pdf_dir), engine="firefox")
    return dataclasses.replace(settings, **changes)  # type: ignore[arg-type]


def _next[T](answers: list[T]) -> T:
    """The next scripted answer; the last one repeats, as a page that stays as it is."""
    return answers.pop(0) if len(answers) > 1 else answers[0]


def _first(args: Sequence[object]) -> object:
    """The selector a `set_attribute` script is handed: the first of its one list argument."""
    (handed,) = args
    return handed[0] if isinstance(handed, list) else None


def _nothing() -> None:
    """What a call does in the browser by default: nothing a case can see."""


class FakeSession:
    """One WebDriver session that answers as its arguments script it, logging every call.

    `fail` maps a call name (`navigate <url>`, `print`, `delete`, `execute <kind>`, ...) to
    the error that call raises. Each list of answers is consumed in order, its last repeated.
    """

    def __init__(
        self,
        log: CallLog,
        *,
        viewports: Sequence[object] = ([VIEWPORT.width, VIEWPORT.height],),
        seed: object = (404, 0),
        timing_reply: object = None,
        probes: Sequence[object] = (),
        visible: Mapping[str, Sequence[object]] | None = None,
        elements: Mapping[str, Sequence[str | None]] | None = None,
        clicks: Sequence[Exception | None] = (None,),
        unmatched: frozenset[str] = frozenset(),
        fail: Mapping[str, BaseException] | None = None,
        process_id: int | None = FIREFOX_PID,
        on_navigate: Callable[[str], None] | None = None,
        on_delete: Callable[[], None] | None = None,
    ) -> None:
        """Answer with what is given; every default is a quiet page that settles at once."""
        self.log = log
        self.browser_version = VERSION
        self.process_id = process_id
        self._viewports = list(viewports)
        self._seed = list(seed) if isinstance(seed, tuple) else seed
        self._timing = timing() if timing_reply is None else timing_reply
        self._probes = list(probes) or [probe_dict()]
        self._visible = {key: list(value) for key, value in (visible or {}).items()}
        self._elements = {key: list(value) for key, value in (elements or {}).items()}
        self._clicks = list(clicks)
        self._unmatched = unmatched  # selectors a `set_attribute` matches nothing for
        self._fail = dict(fail or {})
        self._on_navigate = on_navigate
        self._on_delete = on_delete
        # Every WebDriver Print body sent, in order: the log keeps only its backgrounds.
        self.print_bodies: list[Mapping[str, object]] = []

    def _called(
        self, name: str, *args: object, during: Callable[[], None] = _nothing
    ) -> None:
        """Log the call, run `during` as the browser would while it ran, then fail as scripted."""
        self.log.append(call(name, *args))
        during()
        failure = self._fail.get(" ".join((name, *map(str, args[:1]))))
        if failure is None:
            failure = self._fail.get(name)
        if failure is not None:
            raise failure

    def set_window_size(self, width: int, height: int) -> None:
        """Log the size asked for."""
        self._called("set_window_size", width, height)

    def navigate(self, url: str) -> None:
        """Log the URL, and let the case write to the driver's log as the page loads."""
        hook = self._on_navigate
        self._called(
            "navigate", url, during=_nothing if hook is None else partial(hook, url)
        )

    def execute(self, script: str, *args: object) -> object:
        """Answer the page script `script` is, by `gecko_page`'s constants."""
        kind = _SCRIPT_KINDS[script]
        self._called(f"execute {kind}", *args)
        answers: dict[str, Callable[[], object]] = {
            "viewport": lambda: _next(self._viewports),
            "seed": lambda: self._seed,
            "store": lambda: None,
            "probe": lambda: _next(self._probes),
            "timing": lambda: self._timing,
            "visible": lambda: _next(self._visible.get(str(args[0]), [True])),
            "set_attribute": lambda: _first(args) not in self._unmatched,
        }
        return answers[kind]()

    def find_css(self, selector: str) -> str | None:
        """The element the script names for `selector`, or `ELEMENT`."""
        self._called("find_css", selector)
        return _next(self._elements.get(selector, [ELEMENT]))

    def click(self, element: str) -> None:
        """Click, or raise the next scripted click error."""
        self._called("click", element)
        outcome = _next(self._clicks)
        if outcome is not None:
            raise outcome

    def print_pdf(self, parameters: Mapping[str, object]) -> bytes:
        """Log the backgrounds asked for, keep the whole body sent; hand back the PDF."""
        self.print_bodies.append(parameters)
        self._called("print", parameters["background"])
        return PDF

    def delete(self) -> None:
        """Log the deletion, and let the case write to the driver's log as Firefox closes."""
        self._called("delete", during=self._on_delete or _nothing)


#: Which page script each `execute` runs, by the script's own text.
_SCRIPT_KINDS: Final = {
    gecko_page.VIEWPORT_SCRIPT: "viewport",
    gecko_page.SEED_SCRIPT: "seed",
    gecko_page.STORE_SCRIPT: "store",
    gecko_page.PROBE_SCRIPT: "probe",
    gecko_page.TIMING_SCRIPT: "timing",
    gecko_page.VISIBLE_SCRIPT: "visible",
    gecko_page.probe_script(print_actions.SET_ATTRIBUTE_SOURCE): "set_attribute",
}


class FakeDriver:
    """`Geckodriver` as a state sees it: its log, and the two listings, each logged."""

    def __init__(
        self,
        log: CallLog,
        *,
        before: str = "",
        fail: Mapping[str, BaseException] | None = None,
    ) -> None:
        """A log holding `before` already; `write` adds to it as the state runs."""
        self.log = log
        self.text = before
        self._fail = dict(fail or {})

    def write(self, text: str) -> None:
        """Firefox or geckodriver writing to the log."""
        self.text += text

    def log_offset(self) -> int:
        """How many bytes the log holds now."""
        return len(self.text.encode("utf-8"))

    def log_since(self, offset: int) -> str:
        """The log from byte `offset` on."""
        return self.text.encode("utf-8")[offset:].decode("utf-8")

    def note_firefox(self, pid: int) -> None:
        """Log the Firefox noted, or raise as the script says."""
        self.log.append(call("note_firefox", pid))
        if "note_firefox" in self._fail:
            raise self._fail["note_firefox"]

    def record_helpers(self) -> None:
        """Log the recording, or raise as the script says."""
        self.log.append(call("record_helpers"))
        if "record_helpers" in self._fail:
            raise self._fail["record_helpers"]


class FakeClient:
    """Opens the one scripted session, or raises what the case says New Session raises."""

    def __init__(self, session: FakeSession, failure: Exception | None = None) -> None:
        """Hand out `session`, unless `failure` is given."""
        self.session = session
        self._failure = failure

    def new_session(self, capabilities: Mapping[str, object]) -> FakeSession:
        """Log the capabilities asked for, then open or fail."""
        self.session.log.append(call("new_session", capabilities))
        if self._failure is not None:
            raise self._failure
        return self.session


class TickClock:
    """A clock that moves only when the state sleeps, by exactly what it slept."""

    def __init__(self, log: CallLog) -> None:
        """Start at zero, logging every sleep into `log`."""
        self.now = 0.0
        self._log = log

    def __call__(self) -> float:
        """The current reading."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Log the sleep and move the clock by it."""
        self._log.append(call("sleep", round(seconds, 6)))
        self.now += seconds


def webdriver_error(
    command: str, error: str, message: str = "it failed"
) -> WebDriverError:
    """A WebDriver failure of `command`, under the W3C code `error`."""
    return WebDriverError(command, error, message)


class StateCase(unittest.TestCase):
    """A PDF folder, one call log, and a state run against the fakes in one line."""

    def setUp(self) -> None:
        """Give the case a PDF folder of its own, removed after it."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pdf_dir = Path(folder.name) / "pdfs"
        self.pdf_dir.mkdir()
        self.scratch_root = Path(folder.name) / "scratch"
        self.log: CallLog = []
        self.driver = FakeDriver(self.log)

    def session(self, **script: object) -> FakeSession:
        """A session logging into this case's log, answering as `script` says."""
        return FakeSession(self.log, **script)  # type: ignore[arg-type]

    def job(
        self,
        the_state: State | None = None,
        *,
        the_target: Target | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        **settings: object,
    ) -> gecko_state.StateJob:
        """The job of one state (the default) of one target (the default)."""
        chosen = the_target or target(states=(the_state or state(),))
        return gecko_state.StateJob(
            target=chosen,
            state=chosen.states[0],
            settings=firefox_settings(self.pdf_dir, **settings),
            base_url=BASE_URL,
            dead_port=DEAD_PORT,
            expected_hosts=(),
            scratch_root=self.scratch_root,
            bounder=FakeBounder(self.log),
            clock=clock or FakeClock(),
            sleep=sleep or (lambda seconds: self.log.append(call("sleep", seconds))),
        )

    def run_state(
        self,
        session: FakeSession | None = None,
        job: gecko_state.StateJob | None = None,
        *,
        failure: Exception | None = None,
    ) -> StateRecord:
        """Run one state against `session` (a quiet one) under `job` (the default)."""
        client = FakeClient(session or self.session(), failure)
        return gecko_state.run_state(client, self.driver, job or self.job())

    def calls(self, *skip: str) -> list[tuple[str, ...]]:
        """The log without the watchdog's entries, the sleeps, or any call named in `skip`."""
        hidden = {"bound", "unbound", "sleep", *skip}
        return [entry for entry in self.log if entry[0] not in hidden]
