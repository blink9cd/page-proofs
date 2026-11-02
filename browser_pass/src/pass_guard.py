"""The egress guard: what each engine is launched with under `--loopback-only`.

`--loopback-only` is the egress guard: requests off the served origin (127.0.0.1 at the
port the pages are served on) fail, an extension's own included. A page's script could otherwise
reach whatever else listens on the machine's loopback, a development server or a local API. It
is implied by an extension (`pass_extension`), and exists on its own so the no-extension
BASELINE can be measured under the same guard. The served port is known only once the pages are
served, so `Isolation.serving` names it before any engine launches. Each engine has its own
guard, each measured to fail an off-loopback page request while loopback still loads; what each
guards beyond that differs:

- **Chromium** sends every request but the served origin's, a WebSocket's included, to a proxy
  on a port nothing listens on, loopback's too (measured: an image, a fetch and a WebSocket to
  another loopback port reached nothing); resolves no name but 127.0.0.1
  (`--host-resolver-rules`); sends no UDP that no proxy carries (and an HTTP proxy carries
  none), so WebRTC sends none (measured: a STUN request to a loopback port stopped), and speaks
  no QUIC; and runs its FULL build (`channel="chromium"`) rather than the headless shell, which
  cannot load an extension, so a baseline is measured in the very build its extension run is;
- **Firefox** sends every request but the served origin's to such a proxy, loopback's too, and
  lets no add-on bypass it; and has no WebRTC (measured: `RTCPeerConnection` is gone), no
  WebTransport, no HTTP/3 and no DNS prefetch, each set by a pref;
- **WebKit** sends its requests through Playwright's own proxy option, loopback's excepted, and
  that is all. Its bypass names hosts, not ports, so every loopback port stays reachable
  (measured: a bypass naming the served port left the served origin itself unreachable, and
  Playwright's routes missed a worker's WebSocket); and Playwright gives WebKit no switch for
  its UDP, and WebRTC in it still sends a STUN request under the guard (measured). So a WebKit
  run is guarded for its page's requests off loopback, not its loopback ports or its UDP.

Entry point assumptions: the standard library only, so the CLI can plan a run with no
Playwright installed; `pass_engine.launch_options` hands these options to each launch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:  # pragma: no cover - read by the type checker alone, never run
    from pass_extension import Isolation

#: The one host the egress guard lets a request reach: the address `--serve` listens on.
LOOPBACK_HOST = "127.0.0.1"

#: A port nothing listens on (discard), where the guard sends every off-loopback request.
REFUSING_PORT = 9

#: The proxy every request the guard refuses is sent to, on that port.
REFUSING_PROXY = f"http://{LOOPBACK_HOST}:{REFUSING_PORT}"

#: The port an http(s) url means when it names none.
_DEFAULT_PORTS = {"http": 80, "https": 443}

#: Playwright's name for the full Chromium build, as against its headless shell.
CHROMIUM_CHANNEL = "chromium"

#: Chromium's guard. The resolver rule applies to IP literals as to names, so loopback is
#: excluded by its exact address.
#: WebRTC's sockets never ask the resolver, so its UDP is refused by its own policy, which this
#: build names `--webrtc-ip-handling-policy` (measured: the older `--force-` spelling stopped
#: nothing); QUIC is refused outright. The proxy and its bypass, which name the served port, are
#: added at launch (`_chromium_args`).
CHROMIUM_GUARD_ARGS = (
    f"--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE {LOOPBACK_HOST}",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-quic",
)

#: Firefox's guard: a manual proxy for every scheme, loopback included, which no add-on may
#: bypass, and which the served origin alone does: `network.proxy.no_proxies_on`, added at launch
#: (`_firefox_prefs`), names its port, which Firefox matches (measured). The served origin stays
#: a secure context, as loopback is, so a page keeps its secure-only APIs. Then every channel
#: that would not go through the proxy (WebRTC, WebTransport, HTTP/3, each UDP), and the name
#: lookups a page's links would prefetch.
FIREFOX_GUARD_PREFS: dict[str, str | int | bool] = {
    "network.proxy.type": 1,
    "network.proxy.http": LOOPBACK_HOST,
    "network.proxy.http_port": REFUSING_PORT,
    "network.proxy.ssl": LOOPBACK_HOST,
    "network.proxy.ssl_port": REFUSING_PORT,
    "network.proxy.allow_hijacking_localhost": True,
    "network.proxy.testing_localhost_is_secure_when_hijacked": True,
    "network.proxy.socks_remote_dns": True,
    "network.proxy.allow_bypass": False,
    "media.peerconnection.enabled": False,
    "network.webtransport.enabled": False,
    "network.http.http3.enable": False,
    "network.dns.disablePrefetch": True,
}

#: WebKit's guard, through Playwright's proxy option, bypassed by loopback alone: every port of
#: it, since WebKit's bypass list names hosts and no port (measured: `127.0.0.1:<port>` matched
#: nothing, so the served origin itself went to the proxy).
WEBKIT_GUARD_PROXY = {
    "server": REFUSING_PROXY,
    "bypass": LOOPBACK_HOST,
}


def served_port(base_url: str) -> int:
    """The port of the origin `base_url` is on: the one it names, or its scheme's default.

    Raises:
        ValueError: it names a port that is not one, or its scheme is not http(s).
    """
    parts = urlsplit(base_url)
    if parts.scheme not in _DEFAULT_PORTS:
        raise ValueError(f"{base_url} is not an http(s) url")
    return parts.port or _DEFAULT_PORTS[parts.scheme]


def guard_options(engine: str, isolation: Isolation) -> dict[str, Any]:
    """The launch options `engine` takes for the egress guard; none when the run is unguarded.

    A fresh copy each call, so a caller extending one changes no other launch.

    Raises:
        ValueError: the run is guarded and no origin is served yet (`Isolation.serving`): a
            defect of the caller's, since the guard admits that origin alone.
    """
    if not isolation.loopback_only:
        return {}
    port = isolation.served_port
    if port is None:
        raise ValueError(
            "the guard admits the served origin alone, and no origin is served"
        )
    if engine == "chromium":
        return {"channel": CHROMIUM_CHANNEL, "args": _chromium_args(port)}
    if engine == "firefox":
        return {"firefox_user_prefs": _firefox_prefs(port)}
    return {"proxy": dict(WEBKIT_GUARD_PROXY)}


def _chromium_args(port: int) -> list[str]:
    """Chromium's guard for the origin served on `port`: its switches, then its proxy.

    `<-loopback>` removes Chromium's implicit loopback bypass, and comes first because Chromium
    reads its bypass list last rule first: after the served port's rule, it would override it.
    """
    bypass = f"<-loopback>;{LOOPBACK_HOST}:{port}"
    return [
        *CHROMIUM_GUARD_ARGS,
        f"--proxy-server={REFUSING_PROXY}",
        f"--proxy-bypass-list={bypass}",
    ]


def _firefox_prefs(port: int) -> dict[str, str | int | bool]:
    """Firefox's guard prefs for the origin served on `port`, the one its proxy is bypassed for."""
    return {
        **FIREFOX_GUARD_PREFS,
        "network.proxy.no_proxies_on": f"{LOOPBACK_HOST}:{port}",
    }
