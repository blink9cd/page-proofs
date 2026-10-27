"""What a Firefox session asks for: the isolation and state prefs, the capabilities, the dead port.

The isolation prefs keep each request Firefox makes but the served origin's off the network, in
layers, each measured on macOS by running Firefox inside a kernel network fence that logged
what got past the layers:

- The proxy. A proxy auto-config script, `proxy_script`, sends every request but the served
  origin's to `DeadPort`, a loopback port held for the run that ends each connection at once,
  so the request fails at once instead of leaving; the served origin's pages and WebSockets
  alone go direct. Loopback is handed to the script too, so a page reaches no other port on
  it, `localhost` and `::1` included (measured: an image, a POST and a WebSocket to another
  loopback port reached nothing). Firefox runs a script it cannot evaluate as no proxy at all
  (measured: every request went direct), so the script is one fixed text, held by a test.
- No bypass. Remote Settings retries a failed proxied request with the proxy bypassed, and that
  retry never asks the proxy service, so the proxy's own failover setting does not govern it.
  Measured with the proxy alone, those retries resolved Remote Settings' host and went direct;
  with `network.proxy.allow_bypass` off, every one of them went to the dead proxy. Firefox's
  code gives Telemetry's sender the same retry, behind the same pref.
- No lookup. Every host name Firefox resolves itself resolves to loopback
  (`network.dns.forceResolve`), so no lookup leaves the machine, and a direct connection by
  name lands on loopback. A proxied request hands its name to the proxy and resolves nothing.
- The rest. Protocols no HTTP proxy carries (WebRTC, HTTP/3, WebTransport), connections opened
  on a guess, and Firefox's own reachability checks are off.

One more layer is not a pref but an environment variable, so it rides on geckodriver's
environment with the run's others: Firefox's non-local guard, which ends the browser on a
direct TCP connection it counts as non-local. With every layer in place, a page asking for three
off-site resources made no lookup and no direct connection.

Every session also turns off behaviors of Firefox's own that show in a page's record as if the
page had done them: the icon it fetches for its tab, a link's visited color, and its short
lazy-load margin. `_PAGE_OWN_PREFS` says what each did, as measured.

These are kept apart from geckodriver's process because a session's request needs nothing of
it: each is a plain value, or one loopback socket, that a test can check without starting
anything. Every behavior claimed here was measured on macOS, the one platform the Firefox
engine runs on; where another platform is known to differ, the claim says so.

Entry point assumptions: the standard library and this folder's `print_record` and
`print_targets` only.
"""

from __future__ import annotations

import contextlib
import json
import socket
import struct
import threading
from collections.abc import Mapping
from types import TracebackType
from typing import Final
from urllib.parse import quote, urlsplit

from print_record import EngineError
from print_targets import NATIVE_DARK

#: The one address the dead proxy is held on, and every loopback socket here bound to.
LOOPBACK: Final = "127.0.0.1"

#: The highest port a TCP socket can have.
HIGHEST_PORT: Final = 65535

#: The largest integer JSON carries exactly, which WebDriver makes the ceiling on a timeout.
_MAX_SAFE_INTEGER: Final = 2**53 - 1

#: How long `DeadPort`'s stop may block on its wake-up connection, and again on the wait for
#: its thread to end. Over 300 stops on macOS, the slowest connection took 0.19 ms and the
#: slowest wait 0.15 ms (measured); the bound keeps a wedged one from hanging the run.
_SOCKET_TIMEOUT_S: Final = 10.0

#: How often `DeadPort`'s accept loop looks at its stop flag while no connection arrives, and
#: the longest it waits before trying an accept that failed again.
_ACCEPT_POLL_S: Final = 0.5

#: The accept loop's first wait after an accept that failed outright, doubled after each
#: further failure up to `_ACCEPT_POLL_S`: an error that persists, such as running out of
#: file descriptors, fails again at once, and a loop retrying with no wait pins a core.
_FIRST_BACK_OFF_S: Final = 0.01
_BACK_OFF_GROWTH: Final = 2

#: Connections the kernel queues for `DeadPort` before its thread accepts them: a page's
#: blocked requests arrive in bursts, and one the queue cannot hold is not answered at once.
_BACKLOG: Final = 128

#: `struct linger` with lingering on and zero seconds: a close that resets the connection.
_LINGER_ZERO: Final = struct.pack("ii", 1, 0)

#: The name `DeadPort`'s thread leads with, so a thread dump says which tool it belongs to.
_TOOL_NAME: Final = "print_pass"

#: `network.proxy.type`: a proxy auto-config script, at the URL `network.proxy.autoconfig_url`.
_AUTOCONFIG_PROXY: Final = 2

#: What a `data:` URL holding a proxy auto-config script is typed as.
_AUTOCONFIG_TYPE: Final = "application/x-ns-proxy-autoconfig"

#: The schemes the served origin is reached by, which the script sends direct: its pages over
#: plain HTTP, and a WebSocket to the same server. Firefox hands the script a WebSocket's URL
#: under its own scheme (measured: an `http` rule did not admit a `ws` URL).
_DIRECT_SCHEMES: Final = ("http", "ws")

#: `network.trr.mode`: DNS over HTTPS off by choice, so the system's own resolver is the only one.
_TRR_OFF: Final = 5

#: `layout.css.prefers-color-scheme.content-override`: what the page's media query reads.
_CONTENT_SCHEME: Final = "layout.css.prefers-color-scheme.content-override"
_CONTENT_DARK, _CONTENT_LIGHT = 0, 1

#: The prefs for each color scheme a targets file may ask for. `native-dark` darkens the
#: system theme Firefox reads, its counterpart to Chrome's `--force-dark-mode`.
_COLOR_SCHEME_PREFS: Final[Mapping[str, Mapping[str, object]]] = {
    "native": {},
    "light": {_CONTENT_SCHEME: _CONTENT_LIGHT},
    "dark": {_CONTENT_SCHEME: _CONTENT_DARK},
    NATIVE_DARK: {"ui.systemUsesDarkTheme": 1},
}

#: The prefs for each reduced-motion setting a targets file may ask for.
_REDUCED_MOTION_PREFS: Final[Mapping[str, Mapping[str, object]]] = {
    "native": {},
    "reduce": {"ui.prefersReducedMotion": 1},
}

#: How near the window, on each side, a lazy image starts loading: a million CSS pixels,
#: about a thousand printed pages. A decimal string, since Firefox took the margin given as
#: one and ignored the same value given as an integer (measured: as integers, no image past
#: Firefox's own margin loaded).
_LAZY_LOAD_MARGIN: Final = "1000000"

#: Prefs every session asks for, so what a state reports is the page's own. Each turns off
#: a behavior of Firefox's own that shows in the page's record as if the page had done it.
_PAGE_OWN_PREFS: Final[Mapping[str, object]] = {
    # Firefox fetches an icon for its tab: the one a page links, or else a guessed
    # `/favicon.ico`. It times the fetch in the page's own Resource Timing, so a page with
    # no such file had an HTTP 404 in every state (measured). Off, neither fetch is made.
    "browser.chrome.site_icons": False,
    # A link's visited color comes from history, which Firefox looks up asynchronously, and
    # again for each print's copy of the page. A print that waited on an image showed the
    # page's link to itself visited, and the next print of the same load unvisited, so
    # backgrounds off and on differed where no background did (measured). Off, every link
    # prints unvisited.
    "layout.css.visited_links_enabled": False,
    # Firefox starts loading a lazy image only once it is near the window, nearer than
    # Chromium's margin: an image 1400 px below a 1280x800 window never started, so the
    # probe read it incomplete and the settle waited out the timeout, while the print loaded
    # it anyway. With these, a lazy image within the margin loads as an eager one, so the
    # probe reads it as the print will hold it, and a broken one is still caught (measured:
    # each side's image, 4,000 px or more out, and one 500,000 px below, read complete, and
    # a missing one read broken).
    "dom.lazy-loading.margin.top": _LAZY_LOAD_MARGIN,
    "dom.lazy-loading.margin.bottom": _LAZY_LOAD_MARGIN,
    "dom.lazy-loading.margin.left": _LAZY_LOAD_MARGIN,
    "dom.lazy-loading.margin.right": _LAZY_LOAD_MARGIN,
}


def isolation_prefs(dead_port: int, *, served: str) -> dict[str, object]:
    """The prefs that keep every Firefox request but the served origin's off the network.

    `dead_port` is a loopback port that ends every connection at once: `DeadPort`'s.
    `served` is the base URL the tool's server answers on, `http://127.0.0.1:<port>`.

    Raises:
        ValueError: `dead_port` is not a port from 1 to 65535, or `served` is not plain
            HTTP on the loopback address with such a port.
    """
    script = proxy_script(served_port=_served_port(served), dead_port=dead_port)
    return {
        # The dead proxy. A proxy auto-config script sends every request, and the
        # WebSockets that ride them, to a loopback port that ends it, so it fails at once
        # instead of leaving the machine; the served origin alone goes direct. Firefox
        # never hands `localhost`, `127.0.0.1` or `[::1]` to a proxy unless the hijack pref
        # is on, and on it hands them to the script, so no other loopback port is reached.
        "network.proxy.type": _AUTOCONFIG_PROXY,
        "network.proxy.autoconfig_url": f"data:{_AUTOCONFIG_TYPE},"
        + quote(script, safe=""),
        "network.proxy.allow_hijacking_localhost": True,
        # No direct connection when a proxy fails: the proxy service's own failover would
        # otherwise send a request direct once the dead proxy ended it.
        "network.proxy.failover_direct": False,
        # Remote Settings and Telemetry retry a failed proxied request with the proxy
        # bypassed, a retry that never asks the proxy service, so the failover pref above
        # cannot refuse it (measured: Remote Settings' retries went direct); this one does.
        "network.proxy.allow_bypass": False,
        # Every host name Firefox resolves itself resolves to loopback: no lookup leaves the
        # machine, and a direct connection by name lands on loopback.
        "network.dns.forceResolve": LOOPBACK,
        # WebRTC's STUN and ICE are UDP, which no HTTP proxy carries.
        "media.peerconnection.enabled": False,
        # HTTP/3 and WebTransport are QUIC, over UDP, which no HTTP proxy carries either.
        "network.http.http3.enable": False,
        "network.webtransport.enabled": False,
        # A proxied request hands its host name to the proxy and resolves nothing; these
        # stop Firefox prefetching a lookup for each name it sees in a page.
        "network.dns.disablePrefetch": True,
        "network.dns.disablePrefetchFromHTTPS": True,
        # No connection opened on a guess: the predictor, link prefetch, and the
        # speculative connections a hovered link starts.
        "network.predictor.enabled": False,
        "network.prefetch-next": False,
        "network.http.speculative-parallel-limit": 0,
        # No resolver but the system's: DNS over HTTPS would reach one of its own.
        "network.trr.mode": _TRR_OFF,
        # Firefox's own reachability checks, which reach out on a timer whatever the page does.
        "network.captive-portal-service.enabled": False,
        "network.connectivity-service.enabled": False,
        # No service worker: one could outlive its page and answer a later state from cache.
        "dom.serviceWorkers.enabled": False,
        # The page's uncaught errors and console lines go to stdout, which is geckodriver's
        # log: the one place a run reads a page's errors from.
        "devtools.console.stdout.content": True,
    }


def proxy_script(*, served_port: int, dead_port: int) -> str:
    """The proxy auto-config script: the served origin direct, every other URL to `dead_port`.

    A URL is the served origin's when it starts with its scheme, the loopback address and
    `served_port`, then a slash, so no user name, longer port or other host can pass.

    Raises:
        ValueError: either port is not one from 1 to 65535.
    """
    for port in (served_port, dead_port):
        if not 0 < port <= HIGHEST_PORT:
            raise ValueError(f"a port is from 1 to {HIGHEST_PORT}, not {port!r}")
    direct = " || ".join(
        f"url.indexOf({json.dumps(f'{scheme}://{LOOPBACK}:{served_port}/')}) === 0"
        for scheme in _DIRECT_SCHEMES
    )
    dead = json.dumps(f"PROXY {LOOPBACK}:{dead_port}")
    return (
        "function FindProxyForURL(url, host) {"
        f' if ({direct}) return "DIRECT"; return {dead}; }}'
    )


def _served_port(served: str) -> int:
    """The port of `served`, which must be plain HTTP on the loopback address.

    Raises:
        ValueError: it is not, or names no port, or does not parse.
    """
    parts = urlsplit(served)
    port = parts.port  # raises on a port that is not one
    if parts.scheme != _DIRECT_SCHEMES[0] or parts.hostname != LOOPBACK or port is None:
        raise ValueError(
            f"the served origin is http://{LOOPBACK}:<port>, not {served!r}"
        )
    return port


def state_prefs(*, color_scheme: str, reduced_motion: str) -> dict[str, object]:
    """The prefs one state's color scheme and reduced motion ask Firefox for, in a new dict.

    `native` asks for nothing, leaving Firefox's own setting.

    Raises:
        ValueError: either value is not one a targets file may hold.
    """
    return {
        **_prefs_for(_COLOR_SCHEME_PREFS, color_scheme, "color scheme"),
        **_prefs_for(_REDUCED_MOTION_PREFS, reduced_motion, "reduced motion"),
    }


def _prefs_for(
    table: Mapping[str, Mapping[str, object]], value: str, what: str
) -> Mapping[str, object]:
    """`table`'s prefs for `value`; a ValueError naming `what` when it has none."""
    try:
        return table[value]
    except KeyError:
        raise ValueError(f"no Firefox prefs for {what} {value!r}") from None


def capabilities(*, prefs: Mapping[str, object], timeout_ms: int) -> dict[str, object]:
    """The new-session body: headless Firefox with `prefs`, its timeouts at `timeout_ms`.

    The prefs that keep a state's report the page's own come first, and `prefs` over them,
    so a pref the caller names wins. No `binary` is named: geckodriver finds the installed
    Firefox itself. Certificate errors stay errors, a page load waits for the load event, and
    Find Element never waits: the engine polls for itself.

    Raises:
        ValueError: `timeout_ms` is not from 1 to the largest integer WebDriver carries.
    """
    if not 0 < timeout_ms <= _MAX_SAFE_INTEGER:
        raise ValueError(
            f"a timeout is from 1 to {_MAX_SAFE_INTEGER} ms, not {timeout_ms!r}"
        )
    return {
        "alwaysMatch": {
            "browserName": "firefox",
            "acceptInsecureCerts": False,
            "pageLoadStrategy": "normal",
            "timeouts": {"script": timeout_ms, "pageLoad": timeout_ms, "implicit": 0},
            "moz:firefoxOptions": {
                "args": ["-headless"],
                "prefs": {**_PAGE_OWN_PREFS, **prefs},
            },
        }
    }


class DeadPort:
    """A loopback TCP port, held while the block runs, that ends every connection at once.

    `with DeadPort() as port:` gives the port. A listener holds it, and a thread of its own
    accepts each connection and closes it with a zero linger, which resets it: the client
    fails within milliseconds, having sent nothing anywhere but this process. While the
    listener holds the port, no other socket can bind that address on macOS, `SO_REUSEADDR`
    or not (measured; Linux binds the same way). Windows would not hold it: there a rival
    setting `SO_REUSEADDR` can take a port unless its holder set `SO_EXCLUSIVEADDRUSE`, which
    this does not, and the engine does not run there. Leaving the block stops the thread and
    closes the listener, freeing the port. One already held cannot be entered again.

    Why not a socket bound and never listening, or one merely connected, both thread-free:
    each was measured on macOS. The kernel drops a connection to a bound socket that is not
    listening, unanswered, so a request proxied to it would stall for the whole connect
    timeout (still unanswered after 3 s). A port held by a connected socket does refuse at
    once, but a process that sets `SO_REUSEADDR` can bind and listen on it.
    """

    def __init__(self) -> None:
        """Hold nothing until the block starts."""
        self._held = contextlib.ExitStack()
        self._port: int | None = None

    def __enter__(self) -> int:
        """Listen on a free loopback port, start the thread resetting it, and give the port.

        Raises:
            RuntimeError: this one already holds a port; a second listener and thread would
                outlive the stop, which ends only the last.
        """
        if self._port is not None:
            raise RuntimeError(f"the dead port {self._port} is already held")
        with contextlib.ExitStack() as held:
            listener = held.enter_context(
                socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            )
            # The accept loop looks at its stop flag at least this often, so a stop whose
            # wake-up connection failed still ends it.
            listener.settimeout(_ACCEPT_POLL_S)
            listener.bind((LOOPBACK, 0))
            listener.listen(_BACKLOG)
            port: int = listener.getsockname()[1]
            stop = threading.Event()
            # A daemon, so a thread whose stop failed can never keep the process alive.
            thread = threading.Thread(
                target=_reset_each_connection,
                args=(listener, stop),
                name=f"{_TOOL_NAME} dead port {port}",
                daemon=True,
            )
            thread.start()
            # Run before the listener's close, which was entered first.
            held.callback(_stop_resetting, thread, stop, port)
            self._held = held.pop_all()
        self._port = port
        return port

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the thread, then close the listener, freeing the port.

        Raises:
            EngineError: the thread outlived its stop; the listener is closed all the same.
        """
        self._port = None
        self._held.close()


def _reset_each_connection(listener: socket.socket, stop: threading.Event) -> None:
    """Accept each connection on `listener` and reset it at once, until `stop` is set.

    An accept that fails outright is tried again after a wait on `stop`, longer after each
    failure in a row, up to `_ACCEPT_POLL_S`; one that succeeds starts the waits over.
    """
    back_off_s = _FIRST_BACK_OFF_S
    while not stop.is_set():
        try:
            connection, _ = listener.accept()
        except (TimeoutError, ConnectionAbortedError):
            # A timeout, so `stop` is looked at again; or a connection its client gave up
            # on before it was accepted, which needs nothing.
            continue
        except OSError:
            # Anything else may fail again at once, such as a process out of descriptors.
            # Waiting on `stop` keeps a stop prompt; the queued connections wait meanwhile.
            stop.wait(back_off_s)
            back_off_s = min(back_off_s * _BACK_OFF_GROWTH, _ACCEPT_POLL_S)
            continue
        back_off_s = _FIRST_BACK_OFF_S
        # A client already gone needs no reset, and its error must not end the thread, which
        # would leave every later connection queued unanswered; the close happens either way.
        with contextlib.closing(connection), contextlib.suppress(OSError):
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, _LINGER_ZERO)


def _stop_resetting(thread: threading.Thread, stop: threading.Event, port: int) -> None:
    """Stop `thread`'s accept loop on `port` and wait for it, each step bounded.

    Raises:
        EngineError: the thread was still running when the wait for it ran out. It is a
            daemon, so it cannot keep the process alive; but a stop that cannot say it
            stopped is a failure, and this module reports each failure by raising.
    """
    stop.set()
    # One last connection wakes the accept at once, instead of at its next timeout. A
    # failure only means the loop wakes on that timeout.
    with contextlib.suppress(OSError):
        socket.create_connection((LOOPBACK, port), timeout=_SOCKET_TIMEOUT_S).close()
    thread.join(timeout=_SOCKET_TIMEOUT_S)
    if thread.is_alive():
        raise EngineError(
            f"the dead port's thread outlived its stop by {_SOCKET_TIMEOUT_S:g} s"
        )
