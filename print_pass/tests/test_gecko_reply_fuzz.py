"""Suite: `gecko_reply.decode_response` by property.

Any status and any body either decode to a value or raise `WebDriverError`, never anything
else, and the error's text is one line naming the command and its code. The strategies weight
plausible replies (a success's `value`, an error's code and message, both as JSON, under the
statuses geckodriver answers with) so that values and W3C errors, not just refusals, are
checked; the same function by example is `test_gecko_reply.py`.

Entry point assumptions: standalone, or discovered by `unittest discover`. The fixtures
module beside this one puts the tool's private `src/` on `sys.path`.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _gecko_client_fixtures  # noqa: E402, F401 - puts the tool's private src/ on sys.path
import gecko_reply  # noqa: E402 - the tool's private src/, via the fixtures' insert
from gecko_reply import WebDriverError  # noqa: E402 - as above

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

_COMMANDS = st.sampled_from(("new session", "navigate", "print", "find element"))
_STATUSES = st.one_of(st.sampled_from((200, 400, 404, 500)), st.integers())

#: Any value JSON can carry; NaN is left out, since it never equals itself.
_JSON_VALUES = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False),
        st.text(),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(), children, max_size=4),
    ),
    max_leaves=12,
)
#: W3C codes, and arbitrary text in a code's place: empty, multi-line or otherwise.
_CODES = st.one_of(
    st.sampled_from(("no such element", "unknown error", "timeout")), st.text()
)
_ERROR_VALUES = st.fixed_dictionaries(
    {"error": st.one_of(_CODES, _JSON_VALUES), "message": st.text()},
    optional={"stacktrace": st.text()},
)
_DOCUMENTS = st.one_of(
    _JSON_VALUES,
    st.fixed_dictionaries({"value": _JSON_VALUES}),
    st.fixed_dictionaries({"value": _ERROR_VALUES}),
)
_BODIES = st.one_of(
    st.binary(),
    _DOCUMENTS.map(lambda document: json.dumps(document).encode("utf-8")),
)


def _canonical(value: object) -> str:
    """`value` as JSON text, so two values compare equal exactly when JSON reads them so."""
    return json.dumps(value, sort_keys=True)


class FuzzTests(unittest.TestCase):
    """Any reply decodes to its value or raises a one-line `WebDriverError`, never else."""

    @given(_COMMANDS, _STATUSES, _BODIES)
    def test_fuzz_decode_response(self, command: str, status: int, body: bytes) -> None:
        try:
            value = gecko_reply.decode_response(command, status, body)
        except WebDriverError as error:
            self.assertEqual(error.command, command)
            self.assertTrue(error.error)
            self.assertEqual(len(str(error).splitlines()), 1)
            self.assertTrue(str(error).startswith(f"{command}: {error.error}: "))
            return
        self.assertEqual(status, 200)
        document = json.loads(body.decode("utf-8"))
        self.assertEqual(_canonical(value), _canonical(document["value"]))

    @given(_COMMANDS, _JSON_VALUES)
    def test_a_success_returns_any_value_it_carries(
        self, command: str, value: object
    ) -> None:
        body = json.dumps({"value": value}).encode("utf-8")
        decoded = gecko_reply.decode_response(command, 200, body)
        self.assertEqual(_canonical(decoded), _canonical(value))

    @given(
        _COMMANDS, st.integers().filter(lambda status: status != 200), _CODES, st.text()
    )
    def test_an_error_raises_its_one_line_code_or_is_invalid(
        self, command: str, status: int, code: str, message: str
    ) -> None:
        body = json.dumps({"value": {"error": code, "message": message}}).encode()
        with self.assertRaises(WebDriverError) as caught:
            gecko_reply.decode_response(command, status, body)
        expected = code if code.splitlines() == [code] else "invalid response"
        self.assertEqual(caught.exception.error, expected)


if __name__ == "__main__":
    unittest.main()
