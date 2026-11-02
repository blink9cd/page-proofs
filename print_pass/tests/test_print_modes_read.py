"""Grouped suite: print_modes' read and compare runs end to end (given PDFs, each to a report).

Each case runs `main` past its checks into a run. Nothing here runs poppler: the readers under
`read_pdf` and `compare_pdfs` are `_print_report_fixtures.fake_readers`, so both run for real
over scripted readings, and `report.json` is the real report of those. The make run is the
sibling `test_print_modes.py`'s.
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
from typing import Final
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# ...and the tool's own folder, where the CLI that `run_main` runs is found.
sys.path.insert(0, str(_TOOL_DIR))
# First, and apart: each appends the shared bounded_run to the path, which the CLI fixtures
# below import.
import print_modes  # noqa: E402 - the tool's private src/
import print_report  # noqa: E402 - as above
from pdf_raster import Raster, RasterError  # noqa: E402 - as above
from pdf_tools import PopplerError  # noqa: E402 - as above

from _print_pass_fixtures import (  # noqa: E402 - this folder's shared fixtures
    Outcome,
    RunCase,
    pdf_file,
    run_main,
)
from _print_report_fixtures import (  # noqa: E402 - as above
    DPI,
    color_raster,
    fake_readers,
)

#: The readers `read_pdf` calls that run poppler, each bounded by the run's --timeout.
_POPPLER_READERS: Final = (
    "read_info",
    "read_fonts",
    "read_images",
    "render_png",
    "render_rasters",
)


def _differing_rasters(
    pdf: Path, *, gray: bool, dpi: int, timeout: float, pages: int
) -> tuple[Raster, ...]:
    """`render_rasters` whose `b.pdf` is marked differently from every other PDF."""
    return (color_raster(marked=3 if pdf.name == "b.pdf" else 2),)


class ReadTest(RunCase):
    """--mode read: every PDF read into the report, the exit code its findings'."""

    def read(self, *names: str, readers: Mapping[str, object] | None = None) -> Outcome:
        """Read placeholder PDFs of `names` over fake readers."""
        pdfs = [pdf_file(self.root / "in", name) for name in names]
        with fake_readers(**(readers or {})):
            return run_main(
                *("--mode", "read", "--out", str(self.out), "--dpi", str(DPI)),
                *(arg for pdf in pdfs for arg in ("--pdf", str(pdf))),
            )

    def test_every_pdf_is_read_into_the_report(self) -> None:
        outcome = self.read("b.pdf", "a.pdf")
        self.assertEqual(outcome.code, 0, outcome.stderr)
        pdfs = self.report()["pdfs"]
        assert isinstance(pdfs, list)
        self.assertEqual([pdf["file"] for pdf in pdfs], ["b.pdf", "a.pdf"])
        self.assertTrue((self.out / "text" / "a.txt").is_file())
        self.assertIn("totals: pdfs 2, findings 0, warnings 0", outcome.stdout)
        self.assertIn(f"report: {self.out / 'report.json'}", outcome.stdout)

    def test_a_finding_exits_one(self) -> None:
        failed = PopplerError("pdfinfo exited 1: bad")
        outcome = self.read("a.pdf", readers={"read_info": failed})
        self.assertEqual(outcome.code, 1, outcome.stderr)
        self.assertIn(
            "FINDING a.pdf: info failed: pdfinfo exited 1: bad", outcome.stdout
        )

    def test_a_given_pdf_is_measured_against_a_named_one_cm_margin(self) -> None:
        # A PDF printed elsewhere states no margin: 1 cm is assumed, by name, at the call.
        self.assertEqual(print_modes.OUTSIDE_PDF_MARGIN_INCHES, Fraction(50, 127))
        with mock.patch.object(
            print_report, "read_pdf", wraps=print_report.read_pdf
        ) as reads:
            outcome = self.read("a.pdf", "b.pdf")
        self.assertEqual(outcome.code, 0, outcome.stderr)
        margins = {call.kwargs["margin_inches"] for call in reads.call_args_list}
        self.assertEqual(margins, {print_modes.OUTSIDE_PDF_MARGIN_INCHES})

    def test_the_timeout_bounds_each_poppler_call(self) -> None:
        pdf = pdf_file(self.root / "in", "a.pdf")
        with fake_readers() as fakes:
            outcome = run_main(
                *("--mode", "read", "--out", str(self.out), "--pdf", str(pdf)),
                *("--dpi", str(DPI), "--timeout", "7.5"),
            )
        self.assertEqual(outcome.code, 0, outcome.stdout)
        for name in _POPPLER_READERS:
            with self.subTest(reader=name):
                calls = fakes[name].call_args_list
                self.assertTrue(calls)
                self.assertEqual({call.kwargs["timeout"] for call in calls}, {7.5})


class CompareTest(RunCase):
    """--mode compare: the two PDFs' renders compared, the exit code whether they differ."""

    def compare(self, readers: Mapping[str, object] | None = None) -> Outcome:
        """Compare placeholder PDFs `a.pdf` and `b.pdf` over fake readers."""
        a, b = (pdf_file(self.root / "in", name) for name in ("a.pdf", "b.pdf"))
        with fake_readers(**(readers or {})):
            return run_main(
                *("--mode", "compare", "--out", str(self.out), "--dpi", str(DPI)),
                *("--pdf", str(a), "--pdf", str(b)),
            )

    def test_two_identical_renders_exit_zero(self) -> None:
        outcome = self.compare()
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(self.report()["mode"], "compare")
        self.assertIn("a.pdf vs b.pdf: identical", outcome.stdout)

    def test_two_differing_renders_exit_one(self) -> None:
        outcome = self.compare({"render_rasters": _differing_rasters})
        self.assertEqual(outcome.code, 1, outcome.stderr)
        self.assertIn("a.pdf vs b.pdf: page(s) 1 differ", outcome.stdout)

    def test_a_pdf_that_cannot_render_is_two_and_writes_nothing(self) -> None:
        failed = RasterError("pdftoppm exited 1: damaged")
        outcome = self.compare({"render_rasters": failed})
        self.assertEqual(outcome.code, 2)
        self.assertIn(
            "could not compare a.pdf and b.pdf: pdftoppm exited 1", outcome.stderr
        )
        self.assertFalse(self.out.exists())

    def test_the_timeout_bounds_each_render(self) -> None:
        a, b = (pdf_file(self.root / "in", name) for name in ("a.pdf", "b.pdf"))
        with fake_readers() as fakes:
            outcome = run_main(
                *("--mode", "compare", "--out", str(self.out), "--timeout", "7.5"),
                *("--dpi", str(DPI), "--pdf", str(a), "--pdf", str(b)),
            )
        self.assertEqual(outcome.code, 0, outcome.stdout)
        calls = fakes["render_rasters"].call_args_list
        self.assertEqual([call.kwargs["timeout"] for call in calls], [7.5, 7.5])


if __name__ == "__main__":
    unittest.main()
