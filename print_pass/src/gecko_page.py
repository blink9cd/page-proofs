"""What a Firefox state runs in its page, and what it accepts back from each script.

The Firefox engine reads its page through WebDriver's Execute Script, synchronous only: a
script that returned a Promise would never settle with the page's scripts off, so none here
does. Each returns plain arrays of strings and numbers, and each answer is type-checked here
before anything is built from it, since a page can redefine `performance`, `innerWidth` or
anything else a script reads: an answer is data the page handed back, not a fact about it.

- `VIEWPORT_SCRIPT` reads the viewport the page is laid out in (`viewport_size`);
- `SEED_SCRIPT` reads whether the storage seed page, a reserved missing path on the served
  origin, is the loopback server's own script-free error page (`seed_confirmed`), and
  `STORE_SCRIPT` then sets each stored value there, before the page's own scripts can run;
- `VISIBLE_SCRIPT` reads whether a selector's first match is visible as Playwright defines
  it: a box of some width and height, and a computed `visibility` other than `hidden`;
- `TIMING_SCRIPT` reads the document's navigation row and every resource row as pairs of a
  URL and a status (`timing_rows`);
- `PROBE_SCRIPT` is the Chromium engine's own probe, called on its argument, which
  `ProbePage` hands to `print_probe.run_probe` as the page it evaluates in.

Two checks on what reaches a state's record are here too: `is_served`, whether the document
is on the served origin, and `without_scratch`, the scratch folder's path taken out of the
log before any of it is recorded.

Entry point assumptions: the standard library, and the tool's own `gecko_network` (the row
type), `print_probe` (the probe's source) and `print_record` (the error) only.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final, Protocol, TypeGuard
from urllib.parse import urlsplit

from gecko_network import TimingEntry
from print_probe import PROBE_SOURCE
from print_record import EngineError

#: The viewport's width and height, as the page reads them.
VIEWPORT_SCRIPT: Final = "return [window.innerWidth, window.innerHeight];"

#: The reserved path the storage seed is set on: a missing one, so the loopback server
#: answers it with its own error page, which runs no script. The leading dot keeps it out of
#: the way of any page a site serves.
SEED_PATH: Final = "/.print-pass-storage-seed"

#: The seed page's own navigation status, when it has exactly one, and its script count.
SEED_SCRIPT: Final = (
    "const rows = performance.getEntriesByType('navigation');"
    " return [rows.length === 1 ? rows[0].responseStatus : null, document.scripts.length];"
)

#: Each `[key, value]` pair of its argument, set in the origin's `localStorage`.
STORE_SCRIPT: Final = (
    "for (const [key, value] of arguments[0]) { localStorage.setItem(key, value); }"
    " return null;"
)

#: Whether the selector's first match is visible as Playwright defines it.
VISIBLE_SCRIPT: Final = (
    "const element = document.querySelector(arguments[0]);"
    " if (element === null) { return false; }"
    " const box = element.getBoundingClientRect();"
    " return box.width > 0 && box.height > 0"
    " && getComputedStyle(element).visibility !== 'hidden';"
)

#: The navigation rows, then the resource rows, each as `[name, responseStatus]`.
TIMING_SCRIPT: Final = (
    "const pair = (entry) => [entry.name, entry.responseStatus];"
    " return [performance.getEntriesByType('navigation').map(pair),"
    " performance.getEntriesByType('resource').map(pair)];"
)

#: What the scratch folder's path is written as wherever a log line held it.
SCRATCH_TOKEN: Final = "<scratch>"

# The status the seed page must read, and the scripts it must hold.
_SEED_STATUS: Final = 404
_SEED_SCRIPTS: Final = 0

# The widest status a row may hold: HTTP's are three digits, and 0 is no response.
_HIGHEST_STATUS: Final = 999

# The two parts of a read: navigation rows and resource rows; and of a row: URL and status.
_PARTS: Final = 2

# What a malformed Resource Timing read is refused with, before the problem itself.
_MALFORMED: Final = "Resource Timing read back malformed"


class Executor(Protocol):
    """A WebDriver session, as far as the probe's adapter is concerned: Execute Script."""

    def execute(self, script: str, *args: object) -> object:
        """Run `script` as a synchronous function body given `args`; what it returns."""


def probe_script(source: str) -> str:
    """The probe's function expression `source`, called on the script's first argument."""
    return "return (\n" + source.strip().removesuffix(";") + "\n)(arguments[0]);"


#: The Chromium engine's probe, as a synchronous script.
PROBE_SCRIPT: Final = probe_script(PROBE_SOURCE)


class ProbePage:
    """`print_probe`'s page over a WebDriver session: the probe run as a synchronous script."""

    def __init__(self, session: Executor) -> None:
        """Run the probe in `session`."""
        self._session = session

    def evaluate(self, expression: str, arg: object = None) -> object:
        """Call the function expression `expression` on `arg` in the page; what it returns."""
        return self._session.execute(probe_script(expression), arg)


def viewport_size(raw: object) -> tuple[int, int]:
    """The width and height `VIEWPORT_SCRIPT` read, each a positive whole number.

    Raises:
        EngineError: `raw` is not two positive whole numbers.
    """
    if isinstance(raw, list) and len(raw) == _PARTS:
        width, height = raw
        if _is_whole(width) and _is_whole(height) and width > 0 and height > 0:
            return width, height
    raise EngineError("the viewport read back as no width and height")


def seed_confirmed(raw: object) -> bool:
    """Whether `SEED_SCRIPT` read the loopback server's own error page: a 404, no script."""
    if not (isinstance(raw, list) and len(raw) == _PARTS):
        return False
    status, scripts = raw
    return (
        _is_whole(status)
        and _is_whole(scripts)
        and (status, scripts) == (_SEED_STATUS, _SEED_SCRIPTS)
    )


# fuzz-target
def timing_rows(raw: object) -> tuple[TimingEntry, tuple[TimingEntry, ...]]:
    """The document's one navigation row and the resource rows `TIMING_SCRIPT` read.

    Raises:
        EngineError: `raw` is not two lists, the first holding exactly one row, of rows
            each a URL and a whole status from 0 to 999; the message names the problem.
    """
    if not (
        isinstance(raw, list)
        and len(raw) == _PARTS
        and all(isinstance(part, list) for part in raw)
    ):
        raise _malformed("not a navigation list and a resource list")
    navigation, resources = raw
    if len(navigation) != 1:
        raise _malformed(f"{len(navigation)} navigation rows, not 1")
    return _row(navigation[0]), tuple(_row(row) for row in resources)


def is_served(url: str, base_url: str) -> bool:
    """Whether `url` is on `base_url`'s origin: the same scheme, host and port.

    A URL with no host -- `about:`, `data:`, an object URL's `blob:` -- is on none, and an
    origin that cannot be read matches none, so a base that does not parse serves nothing.
    """
    origin = _origin(url)
    return origin is not None and origin == _origin(base_url)


def scratch_forms(root: Path) -> tuple[str, ...]:
    """The forms a log can name the scratch folder by: as made, and resolved.

    They differ where the temporary folder is reached through a symlink, as macOS's is.
    """
    return (str(root), str(root.resolve()))


def without_scratch(text: str, forms: Sequence[str]) -> str:
    """`text` with each of `forms` written as `SCRATCH_TOKEN`, the longest first.

    The longest first, since a resolved path can hold the path it resolved from.
    """
    for form in sorted(set(forms), key=len, reverse=True):
        text = text.replace(form, SCRATCH_TOKEN)
    return text


def _row(raw: object) -> TimingEntry:
    """One row read back, as a `TimingEntry`; `EngineError` unless a URL and a status."""
    if not (
        isinstance(raw, list)
        and len(raw) == _PARTS
        and isinstance(raw[0], str)
        and _is_whole(raw[1])
    ):
        raise _malformed("a row is not a URL and a status")
    name, status = raw
    if not 0 <= status <= _HIGHEST_STATUS:
        raise _malformed(f"a status of {status} is no HTTP status")
    return TimingEntry(name=name, responseStatus=status)


def _is_whole(value: object) -> TypeGuard[int]:
    """Whether `value` is an int and not a bool, which JSON's true and false read as."""
    return type(value) is int


def _malformed(problem: str) -> EngineError:
    """The error a malformed Resource Timing read is refused with, naming `problem`."""
    return EngineError(f"{_MALFORMED}: {problem}")


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """`url`'s scheme, host and port; None when it names no host, or does not parse."""
    try:
        parts = urlsplit(url)
        port = parts.port  # raises on a port that is not one
    except ValueError:
        return None
    if parts.hostname is None:
        return None
    return (parts.scheme, parts.hostname, port)
