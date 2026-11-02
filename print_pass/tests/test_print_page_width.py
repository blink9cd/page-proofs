"""Suite: a state's `page_width`, as the margin each engine is sent and the report records.

A state may name the width, in CSS px, its page is laid out at; the tool turns it into one
margin, the same on all four sides of US Letter, `(816 - page_width) / 2` px. This suite
holds that arithmetic, each engine's print builder taking the margin (Chromium's
`page.pdf` options as a CSS length, Firefox's WebDriver Print body in centimeters), and the
report's two fields for it. A width in odd pixels gives a half-pixel margin, which both
builders must carry exactly as far as their unit allows. The engines handing a state's
margin to their builders is the sibling `test_print_page_width_engines.py`'s.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from typing import Final, cast

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_print  # noqa: E402 - the tool's private src/, via the insert above
import print_playwright  # noqa: E402 - as above
import print_report  # noqa: E402 - as above
import print_targets  # noqa: E402 - as above
from _pdf_privacy_fixtures import IDENTITY  # noqa: E402 - beside this module
from _print_engine_fixtures import state  # noqa: E402 - as above
from _print_report_fixtures import (  # noqa: E402 - as above
    PDF_NAME,
    comparison,
    pdf_report,
    state_record,
    targets,
)

#: CSS pixels per inch, and centimeters per inch, spelled out rather than read from the
#: subject, so a change to either fails here.
_PX_PER_INCH: Final = 96
_CM_PER_INCH: Final = Fraction(254, 100)

#: A width giving a whole 72 px (0.75 in) margin, and an odd one giving 71.5 px.
_EVEN_WIDTH: Final = 672
_ODD_WIDTH: Final = 673
_EVEN_MARGIN: Final = Fraction(72, _PX_PER_INCH)
_ODD_MARGIN: Final = Fraction(143, 2 * _PX_PER_INCH)

#: A margin no engine's default is, standing in for "whatever the engine would use".
_DEFAULT: Final = Fraction(1, 3)


class MarginMathTests(unittest.TestCase):
    """`state_margin_inches`: half of what the width leaves of Letter, or the default."""

    def test_an_even_width_gives_a_whole_pixel_margin(self) -> None:
        margin = print_targets.state_margin_inches(
            state(page_width=_EVEN_WIDTH), _DEFAULT
        )
        self.assertEqual(margin, _EVEN_MARGIN)

    def test_an_odd_width_gives_a_half_pixel_margin_exactly(self) -> None:
        margin = print_targets.state_margin_inches(
            state(page_width=_ODD_WIDTH), _DEFAULT
        )
        self.assertEqual(margin, _ODD_MARGIN)
        self.assertEqual(margin * _PX_PER_INCH, Fraction(143, 2))

    def test_the_margins_and_the_width_fill_letter(self) -> None:
        for width in (320, 675, 815):
            with self.subTest(page_width=width):
                margin = print_targets.state_margin_inches(
                    state(page_width=width), _DEFAULT
                )
                self.assertEqual(width + 2 * margin * _PX_PER_INCH, 816)

    def test_no_page_width_is_the_engines_default(self) -> None:
        self.assertIs(print_targets.state_margin_inches(state(), _DEFAULT), _DEFAULT)


class ChromiumBuilderTests(unittest.TestCase):
    """`print_options` takes the margin and sends it, on every side, as a CSS length."""

    def margins(self, margin: Fraction) -> object:
        """The `margin` option `print_options` builds for `margin`."""
        options = print_playwright.print_options(
            backgrounds=False, header_footer=False, margin_inches=margin
        )
        return options["margin"]

    def every_side(self, length: str) -> dict[str, str]:
        """`length` on all four sides, as Playwright's `margin` names them."""
        return dict.fromkeys(("top", "right", "bottom", "left"), length)

    def test_the_default_is_still_the_dialogs_1cm(self) -> None:
        options = print_playwright.print_options(backgrounds=False, header_footer=False)
        self.assertEqual(options["margin"], self.every_side("1cm"))
        self.assertEqual(
            self.margins(print_playwright.MARGIN_INCHES), options["margin"]
        )

    def test_a_page_width_margin_is_sent_in_whole_pixels(self) -> None:
        self.assertEqual(self.margins(_EVEN_MARGIN), self.every_side("72px"))

    def test_a_half_pixel_margin_is_sent_exactly(self) -> None:
        self.assertEqual(self.margins(_ODD_MARGIN), self.every_side("71.5px"))

    def test_a_margin_no_decimal_holds_is_sent_in_inches(self) -> None:
        # A seventh of an inch is no terminating decimal in px or cm.
        seventh = Fraction(1, 7)
        self.assertEqual(self.margins(seventh), self.every_side(f"{1 / 7}in"))

    def test_each_length_reads_back_as_the_margin_it_was_given(self) -> None:
        for margin, length in ((_ODD_MARGIN, "71.5px"), (Fraction(50, 127), "1cm")):
            with self.subTest(length=length):
                self.assertEqual(print_playwright.css_margin(margin), length)


class GeckoBuilderTests(unittest.TestCase):
    """`print_parameters` takes the margin and sends it, on every side, in centimeters."""

    def margins(self, margin: Fraction) -> dict[str, float]:
        """The body's `margin` table for `margin`."""
        body = gecko_print.print_parameters(backgrounds=False, margin_inches=margin)
        return cast(dict[str, float], body["margin"])

    def test_the_default_is_still_the_dialogs_half_inch(self) -> None:
        body = gecko_print.print_parameters(backgrounds=False)
        self.assertEqual(set(cast(dict[str, float], body["margin"]).values()), {1.27})
        self.assertEqual(self.margins(gecko_print.MARGIN_INCHES), body["margin"])

    def test_a_page_width_margin_is_sent_in_centimeters(self) -> None:
        sides = self.margins(_EVEN_MARGIN)
        self.assertEqual(set(sides), {"top", "bottom", "left", "right"})
        self.assertEqual(set(sides.values()), {1.905})

    def test_a_half_pixel_margin_is_sent_as_near_as_a_float_holds(self) -> None:
        sent = float(_ODD_MARGIN * _CM_PER_INCH)
        self.assertEqual(set(self.margins(_ODD_MARGIN).values()), {sent})


class ReportFieldTests(unittest.TestCase):
    """Each state's entry names its page width and the margin it was printed with."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)

    def entry(self, page_width: int | None, margin: Fraction) -> dict[str, object]:
        """The report's entry for the fixed record, at `page_width` and `margin`."""
        record = dataclasses.replace(
            state_record(self.out), page_width=page_width, margin_inches=margin
        )
        report = print_report.make_report(
            [record],
            {self.out / "pdfs" / PDF_NAME: pdf_report()},
            [comparison()],
            out_dir=self.out,
            targets=targets(),
            engine="shell",
            header_footer=False,
            identity=IDENTITY,
        )
        states = cast(list[dict[str, object]], report["states"])
        return states[0]

    def test_a_page_width_state_records_its_width_and_margin(self) -> None:
        entry = self.entry(_ODD_WIDTH, _ODD_MARGIN)
        self.assertEqual((entry["page_width"], entry["margin_px"]), (_ODD_WIDTH, 71.5))

    def test_a_state_without_one_records_null_and_the_engines_margin(self) -> None:
        entry = self.entry(None, print_playwright.MARGIN_INCHES)
        self.assertEqual((entry["page_width"], entry["margin_px"]), (None, 37.7953))

    def test_the_run_level_entry_still_names_the_engines_default(self) -> None:
        record = dataclasses.replace(
            state_record(self.out), page_width=_EVEN_WIDTH, margin_inches=_EVEN_MARGIN
        )
        report = print_report.make_report(
            [record],
            {},
            [],
            out_dir=self.out,
            targets=targets(),
            engine="shell",
            header_footer=False,
            identity=IDENTITY,
        )
        self.assertEqual(cast(dict[str, object], report["print"])["margin"], "1cm")


if __name__ == "__main__":
    unittest.main()
