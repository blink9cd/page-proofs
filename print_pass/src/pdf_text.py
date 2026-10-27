"""Read a PDF's link annotations and metadata with pypdf, and its text layer with pdfminer.

Three readers, each over one file, each turning whatever pypdf or pdfminer raises into a
`PdfTextError` that names the file by its NAME only. A report quotes these messages, and
a report is meant to be safe to share: the directory a PDF was written into usually sits
under a home directory, so it never enters a message.

- `read_links`: every `/Link` annotation, page by page, with its URI or its internal
  destination and its rectangle. An explicit destination is spelled in PDF syntax
  (`[ 5 0 R /XYZ 0 792 0 ]`), because pypdf's `str()` of a page reference resolves it
  and prints the whole page dictionary, and its `repr()` carries a memory address; one
  that is neither array, string nor name is read by its class (`<DictionaryObject>`). A
  `/GoToR` or `/Launch` action's file is its URI, as `file:<spec>`.
- `read_metadata`: the Info dictionary as plain strings, and the raw XMP packet. The
  packet is read straight from the catalog rather than through pypdf's
  `xmp_metadata`, which PARSES it and raises on malformed XML: a broken packet can
  carry a name or a path as well as a sound one, and must still reach the matcher.
- `read_text`: pdfminer's text per page, the count of glyphs it could not map to text
  (`(cid:N)`), and which fonts draw each non-ASCII glyph. pdfminer puts a form
  XObject's glyphs in an `LTFigure`, outside the page's own text boxes, and at its
  default layout parameters leaves them there bare, so the last glyph of one line or
  run would run into the first of the next and hide a path or a hostname from the
  privacy matcher. Its defaults are kept but `all_texts`, which lays a figure's glyphs
  out in lines and boxes as a page's are: a figure's text, nested figures included, is
  its text boxes', each line ending in a newline, with a space between two runs apart
  on one. Those breaks count among the page's characters; the glyph inventory walks
  the whole layout tree, and the `(cid:N)` count reads the whole text. What pdfminer
  logs meanwhile, a warning or worse, is counted by its message template rather than
  printed: logging's last-resort handler would otherwise write each record to stderr
  (dozens of lines over one site's prints), burying the summary. A template holds no
  argument, which is where a message's values sit; pdfminer formats one message
  itself, quoting the open file, so the PDF's path in a template is replaced by its
  name.

Entry point assumptions: pypdf and pdfminer.six are importable (the `print` extra),
and so is the sibling `pdf_tools`, whose subset tag `base_font_name` strips; every
function takes a path to a PDF and reads nothing else.
"""

from __future__ import annotations

import io
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pypdf
from pdfminer.high_level import extract_pages
from pdfminer.layout import (
    LAParams,
    LTChar,
    LTContainer,
    LTFigure,
    LTItem,
    LTPage,
    LTTextContainer,
)
from pdfminer.psexceptions import PSException
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject, PdfObject, StreamObject

from pdf_tools import SUBSET_TAG

#: What a reader turns into a `PdfTextError`: each library's own base error, the file
#: system's, the lookup and conversion errors a malformed object tree raises from
#: inside either library (a missing `/Rect`, a rectangle of three numbers), and the
#: NotImplementedError pypdf raises for a stream filter it lacks, `/Crypt` with a
#: `/Name` among them: the Identity crypt filter an encrypted PDF puts on its
#: unencrypted metadata stream.
_READ_ERRORS: Final = (
    PyPdfError,
    PSException,
    OSError,
    KeyError,
    IndexError,
    TypeError,
    ValueError,
    NotImplementedError,
)

#: pdfminer's text for a glyph it cannot map to Unicode.
_CID_GLYPH: Final = re.compile(r"\(cid:\d+\)")

#: The last code point that is plain ASCII.
_ASCII_LAST: Final = 127

#: The annotation subtype that is a link.
_LINK_SUBTYPE: Final = "/Link"
#: The actions that open a file of the reader's: another PDF (`/GoToR`) or anything (`/Launch`).
_FILE_ACTIONS: Final = frozenset({"/GoToR", "/Launch"})

#: Serialized PDF syntax is bytes; latin-1 maps every byte to one character, so an
#: unusual byte in a destination can never fail the read.
_PDF_SYNTAX_ENCODING: Final = "latin-1"

#: An XMP packet is UTF-8 by default; a stray byte becomes U+FFFD rather than an error.
_XMP_ENCODING: Final = "utf-8"

#: The layout reader `read_text` walks: pdfminer's own, or a test's fake.
Extract = Callable[[Path], Iterable[LTPage]]

#: The logger every pdfminer module's own logger is a child of.
_PDFMINER_LOGGER: Final = "pdfminer"

#: The least a counted record is: what logging's last-resort handler prints, so a
#: pdfminer set to log more than that adds nothing to the count.
_LOG_FLOOR: Final = logging.WARNING


class PdfTextError(RuntimeError):
    """A PDF pypdf or pdfminer could not read; the message names the file, not its folder."""


@dataclass(frozen=True)
class Link:
    """One link annotation: its page (from 1), where it goes, and where it sits."""

    page: int
    uri: str | None
    destination: str | None
    rect: tuple[float, float, float, float]


@dataclass(frozen=True)
class TextLayer:
    """A PDF's text: one string per page, unmapped glyphs, non-ASCII glyph fonts, and log.

    `log` counts each message pdfminer logged reading the PDF, by its template.
    """

    pages: tuple[str, ...]
    cid_glyphs: int
    glyph_fonts: Mapping[str, tuple[str, ...]]
    log: Mapping[str, int]


class _LogCounter(logging.Handler):
    """Counts pdfminer's records by message template, the PDF's path named by its name."""

    def __init__(self, pdf: Path) -> None:
        """Count `_LOG_FLOOR` and above for a read of `pdf`."""
        super().__init__(_LOG_FLOOR)
        # The path as pdfminer opened it, and as a repr quotes it (a Windows path's
        # backslashes doubled), the longer first.
        self._paths = sorted({str(pdf), repr(str(pdf))[1:-1]}, key=len, reverse=True)
        self._name = pdf.name
        self.counts: Counter[str] = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        """Count `record` under its template; its arguments are never read."""
        template = str(record.msg)
        for path in self._paths:
            template = template.replace(path, self._name)
        self.counts[template] += 1


def _read_error(verb: str, pdf: Path, error: Exception) -> PdfTextError:
    """A `PdfTextError` for `error`, naming `pdf` by its name alone."""
    # An OSError's str() carries the full path it failed on; its strerror does not.
    reason = error.strerror if isinstance(error, OSError) else str(error)
    return PdfTextError(f"cannot read {verb} from {pdf.name}: {reason}")


def _resolved(value: PdfObject | None) -> PdfObject | None:
    """`value` with any indirect reference followed; None stays None."""
    return None if value is None else value.get_object()


def _as_text(value: PdfObject | None) -> str | None:
    """A PDF value as text: a string or name as itself, an array in PDF syntax.

    Anything else -- a dictionary, a number, a stream -- is read as `<ClassName>`: a
    dictionary's text would carry every value it holds, and none is a destination's name.
    """
    if value is None or isinstance(value, str | bytes):
        return None if value is None else str(value)
    if isinstance(value, ArrayObject):
        buffer = io.BytesIO()
        value.write_to_stream(buffer)
        return buffer.getvalue().decode(_PDF_SYNTAX_ENCODING)
    return f"<{type(value).__name__}>"


def _file_target(action: DictionaryObject) -> str | None:
    """A `/GoToR` or `/Launch` action's file, as a `file:` URL; None for any other action.

    Either opens a file of the reader's, so it is read where a URI is, and judged as one.
    A file specification is a string, or a dictionary whose `/UF` or else `/F` names it.
    """
    if _resolved(action.get("/S")) not in _FILE_ACTIONS:
        return None
    spec = _resolved(action.get("/F"))
    if isinstance(spec, DictionaryObject):
        spec = _resolved(spec.get("/UF", spec.get("/F")))
    return f"file:{spec}" if isinstance(spec, str) else None


def _rectangle(annotation: DictionaryObject) -> tuple[float, float, float, float]:
    """An annotation's `/Rect` as four floats; any other shape is a malformed link."""
    rect = annotation["/Rect"]
    if not isinstance(rect, ArrayObject):
        raise ValueError(f"a link's /Rect is a {type(rect).__name__}, not an array")
    # Unpacking raises ValueError on any count but four, which the reader reports.
    left, bottom, right, top = (float(number.get_object()) for number in rect)
    return (left, bottom, right, top)


def _link(page: int, annotation: DictionaryObject) -> Link:
    """The `Link` one `/Link` annotation describes."""
    action = _resolved(annotation.get("/A"))
    if not isinstance(action, DictionaryObject):
        action = DictionaryObject()
    destination = annotation.get("/Dest", action.get("/D"))
    uri = _as_text(_resolved(action.get("/URI")))
    return Link(
        page=page,
        uri=_file_target(action) if uri is None else uri,
        destination=_as_text(_resolved(destination)),
        rect=_rectangle(annotation),
    )


def _page_links(page: int, annotations: PdfObject | None) -> Iterator[Link]:
    """Every link among one page's `/Annots` entries, in the order the page lists them."""
    listed = _resolved(annotations)
    if not isinstance(listed, ArrayObject):
        return
    for entry in listed:
        annotation = _resolved(entry)
        if (
            isinstance(annotation, DictionaryObject)
            and _resolved(annotation.get("/Subtype")) == _LINK_SUBTYPE
        ):
            yield _link(page, annotation)


def read_links(pdf: Path) -> tuple[Link, ...]:
    """Every link annotation in `pdf`, page by page; raises `PdfTextError` if unreadable."""
    try:
        reader = pypdf.PdfReader(pdf)
        return tuple(
            link
            for number, page in enumerate(reader.pages, start=1)
            for link in _page_links(number, page.get("/Annots"))
        )
    except _READ_ERRORS as error:
        raise _read_error("links", pdf, error) from error


def _xmp_packet(reader: pypdf.PdfReader) -> str | None:
    """The catalog's raw XMP packet as text, or None when there is no metadata stream."""
    stream = _resolved(reader.root_object.get("/Metadata"))
    if not isinstance(stream, StreamObject):
        return None
    return stream.get_data().decode(_XMP_ENCODING, "replace")


def read_metadata(pdf: Path) -> tuple[Mapping[str, str], str | None]:
    """The Info dictionary of `pdf` (keys without their slash) and its raw XMP packet."""
    try:
        reader = pypdf.PdfReader(pdf)
        info = reader.metadata
        # Indexing, not iterating values: DocumentInformation's [] follows references.
        fields = (
            {}
            if info is None
            else {str(key).removeprefix("/"): str(info[key]) for key in info}
        )
        return fields, _xmp_packet(reader)
    except _READ_ERRORS as error:
        raise _read_error("metadata", pdf, error) from error


def _glyphs(item: LTItem) -> Iterator[LTChar]:
    """Every glyph in the layout tree under `item`, depth first."""
    if isinstance(item, LTChar):
        yield item
    elif isinstance(item, LTContainer):
        for child in item:
            yield from _glyphs(child)


def _page_text(page: LTPage) -> str:
    """One page's text, in pdfminer's layout order: each text container's and figure's."""
    return "".join(_item_text(item) for item in page)


def _item_text(item: LTItem) -> str:
    """The text one layout item adds to its page: a text container's, as pdfminer reads it.

    A figure's is the text of each item in it, so a figure nested in one is read too; a
    figure holding no text, an image's, adds nothing.
    """
    if isinstance(item, LTTextContainer):
        return item.get_text()
    if isinstance(item, LTFigure):
        return "".join(_item_text(child) for child in item)
    return ""


def _record_glyph_fonts(page: LTPage, fonts: dict[str, set[str]]) -> None:
    """Add each non-ASCII glyph on `page`, and the base name of its font, to `fonts`."""
    for glyph in _glyphs(page):
        text = glyph.get_text()
        if any(ord(character) > _ASCII_LAST for character in text):
            fonts.setdefault(text, set()).add(base_font_name(glyph.fontname))


def _layout_pages(pdf: Path) -> Iterator[LTPage]:
    """Each page of `pdf` as pdfminer lays it out, a form's glyphs laid out as a page's are."""
    return extract_pages(pdf, laparams=LAParams(all_texts=True))


def read_text(pdf: Path, *, extract: Extract = _layout_pages) -> TextLayer:
    """The text layer of `pdf` as pdfminer lays it out; raises `PdfTextError` if unreadable.

    pdfminer's records are counted by a handler that exists for this read alone: while
    one is attached, logging's last resort never fires, so none reaches stderr.
    """
    pages: list[str] = []
    fonts: dict[str, set[str]] = {}
    counter = _LogCounter(pdf)
    logger = logging.getLogger(_PDFMINER_LOGGER)
    logger.addHandler(counter)
    try:
        # extract_pages is lazy: a broken page raises while it is being iterated.
        for page in extract(pdf):
            pages.append(_page_text(page))
            _record_glyph_fonts(page, fonts)
    except _READ_ERRORS as error:
        raise _read_error("text", pdf, error) from error
    finally:
        logger.removeHandler(counter)
    return TextLayer(
        pages=tuple(pages),
        cid_glyphs=sum(len(_CID_GLYPH.findall(text)) for text in pages),
        glyph_fonts={glyph: tuple(sorted(fonts[glyph])) for glyph in sorted(fonts)},
        log={template: counter.counts[template] for template in sorted(counter.counts)},
    )


# fuzz-target
def base_font_name(name: str) -> str:
    """`name` without a leading six-capital `ABCDEF+` subset tag before more of a name.

    The tag is pdffonts' reader's, `SUBSET_TAG`, so the two never disagree on a name.
    """
    tag = SUBSET_TAG.match(name)
    return name if tag is None else name[tag.end() :]
