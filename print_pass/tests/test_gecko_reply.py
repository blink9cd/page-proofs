"""`gecko_reply`: the client's error, a reply decoded, and each value read from one parsed.

Every reply geckodriver sends is decoded here into its `value` or one `WebDriverError`, and
the values the client reads further (New Session's id, version and Firefox pid, Find
Element's reference, Print's PDF) are parsed here too, each refusal naming the command it is
handed. The client sending them is `test_gecko_client.py`; `decode_response` by property is
`test_gecko_reply_fuzz.py`.

Entry point assumptions: standalone, or discovered by `unittest discover`. The fixtures
module beside this one puts the tool's private `src/` on `sys.path`.
"""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The fixtures first: importing them puts the tool's private src/ on sys.path.
from _gecko_client_fixtures import (  # noqa: E402 - beside this module, via the insert above
    BROWSER_VERSION,
    ELEMENT_ID,
    ELEMENT_KEY,
    SESSION_ID,
    STACKTRACE,
    failure,
    ok,
)

import gecko_reply  # noqa: E402 - the tool's private src/, via the fixtures' insert
from gecko_reply import WebDriverError  # noqa: E402 - as above
from print_record import EngineError  # noqa: E402 - as above

#: The error code a malformed reply is reported under.
_INVALID = "invalid response"


class WebDriverErrorTests(unittest.TestCase):
    def test_text_is_the_command_the_error_and_the_first_line_of_the_message(
        self,
    ) -> None:
        error = WebDriverError("print", "unknown error", "it broke\nat this frame")
        self.assertEqual(str(error), "print: unknown error: it broke")
        self.assertEqual(error.command, "print")
        self.assertEqual(error.error, "unknown error")

    def test_is_the_engine_error_the_cli_catches(self) -> None:
        self.assertIsInstance(WebDriverError("navigate", "timeout", ""), EngineError)

    def test_an_empty_message_still_names_the_command_and_error(self) -> None:
        self.assertEqual(
            str(WebDriverError("navigate", "timeout", "")), "navigate: timeout: "
        )


class DecodeResponseTests(unittest.TestCase):
    def assert_invalid(self, status: int, body: bytes) -> WebDriverError:
        """Decode a reply that must be refused as malformed, and return the refusal."""
        with self.assertRaises(WebDriverError) as caught:
            gecko_reply.decode_response("navigate", status, body)
        self.assertEqual(caught.exception.command, "navigate")
        self.assertEqual(caught.exception.error, _INVALID)
        self.assertIn(f"status {status}", str(caught.exception))
        return caught.exception

    def test_a_success_returns_its_value(self) -> None:
        for value in (None, 0, "text", [1, "two"], {"a": {"b": [True]}}):
            with self.subTest(value=value):
                status, body = ok(value)
                self.assertEqual(
                    gecko_reply.decode_response("execute", status, body), value
                )

    def test_an_error_reply_raises_its_code_and_message_first_line_only(self) -> None:
        status, body = failure(404, "no such element", "Unable to locate\nsecond line")
        with self.assertRaises(WebDriverError) as caught:
            gecko_reply.decode_response("find element", status, body)
        self.assertEqual(caught.exception.command, "find element")
        self.assertEqual(caught.exception.error, "no such element")
        self.assertEqual(
            str(caught.exception), "find element: no such element: Unable to locate"
        )
        self.assertNotIn(STACKTRACE.splitlines()[0], str(caught.exception))

    def test_any_status_but_200_carrying_an_error_raises_it(self) -> None:
        for status in (400, 404, 500, 201, 302):
            with self.subTest(status=status):
                with self.assertRaises(WebDriverError) as caught:
                    gecko_reply.decode_response("print", *failure(status, "timeout"))
                self.assertEqual(caught.exception.error, "timeout")

    def test_a_body_that_is_not_utf8_is_invalid(self) -> None:
        error = self.assert_invalid(200, b'{"value": "\xff"}')
        self.assertIn("UTF-8", str(error))

    def test_a_body_that_is_not_json_is_invalid(self) -> None:
        for status, body in ((200, b"<html>"), (502, b"Bad Gateway"), (200, b"")):
            with self.subTest(body=body):
                self.assertIn("JSON", str(self.assert_invalid(status, body)))

    def test_json_nested_past_the_parser_stack_is_invalid_not_a_crash(self) -> None:
        depth = 1_000_000
        self.assert_invalid(200, b"[" * depth + b"]" * depth)

    def test_json_that_is_not_an_object_is_invalid(self) -> None:
        for body in (b"[1]", b'"value"', b"null", b"7"):
            with self.subTest(body=body):
                self.assert_invalid(200, body)

    def test_a_success_without_a_value_is_invalid(self) -> None:
        self.assert_invalid(200, b'{"sessionId": "x"}')

    def test_a_failure_without_a_well_formed_error_is_invalid(self) -> None:
        values: tuple[object, ...] = (
            None,
            "unknown error",
            {"message": "no code"},
            {"error": "timeout"},
            {"error": 7, "message": "a number"},
            {"error": "timeout", "message": ["a list"]},
            {"error": "", "message": "an empty code"},
            {"error": "two\nlines", "message": "a code over two lines"},
            {"error": "timeout\n", "message": "a code ending a line"},
        )
        for value in values:
            with self.subTest(value=value):
                self.assert_invalid(500, json.dumps({"value": value}).encode("utf-8"))
        self.assert_invalid(500, b"{}")

    def test_a_refusal_never_quotes_the_body(self) -> None:
        error = self.assert_invalid(500, b"private body text")
        self.assertNotIn("private", str(error))


class ValueParseTests(unittest.TestCase):
    """Each parse of a decoded value: what it returns, and the command its refusal names."""

    def assert_refused(self, command: str, refusal: WebDriverError) -> None:
        """`refusal` names `command`, as a malformed reply."""
        self.assertEqual(refusal.command, command)
        self.assertEqual(refusal.error, _INVALID)

    def test_new_sessions_id_and_version_are_read(self) -> None:
        value = {"sessionId": SESSION_ID, "capabilities": {"browserVersion": "157.0"}}
        self.assertEqual(
            gecko_reply.session_fields("new session", value), (SESSION_ID, "157.0")
        )

    def test_a_session_value_without_both_is_refused_naming_the_command(self) -> None:
        with self.assertRaises(WebDriverError) as caught:
            gecko_reply.session_fields("open", {"sessionId": SESSION_ID})
        self.assert_refused("open", caught.exception)

    def test_firefoxs_pid_is_a_positive_whole_number_or_none(self) -> None:
        def value(pid: object) -> dict[str, object]:
            capabilities = {"browserVersion": BROWSER_VERSION, "moz:processID": pid}
            return {"capabilities": capabilities}

        self.assertEqual(gecko_reply.process_id(value(4321)), 4321)
        for pid in (True, 0, -1, 4321.0, "4321", None):
            with self.subTest(pid=pid):
                self.assertIsNone(gecko_reply.process_id(value(pid)))
        self.assertIsNone(gecko_reply.process_id([4321]))

    def test_an_element_reference_fit_for_a_path_is_read(self) -> None:
        self.assertEqual(
            gecko_reply.element_reference("find", {ELEMENT_KEY: ELEMENT_ID}), ELEMENT_ID
        )

    def test_an_element_reference_unfit_for_a_path_is_refused(self) -> None:
        for value in (None, {}, {ELEMENT_KEY: 7}, {ELEMENT_KEY: "a/b"}, [ELEMENT_ID]):
            with self.subTest(value=value):
                with self.assertRaises(WebDriverError) as caught:
                    gecko_reply.element_reference("find", value)
                self.assert_refused("find", caught.exception)

    def test_a_printed_pdf_is_its_decoded_bytes(self) -> None:
        pdf = b"%PDF-1.7\nbody\n"
        encoded = base64.b64encode(pdf).decode("ascii")
        self.assertEqual(gecko_reply.printed_pdf("print", encoded), pdf)

    def test_a_print_value_that_is_no_pdf_is_refused_naming_the_command(self) -> None:
        not_a_pdf = base64.b64encode(b"<html>").decode("ascii")
        for value in (7, "not base64!", not_a_pdf):
            with self.subTest(value=value):
                with self.assertRaises(WebDriverError) as caught:
                    gecko_reply.printed_pdf("print it", value)
                self.assert_refused("print it", caught.exception)


if __name__ == "__main__":
    unittest.main()
