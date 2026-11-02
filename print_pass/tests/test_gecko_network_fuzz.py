"""Suite: `gecko_network.network_record` by property.

Any rows, any document row, any base URL and any log text make a record, never an error,
and every path the record names starts at the root, since a record names a loopback
request by its path; the isolation failures they make each name a URL off loopback, and a
status, never a bare 0. The strategies weight plausible input (URLs on and off the
loopback address, statuses a browser reports, page-error lines on and off the served
origin, inline and from the browser's own scripts), so that each rule is reached, not only
the refusals; the same functions by example are `test_gecko_network.py`.

Entry point assumptions: standalone, or discovered by `unittest discover`; the bootstrap
below names the tool's private `src/`.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from typing import Final

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_network  # noqa: E402 - the tool's private src/, via the insert above
from gecko_network import TimingEntry  # noqa: E402 - as above
from print_playwright import is_loopback  # noqa: E402 - as above

# The determinism convention: a profile registered at import, never an
# `@settings` decorator. deadline=None and too_slow suppressed: a busy machine measures load.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: An isolation failure's status and URL: a status other than 0, or no response at all.
_WORDED_STATUS: Final = r"(\(no response\)|-?[1-9][0-9]*) (?P<url>.*)"

#: The served origin every generated page is on.
_BASE: Final = "http://127.0.0.1:8123"

#: Path characters that cannot end a URL's path early, nor spell a log line's separator.
_PATH_ALPHABET: Final = "abcxyz0189/._-~%"

#: Text with no line break of any kind `str.splitlines` splits on.
_ONE_LINE = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Zl", "Zp")), max_size=40
)
_PATHS = st.text(alphabet=_PATH_ALPHABET, max_size=20)
_ORIGINS = st.sampled_from(
    (
        _BASE,
        "http://127.0.0.1:9",
        "https://127.0.0.1:8123",
        "http://localhost:8123",
        "http://192.0.2.1",
        "ws://127.0.0.1:8123",
        f"blob:{_BASE}",
        "data:text/plain,",
        "about:",
        "resource://gre",
        "http://[::1",
        "http://127.0.0.1:99999",
        "",
    )
)
_URLS = st.one_of(
    st.builds(lambda origin, path: f"{origin}/{path}", _ORIGINS, _PATHS),
    st.builds(lambda origin, path: f"{origin}?{path}", _ORIGINS, _PATHS),
    st.text(max_size=40),
)
_STATUSES = st.one_of(st.sampled_from((0, 200, 304, 399, 400, 404, 500)), st.integers())
_ROWS = st.builds(
    lambda name, status: TimingEntry(name=name, responseStatus=status),
    _URLS,
    _STATUSES,
)
_ERROR_LINES = st.builds(
    lambda url, line, message: f"JavaScript error: {url}, line {line}: {message}",
    _URLS,
    st.integers(min_value=0, max_value=10_000),
    _ONE_LINE,
)
_LOG_LINES = st.lists(st.one_of(_ERROR_LINES, st.text(max_size=60)), max_size=8)


#: Script URLs whole, beside the generated ones: a `srcdoc` frame's, an eval in one, and
#: the error page's, a blank frame's and an extension's, which are not the page's.
_WHOLE_SCRIPT_URLS = st.sampled_from(
    (
        "about:srcdoc",
        "about:srcdoc line 2 > eval",
        "about:srcdocs",
        "about:blank",
        "about:neterror?e=proxyConnectFailure",
        "moz-extension://5f2c/content.js",
    )
)

#: The openings of the two kinds of isolation failure, each before `<status> <url>`.
_ISOLATION_OPENINGS: Final = ("document off loopback", "request answered off loopback")


def _is_the_pages(url: str) -> bool:
    """Whether a generated page-error URL is one this suite made the page's own."""
    on_origin = url.startswith(
        (f"{_BASE}/", f"{_BASE}?", f"blob:{_BASE}/", f"blob:{_BASE}?")
    )
    inline = url.startswith("data:") or url.split(" ")[0] == "about:srcdoc"
    return on_origin or inline


class FuzzTests(unittest.TestCase):
    """Any input makes a record, never an error; every path in it starts at the root."""

    @given(
        st.lists(_ROWS, max_size=12),
        _ROWS,
        st.one_of(st.just(_BASE), st.text(max_size=30)),
        _LOG_LINES.map("\n".join),
    )
    def test_fuzz_network_record(
        self,
        entries: list[TimingEntry],
        document: TimingEntry,
        base_url: str,
        log_text: str,
    ) -> None:
        record = gecko_network.network_record(entries, document, base_url, log_text)
        for entry in record.failed:
            self.assertTrue(entry.startswith("/"), entry)
            self.assertTrue(entry.endswith(" (no response)"), entry)
        for entry in record.http_errors:
            status, _, path = entry.partition(" ")
            self.assertGreaterEqual(int(status), 400)
            self.assertTrue(path.startswith("/"), entry)
        for url in record.blocked:
            self.assertFalse(is_loopback(url, served=base_url), url)
        for message in record.page_errors:
            self.assertLessEqual(len(message.splitlines()), 1, message)
        problems = gecko_network.timing_problems(entries)
        self.assertTrue(all(problem.what for problem in problems))
        for line in gecko_network.isolation_failures(entries, document, base_url):
            opening, _, rest = line.partition(": ")
            # A status of 0 is worded as the record words it, so no line holds a bare 0.
            worded = re.fullmatch(_WORDED_STATUS, rest, re.DOTALL)
            self.assertIn(opening, _ISOLATION_OPENINGS)
            self.assertIsNotNone(worded, line)
            assert worded is not None
            self.assertFalse(is_loopback(worded["url"], served=base_url), line)

    @given(
        st.lists(
            st.tuples(
                st.one_of(
                    st.builds(
                        lambda origin, path: f"{origin}/{path}", _ORIGINS, _PATHS
                    ),
                    _WHOLE_SCRIPT_URLS,
                ),
                _ONE_LINE,
            ),
            max_size=6,
        )
    )
    def test_the_pages_own_lines_are_the_page_errors_in_order(
        self, errors: list[tuple[str, str]]
    ) -> None:
        log = "\n".join(
            f"JavaScript error: {url}, line 1: {message}" for url, message in errors
        )
        record = gecko_network.network_record(
            [], {"name": _BASE, "responseStatus": 200}, _BASE, log
        )
        expected = tuple(message for url, message in errors if _is_the_pages(url))
        self.assertEqual(record.page_errors, expected)


if __name__ == "__main__":
    unittest.main()
