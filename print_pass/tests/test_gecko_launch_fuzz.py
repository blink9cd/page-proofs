"""Suite: geckodriver's log readers by property -- any text, and their line after any text.

`listening_port` and `isolation_breach` read a log the run does not control the whole of, so
both are fuzzed. Any text either names no port or names one in range that a whole
`Listening on 127.0.0.1:<port>` line holds; and any text either names no breach or names one
in a single line. Drawn text rarely holds either line, so a second property for each builds
one after arbitrary text and requires it found. The same functions by example are
`test_gecko_launch.py`'s and `test_gecko_log.py`'s.

Entry point assumptions: standalone, or discovered by `unittest discover`. The insert below
reaches the subject's private `src/` beside this folder, never the working directory.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_launch  # noqa: E402 - the tool's private src/, via the insert above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: What every port line opens with.
_PREFIX = "Listening on "

#: The ports a TCP socket can have.
_LOWEST_PORT, _HIGHEST_PORT = 1, 65535

#: Hosts, ports and line ends near the ones geckodriver writes, right and wrong, so that
#: drawn text often comes close to a port line.
_HOSTS = st.sampled_from(
    ["127.0.0.1", "127.0.0.10", "0.0.0.0", "localhost", "[::1]", ""]
)
_PORT_TEXTS = st.one_of(
    st.integers(min_value=-10, max_value=70000).map(str),
    # The fourth is two ARABIC-INDIC DIGIT FOURs: digits int() reads, but no port.
    st.sampled_from(["", "080", "4e3", chr(0x0664) * 2, " 80"]),
)
_ENDS = st.sampled_from(["\n", "\r\n", "", "\r", " \n", "x\n"])
_PORT_LINES = st.builds(
    lambda host, port, end: f"{_PREFIX}{host}:{port}{end}", _HOSTS, _PORT_TEXTS, _ENDS
)
_LOGS = st.lists(st.one_of(st.text(), _PORT_LINES), max_size=6).map("".join)

#: Firefox's refusal of a direct connection, for a host and the address it resolved to.
_REFUSAL = (
    "FATAL ERROR: Non-local network connections are disabled and a connection attempt to "
    "{host} ({address}) was made."
)

#: What a refusal can name: printable ASCII but a space or a parenthesis, the address
#: possibly empty.
_HOST_TEXT = st.from_regex(r"\A[!-'*-~]{1,40}\Z", fullmatch=True)
_ADDRESS_TEXT = st.from_regex(r"\A[!-'*-~]{0,40}\Z", fullmatch=True)
_REFUSALS = st.builds(
    lambda host, address: _REFUSAL.format(host=host, address=address),
    _HOST_TEXT,
    _ADDRESS_TEXT,
)
_BREACH_LOGS = st.lists(st.one_of(st.text(), _REFUSALS), max_size=6).map("".join)


def _holds_line(text: str, port: int) -> bool:
    """Whether `text` holds a whole loopback line naming exactly `port`."""
    line = f"{_PREFIX}127.0.0.1:{port}"
    return f"{line}\n" in text or f"{line}\r\n" in text


class PropertyTests(unittest.TestCase):
    """A port or breach named is one the text holds, and one written after any text is found."""

    @given(_LOGS)
    @example(f"{_PREFIX}127.0.0.1:4444\n")
    @example(f"{_PREFIX}127.0.0.1:65536\n{_PREFIX}127.0.0.1:1\r\n")
    def test_fuzz_listening_port(self, text: str) -> None:
        port = gecko_launch.listening_port(text)
        if port is not None:
            self.assertIs(type(port), int)
            self.assertTrue(_LOWEST_PORT <= port <= _HIGHEST_PORT, port)
            self.assertTrue(_holds_line(text, port), text)

    @given(
        st.text(),
        st.integers(min_value=_LOWEST_PORT, max_value=_HIGHEST_PORT),
        st.text(),
    )
    def test_a_port_line_after_any_text_is_found(
        self, before: str, port: int, after: str
    ) -> None:
        # Text naming a port of its own would rightly win; the line is after it.
        assume(gecko_launch.listening_port(f"{before}\n") is None)
        text = (
            f"{before}\n1791229212618\tgeckodriver\tINFO\t{_PREFIX}127.0.0.1:{port}\n"
        )
        self.assertEqual(gecko_launch.listening_port(text + after), port)

    @given(_BREACH_LOGS)
    @example(_REFUSAL.format(host="192.0.2.1", address="192.0.2.1"))
    @example(_REFUSAL.format(host="x", address="") + "\n")
    def test_fuzz_isolation_breach(self, text: str) -> None:
        found = gecko_launch.isolation_breach(text)
        if found is not None:
            self.assertIs(type(found), str)
            self.assertEqual(found.splitlines(), [found])
            self.assertIn("Non-local network connections are disabled", text)

    @given(st.text(), _HOST_TEXT, _ADDRESS_TEXT, st.text())
    def test_a_refusal_after_any_text_is_found(
        self, before: str, host: str, address: str, after: str
    ) -> None:
        # Text holding a refusal of its own would rightly win; the refusal is after it.
        assume(gecko_launch.isolation_breach(before) is None)
        line = _REFUSAL.format(host=host, address=address)
        found = gecko_launch.isolation_breach(f"{before}\n{line}\n{after}")
        assert found is not None
        self.assertIn(f"{host} ({address})", found)


if __name__ == "__main__":
    unittest.main()
