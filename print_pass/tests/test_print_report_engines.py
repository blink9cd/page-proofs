"""Suite: the report's `print` entry and `cannot_observe` list, per engine, and a Firefox PDF.

The shell and installed Chrome print through Playwright's `page.pdf()`, and their report is
what it always was, to the byte: the committed sample's report holds their entry. Firefox
prints through WebDriver's Print, so its entry is read from the parameters that command is
sent, with what the command cannot set beside them, and a parameter the report has no name
for fails the report; its report alone also says what its network record cannot list. A
make run hands the report the engine its settings name. And a PDF made the way Firefox's
WebDriver Print makes them -- untagged, with no `Title` -- is no problem for the readers on
either count.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/` and the tool's own folder,
and discovery's top-level directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Final
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
sys.path.insert(0, str(_TOOL_DIR))

import gecko_network  # noqa: E402 - the tool's private src/, via the insert above
import gecko_print  # noqa: E402 - as above
import print_engine  # noqa: E402 - as above
import print_preflight  # noqa: E402 - as above
import print_report  # noqa: E402 - as above
from _pdf_privacy_fixtures import IDENTITY  # noqa: E402 - beside this module
from _print_pass_fixtures import RunCase, run_main  # noqa: E402 - as above
from _print_report_fixtures import (  # noqa: E402 - as above
    DPI,
    PDF_NAME,
    TIMEOUT,
    comparison,
    fake_readers,
    gray_raster,
    make_json,
    pdf_info,
    pdf_report,
    read,
    state_record,
    targets,
)
from pdf_raster import ink_extent  # noqa: E402 - the tool's private src/
from print_record import Engine  # noqa: E402 - as above

#: The committed sample's report, whose `print` entry the shell's must match to the byte.
_SAMPLE_REPORT: Final = _TOOL_DIR / "samples" / "expected_report.json"

#: What the Quartz writer names itself in a PDF Firefox printed on macOS.
_QUARTZ: Final = "Quartz PDFContext"


def _make(
    out: Path, engine: Engine, *, header_footer: bool = False
) -> dict[str, object]:
    """The make report of the fixed state, printed by `engine`."""
    return print_report.make_report(
        [state_record(out)],
        {out / "pdfs" / PDF_NAME: pdf_report()},
        [comparison()],
        out_dir=out,
        targets=targets(read()),
        engine=engine,
        header_footer=header_footer,
        identity=IDENTITY,
    )


def _text(value: object) -> str:
    """`value` as `report.json` writes it: sorted keys, so two equal entries read the same."""
    return json.dumps(value, sort_keys=True, indent=2)


class _OutCase(unittest.TestCase):
    """A fresh out dir per case."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)


class PlaywrightEntryTests(_OutCase):
    """The shell's and Chrome's entry is the one the sample has always held."""

    def test_each_playwright_engine_writes_the_documented_entry_to_the_byte(
        self,
    ) -> None:
        for engine in ("shell", "chrome"):
            with self.subTest(engine=engine):
                report = _make(self.out, engine)
                self.assertEqual(_text(report["print"]), _text(make_json()["print"]))

    def test_the_shells_entry_is_the_committed_samples(self) -> None:
        sample = json.loads(_SAMPLE_REPORT.read_text(encoding="utf-8"))
        self.assertEqual(
            _text(_make(self.out, "shell")["print"]), _text(sample["print"])
        )


class FirefoxEntryTests(_OutCase):
    """Firefox's entry: the WebDriver Print parameters sent, and what they cannot set."""

    def test_the_entry_names_each_parameter_and_what_the_command_cannot_set(
        self,
    ) -> None:
        expected = {
            "page_cm": {"width": 21.59, "height": 27.94},
            "margin_cm": {"top": 1.27, "bottom": 1.27, "left": 1.27, "right": 1.27},
            "scale": 1,
            "shrink_to_fit": True,
            "orientation": "portrait",
            "tagged": False,
            "header_footer": False,
            "cannot_control": list(gecko_print.CANNOT_CONTROL),
        }
        self.assertEqual(_text(_make(self.out, "firefox")["print"]), _text(expected))

    def test_the_entry_is_read_from_the_parameters_sent_without_the_per_pdf_one(
        self,
    ) -> None:
        body = {
            "page": {"width": 10.0, "height": 20.0},
            "margin": {"top": 2.0, "bottom": 2.0, "left": 3.0, "right": 3.0},
            "background": True,
            "scale": 0.5,
            "shrinkToFit": False,
            "orientation": "landscape",
        }
        with mock.patch.object(
            gecko_print, "print_parameters", return_value=body
        ) as sent:
            entry = _make(self.out, "firefox")["print"]
        sent.assert_called_once_with(backgrounds=False)
        self.assertEqual(
            entry,
            {
                "page_cm": body["page"],
                "margin_cm": body["margin"],
                "scale": 0.5,
                "shrink_to_fit": False,
                "orientation": "landscape",
                "tagged": False,
                "header_footer": False,
                "cannot_control": list(gecko_print.CANNOT_CONTROL),
            },
        )

    def test_everything_but_the_entry_and_its_limits_is_as_any_engine_reports_it(
        self,
    ) -> None:
        firefox, shell = _make(self.out, "firefox"), _make(self.out, "shell")
        del firefox["print"], firefox["cannot_observe"], shell["print"]
        self.assertEqual(_text(firefox), _text(shell))

    def test_a_header_and_footer_asked_of_firefox_is_refused(self) -> None:
        # WebDriver's Print never prints one, so the entry could only misstate the run.
        with self.assertRaises(ValueError):
            _make(self.out, "firefox", header_footer=True)

    def test_a_parameter_sent_with_no_report_name_fails_the_report(self) -> None:
        # Left out, it would vanish from the entry that says how every page was printed.
        body = {**gecko_print.print_parameters(backgrounds=False), "pageRanges": ["1"]}
        with (
            mock.patch.object(gecko_print, "print_parameters", return_value=body),
            self.assertRaises(KeyError) as caught,
        ):
            _make(self.out, "firefox")
        self.assertEqual(caught.exception.args, ("pageRanges",))


class CannotObserveTests(_OutCase):
    """A Firefox report says what its network record cannot list; no other report changes."""

    def test_a_firefox_report_carries_what_its_engine_cannot_observe(self) -> None:
        report = _make(self.out, "firefox")
        self.assertEqual(report["cannot_observe"], list(gecko_network.CANNOT_OBSERVE))

    def test_each_playwright_engines_report_is_unchanged_to_the_byte(self) -> None:
        for engine in ("shell", "chrome"):
            with self.subTest(engine=engine):
                report = _make(self.out, engine)
                self.assertNotIn("cannot_observe", report)
                self.assertEqual(_text(report), _text(make_json()))


class _NoStates:
    """Stands in for `run_targets`: prints nothing, and returns no record."""

    def __call__(self, *args: object, **kwargs: object) -> tuple[()]:
        """No state printed."""
        return ()


@contextlib.contextmanager
def _fake_sync_playwright() -> Iterator[object]:
    """`sync_playwright()` as the fake engine needs it: a context yielding a stand-in."""
    yield object()


class MakeRunTests(RunCase):
    """A make run hands the report the engine its settings name."""

    def test_the_report_is_handed_the_engine_the_run_printed_with(self) -> None:
        with (
            mock.patch.object(print_engine, "run_targets", _NoStates()),
            mock.patch.object(
                print_preflight,
                "import_sync_playwright",
                return_value=_fake_sync_playwright,
            ),
            mock.patch.object(
                print_report, "make_report", wraps=print_report.make_report
            ) as made,
        ):
            outcome = run_main(
                *("--mode", "make", "--out", str(self.out), "--serve", str(self.site)),
                *("--engine", "chrome", "--browsers", str(self.builds)),
            )
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(made.call_args.kwargs["engine"], "chrome")


class FirefoxPdfTests(_OutCase):
    """A PDF as Firefox's WebDriver Print writes it raises no problem for what it lacks."""

    def test_an_untagged_pdf_with_no_title_is_read_without_a_problem(self) -> None:
        firefox_info = dataclasses.replace(
            pdf_info(),
            fields={"Creator": "Firefox", "Producer": _QUARTZ, "Pages": "1"},
            title=None,
            creator="Firefox",
            producer=_QUARTZ,
            tagged=False,
        )
        metadata = ({"Creator": "Firefox", "Producer": _QUARTZ}, None)
        with fake_readers(read_info=firefox_info, read_metadata=metadata):
            report = self.read_firefox_pdf()
        self.assertEqual(report.problems, ())
        self.assertEqual(report.flags, ())
        self.assertEqual(report.info, firefox_info)

    def test_its_blank_share_is_measured_against_the_half_inch_it_printed(self) -> None:
        # The regression: a Firefox PDF was measured against Chromium's 1 cm, and at 10 dpi
        # the fixed page's blank share below its last ink reads 0.4 at 0.5 in, 0.4175 at 1 cm.
        with fake_readers():
            report = self.read_firefox_pdf()
        half_inch = ink_extent(
            gray_raster(), DPI, margin_inches=gecko_print.MARGIN_INCHES
        )
        one_cm = ink_extent(
            gray_raster(), DPI, margin_inches=print_engine.MARGIN_INCHES
        )
        self.assertEqual(report.ink, (half_inch,))
        self.assertEqual(half_inch.blank_below_share, 0.4)
        self.assertNotEqual(half_inch, one_cm)

    def read_firefox_pdf(self) -> print_report.PdfReport:
        """Read the fixed PDF as a Firefox make run reads one: against Firefox's margin."""
        return print_report.read_pdf(
            self.out / "pdfs" / PDF_NAME,
            self.out,
            dpi=DPI,
            timeout=TIMEOUT,
            identity=IDENTITY,
            margin_inches=gecko_print.MARGIN_INCHES,
        )


if __name__ == "__main__":
    unittest.main()
