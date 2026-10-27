#!/usr/bin/env python3
"""Render a site's pages across engines, viewports, orientations, modes and scripting, and measure.

The browser pass, committed and repeatable. It REFUSES (exit 2) rather than report a zero it
cannot stand behind -- the instrument faults that bought that rule are in browser_pass.md.

One invocation, in order:

1. **Validates** the targets file (pages, viewports, an optional base url, optional states) and
   the options.
2. **Reads the commit** the measured tree is at -- first, so it names the tree as served, and as
   unknown, never clean, when git fails -- then **serves** `--serve` on 127.0.0.1 through the
   shared `loopback_site`, on a port the OS picks, and confirms every page's bytes against disk --
   so the tree measured is the tree named. With the targets file's `not_found`, a page with no
   file is confirmed to answer 404 with that file instead, and its own 404 is no finding. A
   page that is a meta refresh is measured where it points if `redirects` declares it, else refused.
   Without `--serve` it measures the targets file's `base_url`, and the report says the tree is
   unproven.
3. **Renders** every page at every viewport (phones in portrait AND landscape), light and dark,
   scripts on and off, in each engine, and runs the eight page-load detectors on each rendering:
   four on the layout, three on what the browser reported, and one on what the page's Content
   Security Policy refused. In Chromium with scripts on it loads the page once more, its scripts
   held until the first paint, to catch what a late script moves. Each state renders the same way
   with scripts on, its detectors run once its actions have reached it. With `--prove` it also
   plants one fault per detector in every rendering and checks each is caught; with
   `--require-csp`, a page as it loads whose markup declares no policy is a finding. With an
   extension option, that engine's every context loads the extension, confirmed before it is
   measured; with `--loopback-only` (implied by an extension) no request leaves 127.0.0.1.
4. **Records** `report.json` and `report.md` in a new timestamped folder, compares every engine's
   layout with the first engine's, names each page whose motion was stilled before it was
   measured (or left running), prints the verdict, and exits 0 / 1 / 2.

Exit codes: `0` every detector silent (and every plant caught); `1` a detector found something,
or missed its plant; `2` the run could not measure honestly. The detectors, the matrix and the
report are described in `browser_pass.md`.

Entry point assumptions: Python 3.12 or later, with the repository's `browser` extra installed,
and the pinned engine builds installed where `--browsers` (or its default) points.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import traceback
from collections.abc import Callable, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

# This tool's own src/ first, then just after it the shared loopback server, subprocess runner,
# --help formatter, UTF-8 stdio policy and commit reading: each a folder beside this tool's own
# folder, named rather than searched for, so a same-named folder above the clone is never
# imported in its place, and ahead of every installed distribution, so none of the same name
# can shadow it.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "bounded_run"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "help_formatter"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "loopback_site"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "stdio_utf8"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "commit_facts"))

import commit_facts
import help_formatter
import loopback_site
import pass_extension
import pass_facts
import pass_motion
import pass_progress
import pass_redirects
import pass_report
import pass_serve
import pass_targets
import stdio_utf8

_TOOL = Path(__file__).stem

#: Where a run's folder goes when `--out` is not given: beside the targets file it measured.
RESULTS_DIR_NAME = "browser_pass_runs"

#: Default bound, in seconds, on each page load and on waiting for a page to settle.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Bound on each git call that reads a run's facts, and on confirming or reading each served page.
_GIT_TIMEOUT_SECONDS = 30.0
_CONFIRM_TIMEOUT_SECONDS = 10.0

_EXIT_CANNOT_MEASURE = 2

#: What `measure` hands `record`: base url, redirects followed, engine versions and results.
_Measured = tuple[str, pass_redirects.Followed, dict[str, str], pass_report.Results]


class _Engine(Protocol):
    """The part of `pass_engine` this CLI calls -- a seam the CLI tests replace."""

    @property
    def EngineError(self) -> type[Exception]: ...  # noqa: N802 - mirrors the module's class name

    def playwright_version(self) -> str: ...

    def run_matrix(
        self,
        renderings: Sequence[pass_targets.Rendering],
        base_url: str,
        *,
        timeout: float,
        prove: bool,
        scratch: Path,
        progress: Callable[[pass_report.RenderingResult], None],
        states: Sequence[pass_targets.State],
        require_csp: bool,
        missing_pages: Set[str],
        isolation: pass_extension.Isolation,
    ) -> tuple[dict[str, str], list[pass_report.RenderingResult]]: ...


@dataclass(frozen=True)
class Plan:
    """A validated run: what to render, where it is served from, and where results go."""

    targets: pass_targets.Targets
    serve: Path | None
    # The served pages with no file, each expected to answer 404.
    missing_pages: frozenset[str]
    renderings: tuple[pass_targets.Rendering, ...]
    reference: str
    browsers: Path
    out_dir: Path
    facts_tree: Path
    timeout: float
    prove: bool
    require_csp: bool
    # Each engine's extension, and whether off-loopback requests are refused.
    isolation: pass_extension.Isolation
    started: str


def _positive_seconds(text: str) -> float:
    """Parse `--timeout`: a positive number of seconds."""
    try:
        value = float(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a number of seconds"
        ) from error
    if value <= 0:
        raise argparse.ArgumentTypeError("--timeout must be more than 0 seconds")
    return value


def build_parser() -> argparse.ArgumentParser:
    """The argument parser: named options only, each optional's live default shown."""
    parser = argparse.ArgumentParser(
        description=(
            "Render a site's pages across engines, viewports (phones in both orientations), "
            "light and dark, scripts on and off; run the layout, console, network and CSP "
            "detectors on every rendering, and in Chromium the late-script layout-shift detector."
        ),
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=(
            "Prerequisites: the repository's `browser` extra installed, and "
            "the pinned engine builds installed where --browsers points -- browser_pass.md, "
            "'Installing the engines', has both commands. "
            "Exit codes: 0 every detector silent, 1 a finding or a missed plant, "
            "2 the run could not measure honestly."
        ),
    )
    parser.add_argument(
        "--targets",
        required=True,
        type=Path,
        help="TOML file naming the pages and viewports.",
    )
    parser.add_argument(
        "--serve",
        type=Path,
        default=None,
        help="Folder to serve on loopback and measure; without it, the targets file's base_url.",
    )
    # Four literal calls rather than a loop over a table: cli_doc_consistency reads each option's
    # name from its add_argument literal, and a name held in a tuple is invisible to it.
    parser.add_argument(
        "--engines",
        nargs="+",
        choices=pass_targets.ENGINES,
        default=list(pass_targets.ENGINES),
        help="Engines to render in; the first is the reference the others are compared with.",
    )
    parser.add_argument(
        "--orientations",
        nargs="+",
        choices=pass_targets.ORIENTATIONS,
        default=list(pass_targets.ORIENTATIONS),
        help="Orientations of a rotating (phone) viewport; a fixed viewport always runs.",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=pass_targets.MODES,
        default=list(pass_targets.MODES),
        help="Color schemes to render in.",
    )
    parser.add_argument(
        "--scripts",
        nargs="+",
        choices=pass_targets.SCRIPTS,
        default=list(pass_targets.SCRIPTS),
        help="Scripting on, and off through the engine's own switch.",
    )
    parser.add_argument(
        "--browsers",
        type=Path,
        default=None,
        help="Folder holding the engine builds; without it, $PLAYWRIGHT_BROWSERS_PATH, else "
        "the folder Playwright itself installs them into.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Folder receiving one timestamped run folder; without it, {RESULTS_DIR_NAME}/ "
        "beside the targets file.",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_seconds,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Seconds allowed for each step of a page: its load, its settle, each action of a state, "
        "and each wait of the late-script load.",
    )
    parser.add_argument(
        "--prove",
        action="store_true",
        help="Also plant one fault per detector in every rendering, and fail if one goes uncaught.",
    )
    parser.add_argument(
        "--require-csp",
        action="store_true",
        help="Also find each page as it loads, scripts on and off, whose markup declares no "
        "Content-Security-Policy meta. A state is not held to it.",
    )
    parser.add_argument(
        "--chromium-extension",
        type=Path,
        help="Unpacked extension folder (holding manifest.json) every Chromium context loads, "
        "in the full Chromium build; implies --loopback-only.",
    )
    parser.add_argument(
        "--firefox-extension",
        type=Path,
        help="Signed .xpi every Firefox context loads, sideloaded into a throwaway profile; "
        "implies --loopback-only.",
    )
    parser.add_argument(
        "--loopback-only",
        action="store_true",
        help="Refuse requests off 127.0.0.1 in every engine (Chromium in its full build): "
        "the baseline an extension run is compared with. WebKit's UDP is not guarded.",
    )
    return parser


def _load_engine() -> _Engine | None:
    """The Playwright-driving engine, or None when Playwright itself is not installed."""
    try:
        import pass_engine  # noqa: PLC0415 - deferred, so a missing Playwright is a clean refusal
    except ModuleNotFoundError as error:
        if (error.name or "").startswith("playwright"):
            return None
        raise
    return pass_engine


def plan_run(args: argparse.Namespace) -> Plan | str:
    """Validate everything before anything is measured; a string is the reason to refuse."""
    try:
        targets = pass_targets.load_targets(args.targets)
    except (pass_targets.TargetsError, OSError) as error:
        return f"cannot read {args.targets}: {error}"
    if args.serve is None and targets.base_url is None:
        return "nothing to measure: pass --serve, or give the targets file a base_url"
    if args.serve is None and targets.not_found is not None:
        return (
            "not_found needs --serve: a server this run does not start answers a "
            "page with no file its own way"
        )
    if args.serve is None and targets.redirects:
        return "redirects needs --serve: only a tree this run serves is read for a meta refresh"
    if args.serve is not None and not args.serve.is_dir():
        return f"--serve {args.serve} is not a folder"
    browsers = args.browsers or pass_facts.default_browsers_path(
        os.environ, platform=sys.platform, home=pass_facts.home_folder()
    )
    if browsers is None:
        return "cannot tell where the engine builds are: pass --browsers"
    isolation = pass_extension.plan_isolation(
        args.engines,
        chromium=args.chromium_extension,
        firefox=args.firefox_extension,
        loopback_only=args.loopback_only,
        base_url=None if args.serve else targets.base_url,
    )
    if isinstance(isolation, str):
        return isolation
    try:
        # Asks for no file without a not_found, which a run without --serve never has.
        missing = pass_targets.missing_pages(
            targets, lambda page: loopback_site.page_file(args.serve, page).is_file()
        )
    except loopback_site.SiteServeError as error:
        return f"cannot serve {args.serve}: {error}"
    renderings = pass_targets.expand_matrix(
        targets,
        engines=args.engines,
        orientations=args.orientations,
        modes=args.modes,
        scripts=args.scripts,
    )
    return Plan(
        targets=targets,
        serve=args.serve,
        missing_pages=missing,
        renderings=tuple(renderings),
        reference=args.engines[0],
        browsers=browsers,
        out_dir=args.out or args.targets.parent / RESULTS_DIR_NAME,
        facts_tree=args.serve or args.targets.parent,
        timeout=args.timeout,
        prove=args.prove,
        require_csp=args.require_csp,
        isolation=isolation,
        started=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
    )


def _announce(result: pass_report.RenderingResult) -> None:
    """Print a rendering as it finishes, but only one with something to say."""
    line = pass_progress.announcement(result)
    if line is not None:
        _say(line)


def measure(plan: Plan, engine: _Engine) -> _Measured:
    """Serve (or reach) the pages, follow each declared redirect, and render the whole matrix."""
    # Playwright reads this once, when it starts; the engine starts it inside run_matrix.
    os.environ[pass_facts.BROWSERS_ENV] = str(plan.browsers)
    with (
        tempfile.TemporaryDirectory(prefix=f"{_TOOL}-") as scratch,
        pass_serve.pages_at(
            plan.targets,
            plan.serve,
            plan.missing_pages,
            timeout=_CONFIRM_TIMEOUT_SECONDS,
        ) as (base_url, redirects),
    ):
        renderings = pass_redirects.retarget(plan.renderings, redirects)
        _say(f"{len(renderings)} renderings; engine builds in {plan.browsers}")
        left_out = pass_progress.states_left_out(plan.targets, plan.renderings)
        if left_out is not None:
            _say(left_out)
        versions, results = engine.run_matrix(
            renderings,
            base_url,
            timeout=plan.timeout,
            prove=plan.prove,
            scratch=Path(scratch),
            progress=_announce,
            states=plan.targets.states,
            require_csp=plan.require_csp,
            missing_pages=plan.missing_pages,
            isolation=plan.isolation,
        )
    return base_url, redirects, versions, results


def read_commit(plan: Plan) -> commit_facts.CommitFacts:
    """The commit the measured tree is at, read BEFORE anything is served, and why git failed.

    Before, because the tree served is the tree at the start: a commit made during a long pass
    would otherwise be recorded as the one measured. And a git that fails or hangs then costs
    only its own bound, never the finished matrix -- the report records the commit as unknown.
    """
    commit = commit_facts.read(
        plan.facts_tree, git=shutil.which("git"), timeout=_GIT_TIMEOUT_SECONDS
    )
    if commit.problem is not None:
        _warn(f"which commit {plan.facts_tree} is at is unknown: {commit.problem}")
    return commit


def record(
    plan: Plan,
    engine: _Engine,
    commit: commit_facts.CommitFacts,
    measured: _Measured,
) -> int:
    """Write the report, print any motion stilled, the verdict and where the report is.

    Returns the exit code, which motion never changes.
    """
    base_url, redirects, versions, results = measured
    facts = pass_report.RunFacts(
        started=plan.started,
        commit=commit.sha,
        # The report's own name for the shared `dirty`, kept so earlier reports still read.
        tree_dirty=commit.dirty,
        served=str(plan.serve) if plan.serve else None,
        base_url=base_url,
        playwright=engine.playwright_version(),
        engines=versions,
        prove=plan.prove,
        require_csp=plan.require_csp,
        redirects=redirects,
        loopback_only=plan.isolation.loopback_only,
        extensions=plan.isolation.facts,
    )
    comparisons = pass_report.cross_engine(results, reference=plan.reference)
    document = pass_report.report_document(results, comparisons, facts)
    markdown = pass_report.report_markdown(results, comparisons, facts)
    run_dir = plan.out_dir / plan.started
    pass_report.write_report(run_dir, document, markdown)
    for motion in pass_motion.motion_lines(results):
        _say(motion)
    _say(
        f"verdict {document['verdict']}; report: {run_dir / pass_report.MARKDOWN_NAME}"
    )
    return pass_report.exit_code(results)


def _say(line: str) -> None:
    """Print one progress line to stdout."""
    print(f"{_TOOL}: {line}", flush=True)


def _warn(line: str) -> None:
    """Print one problem to stderr, where diagnostics belong."""
    print(f"{_TOOL}: {line}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate, measure, record; return the exit code."""
    # First, before anything prints: output is often piped, where a locale-derived encoding
    # is not UTF-8.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    plan = plan_run(build_parser().parse_args(argv))
    if isinstance(plan, str):
        _warn(plan)
        return _EXIT_CANNOT_MEASURE
    engine = _load_engine()
    if engine is None:
        _warn(
            "Playwright is not installed: install the repository's `browser` extra "
            "(browser_pass.md, 'Installing the engines')"
        )
        return _EXIT_CANNOT_MEASURE
    try:
        return _measure_and_record(plan, engine)
    except Exception:
        # Nothing is silenced -- the traceback is printed in full. What this
        # catch is for is the EXIT CODE: an escaping exception leaves the interpreter with 1,
        # which here means a detector found something, and a run that died found nothing.
        _warn(f"the run could not finish:\n{traceback.format_exc()}")
        return _EXIT_CANNOT_MEASURE


def _measure_and_record(plan: Plan, engine: _Engine) -> int:
    """Measure the matrix and write the report; returns the exit code."""
    commit = read_commit(plan)
    try:
        measured = measure(plan, engine)
    except (OSError, loopback_site.SiteServeError) as error:
        _warn(f"cannot serve {plan.serve}: {error}")
        return _EXIT_CANNOT_MEASURE
    except (engine.EngineError, pass_redirects.RedirectRefused) as error:
        _warn(str(error))
        return _EXIT_CANNOT_MEASURE
    return record(plan, engine, commit, measured)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
