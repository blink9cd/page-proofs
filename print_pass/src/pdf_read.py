"""Read a PDF's links, metadata and text in ONE bounded child process per PDF.

pypdf and pdfminer parse a PDF the page or its maker chose. A hostile PDF can make either
raise an exception no reader expects (measured: pypdf's AttributeError and OverflowError,
pdfminer's AssertionError on an invalid octal escape), or run for as long as it likes
(pdfminer's layout grid is quadratic in a page's size). In this process the first would end
the whole run and the second would hold it. So `read_content` runs `pdf_read_worker` in a
child `python -I`, through the shared `bounded_run`, which kills the child and its whole
tree at `timeout`. The worker runs links, metadata and text in that order and writes one
line per reader as each finishes, so a reader that answered keeps its answer when a later
one hangs, crashes the child or overflows its line.

Each reader's outcome is what pdf_text's in-process reader of the same name returns, built
back from its line, or a `PdfTextError` naming the PDF by its name alone: its own error (a
reply past `MAX_REPLY_CHARS` among them, which the worker writes as one), a crash, a line
that is no reply or is past that cap after all, or, for a reader with no
line, the timeout, the exit or the failed start that ended the child. The report makes
each failure a finding for that PDF's step; the other steps, and the other PDFs, still run.

The child is pinned apart from this process: `python -I` ignores every `PYTHON*` variable,
the working directory and the user's site folder; its program is a constant, the PDF reaches
it in argv alone, and its environment is `worker_env`'s, the few variables an interpreter
needs to start, so nothing else of this process's environment is handed to a library parsing
a hostile PDF.

Entry point assumptions: `bounded_run` sits in a folder beside this tool's own, and the
interpreter running this module can run the worker: the child is the same interpreter,
isolated, with this folder first on its path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# The shared tree-killing subprocess runner: a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place,
# and put near the front of the path, ahead of every installed distribution, so none of the
# same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run
from pdf_read_worker import (
    CRASH,
    ERROR,
    LINKS,
    MAX_REPLY_CHARS,
    MEMORY_BUDGET_BYTES,
    METADATA,
    OVER_BUDGET_EXIT,
    READER,
    READERS,
    RESULT,
    TEXT,
)
from pdf_text import Link, PdfTextError, TextLayer

#: The folder the worker is imported from: this module's own.
WORKER_FOLDER: Final = Path(__file__).resolve().parent

#: What the child runs: the worker's folder first on its path (`-I` puts none there),
#: then the worker's `serve`, its budgeted entry, over the PDF argv names. A constant, so
#: nothing of the PDF's name ever reaches code; the PDF travels in argv alone.
_LAUNCH: Final = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from pdf_read_worker import serve; raise SystemExit(serve(*sys.argv[2:]))"
)

#: The variables the child is given, when this process has them: what an interpreter needs
#: to start and to find a temporary folder and a locale, on POSIX and Windows alike. A
#: credential or a key-log path in this process's environment stays here.
_WORKER_ENV: Final = (
    "PATH",
    "SYSTEMROOT",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)

# MAX_REPLY_CHARS is the worker's own, which keeps an oversized line from being written at
# all; checked again here as a backstop, it keeps an oversized line from being parsed into
# memory. The capture itself is bounded only by the timeout.

#: An exception's name opening the last line of a traceback, as Python prints it: only a
#: word ending as an exception's name does, so a warning or a word the PDF chose is not one.
_EXCEPTION_NAME: Final = re.compile(
    r"[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt)\b"
)

#: The ending of a complete line; a line cut short by the kill has none.
_LINE_ENDS: Final = ("\n", "\r")

#: How the worker is run: (pdf, timeout) to the finished child process.
type Run = Callable[[Path, float], subprocess.CompletedProcess[str]]

#: What `read_metadata` returns: the Info fields, and the raw XMP packet or None.
type Metadata = tuple[Mapping[str, str], str | None]


@dataclass(frozen=True)
class PdfContent:
    """Each reader's outcome over one PDF: what it read, or the failure that is its finding."""

    links: tuple[Link, ...] | PdfTextError
    metadata: Metadata | PdfTextError
    text: TextLayer | PdfTextError


class _MalformedReply(ValueError):
    """A reader's line is JSON but not the shape its result has."""


class _LineFailure(Exception):
    """A reader's line says it failed, or is no reply: the message is the reason."""


def run_worker(
    pdf: Path, timeout: float, *, folder: Path = WORKER_FOLDER
) -> subprocess.CompletedProcess[str]:
    """Run the worker in `folder` over `pdf`, bounded by `timeout`.

    The PDF is named absolute, so the child's working directory does not matter.
    """
    argv = [sys.executable, "-I", "-c", _LAUNCH, str(folder), str(pdf.absolute())]
    return bounded_run.run_bounded(argv, timeout=timeout, env=worker_env())


def worker_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The child's environment: `_WORKER_ENV`'s variables `environ` (this process's) has."""
    source = os.environ if environ is None else environ
    return {name: source[name] for name in _WORKER_ENV if name in source}


# fuzz-target
def read_content(pdf: Path, *, timeout: float, run: Run = run_worker) -> PdfContent:
    """Every reader's outcome over `pdf`, read in one worker; never raises for the PDF."""
    lines, unanswered = _answers(pdf, timeout, run)
    answered = dict(zip(READERS, lines, strict=False))

    def answer_of[T](reader: str, decode: Callable[[object], T]) -> T | PdfTextError:
        line = answered.get(reader)
        if line is None:
            return _failure(reader, pdf, unanswered)
        return _decoded(reader, pdf, line, decode)

    return PdfContent(
        links=answer_of(LINKS, _links),
        metadata=answer_of(METADATA, _metadata),
        text=answer_of(TEXT, _text),
    )


def outcome[T](value: T | PdfTextError) -> T:
    """`value` as read; a reader's failure is raised, for a step that makes it a finding."""
    if isinstance(value, PdfTextError):
        raise value
    return value


def _answers(pdf: Path, timeout: float, run: Run) -> tuple[Sequence[str], str]:
    """The complete lines the worker wrote, and why a reader with none has none."""
    try:
        completed = run(pdf, timeout)
    except subprocess.TimeoutExpired as error:
        # What the post-kill read recovered, as text; None when that read expired too.
        recovered = error.output if isinstance(error.output, str) else ""
        reason = f"its reader hit the {timeout:g} s timeout and was killed"
        return _complete_lines(recovered), reason
    except OSError as error:
        # strerror, never str(): the latter quotes the interpreter's full path.
        return (), f"its reader could not be started ({error.strerror})"
    if completed.returncode == OVER_BUDGET_EXIT:
        budget = MEMORY_BUDGET_BYTES / 1024**3
        reason = f"its reader passed its {budget:g} GiB memory budget and was ended"
    elif completed.returncode != 0:
        reason = f"its reader exited {completed.returncode}{_raised(completed.stderr)}"
    else:
        reason = "its reader printed no reply"
    return _complete_lines(completed.stdout), reason


def _complete_lines(stdout: str) -> Sequence[str]:
    """Each line of `stdout` that ends: the last, cut short by a kill, is not a reply.

    A reply line holds no line break of any kind, since the worker writes it ASCII with
    every control character escaped.
    """
    return [
        line for line in stdout.splitlines(keepends=True) if line.endswith(_LINE_ENDS)
    ]


def _decoded[T](
    reader: str, pdf: Path, line: str, decode: Callable[[object], T]
) -> T | PdfTextError:
    """`reader`'s result on `line`, built by `decode`, or the failure the line says."""
    if len(line) > MAX_REPLY_CHARS:
        return _failure(
            reader, pdf, f"its reply exceeded {MAX_REPLY_CHARS:,} characters"
        )
    try:
        result = _result(reader, line)
    except _LineFailure as reason:
        return _failure(reader, pdf, str(reason))
    except PdfTextError as error:
        return error
    try:
        return decode(result)
    except (KeyError, ValueError):
        return _failure(reader, pdf, "its reader's reply is malformed")


def _failure(reader: str, pdf: Path, reason: str) -> PdfTextError:
    """A `PdfTextError` for `reader` over `pdf`, worded as pdf_text's own, by name alone."""
    return PdfTextError(f"cannot read {reader} from {pdf.name}: {reason}")


def _raised(stderr: str) -> str:
    """` (<exception>)` when stderr ends in a line opening with an exception's name.

    The name alone: a traceback's message, like its frames, can quote a path.
    """
    lines = [line for line in stderr.splitlines() if line.strip()]
    named = _EXCEPTION_NAME.match(lines[-1]) if lines else None
    return "" if named is None else f" ({named.group(0)})"


def _result(reader: str, line: str) -> object:
    """The `result` on `reader`'s `line`; its `error` is raised, and anything else refused.

    Raises:
        PdfTextError: the line holds the reader's own error, as it is worded.
        _LineFailure: the line holds a crash, or is no reply for `reader` at all.
    """
    try:
        answer = json.loads(line)
    except ValueError:
        answer = None
    if isinstance(answer, dict) and len(answer) == 2 and answer.get(READER) == reader:
        ((key, value),) = (item for item in answer.items() if item[0] != READER)
        if key == RESULT:
            return value
        if key == ERROR and isinstance(value, str):
            raise PdfTextError(value)
        if key == CRASH and isinstance(value, str):
            raise _LineFailure(f"its reader raised {value}")
    raise _LineFailure("its reader printed no reply")


def _links(result: object) -> tuple[Link, ...]:
    """The links a `links` result lists."""
    return tuple(_link(_of(entry, dict)) for entry in _of(result, list))


def _link(fields: dict[str, object]) -> Link:
    """One link, from its JSON object; a rectangle of any count but four is malformed."""
    left, bottom, right, top = (_number(edge) for edge in _of(fields["rect"], list))
    return Link(
        page=_of(fields["page"], int),
        uri=_optional_text(fields["uri"]),
        destination=_optional_text(fields["destination"]),
        rect=(left, bottom, right, top),
    )


def _metadata(result: object) -> tuple[Mapping[str, str], str | None]:
    """The Info fields and XMP packet a `metadata` result holds."""
    fields = _of(result, dict)
    info = _of(fields["info"], dict)
    return {key: _of(value, str) for key, value in info.items()}, _optional_text(
        fields["xmp"]
    )


def _text(result: object) -> TextLayer:
    """The text layer a `text` result holds."""
    fields = _of(result, dict)
    fonts = _of(fields["glyph_fonts"], dict)
    log = _of(fields["log"], dict)
    return TextLayer(
        pages=_strings(fields["pages"]),
        cid_glyphs=_of(fields["cid_glyphs"], int),
        glyph_fonts={glyph: _strings(names) for glyph, names in fonts.items()},
        log={template: _of(count, int) for template, count in log.items()},
    )


def _strings(value: object) -> tuple[str, ...]:
    """A JSON array of strings, as a tuple."""
    return tuple(_of(item, str) for item in _of(value, list))


def _optional_text(value: object) -> str | None:
    """A JSON string, or null."""
    return None if value is None else _of(value, str)


def _number(value: object) -> float:
    """A JSON number, as a float; a JSON `true` is no number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _MalformedReply(f"expected a number, not {type(value).__name__}")
    return float(value)


def _of[T](value: object, kind: type[T]) -> T:
    """`value`, which must be a `kind`; no reply field is a JSON `true` or `false`."""
    if isinstance(value, bool) or not isinstance(value, kind):
        raise _MalformedReply(f"expected {kind.__name__}, not {type(value).__name__}")
    return value
