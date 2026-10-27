"""Shape what a run brought back: outcomes grouped by page and preset, a summary, two tables.

The runner schedules and executes; this module only shapes the results. It is kept apart so the
record format -- what `summary.json` holds, and what the printed tables show -- reads in one
place. Each run's audits, and each page and preset's reduction of them, are shaped by
`audit_summary`; this module places them in the record.

Entry point assumptions: pure functions over outcomes the runner already collected. The shared
`commit_facts` is importable, which the CLI's bootstrap arranges.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import audit_summary
import commit_facts
import lighthouse_audits
import lighthouse_report
import run_facts

#: The medians table: metric, heading, format, and the scale applied before formatting.
_COLUMNS: tuple[tuple[str, str, str, float], ...] = (
    ("score", "perf", "{:.0f}", 100.0),
    ("fcp_ms", "FCP ms", "{:.0f}", 1.0),
    ("lcp_ms", "LCP ms", "{:.0f}", 1.0),
    ("tbt_ms", "TBT ms", "{:.0f}", 1.0),
    ("cls", "CLS", "{:.4f}", 1.0),
    ("total_bytes", "bytes", "{:.0f}", 1.0),
    ("requests", "requests", "{:g}", 1.0),
    ("font_bytes_before_fcp", "font bytes by FCP", "{:.0f}", 1.0),
)


@dataclass(frozen=True)
class Job:
    """One scheduled Lighthouse run."""

    round: int
    page: str
    preset: str


@dataclass(frozen=True)
class Outcome:
    """What one run produced: its metrics, audits and Lighthouse version, or an error."""

    job: Job
    metrics: lighthouse_report.RunMetrics | None = None
    version: str | None = None
    error: str | None = None
    # Out of the hash: the audits are a dict, which cannot be hashed.
    audits: lighthouse_audits.RunAudits | None = field(default=None, hash=False)


@dataclass(frozen=True)
class PageResult:
    """Every run of one page on one preset."""

    page: str
    preset: str
    outcomes: tuple[Outcome, ...]

    def measured(self) -> list[lighthouse_report.RunMetrics]:
        """The metrics of the runs that measured."""
        return [o.metrics for o in self.outcomes if o.metrics is not None]

    def medians(self) -> dict[str, float] | None:
        """Per-metric medians over the measured runs; `None` when no run measured."""
        measured = self.measured()
        return lighthouse_report.median_metrics(measured) if measured else None

    def audit_medians(self) -> audit_summary.AuditMedians | None:
        """The audits reduced over the measured runs; `None` when no run measured."""
        return audit_summary.audit_medians(
            [o.audits for o in self.outcomes if o.audits is not None]
        )


def collect_results(
    outcomes: Sequence[Outcome], pages: Sequence[str], presets: Sequence[str]
) -> list[PageResult]:
    """Group outcomes by page and preset, in the order the pages and presets were given."""
    return [
        PageResult(
            page,
            preset,
            tuple(o for o in outcomes if o.job.page == page and o.job.preset == preset),
        )
        for page in pages
        for preset in presets
    ]


def lighthouse_versions(results: Sequence[PageResult]) -> list[str]:
    """Every Lighthouse version that wrote a report, sorted; one entry when all agree."""
    return sorted(
        {o.version for r in results for o in r.outcomes if o.version is not None}
    )


def failures(results: Sequence[PageResult]) -> list[Outcome]:
    """Every run that did not measure, in schedule order within each page and preset."""
    return [o for r in results for o in r.outcomes if o.error is not None]


def _result_record(result: PageResult) -> dict[str, object]:
    """One page and preset's entry in the summary."""
    return {
        "page": result.page,
        "preset": result.preset,
        "measured_runs": len(result.measured()),
        "medians": result.medians(),
        "audit_medians": result.audit_medians(),
        "runs": [
            {
                "round": o.job.round,
                "metrics": None if o.metrics is None else asdict(o.metrics),
                "audits": o.audits,
                "error": o.error,
            }
            for o in result.outcomes
        ],
    }


def summary_document(
    results: Sequence[PageResult],
    *,
    tool: str,
    runs: int,
    flags: Mapping[str, Sequence[str]],
    served: str | None,
    url: str | None,
    commit: commit_facts.CommitFacts,
    chrome: run_facts.ChromeFacts,
) -> dict[str, object]:
    """The `summary.json` a run writes: what was measured, with what, and every number.

    `served` is the folder `--serve` named, as given, or `None` for the default `src/`. The
    commit alone cannot tell the two apart: a package in an ignored folder inside the checkout
    reads as the checkout's HEAD, clean, whatever commit it was built from. On a hosted run,
    `served` is the `--expect` folder as given -- the tree whose bytes were measured -- and
    `url` is the base measured; `url` is `None` on every loopback run, so a summary says which
    kind of run it records.

    Chrome's version is recorded and its path is not: the path is a fact about this machine's
    layout, and the version is what a later run compares against. `served` can be such a path
    too -- an absolute folder is recorded as given -- and is kept anyway: it is the one field
    naming the tree measured, which the commit cannot. That, and a `url` that may name a
    private address, is why a summary, like the raw reports beside it, stays a local record
    and is never committed.
    """
    return {
        "tool": tool,
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "served": served,
        "url": url,
        "commit": commit.sha,
        # The report's own name for the shared `dirty`: older summaries use it, and the
        # recorded baselines are compared by it.
        "src_dirty": commit.dirty,
        "lighthouse_versions": lighthouse_versions(results),
        "chrome_version": chrome.version,
        "runs_per_page_and_preset": runs,
        "flags": {preset: list(preset_flags) for preset, preset_flags in flags.items()},
        "results": [_result_record(result) for result in results],
    }


def format_table(results: Sequence[PageResult]) -> list[str]:
    """The medians as a Markdown table, one row per page and preset."""
    header = ["page", "preset", "runs", *(heading for _, heading, _, _ in _COLUMNS)]
    rows = [header, ["---"] * len(header)]
    for result in results:
        medians = result.medians()
        cells = (
            ["n/a"] * len(_COLUMNS)
            if medians is None
            else [fmt.format(medians[name] * scale) for name, _, fmt, scale in _COLUMNS]
        )
        rows.append([result.page, result.preset, str(len(result.measured())), *cells])
    return ["| " + " | ".join(row) + " |" for row in rows]


def format_audit_table(results: Sequence[PageResult]) -> list[str]:
    """The audit medians as a Markdown table, one row per page and preset."""
    return audit_summary.format_table(
        [(result.page, result.preset, result.audit_medians()) for result in results]
    )
