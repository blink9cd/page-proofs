"""A W3C WebDriver client for geckodriver: classic WebDriver, JSON over HTTP, on loopback only.

The Firefox engine drives Firefox through geckodriver, which speaks WebDriver as JSON over
HTTP. This is its client, on the standard library alone. `GeckoClient` opens a session, and
`GeckoSession` sends the few commands the engine needs: the timeouts, the window's size,
navigation, a synchronous script, finding and clicking an element, Print, and the session's
deletion. Every reply passes through `gecko_reply.decode_response`, which makes it its
`value` or one `WebDriverError`, never anything else, whatever the body holds, and each value
read further is parsed there too.

Three rules hold for every request. It goes to `http://127.0.0.1:<port>` and nowhere else:
the client refuses any other base URL, an id spliced into a path must be a plain token, and
`urllib_transport` ignores every proxy the environment names and follows no redirect. It
runs inside the watchdog's bound at `TIMED_CALL_BOUND_FACTOR` times the timeout, and the
transport is handed the timeout itself, so a socket's own timeout fires first and only a
wedged call reaches the bound. And it ends in its result or in one of three exits:

- a `WebDriverError` naming its command, under the W3C error code geckodriver replied with,
  `TRANSPORT` when no reply could be read, or `INVALID_RESPONSE` when the reply is not
  WebDriver's; its text is one line, never a stack;
- a `ValueError` for the caller's bad argument, raised before anything is sent: a payload
  JSON cannot carry, or an element reference unfit for a path, as `GeckoClient` itself
  refuses a base URL off loopback or a timeout the watchdog could not bound;
- whatever an injected transport raises that is not an `OSError`, unchanged, as that
  transport's own defect rather than a failed command.

Entry point assumptions: the standard library, and the tool's own `gecko_reply`,
`print_record`, `print_watchdog` and `print_engine_state` (for the bound's factor) only. The
transport and the bound are injected, so a suite runs the client without a socket or a watch
thread.
"""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import IO, Final

from gecko_reply import (
    INVALID_RESPONSE,
    NO_SUCH_ELEMENT,
    TRANSPORT,
    WEBDRIVER_ID,
    decode_response,
    element_reference,
    printed_pdf,
    process_id,
    session_fields,
)

# Re-exported: the error the client raises, which its callers catch by this module's name.
from gecko_reply import WebDriverError as WebDriverError
from print_engine_state import TIMED_CALL_BOUND_FACTOR
from print_record import first_line
from print_watchdog import Bounder, check_bound

#: Sends one HTTP request: method, URL, JSON body (None for none) and timeout in seconds, to
#: the reply's status and body. A reply with an error status is returned; a request that
#: gets no reply raises `OSError`.
type Transport = Callable[[str, str, bytes | None, float], tuple[int, bytes]]

#: The Content-Type every request is sent with.
JSON_CONTENT_TYPE: Final = "application/json; charset=utf-8"

#: The longest reply read, in bytes. A page decides how long some replies are (a script's
#: result; Print's PDF, base64), so a reply past this fails its command rather than being
#: held in memory whole: `urllib_transport` reads one byte past it, and the client refuses
#: what does. Room for a printed PDF of about 190 MB.
MAX_REPLY_BYTES: Final = 256_000_000

# The name each command goes by in its bound and its errors: short, and stable, since a
# report or a caller may match on one.
_NEW_SESSION: Final = "new session"
_SET_TIMEOUTS: Final = "set timeouts"
_SET_WINDOW_SIZE: Final = "set window size"
_NAVIGATE: Final = "navigate"
_EXECUTE: Final = "execute"
_FIND_ELEMENT: Final = "find element"
_CLICK: Final = "click"
_PRINT: Final = "print"
_DELETE_SESSION: Final = "delete session"

# The highest TCP port.
_MAX_PORT: Final = 65535

# The only base URL the client takes: loopback by address, the port in ASCII digits with no
# leading zero, and no path. `localhost` is refused, since a resolver may map it elsewhere.
_LOOPBACK_BASE: Final = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})")

#: How a session sends one command: its name, the HTTP method, the path under the base URL
#: and the payload (None for no body), to the reply's decoded value.
type _Send = Callable[[str, str, str, Mapping[str, object] | None], object]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Hands a redirect back as the reply it is, so none can lead a request off loopback."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> None:
        """Follow nothing: the redirect's status reaches the caller as an error status."""
        return None


def _loopback_opener() -> urllib.request.OpenerDirector:
    """An opener that takes no proxy from the environment and follows no redirect.

    The default opener honors `http_proxy` and its kin, which would carry a loopback
    request off this machine, or into a dead proxy. Built for each request, so no opener
    made under an earlier environment can stand in for this one.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())


def urllib_transport(
    method: str, url: str, body: bytes | None, timeout_s: float
) -> tuple[int, bytes]:
    """Send one request through `urllib`; the reply's status and body, an error's included.

    Raises:
        OSError: the request got no reply it could read: the connection was refused, the
            socket failed or timed out, or the reply was not HTTP (a `ConnectionError`).
    """
    request = urllib.request.Request(
        url, data=body, method=method, headers={"Content-Type": JSON_CONTENT_TYPE}
    )
    try:
        return _exchange(request, timeout_s)
    except http.client.HTTPException as error:
        # A reply that breaks HTTP is a transport failure, so it is raised as the one error
        # the transport promises rather than as http.client's own, which is not an OSError.
        raise ConnectionError(f"malformed HTTP reply: {first_line(error)}") from error


def _exchange(request: urllib.request.Request, timeout_s: float) -> tuple[int, bytes]:
    """Open `request` and read its reply, returning an error status as a reply.

    No more than one byte past `MAX_REPLY_BYTES` is read, which is enough for the client to
    refuse the reply as too long.
    """
    try:
        with _loopback_opener().open(request, timeout=timeout_s) as response:
            status: int = response.status
            return status, _read_capped(response)
    except urllib.error.HTTPError as error:
        # WebDriver replies to every failed command with an error status and a body naming
        # the error: a reply to decode, not a failure of the transport.
        with error:
            return error.code, _read_capped(error)


def _read_capped(reply: IO[bytes]) -> bytes:
    """`reply`'s body, or its first `MAX_REPLY_BYTES` and one byte more when it is longer.

    A body within the cap is read to its end, so one cut short still raises
    `http.client.IncompleteRead`, as a whole read does: a read of a set length returns what
    arrived without saying the rest never will.
    """
    body = reply.read(MAX_REPLY_BYTES + 1)
    if len(body) > MAX_REPLY_BYTES:
        return body
    return body + reply.read()


def _check_loopback_base(base_url: str) -> None:
    """Refuse a base URL that is not `http://127.0.0.1:<port>`.

    Raises:
        ValueError: naming the URL refused.
    """
    match = _LOOPBACK_BASE.fullmatch(base_url)
    if match is None or int(match.group(1)) > _MAX_PORT:
        raise ValueError(
            f"a WebDriver base URL must be http://127.0.0.1:<port>, not {base_url!r}"
        )


def _json_body(payload: Mapping[str, object]) -> bytes:
    """`payload` as the UTF-8 JSON a request carries.

    Raises:
        ValueError: it holds NaN or an infinity, which JSON cannot carry, or a value or a
            key of a type JSON has no form for.
    """
    try:
        text = json.dumps(payload, allow_nan=False, default=_plain_mapping)
    except TypeError as error:
        # The caller's bad argument, as NaN is, so the same class: every argument this
        # module refuses is a ValueError, never to be taken for the engine failing.
        raise ValueError(f"a WebDriver payload must be JSON: {error}") from error
    return text.encode("utf-8")


def _plain_mapping(value: object) -> dict[object, object]:
    """A mapping JSON cannot write as it is, such as a read-only view, as a plain dict.

    Raises:
        TypeError: `value` is not a mapping, as `json.dumps` raises for it unaided.
    """
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"a {type(value).__name__} cannot be sent as JSON")


class GeckoSession:
    """One WebDriver session: each command the engine sends, bounded, decoded and named."""

    def __init__(
        self,
        send: _Send,
        session_id: str,
        browser_version: str,
        *,
        process_id: int | None,
    ) -> None:
        """Keep how to send a command, the session's id, and Firefox's version and pid.

        Made by `GeckoClient.new_session`, which has checked the id is fit for a path.
        `process_id` is the pid New Session named for Firefox, or None for none.
        """
        self.session_id = session_id
        self.browser_version = browser_version
        self.process_id = process_id
        self._send = send
        self._path = f"/session/{session_id}"

    def _post(self, command: str, suffix: str, payload: Mapping[str, object]) -> object:
        """POST `payload` to the session's path plus `suffix`; the reply's value."""
        return self._send(command, "POST", f"{self._path}{suffix}", payload)

    def set_timeouts(self, *, script_ms: int, page_load_ms: int) -> None:
        """Set the script and page-load timeouts, and no implicit wait.

        With no implicit wait a find that matches nothing answers at once, so a wait for an
        element is the engine's own poll, bounded by its own clock.
        """
        timeouts = {"script": script_ms, "pageLoad": page_load_ms, "implicit": 0}
        self._post(_SET_TIMEOUTS, "/timeouts", timeouts)

    def set_window_size(self, width: int, height: int) -> None:
        """Ask for an outer window of `width` by `height` CSS pixels.

        The reply's rect is not returned: what the page is laid out in is the viewport,
        which the caller reads back from the page itself.
        """
        self._post(_SET_WINDOW_SIZE, "/window/rect", {"width": width, "height": height})

    def navigate(self, url: str) -> None:
        """Load `url`, returning once the page-load strategy says it has loaded."""
        self._post(_NAVIGATE, "/url", {"url": url})

    def execute(self, script: str, *args: object) -> object:
        """Run `script` as a synchronous function body given `args`; what it returns.

        Raises:
            ValueError: an argument holds NaN, an infinity, or a value or a key of a type
                JSON has no form for; nothing is sent.
            WebDriverError: the script threw or overran its timeout, geckodriver could not
                be reached, or its reply is not WebDriver's.
            Exception: whatever the injected transport raises that is not an `OSError`,
                unchanged: the transport's own defect, not a failed command.
        """
        return self._post(
            _EXECUTE, "/execute/sync", {"script": script, "args": list(args)}
        )

    def find_css(self, selector: str) -> str | None:
        """The reference of the first element `selector` matches, or None for none.

        Raises:
            WebDriverError: any failure but `NO_SUCH_ELEMENT`, or a reply holding no
                reference fit for a path.
        """
        locator = {"using": "css selector", "value": selector}
        try:
            value = self._post(_FIND_ELEMENT, "/element", locator)
        except WebDriverError as error:
            if error.error == NO_SUCH_ELEMENT:
                return None
            raise
        return element_reference(_FIND_ELEMENT, value)

    def click(self, element: str) -> None:
        """Click the element `find_css` returned the reference of.

        Raises:
            ValueError: `element` is not a reference fit for a path; nothing is sent.
        """
        if WEBDRIVER_ID.fullmatch(element) is None:
            raise ValueError(f"not a WebDriver element reference: {element!r}")
        self._post(_CLICK, f"/element/{element}/click", {})

    def print_pdf(self, parameters: Mapping[str, object]) -> bytes:
        """Print the page with WebDriver's Print `parameters`; the PDF it made.

        Firefox prints as its print dialog does: `beforeprint` fires, a static clone of the
        page is printed, then `afterprint` fires.

        Raises:
            WebDriverError: the print failed, or its reply is not base64 of a PDF.
        """
        return printed_pdf(_PRINT, self._post(_PRINT, "/print", parameters))

    def delete(self) -> None:
        """End the session, which closes its Firefox."""
        self._send(_DELETE_SESSION, "DELETE", self._path, None)


class GeckoClient:
    """Opens WebDriver sessions on one geckodriver listening on loopback."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: Transport,
        bounder: Bounder,
        timeout_s: float,
    ) -> None:
        """Keep what every request needs, refusing what could leave loopback or not be bound.

        Raises:
            ValueError: `base_url` is not `http://127.0.0.1:<port>`, or `timeout_s` makes a
                bound the watchdog refuses.
        """
        _check_loopback_base(base_url)
        check_bound(timeout_s * TIMED_CALL_BOUND_FACTOR)
        self._base_url = base_url
        self._transport = transport
        self._bounder = bounder
        self._timeout_s = timeout_s

    def new_session(self, capabilities: Mapping[str, object]) -> GeckoSession:
        """Start a session asking for `capabilities`: W3C's `alwaysMatch` and `firstMatch`.

        Raises:
            WebDriverError: geckodriver refused or could not be reached, or its reply holds
                no session id fit for a path, or no browser version.
        """
        payload = {"capabilities": capabilities}
        value = self._send(_NEW_SESSION, "POST", "/session", payload)
        session_id, version = session_fields(_NEW_SESSION, value)
        return GeckoSession(
            self._send, session_id, version, process_id=process_id(value)
        )

    def _send(
        self,
        command: str,
        method: str,
        path: str,
        payload: Mapping[str, object] | None,
    ) -> object:
        """Send one command inside its own bound; the reply's value.

        Raises:
            ValueError: `payload` holds NaN, an infinity, or a value or a key of a type JSON
                has no form for; nothing is sent.
            WebDriverError: `TRANSPORT` when the transport raised `OSError`; else as
                `decode_response` raises.
            Exception: whatever the transport raises that is not an `OSError`, unchanged:
                the transport's own defect, not a failed command.
        """
        body = None if payload is None else _json_body(payload)
        what = f"webdriver {command}"
        try:
            with self._bounder.bound(self._timeout_s * TIMED_CALL_BOUND_FACTOR, what):
                status, reply = self._transport(
                    method, f"{self._base_url}{path}", body, self._timeout_s
                )
        except OSError as error:
            # TimeoutError, ConnectionError and urllib's URLError are all OSErrors. The
            # class is named, since a bare OSError's text can be empty.
            text = f"{type(error).__name__}: {first_line(error)}"
            raise WebDriverError(command, TRANSPORT, text) from error
        if len(reply) > MAX_REPLY_BYTES:
            raise WebDriverError(
                command,
                INVALID_RESPONSE,
                f"the reply is over {MAX_REPLY_BYTES:,} bytes",
            )
        return decode_response(command, status, reply)
