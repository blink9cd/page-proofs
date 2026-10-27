"""Suite: a Firefox state's network record, from its Resource Timing rows and its log slice.

Firefox has no request listener the engine can reach, so what a state's page asked the
network for is read back from the page's own Resource Timing rows and the document's
navigation row, and what its script threw from geckodriver's log. The record they make means
what the Chromium engine's means, entry for entry: a request off loopback listed whole as
blocked, a loopback one by its path and query, never its port. A page error alone reads
otherwise, keeping its error's class name as Firefox's log gives it. What the rows show
beyond the record is a timing problem, and a page that left loopback is an isolation
failure, which the record never holds. Each case compares whole records and whole texts.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Final

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_network  # noqa: E402 - the tool's private src/, via the insert above
from gecko_network import TimingEntry  # noqa: E402 - as above
from print_record import NetworkRecord, Problem  # noqa: E402 - as above

#: The served origin, as the loopback server hands it out.
_BASE: Final = "http://127.0.0.1:8123"

#: A record of a page that asked for nothing it did not get.
_QUIET: Final = NetworkRecord(
    failed=(), http_errors=(), blocked=(), page_errors=(), csp_violations=()
)


def _row(url: str, status: int = 200) -> TimingEntry:
    """One Resource Timing row: the URL requested and the status it got."""
    return {"name": url, "responseStatus": status}


def _document(path: str = "/", status: int = 200) -> TimingEntry:
    """The document's own navigation row, on the served origin."""
    return _row(_BASE + path, status)


def _record(
    *entries: TimingEntry, document: TimingEntry | None = None, log: str = ""
) -> NetworkRecord:
    """The record of a page served at `_BASE` whose rows and log slice are as given."""
    return gecko_network.network_record(entries, document or _document(), _BASE, log)


#: Every line Firefox 157 wrote to geckodriver's log naming a script error, as measured for
#: a page on the served origin whose scripts threw: inline, from a file, in an eval, from a
#: timer, as an unhandled rejection, from `data:` and `blob:` scripts, in `srcdoc` and
#: `data:` frames; then the page's console quoting the form, and the browser's own script.
#: The served port is written as `_BASE`'s. The timer's message had a second line, which
#: Firefox wrote as a line of its own.
_MEASURED_LINES: Final = (
    f"JavaScript error: {_BASE}/errors.html, line 2: Error: inline top-level",
    f"JavaScript error: {_BASE}/thrower.js, line 1: TypeError: external file",
    f"JavaScript error: {_BASE}/errors.html line 4 > eval, line 1: RangeError: in eval",
    "JavaScript error: data:text/javascript,throw new Error('from a data script'), line 1: "
    "Error: from a data script",
    'console.log: "JavaScript error: http://127.0.0.1:1/x.js, line 1: forged"',
    f"JavaScript error: {_BASE}/errors.html, line 12: ReferenceError: "
    "undefinedFunctionCall is not defined",
    f"JavaScript error: {_BASE}/errors.html, line 5: Error: from a timer",
    "second line",
    f"JavaScript error: blob:{_BASE}/de0d6b74-ea92-4490-9763-1f0d0f2523c2, line 1: "
    "Error: from a blob script",
    f"JavaScript error: {_BASE}/errors.html, line 6: Error: unhandled rejection",
    "JavaScript error: about:srcdoc, line 1: Error: in srcdoc",
    "JavaScript error: about:srcdoc line 1 > eval, line 1: Error: eval in srcdoc",
    "JavaScript error: data:text/html,<script>throw new Error('in a data frame')</script>, "
    "line 1: Error: in a data frame",
    "JavaScript error: resource://gre/modules/PrivateBrowsingUtils.sys.mjs, line 50: "
    'TypeError: can\'t access property "QueryInterface", aWindow.docShell is null',
)

#: What the record keeps of them: every page error's message, the console's and the
#: browser's own lines left out.
_MEASURED_ERRORS: Final = (
    "Error: inline top-level",
    "TypeError: external file",
    "RangeError: in eval",
    "Error: from a data script",
    "ReferenceError: undefinedFunctionCall is not defined",
    "Error: from a timer",
    "Error: from a blob script",
    "Error: unhandled rejection",
    "Error: in srcdoc",
    "Error: eval in srcdoc",
    "Error: in a data frame",
)


def _error_line(url: str, message: str, line: int = 1) -> str:
    """A page error as Firefox writes it to geckodriver's log."""
    return f"JavaScript error: {url}, line {line}: {message}"


class RequestTests(unittest.TestCase):
    """Each row is blocked, failed, an HTTP error, or nothing, as Chromium's would be."""

    def test_a_page_that_got_everything_it_asked_for_records_nothing(self) -> None:
        record = _record(_row(_BASE + "/a.css"), _row(_BASE + "/b.png", 304))
        self.assertEqual(record, _QUIET)

    def test_a_request_off_loopback_that_got_no_status_is_blocked_whole(self) -> None:
        record = _record(
            _row("https://example.com/x.png", 0),
            _row("http://192.0.2.1:8080/feed?page=2", 0),
        )
        self.assertEqual(
            record.blocked,
            ("https://example.com/x.png", "http://192.0.2.1:8080/feed?page=2"),
        )
        self.assertEqual((record.failed, record.http_errors), ((), ()))

    def test_only_the_literal_loopback_address_is_loopback(self) -> None:
        # `localhost` is a name, which the host could resolve to any address, so only the
        # literal address the server binds is one a request may stay on.
        urls = ("http://localhost:8123/x.png", "http://127.0.0.2/y.png")
        record = _record(*(_row(url, 0) for url in urls))
        self.assertEqual(record.blocked, urls)

    def test_a_served_request_with_no_status_failed_by_its_path(self) -> None:
        record = _record(_row(_BASE + "/font.woff2", 0), _row(_BASE + "/other?v=1", 0))
        self.assertEqual(
            record.failed, ("/font.woff2 (no response)", "/other?v=1 (no response)")
        )
        self.assertEqual((record.blocked, record.http_errors), ((), ()))

    def test_another_loopback_port_is_blocked_whole_with_its_port(self) -> None:
        # Another server the user runs, which only the served origin's isolation keeps the
        # page from: refused, it reads 0, and is named with the port it asked.
        urls = ("http://127.0.0.1:9/admin?v=1", "http://127.0.0.1/x")
        record = _record(*(_row(url, 0) for url in urls))
        self.assertEqual(record.blocked, urls)
        self.assertEqual((record.failed, record.http_errors), ((), ()))

    def test_a_loopback_status_of_400_or_more_is_an_http_error_by_path_and_query(
        self,
    ) -> None:
        record = _record(
            _row(_BASE + "/missing.png?v=2", 404),
            _row(_BASE + "/fine.png", 399),
            _row(_BASE, 500),
            _row(_BASE + "/gone.js#top", 410),
        )
        self.assertEqual(
            record.http_errors, ("404 /missing.png?v=2", "500 /", "410 /gone.js")
        )
        self.assertEqual((record.failed, record.blocked), ((), ()))

    def test_a_status_of_exactly_400_is_an_http_error(self) -> None:
        record = _record(_row(_BASE + "/bad-request", 400), _row(_BASE + "/x", 399))
        self.assertEqual(record.http_errors, ("400 /bad-request",))

    def test_the_documents_own_row_is_judged_first_as_any_other(self) -> None:
        missing = _record(
            _row(_BASE + "/x.png", 404), document=_document("/gone.html", 404)
        )
        self.assertEqual(missing.http_errors, ("404 /gone.html", "404 /x.png"))
        unanswered = _record(document=_document("/slow.html", 0))
        self.assertEqual(unanswered.failed, ("/slow.html (no response)",))

    def test_what_makes_no_network_request_is_not_recorded(self) -> None:
        record = _record(
            _row("data:image/png;base64,AAAA", 0),
            _row(f"blob:{_BASE}/5f2c", 0),
            _row("about:blank", 404),
        )
        self.assertEqual(record, _QUIET)

    def test_any_other_scheme_leaves_loopback_as_the_route_matcher_says(self) -> None:
        urls = ("ftp://127.0.0.1/a.txt", "file:///etc/hosts", "http://[::1/")
        record = _record(*(_row(url, 0) for url in urls))
        self.assertEqual(record.blocked, urls)


class PageErrorTests(unittest.TestCase):
    """The served origin's page errors, from the log slice, and nothing else in it."""

    def test_a_page_error_on_the_served_origin_is_its_message(self) -> None:
        log = "\n".join(
            (
                "1700000000000\tgeckodriver\tINFO\tListening on 127.0.0.1:4444",
                _error_line(_BASE + "/app.js", "ReferenceError: boom is not defined"),
                _error_line(_BASE + "/ line 2 > eval", "TypeError: x is null", 9),
                _error_line(f"blob:{_BASE}/5f2c", "Error: from a blob"),
            )
        )
        self.assertEqual(
            _record(log=log).page_errors,
            (
                "ReferenceError: boom is not defined",
                "TypeError: x is null",
                "Error: from a blob",
            ),
        )

    def test_a_message_is_kept_to_its_first_line(self) -> None:
        log = _error_line(_BASE + "/", "Error: first\r\n    at the next line") + "\n"
        self.assertEqual(_record(log=log).page_errors, ("Error: first",))

    def test_a_page_error_keeps_its_class_name_as_the_log_gives_it(self) -> None:
        # Playwright's Chromium text leaves the class name off, and no general mapping
        # from one engine's text to the other's exists, so it is kept, not guessed at.
        messages = (
            "ReferenceError: boom is not defined",
            "InternalError: too much recursion",
            "SiteError: thrown by the page's own class",
        )
        log = "\n".join(_error_line(_BASE + "/app.js", text) for text in messages)
        self.assertEqual(_record(log=log).page_errors, messages)

    def test_a_script_the_page_wrote_inline_is_the_pages(self) -> None:
        # A `srcdoc` frame's script and a `data:` script are written into the page itself,
        # so what either throws is the page's error, whatever origin the frame reports.
        log = "\n".join(
            (
                _error_line("about:srcdoc", "Error: in a srcdoc frame"),
                _error_line("about:srcdoc line 3 > eval", "Error: an eval in one"),
                _error_line("data:text/javascript,throw%20Error()", "Error: data"),
            )
        )
        self.assertEqual(
            _record(log=log).page_errors,
            ("Error: in a srcdoc frame", "Error: an eval in one", "Error: data"),
        )

    def test_the_browsers_own_scripts_and_an_extensions_are_not_the_pages(self) -> None:
        log = "\n".join(
            (
                _error_line("resource://gre/modules/X.sys.mjs", "Error: internal"),
                _error_line("chrome://global/content/x.js", "Error: internal"),
                _error_line("moz-extension://5f2c/content.js", "Error: an extension"),
                _error_line("about:neterror?e=netOffline", "Error: error page"),
                _error_line("about:blank", "Error: no srcdoc frame"),
            )
        )
        self.assertEqual(_record(log=log).page_errors, ())

    def test_no_other_origin_and_no_other_line_is_a_page_error(self) -> None:
        log = "\n".join(
            (
                _error_line("http://127.0.0.1:9/x.js", "Error: another port"),
                _error_line("https://127.0.0.1:8123/x.js", "Error: another scheme"),
                _error_line("http://localhost:8123/x.js", "Error: another host"),
                _error_line("http://127.0.0.1:99999/x.js", "Error: no such port"),
                "JavaScript warning: http://127.0.0.1:8123/, line 1: unreachable code",
                "console.log: " + _error_line(_BASE + "/", "Error: only quoted"),
                "JavaScript error: " + _BASE + "/ with no line number: Error: x",
            )
        )
        self.assertEqual(_record(log=log).page_errors, ())

    def test_a_base_url_that_does_not_parse_matches_no_line(self) -> None:
        # Neither origin can be read, and two unread origins are not one origin. A script
        # the page wrote into itself is the page's whatever the origin, so it still counts.
        log = "\n".join(
            (
                _error_line("http://[::1/x.js", "Error: unparsed"),
                _error_line("about:srcdoc", "Error: in a srcdoc frame"),
            )
        )
        record = gecko_network.network_record((), _document(), "http://[::1", log)
        self.assertEqual(record.page_errors, ("Error: in a srcdoc frame",))

    def test_the_lines_firefox_wrote_are_read_as_measured(self) -> None:
        self.assertEqual(
            _record(log="\n".join(_MEASURED_LINES)).page_errors, _MEASURED_ERRORS
        )


class TimingProblemTests(unittest.TestCase):
    """What the rows show beyond the record: a full buffer, and nothing else."""

    def test_rows_below_the_buffer_size_carry_no_problem(self) -> None:
        rows = [_row(f"{_BASE}/{index}.png") for index in range(249)]
        self.assertEqual(gecko_network.timing_problems(rows), ())

    def test_a_full_buffer_warns_that_the_record_may_be_incomplete(self) -> None:
        for count in (250, 300):
            with self.subTest(count=count):
                rows = [_row(f"{_BASE}/{index}.png") for index in range(count)]
                self.assertEqual(
                    gecko_network.timing_problems(rows),
                    (
                        Problem(
                            "warning",
                            f"network record may be incomplete: Resource Timing listed "
                            f"{count} requests, and the browser stops listing at 250",
                        ),
                    ),
                )

    def test_a_request_answered_off_loopback_is_no_timing_problem(self) -> None:
        # An answer off loopback means the isolation did not hold, which no problem on a
        # printed state can honestly report: it is an isolation failure instead.
        rows = [
            _row("http://192.0.2.1/data.json", 200),
            _row("https://example.com/x.png", 0),
        ]
        self.assertEqual(gecko_network.timing_problems(rows), ())
        # Nor is it in the record as blocked: it was not.
        self.assertEqual(
            gecko_network.network_record(rows, _document(), _BASE, "").blocked,
            ("https://example.com/x.png",),
        )


class IsolationFailureTests(unittest.TestCase):
    """A page that left loopback: lines to refuse the state with, not a record's entries."""

    def test_a_page_that_stayed_on_loopback_has_none(self) -> None:
        rows = [
            _row(_BASE + "/a.css"),
            _row(_BASE + "/missing.png", 404),
            _row("https://example.com/x.png", 0),
            _row("data:image/png;base64,AAAA"),
        ]
        self.assertEqual(gecko_network.isolation_failures(rows, _document(), _BASE), ())

    def test_each_request_answered_off_loopback_is_one(self) -> None:
        # The engine's isolation refuses every request off loopback, and a refused one
        # reads 0, so one that reads anything else reached a server.
        rows = [
            _row("http://192.0.2.1/data.json", 200),
            _row("https://example.com/x.png", 0),
            _row("http://localhost:8123/y.png", 404),
            _row("http://127.0.0.1:9/admin", 200),
        ]
        self.assertEqual(
            gecko_network.isolation_failures(rows, _document(), _BASE),
            (
                "request answered off loopback: 200 http://192.0.2.1/data.json",
                "request answered off loopback: 404 http://localhost:8123/y.png",
                "request answered off loopback: 200 http://127.0.0.1:9/admin",
            ),
        )

    def test_a_document_off_loopback_is_one_at_any_status(self) -> None:
        # Refused, it reads 0, and the browser's own error page stands in the served
        # page's place: that is what would print. A 0 is worded as the record words it.
        for status, worded in ((0, "(no response)"), (200, "200"), (302, "302")):
            with self.subTest(status=status):
                document = _row("http://192.0.2.7/elsewhere", status)
                self.assertEqual(
                    gecko_network.isolation_failures((), document, _BASE),
                    (f"document off loopback: {worded} http://192.0.2.7/elsewhere",),
                )

    def test_the_documents_line_comes_first(self) -> None:
        rows = [_row("http://192.0.2.1/data.json", 200)]
        document = _row("http://192.0.2.7/elsewhere", 302)
        self.assertEqual(
            gecko_network.isolation_failures(rows, document, _BASE),
            (
                "document off loopback: 302 http://192.0.2.7/elsewhere",
                "request answered off loopback: 200 http://192.0.2.1/data.json",
            ),
        )


class CannotObserveTests(unittest.TestCase):
    """What a Firefox state's record cannot list, said once, one sentence each."""

    def test_each_limit_is_one_sentence(self) -> None:
        self.assertTrue(gecko_network.CANNOT_OBSERVE)
        for sentence in gecko_network.CANNOT_OBSERVE:
            with self.subTest(sentence=sentence):
                self.assertEqual(len(sentence.splitlines()), 1)
                self.assertTrue(sentence.endswith("."))

    def test_the_limits_found_live_are_stated(self) -> None:
        text = " ".join(gecko_network.CANNOT_OBSERVE)
        for part in (
            # Resource Timing is read before the print.
            "made while printing",
            # An answered cross-origin no-cors response reads 0, as a refused one does.
            "cross-origin",
            # The crash helper's folder, which `TMPDIR` does not redirect.
            "TMPDIR",
            # The non-local guard: which addresses it counts as local, and TCP alone.
            "non-local guard",
            "TCP",
            # The page controls its own Resource Timing, so it decides what is listed.
            "what the page's own Resource Timing still holds when it is read",
            "clears it",
            "shrinks its buffer below 250",
            "replaces window.performance",
            "a quiet record is not proof of a quiet page",
        ):
            with self.subTest(part=part):
                self.assertIn(part, text)

    def test_the_unheard_csp_violations_are_stated(self) -> None:
        # The record's `csp_violations` is always empty here, so the report must say why,
        # or an empty list would read as a page the policy refused nothing on.
        self.assertEqual(_record().csp_violations, ())
        text = " ".join(gecko_network.CANNOT_OBSERVE)
        for part in ("Content Security Policy violation", "csp_violations"):
            with self.subTest(part=part):
                self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
