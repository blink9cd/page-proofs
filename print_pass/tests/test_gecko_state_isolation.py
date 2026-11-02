"""Grouped suite: a Firefox state that left loopback, or is not the served page, is refused.

Firefox's non-local guard ends the browser at the first direct connection off loopback, and
writes why to geckodriver's log, so a state that fails is checked for it FIRST, and the
breach is reported as itself rather than as whatever call the dying browser failed; a state
that succeeded never is: the guard ends the browser, and a page can write the sentence.
A request answered off loopback, a document refused off it, a document that is not the
served page (`about:blank`, `data:`, `blob:`, another port or scheme), and a Resource
Timing read that is not plain rows each end the state before it prints, its session still
ended. Each case runs the real `run_state` against `_gecko_state_fixtures`' scripted session.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

from _gecko_state_fixtures import (  # noqa: E402 - beside this module
    DOCUMENT_ROW,
    REFUSAL,
    StateCase,
    timing,
    webdriver_error,
)
from _print_engine_fixtures import BASE_URL  # noqa: E402 - as above
from _print_playwright_fixtures import call  # noqa: E402 - as above
from gecko_launch import IsolationBreach  # noqa: E402 - the tool's private src/
from print_record import EngineError  # noqa: E402 - as above


class BreachTests(StateCase):
    """Firefox's non-local guard ending the browser is reported as itself, before all else."""

    def test_a_state_that_died_is_checked_for_a_breach_first(self) -> None:
        gone = webdriver_error(
            "navigate", "unknown error", "Browsing context discarded"
        )
        session = self.session(
            fail={f"navigate {BASE_URL}/": gone, "delete": gone},
            on_navigate=lambda url: self.driver.write(REFUSAL),
        )
        with self.assertRaises(IsolationBreach) as caught:
            self.run_state(session)
        self.assertEqual(
            str(caught.exception),
            "home/default: Firefox tried to connect directly to 192.0.2.1 (192.0.2.1); its "
            "non-local guard refused it and ended the browser",
        )
        self.assertIsInstance(caught.exception.__cause__, EngineError)

    def test_the_refusal_in_the_log_of_a_state_that_succeeded_is_no_breach(
        self,
    ) -> None:
        # The guard ends the browser, so a state that went on to print was never refused:
        # the sentence is a page's own, which its console writes to the same log.
        session = self.session(on_navigate=lambda url: self.driver.write(REFUSAL))
        record = self.run_state(session)
        self.assertEqual(len(record.pdfs), 1)
        self.assertIn("print", [entry[0] for entry in self.log])

    def test_the_refusal_written_as_the_session_ends_is_no_breach_either(self) -> None:
        session = self.session(on_delete=lambda: self.driver.write(REFUSAL))
        record = self.run_state(session)
        self.assertEqual(len(record.pdfs), 1)

    def test_a_breach_logged_before_the_state_is_not_its_own(self) -> None:
        self.driver.write(REFUSAL)
        self.run_state()


class IsolationTests(StateCase):
    """A page that left loopback, or is not the served page, is refused before it prints."""

    def assert_refused(self, reply: object, says: str) -> None:
        """The state ends with `says`, prints nothing, and still ends its session."""
        with self.assertRaises(EngineError) as caught:
            self.run_state(self.session(timing_reply=reply))
        self.assertIn(says, str(caught.exception))
        self.assertNotIn("print", [entry[0] for entry in self.log])
        self.assertEqual(self.calls()[-2:], [call("record_helpers"), call("delete")])

    def test_a_request_answered_off_loopback_is_refused(self) -> None:
        self.assert_refused(
            timing(["http://192.0.2.1/x.png", 200]),
            "home/default: the page left loopback: request answered off loopback: 200 "
            "http://192.0.2.1/x.png",
        )

    def test_a_document_that_is_not_the_served_page_is_refused(self) -> None:
        for url in (
            "about:blank",
            "data:text/html,<p>x</p>",
            f"blob:{BASE_URL}/0d4b6f2e",
        ):
            with self.subTest(url=url):
                self.log.clear()
                self.assert_refused(
                    timing(document=[url, 200]),
                    f"home/default: the document is not the served page: {url}",
                )

    def test_a_document_on_another_port_or_scheme_is_off_the_origin(self) -> None:
        # Only the served origin is reachable, so a document anywhere else on loopback
        # left it, as one off the machine would.
        for url in ("http://127.0.0.1:9/", "https://127.0.0.1:8123/"):
            with self.subTest(url=url):
                self.log.clear()
                self.assert_refused(
                    timing(document=[url, 200]),
                    f"home/default: the page left loopback: document off loopback: 200 {url}",
                )

    def test_a_document_refused_off_loopback_names_no_bare_status(self) -> None:
        self.assert_refused(
            timing(document=["http://192.0.2.1/", 0]),
            "document off loopback: (no response) http://192.0.2.1/",
        )

    def test_a_malformed_read_back_is_refused_naming_the_problem(self) -> None:
        self.assert_refused(
            [[DOCUMENT_ROW], [["/x.png", "200"]]],
            "Resource Timing read back malformed:",
        )


if __name__ == "__main__":
    unittest.main()
