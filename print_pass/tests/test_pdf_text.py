"""Suite: pdf_text reads the text layer of PDFs built here, and strips font subset tags.

Every PDF is synthetic, written by pypdf into a temporary directory, so no PDF is ever
committed. The text layer's non-ASCII glyph inventory needs a PDF drawn with a real
embedded font, which only a browser print makes; here it is reached through
`read_text`'s `extract` seam instead, fed a layout tree of REAL pdfminer objects, and
the live suite checks the same field against a real print. The pypdf readers (links and
metadata) have their own suite, `test_pdf_text_pypdf`.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the
tool's aggregator; the bootstrap below names the tool's private `src/`, and discovery's
top-level directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import contextlib
import io
import logging
import secrets
import string
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pdfminer.layout import (
    LTChar,
    LTFigure,
    LTPage,
    LTRect,
    LTTextBoxHorizontal,
    LTTextLineHorizontal,
)
from pdfminer.pdfcolor import PREDEFINED_COLORSPACE
from pdfminer.pdffont import PDFType1Font
from pdfminer.pdfinterp import PDFGraphicState, PDFResourceManager
from pdfminer.pdfparser import PDFSyntaxError
from pdfminer.psparser import LIT
from pypdf.constants import UserAccessPermissions
from pypdf.generic import ContentStream, DictionaryObject, NameObject

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
import pdf_text  # noqa: E402 - the tool's private src/, via the insert above
from _pdf_text_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    PAGE_HEIGHT,
    PAGE_WIDTH,
    TempDirCase,
    blank_writer,
    figure_pdf,
    helvetica_resources,
    write_pdf,
)

# Derandomized so the in-suite run is reproducible; no deadline, because a per-example
# wall clock measures machine load rather than correctness.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: A font's glyph box, in thousandths of the size; pdfminer refuses a font without one.
_FONT_BBOX: Final = (0, -200, 1000, 800)
_FONT_SIZE: Final = 12
_GLYPH_WIDTH: Final = 6

#: Non-ASCII glyphs, built from code points so no escape has to survive an editor.
_E_ACUTE: Final = chr(0xE9)
_EM_DASH: Final = chr(0x2014)
_HEART: Final = chr(0x2665)

#: A subset-tagged font name, as a real embedded subset carries it.
_SUBSET_TIMES: Final = "ABCDEF+Times-Roman"

#: A subset tag's shape, spelled without a regex so the property's oracle shares nothing
#: with the implementation: six ASCII capitals, then a plus sign, at the very start, and
#: then a name, since a tag is a prefix of one (ISO 32000-1, 9.6.4).
_SUBSET_LETTERS: Final = 6
_SUBSET_MARK: Final = "+"

#: A capital `str.isupper` accepts but a subset tag never carries.
_NON_ASCII_CAPITAL: Final = chr(0xC9)

#: What pdfminer logs, by its template, reading a font descriptor with no FontBBox
#: (measured, pdfminer.six 20260107): the warning a browser's print floods stderr with.
_NO_BBOX_TEMPLATE: Final = "Could not get FontBBox from font descriptor because %r cannot be parsed as 4 floats"

#: The logger every pdfminer module's own logger is a child of.
_PDFMINER: Final = "pdfminer"

#: What a repr doubles, spelled once so no escape has to be read twice.
_BACKSLASH: Final = "\\"

#: Six letters that are usually ASCII capitals: a lower-case letter or a non-ASCII
#: capital makes a near miss.
_TAG_LETTERS: Final = st.text(
    alphabet=string.ascii_uppercase + "a" + _NON_ASCII_CAPITAL,
    min_size=_SUBSET_LETTERS,
    max_size=_SUBSET_LETTERS,
)

#: Font names, a fair share of them opening tag-shaped: the six letters, then a plus, some
#: other mark, or nothing, then any name or none; and a share with a tag's shape after
#: other text, where no tag is. Plain text alone would almost never reach a tag.
_FONT_NAMES: Final = st.one_of(
    st.text(),
    st.builds(
        lambda letters, mark, rest: letters + mark + rest,
        _TAG_LETTERS,
        st.sampled_from((_SUBSET_MARK, "-", "")),
        st.text(),
    ),
    st.builds(
        lambda before, letters, rest: before + letters + _SUBSET_MARK + rest,
        st.text(min_size=1),
        _TAG_LETTERS,
        st.text(),
    ),
)


def _is_subset_tagged(name: str) -> bool:
    """Whether `name` opens with six ASCII capitals and a plus before more of a name."""
    letters = name[:_SUBSET_LETTERS]
    return (
        len(letters) == _SUBSET_LETTERS
        and all(letter in string.ascii_uppercase for letter in letters)
        and name[_SUBSET_LETTERS : _SUBSET_LETTERS + 1] == _SUBSET_MARK
        and len(name) > _SUBSET_LETTERS + 1
    )


def _hello_pdf(path: Path) -> Path:
    """One Letter page drawing `Hello` in the standard Type1 Helvetica."""
    writer = blank_writer()
    page = writer.pages[0]
    page[NameObject("/Resources")] = helvetica_resources()
    content = ContentStream(None, None)
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Hello) Tj ET")
    # replace_contents registers the stream as an indirect object, as a content stream
    # must be; a stream placed in the page dictionary directly would be written inline.
    page.replace_contents(content)
    return write_pdf(writer, path)


def _no_bbox_pdf(path: Path) -> Path:
    """One Letter page drawing `Hello` in a Type1 font whose descriptor has no FontBBox."""
    writer = blank_writer()
    page = writer.pages[0]
    descriptor = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/FontDescriptor"),
            NameObject("/FontName"): NameObject("/FixtureSans"),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/FixtureSans"),
            NameObject("/FontDescriptor"): descriptor,
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = ContentStream(None, None)
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Hello) Tj ET")
    page.replace_contents(content)
    return write_pdf(writer, path)


def _locked_pdf(path: Path) -> Path:
    """A blank page encrypted to allow printing alone, with no password to open it.

    pdfminer reads it anyway, and warns that it should not, quoting the open file itself.
    The password that would lift the restriction is random: nothing here needs it back.
    """
    writer = blank_writer()
    writer.encrypt(
        user_password="",
        owner_password=secrets.token_hex(),
        permissions_flag=UserAccessPermissions.PRINT,
        algorithm="RC4-128",
    )
    return write_pdf(writer, path)


def _char(text: str, font_name: str) -> LTChar:
    """A REAL pdfminer glyph drawing `text` in a Type1 font named `font_name`."""
    font = PDFType1Font(
        PDFResourceManager(),
        {
            "BaseFont": LIT(font_name),
            "FontDescriptor": {
                "FontName": LIT(font_name),
                "FontBBox": list(_FONT_BBOX),
            },
        },
    )
    return LTChar(
        (1, 0, 0, 1, 0, 0),
        font,
        _FONT_SIZE,
        1,
        0,
        text,
        _GLYPH_WIDTH,
        font.char_disp(0),
        PREDEFINED_COLORSPACE["DeviceGray"],
        PDFGraphicState(),
    )


def _text_box(*chars: LTChar) -> LTTextBoxHorizontal:
    """A text box holding one line of `chars`."""
    line = LTTextLineHorizontal(0.1)
    for char in chars:
        line.add(char)
    box = LTTextBoxHorizontal()
    box.add(line)
    return box


def _figure(*items: LTTextBoxHorizontal | LTFigure | LTRect) -> LTFigure:
    """A page-sized figure, as pdfminer lays out a form XObject, holding `items` in order."""
    figure = LTFigure("Fm1", (0, 0, PAGE_WIDTH, PAGE_HEIGHT), (1, 0, 0, 1, 0, 0))
    for item in items:
        figure.add(item)
    return figure


def _page(number: int, *items: LTTextBoxHorizontal | LTFigure | LTRect) -> LTPage:
    """A Letter-sized layout page holding `items` in order."""
    page = LTPage(number, (0, 0, PAGE_WIDTH, PAGE_HEIGHT))
    for item in items:
        page.add(item)
    return page


class ReadTextTests(TempDirCase):
    """pdfminer's text per page, its `(cid:N)` count, and the non-ASCII glyph fonts."""

    def test_a_real_pdf_reads_its_text(self) -> None:
        layer = pdf_text.read_text(_hello_pdf(self.folder / "hello.pdf"))
        self.assertEqual(layer, pdf_text.TextLayer(("Hello\n",), 0, {}, {}))

    def test_a_corrupt_file_is_a_read_error(self) -> None:
        pdf = self.folder / "corrupt.pdf"
        pdf.write_bytes(b"%PDF-1.4 and then nothing")
        with self.assertRaises(pdf_text.PdfTextError) as caught:
            pdf_text.read_text(pdf)
        self.assert_read_error(caught.exception, pdf)

    def test_an_error_partway_through_the_pages_is_a_read_error(self) -> None:
        def extract(pdf: Path) -> Iterator[LTPage]:
            yield _page(1, _text_box(_char("a", "Helvetica")))
            raise PDFSyntaxError(f"broken after page 1 of {pdf.name}")

        pdf = self.folder / "partial.pdf"
        with self.assertRaises(pdf_text.PdfTextError) as caught:
            pdf_text.read_text(pdf, extract=extract)
        self.assert_read_error(caught.exception, pdf)

    def test_non_ascii_glyphs_are_mapped_to_the_fonts_drawing_them(self) -> None:
        pages = [
            _page(
                1,
                _text_box(_char(_E_ACUTE, _SUBSET_TIMES), _char("a", "Helvetica")),
                _text_box(_char(_E_ACUTE, "Courier")),
            ),
            _page(2, _text_box(_char(_EM_DASH, "GHIJKL+Georgia"))),
        ]
        layer = pdf_text.read_text(
            self.folder / "fake.pdf", extract=lambda _pdf: iter(pages)
        )
        self.assertEqual(
            layer.glyph_fonts,
            {_E_ACUTE: ("Courier", "Times-Roman"), _EM_DASH: ("Georgia",)},
        )
        self.assertEqual(layer.pages, (_E_ACUTE + "a" + _E_ACUTE, _EM_DASH))

    def test_a_figure_s_text_boxes_are_page_text_nested_figures_included(self) -> None:
        # pdfminer lays a form XObject's text out inside an LTFigure rather than among the
        # page's own text boxes, so a walk of those alone would miss it, and which font
        # drew it. A figure nested in the figure is read too, each box as pdfminer gives it.
        inner = _figure(_text_box(_char("b", "Helvetica")))
        figure = _figure(
            _text_box(_char("a", "Helvetica")),
            inner,
            _text_box(_char(_HEART, _SUBSET_TIMES)),
        )
        page = _page(1, figure, _text_box(_char("x", "Helvetica")))
        layer = pdf_text.read_text(
            self.folder / "fake.pdf", extract=lambda _pdf: iter([page])
        )
        self.assertEqual(layer.pages, ("ab" + _HEART + "x",))
        self.assertEqual(layer.glyph_fonts, {_HEART: ("Times-Roman",)})

    def test_a_form_s_lines_and_runs_read_apart_as_the_page_s_own_do(self) -> None:
        # Left at pdfminer's default, a figure holds its bare glyphs, and the last of one
        # line or run ran into the first of the next, where no matcher sees a break.
        pdf = figure_pdf(self.folder / "form.pdf", "first line", ("left", "right"))
        layer = pdf_text.read_text(pdf)
        self.assertEqual(layer.pages, ("first line\nleft right\n",))

    def test_a_figure_without_text_and_a_shape_add_nothing(self) -> None:
        # An image is drawn inside a figure too, and has no text to add; nor has a rule.
        rule = LTRect(1, (0, 0, PAGE_WIDTH, 1))
        page = _page(1, _text_box(_char("x", "Helvetica")), _figure(rule), rule)
        layer = pdf_text.read_text(
            self.folder / "fake.pdf", extract=lambda _pdf: iter([page])
        )
        self.assertEqual(layer.pages, ("x",))

    def test_unmapped_glyphs_are_counted(self) -> None:
        page = _page(
            1, _text_box(_char("(cid:3)", "Helvetica"), _char("(cid:17)", "Helvetica"))
        )
        layer = pdf_text.read_text(
            self.folder / "fake.pdf", extract=lambda _pdf: iter([page])
        )
        self.assertEqual(layer.cid_glyphs, 2)
        self.assertEqual(layer.glyph_fonts, {})


class ReadTextLogTests(TempDirCase):
    """pdfminer's log while it reads one PDF: counted by template, printed nowhere."""

    def read_quietly(
        self, pdf: Path, *, extract: pdf_text.Extract | None = None
    ) -> tuple[pdf_text.TextLayer, str]:
        """`read_text` of `pdf`, and what it wrote to stderr meanwhile."""
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            layer = (
                pdf_text.read_text(pdf)
                if extract is None
                else pdf_text.read_text(pdf, extract=extract)
            )
        return layer, stderr.getvalue()

    def test_a_record_pdfminer_logs_is_counted_and_printed_nowhere(self) -> None:
        # Without a handler of the read's own, logging's last resort prints it to stderr.
        layer, stderr = self.read_quietly(_no_bbox_pdf(self.folder / "nobbox.pdf"))
        self.assertEqual(stderr, "")
        self.assertEqual(layer.log, {_NO_BBOX_TEMPLATE: 1})

    def test_each_template_is_counted_whatever_its_arguments(self) -> None:
        # The template holds no value the PDF supplied; its arguments do, and are dropped.
        def extract(pdf: Path) -> Iterator[LTPage]:
            fonts = logging.getLogger(_PDFMINER + ".pdffont")
            for value in ("first value", "second value"):
                fonts.warning("font %r is odd", value)
            logging.getLogger(_PDFMINER + ".pdfpage").error("MediaBox missing")
            yield _page(1)

        layer, stderr = self.read_quietly(self.folder / "fake.pdf", extract=extract)
        self.assertEqual(
            list(layer.log.items()), [("MediaBox missing", 1), ("font %r is odd", 2)]
        )
        self.assertEqual(stderr, "")

    def test_a_record_below_a_warning_is_not_counted(self) -> None:
        # logging's last resort prints a warning and above; nothing less was ever flooding.
        logger = logging.getLogger(_PDFMINER)
        self.addCleanup(logger.setLevel, logger.level)
        logger.setLevel(logging.DEBUG)

        def extract(pdf: Path) -> Iterator[LTPage]:
            logger.debug("parsing %s", "a stream")
            logger.info("read %d objects", 3)
            logger.warning("a warning")
            yield _page(1)

        layer, _ = self.read_quietly(self.folder / "fake.pdf", extract=extract)
        self.assertEqual(layer.log, {"a warning": 1})

    def test_the_pdf_s_folder_never_enters_a_template(self) -> None:
        # pdfminer formats the open file's repr into this warning itself, so the path it
        # was opened by is in the template: it is named by its name alone, as in an error.
        pdf = _locked_pdf(self.folder / "locked.pdf")
        layer, _ = self.read_quietly(pdf)
        (template,) = layer.log
        self.assertIn(f"name='{pdf.name}'", template)
        self.assertNotIn(str(self.folder), template)

    def test_a_path_whose_repr_differs_from_it_never_enters_a_template(self) -> None:
        # The warning quotes the file's repr, which doubles each backslash, so the path as
        # opened is not in it: a Windows path is full of backslashes, and a POSIX folder's
        # name may hold one.
        folder = self.folder / ("back" + _BACKSLASH + "slash")
        folder.mkdir(parents=True)
        pdf = _locked_pdf(folder / "locked.pdf")
        layer, _ = self.read_quietly(pdf)
        (template,) = layer.log
        self.assertIn(f"name='{pdf.name}'", template)
        self.assertNotIn(str(self.folder), template)

    def test_the_handler_is_gone_after_a_read_whether_it_ends_or_fails(self) -> None:
        logger = logging.getLogger(_PDFMINER)
        before = list(logger.handlers)

        def extract(pdf: Path) -> Iterator[LTPage]:
            logger.warning("about to fail")
            yield _page(1)
            raise PDFSyntaxError("broken after page 1")

        self.read_quietly(_hello_pdf(self.folder / "hello.pdf"))
        self.assertEqual(logger.handlers, before)
        with self.assertRaises(pdf_text.PdfTextError):
            self.read_quietly(self.folder / "fake.pdf", extract=extract)
        self.assertEqual(logger.handlers, before)


class BaseFontNameTests(unittest.TestCase):
    """Only a six-capital `ABCDEF+` subset tag is stripped."""

    def test_a_subset_tag_is_stripped(self) -> None:
        self.assertEqual(pdf_text.base_font_name("ABCDEF+Times-Roman"), "Times-Roman")

    def test_a_plain_name_is_kept(self) -> None:
        self.assertEqual(pdf_text.base_font_name("Helvetica"), "Helvetica")

    def test_a_lower_case_prefix_is_not_a_subset_tag(self) -> None:
        self.assertEqual(pdf_text.base_font_name("abcdef+Lower"), "abcdef+Lower")

    def test_a_five_letter_prefix_is_not_a_subset_tag(self) -> None:
        self.assertEqual(pdf_text.base_font_name("ABCDE+Short"), "ABCDE+Short")

    def test_a_tag_with_no_name_after_it_is_kept(self) -> None:
        # pdffonts' reader keeps it too: one pattern, pdf_tools', says what a tag is.
        self.assertEqual(pdf_text.base_font_name("ABCDEF+"), "ABCDEF+")

    def test_a_tag_shape_after_other_text_is_not_a_subset_tag(self) -> None:
        name = "Times-ABCDEF+Roman"
        self.assertEqual(pdf_text.base_font_name(name), name)

    def test_a_non_ascii_capital_is_not_a_subset_tag(self) -> None:
        name = "ABCD" + _NON_ASCII_CAPITAL + "F+Serif"
        self.assertEqual(pdf_text.base_font_name(name), name)

    @given(_FONT_NAMES)
    def test_fuzz_base_font_name(self, name: str) -> None:
        # Re-derived independently: the tag is the first seven characters when they are
        # six ASCII capitals and a plus with more after them, and nothing is removed
        # otherwise.
        tagged = _is_subset_tagged(name)
        expected = name[_SUBSET_LETTERS + 1 :] if tagged else name
        self.assertEqual(pdf_text.base_font_name(name), expected)


if __name__ == "__main__":  # pragma: no cover - the discovery runner is the entry point
    unittest.main()
