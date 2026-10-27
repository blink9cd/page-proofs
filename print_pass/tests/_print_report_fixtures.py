"""Shared by the report's suites: records and readings built in one line, and fake readers.

`test_print_report_pdf` reads one PDF with every reader replaced through `fake_readers`, and
`test_print_report_pdf_compare` compares two; `test_print_report` shapes fixed records into
the report and checks it against the documented shape built here, which
`test_print_report_output` summarizes, normalizes and writes. Each builder returns one plain,
fixed value, and a case changes only what it is about with `dataclasses.replace`, so the
checker sees every field it sets. Nothing here runs poppler, pypdf or pdfminer: the readers'
own suites do. The rasters are real `Raster` values, so the pure readers the report calls on
them -- dominant shares, ink extent, comparison -- run for real.

Entry point assumptions: imported by a suite that `unittest` discovery loaded with this folder
as its top-level directory, AFTER that suite's bootstrap has put the tool's private `src/` on
the path, which is where every module imported below comes from.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Final, cast
from unittest import mock

import print_report_pdf
from pdf_privacy import Flag
from pdf_raster import InkExtent, Raster, Share
from pdf_read import Metadata, PdfContent
from pdf_text import Link, PdfTextError, TextLayer
from pdf_tools import FontRow, ImageRow, PageSize, PdfInfo
from print_engine import MARGIN_INCHES, MadePdf, NetworkRecord, Problem, StateRecord
from print_probe import ImageState, ProbeResult, ReadValue, RunningAnimation
from print_targets import Action, Read, State, Target, TargetsFile, Viewport

#: The resolution every read case renders at: low, so a raster a few rows tall still stands
#: taller than its two 1 cm margins.
DPI: Final = 10
TIMEOUT: Final = 5.0

#: A page raster 10 pixels wide and 20 tall: 200 pixels, so one pixel is half a percent.
WIDTH: Final = 10
HEIGHT: Final = 20
GRAY_CHANNELS: Final = 1
COLOR_CHANNELS: Final = 3

#: Paper, full ink, and a faint gray drawn on ONE pixel: under the 1% floor, yet still ink.
PAPER: Final = 255
INK: Final = 0
FAINT: Final = 128
WHITE: Final = (255, 255, 255)
RED: Final = (255, 0, 0)

#: The rows the gray raster inks: two full-ink pixels on one, the faint pixel on the other.
INK_ROW: Final = 5
FAINT_ROW: Final = 10

#: A non-ASCII glyph, built from its code point so no escape passes through an editor.
HEART: Final = chr(0x2665)

#: A message template pdfminer logs reading a font, as the text layer counts it.
PDFMINER_TEMPLATE: Final = "Could not get FontBBox from font descriptor because %r cannot be parsed as 4 floats"

#: The fixed state's names, and the file its one PDF is written to under the out dir.
TARGET: Final = "home"
STATE: Final = "default"
PDF_NAME: Final = "home--default--bg-off.pdf"
READ_NAME: Final = "mode attribute"

#: Two animations still running at print, each name and target distinct, so a mapping that
#: swapped the two fields or kept one entry for both would show.
ANIMATIONS: Final = (
    RunningAnimation("fade-in", "div#hero.card"),
    RunningAnimation("spin", "span.loader"),
)

_LINK_RECT: Final = (0.0, 0.0, 10.0, 10.0)


def gray_raster() -> Raster:
    """A gray page: paper, two ink pixels (exactly 1%) on one row, a faint one (0.5%) lower."""
    pixels = bytearray([PAPER] * WIDTH * HEIGHT)
    pixels[INK_ROW * WIDTH] = INK
    pixels[INK_ROW * WIDTH + 1] = INK
    pixels[FAINT_ROW * WIDTH] = FAINT
    return Raster(WIDTH, HEIGHT, GRAY_CHANNELS, bytes(pixels))


def color_raster(marked: int = 2) -> Raster:
    """A color page: white, with its first `marked` pixels red."""
    pixels = [*RED] * marked + [*WHITE] * (WIDTH * HEIGHT - marked)
    return Raster(WIDTH, HEIGHT, COLOR_CHANNELS, bytes(pixels))


def pdf_info() -> PdfInfo:
    """What pdfinfo says of a plain one-page Letter PDF a browser printed."""
    return PdfInfo(
        pages=1,
        page_sizes=(PageSize(612.0, 792.0, "Letter"),),
        fields={"Title": "Sample", "Creator": "Chromium", "Pages": "1"},
        title="Sample",
        creator="Chromium",
        producer="Skia/PDF m153",
        author_present=False,
        tagged=True,
        pdf_version="1.4",
        encrypted=False,
        dates={"CreationDate": "utc"},
    )


def font_row() -> FontRow:
    """An embedded subset font with a ToUnicode map: nothing to warn about."""
    return FontRow(
        name="ABCDEF+Times-Roman",
        base_name="Times-Roman",
        subset=True,
        type="CID TrueType",
        encoding="Identity-H",
        embedded=True,
        unicode=True,
    )


def image_row() -> ImageRow:
    """One 64 x 32 image on page 1 at 64 ppi."""
    return ImageRow(
        page=1,
        width=64,
        height=32,
        color="icc",
        bpc=8,
        encoding="image",
        x_ppi=64,
        y_ppi=64,
        size_bytes=203,
    )


def web_link() -> Link:
    """A link on page 1 to a public web address: nothing the privacy matcher flags."""
    return Link(1, "https://example.com/", None, _LINK_RECT)


def text_layer() -> TextLayer:
    """One page of text, no unmapped glyph, one non-ASCII glyph with its font, one log."""
    return TextLayer(
        pages=("Hello\n",),
        cid_glyphs=0,
        glyph_fonts={HEART: ("TimesNewRomanPSMT",)},
        log={PDFMINER_TEMPLATE: 2},
    )


def metadata() -> tuple[Mapping[str, str], str | None]:
    """What pypdf reads of the Info dictionary, and no XMP packet."""
    return {"Title": "Sample", "Creator": "Chromium"}, None


def _png_renders(
    pdf: Path, out_dir: Path, stem: str, *, dpi: int, timeout: float
) -> tuple[Path, ...]:
    """`render_png`'s one page, named as it names them, written nowhere."""
    return (out_dir / f"{stem}-1.png",)


def _rasters(
    pdf: Path, *, gray: bool, dpi: int, timeout: float, pages: int
) -> tuple[Raster, ...]:
    """`render_rasters`' one page, gray or color, whatever count it is asked for."""
    return (gray_raster(),) if gray else (color_raster(),)


def _defaults() -> dict[str, object]:
    """Each reader `read_pdf` calls, and what it returns (or does) unless a case says otherwise.

    `read_links`, `read_metadata` and `read_text` are not patched alone: they are what the
    one `read_content` fake answers for each, unless a case replaces `read_content` itself.
    """
    return {
        "read_info": pdf_info(),
        "read_fonts": (font_row(),),
        "read_images": (image_row(),),
        "read_links": (web_link(),),
        "read_metadata": metadata(),
        "read_text": text_layer(),
        "render_png": _png_renders,
        "render_rasters": _rasters,
    }


#: The content readers `read_content` answers for, in its field order.
_CONTENT: Final = ("read_links", "read_metadata", "read_text")


def _content(behaviors: Mapping[str, object]) -> PdfContent:
    """What the `read_content` fake returns: each content reader's value or failure."""
    # Each case hands a value of the reader's own type, or a PdfTextError: cast, not checked.
    return PdfContent(
        links=cast("tuple[Link, ...] | PdfTextError", behaviors["read_links"]),
        metadata=cast("Metadata | PdfTextError", behaviors["read_metadata"]),
        text=cast("TextLayer | PdfTextError", behaviors["read_text"]),
    )


@contextmanager
def fake_readers(**behaviors: object) -> Iterator[dict[str, mock.MagicMock]]:
    """Replace every reader `read_pdf` calls; yield the fakes by name, to check their calls.

    A behavior is what the reader returns, or an exception it raises, or a function it runs
    in its place. A content reader's behavior is a value or a `PdfTextError`, which the
    one `read_content` fake hands back as that reader's outcome.
    """
    fakes: dict[str, mock.MagicMock] = {}
    chosen = {
        name: behaviors.pop(name, default) for name, default in _defaults().items()
    }
    chosen["read_content"] = behaviors.pop("read_content", _content(chosen))
    with ExitStack() as stack:
        for name, behavior in chosen.items():
            if name in _CONTENT:
                continue
            fake = mock.MagicMock(name=name)
            if isinstance(behavior, BaseException) or callable(behavior):
                fake.side_effect = behavior
            else:
                fake.return_value = behavior
            stack.enter_context(mock.patch.object(print_report_pdf, name, fake))
            fakes[name] = fake
        if behaviors:
            raise TypeError(f"no reader is named {sorted(behaviors)}")
        yield fakes


def probe_result() -> ProbeResult:
    """A read-back of a light page: one lazy image, two animations, one stored key unset."""
    return ProbeResult(
        color_scheme="light",
        reduced_motion="no-preference",
        ready_state="complete",
        fonts_status="loaded",
        title="Sample",
        images=(ImageState("/a.png", True, "lazy", 64, has_src=True),),
        animations=ANIMATIONS,
        reads=(ReadValue(READ_NAME, ("light",)),),
        storage=(("mode", None),),
    )


def state_record(out_dir: Path) -> StateRecord:
    """One default state of `home`, printed backgrounds off into `<out_dir>/pdfs`."""
    return StateRecord(
        target=TARGET,
        state=STATE,
        page="/",
        engine="shell",
        browser_version="153.0.7000.0",
        viewport=Viewport(1280, 800),
        color_scheme="native",
        color_scheme_emulated=False,
        reduced_motion="native",
        reduced_motion_emulated=False,
        storage=(("mode", "dark"),),
        actions=(Action("click", "#menu-toggle", None),),
        print_at_ms=None,
        page_width=None,
        margin_inches=MARGIN_INCHES,
        probe=probe_result(),
        network=NetworkRecord((), (), (), (), ()),
        load_ms=120,
        printed_at_ms=480,
        pdfs=(MadePdf(False, out_dir / "pdfs" / PDF_NAME, MARGIN_INCHES),),
        problems=(Problem("warning", "fonts not loaded at print: loading"),),
    )


def targets(*reads: Read, hosts: tuple[str, ...] = ()) -> TargetsFile:
    """A targets file of the one target `state_record` prints, holding `reads`.

    `hosts` are the off-loopback hosts it expects; it expects none unless they are given.
    """
    state = State(STATE, "native", "native", (), (), None)
    return TargetsFile(
        viewport=None,
        backgrounds=None,
        expected_offsite_hosts=hosts,
        targets=(Target(TARGET, "/", (), reads, (state,)),),
    )


def read(name: str = READ_NAME, *, every: bool = False) -> Read:
    """A read of the root's `data-mode`: the first match, or every match with `every`."""
    return Read(name, ":root", "attribute", "data-mode", every)


def pdf_report() -> print_report_pdf.PdfReport:
    """What `read_pdf` makes of the fixed state's PDF: one page, one of everything, one flag."""
    return print_report_pdf.PdfReport(
        file=f"pdfs/{PDF_NAME}",
        dpi=96,
        info=pdf_info(),
        fonts=(font_row(),),
        images=(image_row(),),
        links=(web_link(),),
        text=text_layer(),
        text_file="text/home--default--bg-off.txt",
        renders=("renders/home--default--bg-off-1.png",),
        gray_levels=((Share((255,), 0.93),),),
        colors=((Share(WHITE, 0.93),),),
        ink=(InkExtent(10, 500, 0.35),),
        flags=(Flag("metadata:Author", "names a person"),),
        problems=(Problem("finding", "metadata:Author: names a person"),),
    )


def comparison() -> dict[str, object]:
    """What `compare_pdfs` returns for the fixed state's two PDFs: page 1 differs."""
    return {
        "a": f"pdfs/{PDF_NAME}",
        "b": "pdfs/home--default--bg-on.pdf",
        "identical": False,
        "page_count": [1, 1],
        "pages": [
            {
                "page": 1,
                "identical": False,
                "differing_pixels": 1200,
                "bbox_pt": [0.0, 0.0, 612.0, 792.0],
            }
        ],
    }


def state_json() -> dict[str, object]:
    """The report's entry for `state_record`, as documented."""
    return {
        "target": TARGET,
        "state": STATE,
        "page": "/",
        "engine": "shell",
        "browser_version": "153.0.7000.0",
        "viewport": {"width": 1280, "height": 800},
        "color_scheme": {"requested": "native", "emulated": False},
        "reduced_motion": {"requested": "native", "emulated": False},
        "storage": {"mode": "dark"},
        "actions": ["click #menu-toggle"],
        "print_at_ms": None,
        # Printed at the engine's own margin: Chromium's 1 cm, in CSS px to 4 places.
        "page_width": None,
        "margin_px": 37.7953,
        "read_back": {
            "color_scheme": "light",
            "reduced_motion": "no-preference",
            "ready_state": "complete",
            "fonts_status": "loaded",
            "title": "Sample",
            "images": [
                {
                    "src": "/a.png",
                    "has_src": True,
                    "complete": True,
                    "loading": "lazy",
                    "natural_width": 64,
                }
            ],
            # Literal, not built from ANIMATIONS: a built copy would restate the mapping.
            "animations": [
                {"name": "fade-in", "target": "div#hero.card"},
                {"name": "spin", "target": "span.loader"},
            ],
            "reads": {READ_NAME: ["light"]},
            "storage": {"mode": None},
        },
        # Every list is present, empty or not: an absent one could not be told from one
        # never measured.
        "network": {
            "failed": [],
            "http_errors": [],
            "blocked": [],
            "page_errors": [],
            "csp_violations": [],
        },
        "timings": {"load_ms": 120, "printed_at_ms": 480},
        "pdfs": [f"pdfs/{PDF_NAME}"],
        "problems": [
            {"severity": "warning", "what": "fonts not loaded at print: loading"}
        ],
    }


def pdf_json() -> dict[str, object]:
    """The report's entry for `pdf_report`, as documented."""
    return {
        "file": f"pdfs/{PDF_NAME}",
        "pages": 1,
        "page_sizes": [{"width_pt": 612.0, "height_pt": 792.0, "name": "Letter"}],
        "metadata": {
            "title": "Sample",
            "creator": "Chromium",
            "producer": "Skia/PDF m153",
            "tagged": True,
            "pdf_version": "1.4",
            "encrypted": False,
            "author": "absent",
            "dates": {"CreationDate": "utc"},
        },
        "fonts": [
            {
                "base_name": "Times-Roman",
                "subset": True,
                "type": "CID TrueType",
                "encoding": "Identity-H",
                "embedded": True,
                "unicode": True,
            }
        ],
        "images": {
            "items": [
                {
                    "page": 1,
                    "width": 64,
                    "height": 32,
                    "color": "icc",
                    "bpc": 8,
                    "encoding": "image",
                    "x_ppi": 64,
                    "y_ppi": 64,
                    "size_bytes": 203,
                }
            ],
            "min_ppi": 64,
            "max_ppi": 64,
        },
        "links": [
            {
                "page": 1,
                "uri": "https://example.com/",
                "destination": None,
                "rect": [0.0, 0.0, 10.0, 10.0],
            }
        ],
        "text": {
            "file": "text/home--default--bg-off.txt",
            "chars_per_page": [6],
            "cid_glyphs": 0,
            "non_ascii_glyph_fonts": {HEART: ["TimesNewRomanPSMT"]},
            "log": {PDFMINER_TEMPLATE: 2},
        },
        "renders": {
            "dpi": 96,
            "files": ["renders/home--default--bg-off-1.png"],
            "gray_levels": [[{"level": 255, "share": 0.93}]],
            "colors": [[{"rgb": [255, 255, 255], "share": 0.93}]],
            "blank_below_last_ink": [0.35],
        },
        "privacy": [{"where": "metadata:Author", "what": "names a person"}],
        "problems": [
            {"severity": "finding", "what": "metadata:Author: names a person"}
        ],
    }


def make_json() -> dict[str, object]:
    """The whole make report for the fixed state, its PDF and its one comparison."""
    return {
        "tool": "print_pass",
        "schema": 2,
        "mode": "make",
        "print": {
            "format": "Letter",
            "margin": "1cm",
            "tagged": True,
            "header_footer": False,
            "prefer_css_page_size": False,
            "scale": 1,
        },
        "targets": {"expected_offsite_hosts": []},
        "states": [state_json()],
        "pdfs": [pdf_json()],
        "comparisons": [comparison()],
        "totals": {"states": 1, "pdfs": 1, "findings": 1, "warnings": 1},
    }
