"""Suite: pdf_read_worker answers each reader with one JSON line, run in this process.

`main` is called directly, its stdout and stderr captured, so each branch is measured
here; the same module run as a real child process is `test_pdf_read`'s. The PDFs are
`_pdf_read_fixtures`' seed and crash classes, written into a temporary folder.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the
tool's aggregator; the bootstrap below names the tool's private `src/`, and discovery's
top-level directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pdf_read_worker  # noqa: E402 - the tool's private src/, via the insert above
from _pdf_read_fixtures import CRASHES, SEED  # noqa: E402 - beside this module

#: How long a watch thread may take to see a peak past its budget: far longer than it takes.
_JOIN_TIMEOUT = 30.0


class WorkerTests(unittest.TestCase):
    """Each line `main` prints, and what it keeps off stdout and stderr."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name)

    def run_main(self, data: bytes) -> tuple[list[dict[str, object]], str, str]:
        """`main`'s lines over a PDF holding `data`, parsed, then stdout and stderr."""
        pdf = self.folder / "seed.pdf"
        pdf.write_bytes(data)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pdf_read_worker.main(str(pdf))
        self.assertEqual(code, 0)
        printed = out.getvalue()
        return (
            [json.loads(line) for line in printed.splitlines()],
            printed,
            err.getvalue(),
        )

    def test_each_reader_answers_on_its_own_line_in_order(self) -> None:
        link = {
            "page": 1,
            "uri": "https://example.com/",
            "destination": None,
            "rect": [10.0, 10.0, 50.0, 50.0],
        }
        metadata = {"info": {"Producer": "pypdf", "Title": "Seed"}, "xmp": None}
        text = {"pages": ["Hi\n"], "cid_glyphs": 0, "glyph_fonts": {}, "log": {}}
        lines, printed, _ = self.run_main(SEED)
        self.assertEqual(
            lines,
            [
                {"reader": "links", "result": [link]},
                {"reader": "metadata", "result": metadata},
                {"reader": "text", "result": text},
            ],
        )
        self.assertTrue(printed.endswith("\n"))

    def test_an_unreadable_pdf_answers_each_reader_with_its_error(self) -> None:
        lines, _, _ = self.run_main(b"not a pdf")
        self.assertEqual(
            [line["reader"] for line in lines], ["links", "metadata", "text"]
        )
        for line in lines:
            self.assertEqual(sorted(line), ["error", "reader"])
        self.assertRegex(str(lines[2]["error"]), r"^cannot read text from seed\.pdf: ")

    def test_each_crash_class_answers_with_its_class_name_alone(self) -> None:
        raised = {"links": "AttributeError", "metadata": "OverflowError"}
        raised["text"] = "AssertionError"
        order = list(pdf_read_worker.READERS)
        for reader, data in CRASHES.items():
            with self.subTest(reader=reader):
                lines, _, _ = self.run_main(data)
                self.assertEqual(
                    lines[order.index(reader)],
                    {"reader": reader, "crash": raised[reader]},
                )

    def test_what_a_library_prints_logs_or_warns_reaches_neither_stream(self) -> None:
        def chatty(pdf: Path) -> object:
            print("a library's own line")
            print("a library's own error", file=sys.stderr)
            logging.getLogger("pypdf").warning("a library's own record")
            warnings.warn("a library's own warning", stacklevel=1)
            return "\ud800"

        with mock.patch.object(pdf_read_worker, "READERS", {"links": chatty}):
            lines, printed, err = self.run_main(SEED)
        # A lone surrogate travels as its escape, in a reply that is ASCII throughout.
        self.assertEqual(lines, [{"reader": "links", "result": "\ud800"}])
        self.assertEqual(printed, '{"reader": "links", "result": "\\ud800"}\n')
        self.assertEqual(err, "")

    def test_a_library_raising_past_exception_is_a_crash_too(self) -> None:
        def exits(pdf: Path) -> object:
            raise SystemExit(3)

        with mock.patch.object(pdf_read_worker, "READERS", {"links": exits}):
            lines, _, _ = self.run_main(SEED)
        self.assertEqual(lines, [{"reader": "links", "crash": "SystemExit"}])

    def test_an_interrupt_is_never_a_reply(self) -> None:
        def interrupted(pdf: Path) -> object:
            raise KeyboardInterrupt

        with mock.patch.object(pdf_read_worker, "READERS", {"links": interrupted}):
            with self.assertRaises(KeyboardInterrupt):
                self.run_main(SEED)

    def test_a_reply_past_the_cap_is_written_as_that_readers_error(self) -> None:
        # The worker keeps its own line short, so the parent never captures a hostile
        # PDF's inflated text in the first place.
        def long(pdf: Path) -> object:
            return "x" * 100

        readers = {"links": long, "metadata": lambda pdf: "short"}
        with (
            mock.patch.object(pdf_read_worker, "READERS", readers),
            mock.patch.object(pdf_read_worker, "MAX_REPLY_CHARS", 60),
        ):
            lines, _, _ = self.run_main(SEED)
        reason = "cannot read links from seed.pdf: its reply exceeded 60 characters"
        self.assertEqual(
            lines,
            [
                {"reader": "links", "error": reason},
                {"reader": "metadata", "result": "short"},
            ],
        )

    def test_an_unknown_reader_is_the_callers_error_not_the_pdfs(self) -> None:
        with self.assertRaises(KeyError):
            pdf_read_worker.reply("fonts", self.folder / "seed.pdf")


class MemoryBudgetTests(unittest.TestCase):
    """The child's entry sets its memory budget first, where the host can hold one."""

    def test_the_budget_is_two_gibibytes(self) -> None:
        self.assertEqual(pdf_read_worker.MEMORY_BUDGET_BYTES, 2 * 1024**3)

    def test_serve_sets_the_budget_and_its_watch_before_any_reader_runs(self) -> None:
        order = mock.Mock()
        order.main.return_value = 0
        with (
            mock.patch.object(pdf_read_worker, "limit_memory", order.limit_memory),
            mock.patch.object(pdf_read_worker, "watch_memory", order.watch_memory),
            mock.patch.object(pdf_read_worker, "main", order.main),
        ):
            self.assertEqual(pdf_read_worker.serve("seed.pdf"), 0)
        budget = pdf_read_worker.MEMORY_BUDGET_BYTES
        expected = [
            mock.call.limit_memory(budget),
            mock.call.watch_memory(budget),
            mock.call.main("seed.pdf"),
        ]
        self.assertEqual(order.mock_calls, expected)

    def test_a_budget_the_host_takes_is_set_as_hard_and_soft_limit(self) -> None:
        # Skipped in the body, not by a decorator, so mypy's win32 pass skips it too.
        if sys.platform == "win32":  # pragma: only-on win32 - no RLIMIT_AS on Windows
            self.skipTest("Windows has no address-space limit")  # pragma: only-on win32
        import resource

        with mock.patch.object(resource, "setrlimit") as setrlimit:
            self.assertTrue(pdf_read_worker.limit_memory(1024))
        setrlimit.assert_called_once_with(resource.RLIMIT_AS, (1024, 1024))

    @unittest.skipIf(sys.platform == "win32", "Windows has no address-space limit")
    def test_a_budget_the_host_refuses_is_reported_unset(self) -> None:
        # Measured: macOS refuses an address-space limit below its own with ValueError.
        import resource

        for refusal in (ValueError("current limit exceeds maximum limit"), OSError()):
            with (
                self.subTest(refusal=type(refusal).__name__),
                mock.patch.object(resource, "setrlimit", side_effect=refusal),
            ):
                self.assertFalse(pdf_read_worker.limit_memory(1024))

    def test_windows_sets_no_budget(self) -> None:
        with mock.patch.object(pdf_read_worker.sys, "platform", "win32"):
            self.assertFalse(pdf_read_worker.limit_memory(1024))


class MemoryWatchTests(unittest.TestCase):
    """Where no address-space budget holds, a watch ends the child past its peak budget."""

    def test_the_watch_ends_the_process_once_its_peak_passes_the_budget(self) -> None:
        peaks = iter([10, 25, 26, 99])
        end = mock.Mock()
        pdf_read_worker.watch_peak(25, 0.0, lambda: next(peaks), end)
        end.assert_called_once_with(pdf_read_worker.OVER_BUDGET_EXIT)
        self.assertEqual(next(peaks), 99)  # the third peak read was the last

    def test_the_peak_is_read_in_bytes_on_macos_and_kibibytes_elsewhere(self) -> None:
        # Skipped in the body, not by a decorator, so mypy's win32 pass skips it too.
        if sys.platform == "win32":  # pragma: only-on win32 - no getrusage on Windows
            self.skipTest("Windows has no getrusage")  # pragma: only-on win32
        import resource

        # ru_maxrss 3 is 3 bytes on macOS, under a 2048-byte budget, and 3,072 bytes on
        # Linux, past it; 4,096 is past it either way.
        for platform, reads in (("darwin", 2), ("linux", 1)):
            usage = [mock.Mock(ru_maxrss=3), mock.Mock(ru_maxrss=4096)]
            end = mock.Mock()
            with (
                self.subTest(platform=platform),
                mock.patch.object(pdf_read_worker.sys, "platform", platform),
                mock.patch.object(resource, "getrusage", side_effect=usage) as read,
            ):
                watch = pdf_read_worker.watch_memory(2048, interval=0.0, end=end)
                assert watch is not None
                watch.join(timeout=_JOIN_TIMEOUT)
                self.assertFalse(watch.is_alive())
                self.assertTrue(watch.daemon)
                end.assert_called_once_with(pdf_read_worker.OVER_BUDGET_EXIT)
                self.assertEqual(read.call_count, reads)
                read.assert_called_with(resource.RUSAGE_SELF)

    def test_windows_starts_no_watch(self) -> None:
        with mock.patch.object(pdf_read_worker.sys, "platform", "win32"):
            self.assertIsNone(pdf_read_worker.watch_memory(1024))

    def test_the_watch_reads_every_twentieth_of_a_second_and_exits_with_its_own_code(
        self,
    ) -> None:
        self.assertEqual(pdf_read_worker.WATCH_INTERVAL_S, 0.05)
        self.assertNotIn(pdf_read_worker.OVER_BUDGET_EXIT, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
