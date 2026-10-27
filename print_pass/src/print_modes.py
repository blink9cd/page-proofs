"""The three runs: make PDFs of served pages, read given PDFs, compare two -- each to a report.

`run_make`, `run_read` and `run_compare` take a plan the CLI has already validated and whose
`--out` it has already cleared, and return the exit code. A make run under `--engine firefox`
branches before Playwright is looked for, so it never needs Playwright: off macOS it refuses
before anything is looked up, and without geckodriver it refuses naming how to install it
and Firefox; then `gecko_engine` prints, under a watchdog whose expiry is its run guard's.
Either engine prints the targets `print_redirects` resolved against the served tree: a
declared meta refresh at its destination, an undeclared one not at all, and the report
records both (`print_report_redirects`).
Each writes `<out>/report.json`, prints the report's summary (findings first) and where it
went, and returns the report's own code; a run that cannot measure honestly prints why on
stderr, writes no report, and returns `EXIT_CANNOT_MEASURE`. The plans, that code and the
message prefix are `print_plan`'s, since this module imports the PDF readers and the CLI
imports it only once a run starts.

Every PDF of one run is read with ONE identity, `host_identity()`'s, which also shapes the
make report: the identity the PDFs were read with is the one their report is scrubbed with.
An engine's error is printed on stderr and never reaches a file: its first line can name an
executable's path.

Entry point assumptions: the CLI's bootstrap has put this folder and the shared utilities
on the path; the plan's `--serve` is a folder and its PDFs are files.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from contextlib import AbstractContextManager
from fractions import Fraction
from pathlib import Path
from types import TracebackType
from typing import Final

# The shared loopback server and UTF-8 stdio policy: each a folder beside this tool's own,
# named rather than searched for, so a same-named folder above the clone is never imported
# in its place, and put near the front of the path, ahead of every installed distribution,
# so none of the same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "loopback_site"))
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "stdio_utf8"))

import gecko_engine
import gecko_launch
import loopback_site
import pdf_privacy
import print_engine
import print_preflight
import print_redirects
import print_report
import print_report_redirects
import stdio_utf8
from pdf_privacy import Identity
from pdf_raster import RasterError
from print_engine import EngineError, StateRecord
from print_plan import EXIT_CANNOT_MEASURE, PAIR, MakePlan, ReadPlan, say, warn
from print_playwright import PlaywrightLike
from print_preflight import SyncPlaywright
from print_record import CM_PER_INCH, FIREFOX, Engine, first_line
from print_redirects import Redirect, RedirectError
from print_targets import TargetsError, TargetsFile, adhoc_targets, load_targets
from print_watchdog import Bounder, Watchdog

#: What a make run says when Playwright itself is not importable, before what its engine
#: needs besides: the shell its pinned builds, and chrome installed Google Chrome, which never
#: starts them.
_NO_PLAYWRIGHT: Final = (
    "Playwright is not installed: install the repository's `print` extra "
    "(pip install '.[print]')"
)
_PLAYWRIGHT_ENGINE_NEEDS: Final = {
    "shell": ", then the pinned engine builds "
    "(python -m playwright install --only-shell chromium)",
    "chrome": "; the chrome engine also needs installed Google Chrome",
}

#: What a Firefox make run says when geckodriver is not found.
_NO_GECKODRIVER: Final = (
    "geckodriver was not found on PATH: `brew install geckodriver` installs it, and "
    "Firefox itself is needed too: brew install --cask firefox, "
    "or Mozilla's installer"
)

#: The margin a PDF given to a read run is measured against. Such a PDF -- Safari's, or any
#: made outside this tool -- states no margin of its own, so its blank share is measured as
#: though it had been printed at 1 cm, Chrome's dialog default: a stated assumption, chosen
#: here at the one call that reads it, never a default inside the measurement.
OUTSIDE_PDF_MARGIN_INCHES: Final = 1 / CM_PER_INCH

#: What the watchdog names the driver's start and stop: Playwright's own names for them.
_DRIVER_START: Final = "playwright.start"
_DRIVER_STOP: Final = "playwright.stop"


def run_make(plan: MakePlan, *, platform: str = sys.platform) -> int:
    """Load the targets, print every state, read and compare the PDFs, and report.

    `platform` is the one a Firefox run checks it is on, `sys.platform` unless a suite says.
    """
    executable = ""
    if plan.settings.engine == FIREFOX:
        try:
            gecko_launch.require_measured_platform(platform)
        except EngineError as error:
            warn(str(error))
            return EXIT_CANNOT_MEASURE
        executable = gecko_launch.find_geckodriver() or ""
        if not executable:
            warn(_NO_GECKODRIVER)
            return EXIT_CANNOT_MEASURE
    try:
        targets = (
            adhoc_targets(plan.pages)
            if plan.targets is None
            else load_targets(plan.targets)
        )
    except TargetsError as error:
        warn(str(error))
        return EXIT_CANNOT_MEASURE
    try:
        records, redirects = _print(plan, targets, executable)
    except (EngineError, RedirectError) as error:
        # Here and never in the report: its first line can name an executable's path.
        warn(str(error))
        return EXIT_CANNOT_MEASURE
    identity = pdf_privacy.host_identity()
    timeout = plan.settings.timeout_s
    # Each PDF is measured against the margin its own record says it was printed with.
    reports = {
        made.path: _read(
            made.path, plan.out, plan.dpi, timeout, identity, made.margin_inches
        )
        for record in records
        for made in record.pdfs
    }
    report = print_report.make_report(
        records,
        reports,
        _compare_pairs(records, plan),
        out_dir=plan.out,
        targets=targets,
        engine=plan.settings.engine,
        header_footer=plan.settings.header_footer,
        identity=identity,
    )
    with_redirects = print_report_redirects.with_redirects(
        report, redirects, out_dir=plan.out, identity=identity
    )
    return _record(with_redirects, plan.out)


def run_read(plan: ReadPlan) -> int:
    """Read every PDF into `--out`, and report."""
    plan.out.mkdir()
    identity = pdf_privacy.host_identity()
    reports = [
        _read(
            pdf, plan.out, plan.dpi, plan.timeout, identity, OUTSIDE_PDF_MARGIN_INCHES
        )
        for pdf in plan.pdfs
    ]
    return _record(print_report.read_report(reports), plan.out)


def run_compare(plan: ReadPlan) -> int:
    """Compare the two PDFs' renders and report; nothing is written if one cannot render."""
    first, second = plan.pdfs
    try:
        comparison = print_report.compare_pdfs(
            first, second, dpi=plan.dpi, timeout=plan.timeout, out_dir=plan.out
        )
    except RasterError as error:
        warn(f"could not compare {first.name} and {second.name}: {error}")
        return EXIT_CANNOT_MEASURE
    plan.out.mkdir()
    return _record(print_report.compare_report(comparison), plan.out)


def _print(
    plan: MakePlan, targets: TargetsFile, executable: str
) -> tuple[tuple[StateRecord, ...], tuple[Redirect, ...]]:
    """Print every state with the plan's engine: Firefox through `executable`, or Playwright.

    `--serve` is served first, and every target's meta refresh resolved against it before
    `--out` is made, so a refused redirect leaves nothing behind. The server outlives the
    engine. Returns the records and the redirects met.

    Raises:
        EngineError: the engine could not print, or, for Playwright, is not installed.
        RedirectError: a declared redirect cannot be followed.
    """
    sync_playwright = None if plan.settings.engine == FIREFOX else _playwright(plan)
    with loopback_site.serve_directory(plan.serve) as base_url:
        resolution = print_redirects.resolve_redirects(targets, base_url, plan.serve)
        _make_out(plan)
        if sync_playwright is None:
            say(f"geckodriver: {executable}")
            records = _print_firefox(plan, resolution.targets, executable, base_url)
        else:
            records = _print_states(plan, resolution.targets, sync_playwright, base_url)
    return records, resolution.redirects


def _playwright(plan: MakePlan) -> SyncPlaywright:
    """Playwright's entry point, its engine builds pointed at first.

    Raises:
        EngineError: Playwright is not installed.
    """
    _use_browsers(print_preflight.browsers_dir(plan.browsers, os.environ))
    sync_playwright = print_preflight.load_playwright()
    if sync_playwright is None:
        raise EngineError(_no_playwright(plan.settings.engine))
    return sync_playwright


def _make_out(plan: MakePlan) -> None:
    """Make `--out` and the PDF folder in it, once nothing stands in the run's way."""
    plan.out.mkdir()
    plan.settings.pdf_dir.mkdir()


def _no_playwright(engine: Engine) -> str:
    """Why a Playwright run cannot start: Playwright, and what `engine` needs besides."""
    return _NO_PLAYWRIGHT + _PLAYWRIGHT_ENGINE_NEEDS[engine]


def _print_firefox(
    plan: MakePlan, targets: TargetsFile, executable: str, base_url: str
) -> tuple[StateRecord, ...]:
    """Print every state served at `base_url` through the Firefox engine.

    The run's watchdog expires through its guard, which kills geckodriver first.

    Raises:
        EngineError: Firefox, geckodriver or the scratch failed, or isolation did not hold.
    """
    guard = gecko_engine.RunGuard()
    watchdog = Watchdog(on_expire=guard.on_expire)
    return gecko_engine.run_targets(
        base_url,
        targets,
        plan.settings,
        executable=executable,
        bounder=watchdog,
        guard=guard,
    )


def _use_browsers(browsers: Path | None) -> None:
    """Point Playwright at `browsers` before it starts, or leave it its own default."""
    if browsers is None:
        say("engine builds: Playwright's own default folder")
        return
    # Read once, when Playwright starts; absolute, since the driver has its own cwd. The
    # hermetic value names no folder, so it is handed on as given.
    hermetic = str(browsers) == print_preflight.HERMETIC_BROWSERS
    named = browsers if hermetic else browsers.absolute()
    os.environ[print_preflight.BROWSERS_ENV] = str(named)
    say(f"engine builds: {browsers}")


def _print_states(
    plan: MakePlan,
    targets: TargetsFile,
    sync_playwright: SyncPlaywright,
    base_url: str,
) -> tuple[StateRecord, ...]:
    """Print every state served at `base_url` through the engine.

    The driver's start and stop each run inside the watchdog.

    Raises:
        EngineError: the browser failed a call the engine could not record as a problem.
    """
    watchdog = Watchdog()
    with _BoundedDriver(
        sync_playwright(), watchdog, plan.settings.timeout_s
    ) as playwright:
        return print_engine.run_targets(
            playwright, base_url, targets, plan.settings, bounder=watchdog
        )


class _BoundedDriver(AbstractContextManager[PlaywrightLike]):
    """Playwright's driver, started and stopped each inside a bound of `seconds`.

    Neither takes a timeout of its own: the start spawns the driver and waits for its
    handshake, and the stop waits for it to close, so either could hang the run unbounded.
    """

    def __init__(
        self,
        driver: AbstractContextManager[PlaywrightLike],
        bounder: Bounder,
        seconds: float,
    ) -> None:
        """Start nothing yet: `driver` starts on entry."""
        self._driver = driver
        self._bounder = bounder
        self._seconds = seconds

    def __enter__(self) -> PlaywrightLike:
        """Start the driver inside the watchdog; its root object."""
        with self._bounder.bound(self._seconds, _DRIVER_START):
            return self._driver.__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Stop the driver inside the watchdog, handing it how the run ended."""
        with self._bounder.bound(self._seconds, _DRIVER_STOP):
            return self._driver.__exit__(exc_type, exc_value, traceback)


def _read(
    pdf: Path,
    out: Path,
    dpi: int,
    timeout: float,
    identity: Identity,
    margin_inches: Fraction,
) -> print_report.PdfReport:
    """Read one PDF with every reader, its text and renders written under `out`.

    Its ink is measured against `margin_inches`, the margin it was printed with.
    """
    return print_report.read_pdf(
        pdf,
        out,
        dpi=dpi,
        timeout=timeout,
        identity=identity,
        margin_inches=margin_inches,
    )


def _compare_pairs(
    records: Sequence[StateRecord], plan: MakePlan
) -> list[dict[str, object]]:
    """Compare each state's backgrounds off and on PDFs, where it printed both.

    A pair that cannot render is left out, not fatal: each PDF's own reading already
    carries its render failure as a finding, so the run still exits 1.
    """
    comparisons: list[dict[str, object]] = []
    for record in records:
        by_side = {made.backgrounds: made.path for made in record.pdfs}
        if len(by_side) < PAIR:
            continue
        try:
            comparisons.append(
                print_report.compare_pdfs(
                    by_side[False],
                    by_side[True],
                    dpi=plan.dpi,
                    timeout=plan.settings.timeout_s,
                    out_dir=plan.out,
                )
            )
        except RasterError as error:
            where = f"{record.target}/{record.state}"
            warn(f"{where}: backgrounds off and on not compared: {first_line(error)}")
    return comparisons


def _record(report: dict[str, object], out: Path) -> int:
    """Write `report.json`, print the summary and where the report is; its exit code."""
    path = print_report.write_report(report, out)
    for line in print_report.summary_lines(report):
        # A line quotes what the page chose (an animation's id, an error, a font's name):
        # each character a terminal would act on is spelled, never sent.
        stdio_utf8.print_escaped(stdio_utf8.spell_unsafe(line), sys.stdout)
    say(f"report: {path}")
    return print_report.exit_code(report)
