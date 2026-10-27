"""Suite: read_pdf runs every reader over one PDF, turning each failure into a finding.

Every reader is replaced through `fake_readers`, so a case says only what one reader returns
or raises; the rasters are real, so the dominant shares and ink extent the report takes from
them run for real. Comparing two PDFs and the report's paths are the sibling
`test_print_report_pdf_compare`'s; the scrub is `test_print_report_scrub`'s.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Final
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pdf_privacy  # noqa: E402 - the tool's private src/, via the insert above
import print_report_pdf  # noqa: E402 - as above
from _pdf_privacy_fixtures import (  # noqa: E402 - beside this module
    HOME,
    HOSTNAMES,
    IDENTITY,
    LOGIN,
    SOMEONE,
    USERS,
    USERS_PATH,
)
from _print_report_fixtures import (  # noqa: E402 - as above
    DPI,
    FAINT_ROW,
    PDF_NAME,
    RED,
    TIMEOUT,
    WHITE,
    color_raster,
    fake_readers,
    font_row,
    gray_raster,
    image_row,
    pdf_info,
    text_layer,
    web_link,
)
from pdf_raster import Raster, RasterError, Share, ink_extent  # noqa: E402 - src/
from pdf_text import Link, PdfTextError  # noqa: E402 - src/
from pdf_tools import PdfInfo, PopplerError  # noqa: E402 - src/
from print_engine import MARGIN_INCHES, Problem  # noqa: E402 - src/

_STEM: Final = PDF_NAME.removesuffix(".pdf")
_TEXT_FILE: Final = f"text/{_STEM}.txt"
_AUTHOR_FLAG: Final = pdf_privacy.Flag("metadata:Author", pdf_privacy.NAMES_A_PERSON)


def _short_identity(login: str, *, hostname: str | None = None) -> pdf_privacy.Identity:
    """An account whose login or hostname is short enough to sit inside an Info field's name.

    Its home is assembled from parts, as every fixture home is.
    """
    return pdf_privacy.Identity(
        login=login,
        full_name=None,
        hostnames=() if hostname is None else (hostname,),
        home="/" + USERS + "/" + login,
    )


def _printing(field: str, value: str) -> PdfInfo:
    """`pdf_info()` with the printed Info field `field` holding `value`."""
    info = pdf_info()
    return {
        "Title": dataclasses.replace(info, title=value),
        "Creator": dataclasses.replace(info, creator=value),
        "Producer": dataclasses.replace(info, producer=value),
    }[field]


def _printed(info: PdfInfo | None, field: str) -> str | None:
    """What the report prints for the Info field `field`."""
    assert info is not None
    printed = {"Title": info.title, "Creator": info.creator, "Producer": info.producer}
    return printed[field]


class ReadCase(unittest.TestCase):
    """A fresh out dir per case, and the PDF `read_pdf` is pointed at inside it."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)
        self.pdf = self.out / "pdfs" / PDF_NAME

    def read(
        self, pdf: Path | None = None, *, identity: pdf_privacy.Identity = IDENTITY
    ) -> print_report_pdf.PdfReport:
        return print_report_pdf.read_pdf(
            self.pdf if pdf is None else pdf,
            self.out,
            dpi=DPI,
            timeout=TIMEOUT,
            identity=identity,
            margin_inches=MARGIN_INCHES,
        )


class HappyPathTests(ReadCase):
    def test_every_reader_reaches_the_report(self) -> None:
        with fake_readers():
            report = self.read()
        self.assertEqual(report.file, f"pdfs/{PDF_NAME}")
        self.assertEqual(report.dpi, DPI)
        self.assertEqual(report.info, pdf_info())
        self.assertEqual(report.fonts, (font_row(),))
        self.assertEqual(report.images, (image_row(),))
        self.assertEqual(report.links, (web_link(),))
        self.assertEqual(report.text, text_layer())
        self.assertEqual(report.text_file, _TEXT_FILE)
        self.assertEqual(report.renders, (f"renders/{_STEM}-1.png",))
        self.assertEqual(report.flags, ())
        self.assertEqual(report.problems, ())

    def test_levels_and_colors_keep_the_one_percent_floor(self) -> None:
        # The faint pixel is half a percent of the page: under the floor, so not a level.
        with fake_readers():
            report = self.read()
        self.assertEqual(
            report.gray_levels, ((Share((255,), 0.985), Share((0,), 0.01)),)
        )
        self.assertEqual(report.colors, ((Share(WHITE, 0.99), Share(RED, 0.01)),))

    def test_ink_is_read_from_the_gray_render_faint_ink_included(self) -> None:
        with fake_readers():
            report = self.read()
        expected = ink_extent(gray_raster(), DPI, margin_inches=MARGIN_INCHES)
        self.assertEqual(report.ink, (expected,))
        self.assertEqual(report.ink[0].last_ink_row, FAINT_ROW)

    def test_the_readers_run_in_order_bounded_by_the_timeout(self) -> None:
        with fake_readers() as fakes:
            order = mock.Mock()
            for name, fake in fakes.items():
                order.attach_mock(fake, name)
            self.read()
        self.assertEqual(
            order.mock_calls,
            [
                mock.call.read_info(self.pdf, timeout=TIMEOUT),
                mock.call.read_fonts(self.pdf, timeout=TIMEOUT),
                mock.call.read_images(self.pdf, timeout=TIMEOUT),
                mock.call.read_content(self.pdf, timeout=TIMEOUT),
                mock.call.render_png(
                    self.pdf, self.out / "renders", _STEM, dpi=DPI, timeout=TIMEOUT
                ),
                mock.call.render_rasters(
                    self.pdf, gray=True, dpi=DPI, timeout=TIMEOUT, pages=1
                ),
                mock.call.render_rasters(
                    self.pdf, gray=False, dpi=DPI, timeout=TIMEOUT, pages=1
                ),
            ],
        )

    def test_the_text_layer_is_written_page_after_page(self) -> None:
        layer = dataclasses.replace(text_layer(), pages=("One\n", "Two\n"))
        with fake_readers(read_text=layer):
            self.read()
        written = (self.out / _TEXT_FILE).read_bytes()
        self.assertEqual(written, "One\n\fTwo\n".encode())

    def test_a_pdf_outside_the_out_dir_is_named_by_its_name_alone(self) -> None:
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        with fake_readers():
            report = self.read(Path(elsewhere.name) / PDF_NAME)
        self.assertEqual(report.file, PDF_NAME)


class EncryptedTests(ReadCase):
    def encrypted_info(self, **fields: str) -> PdfInfo:
        return dataclasses.replace(
            pdf_info(), encrypted=True, fields={"Encrypted": "yes", **fields}
        )

    def test_an_encrypted_pdf_skips_links_metadata_and_text(self) -> None:
        with fake_readers(read_info=self.encrypted_info()) as fakes:
            report = self.read()
        fakes["read_content"].assert_not_called()
        self.assertEqual(
            report.problems,
            (Problem("warning", "encrypted: text and links not read"),),
        )
        self.assertEqual(
            (report.links, report.text, report.text_file), ((), None, None)
        )
        self.assertEqual(report.fonts, (font_row(),))
        self.assertEqual(report.renders, (f"renders/{_STEM}-1.png",))

    def test_privacy_reads_pdfinfos_fields_when_encrypted(self) -> None:
        info = self.encrypted_info(Author="Someone")
        spy = mock.Mock(wraps=pdf_privacy.privacy_flags)
        with (
            fake_readers(read_info=info),
            mock.patch.object(print_report_pdf, "privacy_flags", spy),
        ):
            report = self.read()
        self.assertEqual(spy.call_args_list[0].kwargs["info"], info.fields)
        self.assertEqual(report.flags, (_AUTHOR_FLAG,))


class PrivacyTests(ReadCase):
    def test_privacy_reads_pypdfs_info_dictionary(self) -> None:
        spy = mock.Mock(wraps=pdf_privacy.privacy_flags)
        read = ({"Author": "Someone"}, "<x:xmpmeta/>")
        with (
            fake_readers(read_metadata=read),
            mock.patch.object(print_report_pdf, "privacy_flags", spy),
        ):
            report = self.read()
        call = spy.call_args_list[0]
        self.assertEqual(call.args, (IDENTITY,))
        self.assertEqual(
            call.kwargs,
            {
                "info": {"Author": "Someone"},
                "xmp": "<x:xmpmeta/>",
                "links": (web_link(),),
                "text_pages": ("Hello\n",),
            },
        )
        self.assertEqual(report.flags, (_AUTHOR_FLAG,))

    def test_a_failed_metadata_read_falls_back_to_pdfinfos_fields(self) -> None:
        info = dataclasses.replace(pdf_info(), fields={"Author": "Someone"})
        with fake_readers(read_info=info, read_metadata=PdfTextError("broken")):
            report = self.read()
        self.assertEqual(report.flags, (_AUTHOR_FLAG,))

    def test_with_neither_metadata_nor_info_nothing_is_searched_there(self) -> None:
        with fake_readers(
            read_info=PopplerError("no"), read_metadata=PdfTextError("no")
        ):
            report = self.read()
        self.assertEqual(report.flags, ())

    def test_each_flag_is_also_a_finding_after_the_readers_problems(self) -> None:
        font = dataclasses.replace(font_row(), embedded=False)
        with fake_readers(
            read_fonts=(font,), read_metadata=({"Author": "Someone"}, None)
        ):
            report = self.read()
        self.assertEqual(
            report.problems,
            (
                Problem("warning", "font not embedded: Times-Roman"),
                Problem("finding", "metadata:Author: names a person"),
            ),
        )

    def test_a_flagged_title_creator_or_producer_is_withheld(self) -> None:
        info = dataclasses.replace(pdf_info(), title=HOME, producer=HOME)
        read = ({"Title": HOME, "Producer": HOME, "Creator": "Chromium"}, None)
        with fake_readers(read_info=info, read_metadata=read):
            report = self.read()
        assert report.info is not None
        self.assertEqual(
            (report.info.title, report.info.creator, report.info.producer),
            (print_report_pdf.WITHHELD, "Chromium", print_report_pdf.WITHHELD),
        )

    def test_a_field_whose_own_name_is_flagged_is_withheld_when_its_value_is(
        self,
    ) -> None:
        # A login or hostname inside the field's NAME moves every flag on that field to the
        # neutral place, which names no field: the printed value must be judged alone.
        cases = (
            ("Title", _short_identity("it")),
            ("Creator", _short_identity("at")),
            ("Producer", _short_identity("du")),
            ("Producer", _short_identity(SOMEONE, hostname="pro")),
        )
        for field, identity in cases:
            with self.subTest(field=field, identity=identity):
                value = f"Notes kept in {identity.home}/notes"
                info = _printing(field, value)
                with fake_readers(read_info=info, read_metadata=({field: value}, None)):
                    report = self.read(identity=identity)
                self.assertEqual(
                    _printed(report.info, field), print_report_pdf.WITHHELD
                )

    def test_the_title_is_judged_by_the_title_rules_the_creator_by_its_own(
        self,
    ) -> None:
        # The login flags a Creator, never a Title, which carries the site author's name.
        value = f"Notes by {LOGIN}"
        info = dataclasses.replace(pdf_info(), title=value, creator=value)
        with fake_readers(read_info=info):
            report = self.read()
        assert report.info is not None
        self.assertEqual(
            (report.info.title, report.info.creator), (value, print_report_pdf.WITHHELD)
        )

    def test_an_absent_title_creator_or_producer_stays_absent(self) -> None:
        info = dataclasses.replace(pdf_info(), title=None, creator=None, producer=None)
        with fake_readers(read_info=info):
            report = self.read()
        assert report.info is not None
        self.assertEqual(
            (report.info.title, report.info.creator, report.info.producer),
            (None, None, None),
        )

    def test_a_flagged_link_is_withheld_and_the_others_kept(self) -> None:
        home_link = Link(2, "file://" + USERS_PATH + "/a.html", None, (1, 2, 3, 4))
        internal = Link(2, None, "[ 5 0 R /Fit ]", (1, 2, 3, 4))
        with fake_readers(read_links=(web_link(), home_link, internal)):
            report = self.read()
        self.assertEqual(
            report.links,
            (
                web_link(),
                dataclasses.replace(home_link, uri=print_report_pdf.WITHHELD),
                internal,
            ),
        )
        self.assertIn(
            Problem("finding", "link on page 2: a home-rooted path"), report.problems
        )


class ProblemTests(ReadCase):
    def test_a_font_not_embedded_or_without_tounicode_is_a_warning(self) -> None:
        bare = dataclasses.replace(font_row(), embedded=False, unicode=False)
        with fake_readers(read_fonts=(font_row(), bare)):
            report = self.read()
        self.assertEqual(
            report.problems,
            (
                Problem("warning", "font not embedded: Times-Roman"),
                Problem("warning", "font without ToUnicode: Times-Roman"),
            ),
        )

    def test_unmapped_glyphs_are_a_warning(self) -> None:
        layer = dataclasses.replace(text_layer(), cid_glyphs=3)
        with fake_readers(read_text=layer):
            report = self.read()
        self.assertEqual(
            report.problems,
            (Problem("warning", "unmapped glyphs in the text layer: 3 (cid:N)"),),
        )

    def test_each_reader_failure_is_a_finding_and_the_rest_still_run(self) -> None:
        failures = (
            ("read_fonts", PopplerError("pdffonts exited 1"), "fonts", "fonts"),
            ("read_images", PopplerError("pdfimages hung"), "images", "images"),
            ("read_links", PdfTextError("cannot read links"), "links", "links"),
            ("read_metadata", PdfTextError("no Info"), "metadata", None),
            ("read_text", PdfTextError("no text"), "text", "text"),
            ("render_png", RasterError("pdftoppm exited 1"), "png renders", "renders"),
        )
        for reader, error, step, emptied in failures:
            with self.subTest(reader=reader), fake_readers(**{reader: error}) as fakes:
                report = self.read_again()
                self.assertEqual(
                    report.problems, (Problem("finding", f"{step} failed: {error}"),)
                )
                for name, fake in fakes.items():
                    self.assertTrue(fake.called, name)
                if emptied is not None:
                    self.assertIn(getattr(report, emptied), ((), None))
                self.assertEqual(report.gray_levels[0][0], Share((255,), 0.985))

    def read_again(self) -> print_report_pdf.PdfReport:
        # Each subtest renders again into the same out dir, so the renders go to a fresh one.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)
        return self.read(self.out / "pdfs" / PDF_NAME)

    def test_a_failed_gray_render_empties_levels_and_ink_alone(self) -> None:
        def rasters(pdf: Path, *, gray: bool, **_: object) -> tuple[Raster, ...]:
            if gray:
                raise RasterError("pdftoppm did not finish within 5s")
            return (color_raster(),)

        with fake_readers(render_rasters=rasters):
            report = self.read()
        self.assertEqual(
            report.problems,
            (
                Problem(
                    "finding", "gray render failed: pdftoppm did not finish within 5s"
                ),
            ),
        )
        self.assertEqual((report.gray_levels, report.ink), ((), ()))
        self.assertEqual(len(report.colors), 1)

    def test_a_failed_color_render_empties_colors_alone(self) -> None:
        def rasters(pdf: Path, *, gray: bool, **_: object) -> tuple[Raster, ...]:
            if not gray:
                raise RasterError("pdftoppm exited 1: no page")
            return (gray_raster(),)

        with fake_readers(render_rasters=rasters):
            report = self.read()
        self.assertEqual(
            report.problems,
            (Problem("finding", "color render failed: pdftoppm exited 1: no page"),),
        )
        self.assertEqual((report.colors, len(report.gray_levels)), ((), 1))

    def test_a_page_too_short_for_its_margins_fails_the_ink_step(self) -> None:
        short = Raster(2, 2, 1, bytes(4))
        with fake_readers(render_rasters=lambda pdf, **kw: (short,)):
            report = self.read()
        self.assertEqual(len(report.problems), 1)
        self.assertTrue(report.problems[0].what.startswith("ink extent failed: "))
        self.assertEqual((report.ink, len(report.gray_levels)), ((), 1))

    def test_a_text_file_already_there_is_refused_not_overwritten(self) -> None:
        existing = self.out / _TEXT_FILE
        existing.parent.mkdir(parents=True)
        existing.write_text("an earlier PDF's text", encoding="utf-8", newline="\n")
        with fake_readers():
            report = self.read()
        self.assertEqual(
            report.problems,
            (Problem("finding", f"text failed: text already holds {_STEM}.txt"),),
        )
        self.assertEqual((report.text, report.text_file), (text_layer(), None))
        self.assertEqual(existing.read_text(encoding="utf-8"), "an earlier PDF's text")

    def test_a_failure_keeps_the_first_line_of_its_message(self) -> None:
        with fake_readers(read_images=PopplerError("first\nsecond")):
            report = self.read()
        self.assertEqual(report.problems, (Problem("finding", "images failed: first"),))

    def test_every_problem_text_is_scrubbed(self) -> None:
        message = (
            f"cannot open {self.out}/pdfs/a.pdf, {HOME}/b.pdf or {USERS_PATH}/c.pdf"
        )
        with fake_readers(read_info=PopplerError(message)):
            report = self.read()
        self.assertEqual(
            report.problems[0].what,
            "info failed: cannot open <out>/pdfs/a.pdf, ~/b.pdf or <home>/c.pdf",
        )

    def test_each_pdfminer_log_template_is_scrubbed_and_those_alike_merged(
        self,
    ) -> None:
        # A library's text, as a problem's is; two that scrub alike are one template.
        log = {
            f"font at {HOME}/a.ttf: %r": 2,
            f"font at {HOME.upper()}/a.ttf: %r": 1,
            f"on {HOSTNAMES[1]}: %s": 4,
        }
        with fake_readers(read_text=dataclasses.replace(text_layer(), log=log)):
            report = self.read()
        assert report.text is not None
        self.assertEqual(
            list(report.text.log.items()),
            [("font at ~/a.ttf: %r", 3), ("on <hostname>: %s", 4)],
        )

    def test_a_problem_text_naming_this_machines_hostname_is_scrubbed(self) -> None:
        message = f"cannot reach {HOSTNAMES[1]}: refused"
        with fake_readers(read_info=PopplerError(message)):
            report = self.read()
        self.assertEqual(
            report.problems[0].what, "info failed: cannot reach <hostname>: refused"
        )

    def test_an_error_no_reader_raises_is_not_swallowed(self) -> None:
        with fake_readers(read_fonts=KeyError("a bug")), self.assertRaises(KeyError):
            self.read()


if __name__ == "__main__":
    unittest.main()
