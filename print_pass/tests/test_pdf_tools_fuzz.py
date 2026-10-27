"""Suite: the parsers of poppler's output, by property.

Arbitrary text either parses into well-formed records or raises PopplerError, never anything
else. The strategies weight plausible input (poppler's own header, rule and row layout around
arbitrary cells) so that many examples parse and the records, not just the refusals, are
checked; the same parsers by example are `test_pdf_tools.py`.
"""

from __future__ import annotations

import math
import sys
import unittest
from dataclasses import astuple
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pdf_tools  # noqa: E402 - the tool's private src/, via the insert above
from _print_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    FONT_HEADER,
    FONT_RULE,
    IMAGE_HEADER,
    IMAGE_RULE,
    font_line,
    image_line,
)
from pdf_tools import PopplerError  # noqa: E402 - as above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

_DATE_CLASSES = {"utc", "local offset (withheld)", "unparsed"}
_INFO_KEYS = (
    "Title",
    "Author",
    "Tagged",
    "Encrypted",
    "CreationDate",
    "Page    1 size",
)
_INFO_VALUES = (
    "yes",
    "no",
    "612 x 792 pts",
    "9.5 x 1e3 pts",
    "D:20000101000000Z",
    "D:20000101000000-04'00'",
)
_TOKENS = (
    "yes",
    "no",
    "0",
    "7",
    "0.180",
    "12.5K",
    "203B",
    "[inline]",
    "[none]",
    "image",
    "Type 1",
    "-",
)


def _lines(rows: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    """Text made of `rows`, one per line."""
    return st.lists(rows, max_size=6).map(
        lambda items: "".join(f"{item}\n" for item in items)
    )


def _either(*plausible: str) -> st.SearchStrategy[str]:
    """One of the `plausible` values, or arbitrary text."""
    return st.one_of(st.sampled_from(plausible), st.text())


#: Arbitrary text; and a Pages line followed by field-shaped lines and arbitrary ones, so
#: many examples parse and the typed fields, not just the refusals, are exercised.
_INFO_FIELD = st.builds(
    "{}: {}".format, st.sampled_from(_INFO_KEYS), _either(*_INFO_VALUES)
)
_INFO_TEXTS = st.one_of(
    st.text(),
    _lines(st.one_of(_INFO_FIELD, st.text())).map("Pages:           1\n".__add__),
)
#: A row of arbitrary text, or of whitespace-joined tokens a table cell could plausibly hold.
_ROW_TEXT = st.one_of(st.text(), st.lists(_either(*_TOKENS), max_size=17).map(" ".join))
#: Rows built in poppler's layout around arbitrary cells: every row of that shape, or mixed
#: with arbitrary rows, since one row that does not fit refuses the whole table.
_FONT_ROW = st.builds(
    font_line,
    st.text(min_size=1),
    _either("Type 1", "CID TrueType"),
    object_id=st.sampled_from(("     7  0", "[none]")),
)
_FONT_ROWS = st.one_of(_lines(_FONT_ROW), _lines(st.one_of(_FONT_ROW, _ROW_TEXT)))
_SIZES = ("0B", "203B", "12.5K", "999.9M")
_OBJECT_IDS = ("    9  0", "[inline]", "[none]")
_PPIS = ("64", "0.072", "1.5")
_IMAGE_ROW = st.builds(
    image_line,
    _either(*_SIZES),
    _either(*_OBJECT_IDS),
    _either(*_PPIS),
    _either(*_PPIS),
)
_PLAUSIBLE_IMAGE_ROW = st.builds(
    image_line,
    st.sampled_from(_SIZES),
    st.sampled_from(_OBJECT_IDS),
    st.sampled_from(_PPIS),
    st.sampled_from(_PPIS),
)
_IMAGE_ROWS = st.one_of(
    _lines(_PLAUSIBLE_IMAGE_ROW), _lines(st.one_of(_IMAGE_ROW, _ROW_TEXT))
)
#: Text shaped like a dashed rule, so a rule of the wrong column count is fuzzed too.
_RULE_SHAPED = st.from_regex(r"-+(?: -+)*", fullmatch=True)


def _tables(
    header: str, rule: str, rows: st.SearchStrategy[str]
) -> st.SearchStrategy[str]:
    """`rows` below the tool's own header and rule, or below arbitrary text in their place.

    The arbitrary heads put the header check itself under the fuzzer: plain text, the header
    over a rule-shaped line, or lines mixing header, rule and text. They are one table in
    four, since only a real head lets the rows parse and the records they make be checked.
    """
    real = st.just(header + rule)
    head_lines = st.one_of(
        st.sampled_from((header.rstrip("\n"), rule.rstrip("\n"))), st.text()
    )
    arbitrary = st.one_of(
        st.text(),
        st.builds("{}{}\n".format, st.just(header), _RULE_SHAPED),
        _lines(head_lines),
    )
    heads = st.sampled_from((real, real, real, arbitrary)).flatmap(lambda head: head)
    return st.tuples(heads, rows).map("".join)


_FONT_TABLES = _tables(FONT_HEADER, FONT_RULE, _FONT_ROWS)
_IMAGE_TABLES = _tables(IMAGE_HEADER, IMAGE_RULE, _IMAGE_ROWS)


class FuzzTests(unittest.TestCase):
    """Arbitrary text either parses into well-formed records or raises PopplerError, never else."""

    @given(_INFO_TEXTS)
    def test_fuzz_parse_pdfinfo(self, text: str) -> None:
        try:
            info = pdf_tools.parse_pdfinfo(text)
        except PopplerError:
            return
        self.assertIsInstance(info.pages, int)
        for size in info.page_sizes:
            self.assertTrue(
                math.isfinite(size.width_pt) and math.isfinite(size.height_pt)
            )
            self.assertIn(size.name, {None, *pdf_tools.NAMED_SIZES.values()})
        self.assertTrue(all(isinstance(value, str) for value in info.fields.values()))
        self.assertLessEqual(set(info.dates.values()), _DATE_CLASSES)
        self.assertIn(info.tagged, (True, False, None))

    @given(_FONT_TABLES)
    def test_fuzz_parse_pdffonts(self, text: str) -> None:
        try:
            fonts = pdf_tools.parse_pdffonts(text)
        except PopplerError:
            return
        for font in fonts:
            for cell in (font.name, font.base_name, font.type, font.encoding):
                self.assertTrue(cell and cell == cell.strip())
            self.assertTrue(font.name.endswith(font.base_name))
            for flag in (font.subset, font.embedded, font.unicode):
                self.assertIsInstance(flag, bool)

    @given(_IMAGE_TABLES)
    def test_fuzz_parse_pdfimages_list(self, text: str) -> None:
        try:
            images = pdf_tools.parse_pdfimages_list(text)
        except PopplerError:
            return
        for image in images:
            numbers = [value for value in astuple(image) if not isinstance(value, str)]
            self.assertEqual(len(numbers), 7)
            self.assertTrue(
                all(isinstance(number, int) and number >= 0 for number in numbers)
            )
            self.assertTrue(image.color and image.encoding)


if __name__ == "__main__":
    unittest.main()
