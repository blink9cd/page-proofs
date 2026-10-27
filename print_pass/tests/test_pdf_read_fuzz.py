"""Suite: over any mutation of a small valid PDF, each of pdf_read's readers reads or fails.

The property splices a few byte runs of `_pdf_read_fixtures`' seed and reads the result
with every reader; each comes back read or as a `PdfTextError`, and nothing else leaves.
The three crash classes found mutating such PDFs are kept as explicit examples, so every
run replays them first.

The worker runs IN this process here, through `read_content`'s `run` seam: `main` is
called with its stdout captured, as the bounded child would print it. A real child per
example would cost the interpreter's start each time, and the boundary itself (the
timeout, the kill, an exit) is `test_pdf_read`'s. What crosses it is the same JSON either
way.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the
tool's aggregator; the bootstrap below names the tool's private `src/`, and discovery's
top-level directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pdf_read  # noqa: E402 - the tool's private src/, via the insert above
import pdf_read_worker  # noqa: E402 - as above
from _pdf_read_fixtures import CRASHES, SEED  # noqa: E402 - beside this module
from pdf_text import PdfTextError, TextLayer  # noqa: E402 - src/

# The determinism convention: a profile registered at import, never an
# `@settings` decorator. deadline=None and too_slow suppressed: a busy machine measures load.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: The most bytes one splice removes or inserts, and the most splices one example makes:
#: small, so most examples stay close enough to a PDF to reach deep into both parsers.
_SPLICE_BYTES: Final = 8
_SPLICES: Final = 4

#: Unused by the in-process run, which has no bound; any positive number will do.
_TIMEOUT: Final = 1.0

#: One splice: where, how many bytes to remove there, and what to insert in their place.
type _Splice = tuple[int, int, bytes]


def _mutated(splices: Sequence[_Splice]) -> bytes:
    """The seed with each splice applied in turn, its position wrapped onto the bytes.

    The seed is far longer than every splice could remove, so the bytes are never empty.
    """
    data = bytearray(SEED)
    for at, cut, inserted in splices:
        start = at % len(data)
        data[start : start + cut] = inserted
    return bytes(data)


#: A mutation of the seed: one to `_SPLICES` splices.
_MUTATIONS: Final = st.lists(
    st.tuples(
        st.integers(0, len(SEED) - 1),
        st.integers(0, _SPLICE_BYTES),
        st.binary(max_size=_SPLICE_BYTES),
    ),
    min_size=1,
    max_size=_SPLICES,
).map(_mutated)


def _in_process(pdf: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    """The worker's reply over `pdf`, run in this process."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = pdf_read_worker.main(str(pdf))
    return subprocess.CompletedProcess([str(pdf)], code, out.getvalue(), "")


class MutatedPdfTests(unittest.TestCase):
    """Every reader, over mutations of the seed and over every crash class kept."""

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.pdf = Path(holder.name) / "mutated.pdf"

    @given(_MUTATIONS)
    @example(CRASHES["links"])
    @example(CRASHES["metadata"])
    @example(CRASHES["text"])
    def test_fuzz_read_content(self, data: bytes) -> None:
        # Nothing may leave read_content, and each reader's outcome is its result's type
        # or a PdfTextError.
        self.pdf.write_bytes(data)
        content = pdf_read.read_content(self.pdf, timeout=_TIMEOUT, run=_in_process)
        self.assertIsInstance(content.links, tuple | PdfTextError)
        self.assertIsInstance(content.metadata, tuple | PdfTextError)
        self.assertIsInstance(content.text, TextLayer | PdfTextError)

    def test_every_crash_class_kept_still_crashes_its_library(self) -> None:
        # Each example above replays a crash only while the library still has it: a
        # library fixed upstream leaves a seed that pins nothing, and this says so.
        for reader, data in CRASHES.items():
            with self.subTest(reader=reader):
                self.pdf.write_bytes(data)
                reply = pdf_read_worker.reply(reader, self.pdf)
                self.assertEqual(list(reply), [pdf_read_worker.CRASH])


if __name__ == "__main__":
    unittest.main()
