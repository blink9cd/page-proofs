"""The Playwright boundary: what the engine calls, what it hands across, and which requests may.

The engine never imports Playwright. The CLI passes the real synchronous API in and the suites
pass a fake, so what the engine may call is named here as `Protocol`s holding ONLY the members
it uses. Playwright's own classes satisfy them under mypy with no cast (checked against 1.63),
so a member the engine starts to use must be added here first.

Two shapes here are not the obvious ones, each because mypy measured the obvious one failing
against Playwright's classes:

- `PageLike.on` is overloaded per event, in Playwright's own overload order. Playwright types
  `on` only through per-event overloads, and mypy matches one overloaded member against
  another item by item, in order.
- `BrowserLike.new_context` and `PageLike.pdf` are properties returning a callable, not
  methods taking `**options`. They are called with the keyword dictionaries
  `context_options` and `print_options` build, and mypy finds a method that spells each
  keyword incompatible with one taking `**options`, while it matches the callable.

Which requests stay on the served origin, `is_loopback`, and how a record names one,
`request_label`, are public rather than the route's own: a record made without a route, or by the CSP
collector, must draw the same line and name a request the same way, or two records would
mean different things by one entry.

Entry point assumptions: the standard library, `print_targets`, `print_record`'s
`CM_PER_INCH`, and the schemes a request is made with, which `print_offsite` holds.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from fractions import Fraction
from typing import Final, Literal, Protocol, overload
from urllib.parse import SplitResult, urlsplit

from print_record import CM_PER_INCH
from print_targets import NATIVE_DARK, PX_PER_INCH, State, Viewport

#: The paper Chrome's print dialog defaults to here, and the margin it puts on every side
#: unless a state's `page_width` sets its own. Playwright's own default margin is zero, so
#: a margin is always passed.
LETTER: Final = "Letter"
_MARGIN_CM: Final = 1
MARGIN: Final = f"{_MARGIN_CM}cm"
#: The same margin in inches, exactly 50/127, which a Chromium PDF's ink is measured against.
#: Derived from the centimeters sent, so the margin printed and the margin measured are one
#: value; as the float `1 / 2.54`, a raster exactly as tall as its two margins (100 rows at
#: 127 dpi) would stand a hair taller than them.
MARGIN_INCHES: Final = Fraction(_MARGIN_CM) / CM_PER_INCH

#: The one address a network request may reach: the one the tool's own server binds.
LOOPBACK_HOST: Final = "127.0.0.1"

#: The schemes a request to the served origin is made with: its pages over plain HTTP, and
#: a WebSocket to the same server. The server speaks no TLS, so `https` and `wss` on its
#: port reach nothing of it.
SERVED_SCHEMES: Final = ("http", "ws")

#: Playwright's timeouts are milliseconds; the engine's settings are seconds.
MS_PER_SECOND: Final = 1000

_MARGIN_SIDES: Final = ("top", "right", "bottom", "left")

# The CSS units a margin is written in, each with its length in an inch, in the order tried:
# the first that holds the margin as a terminating decimal writes it, so a page width's
# margin (a whole or half pixel) goes as px, and the dialog's 1 cm as cm.
_EXACT_UNITS: Final = (("px", Fraction(PX_PER_INCH)), ("cm", CM_PER_INCH))
# The primes a terminating decimal's denominator may hold: those of ten.
_DECIMAL_PRIMES: Final = (2, 5)

# Printed at the size the page lays out at: no zoom, and one device pixel per CSS pixel.
_PRINT_SCALE: Final = 1
_DEVICE_SCALE_FACTOR: Final = 1

# The schemes that make no network request, beside the network's own. `data:`,
# `blob:` and `about:` URLs are the page's own inline data, object URLs and blank page:
# nothing leaves the browser for them.
_LOCAL_SCHEMES: Final = frozenset({"data", "blob", "about"})

# A server path with nothing after the host is the root.
_ROOT_PATH: Final = "/"

# What Playwright takes for "leave this setting alone".
_NO_EMULATION: Final = "null"

# No service worker may register: the requests one makes for its page pass no context route
# (Playwright's own `BrowserContext.route` says so), so one could reach off loopback unseen.
_BLOCK_SERVICE_WORKERS: Final = "block"

# The requested color schemes that emulate nothing: the engine's own setting, and the real
# dark setting installed Chrome is launched with for `native-dark`.
_UNEMULATED_SCHEMES: Final = frozenset({"native", NATIVE_DARK})
_NATIVE_MOTION: Final = "native"


class RequestLike(Protocol):
    """A request, as a route or a `requestfailed` listener is handed it."""

    @property
    def url(self) -> str:
        """The whole URL requested."""

    @property
    def failure(self) -> str | None:
        """Why the request failed, in the browser's own words; None while it has not."""


class ResponseLike(Protocol):
    """A response, as a `response` listener is handed it."""

    @property
    def url(self) -> str:
        """The URL the response answers."""

    @property
    def status(self) -> int:
        """The HTTP status."""


class RouteLike(Protocol):
    """A request a context route intercepted."""

    @property
    def request(self) -> RequestLike:
        """The request intercepted."""

    def abort(self, error_code: str | None = None) -> None:
        """Fail the request with `error_code`, as the browser would name the failure."""


class WebSocketRouteLike(Protocol):
    """A WebSocket a context's socket route intercepted, not yet connected to any server.

    Only its URL is read. Its `close` hangs the synchronous API when a socket route's handler
    calls it, and its `connect_to_server` would reach the socket's own server, so neither is
    here.
    """

    @property
    def url(self) -> str:
        """The URL the page opened the socket to."""


class PageLike(Protocol):
    """A page: listeners, navigation, actions, the probe's `evaluate`, and `pdf`.

    `on` calls a listener with each error the page's script throws and does not catch, each
    request that fails, or each response.
    """

    @overload
    def on(
        self, event: Literal["pageerror"], f: Callable[[Exception], None]
    ) -> None: ...

    @overload
    def on(
        self, event: Literal["requestfailed"], f: Callable[[RequestLike], None]
    ) -> None: ...

    @overload
    def on(
        self, event: Literal["response"], f: Callable[[ResponseLike], None]
    ) -> None: ...

    def goto(self, url: str, *, wait_until: Literal["load"], timeout: float) -> object:
        """Navigate to `url`, returning once its `load` event fires."""

    def click(self, selector: str, *, timeout: float) -> None:
        """Click the element `selector` matches, waiting for it to be actionable."""

    def wait_for_selector(self, selector: str, *, timeout: float) -> object:
        """Wait until an element matching `selector` is visible."""

    def wait_for_timeout(self, timeout: float) -> None:
        """Wait `timeout` milliseconds."""

    def evaluate(self, expression: str, arg: object = ...) -> object:
        """Evaluate `expression` in the page, calling it with `arg` when it is a function."""

    @property
    def pdf(self) -> Callable[..., object]:
        """Print the page to PDF, called with `path` and `print_options`' keywords."""


class ContextLike(Protocol):
    """A browser context: one state's own storage, emulation, routes and CSP collector."""

    def route(
        self, url: Callable[[str], bool], handler: Callable[[RouteLike], None]
    ) -> object:
        """Hand every request whose URL `url` accepts to `handler`."""

    def route_web_socket(
        self,
        url: Callable[[str], bool],
        handler: Callable[[WebSocketRouteLike], None],
    ) -> object:
        """Hand every WebSocket whose URL `url` accepts to `handler`, before it connects.

        A WebSocket never meets `route`: only this sees it.
        """

    def expose_binding(
        self, name: str, callback: Callable[[object, object], None]
    ) -> object:
        """Expose `callback` to every page of this context as the global `name`.

        A page's call hands it the caller (`source`) and the page's own argument. An
        exception it raises goes back to the page, never to the Python call in flight.
        """

    def add_init_script(self, script: str) -> object:
        """Run `script` in every document of this context, before the document's own."""

    def new_page(self) -> PageLike:
        """Open a page in this context."""

    def set_default_timeout(self, timeout: float) -> None:
        """Bound, in milliseconds, every call on this context's pages that takes a timeout."""

    def close(self) -> None:
        """Close the context and its pages."""


class BrowserLike(Protocol):
    """A launched browser."""

    @property
    def version(self) -> str:
        """The browser's version."""

    @property
    def new_context(self) -> Callable[..., ContextLike]:
        """Open a context, called with `context_options`' keywords."""

    def close(self) -> None:
        """Close the browser and every context in it."""


class BrowserTypeLike(Protocol):
    """`playwright.chromium`: what launches a browser."""

    def launch(
        self,
        *,
        timeout: float,
        channel: str | None = ...,
        args: Sequence[str] | None = ...,
    ) -> BrowserLike:
        """Launch the pinned headless shell, or installed Chrome for `channel="chrome"`."""


class PlaywrightLike(Protocol):
    """The synchronous API's root object, as far as the engine uses it."""

    @property
    def chromium(self) -> BrowserTypeLike:
        """The Chromium browser type."""


def print_options(
    *,
    backgrounds: bool,
    header_footer: bool,
    margin_inches: Fraction = MARGIN_INCHES,
) -> dict[str, object]:
    """The keywords `page.pdf` takes for one print: the dialog's defaults, and what is asked.

    US Letter, tagged, scale 1 and no CSS page-size preference are fixed: what Chrome's print
    dialog does here, where Playwright's own defaults differ. The margin, on every side, is
    `margin_inches` (the dialog's 1 cm unless a state's page width sets its own), written
    by `css_margin`. It is a fresh dictionary on every call, so no caller can reach another's.
    """
    return {
        "format": LETTER,
        "margin": dict.fromkeys(_MARGIN_SIDES, css_margin(margin_inches)),
        "tagged": True,
        "print_background": backgrounds,
        "display_header_footer": header_footer,
        "prefer_css_page_size": False,
        "scale": _PRINT_SCALE,
    }


def css_margin(margin_inches: Fraction) -> str:
    """`margin_inches` as the CSS length `page.pdf` is sent: `71.5px`, `1cm`.

    Written exactly, in the first of `_EXACT_UNITS` holding it as a terminating decimal, so
    a page width's half-pixel margin is not rounded. A margin neither unit holds exactly
    (none the tool derives) is written in inches, as near as a float holds it.
    """
    for unit, per_inch in _EXACT_UNITS:
        text = _decimal_text(margin_inches * per_inch)
        if text is not None:
            return f"{text}{unit}"
    return f"{float(margin_inches)}in"


def _decimal_text(value: Fraction) -> str | None:
    """`value` as a plain decimal (`71.5`, `1`), or None when no terminating decimal is it."""
    rest, places = value.denominator, 0
    for prime in _DECIMAL_PRIMES:
        count = 0
        while rest % prime == 0:
            rest, count = rest // prime, count + 1
        places = max(places, count)
    if rest != 1:
        return None
    # The fewest decimal places that hold `value` whole, built from a string, which Decimal
    # takes exactly at any length; a lowest-terms denominator leaves no trailing zero.
    digits = value * 10**places
    return format(Decimal(f"{digits.numerator}E-{places}"), "f")


def color_scheme_emulated(scheme: str) -> bool:
    """Whether a requested color scheme is emulated, rather than the browser's own."""
    return scheme not in _UNEMULATED_SCHEMES


def reduced_motion_emulated(motion: str) -> bool:
    """Whether a requested reduced-motion preference is emulated."""
    return motion != _NATIVE_MOTION


def context_options(
    state: State, viewport: Viewport, base_url: str
) -> dict[str, object]:
    """The keywords `browser.new_context` takes for one state.

    What is not emulated is passed as `"null"`, which leaves the browser's own setting. Stored
    values go in through `storage_state`, for `base_url`'s origin, so no script is injected
    to set them. Service workers are blocked, so every request the page makes meets the route.
    """
    options: dict[str, object] = {
        "viewport": {"width": viewport.width, "height": viewport.height},
        "device_scale_factor": _DEVICE_SCALE_FACTOR,
        "color_scheme": (
            state.color_scheme
            if color_scheme_emulated(state.color_scheme)
            else _NO_EMULATION
        ),
        "reduced_motion": (
            state.reduced_motion
            if reduced_motion_emulated(state.reduced_motion)
            else _NO_EMULATION
        ),
        "service_workers": _BLOCK_SERVICE_WORKERS,
    }
    if state.storage:
        stored = [{"name": key, "value": value} for key, value in state.storage]
        options["storage_state"] = {
            "cookies": [],
            "origins": [{"origin": base_url, "localStorage": stored}],
        }
    return options


# fuzz-target
def is_loopback(url: str, *, served: str) -> bool:
    """Whether a request to `url` stays on the served origin, or is no network request.

    `served` is the base URL the tool's server answers on. Only its literal address and its
    port count: another port on loopback is another server the user runs, a dev server, a
    local API or a debugger, never the page's to reach; `localhost` is a name the host
    resolves, and `::1` is another address. A URL that does not parse is not loopback, so the
    route that asks blocks it rather than raising inside Playwright's dispatch.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in _LOCAL_SCHEMES or _on_served_origin(parts, served)


# fuzz-target
def request_label(url: str, *, served: str) -> str:
    """A URL as a record names it: on the served origin by its path and query, any other whole.

    The served port changes with every run, so it is never recorded; nor is a fragment, which
    no request carries. Any other port is kept, since it names another server. Kept in one
    place, so that no two records name one request two ways, the collector's refused URLs
    among them. A URL that does not parse, or a word in a URL's place (a CSP violation's
    `inline`), is kept as it came.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not _on_served_origin(parts, served):
        return url
    path = parts.path or _ROOT_PATH
    return f"{path}?{parts.query}" if parts.query else path


def _on_served_origin(parts: SplitResult, served: str) -> bool:
    """Whether `parts` name `served`'s origin: a served scheme, its address and its port.

    A port that does not parse, on either side, matches nothing.
    """
    try:
        origin = urlsplit(served)
        same_port = parts.port is not None and parts.port == origin.port
    except ValueError:
        return False
    return (
        parts.scheme in SERVED_SCHEMES
        and origin.scheme == SERVED_SCHEMES[0]
        and parts.hostname == LOOPBACK_HOST == origin.hostname
        and same_port
    )
