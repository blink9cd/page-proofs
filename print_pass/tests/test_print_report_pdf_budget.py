"""Suite: read_pdf and compare_pdfs render within a pixel budget, one page at a time.

A PDF's page size is its maker's to choose, and a raster's memory follows it: one 200-inch
page is near a gigabyte in color at 96 dpi. So each page's pixels at the run's resolution are
judged against `MAX_RASTER_PIXELS` from the sizes pdfinfo read, before pdftoppm is run at
all, and the pages are rendered and measured one at a time, so only what is measured of a
page outlives it. Every reader is replaced through `fake_readers`, as in the sibling
`test_print_report_pdf`, whose file has no room left for these cases.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import dataclasses
import gc
import sys
import tempfile
import unittest
import weakref
from collections.abc import Iterator
from pathlib import Path
from typing import Final

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_report_pdf  # noqa: E402 - the tool's private src/, via the insert above
from _pdf_privacy_fixtures import IDENTITY  # noqa: E402 - beside this module
from _print_report_fixtures import (  # noqa: E402 - as above
    DPI,
    PDF_NAME,
    TIMEOUT,
    color_raster,
    fake_readers,
    gray_raster,
    pdf_info,
)
from pdf_raster import Raster, RasterError  # noqa: E402 - src/
from pdf_tools import PageSize, PdfInfo, PopplerError  # noqa: E402 - src/
from print_engine import MARGIN_INCHES, Problem  # noqa: E402 - src/

#: A page 80000 pt a side: 11112 px at the fixtures' 10 dpi, past the budget squared.
_HUGE: Final = PageSize(80000.0, 80000.0, None)

#: What each render step says of a PDF holding `_HUGE` as its second page.
_PAST: Final = (
    f"page 2 is 11112x11112 px at {DPI} dpi, "
    "past the render budget of 100,000,000 pixels"
)

#: The render steps, in the order their findings come.
_RENDER_STEPS: Final = ("png renders", "gray render", "color render")


def _info(*sizes: PageSize) -> PdfInfo:
    """The fixtures' pdfinfo, holding a page of each of `sizes`."""
    return dataclasses.replace(pdf_info(), pages=len(sizes), page_sizes=sizes)


def _letter() -> PageSize:
    """The fixtures' one page size: Letter."""
    return pdf_info().page_sizes[0]


class _ReadCase(unittest.TestCase):
    """A fresh out dir per case, and the PDF `read_pdf` is pointed at inside it."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)
        self.pdf = self.out / "pdfs" / PDF_NAME
        self.pdf.parent.mkdir()

    def read(self) -> print_report_pdf.PdfReport:
        return print_report_pdf.read_pdf(
            self.pdf,
            self.out,
            dpi=DPI,
            timeout=TIMEOUT,
            identity=IDENTITY,
            margin_inches=MARGIN_INCHES,
        )


class ReadBudgetTests(_ReadCase):
    """A page past the budget, or no sizes to judge by, is each render step's finding."""

    def test_a_page_past_the_budget_fails_every_render_before_pdftoppm(self) -> None:
        with fake_readers(read_info=_info(_letter(), _HUGE)) as fakes:
            report = self.read()
        fakes["render_png"].assert_not_called()
        fakes["render_rasters"].assert_not_called()
        expected = tuple(
            Problem("finding", f"{step} failed: {_PAST}") for step in _RENDER_STEPS
        )
        self.assertEqual(report.problems, expected)
        self.assertEqual((report.renders, report.gray_levels), ((), ()))
        self.assertEqual((report.colors, report.ink), ((), ()))

    def test_no_page_sizes_to_judge_by_fails_every_render(self) -> None:
        unread = PopplerError("pdfinfo exited 1: damaged")
        with fake_readers(read_info=unread) as fakes:
            report = self.read()
        fakes["render_rasters"].assert_not_called()
        reason = "no page sizes to budget the render by: the info step failed"
        self.assertEqual(
            report.problems[1:],
            tuple(
                Problem("finding", f"{step} failed: {reason}") for step in _RENDER_STEPS
            ),
        )

    def test_each_render_is_asked_for_the_pages_pdfinfo_counted(self) -> None:
        with fake_readers(read_info=_info(_letter(), _letter())) as fakes:
            self.read()
        for call in fakes["render_rasters"].call_args_list:
            self.assertEqual(call.kwargs["pages"], 2)


class PerPageTests(_ReadCase):
    """Only what is measured of a page outlives it: its levels or colors, and its ink."""

    def test_no_page_is_held_once_the_next_is_rendered(self) -> None:
        def pages(pdf: Path, *, gray: bool, **_: object) -> Iterator[Raster]:
            held: list[weakref.ref[Raster]] = []
            for _page in range(3):
                gc.collect()
                self.assertTrue(all(ref() is None for ref in held))
                page = gray_raster() if gray else color_raster()
                held.append(weakref.ref(page))
                yield page
                del page

        with fake_readers(read_info=_info(*[_letter()] * 3), render_rasters=pages):
            report = self.read()
        self.assertEqual(report.problems, ())
        self.assertEqual((len(report.gray_levels), len(report.colors)), (3, 3))
        self.assertEqual(len(report.ink), 3)

    def test_a_page_failing_its_margins_fails_ink_and_keeps_the_levels(self) -> None:
        short = Raster(1, 1, 1, b"\xff")
        with fake_readers(render_rasters=lambda pdf, *, gray, **_: (short,)):
            report = self.read()
        self.assertEqual(len(report.gray_levels), 1)
        self.assertEqual(report.ink, ())
        (problem,) = report.problems
        self.assertTrue(problem.what.startswith("ink extent failed: "), problem)


class CompareBudgetTests(unittest.TestCase):
    """compare_pdfs judges both PDFs' pages before rendering either."""

    def compare(self, **readers: object) -> dict[str, object]:
        out = Path("out")
        with fake_readers(**readers) as self.fakes:
            return print_report_pdf.compare_pdfs(
                out / "a.pdf", out / "b.pdf", dpi=DPI, timeout=TIMEOUT, out_dir=out
            )

    def test_a_page_past_the_budget_refuses_the_comparison_unrendered(self) -> None:
        def info(pdf: Path, *, timeout: float) -> PdfInfo:
            return _info(_letter(), _HUGE) if pdf.name == "b.pdf" else pdf_info()

        with self.assertRaisesRegex(RasterError, f"^b.pdf: {_PAST}$"):
            self.compare(read_info=info)
        self.fakes["render_rasters"].assert_not_called()

    def test_a_pdf_pdfinfo_cannot_read_refuses_the_comparison(self) -> None:
        with self.assertRaisesRegex(RasterError, "^a.pdf: pdfinfo exited 1$"):
            self.compare(read_info=PopplerError("pdfinfo exited 1"))

    def test_each_pdf_renders_the_pages_pdfinfo_counted(self) -> None:
        result = self.compare(read_info=_info(_letter(), _letter()))
        calls = self.fakes["render_rasters"].call_args_list
        self.assertEqual([call.kwargs["pages"] for call in calls], [2, 2])
        self.assertEqual(result["page_count"], [1, 1])

    def test_the_pages_are_compared_as_they_come(self) -> None:
        def pages(pdf: Path, **_: object) -> Iterator[Raster]:
            yield color_raster()
            if pdf.name == "a.pdf":
                yield color_raster(marked=3)

        result = self.compare(render_rasters=pages)
        self.assertEqual(result["page_count"], [2, 1])
        self.assertFalse(result["identical"])
        pages_json = result["pages"]
        assert isinstance(pages_json, list)
        self.assertEqual(len(pages_json), 1)


if __name__ == "__main__":
    unittest.main()
