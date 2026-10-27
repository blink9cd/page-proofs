"""What the engine is given and what it records: its settings, each state's record, its errors.

These are the engine's contract with its callers. The CLI builds `EngineSettings` and catches
`EngineError`; the report reads each `StateRecord`, its `Problem`s, its `NetworkRecord` and
its `MadePdf`s, each carrying the margin it was printed with, which is the margin its ink is
measured against. They are frozen dataclasses holding plain values, so a record can be compared
whole and handed on without a live browser behind it. `engine_errors` is how the engine turns
whatever a browser raises into the one `EngineError` the CLI catches.

Entry point assumptions: the standard library, `print_probe`'s `ProbeResult` and
`print_targets`' records only.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final, Literal

from print_probe import ProbeResult
from print_targets import Action, Viewport

type Severity = Literal["finding", "warning"]
type Engine = Literal["shell", "chrome", "firefox"]
type Backgrounds = Literal["off", "on", "both"]

#: The engine that prints through geckodriver's WebDriver Print. The other two print through
#: Playwright's `page.pdf()`, the shell and installed Chrome alike.
FIREFOX: Final[Engine] = "firefox"

#: Centimeters per inch, exactly: how each engine's print margin, sent in centimeters, is the
#: exact inches its PDF is measured against.
CM_PER_INCH: Final = Fraction(254, 100)


class EngineError(RuntimeError):
    """The engine could not measure honestly: a browser, page load or print failed outright."""


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a state: a finding fails the run, a warning is reported."""

    severity: Severity
    what: str


@dataclass(frozen=True)
class EngineSettings:
    """How every state is printed, from the command line.

    A targets file may override two of these, `viewport` and `backgrounds`; the print margin
    is no setting here, since each state's comes from its own `page_width` or the engine.
    """

    engine: Engine
    backgrounds: Backgrounds
    header_footer: bool
    viewport: Viewport
    timeout_s: float
    pdf_dir: Path


@dataclass(frozen=True)
class NetworkRecord:
    """What a state's page did not get from the network, what it threw, and what CSP refused.

    A loopback request is named by its path and query, so no port is recorded; any other by
    its whole URL. A violation is the shared collector's own line for it, with a refused URL
    named the same way.
    """

    failed: tuple[str, ...]  # "<path> (<failure text>)"
    http_errors: tuple[str, ...]  # "<status> <path>"
    blocked: tuple[str, ...]  # full URLs aborted because they leave loopback
    page_errors: tuple[str, ...]  # first line of each uncaught page error
    # "<directive> blocked <what> on <document>", then ": <sample>" when there is one
    csp_violations: tuple[str, ...]


@dataclass(frozen=True)
class MadePdf:
    """One PDF a state printed: with backgrounds or without, where, and at what margin.

    `margin_inches` is the margin on every side the engine printed this PDF with, exactly:
    the reader measures the page's blank share against it, so it travels with the PDF
    rather than being assumed for the whole run.
    """

    backgrounds: bool
    path: Path
    margin_inches: Fraction


@dataclass(frozen=True)
class StateRecord:
    """Everything one state did and saw: its settings, read-back, network, timings and PDFs."""

    target: str
    state: str
    page: str
    # "shell" | "chrome" | "firefox". A native-dark state is "chrome" under a Playwright
    # engine, whatever the settings name, since the shell ignores the flag that darkens
    # Chrome; under Firefox it stays "firefox", since Firefox's own preference darkens it
    # and a Firefox run never launches Chrome.
    engine: str
    browser_version: str
    viewport: Viewport
    color_scheme: str  # as requested
    color_scheme_emulated: bool
    reduced_motion: str
    reduced_motion_emulated: bool
    storage: tuple[tuple[str, str], ...]
    actions: tuple[Action, ...]
    print_at_ms: int | None
    page_width: int | None  # as the state asked; None: the engine's own margin
    # The margin on every side the state is printed with, exact: the one each of its PDFs
    # records, and the one it would have printed with where it stopped before printing.
    margin_inches: Fraction
    probe: ProbeResult | None  # None when the state failed before its read-back
    network: NetworkRecord
    load_ms: int
    printed_at_ms: int | None
    pdfs: tuple[MadePdf, ...]
    problems: tuple[Problem, ...]


def pdf_name(target: str, state: str, backgrounds: bool) -> str:
    """The file a state's PDF is written to: `home--after-start--bg-off.pdf`.

    Target and state names never hold `--` themselves, so no two states share a file.
    """
    return f"{target}--{state}--bg-{'on' if backgrounds else 'off'}.pdf"


def first_line(error: object) -> str:
    """The first line of an error's text: what a one-line finding can carry of it."""
    return (str(error).splitlines() or [""])[0]


@contextmanager
def engine_errors(where: str, *passing: type[Exception]) -> Iterator[None]:
    """Raise whatever the block raises as an `EngineError`: `<where>: <its first line>`.

    An `EngineError` already says where it happened, and a `ValueError` is the engine refusing
    what it was handed -- a programming error, never the browser's -- so both pass untouched,
    as does each exception type in `passing`.
    """
    untouched: tuple[type[Exception], ...] = (EngineError, ValueError, *passing)
    try:
        yield
    except untouched:
        raise
    except Exception as error:
        # Playwright's own error class is not importable here (the engine never imports
        # Playwright), and a browser that has gone fails whatever is called on it next: an
        # error left raw would end the run with the finding code, for a run never measured.
        raise EngineError(f"{where}: {first_line(error)}") from error
