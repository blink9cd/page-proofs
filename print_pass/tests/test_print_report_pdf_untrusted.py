"""Suite: read_pdf keeps going on what an untrusted PDF hands its readers.

A PDF is the page's, or whoever made it: its text may hold what no UTF-8 can encode, and
its structure may crash pypdf or pdfminer. Every reader but those two libraries' is replaced
through `fake_readers`, as in the sibling `test_print_report_pdf`, whose file has no room
left for these cases; theirs run for real, in their bounded worker, over the crash classes
`_pdf_read_fixtures` keeps.

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

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_report_pdf  # noqa: E402 - the tool's private src/, via the insert above
from _pdf_privacy_fixtures import IDENTITY, SOMEONE, USERS  # noqa: E402 - beside this module
from _print_report_fixtures import (  # noqa: E402 - as above
    DPI,
    PDF_NAME,
    TIMEOUT,
    fake_readers,
    text_layer,
)
from _pdf_read_fixtures import CRASHES  # noqa: E402 - as above
from pdf_text import Link  # noqa: E402 - src/
from print_engine import MARGIN_INCHES, Problem  # noqa: E402 - src/

_STEM: Final = PDF_NAME.removesuffix(".pdf")
_TEXT_FILE: Final = f"text/{_STEM}.txt"


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


class UntrustedTextTests(_ReadCase):
    """A text layer holding what UTF-8 cannot encode is still written, and reported."""

    def test_a_lone_surrogate_in_the_text_is_written_as_its_escape(self) -> None:
        layer = dataclasses.replace(text_layer(), pages=("A\ud800B\n",))
        with fake_readers(read_text=layer):
            report = self.read()
        self.assertEqual(report.problems, ())
        self.assertEqual(report.text_file, _TEXT_FILE)
        self.assertEqual((self.out / _TEXT_FILE).read_bytes(), b"A\\ud800B\n")


class UntrustedLinkTests(_ReadCase):
    """A link's URI and destination are each judged alone, and withheld when flagged."""

    def test_a_flagged_destination_is_withheld_beside_an_unflagged_uri(self) -> None:
        named = "/" + USERS + "/" + SOMEONE + "/notes"
        both = Link(1, "https://example.com/", named, (0.0, 0.0, 1.0, 1.0))
        with fake_readers(read_links=(both,)):
            report = self.read()
        self.assertEqual(
            report.links,
            (dataclasses.replace(both, destination=print_report_pdf.WITHHELD),),
        )
        self.assertIn(
            Problem("finding", "link on page 1: a home-rooted path"), report.problems
        )


class UntrustedReadTests(_ReadCase):
    """A PDF crashing pypdf or pdfminer is a finding for that step; every other step runs."""

    def test_each_crash_class_is_a_finding_and_the_other_steps_still_run(self) -> None:
        raised = {"links": "AttributeError", "metadata": "OverflowError"}
        raised["text"] = "AssertionError"
        # The content readers are left real; the others are faked, as everywhere here.
        real = print_report_pdf.read_content
        for reader, data in CRASHES.items():
            with self.subTest(reader=reader):
                self.pdf.write_bytes(data)
                text_file = self.out / _TEXT_FILE
                text_file.unlink(missing_ok=True)
                with fake_readers(read_content=real):
                    report = self.read()
                crash = f"cannot read {reader} from {PDF_NAME}: its reader raised"
                failed = Problem(
                    "finding", f"{reader} failed: {crash} {raised[reader]}"
                )
                self.assertIn(failed, report.problems)
                self.assertEqual(report.renders, (f"renders/{_STEM}-1.png",))


if __name__ == "__main__":
    unittest.main()
