"""Grouped suite: measuring a hosted copy (`--url`), confirmed against a local folder (`--expect`).

No test here opens a connection off this machine. The "host" is a folder this suite serves on
loopback, through the same shared server a loopback run uses, and that server's base url is
what `--url` names: the runner fetches over a real socket from a server it did not start. The
misbehaving hosts (silent, hanging up mid-body, or redirecting every page to the package's host
or a malformed address) are tiny loopback servers of the suite's own. Tests that never get past
parsing use names under `example.invalid`, which RFC 2606 reserves so no resolver answers them.
"""

# gate-serves: none (serves only temporary trees built at test time)
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import threading
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The shared loopback server, at the fixed sibling path the runner's bootstrap names, put just
# after the tool folder, ahead of every installed distribution, so none of the same name can
# shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "loopback_site"))

import lighthouse_run  # noqa: E402 - import follows the sys.path bootstrap above
import loopback_site  # noqa: E402 - import follows the sys.path bootstrap above
from _lighthouse_run_case import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    ProjectCase,
)
from _lighthouse_run_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    FAKE_COMMIT,
    PACKAGE_MARK,
    make_package,
)

#: A base no resolver answers (RFC 2606), for tests that never get past parsing.
_PARSE_ONLY_BASE = "https://site.example.invalid"

#: What the hanging host waits for at most, should the suite fail to release it.
_HANG_BOUND_SECONDS = 30.0

#: The confirmation bound while a test waits on a host that never answers.
_SHORT_CONFIRM_SECONDS = 0.5

#: How often a test server's loop looks for shutdown, which every test's cleanup waits on.
_SHUTDOWN_POLL_SECONDS = 0.05

#: More bytes than the truncating host sends, so the client's read comes up short.
_PROMISED_BYTES = 1000


class _TruncatingHandler(BaseHTTPRequestHandler):
    """Promises more bytes than it sends, then hangs up: the read raises `IncompleteRead`."""

    def do_GET(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Length", str(_PROMISED_BYTES))
        self.end_headers()
        self.wfile.write(b"<!doctype html>")

    def log_message(self, format: str, *args: object) -> None:
        """Keep the access log off the suite's stderr."""


def _redirecting_handler(target: str) -> type[BaseHTTPRequestHandler]:
    """A handler answering every page with a 301 to the same page at `target`, another origin."""

    class _Redirecting(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header("Location", target + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            """Keep the access log off the suite's stderr."""

    return _Redirecting


def _hanging_handler(release: threading.Event) -> type[BaseHTTPRequestHandler]:
    """A handler that accepts a request and answers nothing until `release` is set."""

    class _Hanging(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            release.wait(timeout=_HANG_BOUND_SECONDS)

        def log_message(self, format: str, *args: object) -> None:
            """Keep the access log off the suite's stderr."""

    return _Hanging


class HostedOptionTests(unittest.TestCase):
    """`--url` and `--expect` at parse time: a bad base or combination is refused with exit 2."""

    def refused_streams(self, *argv: str) -> tuple[str, str]:
        """What `main` writes to stdout and stderr refusing `argv`, after asserting exit 2."""
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as caught,
        ):
            lighthouse_run.main(["--root", ".", *argv])
        self.assertEqual(caught.exception.code, 2)
        return out.getvalue(), err.getvalue()

    def refusal(self, *argv: str) -> str:
        """The message `main` refuses `argv` with, after asserting it exits 2."""
        _, err = self.refused_streams(*argv)
        # Flattened: argparse wraps its message to the terminal width.
        return " ".join(err.split())

    def parsed_base(self, base: str) -> str:
        """The base url the parser keeps for `--url base`."""
        args = lighthouse_run.build_parser().parse_args(
            ["--root", ".", "--url", base, "--expect", "package"]
        )
        kept: str = args.url
        return kept

    def test_a_url_needs_expect(self) -> None:
        self.assertIn("--url needs --expect", self.refusal("--url", _PARSE_ONLY_BASE))

    def test_expect_needs_a_url(self) -> None:
        self.assertIn("--expect needs --url", self.refusal("--expect", "package"))

    def test_a_url_excludes_serve(self) -> None:
        message = self.refusal(
            "--url", _PARSE_ONLY_BASE, "--expect", "package", "--serve", "package"
        )
        self.assertIn("--url and --serve", message)

    def test_a_scheme_other_than_http_or_https_is_refused(self) -> None:
        for base in ("ftp://site.example.invalid", "site.example.invalid"):
            with self.subTest(base=base):
                message = self.refusal("--url", base, "--expect", "package")
                self.assertIn("http or https", message)

    def test_an_empty_host_is_refused(self) -> None:
        for base in ("https://", "https:///archive/", "https://:8443"):
            with self.subTest(base=base):
                message = self.refusal("--url", base, "--expect", "package")
                self.assertIn("no host", message)

    def test_a_query_is_refused(self) -> None:
        for base in (f"{_PARSE_ONLY_BASE}/?v=2", f"{_PARSE_ONLY_BASE}?"):
            with self.subTest(base=base):
                message = self.refusal("--url", base, "--expect", "package")
                self.assertIn("query", message)

    def test_a_fragment_is_refused(self) -> None:
        for base in (f"{_PARSE_ONLY_BASE}/#top", f"{_PARSE_ONLY_BASE}#"):
            with self.subTest(base=base):
                message = self.refusal("--url", base, "--expect", "package")
                self.assertIn("fragment", message)

    def test_credentials_are_refused_without_being_echoed(self) -> None:
        for base in (
            "https://reader@site.example.invalid",
            "https://reader:placeholder-secret@site.example.invalid",
        ):
            with self.subTest(base=base):
                message = self.refusal("--url", base, "--expect", "package")
                self.assertIn("credentials", message)
                self.assertNotIn("placeholder-secret", message)

    def test_a_malformed_url_is_refused_without_being_echoed(self) -> None:
        # The parser itself raises on each, and argparse would quote the whole value.
        for base in (
            "https://reader:placeholder-secret@[site.example.invalid",
            "https://[placeholder-secret]",
        ):
            with self.subTest(base=base):
                out, err = self.refused_streams("--url", base, "--expect", "package")
                self.assertIn("not well formed", " ".join(err.split()))
                self.assertNotIn("placeholder-secret", out + err)

    def test_whitespace_or_a_control_character_in_the_host_is_refused(self) -> None:
        # A tab the url parser would silently drop is refused too, not measured as another host.
        for host in (
            "site example",
            "site\texample",
            "site\x7fexample",
            "site\x00example",
        ):
            with self.subTest(host=host):
                base = f"https://{host}.invalid"
                message = self.refusal("--url", base, "--expect", "package")
                self.assertIn("whitespace or a control character", message)

    def test_the_scheme_and_host_are_lowercased_and_the_path_kept(self) -> None:
        self.assertEqual(
            self.parsed_base("HTTPS://Site.EXAMPLE.invalid:8443/Under/Path/"),
            "https://site.example.invalid:8443/Under/Path",
        )

    def test_a_port_that_is_not_a_number_is_refused(self) -> None:
        message = self.refusal(
            "--url", f"{_PARSE_ONLY_BASE}:https", "--expect", "package"
        )
        self.assertIn("port", message)

    def test_a_trailing_slash_is_stripped(self) -> None:
        self.assertEqual(self.parsed_base(_PARSE_ONLY_BASE + "/"), _PARSE_ONLY_BASE)
        self.assertEqual(self.parsed_base(_PARSE_ONLY_BASE), _PARSE_ONLY_BASE)

    def test_a_base_below_the_host_root_keeps_its_path(self) -> None:
        self.assertEqual(
            self.parsed_base(f"{_PARSE_ONLY_BASE}/site/"), f"{_PARSE_ONLY_BASE}/site"
        )

    def test_an_xn_host_and_a_percent_encoded_path_are_kept(self) -> None:
        # Both ASCII as typed, as their refusals ask: a non-ASCII name in its xn-- form (the
        # host suite refuses any other), and the path percent-encoded.
        self.assertEqual(
            self.parsed_base("https://XN--bcher-kva.example.invalid/caf%C3%A9/"),
            "https://xn--bcher-kva.example.invalid/caf%C3%A9",
        )

    def test_neither_option_is_set_by_default(self) -> None:
        args = lighthouse_run.build_parser().parse_args(["--root", "."])
        self.assertIsNone(args.url)
        self.assertIsNone(args.expect)


class _HostedCase(ProjectCase):
    """A built site outside the project, served on loopback as the "host" `--url` names."""

    def setUp(self) -> None:
        super().setUp()
        self.package = make_package(self.root.parent / "package")
        self.host = self.serve(self.package)

    def serve(self, folder: Path) -> str:
        """Serve `folder` on loopback for the rest of the test; return its base url."""
        base: str = self.enterContext(loopback_site.serve_directory(folder))
        return base

    def serve_handler(self, handler: type[BaseHTTPRequestHandler]) -> str:
        """Serve `handler` on loopback for the rest of the test; return its base url."""
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        worker = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": _SHUTDOWN_POLL_SECONDS},
            daemon=True,
        )
        worker.start()
        self.addCleanup(worker.join, _HANG_BOUND_SECONDS)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def run_hosted(
        self, *extra: str, url: str | None = None, expect: Path | None = None
    ) -> tuple[int, str, str]:
        """Run `main` against `url` (default: the package's host), expecting `expect`."""
        return self.run_main(
            "--url",
            self.host if url is None else url,
            "--expect",
            str(self.package if expect is None else expect),
            *extra,
        )

    def calls(self) -> list[dict[str, Any]]:
        """Every run the fake Lighthouse logged, in order."""
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]


class HostedRunTests(_HostedCase):
    """A hosted run measures `<base><page>` for every page and preset, and serves nothing."""

    def test_every_page_and_preset_is_measured_at_the_hosted_base(self) -> None:
        refuse_to_serve = mock.patch.object(
            loopback_site,
            "serve_directory",
            side_effect=AssertionError("a hosted run served a tree of its own"),
        )
        with refuse_to_serve as serving:
            code, _, err = self.run_hosted()
        self.assertEqual(code, 0, err)
        serving.assert_not_called()
        measured = [
            (call["argv"][0], "--preset=desktop" in call["argv"])
            for call in self.calls()
        ]
        expected = [
            (self.host + job.page, job.preset == "desktop")
            for job in lighthouse_run.build_schedule(
                lighthouse_run.DEFAULT_PAGES,
                lighthouse_run.PRESETS,
                lighthouse_run.MIN_RUNS,
            )
        ]
        self.assertEqual(measured, expected)
        self.assertEqual(
            {call["body"].endswith(PACKAGE_MARK) for call in self.calls()}, {True}
        )

    def test_a_trailing_slash_on_the_base_measures_the_same_urls(self) -> None:
        code, _, err = self.run_hosted(
            "--pages", "/archive/", "--presets", "mobile", url=self.host + "/"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            {call["argv"][0] for call in self.calls()}, {self.host + "/archive/"}
        )
        self.assertEqual(self.summary()["url"], self.host)

    def test_the_summary_names_the_base_and_the_expected_folder(self) -> None:
        code, _, err = self.run_hosted("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.summary()["url"], self.host)
        self.assertEqual(self.summary()["served"], str(self.package))

    def test_a_loopback_run_records_no_url(self) -> None:
        code, out, err = self.run_main("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0, err)
        self.assertIn("url", self.summary())
        self.assertIsNone(self.summary()["url"])
        self.assertNotIn("measuring", out)

    def test_the_progress_names_the_base_measured(self) -> None:
        code, out, err = self.run_hosted("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0, err)
        self.assertIn(f"measuring {self.host}", out)

    def test_the_record_and_progress_hold_the_normalized_base(self) -> None:
        code, out, err = self.run_hosted(
            "--pages", "/", "--presets", "mobile", url=self.host.upper()
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self.summary()["url"], self.host)
        self.assertIn(f"measuring {self.host}:", out)

    def git_folders(self) -> set[Path]:
        """Every folder the fake git ran in, resolved (an 8.3 short name on Windows)."""
        lines = self.git_log.read_text(encoding="utf-8").splitlines()
        return {Path(json.loads(line)["cwd"]).resolve() for line in lines}

    def test_the_commit_is_read_for_the_expected_folder(self) -> None:
        code, _, err = self.run_hosted("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.git_folders(), {self.package.resolve()})
        self.assertEqual(self.summary()["commit"], FAKE_COMMIT)

    def test_a_package_outside_any_repository_has_an_unknown_commit(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_GIT_MODE": "outside"}):
            code, out, err = self.run_hosted("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0, err)
        self.assertIsNone(self.summary()["commit"])
        self.assertIsNone(self.summary()["src_dirty"])
        self.assertIn("commit unknown", out)
        self.assertEqual(err, "")


class HostedPreflightTests(_HostedCase):
    """What a hosted run needs before it starts: the pinned Lighthouse and an `--expect` folder."""

    def test_a_hosted_run_needs_no_src_under_the_root(self) -> None:
        shutil.rmtree(self.root / "src")
        code, _, err = self.run_hosted("--pages", "/", "--presets", "mobile")
        self.assertEqual(code, 0, err)

    def test_a_hosted_run_still_needs_the_pinned_lighthouse(self) -> None:
        for launcher in (self.root / "node_modules" / ".bin").glob("lighthouse*"):
            launcher.unlink()
        code, _, err = self.run_hosted()
        self.assertEqual(code, 2)
        self.assertIn("npm ci", err)
        self.assertFalse(self.log.exists())

    def test_an_expect_that_is_not_a_folder_is_refused(self) -> None:
        for expect in (self.package / "index.html", self.root.parent / "missing"):
            with self.subTest(expect=expect):
                code, _, err = self.run_hosted(expect=expect)
                self.assertEqual(code, 2)
                self.assertIn(f"--expect {expect} is not a folder", err)
                self.assertFalse(self.log.exists())


class HostedConfirmTests(_HostedCase):
    """A page the host does not answer with the expected bytes exits 2, measuring nothing."""

    def assert_refused(self, code: int, err: str, page: str) -> None:
        """Assert a refusal naming `page`, with no Lighthouse run made."""
        self.assertEqual(code, 2)
        self.assertIn(f"{page} was not confirmed", err)
        self.assertFalse(self.log.exists())

    def test_a_host_serving_other_bytes_is_refused_naming_the_page(self) -> None:
        # src/ holds the same pages without the package's mark: the host is a stale copy.
        stale = self.serve(self.root / "src")
        code, _, err = self.run_hosted("--pages", "/archive/", url=stale)
        self.assert_refused(code, err, "/archive/")
        self.assertIn(f"cannot measure {stale}", err)
        self.assertIn("/archive/ was not confirmed: the server did not answer", err)
        self.assertEqual(err.count("/archive/"), 1, err)

    def test_a_page_the_host_does_not_have_is_refused(self) -> None:
        # The package's own bytes, so the home page confirms and only the missing page fails.
        partial = make_package(self.root.parent / "partial")
        # The whole folder: an empty one would be listed with 200, not refused with 404.
        shutil.rmtree(partial / "archive")
        code, _, err = self.run_hosted(
            "--pages", "/", "/archive/", url=self.serve(partial)
        )
        self.assert_refused(code, err, "/archive/")
        self.assertIn(str(HTTPStatus.NOT_FOUND.value), err)

    def test_a_page_the_host_redirects_is_refused(self) -> None:
        # Followed, the redirect would land on the package's own bytes and pass: every run
        # would then measure through a hop no summary records.
        redirecting = self.serve_handler(_redirecting_handler(self.host))
        code, _, err = self.run_hosted("--pages", "/", url=redirecting)
        self.assert_refused(code, err, "/")
        self.assertIn(str(HTTPStatus.MOVED_PERMANENTLY.value), err)
        self.assertNotIn(self.host, err)

    def test_a_redirect_to_a_malformed_address_is_refused_without_quoting_it(
        self,
    ) -> None:
        # urllib parses a redirect's address before any handler may decline to follow it,
        # so a malformed one escaped as a traceback quoting part of the header.
        malformed = self.serve_handler(
            _redirecting_handler("http://[placeholder-target]")
        )
        code, out, err = self.run_hosted("--pages", "/", url=malformed)
        self.assert_refused(code, err, "/")
        self.assertIn(str(HTTPStatus.MOVED_PERMANENTLY.value), err)
        self.assertNotIn("placeholder-target", out + err)

    def test_a_non_ascii_path_in_the_url_is_refused_at_parse_time(self) -> None:
        # The host answers, so only the request could fail: http.client refuses to send a
        # non-ASCII character, and that escaped `main` as a traceback.
        base = f"{self.host}/café"
        argv = ["--root", str(self.root), "--url", base, "--expect", str(self.package)]
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            self.assertRaises(SystemExit) as caught,
        ):
            lighthouse_run.main([*argv, "--pages", "/"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("percent-encode", err.getvalue())
        # Never echoed: a url may carry credentials.
        self.assertNotIn("café", out.getvalue() + err.getvalue())
        self.assertNotIn(self.host, out.getvalue() + err.getvalue())
        self.assertFalse(self.log.exists())

    def test_a_page_missing_from_the_expected_folder_is_refused(self) -> None:
        code, _, err = self.run_hosted("--pages", "/missing.html")
        self.assert_refused(code, err, "/missing.html")

    def test_an_unreachable_host_is_refused(self) -> None:
        # A port that was just served and closed: nothing listens there now.
        with loopback_site.serve_directory(self.package) as closed:
            pass
        code, _, err = self.run_hosted("--pages", "/", url=closed)
        self.assert_refused(code, err, "/")

    def test_a_host_that_never_answers_is_bounded(self) -> None:
        release = threading.Event()
        silent = self.serve_handler(_hanging_handler(release))
        # Registered after the server's cleanups, so it runs first and frees the handler.
        self.addCleanup(release.set)
        with mock.patch.object(
            lighthouse_run, "_CONFIRM_TIMEOUT_SECONDS", _SHORT_CONFIRM_SECONDS
        ):
            code, _, err = self.run_hosted("--pages", "/", url=silent)
        self.assert_refused(code, err, "/")
        self.assertIn("timed out", err)

    def test_a_connection_closed_mid_body_is_refused(self) -> None:
        # IncompleteRead is an http.client.HTTPException, not an OSError: a real network
        # produces it, and it must refuse rather than escape as a traceback.
        truncating = self.serve_handler(_TruncatingHandler)
        code, _, err = self.run_hosted("--pages", "/", url=truncating)
        self.assert_refused(code, err, "/")
        self.assertIn("IncompleteRead", err)


if __name__ == "__main__":
    unittest.main()
