"""The run's verdict, its cross-engine comparison, and the report written in JSON and Markdown.

Exit codes: `0` every rendering measured and every detector silent (and, when proving, every
plant caught); `1` a detector found something, or a detector missed its plant; `2` the
measurement cannot be trusted -- an emulated state did not take, a page never settled, a plant
never planted, or nothing was measured at all. A `2` outranks a `1`: a finding from an instrument
that is not measuring what it claims is not a finding.

The JSON keeps everything, down to every cross-engine difference. The Markdown is for reading:
the verdict first, then only the sections that have something in them, with a finding that
recurs across renderings listed once.

Entry point assumptions: pure functions over finished results, plus `write_report`, which
writes into a folder the caller names.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from pass_checks import CSP_VIOLATION, DETECTORS, csp_watched
from pass_digest import AxisSummary, Box, Difference, compare_digests, summarize
from pass_extension import ExtensionFacts, isolation_lines
from pass_markdown import code_span, plain_text, rendering_label
from pass_motion import NO_MOTION, Motion, motion_record, motion_section
from pass_redirects import Redirect, report_lines
from pass_shift import (
    LATE_SHIFT,
    MEASURED,
    NOT_MEASURED,
    THRESHOLD,
    LateShift,
    late_record,
)
from pass_targets import Rendering, name_renderings, page_label

#: The two files one run writes into its folder.
JSON_NAME = "report.json"
MARKDOWN_NAME = "report.md"

#: Every detector a report names: the page-load detectors, then the late-script load's.
REPORTED_DETECTORS = (*DETECTORS, LATE_SHIFT)

#: Proof verdicts, in the order a proof-table cell counts them. `not measured` is a plant in an
#: engine with no way to report it: neither caught nor missed.
_VERDICTS = ("caught", "missed", "not planted", "n/a", NOT_MEASURED)

#: A proof verdict that makes the run an instrument fault, and one that makes it a finding.
FAULT_VERDICT = "not planted"
MISS_VERDICT = "missed"

_EXIT_CLEAN, _EXIT_FOUND, _EXIT_UNTRUSTED = 0, 1, 2


@dataclass(frozen=True)
class RenderingResult:
    """What one rendering produced: faults, findings, proof verdicts, digest and late shifts.

    And the motion its settle stilled or left running (`pass_motion`): reported, never a
    finding, and kept by a rendering whose settle ran out of time after stilling some. The
    late-script load's settle keeps its own, apart: it stills the same animations again.
    """

    rendering: Rendering
    faults: tuple[str, ...]
    findings: Mapping[str, tuple[str, ...]]
    verdicts: Mapping[str, str]
    digest: Mapping[str, Box]
    late_shift: LateShift
    motion: Motion
    late_motion: Motion = NO_MOTION


#: Every rendering's result, in the order the matrix ran.
Results = list[RenderingResult]


@dataclass(frozen=True)
class RunFacts:
    """What the run measured with, so a later run can tell a site change from an engine change."""

    started: str
    commit: str | None
    tree_dirty: bool | None
    served: str | None
    base_url: str
    playwright: str
    engines: Mapping[str, str]
    prove: bool
    #: Whether a page load declaring no Content-Security-Policy meta was a finding.
    require_csp: bool
    #: Each declared meta-refresh page, and the page measured in its place.
    redirects: tuple[Redirect, ...] = ()
    #: Whether every request off 127.0.0.1 was refused (`--loopback-only`, or an extension).
    loopback_only: bool = False
    #: Each engine's extension, by manifest name and version (and gecko id for Firefox).
    extensions: tuple[ExtensionFacts, ...] = ()


@dataclass(frozen=True)
class Comparison:
    """One rendering key, as the reference engine and one other engine laid it out."""

    key: tuple[str, str | None, int, int, str, str]
    reference: str
    other: str
    differences: tuple[Difference, ...]
    summary: Mapping[str, AxisSummary]


def exit_code(results: Sequence[RenderingResult]) -> int:
    """The run's exit code; see the module docstring."""
    verdicts = [verdict for result in results for verdict in result.verdicts.values()]
    if (
        not results
        or any(result.faults for result in results)
        or FAULT_VERDICT in verdicts
    ):
        return _EXIT_UNTRUSTED
    if any(result.findings for result in results) or MISS_VERDICT in verdicts:
        return _EXIT_FOUND
    return _EXIT_CLEAN


def cross_engine(
    results: Sequence[RenderingResult], *, reference: str
) -> list[Comparison]:
    """Compare each engine's rendering with the reference engine's rendering of the same key.

    A rendering with an instrument fault is compared with nothing: its geometry was taken in a
    state nobody asked for.
    """
    trusted = [result for result in results if not result.faults]
    anchors = {r.rendering.key: r for r in trusted if r.rendering.engine == reference}
    comparisons: list[Comparison] = []
    for result in trusted:
        anchor = anchors.get(result.rendering.key)
        if result.rendering.engine == reference or anchor is None:
            continue
        differences = compare_digests(anchor.digest, result.digest)
        comparisons.append(
            Comparison(
                key=result.rendering.key,
                reference=reference,
                other=result.rendering.engine,
                differences=tuple(differences),
                summary=summarize(differences),
            )
        )
    return comparisons


def report_document(
    results: Sequence[RenderingResult],
    comparisons: Sequence[Comparison],
    facts: RunFacts,
) -> dict[str, object]:
    """The full record, ready for `json.dumps`."""
    return {
        "tool": "browser_pass",
        "exit_code": exit_code(results),
        "verdict": _verdict_label(results),
        "facts": asdict(facts),
        "renderings": [_rendering_record(result) for result in results],
        "cross_engine": [_comparison_record(comparison) for comparison in comparisons],
    }


def _rendering_record(result: RenderingResult) -> dict[str, object]:
    """One rendering's entry in the JSON record (its digest is summarized, not kept)."""
    rendering = result.rendering
    return {
        "engine": rendering.engine,
        "page": rendering.page,
        "state": rendering.state,
        "width": rendering.width,
        "height": rendering.height,
        "orientation": rendering.orientation,
        "mode": rendering.mode,
        "scripts": rendering.scripts,
        "faults": list(result.faults),
        "findings": {
            detector: list(items) for detector, items in result.findings.items()
        },
        "verdicts": dict(result.verdicts),
        "late_shift": late_record(result.late_shift),
        "motion": motion_record(result.motion),
        "late_motion": motion_record(result.late_motion),
    }


def _comparison_record(comparison: Comparison) -> dict[str, object]:
    """One comparison's entry in the JSON record, every difference included."""
    page, state, width, height, mode, scripts = comparison.key
    return {
        "page": page,
        "state": state,
        "width": width,
        "height": height,
        "mode": mode,
        "scripts": scripts,
        "reference": comparison.reference,
        "other": comparison.other,
        "summary": {
            axis: asdict(summary) for axis, summary in comparison.summary.items()
        },
        "differences": [[d.path, d.axis, d.delta] for d in comparison.differences],
    }


def write_report(run_dir: Path, document: Mapping[str, object], markdown: str) -> None:
    """Write the JSON record and the Markdown page into `run_dir`, creating it."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / JSON_NAME).write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (run_dir / MARKDOWN_NAME).write_text(markdown, encoding="utf-8", newline="\n")


def _verdict_label(results: Sequence[RenderingResult]) -> str:
    """The one-word verdict a reader sees first."""
    code = exit_code(results)
    if code == _EXIT_UNTRUSTED:
        return "INSTRUMENT FAULT"
    if any(MISS_VERDICT in result.verdicts.values() for result in results):
        return "PROOF FAILED"
    return "FINDINGS" if code == _EXIT_FOUND else "CLEAN"


def report_markdown(
    results: Sequence[RenderingResult],
    comparisons: Sequence[Comparison],
    facts: RunFacts,
) -> str:
    """The page a person reads."""
    engines = list(facts.engines)
    sections = [
        _header(results, facts),
        _detector_table(results, engines),
        _findings_section(results),
        _faults_section(results),
        motion_section(results),
        _proof_section(results, engines) if facts.prove else "",
        _late_shift_section(results, engines),
        _cross_engine_section(comparisons, engines[0] if engines else ""),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def _header(results: Sequence[RenderingResult], facts: RunFacts) -> str:
    """The verdict and the facts a run is measured with."""
    dirty = " (the measured tree has uncommitted edits)" if facts.tree_dirty else ""
    source = (
        f"`{facts.served}`, served at {facts.base_url}"
        if facts.served
        else f"{facts.base_url} (not served by this run, so which tree answered is not proven)"
    )
    versions = "; ".join(
        f"{engine} {version}" for engine, version in facts.engines.items()
    )
    return "\n".join(
        [
            "# browser_pass report",
            "",
            f"**Verdict: {_verdict_label(results)}**",
            "",
            f"- Started: {facts.started} (UTC)",
            f"- Commit: {facts.commit or 'unknown'}{dirty}",
            f"- Pages from: {source}",
            f"- Engines: Playwright {facts.playwright}; {versions}",
            f"- Content-Security-Policy meta required: {'yes' if facts.require_csp else 'no'}",
            *isolation_lines(facts.loopback_only, facts.extensions),
            *report_lines(facts.redirects),
            f"- Renderings: {len(results)}",
        ]
    )


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """A Markdown table."""
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _detector_table(results: Sequence[RenderingResult], engines: Sequence[str]) -> str:
    """Per detector and engine: how many renderings it found something in."""
    rows = [
        [detector] + [_detector_cell(results, detector, engine) for engine in engines]
        for detector in REPORTED_DETECTORS
    ]
    return (
        "## Detectors\n\nRenderings in which each detector found something. "
        f"`{NOT_MEASURED}` marks an engine a detector cannot see into, and `n/a` renderings "
        "it has nothing to look at in: neither is a zero.\n\n"
        + _table(["Detector", *engines], rows)
    )


def _detector_cell(
    results: Sequence[RenderingResult], detector: str, engine: str
) -> str:
    """How many of `engine`'s renderings `detector` found something in, or why it saw none.

    Never a bare 0 without a measurement behind it -- including for an engine with no
    renderings at all, where 0 would read as a page that did not move.
    """
    mine = [r for r in results if r.rendering.engine == engine]
    count = sum(1 for r in mine if detector in r.findings)
    if detector == LATE_SHIFT and not any(_measured(r) for r in mine):
        return _unmeasured(mine).status if mine else NOT_MEASURED
    watched = any(csp_watched(r.rendering) for r in mine)
    if detector == CSP_VIOLATION and not (count or watched):
        # Only the collector's listener reports a violation, and none runs with scripts off.
        return NOT_MEASURED
    return str(count)


def _measured(result: RenderingResult) -> bool:
    """Whether `result`'s late-script load measured anything."""
    return result.late_shift.status == MEASURED


def _unmeasured(results: Sequence[RenderingResult]) -> LateShift:
    """The late-shift record that best says why none of `results` was measured.

    `not measured` outranks `n/a`: an engine that cannot see a late shift is the stronger
    statement than a scripts-off rendering that has none to see.
    """
    return next(
        (r.late_shift for r in results if r.late_shift.status == NOT_MEASURED),
        results[0].late_shift,
    )


def _findings_section(results: Sequence[RenderingResult]) -> str:
    """Each finding once, with the renderings it recurred in."""
    where: dict[tuple[str, str], list[str]] = {}
    for result in results:
        for detector, items in result.findings.items():
            for item in items:
                label = rendering_label(result.rendering)
                where.setdefault((detector, item), []).append(label)
    if not where:
        return "## Findings\n\nNo detector found anything."
    lines = ["## Findings"]
    for detector in REPORTED_DETECTORS:
        entries = [
            (item, labels) for (d, item), labels in where.items() if d == detector
        ]
        if entries:
            lines.append(f"\n### {detector}\n")
            lines.extend(_finding_line(item, labels) for item, labels in entries)
    return "\n".join(lines)


def _finding_line(item: str, labels: Sequence[str]) -> str:
    """One finding and where it occurred, the renderings named up to a handful.

    The finding is the page's own text (an element id, a console message, a URL), so it is
    written as a span it cannot break out of.
    """
    return f"- {code_span(item)} in {name_renderings(labels)}"


def _faults_section(results: Sequence[RenderingResult]) -> str:
    """Every rendering the run could not measure honestly, and why."""
    lines = [
        f"- {rendering_label(result.rendering)}: {plain_text(fault)}"
        for result in results
        for fault in result.faults
    ]
    return "## Instrument faults\n\n" + "\n".join(lines) if lines else ""


def _proof_section(results: Sequence[RenderingResult], engines: Sequence[str]) -> str:
    """Per detector and engine: how the planted faults fared."""
    rows = [
        [detector] + [_proof_cell(results, detector, e) for e in engines]
        for detector in REPORTED_DETECTORS
    ]
    return (
        "## Proof\n\nOne fault planted per detector in every rendering.\n\n"
        + _table(["Detector", *engines], rows)
    )


def _proof_cell(results: Sequence[RenderingResult], detector: str, engine: str) -> str:
    """How one detector's plants fared in one engine, e.g. `caught 12, n/a 12`."""
    counts = Counter(
        r.verdicts[detector]
        for r in results
        if r.rendering.engine == engine and detector in r.verdicts
    )
    return ", ".join(f"{v} {counts[v]}" for v in _VERDICTS if counts[v]) or "—"


def _late_shift_section(
    results: Sequence[RenderingResult], engines: Sequence[str]
) -> str:
    """Per engine: renderings measured, how many moved above the threshold, and the largest."""
    head = (
        "## Late-script layout shifts\n\nEvery rendering with scripts on is loaded a second "
        "time with its scripts held, then released -- once the page has painted in its own "
        "fonts, or at once when a held script is one it cannot paint without. Its score is the "
        "largest session window of the shifts after it was PRESENTABLE (painted, in its own "
        "fonts), which is the release itself except on that early-release path, as Cumulative "
        f"Layout Shift groups them; a window above {THRESHOLD:g} is a finding."
    )
    if not any(_measured(r) for r in results):
        head += "\n\nNo rendering in this run was measured for late-script shifts."
    rows = [_late_shift_row(results, engine) for engine in engines]
    return f"{head}\n\n" + _table(
        ["Engine", "Measured", "Above threshold", "Largest", "In"], rows
    )


def _late_shift_row(results: Sequence[RenderingResult], engine: str) -> list[str]:
    """One engine's row: its measured renderings summarized, or why it measured none."""
    mine = [r for r in results if r.rendering.engine == engine]
    measured = [r for r in mine if _measured(r)]
    if not measured:
        unmeasured = _unmeasured(mine) if mine else LateShift(NOT_MEASURED)
        return [engine, f"{unmeasured.status}: {unmeasured.reason}", "—", "—", "—"]
    largest = max(measured, key=lambda r: r.late_shift.score)
    above = sum(1 for r in measured if r.late_shift.score > THRESHOLD)
    return [
        engine,
        str(len(measured)),
        str(above),
        f"{largest.late_shift.score:.4f}",
        rendering_label(largest.rendering, in_table=True)
        if largest.late_shift.score
        else "—",
    ]


def _cross_engine_section(comparisons: Sequence[Comparison], reference: str) -> str:
    """Differences against the reference engine, one row per page, engine and axis."""
    head = (
        f"## Cross-engine differences\n\nReference engine: {reference}. Box shifts up to "
        "0.5px, font-size differences under 0.1px and elements neither engine displays are "
        "dropped as noise. Largest at names the element carrying the row's largest difference; "
        "first divergence names where the engines begin to differ in page order, the element "
        "most renderings in the row share. The full list is in the JSON record."
    )
    if not comparisons:
        return f"{head}\n\nNothing was compared: one engine ran, or no rendering had a pair."
    grouped: dict[tuple[str, str, str], list[AxisSummary]] = {}
    for comparison in comparisons:
        # A state is its own row beside its page: the page it shows is not the page as loaded.
        page = page_label(
            plain_text(comparison.key[0], in_table=True), comparison.key[1]
        )
        for axis, summary in comparison.summary.items():
            grouped.setdefault((page, comparison.other, axis), []).append(summary)
    if not grouped:
        return f"{head}\n\nNo cross-engine differences beyond the noise filters."
    rows = [
        _cross_engine_row(page, other, axis, summaries)
        for (page, other, axis), summaries in grouped.items()
    ]
    return f"{head}\n\n" + _table(
        [
            "Page",
            "Engine",
            "Axis",
            "Renderings",
            "Max Δ px",
            "Largest at",
            "First divergence",
        ],
        rows,
    )


def _cross_engine_row(
    page: str, other: str, axis: str, summaries: Sequence[AxisSummary]
) -> list[str]:
    """One table row: the largest difference NAMED WITH ITS OWN ELEMENT, then the divergence.

    Pairing the row's maximum with the first divergence alone read as that element moving by
    it: a 248px box difference on a <noscript> wrapper was shown beside a single letter's box.
    """
    largest = max(summaries, key=lambda summary: summary.max_delta)
    first = Counter(summary.first_path for summary in summaries).most_common(1)[0][0]
    return [
        page,
        other,
        axis,
        str(len(summaries)),
        f"{largest.max_delta:g}",
        code_span(largest.max_path, in_table=True),
        code_span(first, in_table=True),
    ]
