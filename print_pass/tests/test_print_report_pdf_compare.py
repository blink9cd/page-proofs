"""Suite: compare_pdfs, and the report's paths.

`compare_pdfs` is run with `render_rasters` replaced by real rasters, so the comparison itself
runs for real. Reading one PDF is the sibling `test_print_report_pdf`'s; the scrub every text
passes through is `test_print_report_scrub`'s.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Final
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_report_pdf  # noqa: E402 - the tool's private src/, via the insert above
from _pdf_privacy_fixtures import HOME  # noqa: E402 - beside this module
from _print_report_fixtures import (  # noqa: E402 - as above
    COLOR_CHANNELS,
    DPI,
    TIMEOUT,
    color_raster,
    pdf_info,
)
from pdf_raster import Raster, RasterError  # noqa: E402 - src/

#: An out dir that is not under the fixture home.
_OUT: Final = Path("/" + "srv" + "/" + "print-out")


class ReportPathTests(unittest.TestCase):
    def test_a_path_inside_the_out_dir_is_relative_with_forward_slashes(self) -> None:
        self.assertEqual(
            print_report_pdf.report_path(_OUT / "pdfs" / "a.pdf", _OUT), "pdfs/a.pdf"
        )

    def test_a_path_outside_the_out_dir_is_its_name_alone(self) -> None:
        self.assertEqual(
            print_report_pdf.report_path(Path(HOME) / "Downloads" / "a.pdf", _OUT),
            "a.pdf",
        )

    def test_a_relative_out_dir_is_compared_resolved(self) -> None:
        # Resolving reads the process's working directory itself, so the case moves into a
        # folder of its own and is moved back before that folder is removed.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(folder.name)
        absolute = Path(folder.name).resolve() / "out" / "pdfs" / "a.pdf"
        self.assertEqual(
            print_report_pdf.report_path(absolute, Path("out")), "pdfs/a.pdf"
        )


class ComparePdfsTests(unittest.TestCase):
    def compare(
        self, pages: Mapping[str, tuple[Raster, ...]]
    ) -> tuple[dict[str, object], mock.MagicMock]:
        def rasters(
            pdf: Path, *, gray: bool, dpi: int, timeout: float, **_: object
        ) -> tuple[Raster, ...]:
            return pages[pdf.name]

        with (
            mock.patch.object(print_report_pdf, "read_info", return_value=pdf_info()),
            mock.patch.object(
                print_report_pdf, "render_rasters", side_effect=rasters
            ) as fake,
        ):
            result = print_report_pdf.compare_pdfs(
                _OUT / "pdfs" / "a.pdf",
                _OUT / "pdfs" / "b.pdf",
                dpi=DPI,
                timeout=TIMEOUT,
                out_dir=_OUT,
            )
        return result, fake

    def test_identical_renders_compare_identical(self) -> None:
        result, fake = self.compare(
            {"a.pdf": (color_raster(),), "b.pdf": (color_raster(),)}
        )
        self.assertEqual(
            result,
            {
                "a": "pdfs/a.pdf",
                "b": "pdfs/b.pdf",
                "identical": True,
                "page_count": [1, 1],
                "pages": [
                    {
                        "page": 1,
                        "identical": True,
                        "differing_pixels": 0,
                        "bbox_pt": None,
                    }
                ],
            },
        )
        self.assertEqual(
            fake.call_args_list,
            [
                mock.call(
                    _OUT / "pdfs" / "a.pdf",
                    gray=False,
                    dpi=DPI,
                    timeout=TIMEOUT,
                    pages=1,
                ),
                mock.call(
                    _OUT / "pdfs" / "b.pdf",
                    gray=False,
                    dpi=DPI,
                    timeout=TIMEOUT,
                    pages=1,
                ),
            ],
        )

    def test_a_differing_page_names_its_pixels_and_box_in_points(self) -> None:
        # The third pixel of the first row is red in one render only: at 10 dpi a pixel is
        # 7.2 points a side.
        result, _ = self.compare(
            {
                "a.pdf": (color_raster(2), color_raster()),
                "b.pdf": (color_raster(3), color_raster()),
            }
        )
        self.assertFalse(result["identical"])
        self.assertEqual(
            result["pages"],
            [
                {
                    "page": 1,
                    "identical": False,
                    "differing_pixels": 1,
                    "bbox_pt": [14.4, 0.0, 21.6, 7.2],
                },
                {"page": 2, "identical": True, "differing_pixels": 0, "bbox_pt": None},
            ],
        )

    def test_a_page_count_mismatch_differs_on_the_pages_both_have(self) -> None:
        result, _ = self.compare(
            {"a.pdf": (color_raster(), color_raster()), "b.pdf": (color_raster(),)}
        )
        self.assertEqual((result["identical"], result["page_count"]), (False, [2, 1]))
        self.assertEqual(
            result["pages"],
            [{"page": 1, "identical": True, "differing_pixels": 0, "bbox_pt": None}],
        )

    def test_pages_of_different_sizes_differ_uncounted(self) -> None:
        narrow = Raster(4, 2, COLOR_CHANNELS, bytes(4 * 2 * COLOR_CHANNELS))
        result, _ = self.compare({"a.pdf": (color_raster(),), "b.pdf": (narrow,)})
        self.assertEqual(
            result["pages"],
            [
                {
                    "page": 1,
                    "identical": False,
                    "differing_pixels": None,
                    "bbox_pt": None,
                }
            ],
        )
        self.assertFalse(result["identical"])

    def test_a_pdf_that_cannot_be_rendered_raises(self) -> None:
        with (
            mock.patch.object(print_report_pdf, "read_info", return_value=pdf_info()),
            mock.patch.object(
                print_report_pdf, "render_rasters", side_effect=RasterError("no page")
            ),
            self.assertRaises(RasterError),
        ):
            print_report_pdf.compare_pdfs(
                _OUT / "a.pdf", _OUT / "b.pdf", dpi=DPI, timeout=TIMEOUT, out_dir=_OUT
            )


if __name__ == "__main__":
    unittest.main()
