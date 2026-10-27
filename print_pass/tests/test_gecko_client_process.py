"""Suite: the Firefox process a new session names, from its `moz:processID` capability.

geckodriver's New Session reply names the Firefox it started (measured: a whole number under
`moz:processID`), and the engine notes that pid with the driver, which then records its crash
helper. A pid is a positive whole number and nothing else: a boolean, a float, text, zero or a
negative is no pid, and neither is a missing one; each reads as None, which the engine refuses.
A session is built knowing its pid, so no session is ever without the one it was opened with.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the shared fixtures beside this module reach the tool's private `src/`.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping

from _gecko_client_fixtures import (
    BROWSER_VERSION,
    SESSION_ID,
    make_client,
    session_reply,
)
from gecko_client import GeckoSession

#: The key geckodriver names the Firefox process under.
_PROCESS_KEY = "moz:processID"


def _opened(**capabilities: object) -> int | None:
    """The process id of a session whose New Session reply holds `capabilities`."""
    client, _ = make_client(session_reply(more=capabilities))
    return client.new_session({"alwaysMatch": {}}).process_id


def _never_sent(
    command: str, method: str, path: str, payload: Mapping[str, object] | None
) -> object:
    """A sender no case here reaches: building a session sends nothing."""
    raise AssertionError(f"{command} was sent")


class ProcessIdTests(unittest.TestCase):
    """The session's `process_id`: a positive whole number, or None."""

    def test_a_session_is_built_knowing_its_firefox(self) -> None:
        session = GeckoSession(
            _never_sent, SESSION_ID, BROWSER_VERSION, process_id=4321
        )
        self.assertEqual(session.process_id, 4321)

    def test_a_whole_number_is_the_process(self) -> None:
        self.assertEqual(_opened(**{_PROCESS_KEY: 4321}), 4321)

    def test_a_missing_capability_is_no_process(self) -> None:
        self.assertIsNone(_opened())

    def test_anything_but_a_positive_whole_number_is_no_process(self) -> None:
        for value in (True, 0, -1, 4321.0, "4321", None, [4321]):
            with self.subTest(value=value):
                self.assertIsNone(_opened(**{_PROCESS_KEY: value}))


if __name__ == "__main__":
    unittest.main()
