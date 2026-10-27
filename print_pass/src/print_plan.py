"""What the CLI hands a run, and the prefix every message opens with -- with no PDF reader.

`print_modes` runs a plan, and its imports reach the PDF readers (pypdf, pdfminer.six)
through the report. The CLI's must not: a host without them still shows `--help`, and
refuses a run with exit 2 rather than dying at import with 1, which here is the code of a
finding. So what the CLI needs before a run starts lives here -- the plans it validates,
the protocol of the runs it hands them to, the code of a run that cannot measure, and the
message prefix -- and this module imports neither the report nor a reader, nor
Playwright.

Entry point assumptions: the CLI's bootstrap has put this folder on the path; the shared
UTF-8 stdio policy is found by the sibling insert below.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

# The shared UTF-8 stdio policy: a folder beside this tool's own, named rather than searched
# for, so a same-named folder above the clone is never imported in its place, and put near
# the front of the path, ahead of every installed distribution, so none of the same name can
# shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "stdio_utf8"))

import stdio_utf8
from print_engine import EngineSettings

#: The exit status of a run that could not measure honestly.
EXIT_CANNOT_MEASURE: Final = 2

#: Exactly this many PDFs make a pair to compare.
PAIR: Final = 2

#: The tool's own name, which opens every message and names the tool in the report. The
#: watchdog, which imports nothing of the tool's, restates it; its suite holds the two equal.
TOOL: Final = "print_pass"


@dataclass(frozen=True)
class MakePlan:
    """A validated make run: what to serve and print, how, and where it goes."""

    out: Path
    dpi: int
    serve: Path
    targets: Path | None  # the targets file, when one is named
    pages: tuple[str, ...]  # the ad hoc pages, when none is
    browsers: Path | None  # --browsers as given
    settings: EngineSettings


@dataclass(frozen=True)
class ReadPlan:
    """A validated read or compare run: its mode, the PDFs, and how each is read."""

    mode: str
    out: Path
    dpi: int
    timeout: float  # each reader's bound
    pdfs: tuple[Path, ...]


class Runs(Protocol):
    """The three runs, as `print_modes` defines them: each takes its plan, returns the code."""

    def run_make(self, plan: MakePlan) -> int:
        """Print every state of the targets, read and compare the PDFs, and report."""

    def run_read(self, plan: ReadPlan) -> int:
        """Read every PDF, and report."""

    def run_compare(self, plan: ReadPlan) -> int:
        """Compare the two PDFs' renders, and report."""


def say(line: str) -> None:
    """Print one progress line to stdout, escaped as a message rather than data."""
    stdio_utf8.print_escaped(_spelled(f"{TOOL}: {line}"), sys.stdout)


def warn(line: str) -> None:
    """Print one problem to stderr, where diagnostics belong, escaped as `say` escapes."""
    stdio_utf8.print_escaped(_spelled(f"{TOOL}: {line}"), sys.stderr)


def _spelled(text: str) -> str:
    """`text` with each character a terminal would act on spelled as its escape.

    A line can quote what a page or a log chose, so nothing in it may move the cursor, clear
    the screen or reorder what follows; each of its own lines (a traceback's) stays a line.
    """
    return "\n".join(stdio_utf8.spell_unsafe(part) for part in text.split("\n"))
