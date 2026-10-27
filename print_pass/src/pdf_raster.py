"""Render PDF pages with `pdftoppm`, and read the rasters: gray levels, ink extent, comparison.

A print is judged from its PDF, and a PDF is judged here from what poppler draws of it.
`pdftoppm` writes each page as a binary PGM (gray, `-gray`), PPM (color) or PNG (`-png`, kept
for a person to look at). The PGM / PPM rasters are parsed in plain Python -- the format is a
short text header and a flat byte buffer -- so no imaging library has to be pinned for it.

What is read from a raster:

- ``dominant_shares``: the gray levels or colors that each cover at least a share of the page,
  which is how a print's palette (black text on white, or a dark page nobody asked for) shows.
- ``ink_extent``: the first and last rows holding ink, and how much of the content area -- the
  page minus the print margin at top and bottom -- lies blank below the last one. The margin
  is the one the PDF was printed with, which the caller names: it differs by engine.
- ``compare_rasters``: whether two renders of one page are identical, and if not, how many
  pixels differ and the box they fall in, in points from the page's top-left corner.

A raster's memory follows its page's size, which is the PDF maker's to choose, so a caller
judges each page against `MAX_RASTER_PIXELS` with ``check_render_budget`` before rendering,
and ``render_rasters`` renders one page per `pdftoppm` run, handing each on before the next.

Entry point assumptions: `pdftoppm` is on `PATH` (the caller validates that before any run),
and every run goes through `bounded_run`, so a hung render is killed with its whole process
tree. Every failure of the tool -- absent, timed out or exiting non-zero -- is a `RasterError`,
so a reader can record it as a finding and carry on with the PDF's other checks.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final

# The shared tree-killing subprocess runner: a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place,
# and put near the front of the path, ahead of every installed distribution, so none of the
# same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run

#: A gray value at or above this is paper, not ink.
WHITE_THRESHOLD: Final = 250
#: A level or color is reported when it covers at least 1% of the page.
DEFAULT_MIN_SHARE: Final = 0.01
POINTS_PER_INCH: Final = 72

#: Decimal places a share keeps: enough to tell 1% from 1.5% of a page, few enough to diff.
SHARE_PLACES: Final = 4
#: Decimal places a box coordinate keeps, in points: finer than any pixel at print dpi.
POINT_PLACES: Final = 2
#: The one sample depth read: pdftoppm writes 8 bits per channel.
MAXVAL: Final = 255
#: Channels per pixel by the magic number that announces them.
CHANNELS_BY_MAGIC: Final[Mapping[bytes, int]] = {b"P5": 1, b"P6": 3}
GRAY_CHANNELS: Final = 1
PDFTOPPM: Final = "pdftoppm"
#: The most pixels one page may be rendered at. A Letter page at 600 dpi is about 34 million;
#: past this, a color page's raster alone passes 300 MB, and a 200-inch page made to exhaust
#: memory is refused before `pdftoppm` draws it.
MAX_RASTER_PIXELS: Final = 100_000_000
PNG_SUFFIX: Final = ".png"
#: What `pdftoppm` names a page's raster: `.pgm` under `-gray`, `.ppm` without it.
GRAY_SUFFIX: Final = ".pgm"
COLOR_SUFFIX: Final = ".ppm"
#: Every gray level that is paper, not ink.
_PAPER_LEVELS: Final = bytes(range(WHITE_THRESHOLD, MAXVAL + 1))
#: The file-name prefix for renders made into a scratch folder of their own.
_SCRATCH_STEM: Final = "page"
#: The most digits a header number may have. No page raster comes near a billion pixels a side,
#: and the bound keeps a hostile header from reaching `int()`'s digit limit.
_MAX_HEADER_DIGITS: Final = 9

#: Header whitespace, as netpbm defines it, and a `#` comment running to the end of its line.
_SPACE: Final = rb"[ \t\n\v\f\r]"
_SEPARATOR: Final = rb"(?:" + _SPACE + rb"|#[^\r\n]*[\r\n])+"
_NUMBER: Final = rb"(\d{1,%d})" % _MAX_HEADER_DIGITS
#: Magic, width, height, maxval, then EXACTLY one whitespace byte: the pixels start after it,
#: so a first pixel whose value happens to be a whitespace byte stays a pixel.
_HEADER: Final = re.compile(
    rb"(P[56])" + (_SEPARATOR + _NUMBER) * 3 + _SPACE,
)


class RasterError(ValueError):
    """A raster that cannot be parsed or analyzed, or a `pdftoppm` run that failed."""


@dataclass(frozen=True)
class Raster:
    """One rendered page: row-major pixels, `channels` bytes per pixel."""

    width: int
    height: int
    channels: int  # 1 = gray (P5), 3 = RGB (P6)
    pixels: bytes  # row-major, len == width * height * channels

    def __post_init__(self) -> None:
        """Refuse a shape the pixel buffer disagrees with, so no reader must re-check it."""
        if self.channels not in CHANNELS_BY_MAGIC.values():
            raise RasterError(
                f"a raster has 1 or 3 channels, not {self.channels} channels"
            )
        if self.width < 1 or self.height < 1:
            raise RasterError(f"a {self.width}x{self.height} raster holds no pixels")
        needed = self.width * self.height * self.channels
        if len(self.pixels) != needed:
            raise RasterError(
                f"{len(self.pixels)} pixel bytes, where a {self.width}x{self.height} raster "
                f"of {self.channels} channel(s) needs {needed}"
            )


@dataclass(frozen=True)
class Share:
    """A gray level or color, and the share of the page it covers."""

    value: tuple[int, ...]  # (level,) for gray, (r, g, b) for color
    share: float  # rounded to SHARE_PLACES


@dataclass(frozen=True)
class InkExtent:
    """Where a page's ink starts and ends, and the blank below it."""

    first_ink_row: int | None  # None: a blank page
    last_ink_row: int | None
    #: Of the CONTENT area (the page minus its two print margins), 0..1, to 4 places.
    blank_below_share: float


@dataclass(frozen=True)
class PageDiff:
    """How one page's two renders differ."""

    page: int
    identical: bool
    differing_pixels: int
    #: x0, y0, x1, y1 in points from the top-left; None when identical.
    bbox_pt: tuple[float, float, float, float] | None


# fuzz-target
def parse_pnm(data: bytes) -> Raster:
    """Parse a binary P5 (gray) or P6 (color) image at maxval 255.

    Header comments (`#` to the end of a line) are allowed between any two header fields.

    Raises:
        RasterError: any other format, depth or header, or a pixel buffer that is short of,
            or runs past, what the header's size needs.
    """
    header = _HEADER.match(data)
    if header is None:
        raise RasterError(
            f"not a binary P5 (gray) or P6 (color) header: it starts {data[:2]!r}"
        )
    magic, width, height, maxval = header.groups()
    if int(maxval) != MAXVAL:
        raise RasterError(
            f"maxval {int(maxval)}: only 8-bit ({MAXVAL}) rasters are read"
        )
    channels = CHANNELS_BY_MAGIC[magic]
    return Raster(int(width), int(height), channels, data[header.end() :])


def dominant_shares(
    raster: Raster, min_share: float = DEFAULT_MIN_SHARE
) -> tuple[Share, ...]:
    """Each gray level or color covering at least `min_share` of `raster`, most common first.

    A tie is broken by value, ascending, so the order never depends on pixel order.
    """
    total = raster.width * raster.height
    ranked = sorted(_value_counts(raster).items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        Share(value, round(count / total, SHARE_PLACES))
        for value, count in ranked
        if count / total >= min_share
    )


def ink_extent(gray: Raster, dpi: int, *, margin_inches: Fraction) -> InkExtent:
    """The first and last rows of `gray` holding ink, and the content area blank below them.

    A row is ink when any pixel in it is below `WHITE_THRESHOLD`. The content area is the page
    minus `margin_inches` at top and bottom -- the margin the PDF was printed with, exact, so a
    margin a whole number of rows long is not a hair off one; the blank share is measured from
    the last ink row to the content area's last row, clamped to 0..1, so ink in the bottom
    margin reads 0 and a blank page reads 1.

    Raises:
        RasterError: `gray` is a color raster, `dpi` is not positive, `margin_inches` is
            negative, or the raster is no taller than its two margins.
    """
    _require_positive_dpi(dpi)
    if gray.channels != GRAY_CHANNELS:
        raise RasterError("ink extent is read from a gray raster, not a color one")
    if margin_inches < 0:
        raise RasterError(
            f"margin {margin_inches} in: a print margin cannot be negative"
        )
    margin = margin_inches * dpi
    content_height = gray.height - 2 * margin
    if content_height <= 0:
        raise RasterError(
            f"a raster {gray.height} rows tall at {dpi} dpi is no taller than its margins"
        )
    # Stripping paper bytes from each end finds the first and last ink PIXEL in one C-level
    # pass apiece; a Python scan of every row measured about eight times slower on a blank
    # Letter page at 96 dpi.
    pixels = gray.pixels
    first_pixel = len(pixels) - len(pixels.lstrip(_PAPER_LEVELS))
    if first_pixel == len(pixels):
        return InkExtent(None, None, 1.0)
    last = (len(pixels.rstrip(_PAPER_LEVELS)) - 1) // gray.width
    blank = float((gray.height - 1 - margin - last) / content_height)
    return InkExtent(
        first_pixel // gray.width, last, round(min(1.0, max(0.0, blank)), SHARE_PLACES)
    )


def compare_rasters(page: int, a: Raster, b: Raster, dpi: int) -> PageDiff:
    """Compare two renders of page `page`, pixel for pixel.

    A pixel differs when any of its channels does. The box covers every differing pixel whole,
    in points at `dpi`, from the page's top-left corner.

    Raises:
        RasterError: the two differ in size or channels, or `dpi` is not positive.
    """
    _require_positive_dpi(dpi)
    if _shape(a) != _shape(b):
        raise RasterError(
            f"page {page}: cannot compare a {_describe(a)} render with a {_describe(b)} one"
        )
    if a.pixels == b.pixels:
        return PageDiff(page, True, 0, None)
    rows = list(_row_differences(a, b))
    box = (
        min(first for _, _, first, _ in rows),
        rows[0][0],
        max(last for _, _, _, last in rows) + 1,
        rows[-1][0] + 1,
    )
    x0, y0, x1, y1 = (_to_points(pixels, dpi) for pixels in box)
    return PageDiff(
        page, False, sum(count for _, count, _, _ in rows), (x0, y0, x1, y1)
    )


def render_png(
    pdf: Path, out_dir: Path, stem: str, *, dpi: int, timeout: float
) -> tuple[Path, ...]:
    """Render every page of `pdf` to `<out_dir>/<stem>-<page>.png`; return them in page order.

    `out_dir` is created when absent.

    Raises:
        RasterError: `stem` is not a bare file name, `dpi` is not positive, `out_dir` already
            holds renders named for `stem`, or `pdftoppm` failed or wrote no page.
    """
    _require_positive_dpi(dpi)
    if not stem or Path(stem).name != stem:
        raise RasterError(f"a render stem must be a bare file name, not {stem!r}")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Refused, not overwritten: a 12-page PDF writes `stem-01.png` beside a 3-page one's
    # `stem-1.png`, and a folder mixing the two would return another PDF's pages as this one's.
    if _numbered_pages(out_dir, stem, PNG_SUFFIX):
        # The folder's NAME, never its path: a message becomes a report finding, and a
        # report must stay safe to share, so it may not carry a home-rooted path.
        raise RasterError(f"{out_dir.name} already holds renders named {stem}")
    # `--` ends the options, so a PDF whose name starts with `-` is still read as a file.
    _run_pdftoppm(
        ["-png", "-r", str(dpi), "--", str(pdf), str(out_dir / stem)], timeout
    )
    return _written_pages(out_dir, stem, PNG_SUFFIX, pdf)


def check_render_budget(sizes: Sequence[tuple[float, float]], dpi: int) -> None:
    """Refuse the first page whose `(width, height)` in points passes the budget at `dpi`.

    An edge's fraction of a pixel counts as a whole one, as `pdftoppm` rounds it up.

    Raises:
        RasterError: `dpi` is not positive, or a page has no finite size or is past
            `MAX_RASTER_PIXELS`, named by its number and its size in pixels.
    """
    _require_positive_dpi(dpi)
    for page, (width_pt, height_pt) in enumerate(sizes, 1):
        if not (math.isfinite(width_pt) and math.isfinite(height_pt)):
            raise RasterError(f"page {page} has no finite size")
        width, height = _pixels(width_pt, dpi), _pixels(height_pt, dpi)
        if width * height > MAX_RASTER_PIXELS:
            raise RasterError(
                f"page {page} is {width}x{height} px at {dpi} dpi, "
                f"past the render budget of {MAX_RASTER_PIXELS:,} pixels"
            )


def render_rasters(
    pdf: Path, *, gray: bool, dpi: int, timeout: float, pages: int
) -> Iterator[Raster]:
    """Render pages 1 to `pages` of `pdf` at `dpi`, gray or color, yielding each parsed.

    One `pdftoppm` run per page, each bounded by `timeout`, and each page is rendered only
    when the one before it has been taken, so a caller keeping only what it measures holds
    one page's pixels at a time. Its file is written to a scratch folder and removed once
    read. The dpi and page count are checked here, before the first page is asked for.

    Raises:
        RasterError: `dpi` is not positive, there is no page; or, as the pages are taken,
            `pdftoppm` failed or wrote no page, or a page it wrote does not parse.
    """
    _require_positive_dpi(dpi)
    if pages < 1:
        raise RasterError(f"{pdf.name} has no page to render")
    return _each_page(pdf, gray=gray, dpi=dpi, timeout=timeout, pages=pages)


def _each_page(
    pdf: Path, *, gray: bool, dpi: int, timeout: float, pages: int
) -> Iterator[Raster]:
    """`render_rasters`' pages, rendered one `pdftoppm -singlefile` run at a time."""
    flags = ["-gray"] if gray else []
    suffix = GRAY_SUFFIX if gray else COLOR_SUFFIX
    with tempfile.TemporaryDirectory() as scratch:
        prefix = Path(scratch) / _SCRATCH_STEM
        written = prefix.with_name(_SCRATCH_STEM + suffix)
        for page in range(1, pages + 1):
            number = str(page)
            only = ["-f", number, "-l", number, "-singlefile"]
            argv = [*flags, "-r", str(dpi), *only, "--", str(pdf), str(prefix)]
            _run_pdftoppm(argv, timeout)
            if not written.is_file():
                raise RasterError(f"{PDFTOPPM} wrote no page {page} for {pdf.name}")
            data = written.read_bytes()
            written.unlink()
            yield parse_pnm(data)


def _pixels(points: float, dpi: int) -> int:
    """A length in points, as the whole pixels `pdftoppm` renders it at `dpi`."""
    return math.ceil(points * dpi / POINTS_PER_INCH)


def _require_positive_dpi(dpi: int) -> None:
    """Refuse a resolution that is zero or negative: no page has a size at it."""
    if dpi <= 0:
        raise RasterError(f"dpi {dpi}: a resolution must be positive")


def _shape(raster: Raster) -> tuple[int, int, int]:
    """Width, height and channels: what two rasters must share to be compared."""
    return raster.width, raster.height, raster.channels


def _describe(raster: Raster) -> str:
    """`8x4 gray` or `8x4 color`, for a refusal message."""
    kind = "gray" if raster.channels == GRAY_CHANNELS else "color"
    return f"{raster.width}x{raster.height} {kind}"


def _pixel_tuples(data: bytes, channels: int) -> Iterator[tuple[int, ...]]:
    """`data` grouped into one tuple per pixel."""
    return zip(*[iter(data)] * channels, strict=True)


def _value_counts(raster: Raster) -> Counter[tuple[int, ...]]:
    """How many pixels of `raster` hold each gray level or color."""
    if raster.channels == GRAY_CHANNELS:
        # Counting the bytes themselves runs in C; one tuple per pixel would not.
        return Counter({(level,): n for level, n in Counter(raster.pixels).items()})
    return Counter(_pixel_tuples(raster.pixels, raster.channels))


def _row_differences(a: Raster, b: Raster) -> Iterator[tuple[int, int, int, int]]:
    """For each row that differs: the row, its differing pixels, and the first and last column.

    Whole rows are compared first, as bytes, so an unchanged row costs no per-pixel work.
    """
    stride = a.width * a.channels
    for row in range(a.height):
        span = slice(row * stride, (row + 1) * stride)
        row_a, row_b = a.pixels[span], b.pixels[span]
        if row_a != row_b:
            pairs = zip(
                _pixel_tuples(row_a, a.channels),
                _pixel_tuples(row_b, b.channels),
                strict=True,
            )
            columns = [column for column, (p, q) in enumerate(pairs) if p != q]
            yield row, len(columns), columns[0], columns[-1]


def _to_points(pixels: int, dpi: int) -> float:
    """A distance in pixels at `dpi`, in points."""
    return round(pixels * POINTS_PER_INCH / dpi, POINT_PLACES)


def _run_pdftoppm(arguments: list[str], timeout: float) -> None:
    """Run `pdftoppm` with `arguments`, bounded by `timeout`.

    Raises:
        RasterError: it could not be started, outlived `timeout`, or exited non-zero -- the
            last naming the last line it wrote to stderr.
    """
    try:
        completed = bounded_run.run_bounded([PDFTOPPM, *arguments], timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        raise RasterError(f"{PDFTOPPM} did not finish within {timeout:g}s") from expired
    except OSError as error:
        raise RasterError(f"{PDFTOPPM} could not be started: {error}") from error
    if completed.returncode != 0:
        raise RasterError(
            f"{PDFTOPPM} exited {completed.returncode}: {_last_line(completed.stderr)}"
        )


def _last_line(text: str) -> str:
    """The last non-blank line of `text`: where poppler states the error it stopped on."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no error output"


def _numbered_pages(folder: Path, stem: str, suffix: str) -> tuple[Path, ...]:
    """The files `<stem>-<page><suffix>` in `folder`, in page order.

    Sorted by the page number parsed, never by name: pdftoppm pads the number to the page
    count's width, and a name sort is only right while every number has the same width.
    """
    pattern = re.compile(re.escape(stem) + r"-(\d+)" + re.escape(suffix))
    numbered = [
        (int(match.group(1)), path)
        for path in folder.iterdir()
        if (match := pattern.fullmatch(path.name)) is not None
    ]
    return tuple(path for _, path in sorted(numbered))


def _written_pages(folder: Path, stem: str, suffix: str, pdf: Path) -> tuple[Path, ...]:
    """The pages a run wrote, refusing a run that wrote none rather than reporting nothing."""
    pages = _numbered_pages(folder, stem, suffix)
    if not pages:
        raise RasterError(f"{PDFTOPPM} wrote no page for {pdf.name}")
    return pages
