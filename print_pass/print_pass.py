#!/usr/bin/env python3
"""Print a site's pages to PDF in Chromium or Firefox, read any PDF, or compare two.

A repeatable print instrument. A print is judged only from the PDF it made, never from a
screen emulating print media, and a run REFUSES (exit 2) rather than report a result it
could not measure honestly.

One invocation, in order:

1. **Validates its mode's options** (exit 2): `make` serves a folder (`--serve`) and prints
   the pages a targets file names, or `--page` names; `read` reads one or more `--pdf`;
   `compare` compares exactly two. An option of another mode is refused, naming that mode.
2. **Refuses `--out`** (exit 2) when it already exists, when its parent folder does not, or
   when the parent lies inside a git work tree -- or when git cannot say where it lies. A
   PDF records when it was made, so `--out` is refused inside any work tree by construction,
   whatever its ignore rules say, rather than trusted to a later scan
   (`src/print_preflight.py`).
3. **Refuses a missing poppler reader** (exit 2), naming each one missing.
4. **Runs its mode** (`src/print_modes.py`, imported only now: it imports the PDF readers,
   and one not installed refuses the run, exit 2). `make` loads the targets, finds the pinned
   engine builds for a Playwright engine (`--browsers`, else `$PLAYWRIGHT_BROWSERS_PATH`,
   else the folder Playwright itself installs them into), serves
   `--serve` on 127.0.0.1, prints every state of every target through the engine --
   Playwright's `page.pdf()` for the shell and installed Chrome, WebDriver's Print through
   geckodriver for Firefox -- then reads every PDF made and compares each backgrounds off
   and on pair. `read` reads each PDF; `compare` renders the two and compares them.
5. **Records** `<out>/report.json`, prints its summary (findings first), and exits with
   the report's own code.

**Privacy.** Only a folder the tool serves itself on loopback is ever printed, and every
request off loopback is aborted, or under Firefox refused. `report.json` never holds a
metadata date or a flagged value, only the field and its class, and every path in it is
relative to `--out`. An engine's error goes to stderr and never into the report: its first
line can name an executable's path.

Exit codes: `0` every state printed and every PDF read without a finding (compare: the two
render identically); `1` a finding (compare: they differ); `2` the run could not measure
honestly -- bad usage or targets, `--out` refused, Playwright, geckodriver, a PDF reader or
a poppler reader missing, Firefox asked for off macOS, a browser call that failed (an engine
build, installed Chrome or installed Firefox absent among them), a fault in the CSP
collector, a compare PDF that cannot render, a bounded call abandoned, or an unexpected
error, whose traceback is printed.

Entry point assumptions: Python 3.12 or later with the repository's `print` extra installed
(pypdf and pdfminer.six are imported once any run starts, Playwright only for make through
Chromium; `--help` and every refusal before a run need neither), and poppler's readers on
`PATH`. What this module needs before a run is `src/print_plan.py`, which imports no reader,
and the shared libraries in the folders beside this tool's own, which it and `src/` put on
the path as they load, the CSP collector `csp_watch` among them: without one, the CLI does
not start.
"""

from __future__ import annotations

import argparse
import sys
import threading
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Final, get_args

# This tool's own src/ first, then just after it the shared subprocess runner, loopback
# server, UTF-8 stdio policy and --help formatter: each a folder beside this tool's own,
# named rather than searched for, so a same-named folder above the clone is never imported in
# its place, and ahead of every installed distribution, so none of the same name can shadow
# it. The src/ modules reach the libraries they use the same way.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "bounded_run"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "loopback_site"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "stdio_utf8"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "help_formatter"))

import help_formatter
import pdf_tools
import print_plan
import print_preflight
import stdio_utf8
from print_engine import TIMED_CALL_BOUND_FACTOR, EngineSettings
from print_plan import EXIT_CANNOT_MEASURE, PAIR, MakePlan, ReadPlan, Runs
from print_record import FIREFOX, Engine
from print_targets import BACKGROUNDS, TargetsError, parse_viewport

#: The three modes.
MAKE: Final = "make"
READ: Final = "read"
COMPARE: Final = "compare"
MODES: Final = (MAKE, READ, COMPARE)

#: The engines `--engine` names: the engine record's own set.
ENGINES: Final[tuple[str, ...]] = get_args(Engine.__value__)
HEADER_FOOTER: Final = ("off", "on")
_ON: Final = "on"

#: The defaults print_pass.md's option table gives. The timeout is whole seconds, so `--help`
#: shows 30; a value given is parsed as a float.
DEFAULT_ENGINE: Final = "shell"
DEFAULT_BACKGROUNDS: Final = "off"
DEFAULT_HEADER_FOOTER: Final = "off"
DEFAULT_VIEWPORT: Final = "1280x800"
DEFAULT_PAGE: Final = "/"
DEFAULT_TIMEOUT_SECONDS: Final = 30
DEFAULT_DPI: Final = 96

#: The longest `--timeout`. A browser call handed it is bounded at `TIMED_CALL_BOUND_FACTOR`
#: times it, and no bound may pass what a lock can wait, so a longer one would be refused
#: by the engine only once Playwright's driver had started.
MAX_TIMEOUT_SECONDS: Final = threading.TIMEOUT_MAX / TIMED_CALL_BOUND_FACTOR

#: The render resolution's range. Pages are parsed in plain Python, so past 600 dpi one
#: Letter page's raster alone passes 100 MB, and the run crawls long before it fails.
MIN_DPI: Final = 1
MAX_DPI: Final = 600

#: Where under `--out` the engine writes its PDFs.
PDF_DIR: Final = "pdfs"

#: The options only --mode make takes, by argparse dest; `--pdf` is read's and compare's.
_MAKE_DESTS: Final = (
    "serve",
    "targets",
    "page",
    "engine",
    "browsers",
    "backgrounds",
    "header_footer",
    "viewport",
)
_PDF_DEST: Final = "pdf"
_MODE_DESTS: Final = (*_MAKE_DESTS, _PDF_DEST)

#: The make options `--engine firefox` refuses, by argparse dest, each with why. Refused
#: when given at all, as an option of another mode is, even at a value Firefox would honor.
_NOT_FIREFOX: Final = (
    ("header_footer", "WebDriver's Print never prints a header or footer"),
    ("browsers", "it names Playwright's engine builds, and Firefox is none of them"),
)

#: The PDF readers' packages. The runs import them and this module must not: see `_load_runs`.
_READERS: Final = ("pypdf", "pdfminer")


def _seconds(text: str) -> float:
    """Parse `--timeout`: seconds above zero, and no more than `MAX_TIMEOUT_SECONDS`."""
    try:
        value = float(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a number of seconds"
        ) from error
    # NaN fails both comparisons, and infinity the second.
    if not 0 < value <= MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"must be more than 0 and at most {MAX_TIMEOUT_SECONDS:g} seconds, not {text}"
        )
    return value


def _dpi(text: str) -> int:
    """Parse `--dpi`: a whole number from `MIN_DPI` to `MAX_DPI`."""
    try:
        value = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from error
    if not MIN_DPI <= value <= MAX_DPI:
        raise argparse.ArgumentTypeError(
            f"must be from {MIN_DPI} to {MAX_DPI}, not {value}"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    """The argument parser: named options only, each optional's live default shown."""
    parser = stdio_utf8.EscapingArgumentParser(
        description=(
            "Print a site's pages to PDF through Playwright's Chromium or Firefox's "
            "WebDriver Print, read any PDF, or compare two."
        ),
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=(
            "Prerequisites: the shared CSP collector csp_watch, in the folder beside "
            "this tool's own; in every mode, the repository's `print` extra "
            "(pip install '.[print]'), whose pypdf and pdfminer.six read every PDF, and "
            f"poppler's {', '.join(pdf_tools.REQUIRED_TOOLS)} on PATH. For make, by "
            "engine: --engine shell needs the extra's Playwright and the pinned engine "
            "builds, installed with `python -m playwright install --only-shell "
            f"chromium` into ${print_preflight.BROWSERS_ENV}, or Playwright's own folder "
            "when it is unset; --engine chrome needs the "
            "extra's Playwright and installed Google Chrome, and never starts the pinned "
            "builds; a native-dark state under either of the two needs installed Google "
            "Chrome; --engine firefox runs on macOS only, and needs geckodriver on PATH, "
            "which `brew install geckodriver` installs, and "
            "Firefox itself, which it does not. --out must not exist, and must lie "
            "outside every git work tree. A key the targets file sets (viewport, "
            "backgrounds) wins over the matching option; no option sets the print "
            "margin, which a state's page_width alone sets. Exit codes: 0 every state "
            "printed and every PDF read without a finding (compare: the two render "
            "identically), 1 a finding (compare: they differ), 2 the run could not "
            "measure honestly."
        ),
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=MODES,
        help="Make PDFs of served pages, read given PDFs, or compare two.",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        metavar="DIR",
        help="A new folder, outside every git work tree, for the PDFs, renders, text "
        "and report.json.",
    )
    parser.add_argument(
        "--dpi",
        type=_dpi,
        default=DEFAULT_DPI,
        metavar="N",
        help=f"Render resolution, from {MIN_DPI} to {MAX_DPI}.",
    )
    parser.add_argument(
        "--timeout",
        type=_seconds,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="The bound on each step: in make, each page's load, actions and settle, and "
        "each browser call; in every mode, each poppler call that reads or renders a PDF, "
        "and the pypdf and pdfminer reads of one PDF, together, in one child process.",
    )
    _add_make_options(parser)
    pdfs = parser.add_argument_group(f"--mode {READ} and {COMPARE}")
    pdfs.add_argument(
        "--pdf",
        action="append",
        type=Path,
        default=None,
        metavar="FILE",
        help="A PDF to read (repeatable), or one of exactly two to compare.",
    )
    return parser


def _add_make_options(parser: argparse.ArgumentParser) -> None:
    """Add the options only --mode make takes, one literal call each."""
    make = parser.add_argument_group(f"--mode {MAKE}")
    make.add_argument(
        "--serve",
        type=Path,
        default=None,
        metavar="DIR",
        help="The folder served on 127.0.0.1 and printed from; --mode make needs it.",
    )
    pages = make.add_mutually_exclusive_group()
    pages.add_argument(
        "--targets",
        type=Path,
        default=None,
        metavar="FILE",
        help="The TOML file naming the pages, their states and their read-backs.",
    )
    pages.add_argument(
        "--page",
        action="append",
        default=[DEFAULT_PAGE],
        metavar="PATH",
        help="A server path printed in the default state; repeatable.",
    )
    make.add_argument(
        "--engine",
        choices=ENGINES,
        default=DEFAULT_ENGINE,
        help="Playwright's pinned headless shell, installed Google Chrome with a "
        "throwaway profile, or installed Firefox through geckodriver's WebDriver Print, "
        "which takes neither --header-footer nor --browsers.",
    )
    make.add_argument(
        "--browsers",
        type=Path,
        default=None,
        metavar="DIR",
        help=f"Where the pinned engine builds are; without it, "
        f"${print_preflight.BROWSERS_ENV}, else the folder Playwright itself installs "
        "them into. A folder only: Playwright's hermetic mode is "
        f"${print_preflight.BROWSERS_ENV}=0.",
    )
    make.add_argument(
        "--backgrounds",
        choices=BACKGROUNDS,
        default=DEFAULT_BACKGROUNDS,
        help="Print backgrounds off (the print dialog's default), on, or both from one "
        "load, compared.",
    )
    make.add_argument(
        "--header-footer",
        choices=HEADER_FOOTER,
        default=DEFAULT_HEADER_FOOTER,
        help="The print dialog's own header and footer.",
    )
    make.add_argument(
        "--viewport",
        default=DEFAULT_VIEWPORT,
        metavar="WxH",
        help="The window each state is laid out in.",
    )


def _parse(
    parser: argparse.ArgumentParser, argv: Sequence[str] | None
) -> tuple[argparse.Namespace, frozenset[str]]:
    """Parse `argv`; return every option with its default filled in, and which were given.

    Each mode-specific option starts as None, so one still None after parsing was not given
    (no option's type makes a None), and only then gets the parser's default. Filling it
    after, not before, is also what keeps `--page` from appending to its own default.
    """
    unset = argparse.Namespace(**dict.fromkeys(_MODE_DESTS))
    args = parser.parse_args(argv, unset)
    given = frozenset(dest for dest in _MODE_DESTS if getattr(args, dest) is not None)
    for dest in _MODE_DESTS:
        if dest not in given:
            setattr(args, dest, parser.get_default(dest))
    return args, given


def _validate_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace, given: frozenset[str]
) -> MakePlan | ReadPlan:
    """The run `args` ask for, or `parser.error` (exit 2) naming what is wrong with them."""
    if args.mode == MAKE:
        return _validate_make(parser, args, given)
    return _validate_pdfs(parser, args, given)


def _validate_make(
    parser: argparse.ArgumentParser, args: argparse.Namespace, given: frozenset[str]
) -> MakePlan:
    """A make run: a folder to serve, no --pdf, and a viewport that parses.

    Under `--engine firefox`, `--header-footer` and `--browsers` are refused too, given at
    all: WebDriver's Print prints no header or footer, and Firefox is none of Playwright's
    engine builds.
    """
    if _PDF_DEST in given:
        parser.error(f"--mode {READ} and {COMPARE} alone take --pdf")
    if args.engine == FIREFOX:
        _refuse_for_firefox(parser, given)
    if args.serve is None:
        parser.error("--mode make needs --serve, the folder to print from")
    if not args.serve.is_dir():
        parser.error(f"--serve {args.serve} is not a folder")
    if args.browsers is not None and not args.browsers.is_dir():
        parser.error(f"--browsers {args.browsers} is not a folder")
    try:
        viewport = parse_viewport(args.viewport)
    except TargetsError as error:
        parser.error(str(error))
    out: Path = args.out
    settings = EngineSettings(
        engine=args.engine,
        backgrounds=args.backgrounds,
        header_footer=args.header_footer == _ON,
        viewport=viewport,
        timeout_s=args.timeout,
        pdf_dir=out / PDF_DIR,
    )
    return MakePlan(
        out=out,
        dpi=args.dpi,
        serve=args.serve,
        targets=args.targets,
        pages=tuple(args.page),
        browsers=args.browsers,
        settings=settings,
    )


def _refuse_for_firefox(parser: argparse.ArgumentParser, given: frozenset[str]) -> None:
    """Refuse a make option `--engine firefox` cannot use, naming it and why (exit 2).

    Checked before `--browsers` is looked for on disk: whether the folder is there does not
    matter to an engine that would never read it.
    """
    for dest, why in _NOT_FIREFOX:
        if dest in given:
            option = f"--{dest.replace('_', '-')}"
            parser.error(f"--engine {FIREFOX} takes no {option}: {why}")


def _validate_pdfs(
    parser: argparse.ArgumentParser, args: argparse.Namespace, given: frozenset[str]
) -> ReadPlan:
    """A read or compare run: no make option, and the PDFs its mode takes, each a file.

    --timeout bounds each poppler call, and the pypdf and pdfminer reads of each PDF.
    """
    foreign = [f"--{dest.replace('_', '-')}" for dest in _MAKE_DESTS if dest in given]
    if foreign:
        parser.error(f"--mode {MAKE} alone takes {', '.join(foreign)}")
    pdfs: tuple[Path, ...] = tuple(args.pdf or ())
    if args.mode == READ and not pdfs:
        parser.error("--mode read needs at least one --pdf")
    if args.mode == COMPARE and len(pdfs) != PAIR:
        parser.error(f"--mode compare needs exactly two --pdf, not {len(pdfs)}")
    for pdf in pdfs:
        if not pdf.is_file():
            parser.error(f"--pdf {pdf} is not a file")
    if args.mode == READ:
        _refuse_shared_names(parser, pdfs)
    return ReadPlan(
        mode=args.mode,
        out=args.out,
        dpi=args.dpi,
        timeout=args.timeout,
        pdfs=pdfs,
    )


def _refuse_shared_names(parser: argparse.ArgumentParser, pdfs: Sequence[Path]) -> None:
    """Refuse two PDFs whose text and renders would take the same names under --out.

    Both are named from the PDF's stem, compared regardless of case: on a filesystem that
    ignores case, `a.pdf` and `A.PDF` write one file.
    """
    seen: dict[str, Path] = {}
    for pdf in pdfs:
        key = pdf.stem.casefold()
        if key in seen:
            parser.error(
                f"--pdf {seen[key].name} and {pdf.name} would write the same text and "
                "renders under --out: rename one"
            )
        seen[key] = pdf


def _run(plan: MakePlan | ReadPlan) -> int:
    """Run the mode `plan` is for; its exit code."""
    runs = _load_runs()
    if runs is None:
        return EXIT_CANNOT_MEASURE
    if isinstance(plan, MakePlan):
        return runs.run_make(plan)
    if plan.mode == READ:
        return runs.run_read(plan)
    return runs.run_compare(plan)


def _load_runs() -> Runs | None:
    """The runs, or None, having said which, when a PDF reader is not installed.

    Any other module missing is raised: it is not the refusal this names.
    """
    try:
        return _import_runs()
    except ModuleNotFoundError as error:
        # A reader's own submodule is missing when its package is: `pdfminer.high_level`.
        package = (error.name or "").partition(".")[0]
        if package not in _READERS:
            raise
        print_plan.warn(
            f"{package} is not installed: install the repository's `print` extra, "
            "whose pypdf and pdfminer.six read every PDF"
        )
        return None


def _import_runs() -> Runs:
    """The runs' module, imported only once a run starts: it imports the PDF readers."""
    # Deferred, as Playwright is: --help and every refusal before a run need no reader.
    import print_modes  # noqa: PLC0415 - deferred, as above

    return print_modes


def main(argv: Sequence[str] | None = None) -> int:
    """Validate, refuse what cannot be measured honestly, run the mode; the exit code."""
    # First, before anything prints: output is often piped, where a locale-derived encoding
    # is not UTF-8.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    plan = _validate_args(parser, *_parse(parser, argv))
    refusal = print_preflight.refuse_out(plan.out) or print_preflight.missing_poppler()
    if refusal is not None:
        print_plan.warn(refusal)
        return EXIT_CANNOT_MEASURE
    try:
        return _run(plan)
    except Exception:
        # Nothing is silenced: the traceback is printed whole. What this catch is for is
        # the EXIT CODE -- an escaping exception leaves the interpreter with 1, which here
        # means a finding, and a run that died found nothing.
        print_plan.warn(f"the run could not finish:\n{traceback.format_exc()}")
        return EXIT_CANNOT_MEASURE


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
