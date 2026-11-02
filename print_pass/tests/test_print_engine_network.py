"""Suite: what one state's page asks the network for, and what is kept off it.

Each case runs the engine over one target against the fake Playwright, whose page makes the
requests and fires the events its script names, and reads the network record and findings
the state returns. Nothing may leave the loopback address: a request that would is aborted,
and a WebSocket that would is left unconnected, each recorded whole and judged once: a
finding, or a warning when the targets file expects its host. Problem texts are compared
whole, since a later live suite asserts on them.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_engine  # noqa: E402 - the tool's private src/, via the insert above
from _print_engine_fixtures import (  # noqa: E402 - beside this module
    BASE_URL,
    TIMEOUT_S,
    FakeRun,
    StateTestCase,
    engine_settings,
    state,
    targets_file,
)
from _print_playwright_fixtures import (  # noqa: E402 - as above
    FakeRequest,
    FakeResponse,
    FakeScript,
    FakeWebSocketRoute,
    call,
)
from print_targets import Action  # noqa: E402 - the tool's private src/


def _finding(what: str) -> print_engine.Problem:
    return print_engine.Problem("finding", what)


class NetworkTests(StateTestCase):
    """Blocked, failed, HTTP-error and page-error events, each its own problem, once."""

    def test_each_event_is_recorded_and_found(self) -> None:
        script = FakeScript(
            requests=["https://example.com/x.png", BASE_URL + "/ok.png"],
            events=[
                ("response", FakeResponse(BASE_URL + "/missing.png?v=2", 404)),
                ("response", FakeResponse(BASE_URL + "/", 200)),
                (
                    "requestfailed",
                    FakeRequest(BASE_URL + "/font.woff2", "net::ERR_ABORTED"),
                ),
                (
                    "pageerror",
                    RuntimeError("boom is not defined\n    at main (/app.js:1:1)"),
                ),
            ],
        )
        run, record = self.run_one(script=script)
        self.assertEqual(
            record.network,
            print_engine.NetworkRecord(
                failed=("/font.woff2 (net::ERR_ABORTED)",),
                http_errors=("404 /missing.png?v=2",),
                blocked=("https://example.com/x.png",),
                page_errors=("boom is not defined",),
                csp_violations=(),
            ),
        )
        self.assertEqual(
            record.problems,
            (
                _finding("HTTP 404: /missing.png?v=2"),
                _finding("request failed: /font.woff2 (net::ERR_ABORTED)"),
                _finding("blocked off-loopback request: https://example.com/x.png"),
                _finding("page error: boom is not defined"),
            ),
        )
        # Only the off-loopback request met the route, and it was aborted as the client's.
        self.assertEqual(
            run.named("route.abort"),
            [call("route.abort", "https://example.com/x.png", "blockedbyclient")],
        )
        self.assertEqual(len(record.pdfs), 1)
        self.assertEqual(run.unbounded(), [])

    def test_a_request_to_an_expected_host_is_still_aborted_and_only_a_warning(
        self,
    ) -> None:
        # The targets file's hosts reach the judge through the engine: the request is
        # aborted and recorded as any other off loopback, and only its severity changes.
        expected = "https://img.example.org/cover.jpg"
        other = "https://example.com/x.png"
        run = FakeRun(self.pdf_dir, FakeScript(requests=[expected, other]))
        targets = targets_file(expected_offsite_hosts=("example.org",))
        (record,) = run.run(targets, engine_settings(self.pdf_dir))
        self.assertEqual(
            run.named("route.abort"),
            [
                call("route.abort", expected, "blockedbyclient"),
                call("route.abort", other, "blockedbyclient"),
            ],
        )
        self.assertEqual(record.network.blocked, (expected, other))
        self.assertEqual(
            record.problems,
            (
                print_engine.Problem(
                    "warning", f"off-loopback request blocked as expected: {expected}"
                ),
                _finding(f"blocked off-loopback request: {other}"),
            ),
        )

    def test_the_abort_is_bounded_under_its_own_name_at_the_timeout(self) -> None:
        # The route's handler runs during the load, inside the load's bound: only a bound
        # of its own ends a wedged abort at the timeout, and names the abort, not the load.
        blocked = "https://example.com/x.png"
        run, _ = self.run_one(script=FakeScript(requests=[blocked]))
        bound = "route.abort home/default"
        self.assertEqual(
            run.around(call("route.abort", blocked, "blockedbyclient")),
            (call("bound", bound, TIMEOUT_S), call("unbound", bound)),
        )

    def test_another_loopback_port_is_blocked_with_its_port(self) -> None:
        # Only the served origin is reachable: another port on loopback is another server
        # the user runs (a dev server, a local API), so its request and its WebSocket are
        # blocked as any off loopback, and recorded whole, port and all.
        other = "http://127.0.0.1:8080/admin"
        socket = "ws://127.0.0.1:8080/live"
        script = FakeScript(requests=[other], web_sockets=[socket])
        run, record = self.run_one(script=script)
        self.assertEqual(
            run.named("route.abort"), [call("route.abort", other, "blockedbyclient")]
        )
        self.assertCountEqual(record.network.blocked, (other, socket))
        self.assertEqual(run.named("web_socket_route.connect_to_server"), [])

    def test_what_is_not_a_loopback_path_is_recorded_whole(self) -> None:
        script = FakeScript(
            events=[
                (
                    "requestfailed",
                    FakeRequest("data:font/woff2;base64,AA", "net::ERR_FAILED"),
                ),
                ("requestfailed", FakeRequest("http://[::1/x")),
                ("response", FakeResponse(BASE_URL, 500)),
            ],
        )
        _, record = self.run_one(script=script)
        self.assertEqual(
            record.network.failed,
            (
                "data:font/woff2;base64,AA (net::ERR_FAILED)",
                "http://[::1/x (no reason given)",
            ),
        )
        self.assertEqual(record.network.http_errors, ("500 /",))

    def test_a_stopped_state_still_reports_its_network(self) -> None:
        script = FakeScript(
            requests=["https://example.com/x.png"],
            failing_selectors=frozenset({"#start"}),
        )
        _, record = self.run_one(
            state(actions=(Action("click", "#start", None),)), script
        )
        self.assertEqual(
            record.problems,
            (
                _finding('action did not complete: click "#start"'),
                _finding("blocked off-loopback request: https://example.com/x.png"),
            ),
        )


class WebSocketTests(StateTestCase):
    """A WebSocket off loopback is found as blocked, and neither closed nor connected.

    A context's request route never sees a WebSocket: Playwright hands those only to a
    route of their own. The fake's `close` and `connect_to_server` raise, so a handler that
    calls either fails here.
    """

    def test_an_off_loopback_socket_is_recorded_and_left_unconnected(self) -> None:
        on = BASE_URL.replace("http", "ws") + "/live"
        sockets = ["ws://203.0.113.9/", on, "wss://example.com/live"]
        run, record = self.run_one(script=FakeScript(web_sockets=sockets))
        # The handler only records: it neither closes the socket nor connects it.
        self.assertEqual(run.named("web_socket_route.close"), [])
        self.assertEqual(run.named("web_socket_route.connect_to_server"), [])
        self.assertEqual(
            record.network.blocked, ("ws://203.0.113.9/", "wss://example.com/live")
        )
        self.assertEqual(
            record.problems,
            (
                _finding("blocked off-loopback request: ws://203.0.113.9/"),
                _finding("blocked off-loopback request: wss://example.com/live"),
            ),
        )
        self.assertEqual(len(record.pdfs), 1)
        self.assertEqual(run.unbounded(), [])

    def test_the_traps_are_armed(self) -> None:
        socket = FakeWebSocketRoute([], "wss://example.com/live")
        with self.assertRaises(AssertionError):
            socket.close()
        with self.assertRaises(AssertionError):
            socket.connect_to_server()

    def test_the_socket_route_is_in_place_before_the_page_opens(self) -> None:
        # Playwright routes only the sockets opened after the route is added.
        run, _ = self.run_one()
        names = [entry[0] for entry in run.calls]
        self.assertIn("context.route_web_socket", names)
        self.assertLess(
            names.index("context.route_web_socket"), names.index("context.new_page")
        )


if __name__ == "__main__":
    unittest.main()
