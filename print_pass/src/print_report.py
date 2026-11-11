"""The report: every state and PDF of a run shaped into the JSON `report.json` holds.

`make_report`, `read_report` and `compare_report` shape one run of each mode into the JSON
the tool writes. The keys are fixed: `make` emits every top-level key, `cannot_observe` only
for Firefox; `read` its PDFs and totals; `compare` its one comparison. Reading one PDF and
comparing two are `print_report_pdf`'s; the scrub every text passes through is
`print_report_scrub`'s; what is done with a finished report (its summary, exit code,
normalized copy and file) is `print_report_output`'s. Their entry points are re-exported
here (`__all__`), so a caller imports the report from one place.

A report is safe to share: every path in it is relative to the out dir (or a bare file
name), and every problem text, each network entry, each expected off-loopback host, and each
string the page itself supplied to a state's read-back (an image's source, an animation's
name and target, a read's text values, a stored value) has passed through `scrub`, which
replaces the out dir, the home, the hostnames and any other home. The page's title is
withheld instead when the privacy matcher flags it, as the PDF's Title it becomes is. The
read-back's other words (`ready_state`, `color_scheme`, `reduced_motion`, `fonts_status`
and each image's `loading`) are the browser's own, each from a fixed vocabulary the page
does not write (the probe's reader refuses a ready state, a color scheme, a reduced-motion
setting, a font status or an image's loading outside its own), so none can carry a path or
a hostname and none is scrubbed.

Entry point assumptions: the engine's records, the targets file they came from, and
`print_report_pdf`'s readings; the tool's name is `print_plan`'s, which every message opens
with too.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Final

import gecko_network
import gecko_print
from pdf_privacy import Identity
from pdf_raster import Share
from pdf_tools import TITLE_FIELD, PdfInfo
from print_engine import MARGIN, Problem, StateRecord, print_options
from print_plan import TOOL
from print_probe import ProbeResult, ReadValue
from print_record import FIREFOX, Engine
from print_report_output import (
    COMPARE_MODE,
    FINDING,
    REPORT_NAME,
    WARNING,
    exit_code,
    normalize_report,
    summary_lines,
    write_report,
)
from print_report_pdf import PdfReport, compare_pdfs, read_pdf, report_path
from print_report_scrub import scrub
from print_report_withhold import withhold_field
from print_targets import PX_PER_INCH, Action, TargetsFile

__all__ = [
    "REPORT_NAME",
    "SCHEMA_VERSION",
    "PdfReport",
    "compare_pdfs",
    "compare_report",
    "exit_code",
    "make_report",
    "normalize_report",
    "read_pdf",
    "read_report",
    "report_path",
    "scrub",
    "summary_lines",
    "write_report",
]

#: The report's own version: raised when a key changes meaning or leaves.
SCHEMA_VERSION: Final = 2

_MAKE: Final = "make"
_READ: Final = "read"

#: The report's name for each WebDriver Print parameter Firefox is sent: the W3C name in
#: snake_case, as the Playwright entry's keys are. The page and margins carry their unit in
#: the key, where the Playwright entry's margin carries it in its value (`1cm`): the
#: command's numbers are centimeters, and are written as they were sent. A parameter added
#: to the body without a name here fails the report rather than vanish from it.
_GECKO_PRINT_KEYS: Final = MappingProxyType(
    {
        "page": "page_cm",
        "margin": "margin_cm",
        "scale": "scale",
        "shrinkToFit": "shrink_to_fit",
        "orientation": "orientation",
    }
)

#: The one WebDriver Print parameter each PDF sets for itself, as `print_background` is in
#: Playwright's options: the entry states how EVERY page was printed, so it leaves it out.
_GECKO_PER_PDF: Final = "background"

#: Decimal places a state's margin in CSS px keeps: a page width's is a whole or half pixel,
#: and four read an engine's own 1 cm (37.7953 px) without a float's tail.
_PX_PLACES: Final = 4

type _Json = dict[str, object]
type _Scrub = Callable[[str], str]


def make_report(
    records: Sequence[StateRecord],
    pdf_reports: Mapping[Path, PdfReport],
    comparisons: Sequence[dict[str, object]],
    *,
    out_dir: Path,
    targets: TargetsFile,
    engine: Engine,
    header_footer: bool,
    identity: Identity,
) -> dict[str, object]:
    """The make report: how it printed, every state, every PDF read, every pair compared.

    `targets` is the file the records were printed from: it says which reads are first-match,
    whose empty read-back is null. `engine` and `header_footer` are the run's settings, and
    say how every page was printed; under Firefox, the report also says what the states'
    network records cannot list. `identity` is the one the PDFs were read with: its home
    and hostnames are scrubbed from every text. PDFs are listed in the order `pdf_reports`
    holds them.

    Raises:
        KeyError: a record names a target `targets` does not hold; or, under Firefox, a
            WebDriver Print parameter sent has no report name in `_GECKO_PRINT_KEYS`.
        ValueError: a header and footer asked of Firefox, which never prints one.
    """
    first_match = {
        target.name: frozenset(read.name for read in target.reads if not read.all)
        for target in targets.targets
    }
    clean = functools.partial(scrub, out_dir=out_dir, identity=identity)
    states = [
        _state_json(record, out_dir, first_match[record.target], clean, identity)
        for record in records
    ]
    pdfs = list(pdf_reports.values())
    problems = [problem for record in records for problem in record.problems]
    return {
        **_header(_MAKE),
        "print": _print_json(engine, header_footer),
        **_cannot_observe_json(engine),
        "targets": _targets_json(targets, clean),
        "states": states,
        "pdfs": [_pdf_json(report) for report in pdfs],
        "comparisons": list(comparisons),
        "totals": {
            "states": len(states),
            "pdfs": len(pdfs),
            **_severity_totals([*problems, *_pdf_problems(pdfs)]),
        },
    }


def read_report(pdf_reports: Sequence[PdfReport]) -> dict[str, object]:
    """The read report: every PDF read, and the totals."""
    return {
        **_header(_READ),
        "pdfs": [_pdf_json(report) for report in pdf_reports],
        "totals": {
            "pdfs": len(pdf_reports),
            **_severity_totals(_pdf_problems(pdf_reports)),
        },
    }


def compare_report(comparison: dict[str, object]) -> dict[str, object]:
    """The compare report: the one comparison `compare_pdfs` made."""
    return {**_header(COMPARE_MODE), "comparisons": [comparison]}


def _header(mode: str) -> _Json:
    """The keys every report opens with."""
    return {"tool": TOOL, "schema": SCHEMA_VERSION, "mode": mode}


def _print_json(engine: Engine, header_footer: bool) -> _Json:
    """How every page was printed, from what `engine` itself is sent for each print."""
    if engine == FIREFOX:
        return _gecko_print_json(header_footer)
    return _playwright_print_json(header_footer)


def _gecko_print_json(header_footer: bool) -> _Json:
    """Firefox's entry: the WebDriver Print body sent, and what that command cannot set.

    Read from the body itself, less the parameter each PDF sets, each under its
    `_GECKO_PRINT_KEYS` name. `tagged` and `header_footer` are the Playwright entry's keys,
    each false here because each says what was requested, and the command takes no tagging
    parameter and prints no header or footer. Whether the build tagged a PDF anyway is that
    PDF's `metadata.tagged`. `cannot_control` says so in words, with the `Title` it cannot set.

    Raises:
        KeyError: a parameter sent has no report name in `_GECKO_PRINT_KEYS`, which the
            entry would otherwise state under a name no report was meant to carry.
        ValueError: `header_footer` is asked: the entry could only misstate the run.
    """
    if header_footer:
        raise ValueError("WebDriver's Print never prints a header or footer")
    sent = gecko_print.print_parameters(backgrounds=False).items()
    return {
        **{
            _GECKO_PRINT_KEYS[key]: value
            for key, value in sent
            if key != _GECKO_PER_PDF
        },
        "tagged": False,
        "header_footer": False,
        "cannot_control": list(gecko_print.CANNOT_CONTROL),
    }


def _cannot_observe_json(engine: Engine) -> _Json:
    """What a Firefox run's network records cannot list; no key at all for another engine."""
    if engine != FIREFOX:
        return {}
    return {"cannot_observe": list(gecko_network.CANNOT_OBSERVE)}


def _playwright_print_json(header_footer: bool) -> _Json:
    """The shell's and installed Chrome's entry, from the options `page.pdf` is passed."""
    options = print_options(backgrounds=False, header_footer=header_footer)
    return {
        "format": options["format"],
        "margin": MARGIN,
        "tagged": options["tagged"],
        "header_footer": options["display_header_footer"],
        "prefer_css_page_size": options["prefer_css_page_size"],
        "scale": options["scale"],
    }


def _targets_json(targets: TargetsFile, clean: _Scrub) -> _Json:
    """What the targets file set that weighs the problems: the hosts off loopback it expects.

    Recorded once for the run, beside no state, so a reader sees why a blocked request is a
    warning, and the list has one source. Each host is scrubbed, as a request quoting it is.
    """
    return {
        "expected_offsite_hosts": [
            clean(host) for host in targets.expected_offsite_hosts
        ]
    }


def _severity_totals(problems: Iterable[Problem]) -> _Json:
    """How many of `problems` are findings, and how many warnings."""
    severities = [problem.severity for problem in problems]
    return {
        "findings": severities.count(FINDING),
        "warnings": severities.count(WARNING),
    }


def _pdf_problems(reports: Iterable[PdfReport]) -> list[Problem]:
    """Every problem of every PDF read."""
    return [problem for report in reports for problem in report.problems]


def _problem_json(problem: Problem, clean: _Scrub) -> _Json:
    """A problem as the report writes it, its text scrubbed."""
    return {"severity": problem.severity, "what": clean(problem.what)}


def _state_json(
    record: StateRecord,
    out_dir: Path,
    first_match: frozenset[str],
    clean: _Scrub,
    identity: Identity,
) -> _Json:
    """One state as the report writes it."""
    network = record.network
    return {
        "target": record.target,
        "state": record.state,
        "page": record.page,
        "engine": record.engine,
        "browser_version": record.browser_version,
        "viewport": {"width": record.viewport.width, "height": record.viewport.height},
        "color_scheme": {
            "requested": record.color_scheme,
            "emulated": record.color_scheme_emulated,
        },
        "reduced_motion": {
            "requested": record.reduced_motion,
            "emulated": record.reduced_motion_emulated,
        },
        # A targets file may name a home path in a selector, a value, or a stored key or value.
        "storage": {clean(key): clean(value) for key, value in record.storage},
        "actions": [clean(_action_text(action)) for action in record.actions],
        "print_at_ms": record.print_at_ms,
        "page_width": record.page_width,
        "margin_px": round(float(record.margin_inches * PX_PER_INCH), _PX_PLACES),
        "read_back": None
        if record.probe is None
        else _read_back_json(record.probe, first_match, clean, identity),
        # Each entry is quoted by a finding, so it is scrubbed as the finding is.
        "network": {
            "failed": [clean(entry) for entry in network.failed],
            "http_errors": [clean(entry) for entry in network.http_errors],
            "blocked": [clean(entry) for entry in network.blocked],
            "page_errors": [clean(entry) for entry in network.page_errors],
            "csp_violations": [clean(entry) for entry in network.csp_violations],
        },
        "timings": {"load_ms": record.load_ms, "printed_at_ms": record.printed_at_ms},
        "pdfs": [report_path(made.path, out_dir) for made in record.pdfs],
        "problems": [_problem_json(problem, clean) for problem in record.problems],
    }


def _action_text(action: Action) -> str:
    """`click #start`, `wait_for #ready`, `wait_ms 300` or `set_attribute :root a=""`."""
    return action.text()


def _read_back_json(
    probe: ProbeResult, first_match: frozenset[str], clean: _Scrub, identity: Identity
) -> _Json:
    """What the page held when it printed, every string it supplied made safe to share.

    The page chose each of them, so any may carry a path; an image's source, an animation's
    name and a read's value are also quoted by the findings, which are scrubbed the same way.
    """
    return {
        "color_scheme": probe.color_scheme,
        "reduced_motion": probe.reduced_motion,
        "ready_state": probe.ready_state,
        "fonts_status": probe.fonts_status,
        "title": _title_json(probe.title, identity),
        "images": [
            {
                "src": clean(image.src),
                "has_src": image.has_src,
                "complete": image.complete,
                "loading": image.loading,
                "natural_width": image.natural_width,
            }
            for image in probe.images
        ],
        "animations": [
            {"name": clean(item.name), "target": clean(item.target)}
            for item in probe.animations
        ],
        "reads": {
            read.name: _read_values(read, first_match, clean) for read in probe.reads
        },
        "storage": {
            clean(key): None if value is None else clean(value)
            for key, value in probe.storage
        },
    }


def _title_json(title: str, identity: Identity) -> str:
    """The page's title, or `WITHHELD` when the privacy matcher's Title rules flag it.

    Chromium copies the title into the PDF's Title, which `read_pdf` withholds when flagged,
    so this copy is judged by the same helper and the same rules: a hostname or a
    home-rooted path flags it, the full name or the login never does.
    """
    return withhold_field(TITLE_FIELD, title, identity)


def _read_values(
    read: ReadValue, first_match: frozenset[str], clean: _Scrub
) -> list[object] | None:
    """A read's values, text scrubbed; a first-match read of nothing is null, not a list."""
    if read.name in first_match and not read.values:
        return None
    return [clean(value) if isinstance(value, str) else value for value in read.values]


def _pdf_json(report: PdfReport) -> _Json:
    """One PDF as the report writes it."""
    text = report.text
    return {
        "file": report.file,
        **_info_json(report.info),
        "fonts": [
            {
                "base_name": font.base_name,
                "subset": font.subset,
                "type": font.type,
                "encoding": font.encoding,
                "embedded": font.embedded,
                "unicode": font.unicode,
            }
            for font in report.fonts
        ],
        "images": _images_json(report),
        "links": [
            {
                "page": link.page,
                "uri": link.uri,
                "destination": link.destination,
                "rect": list(link.rect),
            }
            for link in report.links
        ],
        "text": None
        if text is None
        else {
            "file": report.text_file,
            "chars_per_page": [len(page) for page in text.pages],
            "cid_glyphs": text.cid_glyphs,
            "non_ascii_glyph_fonts": {
                glyph: list(fonts) for glyph, fonts in text.glyph_fonts.items()
            },
            # Already scrubbed by `read_pdf`, as the problems are.
            "log": dict(text.log),
        },
        "renders": {
            "dpi": report.dpi,
            "files": list(report.renders),
            "gray_levels": [
                [_level_json(share) for share in page] for page in report.gray_levels
            ],
            "colors": [
                [_color_json(share) for share in page] for page in report.colors
            ],
            "blank_below_last_ink": [page.blank_below_share for page in report.ink],
        },
        "privacy": [{"where": flag.where, "what": flag.what} for flag in report.flags],
        # Already scrubbed by `read_pdf`, with the home it was given.
        "problems": [
            {"severity": problem.severity, "what": problem.what}
            for problem in report.problems
        ],
    }


def _info_json(info: PdfInfo | None) -> _Json:
    """What pdfinfo said of the document; all null when it could not be read."""
    if info is None:
        return {"pages": None, "page_sizes": None, "metadata": None}
    return {
        "pages": info.pages,
        "page_sizes": [
            {"width_pt": size.width_pt, "height_pt": size.height_pt, "name": size.name}
            for size in info.page_sizes
        ],
        "metadata": {
            "title": info.title,
            "creator": info.creator,
            "producer": info.producer,
            "tagged": info.tagged,
            "pdf_version": info.pdf_version,
            "encrypted": info.encrypted,
            # Whether an author is named, never who: the privacy flags say whether it matters.
            "author": "present" if info.author_present else "absent",
            "dates": dict(info.dates),
        },
    }


def _images_json(report: PdfReport) -> _Json:
    """Every image, and the lowest and highest density of any, on either axis."""
    images = report.images
    return {
        "items": [
            {
                "page": image.page,
                "width": image.width,
                "height": image.height,
                "color": image.color,
                "bpc": image.bpc,
                "encoding": image.encoding,
                "x_ppi": image.x_ppi,
                "y_ppi": image.y_ppi,
                "size_bytes": image.size_bytes,
            }
            for image in images
        ],
        "min_ppi": min(
            (min(image.x_ppi, image.y_ppi) for image in images), default=None
        ),
        "max_ppi": max(
            (max(image.x_ppi, image.y_ppi) for image in images), default=None
        ),
    }


def _level_json(share: Share) -> _Json:
    """A gray level and the share of the page it covers."""
    return {"level": share.value[0], "share": share.share}


def _color_json(share: Share) -> _Json:
    """A color and the share of the page it covers."""
    return {"rgb": list(share.value), "share": share.share}
