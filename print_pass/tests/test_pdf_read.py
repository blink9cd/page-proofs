"""Suite: pdf_read reads a PDF's links, metadata and text in ONE bounded child per PDF.

Two halves. The boundary cases run the REAL worker, or a stand-in written into a temporary
folder, as a real child process through `bounded_run`: a read, each crash class, a timeout
before and after a reader answered, an exit and a reply that is not one. The reply cases
hand `read_content` a finished (or timed out) process through its `run` seam, to pin how
each line of the reply is read. The worker's own suite is `test_pdf_read_worker`; the
property over mutated PDFs is `test_pdf_read_fuzz`.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the
tool's aggregator; the bootstrap below names the tool's private `src/`, and discovery's
top-level directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from functools import partial
from pathlib import Path
from typing import Final
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pdf_read  # noqa: E402 - the tool's private src/, via the insert above
import pdf_text  # noqa: E402 - as above
from _pdf_read_fixtures import CRASHES, SEED  # noqa: E402 - beside this module
from pdf_text import Link, PdfTextError, TextLayer  # noqa: E402 - src/

#: A bound no healthy read of the seed comes near, even on a loaded machine.
_TIMEOUT: Final = 60.0

#: The bound a stand-in that stops answering is given, and how much longer than it the
#: whole call may take: the kill and the reaping, on a loaded machine.
_SHORT_TIMEOUT: Final = 0.5
_KILL_SLACK: Final = 20.0

#: The PDF every reply case names.
_PDF: Final = Path("folder") / "seed.pdf"

#: What each reader reports when the child was killed at the short bound.
_KILLED: Final = "its reader hit the 0.5 s timeout and was killed"

#: A stand-in worker answering for links, then never answering again.
_ANSWERS_LINKS_THEN_HANGS: Final = """\
import json, sys, time

def serve(pdf):
    sys.stdout.write(json.dumps({"reader": "links", "result": []}) + "\\n")
    sys.stdout.flush()
    time.sleep(600)
"""


def _failed(reader: str, reason: str) -> str:
    """The message of a `PdfTextError` for `reader` over `_PDF`, failed for `reason`."""
    return f"cannot read {reader} from seed.pdf: {reason}"


class _FolderCase(unittest.TestCase):
    """A fresh temporary folder per case."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name)

    def pdf(self, data: bytes, name: str = "seed.pdf") -> Path:
        """`data` written to `name` in the folder."""
        path = self.folder / name
        path.write_bytes(data)
        return path

    def stand_in(self, body: str) -> pdf_read.Run:
        """A run of a stand-in worker whose module is `body`, as a real bounded child."""
        folder = self.folder / "stand_in"
        folder.mkdir()
        (folder / "pdf_read_worker.py").write_text(body, encoding="utf-8", newline="\n")
        return partial(pdf_read.run_worker, folder=folder)

    def assert_failed(self, outcome: object, message: str) -> None:
        """`outcome` is a `PdfTextError` worded `message`."""
        self.assertIsInstance(outcome, PdfTextError)
        self.assertEqual(str(outcome), message)


class WorkerBoundaryTests(_FolderCase):
    """The real worker, and stand-ins for it, run as bounded child processes."""

    def test_each_reader_reads_what_the_in_process_reader_reads(self) -> None:
        pdf = self.pdf(SEED)
        content = pdf_read.read_content(pdf, timeout=_TIMEOUT)
        self.assertEqual(content.links, pdf_text.read_links(pdf))
        self.assertEqual(content.metadata, pdf_text.read_metadata(pdf))
        self.assertEqual(content.text, pdf_text.read_text(pdf))

    def test_one_child_reads_every_reader_of_one_pdf(self) -> None:
        pdf = self.pdf(SEED)
        run = mock.Mock(wraps=pdf_read.run_worker)
        content = pdf_read.read_content(pdf, timeout=_TIMEOUT, run=run)
        run.assert_called_once_with(pdf, _TIMEOUT)
        self.assertEqual(content.links, pdf_text.read_links(pdf))

    def test_each_crash_class_fails_its_reader_naming_it(self) -> None:
        raised = {"links": "AttributeError", "metadata": "OverflowError"}
        raised["text"] = "AssertionError"
        for reader, data in CRASHES.items():
            with self.subTest(reader=reader):
                pdf = self.pdf(data, f"{reader}.pdf")
                content = pdf_read.read_content(pdf, timeout=_TIMEOUT)
                expected = f"cannot read {reader} from {reader}.pdf: its reader raised"
                outcome = getattr(content, reader)
                self.assert_failed(outcome, f"{expected} {raised[reader]}")

    def test_an_unreadable_pdf_is_each_readers_own_error(self) -> None:
        pdf = self.pdf(b"not a pdf", "broken.pdf")
        content = pdf_read.read_content(pdf, timeout=_TIMEOUT)
        self.assertIsInstance(content.links, PdfTextError)
        self.assertRegex(str(content.links), r"^cannot read links from broken")

    def test_a_pdf_named_like_an_option_is_read_as_a_file(self) -> None:
        pdf = self.pdf(SEED, "-seed.pdf")
        content = pdf_read.read_content(pdf, timeout=_TIMEOUT)
        self.assertEqual(content.links, pdf_text.read_links(pdf))

    def test_a_reader_answered_before_the_timeout_keeps_its_result(self) -> None:
        run = self.stand_in(_ANSWERS_LINKS_THEN_HANGS)
        started = time.monotonic()
        content = pdf_read.read_content(_PDF, timeout=_SHORT_TIMEOUT, run=run)
        self.assertLess(time.monotonic() - started, _SHORT_TIMEOUT + _KILL_SLACK)
        self.assertEqual(content.links, ())
        self.assert_failed(content.metadata, _failed("metadata", _KILLED))
        self.assert_failed(content.text, _failed("text", _KILLED))

    def test_a_child_that_never_answers_fails_every_reader(self) -> None:
        run = self.stand_in("import time\n\ndef serve(*argv):\n    time.sleep(600)\n")
        content = pdf_read.read_content(_PDF, timeout=_SHORT_TIMEOUT, run=run)
        for reader in ("links", "metadata", "text"):
            self.assert_failed(getattr(content, reader), _failed(reader, _KILLED))

    def test_a_child_that_exits_names_its_exception_alone(self) -> None:
        run = self.stand_in("raise ImportError('no pypdf under /some/where')\n")
        content = pdf_read.read_content(_PDF, timeout=_TIMEOUT, run=run)
        reason = "its reader exited 1 (ImportError)"
        for reader in ("links", "metadata", "text"):
            self.assert_failed(getattr(content, reader), _failed(reader, reason))

    def test_a_child_that_prints_no_reply_is_reported(self) -> None:
        run = self.stand_in("def serve(*argv):\n    print('hello')\n    return 0\n")
        content = pdf_read.read_content(_PDF, timeout=_TIMEOUT, run=run)
        reason = "its reader printed no reply"
        self.assert_failed(content.metadata, _failed("metadata", reason))


def _replying(stdout: str, code: int = 0, stderr: str = "") -> pdf_read.Run:
    """A run that finishes at once, printing `stdout` and `stderr` and exiting `code`."""

    def run(pdf: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([str(pdf)], code, stdout, stderr)

    return run


def _line(reader: str, key: str, value: object) -> str:
    """One reply line, as the worker writes it."""
    return json.dumps({"reader": reader, key: value}) + "\n"


def _results(links: object, metadata: object, text: object) -> pdf_read.Run:
    """A run whose three reply lines hold these results, in the worker's order."""
    lines = (
        _line("links", "result", links),
        _line("metadata", "result", metadata),
        _line("text", "result", text),
    )
    return _replying("".join(lines))


#: A link's JSON as the worker writes it, a rectangle of whole numbers among it.
_LINK_JSON: Final[dict[str, object]] = {
    "page": 2,
    "uri": None,
    "destination": "[ 1 /Fit ]",
    "rect": [1, 2.5, 3, 4],
}

#: That link, as `read_content` builds it back.
_LINK: Final = Link(2, None, "[ 1 /Fit ]", (1.0, 2.5, 3.0, 4.0))

#: The Info fields and XMP packet as the worker writes them.
_METADATA_JSON: Final[dict[str, object]] = {"info": {"Title": "T"}, "xmp": None}

#: A text layer's JSON as the worker writes it.
_TEXT_JSON: Final[dict[str, object]] = {
    "pages": ["a\n", "b"],
    "cid_glyphs": 1,
    "glyph_fonts": {"é": ["Times"]},
    "log": {"t %s": 2},
}

#: The three good lines, for the cases that spoil one of them.
_GOOD: Final = (
    _line("links", "result", [_LINK_JSON]),
    _line("metadata", "result", _METADATA_JSON),
    _line("text", "result", _TEXT_JSON),
)


def _timing_out(output: str | None) -> pdf_read.Run:
    """A run that times out, carrying `output` as what the post-kill read recovered."""

    def run(pdf: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired([str(pdf)], timeout, output=output)

    return run


class ReplyTests(unittest.TestCase):
    """How each line of the reply the run hands back is read."""

    def assert_failed(self, outcome: object, message: str) -> None:
        """`outcome` is a `PdfTextError` worded `message`."""
        self.assertIsInstance(outcome, PdfTextError)
        self.assertEqual(str(outcome), message)

    def test_each_result_is_built_back_into_its_readers_own_types(self) -> None:
        run = _results([_LINK_JSON], _METADATA_JSON, _TEXT_JSON)
        content = pdf_read.read_content(_PDF, timeout=1, run=run)
        self.assertEqual(content.links, (_LINK,))
        self.assertEqual(content.metadata, ({"Title": "T"}, None))
        self.assertEqual(
            content.text, TextLayer(("a\n", "b"), 1, {"é": ("Times",)}, {"t %s": 2})
        )

    def test_an_error_line_is_that_readers_error_as_it_is_worded(self) -> None:
        error = _line("text", "error", "cannot read text from seed.pdf: bad")
        run = _replying(_GOOD[0] + _GOOD[1] + error)
        content = pdf_read.read_content(_PDF, timeout=1, run=run)
        self.assert_failed(content.text, "cannot read text from seed.pdf: bad")
        self.assertEqual(content.metadata, ({"Title": "T"}, None))

    def test_a_line_that_is_not_json_fails_only_its_reader(self) -> None:
        run = _replying(_GOOD[0] + "{not json\n" + _GOOD[2])
        content = pdf_read.read_content(_PDF, timeout=1, run=run)
        self.assert_failed(
            content.metadata, _failed("metadata", "its reader printed no reply")
        )
        self.assertEqual(content.links, (_LINK,))
        self.assertIsInstance(content.text, TextLayer)

    def test_a_line_that_is_not_a_reply_is_reported(self) -> None:
        # Not one object, no reader, another reader, two answers, an unknown key, and an
        # error or crash that is not text.
        replies = (
            "[]",
            json.dumps({"result": []}),
            json.dumps({"reader": "text", "result": []}),
            json.dumps({"reader": "links", "result": [], "error": "x"}),
            json.dumps({"reader": "links", "other": 1}),
            json.dumps({"reader": "links", "error": 3}),
            json.dumps({"reader": "links", "crash": 3}),
        )
        for reply in replies:
            with self.subTest(reply=reply):
                run = _replying(reply + "\n" + _GOOD[1] + _GOOD[2])
                content = pdf_read.read_content(_PDF, timeout=1, run=run)
                reason = "its reader printed no reply"
                self.assert_failed(content.links, _failed("links", reason))
                self.assertIsInstance(content.text, TextLayer)

    def test_a_crash_line_names_the_class_alone(self) -> None:
        run = _replying(_line("links", "crash", "AttributeError") + _GOOD[1])
        content = pdf_read.read_content(_PDF, timeout=1, run=run)
        reason = "its reader raised AttributeError"
        self.assert_failed(content.links, _failed("links", reason))

    def test_a_line_past_the_cap_fails_its_reader_alone(self) -> None:
        long_links = _line("links", "result", [_LINK_JSON] * 3)
        with mock.patch.object(pdf_read, "MAX_REPLY_CHARS", len(_GOOD[1])):
            run = _replying(long_links + "".join(_GOOD[1:]))
            content = pdf_read.read_content(_PDF, timeout=1, run=run)
            cap = f"{pdf_read.MAX_REPLY_CHARS:,}"
        reason = f"its reply exceeded {cap} characters"
        self.assert_failed(content.links, _failed("links", reason))
        self.assertEqual(content.metadata, ({"Title": "T"}, None))

    def test_a_reader_with_no_line_takes_the_exit_and_its_exception(self) -> None:
        cases = (
            ("", ""),
            (
                "Traceback (most recent call last):\n  File x\nMemoryError\n\n",
                " (MemoryError)",
            ),
            ("OSError: [Errno 2] /some/where/pypdf", " (OSError)"),
            (
                "pypdf._utils.PdfReadError: at /some/where",
                " (pypdf._utils.PdfReadError)",
            ),
            ("KeyboardInterrupt", " (KeyboardInterrupt)"),
            ("SystemExit: 3", " (SystemExit)"),
            ("os.error: [Errno 2] /some/where/pypdf", ""),
            ("Segmentation fault: 11", ""),
            ("UserWarning: page chose these words", ""),
            ("Errors: a word the page chose", ""),
            ("ErrorProne: a word the page chose", ""),
        )
        for stderr, named in cases:
            with self.subTest(stderr=stderr):
                run = _replying(_GOOD[0], -11, stderr)
                content = pdf_read.read_content(_PDF, timeout=1, run=run)
                self.assertEqual(content.links, (_LINK,))
                reason = f"its reader exited -11{named}"
                self.assert_failed(content.metadata, _failed("metadata", reason))
                self.assert_failed(content.text, _failed("text", reason))

    def test_a_child_its_memory_watch_ended_names_the_budget(self) -> None:
        exit_code = pdf_read.OVER_BUDGET_EXIT
        run = _replying(_GOOD[0], exit_code, "")
        content = pdf_read.read_content(_PDF, timeout=1, run=run)
        self.assertEqual(content.links, (_LINK,))
        reason = "its reader passed its 2 GiB memory budget and was ended"
        self.assert_failed(content.metadata, _failed("metadata", reason))
        self.assert_failed(content.text, _failed("text", reason))

    def test_a_timeout_keeps_every_complete_line_and_fails_the_rest(self) -> None:
        # The last line was cut short by the kill: not a reply, so not read.
        run = _timing_out(_GOOD[0] + _GOOD[1][:-5])
        content = pdf_read.read_content(_PDF, timeout=2, run=run)
        self.assertEqual(content.links, (_LINK,))
        reason = "its reader hit the 2 s timeout and was killed"
        self.assert_failed(content.metadata, _failed("metadata", reason))
        self.assert_failed(content.text, _failed("text", reason))

    def test_a_timeout_with_nothing_recovered_fails_every_reader(self) -> None:
        # None: the post-kill read expired too, so the output was abandoned.
        content = pdf_read.read_content(_PDF, timeout=2, run=_timing_out(None))
        reason = "its reader hit the 2 s timeout and was killed"
        for reader in ("links", "metadata", "text"):
            self.assert_failed(getattr(content, reader), _failed(reader, reason))

    def test_a_child_that_cannot_start_names_why_and_not_where(self) -> None:
        def run(pdf: Path, timeout: float) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(
                2, "No such file or directory", "/some/where/python"
            )

        content = pdf_read.read_content(_PDF, timeout=1, run=run)
        reason = "its reader could not be started (No such file or directory)"
        self.assert_failed(content.text, _failed("text", reason))

    def test_a_result_of_the_wrong_shape_is_malformed(self) -> None:
        cases: dict[str, tuple[object, ...]] = {
            "links": (
                {},
                [3],
                [{**_LINK_JSON, "rect": [1, 2, 3]}],
                [{**_LINK_JSON, "rect": [1, 2, 3, True]}],
                [{**_LINK_JSON, "rect": [1, 2, 3, "4"]}],
                [{**_LINK_JSON, "page": True}],
                [{**_LINK_JSON, "uri": 3}],
                [{"page": 1}],
            ),
            "metadata": (
                [],
                {"info": {"Title": 1}, "xmp": None},
                {"info": {}, "xmp": 3},
                {"info": {}},
            ),
            "text": (
                {**_TEXT_JSON, "pages": "a"},
                {**_TEXT_JSON, "cid_glyphs": True},
                {**_TEXT_JSON, "glyph_fonts": {"é": [1]}},
                {**_TEXT_JSON, "log": {"t": "2"}},
            ),
        }
        good = {"links": [_LINK_JSON], "metadata": _METADATA_JSON, "text": _TEXT_JSON}
        for reader, results in cases.items():
            for result in results:
                with self.subTest(reader=reader, result=result):
                    run = _results(**{**good, reader: result})
                    content = pdf_read.read_content(_PDF, timeout=1, run=run)
                    reason = "its reader's reply is malformed"
                    self.assert_failed(
                        getattr(content, reader), _failed(reader, reason)
                    )


class OutcomeTests(unittest.TestCase):
    """`outcome` hands a result on and raises a failure, for the report's steps."""

    def test_a_result_is_returned_and_a_failure_raised(self) -> None:
        self.assertEqual(pdf_read.outcome(("a",)), ("a",))
        failure = PdfTextError("cannot read text from seed.pdf: bad")
        with self.assertRaises(PdfTextError) as caught:
            pdf_read.outcome(failure)
        self.assertIs(caught.exception, failure)


if __name__ == "__main__":
    unittest.main()
