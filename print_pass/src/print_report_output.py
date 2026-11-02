"""What is done with a finished report: its summary, its exit code, its normal form, its file.

`print_report` shapes a run into the report; this module takes the report as shaped (or as
`json` loads one back) and turns it into what leaves the tool: the lines printed to the
terminal (findings first), the exit code, the normalized copy a sample's expected report
holds (nothing that varies by run or machine), and `report.json` itself, written whole or
not at all. It also holds the few words both halves share, so the shaping half can import
them without an import cycle through `print_report`, which re-exports this module's names.

Entry point assumptions: the standard library only. A report is read as the shape
`print_report` builds: a `mode`; `redirects`, `states`, `pdfs` and `comparisons` as present
in that mode;
`totals` in every mode but compare.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Final, cast

#: The file a report is written to, in the out dir.
REPORT_NAME: Final = "report.json"

#: How a lone surrogate is written, which UTF-8 cannot encode: as `\ud800`, its escape. Only
#: a lone surrogate fails UTF-8, and inside a JSON string that escape reads back as itself,
#: so the report stays valid UTF-8 JSON and every other character stays readable as itself
#: (`ensure_ascii` would escape them all).
SURROGATE_ERRORS: Final = "backslashreplace"

#: The mode whose exit code is its comparison's, not its findings'.
COMPARE_MODE: Final = "compare"

#: A problem's two severities: a finding fails the run, a warning is reported.
FINDING: Final = "finding"
WARNING: Final = "warning"

#: What `normalize_report` drops, by the entry it sits in: what varies by run, by machine,
#: by the fonts installed, or by the engine's build. The Producer names the engine's
#: milestone, as the browser version does; a font's type and encoding follow the font file
#: installed, as its name does, and so does what pdfminer logs reading the fonts embedded;
#: a link's rectangle, and a compared page's pixel count and box, follow the fonts'
#: metrics. Whether two prints are identical stays.
_STATE_VARYING: Final = ("browser_version", "timings")
_PDF_VARYING: Final = ("renders",)
_METADATA_VARYING: Final = ("producer",)
_FONT_VARYING: Final = ("base_name", "type", "encoding")
_LINK_VARYING: Final = ("rect",)
_TEXT_VARYING: Final = ("log",)
_COMPARED_PAGE_VARYING: Final = ("differing_pixels", "bbox_pt")

#: A loopback host and the port after it, which the served site picks fresh on every run:
#: the host is kept (group 1), the port masked. The look-behind keeps a longer name or
#: address that merely ends in one of these hosts, such as `a.localhost`, off the match.
_LOOPBACK_PORT: Final = re.compile(
    r"(?<![\w.-])(127\.0\.0\.1|localhost|\[::1\]):\d+", re.IGNORECASE
)
_PORT_MASKED: Final = r"\1:<port>"

#: How the summary writes a page count pdfinfo could not read.
_UNKNOWN: Final = "?"

#: The order the totals line names its counts in, whatever order the report holds them in:
#: `report.json` is written with sorted keys, so a report loaded back holds another. A count
#: missing here raises rather than vanishing from the line.
_TOTALS_ORDER: Final = ("states", "pdfs", "findings", "warnings")

type _Json = dict[str, object]


def summary_lines(report: Mapping[str, object]) -> list[str]:
    """The printed summary: findings, warnings, each PDF and comparison, then the totals.

    Totals are printed when the report has them; a compare report is its comparison alone.
    """
    located = list(_located_problems(report))
    lines = [
        f"FINDING {where}: {what}"
        for severity, where, what in located
        if severity == FINDING
    ]
    lines += [
        f"WARNING {where}: {what}"
        for severity, where, what in located
        if severity == WARNING
    ]
    lines += [_pdf_line(pdf) for pdf in _objects(report, "pdfs")]
    lines += [
        _comparison_line(comparison) for comparison in _objects(report, "comparisons")
    ]
    if "totals" in report:
        totals = cast(Mapping[str, object], report["totals"])
        ordered = sorted(totals, key=_TOTALS_ORDER.index)
        lines.append("totals: " + ", ".join(f"{key} {totals[key]}" for key in ordered))
    return lines


def exit_code(report: Mapping[str, object]) -> int:
    """1 when the report holds a finding (compare: when the PDFs differ), else 0."""
    if report["mode"] == COMPARE_MODE:
        comparisons = _objects(report, "comparisons")
        return int(not all(comparison["identical"] for comparison in comparisons))
    totals = cast(Mapping[str, object], report["totals"])
    return 1 if totals["findings"] else 0


def normalize_report(report: Mapping[str, object]) -> dict[str, object]:
    """A copy of `report` holding nothing that varies by run or machine.

    Drops every key the `_*_VARYING` tuples above name from the entries they name, and
    masks the port after a loopback host in every string, wherever it sits; a text layer
    keeps its page count in place of its per-page character counts, and its non-ASCII
    glyphs without the fonts that drew them.
    """
    normalized = cast(_Json, _with_ports_masked(report))
    for state in _objects(normalized, "states"):
        _drop(state, _STATE_VARYING)
    for pdf in _objects(normalized, "pdfs"):
        _normalize_pdf(pdf)
    for comparison in _objects(normalized, "comparisons"):
        for page in _objects(comparison, "pages"):
            _drop(page, _COMPARED_PAGE_VARYING)
    return normalized


def write_report(report: Mapping[str, object], out_dir: Path) -> Path:
    """Write `report` to `<out_dir>/report.json` whole, or not at all; return its path.

    Sorted keys, two-space indent, UTF-8 and one closing LF. Every character is written as
    itself but a lone surrogate, which a page may hand over (a JavaScript string can hold
    half a pair) and no UTF-8 can encode: it is written as its JSON escape (a backslash,
    `u` and four hex digits), which reads back as the same string. The text is written to a
    temp file beside the report and moved into place, so a reader never sees half a report;
    the temp file is removed however the write ends.

    Raises:
        TypeError: the report holds a value JSON cannot carry (nothing is written).
        OSError: the file could not be written or moved into place.
    """
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    target = out_dir / REPORT_NAME
    handle, temp = tempfile.mkstemp(
        dir=out_dir, prefix=f".{REPORT_NAME}.", suffix=".tmp"
    )
    try:
        with os.fdopen(
            handle, "w", encoding="utf-8", errors=SURROGATE_ERRORS, newline="\n"
        ) as stream:
            stream.write(text)
        os.replace(temp, target)
    finally:
        # After the replace the temp name is gone; after a failure this removes it.
        Path(temp).unlink(missing_ok=True)
    return target


def _with_ports_masked(value: object) -> object:
    """A copy of the JSON `value` in which every loopback host's port reads `<port>`."""
    if isinstance(value, str):
        return _LOOPBACK_PORT.sub(_PORT_MASKED, value)
    if isinstance(value, Mapping):
        return {key: _with_ports_masked(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_with_ports_masked(item) for item in value]
    return value


def _normalize_pdf(pdf: _Json) -> None:
    """Drop, in place, what `pdf`'s entry holds that varies; reduce its text layer."""
    _drop(pdf, _PDF_VARYING)
    if pdf["metadata"] is not None:
        _drop(cast(_Json, pdf["metadata"]), _METADATA_VARYING)
    for font in _objects(pdf, "fonts"):
        _drop(font, _FONT_VARYING)
    for link in _objects(pdf, "links"):
        _drop(link, _LINK_VARYING)
    if pdf["text"] is not None:
        text = cast(_Json, pdf["text"])
        _drop(text, _TEXT_VARYING)
        text["chars_per_page"] = len(cast(list[object], text["chars_per_page"]))
        text["non_ascii_glyph_fonts"] = sorted(
            cast(_Json, text["non_ascii_glyph_fonts"])
        )


def _objects(report: Mapping[str, object], key: str) -> list[_Json]:
    """The JSON objects listed under `key`; none when this mode's report has no such key."""
    return cast(list[_Json], report.get(key, []))


def _drop(entry: _Json, keys: Iterable[str]) -> None:
    """Remove each of `keys` from `entry`."""
    for key in keys:
        entry.pop(key, None)


def _located_problems(
    report: Mapping[str, object],
) -> Iterator[tuple[object, str, object]]:
    """Every problem in the report as (severity, where, what): redirects, states, then PDFs.

    A redirect's problem is its target's, since none of its states was printed.
    """
    for redirect in _objects(report, "redirects"):
        for problem in _objects(redirect, "problems"):
            yield problem["severity"], str(redirect["target"]), problem["what"]
    for state in _objects(report, "states"):
        where = f"{state['target']}/{state['state']}"
        for problem in _objects(state, "problems"):
            yield problem["severity"], where, problem["what"]
    for pdf in _objects(report, "pdfs"):
        for problem in _objects(pdf, "problems"):
            yield problem["severity"], str(pdf["file"]), problem["what"]


def _pdf_line(pdf: _Json) -> str:
    """`<file>: <pages> page(s), <fonts> font(s), <images> image(s), <links> link(s)`."""
    pages = _UNKNOWN if pdf["pages"] is None else pdf["pages"]
    images = cast(_Json, pdf["images"])
    counts = (
        f"{pages} page(s)",
        f"{len(_objects(pdf, 'fonts'))} font(s)",
        f"{len(_objects(images, 'items'))} image(s)",
        f"{len(_objects(pdf, 'links'))} link(s)",
    )
    return f"{pdf['file']}: {', '.join(counts)}"


def _comparison_line(comparison: _Json) -> str:
    """`<a> vs <b>: identical`, or what differs: the page counts, then the pages."""
    head = f"{comparison['a']} vs {comparison['b']}"
    if comparison["identical"]:
        return f"{head}: identical"
    count_a, count_b = cast(list[object], comparison["page_count"])
    pages = _objects(comparison, "pages")
    differing = [str(page["page"]) for page in pages if not page["identical"]]
    parts = [f"page counts {count_a} and {count_b}"] if count_a != count_b else []
    if differing:
        parts.append(f"page(s) {', '.join(differing)} differ")
    return f"{head}: {'; '.join(parts)}"
