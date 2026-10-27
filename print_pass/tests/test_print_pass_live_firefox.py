"""Grouped suite: the LIVE Firefox engine -- one page, one state, one run, inside a network fence.

The Firefox engine keeps Firefox on loopback with its prefs, a dead proxy and a non-local
guard, and keeps it out of the reader's own Firefox folders; only a real run can show those
held. So this module runs the whole tool once, with `--engine firefox`, over one page in its
one default state, through a geckodriver of the suite's own writing: a shim that records its
pid and runs the real geckodriver inside a Seatbelt fence, under which nothing but loopback
is reachable, no name is looked up, and nothing can be read or written under the two folders
the reader's own Firefox keeps on macOS -- its profiles and its caches -- each found under
both the home `$HOME` names and the one the account record holds, by its resolved path. A
fence that silently failed to apply would look like one that holds, so its canaries run
first, under the same profile file, in a process and in a child it starts, and libSystem's
`sandbox_check` is asked about the canary from outside; a fence that does not hold FAILS the
module before anything launches. While the run lasts, a watcher lists every process,
recording the shim's group and Firefox's crash helper by pid and arguments, never by name:
the reader may be running Firefox.

The page asks for an image at an RFC 5737 address and one at an RFC 6761 `.invalid` name,
asks a server of the suite's own on another loopback port for an image, a POST and a
WebSocket, holds a lazy image below a spacer taller than the window, writes a marker word into itself
at `beforeprint`, and links no icon. The cases check that the off-site images and the other
port's alone fail the state, blocked, and that the other port's server was never reached; that no `/favicon.ico` was fetched, which guards the site-icon pref; that the
lazy image arrived, which guards the lazy-load margin prefs, whose names Firefox has changed
before and whose rename would otherwise pass in silence; that the marker reached the PDF, so
Firefox's own print path ran; that Firefox's host-resolver log names no host off loopback, so
the layers inside Firefox held and not the fence alone; that nothing the run started is still
running; and that the scratch folder is gone, and named nowhere in the report.

It SKIPS, naming what is missing in the skip and in one line on stderr, off macOS or without
geckodriver, Firefox or `sandbox-exec`, even with `PAGE_PROOFS_REQUIRE_BROWSERS=1`, which
covers the engine builds and poppler alone; without poppler it skips or fails as the other
live suites do (`require_poppler`). With all of them present it never skips. No PDF it makes
is kept: every file it writes is under a temporary folder removed after it.
"""

from __future__ import annotations

import http.server
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import ClassVar, Final, cast

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
from _gecko_live_fixtures import (
    FIREFOX_APP,
    FIREFOX_RUN_BOUND_SECONDS,
    Fence,
    arm_fence,
    driver_args,
    firefox_data_folders,
    live_firefox_host,
    make_fence,
    readers_homes,
    scratch_root,
)

from _gecko_proof_fixtures import Proof, prove_fence  # beside this module
from _gecko_watch_fixtures import (  # as above
    LOOPBACK_HOSTS,
    GroupWatcher,
    looked_up_hosts,
    moz_log_text,
)
from _print_pass_fixtures import Outcome  # as above
from _print_pass_live_fixtures import (  # as above
    EXIT_FINDINGS,
    PIXEL_HEIGHT,
    PIXEL_NAME,
    PIXEL_WIDTH,
    REPORT_NAME,
    SKIP_NOTICE,
    at,
    builds_required,
    load_json,
    make_run_process,
    pixel_png,
    problems,
    require_poppler,
    served_pages,
    state_entry,
)
from gecko_prefs import LOOPBACK  # the tool's private src/
from gecko_process import ProcessRow, list_processes  # as above
from pdf_text import read_text  # as above

#: The two images off loopback: a documentation address, and a name that can never resolve.
_OFFSITE_IMAGES: Final = (
    "http://192.0.2.1/print-pass.png",
    "https://print-pass.invalid/print-pass.png",
)

#: How long the other port's server's shutdown may wait for its thread to end.
_SERVER_STOP_S: Final = 10.0

#: The word the page writes into itself at `beforeprint`, and nowhere else it renders.
_MARKER: Final = "Beforeprintmarker"

#: The spacer above the lazy image: taller than the run's window, so the image starts out
#: outside it, beyond the margin Firefox loads a lazy image within by default.
_SPACER_PX: Final = 1400


class _Recorder(http.server.BaseHTTPRequestHandler):
    """The other port's server: records each request's method and path, and answers 204."""

    hits: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        """Record the request, and answer it."""
        self._answer()

    def do_POST(self) -> None:
        """As a GET."""
        self._answer()

    def _answer(self) -> None:
        type(self).hits.append(f"{self.command} {self.path}")
        self.send_response(http.HTTPStatus.NO_CONTENT)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Write nothing: the hits are the record."""


def _other_port_urls(port: int) -> tuple[str, str, str]:
    """The other port's image, POST and WebSocket URLs."""
    base = f"{LOOPBACK}:{port}"
    return (f"http://{base}/image.png", f"http://{base}/post", f"ws://{base}/socket")


def _page(other_port: int) -> str:
    """The page: no icon link, the off-site and other-port asks, the marker, the lazy image."""
    image, post, socket = _other_port_urls(other_port)
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8"><title>firefox live</title></head>\n'
        "<body><p>A page printed through Firefox.</p>\n"
        '<p id="marker"></p>\n'
        "<script>\n"
        'addEventListener("beforeprint", () => {\n'
        f'  document.getElementById("marker").textContent = "{_MARKER}";\n'
        "});\n"
        f'fetch("{post}", {{method: "POST", mode: "no-cors", body: "x"}})'
        ".catch(() => {});\n"
        f'new WebSocket("{socket}").onerror = () => {{}};\n'
        "</script>\n"
        + "".join(
            f'<img src="{url}" alt="off loopback" width="16" height="16">\n'
            for url in (*_OFFSITE_IMAGES, image)
        )
        + f'<div style="height: {_SPACER_PX}px"></div>\n'
        f'<img src="/{PIXEL_NAME}" alt="lazy" loading="lazy" width="{PIXEL_WIDTH}"'
        f' height="{PIXEL_HEIGHT}">\n'
        "</body></html>\n"
    )


def _blocked_findings(other_port: int) -> tuple[tuple[str, str], ...]:
    """What fails the state: each image off the origin never arrives, and was blocked."""
    image, post, _ = _other_port_urls(other_port)
    return (
        *(
            ("finding", f"{what}: {url}")
            for url in (*_OFFSITE_IMAGES, image)
            for what in ("image broken", "blocked off-loopback request")
        ),
        ("finding", f"blocked off-loopback request: {post}"),
    )


#: The network record's lists the page leaves empty: a blocked request is not a failed one,
#: nothing on the page throws, and with no icon fetched nothing answers with an error.
_EMPTY_NETWORK_LISTS: Final = ("failed", "http_errors", "page_errors")

#: The run's engine. No `--timeout`: the run's bound is counted at the default.
_FIREFOX_OPTIONS: Final = ("--engine", "firefox")

#: The one state a run given no targets file prints.
_STATE: Final = "default"

#: Firefox's crash helper, where the app bundle ships it: a Firefox that has one starts it
#: beside the run, outside geckodriver's group.
_CRASH_HELPER: Final = FIREFOX_APP / "Contents" / "MacOS" / "crashhelper"


class LiveFirefoxTest(unittest.TestCase):
    """One run through the fenced geckodriver, made once for every case here.

    A run per case would spend a live bound apiece on what one run shows; each case reads
    its own part of the one run's report, PDFs, logs and process record.
    """

    fence: ClassVar[Fence]
    proof: ClassVar[Proof]
    geckodriver: ClassVar[str]
    watcher: ClassVar[GroupWatcher]
    outcome: ClassVar[Outcome]
    out: ClassVar[Path]
    survivors: ClassVar[tuple[ProcessRow, ...]]
    other_port: ClassVar[int]

    @classmethod
    def setUpClass(cls) -> None:
        """Skip on a host lacking any part; else prove the fence, arm it, and run once.

        The temporary folder, and every process the run left running, are ended by class
        cleanups, which run even when this raises: the processes first, by recorded pid
        and group alone.

        Raises:
            unittest.SkipTest: macOS, geckodriver, Firefox or `sandbox-exec` is missing;
                or poppler is, with `PAGE_PROOFS_REQUIRE_BROWSERS` unset.
            AssertionError: the fence did not hold, so nothing was launched; or the run's
                `PATH` would not find the shim; or the run outlived its bound.
            RuntimeError: the run wrote no report; or `PAGE_PROOFS_REQUIRE_BROWSERS`
                holds a value other than `1` or empty.
        """
        geckodriver, missing = live_firefox_host()
        if geckodriver is None or missing:
            # A skip whatever the opt-in, which covers the engine builds and poppler alone;
            # a value it refuses is refused here too, and the notice names what is missing.
            builds_required()
            reason = "the live Firefox run needs what this host lacks: " + "; ".join(
                missing
            )
            sys.stderr.write(f"{SKIP_NOTICE}{reason}\n")
            raise unittest.SkipTest(reason)
        require_poppler()
        cls.geckodriver = geckodriver
        folder = tempfile.TemporaryDirectory(prefix="print_pass-live-firefox-")
        cls.addClassCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        cls.fence = make_fence(root, firefox_data_folders(readers_homes()))
        cls.proof = prove_fence(cls.fence)
        if cls.proof.failures:
            raise AssertionError(
                "the fence did not hold, so nothing was launched:\n"
                + "\n".join(cls.proof.failures)
            )
        env = arm_fence(cls.fence, geckodriver, os.environ)
        cls.other_port = cls._serve_another_port()
        site = served_pages(root, {"index.html": _page(cls.other_port)})
        (site / PIXEL_NAME).write_bytes(pixel_png())
        cls.out = root / "out"
        cls.watcher = GroupWatcher(
            cls.fence.pid_file, bound_seconds=FIREFOX_RUN_BOUND_SECONDS
        )
        cls.addClassCleanup(lambda: cls.watcher.end_survivors(list_processes()))
        with cls.watcher:
            cls.outcome = make_run_process(
                site,
                cls.out,
                *_FIREFOX_OPTIONS,
                env=env,
                timeout=FIREFOX_RUN_BOUND_SECONDS,
            )
        cls.survivors = cls.watcher.survivors(list_processes())
        if not (cls.out / REPORT_NAME).is_file():
            raise RuntimeError(
                f"the run wrote no report (exit {cls.outcome.code}):\n"
                f"{cls.outcome.stderr}"
            )

    @classmethod
    def _serve_another_port(cls) -> int:
        """Start the other port's server, stopped by a class cleanup; its port."""
        _Recorder.hits.clear()
        server = http.server.ThreadingHTTPServer((LOOPBACK, 0), _Recorder)
        cls.addClassCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join, _SERVER_STOP_S)
        cls.addClassCleanup(server.shutdown)
        return int(server.server_address[1])

    def report(self) -> dict[str, object]:
        """The report the run wrote."""
        return load_json(self.out / REPORT_NAME)

    def state(self) -> dict[str, object]:
        """The report's entry for the one state the run printed."""
        return state_entry(self.report(), _STATE)

    def test_the_run_exits_with_findings(self) -> None:
        self.assertEqual(self.outcome.code, EXIT_FINDINGS, self.outcome.stderr)

    def test_the_requests_off_the_origin_alone_fail_the_state(self) -> None:
        """Exactly each blocked request's findings, in any order: nothing orders them."""
        self.assertCountEqual(
            problems(self.state()), _blocked_findings(self.other_port)
        )

    def test_another_loopback_port_was_never_reached(self) -> None:
        """Only the served origin is reachable: the other port's server heard nothing.

        Its image and its POST are blocked with the port they asked; its WebSocket, which
        Resource Timing does not time, shows only here.
        """
        self.assertEqual(_Recorder.hits, [])

    def test_every_offsite_request_is_blocked_and_nothing_else_failed(self) -> None:
        """Each is blocked, and no request failed or answered with an error.

        An empty error list is what shows Firefox fetched no `/favicon.ico`, which the
        site serves no more than it links: the site-icon pref held.
        """
        network = cast(dict[str, list[str]], at(self.state(), "network"))
        for url in (*_OFFSITE_IMAGES, *_other_port_urls(self.other_port)[:2]):
            self.assertIn(url, network["blocked"])
        for kind in _EMPTY_NETWORK_LISTS:
            self.assertEqual(network[kind], [], kind)

    def test_the_lazy_image_was_complete_at_its_natural_width(self) -> None:
        """The lazy-load margin prefs held: Firefox loaded the image as an eager one.

        Firefox ignores a pref it does not know, so a renamed margin pref leaves the image
        unloaded at print, and only a live run shows it.
        """
        images = cast(list[dict[str, object]], at(self.state(), "read_back", "images"))
        lazy = [image for image in images if image["loading"] == "lazy"]
        self.assertEqual(len(lazy), 1, images)
        self.assertIs(lazy[0]["complete"], True)
        self.assertEqual(lazy[0]["natural_width"], PIXEL_WIDTH)

    def test_the_marker_written_at_beforeprint_is_in_every_pdf(self) -> None:
        """Firefox's own print path ran: the page's `beforeprint` handler wrote the marker."""
        pdfs = cast(list[dict[str, object]], self.report()["pdfs"])
        self.assertTrue(pdfs)
        for pdf in pdfs:
            file = cast(str, pdf["file"])
            with self.subTest(pdf=file):
                text = "".join(read_text(self.out / file).pages)
                self.assertIn(_MARKER, text)

    def test_firefox_looked_up_no_host_off_loopback(self) -> None:
        """The layers inside Firefox held, not only the fence: no lookup left loopback.

        The page's own host is looked up, which shows the log was written at all.
        """
        hosts = looked_up_hosts(moz_log_text(self.fence.moz_log))
        self.assertIn(LOOPBACK, hosts)
        self.assertEqual([host for host in hosts if host not in LOOPBACK_HOSTS], [])

    def test_the_run_went_through_the_fenced_geckodriver(self) -> None:
        """The tool ran the shim, and the real geckodriver kept the pid the shim recorded."""
        self.assertIn(f"geckodriver: {self.fence.shim}", self.outcome.stdout)
        group = self.watcher.group
        self.assertIsNotNone(group)
        self.assertTrue(
            any(
                row.pid == group and row.args.startswith(self.geckodriver)
                for row in self.watcher.members.values()
            ),
            self.watcher.members,
        )

    def test_nothing_the_run_started_outlives_it(self) -> None:
        """Once the run ended, no process of its group ran, and no crash helper it recorded."""
        self.assertEqual(self.watcher.errors, [])
        self.assertTrue(self.watcher.members)
        self.assertEqual(self.survivors, ())

    def test_firefoxs_crash_helper_was_recorded_when_firefox_ships_one(self) -> None:
        """The helper half of "nothing outlives it" is not vacuous: the helper was found.

        Whether this Firefox has a helper is read from its app bundle, a file, never from
        a process's name.
        """
        if _CRASH_HELPER.is_file():
            self.assertTrue(self.watcher.helpers, self.watcher.members)

    def test_the_scratch_is_removed_and_named_nowhere_in_the_report(self) -> None:
        """The run's scratch folder, in either form a path to it takes, is not in the report."""
        root = scratch_root(driver_args(self.fence))
        text = (self.out / REPORT_NAME).read_text(encoding="utf-8")
        for form in {str(root), str(root.resolve())}:
            with self.subTest(form=form):
                self.assertNotIn(form, text)
        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
