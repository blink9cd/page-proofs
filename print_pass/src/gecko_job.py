"""One Firefox state's work, and the session, client and driver it is handed.

`StateJob` is everything one state needs that the run decided: what it prints, under which
settings, on which served origin and dead port, where the scratch folder is, and the
watchdog, clock and sleep it runs on. Its `pause` is the one way a state waits: a poll of at
most `PAGE_POLL_SECONDS` at a time inside a loop, never one long sleep. The three protocols
are what a state calls on WebDriver's session and client and on geckodriver's process, so a
suite hands it stand-ins for all three.

Entry point assumptions: the standard library, and the tool's own `gecko_budget`,
`print_playwright`, `print_record`, `print_targets` and `print_watchdog` only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from gecko_budget import PAGE_POLL_SECONDS
from print_playwright import MS_PER_SECOND
from print_record import EngineSettings
from print_targets import State, Target
from print_watchdog import Bounder


@dataclass(frozen=True)
class StateJob:
    """One state's work: what it prints, under which settings, on which origin and port."""

    target: Target
    state: State
    settings: (
        EngineSettings  # the run's, with the targets file's viewport and backgrounds
    )
    base_url: str  # the served origin
    dead_port: int  # the loopback port the proxy points every request off loopback at
    expected_hosts: tuple[
        str, ...
    ]  # the targets file's hosts off loopback, for the judge
    scratch_root: Path  # kept out of the record
    bounder: Bounder
    clock: Callable[[], float]
    sleep: Callable[[float], None]

    @property
    def where(self) -> str:
        """The state, named as a report and a bound name it: `home/default`."""
        return f"{self.target.name}/{self.state.name}"

    def elapsed_ms(self, started: float) -> int:
        """Whole milliseconds since `started`, a reading of this job's clock."""
        return round((self.clock() - started) * MS_PER_SECOND)

    def pause(self, seconds: float) -> None:
        """Wait `seconds`, in sleeps of at most `PAGE_POLL_SECONDS`.

        Each step is taken off what is left, so the last one ends the wait exactly.
        """
        left = seconds
        while left > 0:
            step = min(left, PAGE_POLL_SECONDS)
            self.sleep(step)
            left -= step


class SessionLike(Protocol):
    """A WebDriver session, as a state drives it: `gecko_client.GeckoSession`."""

    browser_version: str
    process_id: int | None

    def set_window_size(self, width: int, height: int) -> None:
        """Ask for an outer window of `width` by `height` CSS pixels."""

    def navigate(self, url: str) -> None:
        """Load `url`, returning once it has loaded."""

    def execute(self, script: str, *args: object) -> object:
        """Run `script` as a synchronous function body given `args`; what it returns."""

    def find_css(self, selector: str) -> str | None:
        """The reference of the first element `selector` matches, or None for none."""

    def click(self, element: str) -> None:
        """Click the element `element` refers to."""

    def print_pdf(self, parameters: Mapping[str, object]) -> bytes:
        """Print the page with WebDriver's Print `parameters`; the PDF it made."""

    def delete(self) -> None:
        """End the session, which closes its Firefox."""


class ClientLike(Protocol):
    """A WebDriver client, as a state opens its session: `gecko_client.GeckoClient`."""

    def new_session(self, capabilities: Mapping[str, object]) -> SessionLike:
        """Start a session asking for `capabilities`."""


class DriverLike(Protocol):
    """geckodriver's process, as a state sees it: `gecko_launch.Geckodriver`."""

    def log_offset(self) -> int:
        """How many bytes the log holds now."""

    def log_since(self, offset: int) -> str:
        """Everything written to the log from byte `offset` on."""

    def note_firefox(self, pid: int) -> None:
        """Note the Firefox a session names, and record its crash helper."""

    def record_helpers(self) -> None:
        """Record the crash helper of each noted Firefox still running."""
