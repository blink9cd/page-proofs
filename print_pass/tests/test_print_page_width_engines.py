"""Suite: a `page_width` state printed by each engine, at the one margin it is sent and records.

Each engine computes a state's margin once and hands that one value both to its print
builder and to the `MadePdf` it records, so the margin a PDF was printed with and the margin
its ink is measured against cannot differ. Chromium is run against the fake Playwright, whose
`page.pdf` keeps every option it is passed; Firefox against the scripted WebDriver session,
which keeps every Print body. The arithmetic and the builders themselves are the sibling
`test_print_page_width.py`'s; a make run handing each `MadePdf`'s margin to the reader is
`test_print_modes.py`'s.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from typing import Final, cast

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_print  # noqa: E402 - the tool's private src/, via the insert above
import print_engine  # noqa: E402 - as above
import print_report  # noqa: E402 - as above
from _gecko_state_fixtures import StateCase  # noqa: E402 - beside this module
from _pdf_privacy_fixtures import IDENTITY  # noqa: E402 - as above
from _print_engine_fixtures import StateTestCase, state  # noqa: E402 - as above
from _print_report_fixtures import (  # noqa: E402 - as above
    DPI,
    TIMEOUT,
    fake_readers,
    gray_raster,
)
from pdf_raster import ink_extent  # noqa: E402 - the tool's private src/
from print_record import MadePdf  # noqa: E402 - as above

#: CSS pixels per inch, and centimeters per inch, spelled out rather than read.
_PX_PER_INCH: Final = 96
_CM_PER_INCH: Final = Fraction(254, 100)

#: An odd width, so its margin is a half pixel: (816 - 673) / 2 = 71.5 px.
_WIDTH: Final = 673
_MARGIN: Final = Fraction(143, 2 * _PX_PER_INCH)


def _sent_px(length: object) -> Fraction:
    """A `px` CSS length Chromium was sent, read back as exact inches."""
    text = str(length)
    if not text.endswith("px"):
        raise AssertionError(f"not a px length: {text}")
    return Fraction(text.removesuffix("px")) / _PX_PER_INCH


class ChromiumPageWidthTests(StateTestCase):
    """The state's margin reaches `page.pdf`, the PDF it records, and the state's record."""

    def test_the_margin_sent_is_the_margin_recorded(self) -> None:
        run, record = self.run_one(state(page_width=_WIDTH))
        (page,) = run.pages()
        (options,) = page.pdf_calls
        sides = cast(dict[str, str], options["margin"])
        self.assertEqual(set(sides.values()), {"71.5px"})
        (made,) = record.pdfs
        self.assertEqual(made.margin_inches, _MARGIN)
        self.assertEqual(
            {_sent_px(side) for side in sides.values()}, {made.margin_inches}
        )

    def test_the_record_names_the_width_and_the_margin(self) -> None:
        _, record = self.run_one(state(page_width=_WIDTH))
        self.assertEqual((record.page_width, record.margin_inches), (_WIDTH, _MARGIN))

    def test_a_state_without_one_is_printed_at_the_dialogs_margin(self) -> None:
        run, record = self.run_one()
        (page,) = run.pages()
        sides = cast(dict[str, str], page.pdf_calls[0]["margin"])
        self.assertEqual(set(sides.values()), {print_engine.MARGIN})
        self.assertIsNone(record.page_width)
        self.assertEqual(record.margin_inches, print_engine.MARGIN_INCHES)
        self.assertEqual(record.pdfs[0].margin_inches, print_engine.MARGIN_INCHES)


class GeckoPageWidthTests(StateCase):
    """The state's margin reaches the WebDriver Print body, its PDF and its record."""

    def test_the_margin_sent_is_the_margin_recorded(self) -> None:
        session = self.session()
        record = self.run_state(session, self.job(state(page_width=_WIDTH)))
        (body,) = session.print_bodies
        sides = cast(dict[str, float], body["margin"])
        (made,) = record.pdfs
        self.assertEqual(made.margin_inches, _MARGIN)
        self.assertEqual(
            set(sides.values()), {float(made.margin_inches * _CM_PER_INCH)}
        )
        self.assertEqual(set(sides), {"top", "bottom", "left", "right"})

    def test_the_record_names_the_width_and_the_margin(self) -> None:
        record = self.run_state(job=self.job(state(page_width=_WIDTH)))
        self.assertEqual((record.page_width, record.margin_inches), (_WIDTH, _MARGIN))

    def test_a_state_without_one_is_printed_at_the_dialogs_margin(self) -> None:
        session = self.session()
        record = self.run_state(session)
        sides = cast(dict[str, float], session.print_bodies[0]["margin"])
        self.assertEqual(set(sides.values()), {gecko_print.MARGIN_CM})
        self.assertEqual(record.margin_inches, gecko_print.MARGIN_INCHES)
        self.assertEqual(record.pdfs[0].margin_inches, gecko_print.MARGIN_INCHES)


class InkMarginTests(StateTestCase):
    """The PDF a page-width state records is measured against its own, derived margin."""

    def test_the_ink_is_measured_against_the_derived_margin(self) -> None:
        _, record = self.run_one(state(page_width=_WIDTH))
        (made,) = record.pdfs
        self.assertIsInstance(made, MadePdf)
        with tempfile.TemporaryDirectory() as folder, fake_readers():
            reading = print_report.read_pdf(
                made.path,
                Path(folder),
                dpi=DPI,
                timeout=TIMEOUT,
                identity=IDENTITY,
                margin_inches=made.margin_inches,
            )
        derived = ink_extent(gray_raster(), DPI, margin_inches=_MARGIN)
        default = ink_extent(
            gray_raster(), DPI, margin_inches=print_engine.MARGIN_INCHES
        )
        (measured,) = reading.ink
        self.assertEqual(measured, derived)
        # The derived margin is not the default one, so this case could tell them apart.
        self.assertNotEqual(derived.blank_below_share, default.blank_below_share)


if __name__ == "__main__":
    unittest.main()
