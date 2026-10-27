"""Suite: pdf_raster's pure half -- PNM parsing, dominant shares, ink extent, comparison.

Every raster is built from bytes here, so each figure asserted is known exactly. The renders,
which cross the subprocess boundary, are `test_pdf_raster_render.py`'s.
"""

from __future__ import annotations

import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from hypothesis import HealthCheck, given, settings  # noqa: E402 - position only; keep below the bootstrap above
from hypothesis import strategies as st  # noqa: E402 - position only; keep below the bootstrap above
from pdf_raster import (
    MAX_RASTER_PIXELS,
    WHITE_THRESHOLD,
    check_render_budget,
    InkExtent,
    PageDiff,
    Raster,
    RasterError,
    Share,
    compare_rasters,
    dominant_shares,
    ink_extent,
    parse_pnm,
)  # noqa: E402 - position only; keep below the bootstrap above
from _pdf_raster_fixtures import DPI, gray, ink  # noqa: E402 - beside this module, via the loader's top_level_dir

# The determinism convention: a profile registered at import, never an `@settings`
# decorator, which a fuzzing campaign could not override. deadline=None: a per-example
# wall-clock deadline measures machine load, not correctness.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")


#: A Letter page at 96 dpi: 8.5 x 11 in.
_LETTER_WIDTH_96 = 816


_LETTER_HEIGHT_96 = 1056


#: The resolution at which a 1 cm margin is a whole number of rows: 127 / 2.54 = 50.
_MARGIN_EXACT_DPI = 127


_HALF_INCH = Fraction(1, 2)


#: Inputs `parse_pnm` must refuse, each with a fragment its message must carry.
_REFUSED_PNM = (
    (b"P2\n2 1\n255\n0 0\n", "b'P2'"),  # ASCII gray
    (b"P5\n2 1\n65535\n" + bytes(4), "maxval 65535"),  # 16-bit
    (b"P5\n4 2\n255\n" + bytes(7), "7 pixel bytes"),  # truncated pixels
    (b"P5\n1 1\n255\n" + bytes(2), "2 pixel bytes"),  # bytes after the pixels
    (b"not an image", "b'no'"),  # junk
    (b"", "b''"),
    (b"P5\n1 1\n255", "header"),  # no delimiter after maxval
    (b"P5\n0 2\n255\n", "0x2"),  # zero width
)


#: Shapes a Raster must refuse to be built with, each with its message's fragment.
_REFUSED_SHAPES = (
    ((2, 2, 1, b"\x00"), "1 pixel bytes"),
    ((1, 1, 2, b"\x00\x00"), "2 channels"),
    ((3, 0, 1, b""), "3x0"),
)


def _page(ink_rows: range, width: int = _LETTER_WIDTH_96) -> Raster:
    """A white gray Letter page at 96 dpi with every pixel of `ink_rows` black."""
    pixels = bytearray([255]) * (width * _LETTER_HEIGHT_96)
    for row in ink_rows:
        pixels[row * width : (row + 1) * width] = bytes(width)
    return gray(width, _LETTER_HEIGHT_96, bytes(pixels))


class ParsePnmTests(unittest.TestCase):
    """Binary P5 / P6 at maxval 255 parse; everything else is refused by name."""

    def test_parses_a_gray_p5(self) -> None:
        pixels = bytes([0, 0, 255, 255, 0, 0, 255, 255])
        self.assertEqual(parse_pnm(b"P5\n4 2\n255\n" + pixels), Raster(4, 2, 1, pixels))

    def test_parses_a_color_p6(self) -> None:
        pixels = bytes([255, 0, 0, 0, 0, 255])
        self.assertEqual(parse_pnm(b"P6\n2 1\n255\n" + pixels), Raster(2, 1, 3, pixels))

    def test_skips_header_comments_between_any_two_fields(self) -> None:
        data = b"P5\n# made by a test\n2 # the width\n1\n255\n" + bytes([7, 8])
        self.assertEqual(parse_pnm(data), gray(2, 1, bytes([7, 8])))

    def test_pixel_bytes_that_look_like_whitespace_are_pixels(self) -> None:
        # Exactly ONE whitespace byte ends the header: a raster starting with a newline
        # byte (value 10) must keep it as its first pixel.
        self.assertEqual(parse_pnm(b"P5 2 1 255\n\n\x00"), gray(2, 1, b"\n\x00"))

    def test_refuses_everything_but_binary_eight_bit_p5_and_p6(self) -> None:
        for data, fragment in _REFUSED_PNM:
            with self.subTest(data=data[:16]), self.assertRaises(RasterError) as caught:
                parse_pnm(data)
            self.assertIn(fragment, str(caught.exception))

    def test_a_raster_refuses_a_shape_its_pixels_disagree_with(self) -> None:
        for (width, height, channels, pixels), fragment in _REFUSED_SHAPES:
            with self.subTest(fragment), self.assertRaisesRegex(RasterError, fragment):
                Raster(width, height, channels, pixels)


class DominantSharesTests(unittest.TestCase):
    """The levels or colors covering at least `min_share` of a page, most common first."""

    def setUp(self) -> None:
        self.raster = gray(10, 10, bytes([19] * 25 + [255] * 70 + [114] * 5))

    def test_reports_each_level_by_share_most_common_first(self) -> None:
        expected = (Share((255,), 0.7), Share((19,), 0.25), Share((114,), 0.05))
        self.assertEqual(dominant_shares(self.raster), expected)

    def test_drops_a_level_below_min_share(self) -> None:
        expected = (Share((255,), 0.7), Share((19,), 0.25))
        self.assertEqual(dominant_shares(self.raster, min_share=0.1), expected)

    def test_keeps_a_level_at_exactly_min_share(self) -> None:
        raster = gray(10, 10, bytes([255] * 99 + [7]))
        self.assertEqual(dominant_shares(raster)[-1], Share((7,), 0.01))

    def test_reports_colors_as_rgb_triples(self) -> None:
        raster = Raster(2, 2, 3, bytes([255, 255, 255] * 3 + [200, 30, 30]))
        expected = (Share((255, 255, 255), 0.75), Share((200, 30, 30), 0.25))
        self.assertEqual(dominant_shares(raster), expected)

    def test_breaks_a_tie_by_value_and_rounds_to_four_places(self) -> None:
        expected = tuple(Share((level,), 0.3333) for level in (3, 5, 9))
        self.assertEqual(dominant_shares(gray(3, 1, bytes([9, 3, 5]))), expected)


class InkExtentTests(unittest.TestCase):
    """The first and last rows holding ink, and the blank share of the content area below."""

    def test_a_blank_page_is_wholly_blank(self) -> None:
        self.assertEqual(ink(_page(range(0))), InkExtent(None, None, 1.0))

    def test_ink_inside_the_top_margin_leaves_the_content_area_blank(self) -> None:
        # Rows 10-20 lie above the content area, whose top is 1 cm (37.8 px) down.
        self.assertEqual(ink(_page(range(10, 21))), InkExtent(10, 20, 1.0))

    def test_measures_the_blank_below_mid_page_ink(self) -> None:
        # Content: 1056 - 2 x 37.795 = 980.409 rows; its last row is 1055 - 37.795 =
        # 1017.205; blank below row 520 is 497.205 rows = 0.5071 of the content area.
        extent = ink(_page(range(500, 521)))
        self.assertEqual(extent, InkExtent(500, 520, 0.5071))

    def test_ink_on_the_very_last_row_leaves_nothing_blank(self) -> None:
        last = _LETTER_HEIGHT_96 - 1
        extent = ink(_page(range(last, last + 1)))
        self.assertEqual(extent, InkExtent(last, last, 0.0))

    def test_one_pixel_just_below_the_white_threshold_is_ink(self) -> None:
        pixels = bytearray(_page(range(0)).pixels)
        pixels[600 * _LETTER_WIDTH_96 + 3] = WHITE_THRESHOLD - 1
        pixels[700 * _LETTER_WIDTH_96 + 3] = WHITE_THRESHOLD
        page = gray(_LETTER_WIDTH_96, _LETTER_HEIGHT_96, bytes(pixels))
        extent = ink(page)
        self.assertEqual((extent.first_ink_row, extent.last_ink_row), (600, 600))

    def test_refuses_a_color_raster(self) -> None:
        with self.assertRaisesRegex(RasterError, "gray"):
            ink(Raster(1, 1, 3, bytes(3)))

    def test_refuses_a_raster_no_taller_than_its_margins(self) -> None:
        with self.assertRaisesRegex(RasterError, "margins"):
            ink(gray(1, 75, bytes(75)))

    def test_refuses_a_raster_exactly_as_tall_as_its_margins(self) -> None:
        # At 127 dpi a 1 cm margin is exactly 50 rows, so 100 rows leave no content area.
        with self.assertRaisesRegex(RasterError, "margins"):
            ink(gray(1, 100, bytes(100)), _MARGIN_EXACT_DPI)

    def test_a_raster_one_row_taller_than_its_margins_is_read(self) -> None:
        # One row of content, inked: nothing below it is blank.
        extent = ink(gray(1, 101, bytes(101)), _MARGIN_EXACT_DPI)
        self.assertEqual(extent, InkExtent(0, 100, 0.0))

    def test_the_blank_share_is_measured_against_the_margin_given(self) -> None:
        # Firefox's half inch is 48 rows at 96 dpi: content 960 rows, its last row 1007, and
        # 487 blank rows below row 520 are 0.5073 of it, where a 1 cm margin reads 0.5071.
        page = _page(range(500, 521))
        extent = ink_extent(page, DPI, margin_inches=_HALF_INCH)
        self.assertEqual(extent, InkExtent(500, 520, 0.5073))
        self.assertNotEqual(extent, ink(page))

    def test_refuses_a_negative_margin(self) -> None:
        with self.assertRaisesRegex(RasterError, "margin -1/2 in"):
            ink_extent(_page(range(0)), DPI, margin_inches=-_HALF_INCH)


class CompareRastersTests(unittest.TestCase):
    """Per page: identical or not, how many pixels differ, and where, in points."""

    def setUp(self) -> None:
        self.white = gray(8, 4, bytes([255]) * 32)

    def changed(self, *points: tuple[int, int]) -> Raster:
        """The white raster with each `(x, y)` in `points` turned black."""
        pixels = bytearray(self.white.pixels)
        for x, y in points:
            pixels[y * self.white.width + x] = 0
        return gray(self.white.width, self.white.height, bytes(pixels))

    def test_identical_rasters_are_identical(self) -> None:
        diff = compare_rasters(1, self.white, self.white, DPI)
        self.assertEqual(diff, PageDiff(1, True, 0, None))

    def test_one_changed_pixel_is_boxed_exactly_at_96_dpi(self) -> None:
        # Pixel (3, 1) at 96 dpi spans x 2.25-3.0 pt and y 0.75-1.5 pt.
        diff = compare_rasters(2, self.white, self.changed((3, 1)), DPI)
        self.assertEqual(diff, PageDiff(2, False, 1, (2.25, 0.75, 3.0, 1.5)))

    def test_one_changed_pixel_is_boxed_exactly_at_72_dpi(self) -> None:
        diff = compare_rasters(1, self.white, self.changed((3, 1)), 72)
        self.assertEqual(diff.bbox_pt, (3.0, 1.0, 4.0, 2.0))

    def test_counts_every_differing_pixel_on_a_row(self) -> None:
        diff = compare_rasters(1, self.white, self.changed((0, 2), (7, 2), (4, 2)), 72)
        self.assertEqual(diff, PageDiff(1, False, 3, (0.0, 2.0, 8.0, 3.0)))

    def test_a_color_pixel_differs_when_any_one_channel_does(self) -> None:
        pixels = bytes([255] * 8 * 4 * 3)
        changed = bytearray(pixels)
        changed[(0 * 8 + 1) * 3 + 2] = 0  # pixel (1, 0), blue only
        changed[(3 * 8 + 5) * 3 + 0] = 0  # pixel (5, 3), red only
        a, b = Raster(8, 4, 3, pixels), Raster(8, 4, 3, bytes(changed))
        diff = compare_rasters(1, a, b, DPI)
        self.assertEqual(diff, PageDiff(1, False, 2, (0.75, 0.0, 4.5, 3.0)))

    def test_refuses_rasters_of_different_sizes(self) -> None:
        with self.assertRaisesRegex(RasterError, "8x4.*8x5"):
            compare_rasters(1, self.white, gray(8, 5, bytes([255]) * 40), DPI)

    def test_refuses_rasters_of_different_channels(self) -> None:
        with self.assertRaisesRegex(RasterError, "gray.*color"):
            compare_rasters(1, self.white, Raster(8, 4, 3, bytes([255]) * 96), DPI)


class RenderBudgetTests(unittest.TestCase):
    """A page's pixels at a resolution, judged against the budget before any render runs."""

    def test_the_budget_is_a_hundred_million_pixels(self) -> None:
        self.assertEqual(MAX_RASTER_PIXELS, 100_000_000)

    def test_a_page_past_the_budget_is_refused_by_number_and_size(self) -> None:
        # 14400 pt is 200 in: 19200 px a side at 96 dpi, near 1.1 GB as one color raster.
        sizes = ((612.0, 792.0), (14400.0, 14400.0))
        expected = (
            "^page 2 is 19200x19200 px at 96 dpi, "
            "past the render budget of 100,000,000 pixels$"
        )
        with self.assertRaisesRegex(RasterError, expected):
            check_render_budget(sizes, 96)

    def test_a_page_exactly_at_the_budget_is_rendered(self) -> None:
        # 7500 pt at 96 dpi is 10000 px: a 10000 x 10000 page is the budget exactly.
        check_render_budget(((7500.0, 7500.0), (612.0, 792.0)), 96)

    def test_a_fraction_of_a_pixel_counts_as_a_whole_one(self) -> None:
        # pdftoppm rounds a page's edge up, so the budget must too.
        with self.assertRaisesRegex(RasterError, "is 10000x10001 px"):
            check_render_budget(((7500.0, 7500.01),), 96)

    def test_a_size_that_is_no_finite_number_is_refused(self) -> None:
        for value in (float("nan"), float("inf")):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(RasterError, "^page 1 has no finite size$"),
            ):
                check_render_budget(((value, 792.0),), 96)

    def test_a_resolution_that_is_not_positive_is_refused(self) -> None:
        with self.assertRaisesRegex(RasterError, "^dpi 0:"):
            check_render_budget(((612.0, 792.0),), 0)


@st.composite
def _valid_headers(draw: st.DrawFn) -> bytes:
    """A well-formed P5/P6 header and random pixels, their length at most two bytes off.

    Arbitrary bytes almost never pass the header, and a random payload behind a valid one
    almost never has the length it needs, so the property would rarely reach a parse that
    succeeds. Sizing the payload from the header makes both outcomes common.
    """
    magic = draw(st.sampled_from([b"P5", b"P6"]))
    width, height = draw(st.integers(0, 6)), draw(st.integers(0, 6))
    channels = 1 if magic == b"P5" else 3
    size = max(0, width * height * channels + draw(st.integers(-2, 2)))
    payload = draw(st.binary(min_size=size, max_size=size))
    return magic + f"\n{width} {height}\n255\n".encode() + payload


class ParsePnmPropertyTests(unittest.TestCase):
    """Arbitrary bytes give a well-formed Raster or a RasterError, never anything else."""

    @given(st.one_of(st.binary(), _valid_headers()))
    def test_fuzz_parse_pnm(self, data: bytes) -> None:
        try:
            raster = parse_pnm(data)
        except RasterError:
            return
        expected = raster.width * raster.height * raster.channels
        self.assertEqual(len(raster.pixels), expected)
        self.assertIn(raster.channels, (1, 3))
