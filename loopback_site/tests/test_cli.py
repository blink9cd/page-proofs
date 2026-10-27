"""Grouped suite: the CLI -- its options, its stop signals, and a real serving process.

The live tests launch the script as a child and signal it, so the POSIX-only freeze and thaw
helpers live here with them.
"""

from __future__ import annotations

import contextlib
import errno
import http.client
import io
import os
import re
import selectors
import signal
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import loopback_site  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    TIMEOUT,
    restore_stop_dispositions,
    scratch_tree,
)


#: The CLI the live tests launch as a real process, anchored on the imported subject rather than
#: on this file, so it names the same script from whichever directory this test runs in.
_SCRIPT = Path(loopback_site.__file__).resolve()


#: Bound on a launched CLI printing its ready line, and on it exiting or stopping once signaled.
_STARTUP_SECONDS = 30.0


_EXIT_SECONDS = 10.0


#: How often a test re-reads a launched CLI's wait status while waiting for it to stop.
_POLL_SECONDS = 0.01


#: Connections the burst test opens while the server accepts none, so all wait in the listen
#: queue at once: more than a browser's parallel page load, far past `socketserver`'s 5.
_BURST_CONNECTIONS = 64


#: The one line the CLI prints once every page is confirmed.
_READY_LINE = re.compile(
    r"^serving (?P<tree>.+) at (?P<url>http://127\.0\.0\.1:[1-9][0-9]*)$"
)


#: Execs the CLI with SIGINT already ignored, as a non-interactive shell's `&` hands it to a
#: background child. `exec` keeps an ignored disposition, so only a handler the CLI installs
#: for itself can make SIGINT stop it.
_EXEC_WITH_SIGINT_IGNORED = (
    "import os, signal, sys\n"
    "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
    "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
)


def _free_port() -> int:
    """A loopback port nothing listens on now: the OS picks one, and it is let go at once."""
    with socket.create_server((loopback_site.LOOPBACK_HOST, 0)) as probe:
        return int(probe.getsockname()[1])


def _send_get(port: int, page: str) -> http.client.HTTPConnection | OSError:
    """Connect to `port` and send a GET for `page`, or return the error that refused it."""
    connection = http.client.HTTPConnection(
        loopback_site.LOOPBACK_HOST, port, timeout=TIMEOUT
    )
    try:
        connection.request("GET", page)
    except OSError as error:
        connection.close()
        return error
    return connection


def _answer(sent: http.client.HTTPConnection | OSError) -> tuple[int, bytes] | str:
    """The status and body a sent GET was answered with, or the name of the error it met."""
    if isinstance(sent, OSError):
        return type(sent).__name__
    try:
        reply = sent.getresponse()
        return reply.status, reply.read()
    except OSError as error:
        return type(error).__name__
    finally:
        sent.close()


# SIGSTOP, SIGCONT and waitpid's report of a stopped child are POSIX-only. Each function opens
# with a platform ``raise`` rather than being defined inside an ``if``: mypy stops checking a
# body after an always-true platform raise, so a win32 type-check pass still sees
# both names, and a module-level ``if`` is a statement no split of this suite could carry. The
# win32 arm exists only for that pass; the live tests that call these are skipped off POSIX.
def _freeze(process: subprocess.Popen[str], seconds: float) -> bool:
    """Stop `process` with SIGSTOP; whether it stopped within `seconds`, per its wait status.

    Sending the signal only posts it; the wait status confirms the stop took. A child that
    exits instead is reported at once as not stopped.
    """
    if sys.platform == "win32":
        raise OSError(errno.ENOSYS, "SIGSTOP needs POSIX, which Windows lacks")
    process.send_signal(signal.SIGSTOP)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        pid, status = os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)
        if pid:
            return os.WIFSTOPPED(status)
        time.sleep(_POLL_SECONDS)
    return False


def _thaw(process: subprocess.Popen[str]) -> None:
    """Resume a process `_freeze` stopped."""
    if sys.platform == "win32":
        raise OSError(errno.ENOSYS, "SIGCONT needs POSIX, which Windows lacks")
    process.send_signal(signal.SIGCONT)


class ParserTests(unittest.TestCase):
    """The CLI takes a required directory and server paths that each start with a slash."""

    def test_directory_is_required(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                loopback_site.build_parser().parse_args([])
        self.assertEqual(caught.exception.code, 2)

    def test_pages_default_to_the_root(self) -> None:
        args = loopback_site.build_parser().parse_args(["--directory", "site"])
        self.assertEqual((args.directory, args.pages), (Path("site"), ["/"]))

    def test_takes_several_pages(self) -> None:
        args = loopback_site.build_parser().parse_args(
            ["--directory", "site", "--pages", "/", "/archive/"]
        )
        self.assertEqual(args.pages, ["/", "/archive/"])

    def test_refuses_a_page_without_a_leading_slash(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                loopback_site.build_parser().parse_args(
                    ["--directory", "site", "--pages", "archive/"]
                )
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("archive/", stderr.getvalue())

    def test_the_port_defaults_to_one_the_operating_system_picks(self) -> None:
        args = loopback_site.build_parser().parse_args(["--directory", "site"])
        self.assertEqual(args.port, 0)

    def test_takes_a_fixed_port(self) -> None:
        args = loopback_site.build_parser().parse_args(
            ["--directory", "site", "--port", "8123"]
        )
        self.assertEqual(args.port, 8123)

    def test_refuses_a_port_outside_the_tcp_range_or_not_a_number(self) -> None:
        for text in ("-1", "65536", "eighty"):
            with self.subTest(port=text):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as caught:
                        loopback_site.build_parser().parse_args(
                            ["--directory", "site", "--port", text]
                        )
                self.assertEqual(caught.exception.code, 2)
                self.assertIn(text, stderr.getvalue())

    def test_a_not_found_path_is_not_held_to_a_requests_characters(self) -> None:
        # It names the file a miss is answered with and is never itself sent in a request,
        # so a name a request could not carry still serves every miss.
        args = loopback_site.build_parser().parse_args(
            ["--directory", "site", "--not-found", "/café page.html"]
        )
        self.assertEqual(args.not_found, "/café page.html")


class StopTests(unittest.TestCase):
    """SIGINT and SIGTERM each request a stop, whatever disposition the process inherited."""

    def setUp(self) -> None:
        restore_stop_dispositions(self)

    def test_each_stop_signal_gets_a_handler_even_when_inherited_as_ignored(
        self,
    ) -> None:
        # The ignored disposition is set FIRST, so a missing handler leaves the signal a no-op
        # here rather than killing the suite -- and the disposition assertion, not the flag,
        # is what names that defect (a background launch hands SIGINT over ignored).
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum.name):
                signal.signal(signum, signal.SIG_IGN)
                flag = loopback_site.StopFlag()
                loopback_site.install_stop_handlers(flag)
                self.assertNotIn(
                    signal.getsignal(signum), (signal.SIG_IGN, signal.SIG_DFL)
                )
                self.assertFalse(flag.is_requested())
                signal.raise_signal(signum)
                self.assertTrue(flag.is_requested())

    def test_waits_until_a_stop_is_requested(self) -> None:
        class _RequestedOnThirdCheck(loopback_site.StopFlag):
            """A flag that reports a stop only once it has been checked twice already."""

            def __init__(self) -> None:
                super().__init__()
                self.checks = 0

            def is_requested(self) -> bool:
                self.checks += 1
                return self.checks >= 3

        flag = _RequestedOnThirdCheck()
        loopback_site.wait_for_stop(flag, poll_seconds=0.001)
        self.assertEqual(flag.checks, 3)


class MainTests(unittest.TestCase):
    """`main` refuses what it cannot serve honestly, and announces only a confirmed tree."""

    def setUp(self) -> None:
        restore_stop_dispositions(self)
        self.tree = scratch_tree(self)

    def _run(
        self, argv: list[str], stop: loopback_site.StopFlag | None = None
    ) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = loopback_site.main(argv, stop=stop)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_a_missing_directory_exits_two(self) -> None:
        code, stdout, stderr = self._run(["--directory", str(self.tree / "absent")])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("absent", stderr)

    def test_a_file_named_as_the_directory_exits_two(self) -> None:
        code, stdout, _ = self._run(["--directory", str(self.tree / "404.html")])
        self.assertEqual((code, stdout), (2, ""))

    def test_an_unconfirmable_page_exits_two_before_announcing_anything(self) -> None:
        code, stdout, stderr = self._run(
            ["--directory", str(self.tree), "--pages", "/", "/missing.html"]
        )
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("/missing.html", stderr)

    def test_a_page_a_request_cannot_carry_is_refused_at_parse_time(self) -> None:
        # Each page has its file, so only the request could fail: http.client refuses to send
        # a non-ASCII, space or control character, and that escaped `main` as a traceback.
        # The stop is already requested, so a page wrongly confirmed returns, never serves on.
        stop = loopback_site.StopFlag()
        stop.request(signal.SIGTERM, None)
        for page, file in (
            ("/café/", "café/index.html"),
            ("/a b.html", "a b.html"),
            ("/a\x7fb.html", "a\x7fb.html"),
        ):
            with self.subTest(page=page):
                (self.tree / file).parent.mkdir(exist_ok=True)
                (self.tree / file).write_bytes(b"<!doctype html>\n")
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    self.assertRaises(SystemExit) as caught,
                ):
                    loopback_site.main(
                        ["--directory", str(self.tree), "--pages", "/", page], stop=stop
                    )
                self.assertEqual((caught.exception.code, stdout.getvalue()), (2, ""))
                # Renaming, never percent-encoding: a page is mapped to its file undecoded,
                # while the server decodes the request, so an encoded page names no file.
                message = " ".join(stderr.getvalue().split())
                self.assertIn("rename its file to printable ASCII", message)
                self.assertNotIn("percent-encode", message)

    def test_announces_the_tree_and_url_once_every_page_is_confirmed(self) -> None:
        stop = loopback_site.StopFlag()
        stop.request(signal.SIGTERM, None)
        code, stdout, stderr = self._run(
            ["--directory", str(self.tree), "--pages", "/", "/archive/"], stop
        )
        self.assertEqual((code, stderr), (0, ""))
        lines = stdout.splitlines()
        self.assertEqual(len(lines), 1)
        ready = _READY_LINE.match(lines[0])
        self.assertIsNotNone(ready)
        assert ready is not None  # narrows for the group read below
        self.assertEqual(ready.group("tree"), str(self.tree.resolve()))

    def test_serves_on_the_port_it_is_given(self) -> None:
        stop = loopback_site.StopFlag()
        stop.request(signal.SIGTERM, None)
        port = _free_port()
        code, stdout, stderr = self._run(
            ["--directory", str(self.tree), "--port", str(port)], stop
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(
            stdout,
            f"serving {self.tree.resolve()} at http://127.0.0.1:{port}\n",
        )

    def test_a_port_already_held_exits_two_naming_it(self) -> None:
        # Held on loopback by a listening socket, as another session's review server would
        # hold it: the bind must fail as a refusal, not escape `main` as a traceback.
        with socket.create_server((loopback_site.LOOPBACK_HOST, 0)) as holder:
            port = holder.getsockname()[1]
            code, stdout, stderr = self._run(
                ["--directory", str(self.tree), "--port", str(port)]
            )
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn(str(port), stderr)

    def test_a_listen_failure_without_an_os_reason_still_names_one(self) -> None:
        # An OSError raised with only a message has no strerror; printing that attribute
        # said "None" instead of the reason.
        failure = OSError("no loopback interface")
        with mock.patch.object(loopback_site, "serve_directory", side_effect=failure):
            code, stdout, stderr = self._run(["--directory", str(self.tree)])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("no loopback interface", stderr)
        self.assertNotIn("None", stderr)


@unittest.skipUnless(os.name == "posix", "signals are delivered to a process on POSIX")
class LiveCliTests(unittest.TestCase):
    """The real CLI, as a real process: it answers a burst, serves until signaled, exits 0, lets go of the port."""

    def setUp(self) -> None:
        self.tree = scratch_tree(self)

    def _launch(self, *, sigint_ignored: bool) -> subprocess.Popen[str]:
        argv = [str(_SCRIPT), "--directory", str(self.tree)]
        prefix = ["-c", _EXEC_WITH_SIGINT_IGNORED] if sigint_ignored else []
        process = subprocess.Popen(
            [sys.executable, *prefix, *argv],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.addCleanup(self._reap, process)
        return process

    @staticmethod
    def _reap(process: subprocess.Popen[str]) -> None:
        """Kill a process a failed test left running, and close its pipes."""
        if process.poll() is None:
            process.kill()
        process.wait(timeout=_EXIT_SECONDS)
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()

    def _ready_url(self, process: subprocess.Popen[str]) -> str:
        """The base url from the CLI's ready line, read within the startup bound."""
        assert process.stdout is not None  # piped above
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            ready = selector.select(timeout=_STARTUP_SECONDS)
        self.assertTrue(ready, f"no ready line within {_STARTUP_SECONDS}s")
        match = _READY_LINE.match(process.stdout.readline().rstrip("\n"))
        self.assertIsNotNone(match)
        assert match is not None  # narrows for the group reads below
        self.assertEqual(match.group("tree"), str(self.tree.resolve()))
        return match.group("url")

    def _assert_stops_cleanly(
        self, signum: signal.Signals, *, sigint_ignored: bool
    ) -> None:
        process = self._launch(sigint_ignored=sigint_ignored)
        url = self._ready_url(process)
        with urllib.request.urlopen(url + "/archive/", timeout=TIMEOUT) as reply:
            self.assertEqual(
                reply.read(), (self.tree / "archive" / "index.html").read_bytes()
            )
        process.send_signal(signum)
        self.assertEqual(process.wait(timeout=_EXIT_SECONDS), 0)
        assert process.stderr is not None  # piped above
        self.assertEqual(process.stderr.read(), "")
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(url + "/", timeout=TIMEOUT)

    def test_serves_until_sigterm_then_exits_zero(self) -> None:
        self._assert_stops_cleanly(signal.SIGTERM, sigint_ignored=False)

    def test_sigint_stops_it_even_when_launched_with_sigint_ignored(self) -> None:
        self._assert_stops_cleanly(signal.SIGINT, sigint_ignored=True)

    def test_answers_a_burst_of_simultaneous_connections_resetting_none(self) -> None:
        process = self._launch(sigint_ignored=False)
        port = urlsplit(self._ready_url(process)).port
        assert port is not None  # the ready line's url always names its port
        # Stopped, the server accepts nothing, so the whole burst waits in its listen queue at
        # once: the state a browser's parallel page load reaches whenever connections arrive
        # faster than the accept loop takes them, made certain here instead of left to a race.
        try:
            self.assertTrue(_freeze(process, _EXIT_SECONDS))
            sent = [_send_get(port, "/archive/") for _ in range(_BURST_CONNECTIONS)]
        finally:
            _thaw(process)
        page = (self.tree / "archive" / "index.html").read_bytes()
        self.assertEqual(
            Counter(map(_answer, sent)), Counter({(200, page): _BURST_CONNECTIONS})
        )
