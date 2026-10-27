"""Suite: poppler's readers run through REAL fake executables on PATH.

Each fake is a real process (`_print_fixtures.fake_bin`), so the subprocess boundary is crossed
for real: success, a non-zero exit, a hang killed at the timeout, a binary that is absent, and
the exact argv each reader passes. The parsers they feed are `test_pdf_tools.py`'s.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pdf_tools  # noqa: E402 - the tool's private src/, via the insert above
from _print_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    FONTS_TEXT,
    IMAGES_TEXT,
    INFO_TEXT,
    fake_bin,
)
from pdf_tools import PopplerError  # noqa: E402 - as above

_TIMEOUT = 30.0

#: A fake that logs its argv as one JSON line and prints the text it was built with.
_LOGGING_FAKE = """
import json, sys
with open(@LOG@, "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
sys.stdout.write(@TEXT@)
"""


class RunToolTests(unittest.TestCase):
    """`run_tool`: stdout on success, and a PopplerError naming the tool on every failure."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.bin = Path(scratch.name) / "bin"

    def run_fake(self, body: str, *, timeout: float = _TIMEOUT) -> str:
        """Run a fake `pdfinfo` of `body` through `run_tool`, with no arguments."""
        with mock.patch.dict(os.environ, fake_bin(self.bin, "pdfinfo", body)):
            return pdf_tools.run_tool(["pdfinfo"], timeout=timeout)

    def refusal(self, body: str, *, timeout: float = _TIMEOUT) -> str:
        """The message of the PopplerError a fake `pdfinfo` of `body` raises."""
        with self.assertRaises(PopplerError) as caught:
            self.run_fake(body, timeout=timeout)
        return str(caught.exception)

    def test_success_returns_stdout(self) -> None:
        self.assertEqual(self.run_fake("print('hello')\n"), "hello\n")

    def test_a_failure_names_the_tool_and_quotes_the_last_stderr_line(self) -> None:
        stderr = "first\\nI/O Error: could not open\\n\\n"
        message = self.refusal(
            f"import sys\nsys.stderr.write('{stderr}')\nsys.exit(1)\n"
        )
        self.assertIn("pdfinfo", message)
        self.assertIn("I/O Error: could not open", message)
        self.assertNotIn("first", message)

    def test_a_failure_with_a_silent_stderr_still_says_so(self) -> None:
        message = self.refusal("import sys\nsys.exit(3)\n")
        self.assertIn("exited 3", message)
        self.assertIn("(nothing on stderr)", message)

    def test_a_hang_is_killed_at_the_timeout(self) -> None:
        body = "import time\nwhile True:\n    time.sleep(1)\n"
        message = self.refusal(body, timeout=0.5)
        self.assertIn("timeout", message)
        self.assertIn("pdfinfo", message)

    def test_a_tool_no_fake_stands_in_for_is_never_found(self) -> None:
        # fake_bin's PATH holds its own folder alone, so no run can reach the host's poppler.
        environment = fake_bin(self.bin, "pdfinfo", "")
        with mock.patch.dict(os.environ, environment):
            with self.assertRaises(PopplerError) as caught:
                pdf_tools.run_tool(["pdffonts"], timeout=_TIMEOUT)
        self.assertIn("could not be started", str(caught.exception))
        self.assertEqual(environment, {"PATH": str(self.bin)})

    def test_a_missing_binary_names_the_tool(self) -> None:
        self.bin.mkdir()
        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            with self.assertRaises(PopplerError) as caught:
                pdf_tools.run_tool(["pdffonts"], timeout=_TIMEOUT)
        self.assertIn("pdffonts", str(caught.exception))


class ReaderTests(unittest.TestCase):
    """Each reader's exact argv, and that it returns what its parser makes of the output."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.pdf = self.root / "doc.pdf"

    def logged_run(
        self, name: str, text: str, read: str
    ) -> tuple[object, list[object]]:
        """Call `pdf_tools.<read>` on a logging fake `name` that prints `text`: result, argv log."""
        log = self.root / "argv.jsonl"
        body = _LOGGING_FAKE.replace("@LOG@", repr(str(log))).replace(
            "@TEXT@", repr(text)
        )
        with mock.patch.dict(os.environ, fake_bin(self.root / "bin", name, body)):
            result = getattr(pdf_tools, read)(self.pdf, timeout=_TIMEOUT)
        logged = log.read_text(encoding="utf-8").splitlines()
        return result, [json.loads(line) for line in logged]

    def test_read_info_asks_for_raw_dates_and_every_page_in_one_call(self) -> None:
        result, argv = self.logged_run("pdfinfo", INFO_TEXT, "read_info")
        self.assertEqual(result, pdf_tools.parse_pdfinfo(INFO_TEXT))
        every_page = ["-f", "1", "-l", str(2**31 - 1)]
        self.assertEqual(argv, [["-rawdates", *every_page, "--", str(self.pdf)]])

    def test_read_fonts_lists_the_fonts(self) -> None:
        result, argv = self.logged_run("pdffonts", FONTS_TEXT, "read_fonts")
        self.assertEqual(result, pdf_tools.parse_pdffonts(FONTS_TEXT))
        self.assertEqual(argv, [["--", str(self.pdf)]])

    def test_read_images_lists_without_extracting(self) -> None:
        result, argv = self.logged_run("pdfimages", IMAGES_TEXT, "read_images")
        self.assertEqual(result, pdf_tools.parse_pdfimages_list(IMAGES_TEXT))
        self.assertEqual(argv, [["-list", "--", str(self.pdf)]])

    def test_missing_tools_names_only_the_absent_ones_in_order(self) -> None:
        def which(name: str) -> str | None:
            return None if name == "pdftoppm" else "/opt/bin/" + name

        self.assertEqual(pdf_tools.missing_tools(which), ("pdftoppm",))
        self.assertEqual(
            pdf_tools.missing_tools(lambda name: None), pdf_tools.REQUIRED_TOOLS
        )


if __name__ == "__main__":
    unittest.main()
