#!/usr/bin/env python3
"""Measure a built site with the project's pinned Lighthouse: interleaved runs, medians, JSON.

A later performance run compares with an earlier one only when both used the same Lighthouse,
the same flags, the same interleaving and the same arithmetic, which is why all four are held
here rather than written down as a command sequence that drifts.

One invocation, in order:

1. **Refuses what it could not measure honestly** (exit 2): no tree to serve (`src/` under
   `--root`, or the folder `--serve` names) or to expect (`--expect`), no pinned Lighthouse
   under `node_modules/.bin`, no Chrome to hand it, or a page not answered with the file on
   disk. `--runs` below the median floor, a malformed `--url` and a combination of target
   options that names no single tree are refused at parse time.
2. **Reads the commit the tree is at, then serves that tree on 127.0.0.1**, on a port the
   operating system picks, and confirms every page's bytes against disk, so the tree
   measured is the tree named, never a stale server holding a well-known port. With `--url`
   it serves nothing: it confirms every page the HOST answers against the `--expect` folder
   instead, with the same shared check, and refuses a page the host redirects. The commit is
   read first, so it names the tree as served, and a git that fails is recorded as unknown,
   never as clean. A tree outside any repository (a built site in a scratch folder) is
   recorded as unknown too, with nothing wrong.
   Which tree, and where its pages are fetched from, lives in `src/run_target.py`.
3. **Interleaves the runs**: every page on every preset once per round, for `--runs` rounds,
   so a slow moment on the machine lands across all of them rather than on one page.
4. **Writes** every raw report and a `summary.json` (per-run numbers and audits, their
   medians, the tree measured and any hosted base url, the commit, the Lighthouse and Chrome
   versions and the exact flags) into a new timestamped folder under `--out`, and prints the
   medians as a Markdown table, then the audits' as a second. The record's shape lives in
   `src/run_summary.py`; which audits are read, and from where, in `src/lighthouse_audits.py`.

**Privacy.** Lighthouse launches Chrome with its own fresh temporary profile (no profile flag
is ever passed), error reporting to Lighthouse's maintainers is switched off explicitly, and
the raw reports (which embed the measured urls and the host's user agent) and the summary,
whose `served` is the folder as given and so can be an absolute local path, and whose `url` is
any hosted base, go to a gitignored folder by default. To measure a private address, pass
`--out` a folder outside the repository, and outside anything backed up off the machine.

**Read local timings as noise.** A loopback server has no network to speak of, so the score
and FCP, LCP and TBT swing run to run; CLS, byte counts and request counts are the numbers that
carry.

Exit codes: `0` every run measured; `1` at least one run failed (the summary is still written,
with medians over the runs that did measure); `2` the measurement could not start.

Entry point assumptions: `--root` is a provisioned web project (`npm ci` has run there), and
`node` and Google Chrome are installed. `--serve` or `--expect`, when given, is a folder of
pages that needs nothing else: the pinned Lighthouse still comes from `--root`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# This tool's own src/ first, then just after it the shared subprocess runner, loopback server,
# UTF-8 stdio policy, commit reading and --help formatter: each a folder beside this tool's own
# folder, named rather than searched for, so a same-named folder above the clone is never
# imported in its place, and ahead of every installed distribution, so none of the same name
# can shadow it.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "bounded_run"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "loopback_site"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "stdio_utf8"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "commit_facts"))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "help_formatter"))

import bounded_run
import commit_facts
import help_formatter
import lighthouse_audits
import lighthouse_report
import loopback_site
import run_facts
import run_options
import run_summary
import run_target
import stdio_utf8

_TOOL = Path(__file__).stem

#: The pinned CLI, relative to `--root`, where `npm ci` installs it from the lockfile.
LIGHTHOUSE_BIN = Path("node_modules") / ".bin" / "lighthouse"

#: Where results go when `--out` is not given, relative to `--root`. Gitignored.
RESULTS_SUBDIR = "lighthouse_runs"

#: Inside one run's folder: the raw reports, and the summary beside them.
REPORTS_SUBDIR = "reports"
SUMMARY_NAME = "summary.json"

#: The pages a run measures when `--pages` names none: the home page, which every site has.
#: To measure a not-found page as well, pass it by its FILE name (`/404.html`): a static
#: server answers that with 200, and Lighthouse refuses to audit a page served with an error
#: status.
DEFAULT_PAGES = ("/",)

#: Flags on every run. Performance only, since no other category is reported; headless, since
#: nothing needs a window; no full-page screenshot, which is most of a report's size.
COMMON_FLAGS = (
    "--output=json",
    "--only-categories=performance",
    "--chrome-flags=--headless=new",
    "--quiet",
    "--no-enable-error-reporting",
    "--disable-full-page-screenshot",
)

#: Each preset's own flags. Mobile is Lighthouse's default configuration and needs none.
PRESET_FLAGS: Mapping[str, tuple[str, ...]] = {
    "mobile": (),
    "desktop": ("--preset=desktop",),
}
PRESETS = tuple(PRESET_FLAGS)

#: The fewest runs a median is taken over; why, beside the parser that enforces it.
MIN_RUNS = run_options.MIN_RUNS

#: A run normally takes well under a minute; this bounds a hung Chrome.
DEFAULT_RUN_TIMEOUT_SECONDS = 300.0

#: Bounds on the quick steps around the runs: locating Chrome, each git call, confirming a page.
_FACT_TIMEOUT_SECONDS = 30.0
_GIT_TIMEOUT_SECONDS = 30.0
_CONFIRM_TIMEOUT_SECONDS = 10.0

#: The variable chrome-launcher reads to launch a given Chrome.
_CHROME_PATH_ENV = "CHROME_PATH"


@dataclass(frozen=True)
class Plan:
    """What one invocation measures (which tree, fetched from where) and for how long."""

    target: run_target.Target
    pages: tuple[str, ...]
    presets: tuple[str, ...]
    runs: int
    timeout: float


def build_parser() -> argparse.ArgumentParser:
    """The argument parser: named options only, each set default shown."""
    parser = argparse.ArgumentParser(
        description=(
            "Measure a built site's pages with the project's pinned Lighthouse, as interleaved "
            "runs reduced to per-metric medians."
        ),
        formatter_class=help_formatter.UnsetHidingFormatter,
        epilog=(
            "Prerequisites: run `npm ci` in the project first, so node_modules/.bin/lighthouse "
            "is the pinned version; node and Google Chrome must be installed. Leave the machine "
            "otherwise idle while it runs, or the timings measure that load instead. "
            "Exit codes: 0 every run measured, 1 a run failed, 2 the measurement could not start."
        ),
    )
    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help=f"Web project root holding node_modules/, and the {run_target.PAGES_SUBDIR}/ "
        "measured unless --serve or --expect names another folder.",
    )
    parser.add_argument(
        "--serve",
        type=Path,
        default=None,
        help=f"Folder to serve on loopback and measure, such as a built site; without it, "
        f"{run_target.PAGES_SUBDIR}/ under --root.",
    )
    parser.add_argument(
        "--url",
        type=run_options.base_url,
        default=None,
        help="Base url of a hosted copy to measure instead: each page is measured at "
        "<base><page>, and nothing is served here. Needs --expect; excludes --serve.",
    )
    parser.add_argument(
        "--expect",
        type=Path,
        default=None,
        help="With --url: the folder holding the bytes the host must serve, such as the "
        "built site of the deployed commit. Every page is confirmed against it first.",
    )
    parser.add_argument(
        "--pages",
        nargs="+",
        type=loopback_site.page_path,
        default=list(DEFAULT_PAGES),
        help="Server paths to measure.",
    )
    parser.add_argument(
        "--presets",
        nargs="+",
        choices=PRESETS,
        default=list(PRESETS),
        help="Lighthouse presets to measure every page on.",
    )
    parser.add_argument(
        "--runs",
        type=run_options.run_count,
        default=MIN_RUNS,
        help=f"Rounds per page and preset; at least {MIN_RUNS}.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Folder receiving one new timestamped results folder per invocation; "
        f"without it, {RESULTS_SUBDIR}/ under --root.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_RUN_TIMEOUT_SECONDS,
        help="Seconds allowed for one Lighthouse run.",
    )
    return parser


def parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse `argv`, refusing as argparse does a target-option combination no option sees."""
    parser = build_parser()
    args = parser.parse_args(argv)
    conflict = run_options.option_conflict(
        url=args.url, expect=args.expect, serve=args.serve
    )
    if conflict is not None:
        parser.error(conflict)
    return args


def build_schedule(
    pages: Sequence[str], presets: Sequence[str], runs: int
) -> list[run_summary.Job]:
    """Every run in order: each round measures every page on every preset once."""
    return [
        run_summary.Job(number, page, preset)
        for number in range(1, runs + 1)
        for page in pages
        for preset in presets
    ]


def page_slug(page: str) -> str:
    """A file-name-safe name for a server path: no slashes, and `home` for the root."""
    return page.strip("/").replace("/", "_") or "home"


def preset_flags(presets: Sequence[str]) -> dict[str, list[str]]:
    """The full flag list each preset runs with, as the summary records it."""
    return {preset: [*COMMON_FLAGS, *PRESET_FLAGS[preset]] for preset in presets}


def lighthouse_argv(
    binary: Path, url: str, preset: str, report_path: Path
) -> list[str]:
    """The exact command for one run."""
    return [
        str(binary),
        url,
        f"--output-path={report_path}",
        *preset_flags([preset])[preset],
    ]


def lighthouse_binary(root: Path) -> Path | None:
    """The absolute path launching the pinned Lighthouse under `root`, or None without one.

    Looked up, not named: on Windows npm writes a `lighthouse.cmd` beside the `lighthouse`
    shell script, and only the `.cmd`, which a PATHEXT lookup answers, can be launched.
    """
    folder = (root / LIGHTHOUSE_BIN.parent).absolute()
    found = shutil.which(LIGHTHOUSE_BIN.name, path=str(folder))
    return None if found is None else Path(found)


def preflight_problem(root: Path, target: run_target.Target) -> str | None:
    """Why `root` cannot measure `target`, or `None` when it can."""
    problem = run_target.tree_problem(root, target)
    if problem is None and lighthouse_binary(root) is None:
        return f"no pinned Lighthouse at {LIGHTHOUSE_BIN} under {root}: run `npm ci` there first"
    return problem


def run_job(
    job: run_summary.Job,
    *,
    binary: Path,
    base_url: str,
    reports: Path,
    env: Mapping[str, str],
    timeout: float,
) -> run_summary.Outcome:
    """Run Lighthouse once for `job` and read its report; any failure becomes the error."""
    report_path = reports / f"{page_slug(job.page)}-{job.preset}-run{job.round}.json"
    argv = lighthouse_argv(binary, base_url + job.page, job.preset, report_path)
    try:
        completed = bounded_run.run_bounded(argv, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return run_summary.Outcome(job, error=f"timed out after {timeout:g}s")
    if completed.returncode != 0:
        # A CLI states its reason last; `--quiet` leaves little else on stderr.
        reason = (completed.stderr.strip().splitlines() or ["no output"])[-1]
        return run_summary.Outcome(
            job, error=f"lighthouse exited {completed.returncode}: {reason}"
        )
    try:
        report: object = json.loads(report_path.read_text(encoding="utf-8"))
        return run_summary.Outcome(
            job,
            metrics=lighthouse_report.extract_metrics(report),
            version=lighthouse_report.lighthouse_version(report),
            audits=lighthouse_audits.extract_audits(report),
        )
    except (OSError, ValueError) as error:
        # JSONDecodeError and ReportError are both ValueErrors.
        return run_summary.Outcome(job, error=f"unreadable report: {error}")


def _describe(outcome: run_summary.Outcome, runs: int) -> str:
    """One progress line for a finished run."""
    job = outcome.job
    where = f"round {job.round}/{runs} {job.page} {job.preset}"
    if outcome.metrics is None:
        return f"{where}: FAILED ({outcome.error})"
    return f"{where}: perf {outcome.metrics.score * 100:.0f}, CLS {outcome.metrics.cls:.4f}"


def confirm_and_measure(
    root: Path, plan: Plan, chrome: run_facts.ChromeFacts, reports: Path
) -> list[run_summary.Outcome]:
    """Confirm every page of the plan's target, then run the whole schedule into `reports`.

    A loopback target is served for the length of the runs; a hosted one is fetched in place.

    Raises:
        OSError: a page has no file, the server could not be reached, or `reports` could not
            be created.
        loopback_site.SiteServeError: the server answered a page from another tree.
            On a hosted run, the `run_target.HostedPageError` naming the page.
    """
    # The preflight found it; one gone since then fails to launch, an OSError, as named.
    binary = lighthouse_binary(root) or (root / LIGHTHOUSE_BIN).absolute()
    env = {**os.environ, _CHROME_PATH_ENV: chrome.path}
    outcomes: list[run_summary.Outcome] = []
    confirmed = run_target.confirmed_base(
        plan.target, plan.pages, timeout=_CONFIRM_TIMEOUT_SECONDS
    )
    with confirmed as base_url:
        if plan.target.url is not None:
            _say(f"measuring {base_url}: every page matches {plan.target.named}")
        reports.mkdir(parents=True)
        for job in build_schedule(plan.pages, plan.presets, plan.runs):
            outcome = run_job(
                job,
                binary=binary,
                base_url=base_url,
                reports=reports,
                env=env,
                timeout=plan.timeout,
            )
            _say(_describe(outcome, plan.runs))
            outcomes.append(outcome)
    return outcomes


def read_commit(served: Path) -> commit_facts.CommitFacts:
    """The commit `served` is at, read BEFORE anything is served, and why git failed if it did.

    Before, because the tree served is the tree at the start: a commit made during a long run
    would otherwise be recorded as the one measured. And a git that fails or hangs then costs
    only its own bound, never the finished runs: the summary records the commit as unknown.
    A tree outside any repository is unknown as well, and says nothing: nothing went wrong.
    """
    commit = commit_facts.read(
        served, git=shutil.which("git"), timeout=_GIT_TIMEOUT_SECONDS
    )
    if commit.problem is not None:
        _warn(f"which commit {served} is at is unknown: {commit.problem}")
    return commit


def record_results(
    results: Sequence[run_summary.PageResult],
    run_dir: Path,
    *,
    plan: Plan,
    commit: commit_facts.CommitFacts,
    chrome: run_facts.ChromeFacts,
) -> int:
    """Write `summary.json`, print the medians and any failures, and return the exit code."""
    summary = run_summary.summary_document(
        results,
        tool=_TOOL,
        runs=plan.runs,
        flags=preset_flags(plan.presets),
        served=None if plan.target.named is None else str(plan.target.named),
        url=plan.target.url,
        commit=commit,
        chrome=chrome,
    )
    (run_dir / SUMMARY_NAME).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    dirty = f" ({plan.target.tree} has uncommitted edits)" if commit.dirty else ""
    _say(f"commit {commit.sha or 'unknown'}{dirty}")
    versions = ", ".join(run_summary.lighthouse_versions(results)) or "unknown"
    _say(f"lighthouse {versions}; {chrome.version}")
    # A blank line between the tables, or Markdown reads the second as rows of the first.
    for line in [
        *run_summary.format_table(results),
        "",
        *run_summary.format_audit_table(results),
    ]:
        print(line)
    _say(f"results: {run_dir}")
    failed = run_summary.failures(results)
    for failure in failed:
        job = failure.job
        _warn(f"{job.page} {job.preset} round {job.round}: {failure.error}")
    if failed:
        total = sum(len(result.outcomes) for result in results)
        _warn(f"{len(failed)} of {total} runs failed")
        return 1
    return 0


def _say(line: str) -> None:
    """Print one progress line to stdout."""
    print(f"{_TOOL}: {line}")


def _warn(line: str) -> None:
    """Print one problem to stderr, where diagnostics belong."""
    print(f"{_TOOL}: {line}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, measure, record the results, and return the exit code."""
    # First, before anything prints: output is often piped, where a locale-derived encoding
    # is not UTF-8.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    args = parse_arguments(argv)
    root: Path = args.root
    target = run_target.resolve_target(
        root, serve=args.serve, url=args.url, expect=args.expect
    )
    problem = preflight_problem(root, target)
    if problem is not None:
        _warn(problem)
        return 2
    try:
        chrome = run_facts.chrome_facts(
            root, node=shutil.which("node"), timeout=_FACT_TIMEOUT_SECONDS
        )
    except (run_facts.FactError, OSError, subprocess.TimeoutExpired) as error:
        _warn(f"cannot locate the Chrome Lighthouse would launch: {error}")
        return 2
    # Duplicates dropped, order kept: a page named twice would otherwise be measured twice as
    # often and folded into one result.
    plan = Plan(
        target=target,
        pages=tuple(dict.fromkeys(args.pages)),
        presets=tuple(dict.fromkeys(args.presets)),
        runs=args.runs,
        timeout=args.timeout,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir: Path = (args.out or root / RESULTS_SUBDIR) / stamp
    commit = read_commit(target.tree)
    try:
        outcomes = confirm_and_measure(root, plan, chrome, run_dir / REPORTS_SUBDIR)
    except (OSError, loopback_site.SiteServeError) as error:
        _warn(f"cannot measure {target.where}: {error}")
        return 2
    results = run_summary.collect_results(outcomes, plan.pages, plan.presets)
    return record_results(results, run_dir, plan=plan, commit=commit, chrome=chrome)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
