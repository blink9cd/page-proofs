"""Shared fixtures for pdf_text's suites: a temporary folder, and pypdf pages to draw on.

`test_pdf_text` covers the pdfminer reader and `test_pdf_text_pypdf` the pypdf readers,
and `test_pdf_privacy_content` reads a form's lines through the first; each writes its
synthetic PDFs through these helpers into a fresh temporary directory, so no PDF is ever
committed.

Entry point assumptions: imported by a suite that `unittest` discovery loaded with this
folder as its top-level directory, which puts this module on the path; it needs nothing
from the tool's `src/`.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Final

import pypdf
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DictionaryObject,
    NameObject,
    NumberObject,
)

#: A US Letter page, in points.
PAGE_WIDTH: Final = 612
PAGE_HEIGHT: Final = 792

#: The name a page's resources give the one form XObject `figure_pdf` draws.
_FORM_NAME: Final = "/Fm1"

#: How far below one line of a form's text `figure_pdf` starts the next, in points: more
#: than the 12-point font's height, so pdfminer reads two lines.
_LINE_STEP: Final = 20

#: The gap `figure_pdf` leaves between two runs on one line, in thousandths of the font
#: size: half an em, wider than the space pdfminer reads as a break between words and
#: narrower than the gap it reads as the end of a line (both measured, pdfminer.six
#: 20260107).
_RUN_GAP: Final = 500

#: One line of a form's text: one run, or several drawn one after another along it.
type Line = str | tuple[str, ...]


def blank_writer(pages: int = 1) -> pypdf.PdfWriter:
    """A writer holding `pages` blank Letter pages."""
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    return writer


def write_pdf(writer: pypdf.PdfWriter, path: Path) -> Path:
    """Write `writer` to `path` and hand the path back."""
    with path.open("wb") as sink:
        writer.write(sink)
    return path


def helvetica_resources() -> DictionaryObject:
    """Resources naming the standard Type1 Helvetica `/F1`, which needs no font file."""
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    return DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )


def _pdf_string(text: str) -> str:
    """`text` as a PDF literal string, its backslashes and parentheses escaped."""
    for special in ("\\", "(", ")"):
        text = text.replace(special, "\\" + special)
    return f"({text})"


def _shown(line: Line) -> str:
    """The operator that draws `line`: its runs, each after a gap, in one `TJ`."""
    runs = (line,) if isinstance(line, str) else line
    return "[" + f" -{_RUN_GAP} ".join(_pdf_string(run) for run in runs) + "] TJ"


def figure_pdf(path: Path, *lines: Line) -> Path:
    """One Letter page whose only text, `lines`, is drawn inside a form XObject.

    pdfminer lays a form's glyphs out in an `LTFigure`, never among the page's own text
    boxes. Each line is drawn below the last; a line of several runs draws them along it,
    each after a gap. Every run is printable ASCII.
    """
    writer = blank_writer()
    page = writer.pages[0]
    form = ContentStream(None, None)
    shown = f" 0 -{_LINE_STEP} Td ".join(_shown(line) for line in lines)
    form.set_data(f"BT /F1 12 Tf 72 720 Td {shown} ET".encode("ascii"))
    form[NameObject("/Type")] = NameObject("/XObject")
    form[NameObject("/Subtype")] = NameObject("/Form")
    form[NameObject("/BBox")] = ArrayObject(
        NumberObject(edge) for edge in (0, 0, PAGE_WIDTH, PAGE_HEIGHT)
    )
    form[NameObject("/Resources")] = helvetica_resources()
    # A stream must be an indirect object; pypdf makes one only through the writer's
    # `_add_object`, the call its own `replace_contents` makes for a content stream.
    forms = DictionaryObject({NameObject(_FORM_NAME): writer._add_object(form)})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): forms})
    content = ContentStream(None, None)
    content.set_data(f"q {_FORM_NAME} Do Q".encode("ascii"))
    page.replace_contents(content)
    return write_pdf(writer, path)


class TempDirCase(unittest.TestCase):
    """A case with a fresh temporary directory, removed afterwards."""

    def setUp(self) -> None:
        """Make the directory every PDF in the test is written into."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name)

    def assert_read_error(self, error: Exception, pdf: Path) -> None:
        """The error names the file, and never the directory it sits in."""
        self.assertIn(pdf.name, str(error))
        self.assertNotIn(str(pdf.parent), str(error))
