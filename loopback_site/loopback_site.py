"""Serve one directory on the loopback interface, and prove what it serves.

`python3 -m http.server` binds EVERY interface unless given `--bind`, which on a laptop
publishes the working tree to the local network for as long as it runs. This binds `127.0.0.1`
only, by default on a port the operating system picks, so it can neither be reached from the
network nor collide with another server already holding a fixed port -- the collision that once
had a browser silently answer from someone else's tree. A caller that needs
a fixed port (a launch configuration) names one, and a port already held is refused, exit 2.

The bind keeps other machines off the port; it does not keep off a public page open in the
viewer's own browser, which can aim a request at 127.0.0.1 and, through DNS rebinding, make it
look same-origin. So only a `Host` naming this server's own loopback port is answered. Anything
else gets 403 before a file is opened, and no response carries a CORS header.

Two ways in: the library (`serve_directory`, `page_file`, `confirm_serves`, `page_path` to
parse the pages to confirm, and `read_meta_refresh` to learn whether a page is a meta-refresh
redirect before a browser loads it) for a tool that measures through the server, and the CLI for
serving a tree to review. The CLI confirms every page byte-for-byte, prints ONE line naming
the absolute tree and the base url, and serves until SIGINT or SIGTERM. Exit codes: 0 stopped
cleanly; 2 the directory is not one, the not-found file is not in it, or a page was not
confirmed, or `--port` is already held. Flags and the run-guide live in `loopback_site.md`.

A miss is Python's own script-free error page unless the caller names a not-found file
(`not_found`, `--not-found`): then every request with no file is answered with that file and
status 404, the way GitHub Pages answers with a site's own 404 page. It is opt-in because a
caller may depend on the script-free default.

Entry point assumptions: a library caller passes an existing directory, uses the base url
`serve_directory` yields and never assumes a port. The CLI needs only the standard library and
the two folders beside this one it imports.
"""

from __future__ import annotations

import argparse
import functools
import io
import os
import signal
import socket
import socketserver
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import BinaryIO

# The shared --help formatter and UTF-8 stdio policy live in sibling folders; resolve them from
# this file's own location, not the working directory, so moving the tree needs no edit here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stdio_utf8"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "help_formatter"))

import help_formatter  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above

# Which file a server path names, the proof that a server answers with it, and the reading of
# a page's meta refresh live in this tool's private src/, apart from the serving this file
# owns. The public names are re-exported here, which is where every caller reaches them.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from loopbacksite_confirm import (  # noqa: E402 - import follows the sys.path bootstrap above
    INDEX_DOCUMENT as INDEX_DOCUMENT,
    SiteServeError as SiteServeError,
    confirm_serves as confirm_serves,
    not_found_file,
    page_file as page_file,
)
from loopbacksite_refresh import (  # noqa: E402 - import follows the sys.path bootstrap above
    LOOPBACK_HOST as LOOPBACK_HOST,
    LOOPBACK_HOSTS,
    MetaRefresh as MetaRefresh,
    parse_meta_refresh as parse_meta_refresh,
    read_meta_refresh as read_meta_refresh,
)

#: Port 0 asks the operating system for any free port; the highest a TCP port can be.
_ANY_FREE_PORT, _HIGHEST_PORT = 0, 65535

#: The Content-Type a not-found file is answered with, whatever its name. The file is a site's
#: HTML 404 page, written in UTF-8, so the header names both rather than leaving the charset to
#: sniffing.
NOT_FOUND_CONTENT_TYPE = "text/html; charset=utf-8"

#: How many connections may wait for the accept loop at once. `socketserver` passes 5 to
#: `listen`, and one browser load of a page with its stylesheets and module graph opens more
#: connections in parallel than that. On macOS a connection that finds the queue full is RESET
#: rather than retried, so a random stylesheet or module fails and the page renders broken in a
#: way the change under review did not cause. The kernel caps the value at its own maximum.
_REQUEST_QUEUE_DEPTH = 128

#: How long shutdown waits for the serving thread, which exits promptly once asked.
_JOIN_SECONDS = 10.0

#: How often the serving loop looks for a shutdown request. `shutdown()` blocks until the
#: loop's current `select` times out, so this is what every exit from `serve_directory` costs:
#: at socketserver's own 0.5 s each exit took nearly all of it, paid once per served tree -- by
#: a test suite that serves one per case, many times a run. Twenty idle wake-ups a second cost
#: nothing measurable.
_SHUTDOWN_POLL_SECONDS = 0.05

#: What a client hanging up mid-request raises in the handler: a write raises either,
#: depending on timing, and a read raises the first. A browser hangs up like this whenever it
#: closes a page or abandons a connection mid-request, so neither is a fault of the server.
_CLIENT_HANG_UPS = (ConnectionResetError, BrokenPipeError)

#: The server paths the CLI confirms when `--pages` is not given.
DEFAULT_PAGES = ("/",)

#: The characters a page to confirm may hold: printable ASCII without the space. `http.client`
#: refuses to send anything else in a request path, raising an error that is no `OSError` --
#: the very traceback a refusal at parse time exists to prevent.
PAGE_CHARACTERS = frozenset(map(chr, range(ord("!"), ord("~") + 1)))

#: Bound on each fetch the CLI makes of its own server to confirm a page.
_CONFIRM_TIMEOUT_SECONDS = 10.0

#: The signals that stop the CLI. Each gets a handler installed explicitly: a process launched
#: in the background can inherit SIGINT as ignored, and an ignored signal never becomes
#: Python's default KeyboardInterrupt.
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)

#: How often the CLI checks for a requested stop. A signal cuts a sleep short only long enough
#: to run its handler, and the sleep then resumes, so this bounds how long stopping takes.
_STOP_POLL_SECONDS = 0.2

#: Exit statuses: a clean stop, and a tree the CLI could not serve honestly.
_EXIT_STOPPED = 0
_EXIT_CANNOT_SERVE = 2


def _is_encodable_path(path: str) -> bool:
    """Whether `open()` could even try `path`, rather than refusing it with ValueError.

    `open()` refuses two kinds of name before the OS sees them, each with a ValueError: one
    holding NUL (`%00` decodes to it), and one the filesystem encoding cannot encode -- a lone
    surrogate such as `%ED%A0%80`, which the standard library's path translation decodes with
    `surrogatepass`. `os.fsencode` catches the second and passes NUL through, so both are
    tested. Every other name the OS cannot open fails with OSError, which the standard library
    already answers 404.
    """
    if "\0" in path:
        return False
    try:
        os.fsencode(path)
    # Windows encodes file names with `surrogatepass`, so a lone surrogate encodes there and
    # reaches the OS, which refuses it with the OSError answered 404: this runs on POSIX only.
    except ValueError:  # pragma: only-on posix
        return False
    return True


def _own_hosts(port: int) -> frozenset[str]:
    """The `Host` values a browser sends for this server: either loopback name, with its port."""
    return frozenset(f"{name}:{port}" for name in LOOPBACK_HOSTS)


class _DeepQueueServer(ThreadingHTTPServer):
    """A threading HTTP server whose listen queue holds a browser's burst of connections.

    Threading covers the handling, not the accepting: one loop still accepts every connection,
    so a burst queues behind it however many handler threads are free.
    """

    request_queue_size = _REQUEST_QUEUE_DEPTH

    def handle_error(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: object,
    ) -> None:
        """Drop a client's hang-up silently; report every other handler failure as usual.

        socketserver calls this from inside its `except`, so the failure is the exception
        being handled, not an argument.
        """
        if isinstance(sys.exception(), _CLIENT_HANG_UPS):
            # The default prints a full traceback here, and a passing run that closes pages
            # mid-request would then read as failing, burying any real error among the noise.
            return
        super().handle_error(request, client_address)


class _LoopbackOnlyHandler(SimpleHTTPRequestHandler):
    """A static-file handler that answers only its own loopback origin, and logs nothing.

    Given a not-found file, it answers every request that has no file with that file and
    status 404; without one, a miss is the standard library's own error page, unchanged.
    """

    def __init__(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
        server: socketserver.BaseServer,
        *,
        directory: str,
        not_found: Path | None,
    ) -> None:
        """Keep the not-found file FIRST: the base initializer handles the whole request."""
        self._not_found = not_found
        super().__init__(request, client_address, server, directory=directory)

    def send_head(self) -> io.BytesIO | BinaryIO | None:
        """Refuse a foreign `Host` with 403 before any file is opened; otherwise serve as usual.

        GET and HEAD both come through here before a file is touched, so one check covers
        both. The port is read off the connection the request arrived on, which is the port
        the browser had to name.
        """
        port = self.connection.getsockname()[1]
        if self.headers.get("Host") not in _own_hosts(port):
            self.send_error(
                HTTPStatus.FORBIDDEN, "this server answers only its own loopback origin"
            )
            return None
        if not _is_encodable_path(self.translate_path(self.path)):
            # `open()` refuses a name it cannot encode with ValueError, not the OSError the
            # standard library answers 404 for, so the handler would die mid-request: a
            # traceback here, a connection reset at the client. No file can hold the name,
            # so it is a miss like any other, answered the same 404 (the not-found file too).
            self.send_error(HTTPStatus.NOT_FOUND)
            return None
        return super().send_head()

    def send_error(
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        """Answer a 404 with the not-found file when there is one; any other error as usual.

        Every "no file here" decision the standard library makes ends in this call with 404,
        so answering here keeps its own resolution -- the index lookup, and the redirect that
        adds a directory's missing slash -- rather than re-deriving it.
        """
        body = self._not_found_body() if code == HTTPStatus.NOT_FOUND else None
        if body is None:
            super().send_error(code, message, explain)
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Type", NOT_FOUND_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def list_directory(self, path: str | os.PathLike[str]) -> io.BytesIO | None:
        """List a directory with no index as usual, unless there is a not-found file.

        GitHub Pages lists no directory, so with a not-found file such a directory is a miss.
        """
        if self._not_found is None:
            return super().list_directory(path)
        self.send_error(HTTPStatus.NOT_FOUND)
        return None

    def _not_found_body(self) -> bytes | None:
        """The not-found file's bytes, read now like any file's, or None if there are none."""
        if self._not_found is None:
            return None
        try:
            return self._not_found.read_bytes()
        except OSError:
            # Removed or unreadable since it was checked at startup. Python's own page still
            # answers 404 honestly, where failing here would drop the connection instead.
            return None

    def log_message(self, format: str, *args: object) -> None:
        """Drop the access-log line; callers report what they served themselves."""


@contextmanager
def serve_directory(
    directory: Path, *, not_found: str | None = None, port: int = _ANY_FREE_PORT
) -> Iterator[str]:
    """Serve `directory` on loopback for the length of the block, yielding its base url.

    `not_found`, a server path such as "/404.html", names the file every request with no file
    is answered with, as status 404; it is checked before the server starts. `port` 0 lets the
    operating system pick one.

    Raises:
        SiteServeError: `not_found` names no file inside `directory`.
        OSError: `port` cannot be bound, most often because something already holds it.
    """
    not_found_path = None if not_found is None else not_found_file(directory, not_found)
    handler = functools.partial(
        _LoopbackOnlyHandler, directory=str(directory), not_found=not_found_path
    )
    server = _DeepQueueServer((LOOPBACK_HOST, port), handler)
    worker = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": _SHUTDOWN_POLL_SECONDS},
        daemon=True,
    )
    worker.start()
    try:
        yield f"http://{LOOPBACK_HOST}:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=_JOIN_SECONDS)


class StopFlag:
    """Records that a stop was asked for: set by a signal handler, read by the serving loop.

    A plain attribute rather than a `threading.Event`. A handler runs on the main thread between
    bytecodes, possibly while that thread holds the event's internal lock, and setting the event
    from there could deadlock.
    """

    def __init__(self) -> None:
        """Start with no stop requested."""
        self._requested = False

    def request(self, signum: int, frame: FrameType | None) -> None:
        """Ask for a stop. Shaped as a signal handler so it installs directly."""
        self._requested = True

    def is_requested(self) -> bool:
        """Whether a stop has been asked for."""
        return self._requested


def install_stop_handlers(stop: StopFlag) -> None:
    """Make each of `STOP_SIGNALS` request `stop`, whatever disposition was inherited."""
    for signum in STOP_SIGNALS:
        signal.signal(signum, stop.request)


def wait_for_stop(stop: StopFlag, poll_seconds: float = _STOP_POLL_SECONDS) -> None:
    """Block until `stop` is requested, checking every `poll_seconds`."""
    while not stop.is_requested():
        time.sleep(poll_seconds)


def _server_path(text: str) -> str:
    """Parse the `--not-found` value: a server path, so it starts with /.

    Not held to `PAGE_CHARACTERS`: it names the file a miss is answered with on disk, and is
    never itself sent in a request.
    """
    if not text.startswith("/"):
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a server path: start it with /"
        )
    return text


def page_path(text: str) -> str:
    """Parse one page to confirm: a server path a request can carry exactly as typed.

    Public, as an argparse `type`: every CLI that confirms its pages through `confirm_serves`
    parses them with this, so the rule and its advice live once. A page outside
    `PAGE_CHARACTERS` is refused with advice to rename its file, never to percent-encode it:
    `page_file` maps a page to its file undecoded, while a server decodes the request, so an
    encoded page names no file and can never be confirmed.
    """
    _server_path(text)
    if not PAGE_CHARACTERS.issuperset(text):
        raise argparse.ArgumentTypeError(
            f"{text!r} holds a space, a control or a non-ASCII character, so it cannot be "
            "confirmed or measured: rename its file to printable ASCII without spaces"
        )
    return text


def tcp_port(text: str) -> int:
    """Parse `--port`: a whole number from 0, the operating system's pick, to 65535."""
    try:
        port = int(text)
    except ValueError:
        port = -1
    if not _ANY_FREE_PORT <= port <= _HIGHEST_PORT:
        raise argparse.ArgumentTypeError(f"{text!r} is not a port from 0 to 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    """The argument parser: named options only, each optional's live default shown."""
    parser = argparse.ArgumentParser(
        description="Serve one directory on 127.0.0.1 and confirm which tree answers.",
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=(
            "Listens on 127.0.0.1 only, on --port (0: one the operating system picks), and "
            "answers only a Host of 127.0.0.1:<port> or localhost:<port>. Fetches every --pages "
            "entry and requires its file's exact bytes (with --not-found, a page with no "
            "file must answer 404 with that file's), then prints one line naming the "
            "absolute tree and the base url, and serves until SIGINT or SIGTERM. Exit codes: "
            "0 = stopped cleanly, 2 = --directory is not a directory, --not-found names no "
            "file in it, a page was not confirmed, or --port is already held."
        ),
    )
    parser.add_argument(
        "--directory", required=True, type=Path, help="The directory to serve."
    )
    parser.add_argument(
        "--pages",
        nargs="+",
        type=page_path,
        default=list(DEFAULT_PAGES),
        metavar="PATH",
        help=(
            "Server paths confirmed against disk before the url is printed; each starts "
            "with / and holds only printable ASCII without spaces."
        ),
    )
    parser.add_argument(
        "--not-found",
        type=_server_path,
        default=None,
        metavar="PATH",
        help=(
            "Server path of the file every request with no file is answered with, as status "
            "404, the way GitHub Pages serves a site's own 404 page; starts with /. Unset, a "
            "miss gets Python's own error page."
        ),
    )
    parser.add_argument(
        "--port",
        type=tcp_port,
        default=_ANY_FREE_PORT,
        help="The loopback port to listen on; 0 lets the operating system pick a free one.",
    )
    return parser


def _confirm_pages(
    base_url: str, directory: Path, pages: Sequence[str], not_found: str | None
) -> bool:
    """Confirm every page against disk, reporting the first that fails; True when all pass."""
    for page in pages:
        try:
            confirm_serves(
                base_url,
                directory,
                page,
                timeout=_CONFIRM_TIMEOUT_SECONDS,
                not_found=not_found,
            )
        except (OSError, SiteServeError) as error:
            print(f"error: {page} was not confirmed: {error}", file=sys.stderr)
            return False
    return True


def main(argv: Sequence[str] | None = None, *, stop: StopFlag | None = None) -> int:
    """Serve `--directory` once every page is confirmed, until a stop signal; return the status.

    `stop` is a test seam: a flag already requested returns as soon as the tree is announced.
    """
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    args = build_parser().parse_args(argv)
    directory: Path = args.directory
    if not directory.is_dir():
        print(f"error: not a directory: {directory}", file=sys.stderr)
        return _EXIT_CANNOT_SERVE
    stop = StopFlag() if stop is None else stop
    # Installed before serving, so a signal that lands while pages are being confirmed is kept
    # and honored the moment the loop starts, rather than killing the process mid-confirm.
    install_stop_handlers(stop)
    return _serve_until_stopped(directory, args, stop)


def _serve_until_stopped(
    directory: Path, args: argparse.Namespace, stop: StopFlag
) -> int:
    """Serve `directory`, confirm `args.pages`, announce the tree, and serve until `stop`."""
    pages: Sequence[str] = args.pages
    not_found: str | None = args.not_found
    with ExitStack() as serving:
        try:
            base_url = serving.enter_context(
                serve_directory(directory, not_found=not_found, port=args.port)
            )
        except SiteServeError as error:
            print(f"error: {error}", file=sys.stderr)
            return _EXIT_CANNOT_SERVE
        except OSError as error:
            where = f"{LOOPBACK_HOST}:{args.port}"
            print(f"error: cannot listen on {where}: {error}", file=sys.stderr)
            return _EXIT_CANNOT_SERVE
        if not _confirm_pages(base_url, directory, pages, not_found):
            return _EXIT_CANNOT_SERVE
        print(f"serving {directory.resolve()} at {base_url}", flush=True)
        wait_for_stop(stop)
    return _EXIT_STOPPED


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
