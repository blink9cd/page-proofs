"""The child process `pdf_read` runs: pdf_text's three readers over one PDF, as JSON lines.

pypdf and pdfminer parse a PDF the page or its maker chose, and a hostile one can make either
raise what no reader expects or run for as long as it likes. So every read of one PDF runs
here, in one child `python -I` that `pdf_read` bounds and can kill, and whatever happens here
stays here. `main` runs the readers in `READERS` order (links, metadata, text) and, after
each, writes and flushes ONE line on stdout: a JSON object naming its `reader` and holding
exactly one of

- `result`: what the reader read, as JSON (`pdf_read` builds the dataclasses back);
- `error`: a `PdfTextError`'s message, which names the PDF by its name alone;
- `crash`: the class name of any other exception the read raised, an interrupt aside. Its
  message is not carried: a library's message can quote a path, and a class name is enough
  to say which reader failed and how.

A line is written as soon as its reader is done, so a reader that hangs and is killed costs
only itself and the readers after it. A line longer than `MAX_REPLY_CHARS` is never written:
its reader's line is an `error` saying so instead, so a PDF whose streams inflate to gigabytes
of text never reaches the parent's capture. `serve`, the child's entry, first sets an
address-space budget of `MEMORY_BUDGET_BYTES` where the host can hold one, so a reader
inflating past it fails as that reader's `crash: MemoryError`, and starts `watch_memory`,
which ends the child with `OVER_BUDGET_EXIT` once its peak resident size passes the same
budget, for macOS, which holds no such limit. Each line is written with `ensure_ascii`, so a
lone surrogate in a PDF's text travels as its JSON escape, and no line holds a line break.
While a reader runs, stdout and stderr both point at `os.devnull`: what a library prints,
and what logging's last-resort handler and `warnings` write to `sys.stderr`, goes nowhere, so
it can neither corrupt a reply nor grow what `pdf_read` captures. stderr is left for a
traceback.

Entry point assumptions: `pdf_read` starts the interpreter isolated (`-I`) with a minimal
environment, puts this folder first on `sys.path` and calls `serve` with the PDF's path from
its argv; pypdf and
pdfminer.six are importable there, as in the parent. Nothing of the parent's state is
inherited: the PDF arrives as a path in argv, never through a shell. It is no command-line
tool: it takes no options, and is never run by hand.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

from pdf_text import PdfTextError, read_links, read_metadata, read_text

#: The readers, by the name `main` takes; each also names its step in a message.
LINKS: Final = "links"
METADATA: Final = "metadata"
TEXT: Final = "text"

#: The key naming a line's reader, and the three keys one of which the line also holds.
READER: Final = "reader"
RESULT: Final = "result"
ERROR: Final = "error"
CRASH: Final = "crash"

#: The most characters one reader's line may hold; a longer one is written as its error. A
#: long site's text layer is a few hundred thousand characters, so this leaves it room to
#: grow many times over while a hostile PDF's inflated text stays in this process.
MAX_REPLY_CHARS: Final = 10_000_000

#: The memory the child may take. A site's PDF reads in well under a few hundred megabytes;
#: past this an inflate bomb is stopped before the machine swaps. Linux holds it as an
#: address-space limit, so the read fails alone, as a MemoryError; macOS refuses any such
#: limit below its own (measured), so there `watch_memory` ends the child past it. Windows
#: has neither, so there the timeout alone bounds the read.
MEMORY_BUDGET_BYTES: Final = 2 * 1024**3

#: How often, in seconds, `watch_memory` reads the child's peak resident size. A read is a
#: system call, so this costs nothing, and an inflate passes the budget by no more than what
#: it allocates in one interval.
WATCH_INTERVAL_S: Final = 0.05

#: The exit status of a child `watch_memory` ended: none Python itself exits with, so the
#: parent can name the budget as what ended it.
OVER_BUDGET_EXIT: Final = 3


def _links(pdf: Path) -> object:
    """`read_links` as JSON: a list of links, each rectangle a list of four numbers."""
    return [
        {
            "page": link.page,
            "uri": link.uri,
            "destination": link.destination,
            "rect": list(link.rect),
        }
        for link in read_links(pdf)
    ]


def _metadata(pdf: Path) -> object:
    """`read_metadata` as JSON: the Info fields, and the raw XMP packet or null."""
    info, xmp = read_metadata(pdf)
    return {"info": dict(info), "xmp": xmp}


def _text(pdf: Path) -> object:
    """`read_text` as JSON: pages, unmapped glyphs, each glyph's fonts, and the log."""
    layer = read_text(pdf)
    return {
        "pages": list(layer.pages),
        "cid_glyphs": layer.cid_glyphs,
        "glyph_fonts": {
            glyph: list(fonts) for glyph, fonts in layer.glyph_fonts.items()
        },
        "log": dict(layer.log),
    }


def _too_long(reader: str, pdf: Path) -> str:
    """The error a reply past `MAX_REPLY_CHARS` is written as, worded as pdf_read's own."""
    return (
        f"cannot read {reader} from {pdf.name}: "
        f"its reply exceeded {MAX_REPLY_CHARS:,} characters"
    )


#: Each reader, as the JSON it reads, in the order `main` runs them and writes their lines.
READERS: Final[Mapping[str, Callable[[Path], object]]] = MappingProxyType(
    {LINKS: _links, METADATA: _metadata, TEXT: _text}
)


def reply(reader: str, pdf: Path) -> dict[str, object]:
    """The reply for reading `pdf` with `reader`: its result, its error, or its crash.

    Raises:
        KeyError: `reader` names no reader, which is the caller's bug, not the PDF's.
        KeyboardInterrupt: the run was interrupted, which no reply describes.
    """
    read = READERS[reader]
    try:
        return {RESULT: read(pdf)}
    except PdfTextError as error:
        return {ERROR: str(error)}
    except KeyboardInterrupt:
        raise
    # The process boundary is the reason this worker exists: any other failure of either
    # library over a hostile PDF, a SystemExit or a RecursionError among them, is reported
    # by class, as a finding for this PDF alone, rather than as a traceback.
    except BaseException as error:  # noqa: BLE001 - reported by class name, never swallowed
        return {CRASH: type(error).__name__}


def serve(pdf: str) -> int:
    """The child's entry: `main` over `pdf`, under `MEMORY_BUDGET_BYTES` where it holds."""
    limit_memory(MEMORY_BUDGET_BYTES)
    watch_memory(MEMORY_BUDGET_BYTES)
    return main(pdf)


def limit_memory(budget: int) -> bool:
    """Limit this process's address space to `budget` bytes; whether the host took it.

    Called by `serve` alone, never by a test's own process, which a budget would outlive.
    """
    if sys.platform == "win32":  # pragma: only-on win32 - no RLIMIT_AS on Windows
        return False  # pragma: only-on win32 - as above
    import resource  # POSIX only, so imported past the Windows return

    try:
        resource.setrlimit(resource.RLIMIT_AS, (budget, budget))
    except (ValueError, OSError):
        # macOS refuses a limit below its own: "current limit exceeds maximum limit".
        return False
    return True


def watch_memory(
    budget: int,
    *,
    interval: float = WATCH_INTERVAL_S,
    end: Callable[[int], object] = os._exit,
) -> threading.Thread | None:
    """Start a daemon thread ending this process once its peak resident size passes `budget`.

    It ends the process with `end(OVER_BUDGET_EXIT)`: `os._exit`, which no reader can catch,
    so each line already flushed survives and the parent reads the rest as unanswered. None,
    and no thread, on Windows. Called by `serve` alone, as `limit_memory` is.
    """
    if sys.platform == "win32":  # pragma: only-on win32 - no getrusage on Windows
        return None  # pragma: only-on win32 - as above
    import resource  # POSIX only, so imported past the Windows return

    # ru_maxrss counts bytes on macOS and kibibytes on Linux.
    scale = 1 if sys.platform == "darwin" else 1024

    def peak() -> int:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale

    watch = threading.Thread(
        target=watch_peak,
        args=(budget, interval, peak, end),
        name="memory budget",
        daemon=True,
    )
    watch.start()
    return watch


def watch_peak(
    budget: int, interval: float, peak: Callable[[], int], end: Callable[[int], object]
) -> None:
    """Read `peak` every `interval` seconds until it passes `budget`, then `end` the process.

    A peak never falls, so once past the budget a read stays past it.
    """
    while peak() <= budget:
        time.sleep(interval)
    end(OVER_BUDGET_EXIT)


def main(pdf: str) -> int:
    """Read `pdf` with every reader, writing one reply line each on stdout; 0, whatever they say."""
    path = Path(pdf)
    with open(os.devnull, "w", encoding="utf-8", newline="\n") as sink:
        for reader in READERS:
            # A library writing to either stream would corrupt a reply or swell the capture.
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                answer = reply(reader, path)
            line = json.dumps({READER: reader, **answer}, ensure_ascii=True)
            if len(line) > MAX_REPLY_CHARS:
                line = json.dumps({READER: reader, ERROR: _too_long(reader, path)})
            sys.stdout.write(f"{line}\n")
            sys.stdout.flush()
    return 0
