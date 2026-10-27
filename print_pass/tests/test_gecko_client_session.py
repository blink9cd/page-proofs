"""`gecko_client.GeckoSession`: each WebDriver command it sends, and what it makes of the reply.

Every case runs over `_gecko_client_fixtures`' recording transport and bound stand-in, with a
session already opened, so each log holds exactly the command under test: its method, URL,
body and timeout, inside a bound sized and named for it.

Entry point assumptions: standalone, or discovered by `unittest discover`. The fixtures
module beside this one puts the tool's private `src/` on `sys.path`.
"""

from __future__ import annotations

import base64
import math
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _gecko_client_fixtures import (  # noqa: E402 - beside this module, via the insert above
    ELEMENT_ID,
    ELEMENT_KEY,
    SESSION_URL,
    exchange,
    failure,
    make_session,
    ok,
)
from gecko_client import GeckoSession, WebDriverError  # noqa: E402 - the fixtures' insert

#: The error code a malformed reply is reported under.
_INVALID = "invalid response"

#: A PDF as small as one can start, and the base64 text WebDriver's Print replies with.
_PDF = b"%PDF-1.7\n% a stand-in PDF\n%%EOF\n"
_PDF_BASE64 = base64.b64encode(_PDF).decode("ascii")

#: The print parameters each print case sends, passed through untouched.
_PRINT_PARAMETERS: dict[str, object] = {
    "page": {"width": 21.59, "height": 27.94},
    "background": False,
    "shrinkToFit": True,
}


class SessionCommandTests(unittest.TestCase):
    def test_set_timeouts_sends_script_page_load_and_no_implicit_wait(self) -> None:
        session, log = make_session(ok(None))
        session.set_timeouts(script_ms=3000, page_load_ms=4000)
        self.assertEqual(
            log,
            exchange(
                "set timeouts",
                "POST",
                f"{SESSION_URL}/timeouts",
                {"script": 3000, "pageLoad": 4000, "implicit": 0},
            ),
        )

    def test_set_window_size_sends_the_width_and_height(self) -> None:
        session, log = make_session(ok({"x": 0, "y": 0, "width": 1280, "height": 800}))
        session.set_window_size(1280, 800)
        self.assertEqual(
            log,
            exchange(
                "set window size",
                "POST",
                f"{SESSION_URL}/window/rect",
                {"width": 1280, "height": 800},
            ),
        )

    def test_navigate_sends_the_url(self) -> None:
        session, log = make_session(ok(None))
        session.navigate("http://127.0.0.1:8123/about/")
        self.assertEqual(
            log,
            exchange(
                "navigate",
                "POST",
                f"{SESSION_URL}/url",
                {"url": "http://127.0.0.1:8123/about/"},
            ),
        )

    def test_execute_runs_a_synchronous_script_and_returns_its_value(self) -> None:
        session, log = make_session(ok({"sum": [2, "two"]}))
        result = session.execute("return arguments[0] + 1;", 1, "one")
        self.assertEqual(result, {"sum": [2, "two"]})
        self.assertEqual(
            log,
            exchange(
                "execute",
                "POST",
                f"{SESSION_URL}/execute/sync",
                {"script": "return arguments[0] + 1;", "args": [1, "one"]},
            ),
        )

    def test_execute_without_arguments_sends_an_empty_list(self) -> None:
        session, log = make_session(ok(None))
        session.execute("return 1;")
        self.assertEqual(log[1][3], {"script": "return 1;", "args": []})

    def test_a_payload_json_cannot_carry_is_refused_before_anything_is_sent(
        self,
    ) -> None:
        for value in (math.nan, math.inf):
            with self.subTest(value=value):
                session, log = make_session()
                with self.assertRaises(ValueError):
                    session.execute("return arguments[0];", value)
                self.assertEqual(log, [])

    def test_a_read_only_mapping_is_sent_as_the_object_it_holds(self) -> None:
        session, log = make_session(ok(None))
        session.execute("return 1;", MappingProxyType({"nested": {"a": 1}}))
        self.assertEqual(
            log[1][3], {"script": "return 1;", "args": [{"nested": {"a": 1}}]}
        )

    def test_a_value_json_has_no_form_for_is_refused_before_anything_is_sent(
        self,
    ) -> None:
        # A ValueError, as NaN is: the caller's bad argument, which an engine passes on as
        # a programming error, where a TypeError would read as the browser failing.
        values: tuple[object, ...] = ({"a set"}, {("a", "tuple key"): 1})
        for value in values:
            with self.subTest(value=value):
                session, log = make_session()
                with self.assertRaises(ValueError) as caught:
                    session.execute("return arguments[0];", value)
                self.assertIsInstance(caught.exception.__cause__, TypeError)
                self.assertEqual(log, [])

    def test_find_css_returns_the_element_reference(self) -> None:
        session, log = make_session(ok({ELEMENT_KEY: ELEMENT_ID}))
        self.assertEqual(session.find_css("#menu > button"), ELEMENT_ID)
        self.assertEqual(
            log,
            exchange(
                "find element",
                "POST",
                f"{SESSION_URL}/element",
                {"using": "css selector", "value": "#menu > button"},
            ),
        )

    def test_find_css_returns_none_when_no_element_matches(self) -> None:
        session, _ = make_session(failure(404, "no such element", "Unable to locate"))
        self.assertIsNone(session.find_css("#absent"))

    def test_find_css_raises_any_other_error(self) -> None:
        session, _ = make_session(failure(400, "invalid selector", "Bad selector"))
        with self.assertRaises(WebDriverError) as caught:
            session.find_css("##")
        self.assertEqual(caught.exception.error, "invalid selector")

    def test_find_css_refuses_a_value_without_an_element_reference(self) -> None:
        values: tuple[object, ...] = (
            None,
            ELEMENT_ID,
            {},
            {"ELEMENT": ELEMENT_ID},
            {ELEMENT_KEY: 7},
            {ELEMENT_KEY: ""},
            {ELEMENT_KEY: "a/b"},
            {ELEMENT_KEY: "x?y"},
        )
        for value in values:
            with self.subTest(value=value):
                session, _ = make_session(ok(value))
                with self.assertRaises(WebDriverError) as caught:
                    session.find_css("#menu")
                self.assertEqual(caught.exception.command, "find element")
                self.assertEqual(caught.exception.error, _INVALID)

    def test_click_posts_an_empty_object_to_the_element(self) -> None:
        session, log = make_session(ok(None))
        session.click(ELEMENT_ID)
        self.assertEqual(
            log,
            exchange("click", "POST", f"{SESSION_URL}/element/{ELEMENT_ID}/click", {}),
        )

    def test_click_refuses_what_is_not_an_element_reference(self) -> None:
        for element in ("", "a/b", "..", "x?y", "#menu"):
            with self.subTest(element=element):
                session, log = make_session()
                with self.assertRaises(ValueError):
                    session.click(element)
                self.assertEqual(log, [])

    def test_delete_sends_delete_without_a_body(self) -> None:
        session, log = make_session(ok(None))
        session.delete()
        self.assertEqual(log, exchange("delete session", "DELETE", SESSION_URL, None))


class PrintPdfTests(unittest.TestCase):
    def assert_refused(self, value: object) -> None:
        """Print with `value` as the reply's value, which must be refused as malformed."""
        session, _ = make_session(ok(value))
        with self.assertRaises(WebDriverError) as caught:
            session.print_pdf(_PRINT_PARAMETERS)
        self.assertEqual(caught.exception.command, "print")
        self.assertEqual(caught.exception.error, _INVALID)

    def test_print_sends_the_parameters_and_returns_the_decoded_pdf(self) -> None:
        session, log = make_session(ok(_PDF_BASE64))
        self.assertEqual(session.print_pdf(_PRINT_PARAMETERS), _PDF)
        self.assertEqual(
            log, exchange("print", "POST", f"{SESSION_URL}/print", _PRINT_PARAMETERS)
        )

    def test_a_value_that_is_not_text_is_refused(self) -> None:
        for value in (None, 7, [_PDF_BASE64], {"pdf": _PDF_BASE64}):
            with self.subTest(value=value):
                self.assert_refused(value)

    def test_text_that_is_not_strict_base64_is_refused(self) -> None:
        for value in ("not base64!", _PDF_BASE64[:-1], f"{_PDF_BASE64}\n", "é"):
            with self.subTest(value=value):
                self.assert_refused(value)

    def test_bytes_that_do_not_start_a_pdf_are_refused(self) -> None:
        for content in (b"", b"<html></html>", b" %PDF-1.7", b"%PDF"):
            with self.subTest(content=content):
                self.assert_refused(base64.b64encode(content).decode("ascii"))

    def test_an_error_reply_is_raised(self) -> None:
        session, _ = make_session(failure(500, "unknown error", "Print failed"))
        with self.assertRaises(WebDriverError) as caught:
            session.print_pdf(_PRINT_PARAMETERS)
        self.assertEqual(str(caught.exception), "print: unknown error: Print failed")


class CommandNameTests(unittest.TestCase):
    def test_each_command_names_itself_in_its_errors(self) -> None:
        commands: tuple[tuple[str, Callable[[GeckoSession], object]], ...] = (
            ("set timeouts", lambda s: s.set_timeouts(script_ms=1, page_load_ms=1)),
            ("set window size", lambda s: s.set_window_size(800, 600)),
            ("navigate", lambda s: s.navigate("http://127.0.0.1:8123/")),
            ("execute", lambda s: s.execute("return 1;")),
            ("find element", lambda s: s.find_css("#menu")),
            ("click", lambda s: s.click(ELEMENT_ID)),
            ("print", lambda s: s.print_pdf({})),
            ("delete session", lambda s: s.delete()),
        )
        for command, run in commands:
            with self.subTest(command=command):
                session, log = make_session(failure(500, "unknown error"))
                with self.assertRaises(WebDriverError) as caught:
                    run(session)
                self.assertEqual(caught.exception.command, command)
                self.assertEqual(log[0], ("bound", 10.0, f"webdriver {command}"))


if __name__ == "__main__":
    unittest.main()
