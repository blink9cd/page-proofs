"""Shared by the WebDriver client's suites: a recording transport, a bound stand-in, replies.

`FakeTransport` stands in for `urllib_transport`: it hands back the replies it was given, in
turn, raising any that is an `OSError`, and writes each request into one log. `FakeBounder`
stands in for the watchdog and writes each bound's entry and exit into that same log, so a
case can check that every request ran inside a bound of its own, sized and named for it.
`make_client` and `make_session` build the client over both in one line. Nothing here
reaches a browser or the network.

Entry point assumptions: imported by a suite under this folder. The insert below reaches the
tool's private `src/` from this file's own location, so the import order of a suite does not
matter. Playwright is not imported: the client under test never touches it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_client  # noqa: E402 - the tool's private src/, via the insert above
import print_watchdog  # noqa: E402 - as above

#: The loopback origin a geckodriver would listen on.
BASE_URL = "http://127.0.0.1:4444"

#: The timeout every client here is given, which each request hands the transport.
TIMEOUT_S = 5.0

#: The bound on each request: twice the timeout. Spelled out rather than read from the
#: engine, so a change to the engine's factor fails the cases that use it.
BOUND_S = 10.0

#: The session the fake geckodriver opens, and the path every command of it is under.
SESSION_ID = "0d4b6f2e-5a7c-4e8f-9b1a-2c3d4e5f6a7b"
SESSION_URL = f"{BASE_URL}/session/{SESSION_ID}"

#: The Firefox version the fake geckodriver reports.
BROWSER_VERSION = "157.0"

#: The key W3C WebDriver gives an element reference under, and one such reference.
ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"
ELEMENT_ID = "8f9e0d1c-2b3a-4c5d-8e6f-7a8b9c0d1e2f"

#: What a stand-in error reply carries beside its message: a stack, which no error text
#: may ever quote.
STACKTRACE = "frame one@resource://stack\nframe two@resource://stack\n"

#: The status geckodriver answers a success with.
HTTP_OK = 200

#: One reply the fake transport hands back: a status and body, or the error it raises.
type Reply = tuple[int, bytes] | OSError

#: One entry of the shared log: its kind first, then what it was given.
type Entry = tuple[object, ...]


def ok(value: object) -> tuple[int, bytes]:
    """A success reply carrying `value`, as geckodriver sends one."""
    return HTTP_OK, json.dumps({"value": value}).encode("utf-8")


def failure(status: int, error: str, message: str = "it failed") -> tuple[int, bytes]:
    """An error reply as geckodriver sends one: its code, its message and a stack."""
    value = {"error": error, "message": message, "stacktrace": STACKTRACE}
    return status, json.dumps({"value": value}).encode("utf-8")


def session_reply(
    session_id: str = SESSION_ID,
    version: str = BROWSER_VERSION,
    more: Mapping[str, object] | None = None,
) -> tuple[int, bytes]:
    """New Session's success reply: the session and the capabilities it matched.

    `more` are further capabilities, by name, such as `moz:processID`.
    """
    capabilities = {"browserName": "firefox", "browserVersion": version, **(more or {})}
    return ok({"sessionId": session_id, "capabilities": capabilities})


def exchange(command: str, method: str, url: str, sent: object) -> list[Entry]:
    """The log of one request: its bound opened, the request made inside it, then closed.

    `sent` is the request's body decoded from JSON, or None for a request without one.
    """
    what = f"webdriver {command}"
    return [
        ("bound", BOUND_S, what),
        ("send", method, url, sent, TIMEOUT_S),
        ("unbound", what),
    ]


class FakeTransport:
    """`urllib_transport`'s stand-in: logs each request and hands back the next reply."""

    def __init__(self, log: list[Entry], replies: Iterable[Reply]) -> None:
        """Write into `log`, and answer with `replies` in turn."""
        self._log = log
        self._replies = list(replies)

    def __call__(
        self, method: str, url: str, body: bytes | None, timeout_s: float
    ) -> tuple[int, bytes]:
        """Log the request, its body decoded as UTF-8 JSON, then reply or raise."""
        sent = None if body is None else json.loads(body.decode("utf-8"))
        self._log.append(("send", method, url, sent, timeout_s))
        reply = self._replies.pop(0)
        if isinstance(reply, OSError):
            raise reply
        return reply


class FakeBounder:
    """The watchdog's stand-in: logs each bound's entry and exit, and never expires."""

    def __init__(self, log: list[Entry]) -> None:
        """Write into `log`, the fake transport's own."""
        self._log = log

    @contextmanager
    def bound(self, seconds: float, what: str) -> Iterator[None]:
        """Log `bound` on entry and `unbound` on exit, around the block.

        A bound the watchdog would refuse raises its `ValueError` first, logging nothing.
        """
        print_watchdog.check_bound(seconds)
        self._log.append(("bound", seconds, what))
        try:
            yield
        finally:
            self._log.append(("unbound", what))


def make_client(
    *replies: Reply, base_url: str = BASE_URL, timeout_s: float = TIMEOUT_S
) -> tuple[gecko_client.GeckoClient, list[Entry]]:
    """A client over the fakes, answering with `replies`, and the log they share."""
    log: list[Entry] = []
    client = gecko_client.GeckoClient(
        base_url,
        transport=FakeTransport(log, replies),
        bounder=FakeBounder(log),
        timeout_s=timeout_s,
    )
    return client, log


def make_session(*replies: Reply) -> tuple[gecko_client.GeckoSession, list[Entry]]:
    """A session already opened, answering its commands with `replies`, and an empty log."""
    client, log = make_client(session_reply(), *replies)
    session = client.new_session({"alwaysMatch": {"browserName": "firefox"}})
    log.clear()
    return session, log
