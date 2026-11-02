"""What geckodriver's WebDriver replies hold: each decoded and parsed, or one `WebDriverError`.

Every reply `gecko_client` reads passes through `decode_response`, which makes it its `value`
or one `WebDriverError`, never anything else, whatever the body holds. The values the client
reads further are parsed here too, each refusing a value that is not as WebDriver defines it:
New Session's session id and browser version (`session_fields`) and the Firefox it started
(`process_id`), Find Element's reference (`element_reference`), and Print's PDF
(`printed_pdf`). A parse names the command it is handed in its refusal, so the commands'
names stay the client's.

Kept apart from the client, which sends the commands, since this half reads only what a
reply holds and is the half a fuzz campaign drives: nothing here sends a request, opens a
socket or waits on a bound.

Entry point assumptions: the standard library, and the tool's own `print_record` only.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Final

from print_record import EngineError, first_line

#: The error code of a request that got no reply: the socket failed or timed out.
TRANSPORT: Final = "transport"

#: The error code of a reply that is not WebDriver's: not JSON, or not shaped as a reply.
INVALID_RESPONSE: Final = "invalid response"

#: The W3C error code Find Element replies with when nothing matches.
NO_SUCH_ELEMENT: Final = "no such element"

#: The key W3C WebDriver gives an element reference under.
ELEMENT_KEY: Final = "element-6066-11e4-a52e-4f735466cecf"

#: What a session id or element reference must be to sit in a path as one segment.
#: geckodriver makes both UUIDs; letters, digits and hyphens admit those and no `/`, `?`, `#`
#: or `.`.
WEBDRIVER_ID: Final = re.compile(r"[0-9A-Za-z-]+")

# The status WebDriver answers every success with.
_HTTP_OK: Final = 200

# The capability naming the Firefox geckodriver started: its pid, a whole number (measured).
_PROCESS_ID: Final = "moz:processID"

# The bytes every PDF starts with.
_PDF_MAGIC: Final = b"%PDF-"


class WebDriverError(EngineError):
    """A WebDriver command failed: `<command>: <error>: <first line of its message>`.

    `error` is the W3C error code geckodriver replied with, `TRANSPORT` when no reply could
    be read, or `INVALID_RESPONSE` when the reply was not WebDriver's. Only the message's
    first line is kept, so geckodriver's stack never reaches a report.
    """

    def __init__(self, command: str, error: str, message: str) -> None:
        """Name the command and the error code, and keep the message's first line."""
        super().__init__(f"{command}: {error}: {first_line(message)}")
        self.command = command
        self.error = error


# fuzz-target
def decode_response(command: str, status: int, body: bytes) -> object:
    """A reply's `value`, or the `WebDriverError` it carries; never any other exception.

    A success is status 200 with a JSON object holding `value`. Any other status must carry
    W3C's error object, `{"value": {"error": <code>, "message": <text>}}`, and raises its
    code; its stack is never read. Anything else (a body that is not UTF-8 or not JSON,
    JSON that is not such an object, a code that is empty or spans lines) raises
    `INVALID_RESPONSE`. A refusal names the status and what was wrong, never the body.

    Raises:
        WebDriverError: naming `command`.
    """
    document = _reply_document(command, status, body)
    if status == _HTTP_OK:
        if "value" in document:
            return document["value"]
        problem = "the reply holds no value"
    else:
        fields = _error_fields(document.get("value"))
        if fields is not None:
            raise WebDriverError(command, *fields)
        problem = "the reply holds no error object"
    raise WebDriverError(command, INVALID_RESPONSE, f"status {status}: {problem}")


def session_fields(command: str, value: object) -> tuple[str, str]:
    """New Session's session id and browser version.

    Raises:
        WebDriverError: `INVALID_RESPONSE`, naming `command`, when either is missing or the
            id is no plain token.
    """
    if isinstance(value, dict):
        session_id = value.get("sessionId")
        capabilities = value.get("capabilities")
        version = (
            capabilities.get("browserVersion")
            if isinstance(capabilities, dict)
            else None
        )
        if isinstance(session_id, str) and isinstance(version, str):
            if WEBDRIVER_ID.fullmatch(session_id):
                return session_id, version
    raise WebDriverError(
        command,
        INVALID_RESPONSE,
        "the reply holds no session id fit for a path, or no browser version",
    )


def process_id(value: object) -> int | None:
    """The pid New Session's capabilities name for Firefox; None unless a positive int."""
    capabilities = value.get("capabilities") if isinstance(value, dict) else None
    pid = capabilities.get(_PROCESS_ID) if isinstance(capabilities, dict) else None
    return pid if type(pid) is int and pid > 0 else None


def element_reference(command: str, value: object) -> str:
    """Find Element's element reference, fit for a path.

    Raises:
        WebDriverError: `INVALID_RESPONSE`, naming `command`, when `value` holds none.
    """
    reference = value.get(ELEMENT_KEY) if isinstance(value, dict) else None
    if isinstance(reference, str) and WEBDRIVER_ID.fullmatch(reference):
        return reference
    raise WebDriverError(
        command, INVALID_RESPONSE, "the reply holds no element reference"
    )


def printed_pdf(command: str, value: object) -> bytes:
    """Print's value as the PDF it encodes.

    Raises:
        WebDriverError: `INVALID_RESPONSE`, naming `command`, unless `value` is strict
            base64 of bytes that start a PDF.
    """
    if not isinstance(value, str):
        problem = "the value is not text"
    else:
        try:
            pdf = base64.b64decode(value, validate=True)
        except ValueError:  # binascii.Error, or text outside ASCII
            problem = "the value is not strict base64"
        else:
            if pdf.startswith(_PDF_MAGIC):
                return pdf
            problem = "the decoded bytes do not start a PDF"
    raise WebDriverError(command, INVALID_RESPONSE, problem)


def _reply_document(command: str, status: int, body: bytes) -> dict[str, object]:
    """The reply's body as a JSON object.

    Raises:
        WebDriverError: `INVALID_RESPONSE`, naming `command` and `status`.
    """
    try:
        document = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError:
        problem = "the body is not UTF-8"
    except (ValueError, RecursionError) as error:
        # A JSON error names where parsing stopped, never the text it stopped on. A body
        # nested past the parser's stack raises RecursionError rather than ValueError.
        problem = f"the body is not JSON: {first_line(error)}"
    else:
        if isinstance(document, dict):
            return document
        problem = "the body is not a JSON object"
    raise WebDriverError(command, INVALID_RESPONSE, f"status {status}: {problem}")


def _error_fields(value: object) -> tuple[str, str] | None:
    """W3C's error object's code and message, or None when `value` is not one.

    The code must be one line of text, as every W3C code is, so the error's text stays one.
    """
    if not isinstance(value, dict):
        return None
    error, message = value.get("error"), value.get("message")
    if isinstance(error, str) and isinstance(message, str):
        if error.splitlines() == [error]:
            return error, message
    return None
