"""The fake Playwright: the synchronous API the engine drives, scripted and logged.

`FakePlaywright` and the browser type, browsers, contexts, pages, routes and WebSocket routes
it hands out stand in for Playwright's own. Every one writes each call to ONE shared log, so a
suite can check the order calls happened in across objects, and each does what a `FakeScript`
tells it: the probe results in turn, the requests a page makes and the events it fires, and
which calls fail.

Three calls are traps that raise, so any call to one fails the suite that made it:
`FakePage.emulate_media`, which is not the print path; `FakeWebSocketRoute.close`, which hangs
the real synchronous API when a socket route's handler calls it; and
`FakeWebSocketRoute.connect_to_server`, which would reach the socket's own server.

`FakeBounder` stands in for the watchdog and logs each bound's entry and exit into the same
log, refusing first, with the watchdog's own check, any bound the watchdog would refuse;
`FakeClock` is a clock that moves a fixed step on every reading.

The three fakes the engine hands a dictionary of keywords (`launch`, `new_context` and
`pdf`) bind them to the real Playwright method's own signature first, so a key Playwright
does not take raises the same TypeError here that it raises against a live browser.

Entry point assumptions: imported by a suite under this folder, under an interpreter with
Playwright installed; only its classes' signatures are read. The insert below reaches the
tool's private `src/`, for the watchdog's check, from this file's own location. Nothing here
launches a browser or reaches the network.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from playwright.sync_api import Browser, BrowserType, Page

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import print_watchdog  # noqa: E402 - the tool's private src/, via the insert above
from print_actions import SET_ATTRIBUTE_SOURCE  # noqa: E402 - as above

#: The shared log every fake writes: one tuple per call, its name first, then its arguments.
type CallLog = list[tuple[str, ...]]

# The real methods whose keywords the fakes take, read from the installed Playwright rather
# than restated, so a pin that renames or drops an option changes the fakes with it.
_LAUNCH_SIGNATURE = inspect.signature(BrowserType.launch)
_NEW_CONTEXT_SIGNATURE = inspect.signature(Browser.new_context)
_PDF_SIGNATURE = inspect.signature(Page.pdf)

#: What a fake page's `requestfailed` carries for a request a route aborted: Chrome's own text.
BLOCKED_FAILURE = "net::ERR_BLOCKED_BY_CLIENT"

#: The two browser versions the fake reports: the pinned shell, and installed Chrome.
SHELL_VERSION = "153.0.7000.0"
CHROME_VERSION = "153.0.7001.0"

#: The Playwright channel that names installed Chrome.
_CHROME_CHANNEL = "chrome"

#: What `FakePage.pdf` writes at the path it is given, so a made PDF's path exists.
PLACEHOLDER_PDF = b"%PDF-1.4\n% a placeholder a fake page wrote\n"


def _take_keywords(signature: inspect.Signature, options: dict[str, object]) -> None:
    """Raise TypeError, as calling the real method would, for a keyword it does not take."""
    # The real methods' first parameter is `self`; no instance is needed to check the rest.
    signature.bind(None, **options)


def _fail_if_scripted(error: str | None) -> None:
    """Raise a RuntimeError with `error`'s text, when the script gives one."""
    if error is not None:
        raise RuntimeError(error)


def call(name: str, *args: object, **kwargs: object) -> tuple[str, ...]:
    """One log entry: `name`, each positional argument as text, then `key=repr` by key."""
    keywords = (f"{key}={value!r}" for key, value in sorted(kwargs.items()))
    return (name, *(str(arg) for arg in args), *keywords)


def probe_dict(**overrides: object) -> dict[str, object]:
    """A settled result exactly as the in-page probe returns one, `overrides` layered on."""
    return {
        "colorScheme": "light",
        "reducedMotion": "no-preference",
        "readyState": "complete",
        "fontsStatus": "loaded",
        "title": "A page",
        "images": [],
        "animations": [],
        "reads": [],
        "storage": [],
        **overrides,
    }


def image_dict(
    src: str, *, complete: bool = True, natural_width: int = 64, has_src: bool = True
) -> dict[str, object]:
    """One entry of a probe's `images`, lazy-loaded as the pages under test mostly are."""
    return {
        "src": src,
        "hasSrc": has_src,
        "complete": complete,
        "loading": "lazy",
        "naturalWidth": natural_width,
    }


@dataclass
class FakeScript:
    """What the fake browser does. Every default is a quiet page that settles at once.

    `probes` are what `evaluate` returns in turn across every page, the last one repeated; an
    exception among them is raised instead. `requests` are the URLs a page asks for during
    `goto`, each passed through the context's routes; `web_sockets` are the WebSockets it
    opens then, each passed through the context's socket routes; `events` are fired through
    the page's listeners after both, and each of `csp_payloads` handed to every binding the
    context exposed after them. Each `..._error` is the text of the RuntimeError the call
    it names raises, as a browser that has died does, after the call is logged.
    """

    probes: list[dict[str, object] | Exception] = field(
        default_factory=lambda: [probe_dict()]
    )
    requests: list[str] = field(default_factory=list)
    web_sockets: list[str] = field(default_factory=list)
    events: list[tuple[str, object]] = field(default_factory=list)
    csp_payloads: list[object] = field(default_factory=list)
    failing_selectors: frozenset[str] = frozenset()
    failing_waits: frozenset[float] = frozenset()
    launch_error: str | None = None
    launches_before_error: int = 0  # how many launches succeed before `launch_error`
    new_context_error: str | None = None
    goto_errors: dict[str, str] = field(default_factory=dict)
    pdf_error: str | None = None
    context_close_error: str | None = None
    browser_close_error: str | None = None
    probes_taken: int = field(default=0, init=False)

    def next_probe(self) -> dict[str, object]:
        """The next scripted probe result; a scripted exception is raised instead."""
        item = self.probes[min(self.probes_taken, len(self.probes) - 1)]
        self.probes_taken += 1
        if isinstance(item, Exception):
            raise item
        return item


@dataclass(frozen=True)
class FakeRequest:
    """A request as a route or a `requestfailed` listener is handed it."""

    url: str
    failure: str | None = None


@dataclass(frozen=True)
class FakeResponse:
    """A response as a `response` listener is handed it."""

    url: str
    status: int


class FakeRoute:
    """A request a context's route matched; `aborted_with` is the code it was aborted with."""

    def __init__(self, calls: CallLog, request: FakeRequest) -> None:
        """Hold the matched request; nothing is logged until the handler acts."""
        self.request = request
        self.aborted_with: str | None = None
        self._calls = calls

    def abort(self, error_code: str | None = None) -> None:
        """Log the abort and keep its error code."""
        self._calls.append(call("route.abort", self.request.url, error_code))
        self.aborted_with = error_code


class FakeWebSocketRoute:
    """A WebSocket a context's socket route matched, before it has connected anywhere.

    Its `close` and `connect_to_server` are traps: each logs the call, then fails the suite.
    """

    def __init__(self, calls: CallLog, url: str) -> None:
        """Hold the socket's URL; nothing is logged until the handler acts."""
        self.url = url
        self._calls = calls

    def close(self, code: int | None = None, reason: str | None = None) -> NoReturn:
        """Fail: called inside a socket route's handler, it hangs Playwright's sync API.

        That handler runs on the synchronous API's dispatcher greenlet, and `close` is async:
        its wrapper hands control to the dispatcher until the close is done, which from the
        dispatcher itself is never, so no Playwright timeout can end it.
        """
        self._calls.append(
            call("web_socket_route.close", self.url, code=code, reason=reason)
        )
        raise AssertionError(f"web_socket_route.close must never be called: {self.url}")

    def connect_to_server(self) -> NoReturn:
        """Fail: it opens the socket to its own server, off loopback."""
        self._calls.append(call("web_socket_route.connect_to_server", self.url))
        raise AssertionError(
            f"web_socket_route.connect_to_server must never be called: {self.url}"
        )


class FakePage:
    """A page: listeners, navigation, actions, scripted probes and placeholder PDFs."""

    def __init__(self, context: FakeContext) -> None:
        """Belong to `context`, sharing its log and script."""
        self._context = context
        self._calls = context.calls
        self._script = context.script
        self.listeners: dict[str, list[Callable[..., None]]] = {}
        self.evaluate_args: list[object] = []
        self.pdf_calls: list[dict[str, object]] = []

    def on(self, event: str, f: Callable[..., None]) -> None:
        """Register `f` for `event`."""
        self._calls.append(call("page.on", event))
        self.listeners.setdefault(event, []).append(f)

    def fire(self, event: str, payload: object) -> None:
        """Hand `payload` to every listener registered for `event`, as Playwright does."""
        for listener in self.listeners.get(event, []):
            listener(payload)

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
        """Make the scripted requests through the routes, fire the events, then maybe fail."""
        self._calls.append(
            call("page.goto", url, wait_until=wait_until, timeout=timeout)
        )
        for request_url in self._script.requests:
            # Chrome fails a request its route aborted, so a blocked request is ALSO a
            # failed one: the engine must not count it twice.
            if self._context.intercept(request_url):
                self.fire("requestfailed", FakeRequest(request_url, BLOCKED_FAILURE))
        for socket_url in self._script.web_sockets:
            self._context.intercept_web_socket(socket_url)
        for event, payload in self._script.events:
            self.fire(event, payload)
        for payload in self._script.csp_payloads:
            self._context.call_bindings(self, payload)
        error = self._script.goto_errors.get(url)
        if error is not None:
            raise RuntimeError(error)

    def click(self, selector: str, *, timeout: float) -> None:
        """Log the click; raise when the script says this selector fails."""
        self._calls.append(call("page.click", selector, timeout=timeout))
        self._fail_if_scripted(selector, timeout)

    def wait_for_selector(self, selector: str, *, timeout: float) -> None:
        """Log the wait; raise when the script says this selector fails."""
        self._calls.append(call("page.wait_for_selector", selector, timeout=timeout))
        self._fail_if_scripted(selector, timeout)

    def _fail_if_scripted(self, selector: str, timeout: float) -> None:
        """Raise Playwright's shape of timeout, two lines, for a selector the script fails."""
        if selector in self._script.failing_selectors:
            raise RuntimeError(f"Timeout {timeout}ms exceeded.\nwaiting for {selector}")

    def wait_for_timeout(self, timeout: float) -> None:
        """Log the wait; raise when the script says a wait this long fails."""
        self._calls.append(call("page.wait_for_timeout", timeout))
        if timeout in self._script.failing_waits:
            raise RuntimeError("Target page, context or browser has been closed")

    def evaluate(self, expression: str, arg: object = None) -> object:
        """Keep the argument; answer a `set_attribute`, else the next scripted probe."""
        if expression == SET_ATTRIBUTE_SOURCE and isinstance(arg, list):
            self._calls.append(call("page.evaluate", "set_attribute", *arg[:2]))
            return arg[0] not in self._script.failing_selectors
        self._calls.append(call("page.evaluate"))
        self.evaluate_args.append(arg)
        return self._script.next_probe()

    def pdf(self, **options: object) -> bytes:
        """Keep the options; write a placeholder at `path`, making its folders as Playwright does."""
        _take_keywords(_PDF_SIGNATURE, options)
        background = options.get("print_background")
        self._calls.append(call("page.pdf", print_background=background))
        self.pdf_calls.append(options)
        _fail_if_scripted(self._script.pdf_error)
        path = Path(str(options["path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PLACEHOLDER_PDF)
        return PLACEHOLDER_PDF

    def emulate_media(self, **options: object) -> None:
        """Fail: print is judged from a PDF, never from a page emulating print media."""
        raise AssertionError(f"emulate_media must never be called: {options!r}")


class FakeContext:
    """A browser context: its options, routes, bindings, init scripts and pages."""

    def __init__(
        self, calls: CallLog, script: FakeScript, options: dict[str, object]
    ) -> None:
        """Keep the options it was opened with."""
        self.calls = calls
        self.script = script
        self.options = options
        self.routes: list[
            tuple[Callable[[str], bool], Callable[[FakeRoute], None]]
        ] = []
        self.socket_routes: list[
            tuple[Callable[[str], bool], Callable[[FakeWebSocketRoute], None]]
        ] = []
        self.pages: list[FakePage] = []
        self.bindings: dict[str, Callable[[object, object], None]] = {}
        self.init_scripts: list[str] = []
        self.binding_errors: list[Exception] = []

    def expose_binding(
        self, name: str, callback: Callable[[object, object], None]
    ) -> None:
        """Log the binding by its name, and keep its callback."""
        self.calls.append(call("context.expose_binding", name))
        self.bindings[name] = callback

    def add_init_script(self, script: str) -> None:
        """Log the addition, and keep the script: no fake page runs it."""
        self.calls.append(call("context.add_init_script"))
        self.init_scripts.append(script)

    def call_bindings(self, page: FakePage, payload: object) -> None:
        """Call every binding with `payload` from `page`, as the page's own script would.

        Playwright hands a binding callback's exception back to the PAGE, as the binding
        call's rejection, never to the Python call in flight; so it is kept here, not raised.
        """
        for callback in self.bindings.values():
            try:
                callback({"page": page}, payload)
            except Exception as error:  # the page's to see, as above
                self.binding_errors.append(error)

    def route(
        self, url: Callable[[str], bool], handler: Callable[[FakeRoute], None]
    ) -> None:
        """Keep the matcher and its handler."""
        self.calls.append(call("context.route"))
        self.routes.append((url, handler))

    def route_web_socket(
        self,
        url: Callable[[str], bool],
        handler: Callable[[FakeWebSocketRoute], None],
    ) -> None:
        """Keep the WebSocket matcher and its handler."""
        self.calls.append(call("context.route_web_socket"))
        self.socket_routes.append((url, handler))

    def intercept(self, url: str) -> bool:
        """Hand `url` to the first route whose matcher takes it; True when it was aborted."""
        for matches, handler in self.routes:
            if matches(url):
                route = FakeRoute(self.calls, FakeRequest(url))
                handler(route)
                return route.aborted_with is not None
        return False

    def intercept_web_socket(self, url: str) -> None:
        """Hand the socket `url` to the first socket route whose matcher takes it.

        A socket no route takes connects to its own server, as Playwright's does.
        """
        for matches, handler in self.socket_routes:
            if matches(url):
                handler(FakeWebSocketRoute(self.calls, url))
                return

    def set_default_timeout(self, timeout: float) -> None:
        """Log the default timeout."""
        self.calls.append(call("context.set_default_timeout", timeout))

    def new_page(self) -> FakePage:
        """Open a page in this context."""
        self.calls.append(call("context.new_page"))
        page = FakePage(self)
        self.pages.append(page)
        return page

    def close(self) -> None:
        """Log the close; raise when the script says closing a context fails."""
        self.calls.append(call("context.close"))
        _fail_if_scripted(self.script.context_close_error)


class FakeBrowser:
    """A launched browser: its version, the options it was launched with, its contexts."""

    def __init__(
        self, calls: CallLog, script: FakeScript, options: dict[str, object]
    ) -> None:
        """Report installed Chrome's version when launched on its channel, else the shell's."""
        self._calls = calls
        self._script = script
        self.launch_options = options
        is_chrome = options.get("channel") == _CHROME_CHANNEL
        self.version = CHROME_VERSION if is_chrome else SHELL_VERSION
        self.contexts: list[FakeContext] = []

    def new_context(self, **options: object) -> FakeContext:
        """Open a context with `options`; raise when the script says opening one fails."""
        _take_keywords(_NEW_CONTEXT_SIGNATURE, options)
        self._calls.append(call("browser.new_context", self.version))
        _fail_if_scripted(self._script.new_context_error)
        context = FakeContext(self._calls, self._script, options)
        self.contexts.append(context)
        return context

    def close(self) -> None:
        """Log the close, naming this browser by its version; raise when the script says."""
        self._calls.append(call("browser.close", self.version))
        _fail_if_scripted(self._script.browser_close_error)


class FakeBrowserType:
    """`playwright.chromium`: launches fake browsers, or fails as scripted."""

    def __init__(self, calls: CallLog, script: FakeScript) -> None:
        """Share the log and script with everything launched."""
        self._calls = calls
        self._script = script
        self.browsers: list[FakeBrowser] = []

    def launch(self, **options: object) -> FakeBrowser:
        """Log the launch; raise when the script says this launch fails."""
        _take_keywords(_LAUNCH_SIGNATURE, options)
        self._calls.append(call("chromium.launch", **options))
        if len(self.browsers) >= self._script.launches_before_error:
            _fail_if_scripted(self._script.launch_error)
        browser = FakeBrowser(self._calls, self._script, options)
        self.browsers.append(browser)
        return browser


class FakePlaywright:
    """The synchronous API's root object, as far as the engine uses it: `chromium`."""

    def __init__(self, script: FakeScript | None = None) -> None:
        """Start an empty log; no script is a quiet page that settles at once."""
        self.calls: CallLog = []
        self.script = FakeScript() if script is None else script
        self.chromium = FakeBrowserType(self.calls, self.script)


class FakeBounder:
    """The watchdog's stand-in: logs each bound's entry and exit, and never expires."""

    def __init__(self, calls: CallLog) -> None:
        """Write into `calls`, the fake Playwright's own log."""
        self._calls = calls

    @contextmanager
    def bound(self, seconds: float, what: str) -> Iterator[None]:
        """Log `bound` on entry and `unbound` on exit, around the block.

        A bound the watchdog would refuse raises its `ValueError` first, logging nothing.
        """
        print_watchdog.check_bound(seconds)
        self._calls.append(call("bound", what, seconds))
        try:
            yield
        finally:
            self._calls.append(call("unbound", what))


class FakeClock:
    """A monotonic clock that reads zero first and moves `step` seconds on every reading."""

    def __init__(self, step: float = 1.0) -> None:
        """Start at zero."""
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        """The current reading; the next one is `step` later."""
        now = self._now
        self._now += self._step
        return now
