"""The report's reading half: one PDF read by every reader and two compared, safe to share.

`read_pdf` runs every reader over one PDF, in order -- poppler's inspectors, pypdf's links
and metadata and pdfminer's text (together, in one bounded child process), the renders,
their levels and ink, the privacy matcher -- and keeps going when one fails: a reader's own
error becomes a finding naming its step, and the other steps still run, so one broken
reader never costs the rest of the report. An encrypted PDF skips the pypdf and pdfminer
readers, with a warning. `compare_pdfs` renders two PDFs in color and compares them page by
page. Every render is judged first against `pdf_raster`'s pixel budget from the page sizes
pdfinfo read, and its pages are measured one at a time, so only what is measured of a page
outlives it.

What this module hands on is already safe to share, which is the report's whole promise:

- every problem text, and each message template pdfminer logged, passes through
  `print_report_scrub`'s `scrub`, since a library's message can echo a path or this
  machine's hostname;
- every path is `report_path`'s: relative to the out dir, or a bare name for a PDF outside it;
- a Title, Creator, Producer, link URI or link destination the privacy matcher flags, each
  judged alone, is replaced by `WITHHELD` (`print_report_withhold`), since the report prints
  those values and a flag's own promise is never to repeat one.

Entry point assumptions: the readers' own modules (`pdf_tools`, `pdf_raster`, `pdf_read`,
`pdf_text`, `pdf_privacy`) and the engine's `Problem`; the caller names the out dir, which
this module writes renders and text files into.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from itertools import zip_longest
from dataclasses import dataclass
from fractions import Fraction
from functools import partial
from pathlib import Path
from typing import Final

from pdf_privacy import Flag, Identity, privacy_flags
from pdf_raster import (
    DEFAULT_MIN_SHARE,
    InkExtent,
    PageDiff,
    Raster,
    RasterError,
    Share,
    check_render_budget,
    compare_rasters,
    dominant_shares,
    ink_extent,
    render_png,
    render_rasters,
)
from pdf_read import outcome, read_content
from pdf_text import Link, PdfTextError, TextLayer
from pdf_tools import (
    FontRow,
    ImageRow,
    PdfInfo,
    PopplerError,
    read_fonts,
    read_images,
    read_info,
)
from print_engine import Problem
from print_judge import finding, warning
from print_record import first_line
from print_report_output import SURROGATE_ERRORS
from print_report_scrub import scrub

# WITHHELD is re-exported: the report's readers name it as this module's.
from print_report_withhold import WITHHELD as WITHHELD
from print_report_withhold import withhold_info, withhold_link

#: Where under the out dir the page renders and the text layers go.
RENDERS_DIR: Final = "renders"
TEXT_DIR: Final = "text"
TEXT_SUFFIX: Final = ".txt"

#: The warning an encrypted PDF carries: its links, metadata and text go unread.
ENCRYPTED_WARNING: Final = "encrypted: text and links not read"

#: The errors a reader raises for a PDF it cannot read; anything else is a bug, and raised.
_READER_ERRORS: Final = (PopplerError, RasterError, PdfTextError)

#: Each step's name, as a failure's finding opens with it.
_INFO: Final = "info"
_FONTS: Final = "fonts"
_IMAGES: Final = "images"
_LINKS: Final = "links"
_METADATA: Final = "metadata"
_TEXT: Final = "text"
_PNG: Final = "png renders"
_GRAY: Final = "gray render"
_COLOR: Final = "color render"
_INK: Final = "ink extent"

#: Why a render step fails when pdfinfo read nothing: no page size to judge it by.
_NO_SIZES: Final = "no page sizes to budget the render by: the info step failed"

#: One gray page as measured: its levels, and its ink extent or why it has none.
type _GrayPage = tuple[tuple[Share, ...], InkExtent | RasterError]

#: A text layer's pages are written one after another with a form feed between, as
#: pdftotext writes them, so a reader can still tell where each page ends.
_PAGE_BREAK: Final = "\f"


@dataclass(frozen=True)
class PdfReport:
    """Everything the report says of one PDF, each value already safe to share.

    `file`, `text_file` and `renders` are `report_path`s. `info` and `links` have each
    printed value the privacy matcher flags, judged alone, replaced by `WITHHELD`;
    `info.fields` is never printed. `text.log`'s templates are scrubbed.
    """

    file: str
    dpi: int
    info: PdfInfo | None
    fonts: tuple[FontRow, ...]
    images: tuple[ImageRow, ...]
    links: tuple[Link, ...]
    text: TextLayer | None
    text_file: str | None
    renders: tuple[str, ...]
    gray_levels: tuple[tuple[Share, ...], ...]  # per page
    colors: tuple[tuple[Share, ...], ...]  # per page
    ink: tuple[InkExtent, ...]  # per page
    flags: tuple[Flag, ...]
    problems: tuple[Problem, ...]


@dataclass(frozen=True)
class _Content:
    """What pypdf and pdfminer read: none of it for an encrypted PDF."""

    links: tuple[Link, ...]
    metadata: Mapping[str, str] | None
    xmp: str | None
    text: TextLayer | None


class _Steps:
    """One PDF's problems so far, and the runner that turns a reader's failure into one."""

    def __init__(self) -> None:
        """Start with no problems."""
        self.problems: list[Problem] = []

    def run[T](self, step: str, read: Callable[[], T], failed: T) -> T:
        """`read()`, or `failed` with a finding naming `step` when the reader fails."""
        try:
            return read()
        except _READER_ERRORS as error:
            self.problems.append(finding(f"{step} failed: {first_line(error)}"))
            return failed


def report_path(path: Path, out_dir: Path) -> str:
    """`path` relative to `out_dir` with `/` separators; a path outside it, its name alone.

    Both are resolved first, so a relative out dir or a symlink in either still matches. A
    path outside is named by its name only: its folder may sit under a home directory.
    """
    resolved, root = path.resolve(), out_dir.resolve()
    if resolved.is_relative_to(root):
        return resolved.relative_to(root).as_posix()
    return path.name


def read_pdf(
    pdf: Path,
    out_dir: Path,
    *,
    dpi: int,
    timeout: float,
    identity: Identity,
    margin_inches: Fraction,
) -> PdfReport:
    """Read `pdf` with every reader, rendering into `out_dir`, and report it.

    A reader's own failure is a finding; the other steps still run. The text layer goes to
    `<out_dir>/text/<stem>.txt`, the page renders to `<out_dir>/renders/`. Each page's ink is
    measured against `margin_inches`, the margin `pdf` was printed with.

    Raises:
        OSError: the text layer's folder could not be made or its file written.
    """
    steps = _Steps()
    info = steps.run(_INFO, partial(read_info, pdf, timeout=timeout), None)
    encrypted = info is not None and info.encrypted
    if encrypted:
        steps.problems.append(warning(ENCRYPTED_WARNING))
    fonts: tuple[FontRow, ...] = steps.run(
        _FONTS, partial(read_fonts, pdf, timeout=timeout), ()
    )
    steps.problems.extend(_font_warnings(fonts))
    images: tuple[ImageRow, ...] = steps.run(
        _IMAGES, partial(read_images, pdf, timeout=timeout), ()
    )
    content = (
        _Content((), None, None, None)
        if encrypted
        else _read_content(pdf, timeout, steps)
    )
    text_file = _save_text(content.text, pdf, out_dir, steps)
    png = partial(_png, pdf, out_dir, info, dpi=dpi, timeout=timeout)
    renders: tuple[Path, ...] = steps.run(_PNG, png, ())
    grays: tuple[_GrayPage, ...] = steps.run(
        _GRAY, partial(_gray, pdf, info, dpi, timeout, margin_inches), ()
    )
    colors: tuple[tuple[Share, ...], ...] = steps.run(
        _COLOR, partial(_colors, pdf, info, dpi, timeout), ()
    )
    ink: tuple[InkExtent, ...] = steps.run(_INK, partial(_ink, grays), ())
    flags = privacy_flags(
        identity,
        info=_privacy_info(content.metadata, info),
        xmp=content.xmp,
        links=content.links,
        text_pages=() if content.text is None else content.text.pages,
    )
    problems = (
        *steps.problems,
        *(finding(f"{flag.where}: {flag.what}") for flag in flags),
    )
    return PdfReport(
        file=report_path(pdf, out_dir),
        dpi=dpi,
        info=withhold_info(info, identity),
        fonts=fonts,
        images=images,
        links=tuple(withhold_link(link, identity) for link in content.links),
        text=_scrubbed_log(content.text, out_dir, identity),
        text_file=text_file,
        renders=tuple(report_path(path, out_dir) for path in renders),
        gray_levels=tuple(levels for levels, _ in grays),
        colors=colors,
        ink=ink,
        flags=flags,
        problems=tuple(_scrubbed(problem, out_dir, identity) for problem in problems),
    )


def compare_pdfs(
    a: Path, b: Path, *, dpi: int, timeout: float, out_dir: Path
) -> dict[str, object]:
    """Render `a` and `b` in color at `dpi` and compare them page by page.

    Both PDFs' pages are judged against the render budget before either is rendered, then
    rendered and compared a page of each at a time. Pages are compared as far as both go; a
    page-count mismatch alone makes the two differ. Two pages of different sizes differ,
    their pixels uncounted.

    Raises:
        RasterError: either PDF could not be sized or rendered, or holds a page past the
            budget, named by its file name: nothing could be compared.
    """
    count_a, count_b = _sized_pages(a, dpi, timeout), _sized_pages(b, dpi, timeout)
    color = partial(render_rasters, gray=False, dpi=dpi, timeout=timeout)
    rendered = zip_longest(color(a, pages=count_a), color(b, pages=count_b))
    page_count, diffs = [0, 0], []
    for page, (one, other) in enumerate(rendered, 1):
        page_count[0] += one is not None
        page_count[1] += other is not None
        if one is not None and other is not None:
            diffs.append((page, _compare_page(page, one, other, dpi)))
    return {
        "a": report_path(a, out_dir),
        "b": report_path(b, out_dir),
        "identical": page_count[0] == page_count[1]
        and all(diff is not None and diff.identical for _, diff in diffs),
        "page_count": page_count,
        "pages": [_page_json(page, diff) for page, diff in diffs],
    }


def _read_content(pdf: Path, timeout: float, steps: _Steps) -> _Content:
    """The links and metadata pypdf reads, and pdfminer's text: each failure a finding.

    All three read in one bounded child process (`pdf_read`), so a PDF that crashes or
    outlasts either library is a finding here, never the end of the run; each reader's
    outcome is then its own step, so a reader that answered keeps its answer.
    """
    read = read_content(pdf, timeout=timeout)
    links: tuple[Link, ...] = steps.run(_LINKS, partial(outcome, read.links), ())
    metadata = steps.run(_METADATA, partial(outcome, read.metadata), None)
    text = steps.run(_TEXT, partial(outcome, read.text), None)
    if text is not None and text.cid_glyphs:
        steps.problems.append(
            warning(f"unmapped glyphs in the text layer: {text.cid_glyphs} (cid:N)")
        )
    info, xmp = (None, None) if metadata is None else metadata
    return _Content(links, info, xmp, text)


def _font_warnings(fonts: Sequence[FontRow]) -> Iterator[Problem]:
    """A warning for each font not embedded, and each with no ToUnicode map."""
    for font in fonts:
        if not font.embedded:
            yield warning(f"font not embedded: {font.base_name}")
        if not font.unicode:
            yield warning(f"font without ToUnicode: {font.base_name}")


def _save_text(
    text: TextLayer | None, pdf: Path, out_dir: Path, steps: _Steps
) -> str | None:
    """Write `text` to `<out_dir>/text/<stem>.txt` and return its report path.

    A file already there is refused, never overwritten: two PDFs of one name, read from two
    folders, would otherwise leave the second's text under both.
    """
    if text is None:
        return None
    path = out_dir / TEXT_DIR / f"{pdf.stem}{TEXT_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    write = partial(_write_new, path, _PAGE_BREAK.join(text.pages), out_dir)
    return steps.run(_TEXT, write, None)


def _write_new(path: Path, text: str, out_dir: Path) -> str:
    """Write `text` to `path`, which must not exist yet, and return its report path.

    A lone surrogate, which UTF-8 cannot encode, is written as its escape: a backslash, `u`
    and four hex digits.
    """
    try:
        with path.open(
            "x", encoding="utf-8", errors=SURROGATE_ERRORS, newline="\n"
        ) as stream:
            stream.write(text)
    except FileExistsError as error:
        # Named by folder and file name only, like every message the report carries.
        raise PdfTextError(f"{TEXT_DIR} already holds {path.name}") from error
    return report_path(path, out_dir)


def _page_count(info: PdfInfo | None, dpi: int) -> int:
    """The pages pdfinfo sized, once every one is within the render budget at `dpi`.

    Raises:
        RasterError: the info step failed, so nothing can be judged, or a page is past it.
    """
    if info is None:
        raise RasterError(_NO_SIZES)
    sizes = [(size.width_pt, size.height_pt) for size in info.page_sizes]
    check_render_budget(sizes, dpi)
    return len(sizes)


def _sized_pages(pdf: Path, dpi: int, timeout: float) -> int:
    """`_page_count` of `pdf` as pdfinfo reads it; a failure names the PDF by its name."""
    try:
        return _page_count(read_info(pdf, timeout=timeout), dpi)
    except (PopplerError, RasterError) as error:
        raise RasterError(f"{pdf.name}: {first_line(error)}") from error


def _png(
    pdf: Path, out_dir: Path, info: PdfInfo | None, *, dpi: int, timeout: float
) -> tuple[Path, ...]:
    """`render_png` of `pdf` into the renders folder, once its pages are within budget."""
    _page_count(info, dpi)
    renders = out_dir / RENDERS_DIR
    return render_png(pdf, renders, pdf.stem, dpi=dpi, timeout=timeout)


def _gray(
    pdf: Path, info: PdfInfo | None, dpi: int, timeout: float, margin: Fraction
) -> tuple[_GrayPage, ...]:
    """Each page rendered gray and measured as it comes: its levels, and its ink.

    `map` drops each page once it is measured, before the next is rendered.
    """
    pages = render_rasters(
        pdf, gray=True, dpi=dpi, timeout=timeout, pages=_page_count(info, dpi)
    )
    return tuple(map(partial(_gray_page, dpi, margin), pages))


def _colors(
    pdf: Path, info: PdfInfo | None, dpi: int, timeout: float
) -> tuple[tuple[Share, ...], ...]:
    """Each page rendered in color and measured as it comes: its colors."""
    pages = render_rasters(
        pdf, gray=False, dpi=dpi, timeout=timeout, pages=_page_count(info, dpi)
    )
    return tuple(map(_shares, pages))


def _gray_page(dpi: int, margin: Fraction, page: Raster) -> _GrayPage:
    """One gray page's levels, and its ink extent or the error a too-short page raises.

    The error is kept without its traceback, whose frames would hold the page's pixels.
    """
    try:
        ink: InkExtent | RasterError = ink_extent(page, dpi, margin_inches=margin)
    except RasterError as error:
        ink = error.with_traceback(None)
    return _shares(page), ink


def _ink(grays: Sequence[_GrayPage]) -> tuple[InkExtent, ...]:
    """Each gray page's ink extent: the first page too short for its margins fails them all."""
    extents: list[InkExtent] = []
    for _, ink in grays:
        if isinstance(ink, RasterError):
            raise ink
        extents.append(ink)
    return tuple(extents)


def _shares(page: Raster) -> tuple[Share, ...]:
    """One page's dominant levels or colors, at the 1% floor.

    The floor is kept on purpose: these measure fills (a page's paper, a dark background),
    so anti-aliased text falling below it is intended.
    """
    return dominant_shares(page, DEFAULT_MIN_SHARE)


def _privacy_info(
    metadata: Mapping[str, str] | None, info: PdfInfo | None
) -> Mapping[str, str]:
    """The Info fields the privacy matcher searches.

    pypdf's Info dictionary when it was read; otherwise -- an encrypted PDF, or a failed
    metadata read -- every field pdfinfo printed; with neither, none.
    """
    if metadata is not None:
        return metadata
    return {} if info is None else info.fields


def _scrubbed(problem: Problem, out_dir: Path, identity: Identity) -> Problem:
    """`problem` with its text scrubbed."""
    return dataclasses.replace(
        problem, what=scrub(problem.what, out_dir=out_dir, identity=identity)
    )


def _scrubbed_log(
    text: TextLayer | None, out_dir: Path, identity: Identity
) -> TextLayer | None:
    """`text` with each template of its log scrubbed; two that scrub alike count as one."""
    if text is None:
        return None
    counts: Counter[str] = Counter()
    for template, count in text.log.items():
        counts[scrub(template, out_dir=out_dir, identity=identity)] += count
    return dataclasses.replace(
        text, log={template: counts[template] for template in sorted(counts)}
    )


def _compare_page(page: int, one: Raster, other: Raster, dpi: int) -> PageDiff | None:
    """Page `page`'s comparison; None when the two differ in size, which no count describes."""
    if (one.width, one.height) != (other.width, other.height):
        return None
    return compare_rasters(page, one, other, dpi)


def _page_json(page: int, diff: PageDiff | None) -> dict[str, object]:
    """One page of a comparison as the report writes it: pages of two sizes, uncounted."""
    if diff is None:
        return {
            "page": page,
            "identical": False,
            "differing_pixels": None,
            "bbox_pt": None,
        }
    return {
        "page": page,
        "identical": diff.identical,
        "differing_pixels": diff.differing_pixels,
        "bbox_pt": None if diff.bbox_pt is None else list(diff.bbox_pt),
    }
