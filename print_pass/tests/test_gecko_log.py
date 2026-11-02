"""Suite: what geckodriver's log says about Firefox's isolation: a breach, found and named.

With `MOZ_DISABLE_NONLOCAL_CONNECTIONS` set, Firefox refuses a direct TCP connection to an
address it counts as non-local, writes the refusal to its stderr, which geckodriver's log
carries, and ends. Every case here is a pure function of log text, taken from a measured run's
log where it can be; the same reader by property is `test_gecko_launch_fuzz.py`'s.

Entry point assumptions: standalone, or discovered by `unittest discover`. The insert below
reaches the subject's private `src/` beside this folder, never the working directory.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_launch  # noqa: E402 - the tool's private src/, via the insert above
import gecko_log  # noqa: E402 - as above
from print_record import EngineError  # noqa: E402 - as above

#: The refusal's first line, as Firefox writes it, for a host and the address it resolved to.
_REFUSAL = (
    "FATAL ERROR: Non-local network connections are disabled and a connection attempt to "
    "{host} ({address}) was made.\n"
)

#: A measured run's log around the refusal: Marionette's own port line, a graphics warning,
#: the refusal, its guidance line cut off where the browser died, and the exit.
_MEASURED_LOG = (
    "1791236371814\tMarionette\tINFO\tListening on port 57104\n"
    "[GFX1-]: RenderCompositorSWGL failed mapping default framebuffer, no dt\n"
    + _REFUSAL.format(host="192.0.2.1", address="192.0.2.1")
    + "You should only access hostnames available via the test networking proxy (if "
    "running mochitests) or from a test-specific httpd.js server (if running xpcshell "
    "tests). Browser services should be disabled or redirected to\n"
    "Exiting due to channel error.\n"
)

#: What the description says of the measured refusal, word for word.
_MEASURED_BREACH = (
    "Firefox tried to connect directly to 192.0.2.1 (192.0.2.1); "
    "its non-local guard refused it and ended the browser"
)


class InterfaceTests(unittest.TestCase):
    """`gecko_launch` gives each of these as the very object `gecko_log` defines."""

    def test_each_is_the_same_object_there(self) -> None:
        for name in ("listening_port", "isolation_breach", "IsolationBreach"):
            with self.subTest(name=name):
                self.assertIs(getattr(gecko_launch, name), getattr(gecko_log, name))

    def test_a_breach_is_an_engine_error_of_its_own(self) -> None:
        self.assertTrue(issubclass(gecko_log.IsolationBreach, EngineError))
        self.assertIsNot(gecko_log.IsolationBreach, EngineError)


class IsolationBreachTests(unittest.TestCase):
    """A refusal anywhere in the log is found, and named in one line; nothing else is."""

    def test_the_measured_refusal_is_named_in_one_line(self) -> None:
        self.assertEqual(gecko_log.isolation_breach(_MEASURED_LOG), _MEASURED_BREACH)

    def test_a_refusal_naming_a_host_names_it_and_its_address(self) -> None:
        line = _REFUSAL.format(host="canary.example.invalid", address="203.0.113.9")
        found = gecko_log.isolation_breach(line)
        assert found is not None
        self.assertIn("canary.example.invalid (203.0.113.9)", found)
        self.assertNotIn("\n", found)

    def test_an_ipv6_refusal_is_named(self) -> None:
        line = _REFUSAL.format(host="2001:db8::1", address="2001:db8::1")
        found = gecko_log.isolation_breach(line)
        assert found is not None
        self.assertIn("2001:db8::1 (2001:db8::1)", found)

    def test_the_first_refusal_decides(self) -> None:
        # Measured: one run's log held two, an IPv6 address and then an IPv4 one.
        text = _REFUSAL.format(
            host="2001:db8::1", address="2001:db8::1"
        ) + _REFUSAL.format(host="192.0.2.1", address="192.0.2.1")
        found = gecko_log.isolation_breach(text)
        assert found is not None
        self.assertIn("2001:db8::1", found)
        self.assertNotIn("192.0.2.1", found)

    def test_a_refusal_mid_line_is_not_the_guards(self) -> None:
        # The guard writes its line from the start: one mid-line is another writer's text.
        text = "geckodriver\tINFO\tpartial " + _REFUSAL.format(
            host="192.0.2.1", address="192.0.2.1"
        )
        self.assertIsNone(gecko_log.isolation_breach(text))

    def test_a_page_error_quoting_the_refusal_names_nothing(self) -> None:
        # A page's uncaught error reaches the log as one line led by Firefox's own words,
        # so a page throwing the guard's sentence cannot forge a breach with it.
        refusal = _REFUSAL.format(host="192.0.2.1", address="192.0.2.1")
        text = f"JavaScript error: http://127.0.0.1:8123/, line 1: Error: {refusal}"
        self.assertIsNone(gecko_log.isolation_breach(text))

    def test_a_console_line_opening_with_the_bare_sentence_names_nothing(self) -> None:
        # A console message's later lines start lines of their own in the log; the guard's
        # line always opens with its own prefix, which the page's message did not write.
        refusal = _REFUSAL.format(host="192.0.2.1", address="192.0.2.1")
        bare = refusal.removeprefix("FATAL ERROR: ")
        self.assertIsNone(gecko_log.isolation_breach(f"console.log: x\n{bare}"))

    def test_a_host_or_address_holding_a_control_character_names_nothing(self) -> None:
        # The guard names a host and an address in printable ASCII; a terminal would act on
        # an escape the line carried.
        for host, address in (("192.0.2.1\x1b[2J", "192.0.2.1"), ("x", "\x1b]0;t\x07")):
            with self.subTest(host=host, address=address):
                line = _REFUSAL.format(host=host, address=address)
                self.assertIsNone(gecko_log.isolation_breach(line))

    def test_a_refusal_on_a_later_line_is_found(self) -> None:
        text = "geckodriver\tINFO\tpartial\r\n" + _REFUSAL.format(
            host="192.0.2.1", address="192.0.2.1"
        )
        self.assertEqual(gecko_log.isolation_breach(text), _MEASURED_BREACH)

    def test_a_log_with_no_refusal_names_nothing(self) -> None:
        clean = _MEASURED_LOG.replace("FATAL ERROR: Non-local", "FATAL ERROR: Local")
        for text in ("", "Exiting due to channel error.\n", clean):
            with self.subTest(text=text[:30]):
                self.assertIsNone(gecko_log.isolation_breach(text))

    def test_a_refusal_still_being_written_names_nothing_yet(self) -> None:
        # Cut anywhere before its end, the host or address read could be a prefix of the
        # one it will name.
        line = _REFUSAL.format(host="192.0.2.1", address="192.0.2.1")
        whole = line.index("was made.") + len("was made.")
        for end in range(whole):
            with self.subTest(end=end):
                self.assertIsNone(gecko_log.isolation_breach(line[:end]))
        self.assertIsNotNone(gecko_log.isolation_breach(line[:whole]))


if __name__ == "__main__":
    unittest.main()
