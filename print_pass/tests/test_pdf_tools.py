"""Suite: the parsers of poppler's output, by example.

The parsers are fed captured poppler output (`_print_fixtures`) and rows built in poppler's own
layout, including the two a positional reader gets wrong: a name wider than its column, and a
name padded by bytes rather than characters. The same parsers by property are
`test_pdf_tools_fuzz.py`; running the readers themselves, through fake executables, is
`test_pdf_tools_run.py`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pdf_tools  # noqa: E402 - the tool's private src/, via the insert above
from _print_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    FONT_HEADER,
    FONT_RULE,
    FONTS_TEXT,
    IMAGE_HEADER,
    IMAGE_ROW,
    IMAGE_RULE,
    IMAGES_TEXT,
    INFO_TEXT,
    font_line,
    image_line,
)
from pdf_tools import FontRow, ImageRow, PageSize, PopplerError  # noqa: E402 - as above

#: Characters `str.splitlines` ends a line at and poppler does not: it prints them raw inside
#: a value (measured in a pdfinfo title and a pdffonts name).
_LINE_SEPARATOR = chr(0x2028)
_FORM_FEED = chr(0x0C)


def info_with(old: str, new: str) -> str:
    """The captured pdfinfo text with the line starting `old` replaced by `new` (or dropped)."""
    lines = [new if line.startswith(old) else line for line in INFO_TEXT.splitlines()]
    return "\n".join(line for line in lines if line) + "\n"


class ParsePdfinfoTests(unittest.TestCase):
    """`pdfinfo -rawdates` text: sizes, flags, and a date's class, never its value."""

    def test_the_captured_chromium_output_parses_exactly(self) -> None:
        fields = {
            "Title": "Format Probe",
            "Creator": "Chromium",
            "Producer": "Skia/PDF m153",
            "CreationDate": "D:20000101000000+00'00'",
            "ModDate": "D:20000101000000+00'00'",
            "Custom Metadata": "no",
            "Metadata Stream": "no",
            "Tagged": "yes",
            "UserProperties": "no",
            "Suspects": "no",
            "Form": "none",
            "JavaScript": "no",
            "Pages": "1",
            "Encrypted": "no",
            "Page    1 size": "612 x 792 pts (letter)",
            "Page    1 rot": "0",
            "File size": "29273 bytes",
            "Optimized": "no",
            "PDF version": "1.4",
        }
        expected = pdf_tools.PdfInfo(
            pages=1,
            page_sizes=(PageSize(612.0, 792.0, "Letter"),),
            fields=fields,
            title="Format Probe",
            creator="Chromium",
            producer="Skia/PDF m153",
            author_present=False,
            tagged=True,
            pdf_version="1.4",
            encrypted=False,
            dates={"CreationDate": "utc", "ModDate": "utc"},
        )
        self.assertEqual(pdf_tools.parse_pdfinfo(INFO_TEXT), expected)

    def date_class(self, raw: str) -> str:
        """The class `parse_pdfinfo` gives a CreationDate of `raw`."""
        text = info_with("CreationDate:", f"CreationDate:    {raw}")
        return pdf_tools.parse_pdfinfo(text).dates["CreationDate"]

    def test_a_date_at_zero_offset_either_sign_or_z_is_utc(self) -> None:
        # Measured: macOS's Quartz PDFContext, the path Safari prints through, writes
        # `Z00'00'`: a Z and then its zero offset.
        for raw in (
            "D:20000101000000+00'00'",
            "D:20000101000000-00'00'",
            "D:20000101000000Z",
            "D:20000101000000Z00'00'",
            "D:20000101000000Z00'00",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self.date_class(raw), "utc")

    def test_any_other_offset_is_withheld(self) -> None:
        for raw in ("D:20000101000000-04'00'", "D:20000101000000+05'30"):
            with self.subTest(raw=raw):
                self.assertEqual(self.date_class(raw), "local offset (withheld)")

    def test_a_date_that_does_not_parse_is_unparsed(self) -> None:
        # No offset states no zone, so it cannot be told to be UTC: it is not a known form.
        for raw in ("yesterday", "D:2000", "D:20000101000000"):
            with self.subTest(raw=raw):
                self.assertEqual(self.date_class(raw), "unparsed")

    def test_an_author_with_a_value_is_present_and_an_empty_one_is_not(self) -> None:
        named = pdf_tools.parse_pdfinfo(INFO_TEXT + "Author:          Someone Fake\n")
        empty = pdf_tools.parse_pdfinfo(INFO_TEXT + "Author:          \n")
        self.assertTrue(named.author_present)
        self.assertFalse(empty.author_present)
        self.assertEqual(empty.fields["Author"], "")

    def page_size(self, value: str) -> PageSize:
        """The one page size `parse_pdfinfo` reads from a size line of `value`."""
        text = info_with("Page    1 size:", f"Page    1 size:  {value}")
        (size,) = pdf_tools.parse_pdfinfo(text).page_sizes
        return size

    def test_a4_letter_on_its_side_and_an_odd_size(self) -> None:
        cases = {
            "595 x 842 pts (A4)": PageSize(595.0, 842.0, "A4"),
            "792 x 612 pts (letter)": PageSize(792.0, 612.0, "Letter"),
            "500.5 x 300.25 pts": PageSize(500.5, 300.25, None),
        }
        for printed, expected in cases.items():
            with self.subTest(printed):
                self.assertEqual(self.page_size(printed), expected)

    def test_a_size_is_named_after_rounding(self) -> None:
        self.assertEqual(self.page_size("595.276 x 841.89 pts (A4)").name, "A4")

    def test_every_page_of_a_multi_page_listing_is_read_in_order(self) -> None:
        text = info_with("Pages:", "Pages:           2")
        info = pdf_tools.parse_pdfinfo(text + "Page    2 size:  842 x 595 pts (A4)\n")
        self.assertEqual(info.pages, 2)
        self.assertEqual([size.name for size in info.page_sizes], ["Letter", "A4"])

    def test_the_single_page_form_plain_pdfinfo_prints_is_read(self) -> None:
        text = info_with("Page    1 size:", "Page size:       612 x 792 pts (letter)")
        info = pdf_tools.parse_pdfinfo(text)
        self.assertEqual(info.page_sizes, (PageSize(612.0, 792.0, "Letter"),))

    def test_tagged_absent_is_unknown_and_no_is_false(self) -> None:
        self.assertIsNone(pdf_tools.parse_pdfinfo(info_with("Tagged:", "")).tagged)
        untagged = info_with("Tagged:", "Tagged:          no")
        self.assertFalse(pdf_tools.parse_pdfinfo(untagged).tagged)

    def test_an_encrypted_file_with_its_permissions_is_encrypted(self) -> None:
        line = "Encrypted:       yes (print:yes copy:no change:no addNotes:no algorithm:AES)"
        self.assertTrue(
            pdf_tools.parse_pdfinfo(info_with("Encrypted:", line)).encrypted
        )

    def test_absent_metadata_reads_as_none(self) -> None:
        info = pdf_tools.parse_pdfinfo("Pages:           3\n")
        metadata = (info.title, info.creator, info.producer, info.pdf_version)
        self.assertEqual(metadata, (None, None, None, None))
        self.assertEqual(
            (info.page_sizes, dict(info.dates), info.encrypted), ((), {}, False)
        )

    def test_a_line_that_is_no_field_continues_the_one_before_it(self) -> None:
        # Every printed line must reach the privacy matcher, even one poppler never prints.
        info = pdf_tools.parse_pdfinfo(INFO_TEXT + "file:///nowhere\n")
        self.assertEqual(info.fields["PDF version"], "1.4\nfile:///nowhere")

    def test_a_line_separator_inside_a_value_starts_no_field(self) -> None:
        # Measured: pdfinfo prints a title's U+2028 raw. Only a newline ends one of its
        # lines, so the `Author: x` after the separator stays in the title.
        title = f"Format{_LINE_SEPARATOR}Author: x"
        info = pdf_tools.parse_pdfinfo(info_with("Title:", f"Title:           {title}"))
        self.assertFalse(info.author_present)
        self.assertNotIn("Author", info.fields)
        self.assertEqual(info.title, title)

    def test_a_repeated_field_keeps_both_values(self) -> None:
        info = pdf_tools.parse_pdfinfo(INFO_TEXT + "Title:           Second\n")
        self.assertEqual(info.fields["Title"], "Format Probe\nSecond")

    def test_malformed_text_raises(self) -> None:
        size = "Page    1 size:"
        # Each case names the fragment its message must carry, so none passes by failing early.
        cases = {
            "no Pages line": info_with("Pages:", ""),
            "Pages is not a whole number": info_with("Pages:", "Pages:    one"),
            "not a whole number: '999": info_with("Pages:", "Pages: " + "9" * 30),
            "is not `W x H pts`": info_with(size, f"{size}  wide x tall pts"),
            "Tagged is neither yes nor no": info_with("Tagged:", "Tagged:   maybe"),
            "no field before any field": "  stray\n" + INFO_TEXT,
        }
        for fragment, text in cases.items():
            with self.subTest(fragment), self.assertRaises(PopplerError) as caught:
                pdf_tools.parse_pdfinfo(text)
            self.assertIn(fragment, str(caught.exception))


class ParsePdffontsTests(unittest.TestCase):
    """`pdffonts` rows, located from the dashed rule and re-anchored on each row."""

    def test_the_captured_chromium_output_parses_exactly(self) -> None:
        expected = tuple(
            FontRow(name, base, True, "CID TrueType", "Identity-H", True, True)
            for name, base in (
                ("AAAAAA+Times-Roman", "Times-Roman"),
                ("BAAAAA+TimesNewRomanPSMT", "TimesNewRomanPSMT"),
                ("EAAAAA+Menlo-Regular", "Menlo-Regular"),
            )
        )
        self.assertEqual(pdf_tools.parse_pdffonts(FONTS_TEXT), expected)

    def only_font(self, line: str) -> FontRow:
        """The single row parsed from a table holding just `line`."""
        (row,) = pdf_tools.parse_pdffonts(FONT_HEADER + FONT_RULE + line)
        return row

    def test_a_name_with_spaces_parses(self) -> None:
        name = "ABCDEF+Some Font Name"
        row = self.only_font(font_line(name, sub="yes"))
        expected = FontRow(
            name, "Some Font Name", True, "Type 1", "Standard", False, False
        )
        self.assertEqual(row, expected)

    def test_a_plain_name_is_no_subset_and_no_answers_are_false(self) -> None:
        row = self.only_font(font_line("Helvetica", emb="no", uni="no"))
        expected = FontRow(
            "Helvetica", "Helvetica", False, "Type 1", "Standard", False, False
        )
        self.assertEqual(row, expected)

    def test_a_name_wider_than_its_column_still_parses(self) -> None:
        # Measured: poppler lets a long name overflow `%-36s`, pushing every column right.
        name = "QWERTY+AnExtremelyLongPostScriptFontName With Spaces-BoldItalic"
        row = self.only_font(font_line(name, kind="CID Type 0C (OT)", sub="yes"))
        self.assertEqual(
            (row.name, row.base_name, row.type), (name, name[7:], "CID Type 0C (OT)")
        )

    def test_a_multibyte_name_padded_by_bytes_still_parses(self) -> None:
        # Measured: the padding counts bytes, so in characters the later columns shift LEFT.
        name = "ABCDEF+Café Sans—Pro"
        row = self.only_font(font_line(name, sub="yes"))
        self.assertEqual(
            (row.name, row.base_name, row.type), (name, name[7:], "Type 1")
        )

    def test_a_font_that_is_no_object_of_its_own_still_parses(self) -> None:
        # Measured: a font dictionary written directly in the resources has no object
        # number, and pdffonts prints one `[none]` where `num gen` stand.
        row = self.only_font(font_line("Helvetica", object_id="[none]"))
        expected = FontRow(
            "Helvetica", "Helvetica", False, "Type 1", "Standard", False, False
        )
        self.assertEqual(row, expected)

    def test_a_name_holding_a_form_feed_and_a_line_separator_still_parses(self) -> None:
        # Measured: pdffonts prints both raw inside a name. Only a newline ends a row, so
        # the row stays whole rather than breaking into two that fit nothing.
        name = f"ABCDEF+Foo{_FORM_FEED}Bar{_LINE_SEPARATOR}Baz"
        row = self.only_font(font_line(name, sub="yes"))
        self.assertEqual(
            (row.name, row.base_name, row.type), (name, name[7:], "Type 1")
        )

    def test_a_nameless_font_and_a_bare_tag_keep_their_names(self) -> None:
        nameless = self.only_font(font_line("[none]", kind="Type 3"))
        self.assertEqual(nameless.base_name, "[none]")
        self.assertEqual(self.only_font(font_line("ABCDEF+")).base_name, "ABCDEF+")

    def test_an_empty_table_is_no_fonts(self) -> None:
        self.assertEqual(pdf_tools.parse_pdffonts(FONT_HEADER + FONT_RULE), ())

    def test_tables_that_do_not_fit_raise(self) -> None:
        no_table = "no table headed"
        six_columns = (
            FONT_RULE.rstrip("\n").rsplit(" ", 1)[0] + "\n"
        )  # object ID's gone
        cases = [
            (no_table, ""),
            (no_table, FONT_HEADER.replace("encoding", "charset") + FONT_RULE),
            (no_table, FONT_HEADER + FONT_RULE.replace("-", "=")),
            ("a rule of 6 columns", FONT_HEADER + six_columns),
        ]
        for fragment, text in cases:
            with self.subTest(text=text), self.assertRaises(PopplerError) as caught:
                pdf_tools.parse_pdffonts(text)
            self.assertIn(fragment, str(caught.exception))

    def test_rows_that_do_not_fit_raise(self) -> None:
        good = font_line("Helvetica")
        misaligned, no_id = "columns do not line up", "object ID is neither"
        # (the fragment the message must carry, the row): none may pass by failing early.
        cases = [
            ("does not fit its table", "Helvetica yes no\n"),
            ("uni is neither", good.replace(" no  no  no ", " no  no  maybe")),
            (no_id, good.replace("      7  0", "      x  0")),
            # pdffonts prints `[none]` alone: an inline image's token is no font's ID.
            (no_id, font_line("Helvetica", object_id="[inline]")),
            (no_id, font_line("Helvetica", object_id="7[none]")),
            ("an empty column", f"{'Helvetica':<36}  Standard no no no 7 0\n"),
            (misaligned, "Helvetica Standard no no no 7 0\n"),  # shifted past the name
            # A type wider than its column moves the encoding alone: the gap lands in a word.
            (misaligned, font_line("Helvetica", kind="Type 1" + " " * 12)),
        ]
        for fragment, row in cases:
            with self.subTest(row=row), self.assertRaises(PopplerError) as caught:
                pdf_tools.parse_pdffonts(FONT_HEADER + FONT_RULE + row)
            self.assertIn(fragment, str(caught.exception))


class ParsePdfimagesListTests(unittest.TestCase):
    """`pdfimages -list` rows, split on whitespace below one unbroken dashed rule."""

    captured = ImageRow(1, 64, 32, "icc", 8, "image", 64, 64, 203)

    def only_image(self, line: str) -> ImageRow:
        """The single row parsed from a table holding just `line`."""
        (row,) = pdf_tools.parse_pdfimages_list(IMAGE_HEADER + IMAGE_RULE + line)
        return row

    def test_the_captured_chromium_output_parses_exactly(self) -> None:
        self.assertEqual(pdf_tools.parse_pdfimages_list(IMAGES_TEXT), (self.captured,))

    def test_each_size_suffix_is_a_power_of_1024(self) -> None:
        mebi = 1024 * 1024
        cases = {
            "203B": 203,
            "12.5K": 12_800,
            "2930K": 3_000_320,
            "17.9M": round(17.9 * mebi),
        }
        for printed, expected in cases.items():
            with self.subTest(printed):
                self.assertEqual(
                    self.only_image(image_line(printed)).size_bytes, expected
                )

    def test_an_inline_image_has_no_object_number_and_still_parses(self) -> None:
        # Measured: pdfimages prints one `[inline]` token where `num gen` stand.
        self.assertEqual(
            self.only_image(image_line(object_id="[inline]  ")), self.captured
        )

    def test_an_image_that_is_no_object_of_its_own_still_parses(self) -> None:
        # pdfimages prints `[none]`, like pdffonts, for an image with no object number.
        self.assertEqual(
            self.only_image(image_line(object_id="[none]    ")), self.captured
        )

    def test_a_density_printed_as_a_decimal_rounds_to_whole_ppi(self) -> None:
        # Measured: pdfimages prints a density under 1 ppi as `%5.3f` (`0.072`, `0.540`),
        # so one image drawn huge must not refuse the whole PDF. 0.180 rounds to 0, which
        # is under 1 ppi; 1.5 rounds to 2. Each column is read on its own.
        cases = {("0.180", "1.5"): (0, 2), ("1.5", "0.180"): (2, 0)}
        for (x_ppi, y_ppi), expected in cases.items():
            with self.subTest(x_ppi=x_ppi, y_ppi=y_ppi):
                row = self.only_image(image_line(x_ppi=x_ppi, y_ppi=y_ppi))
                self.assertEqual((row.x_ppi, row.y_ppi), expected)

    def test_an_empty_table_is_no_images(self) -> None:
        self.assertEqual(pdf_tools.parse_pdfimages_list(IMAGE_HEADER + IMAGE_RULE), ())

    def test_rows_that_do_not_fit_raise(self) -> None:
        no_fit, no_size = "does not fit its table", "not a number and unit"
        # (the fragment the message must carry, the row): none may pass by failing early.
        cases = [
            (no_fit, IMAGE_ROW.replace(" 3.3%", "")),  # a column short
            (no_fit, image_line(object_id="    9   ")),  # one object token, not inline
            (no_fit, image_line(object_id="    x  0")),  # an object number not a number
            ("width is not", IMAGE_ROW.replace("  64    32", "  wide  32")),
            ("x-ppi is not a number", image_line(x_ppi="0.1.8")),
            ("y-ppi is not a number", image_line(y_ppi="-1")),
            (no_size, image_line(size="203")),
            (no_size, image_line(size="1.2G")),
            ("interp is neither", IMAGE_ROW.replace("image  no ", "image  maybe ")),
        ]
        for fragment, row in cases:
            with self.subTest(row=row), self.assertRaises(PopplerError) as caught:
                pdf_tools.parse_pdfimages_list(IMAGE_HEADER + IMAGE_RULE + row)
            self.assertIn(fragment, str(caught.exception))

    def test_text_with_no_table_raises(self) -> None:
        with self.assertRaises(PopplerError):
            pdf_tools.parse_pdfimages_list("page num\n")


if __name__ == "__main__":
    unittest.main()
