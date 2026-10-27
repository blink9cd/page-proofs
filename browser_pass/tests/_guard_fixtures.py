"""Shared fixtures for browser_pass's egress-guard live suites: the guarded page, and its probes.

`test_pass_extension_live.py` measures this page with an extension, and in Chromium without one;
`test_pass_loopback_live.py` measures it under `--loopback-only` in Firefox and WebKit. They are
two modules so a splitting runner can run them side by side, each its own engine launches. The page asks for
an image off loopback, which the egress guard must fail; reads back what the committed fixture
extension does to it, as console errors; and starts a WebRTC connection to a STUN server on a
loopback UDP port, which counts the datagrams that reach it. What WebKit is expected to do
under the guard differs by platform, so `webkit_refusal` and `check_webkit_udp` choose it per
`sys.platform` (tested engine-free in `test_guard_expectations.py`).

Entry point assumptions: imported after `_live_fixtures`, which puts the tool's `src/` and the
shared loopback server on `sys.path`.
"""

from __future__ import annotations

import contextlib
import socket
import unittest
from collections.abc import Callable, Iterator, Sequence

import loopback_site
import pass_extension
from _live_fixtures import LiveSite, scratch
from pass_extension import Isolation
from pass_report import RenderingResult
from pass_targets import Rendering, State

#: An address no guard may let a request reach: TEST-NET-1, so a guard that failed would send
#: the request nowhere real either, and its failure would then name another error.
OFF_LOOPBACK = "http://192.0.2.1/off-loopback.png"

_PAGE = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' http:">
    <title>measured with an extension</title>
    <script src="report.js"></script>
    <script src="rtc.js"></script>
  </head>
  <body>
    <main>
      <p style="margin: 0">Measured with an extension.</p>
      <img src="{OFF_LOOPBACK}" alt="" width="40" height="40">
    </main>
  </body>
</html>
"""

#: The page's own script: what it found stored as it first ran, and what the extension did.
_REPORT = """const stored = localStorage.getItem("mode");
console.error(`first script read mode=${stored}`);
window.addEventListener("load", () => {
  const root = document.documentElement.getAttribute("data-fixture-extension");
  const style = document.querySelector("style[data-fixture-extension]") !== null;
  console.error(`fixture extension: root ${root}, style ${style}`);
});
"""

#: How long the WebRTC probe waits for its candidates to be gathered before it says it gave up.
GATHER_BOUND_MS = 5000

#: The page's WebRTC probe: whether WebRTC is there at all, and a STUN server on the loopback
#: port `{port}`, which a STUN request reaches over UDP unless the engine's guard stops it. A
#: count of none proves nothing until gathering is complete -- the STUN request, if any, is sent
#: by then -- so it says when it is, or that it gave up after `{bound}` ms.
_RTC = """const absent = typeof RTCPeerConnection === "undefined";
console.error(`webrtc: ${absent ? "absent" : "present"}`);
if (!absent) {
  const connection = new RTCPeerConnection({
    iceServers: [{ urls: "stun:127.0.0.1:{port}" }],
  });
  let done = false;
  const gathered = () => {
    if (!done) {
      done = true;
      console.error("webrtc: gathered");
    }
  };
  connection.addEventListener("icegatheringstatechange", () => {
    if (connection.iceGatheringState === "complete") gathered();
  });
  connection.addEventListener("icecandidate", (event) => {
    if (event.candidate === null) gathered();
  });
  setTimeout(() => {
    if (!done) console.error("webrtc: gathering unfinished after {bound} ms");
  }, {bound});
  connection.createDataChannel("probe");
  connection.createOffer().then((offer) => connection.setLocalDescription(offer));
}
"""

#: The probe's line once gathering is complete, after which its datagram count can be trusted.
GATHERED = "webrtc: gathered"

STORED = State(
    name="stored", page="/index.html", storage=(("mode", "dark"),), actions=()
)

#: How WebKit names a request its guard's proxy refused, per `sys.platform`: its network stack
#: differs by platform. Each was measured (build webkit-2359): macOS's CFNetwork locally, and
#: Linux's libsoup in a CI run. A platform not named here has no measured wording.
_WEBKIT_REFUSALS = {
    "darwin": "(Could not connect to the server.)",
    "linux": "(Could not connect to proxy server 127.0.0.1",
}

#: Whether WebKit's WebRTC is measured to finish gathering on a platform, which makes its UDP
#: reach a verdict there. On macOS it is measured not to, within a rendering, so a count of
#: none there is no verdict.
_WEBKIT_GATHERS = {"linux": True, "darwin": False}

ACTED = "fixture extension: root on, style true"
ABSENT = "fixture extension: root null, style false"


@contextlib.contextmanager
def _stun_server() -> Iterator[tuple[int, Callable[[], int]]]:
    """A UDP port on loopback, and how many datagrams have reached it so far."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((pass_extension.LOOPBACK_HOST, 0))
        server.setblocking(False)

        def received() -> int:
            count = 0
            # A non-blocking read of an empty queue raises: that is the end of what arrived.
            with contextlib.suppress(BlockingIOError):
                while server.recv(2048):
                    count += 1
            return count

        yield server.getsockname()[1], received


def measure(
    case: unittest.TestCase,
    site: LiveSite,
    isolation: Isolation,
    renderings: list[Rendering],
) -> tuple[list[RenderingResult], int]:
    """Serve the guarded page from `case`'s scratch, and run `renderings` over it in `site`.

    Returns the results, and how many datagrams the page's WebRTC probes sent their STUN server.
    """
    folder = scratch(case)
    (folder / "index.html").write_text(_PAGE, encoding="utf-8", newline="\n")
    (folder / "report.js").write_text(_REPORT, encoding="utf-8", newline="\n")
    with _stun_server() as (port, received):
        rtc = _RTC.replace("{port}", str(port)).replace("{bound}", str(GATHER_BOUND_MS))
        (folder / "rtc.js").write_text(rtc, encoding="utf-8", newline="\n")
        with loopback_site.serve_directory(folder) as base_url:
            versions, results = site.run(
                case,
                renderings,
                base_url=base_url,
                isolation=isolation,
                states=[STORED],
            )
        case.assertTrue(all(versions.values()), versions)
        return results, received()


def off_loopback_failures(result: RenderingResult) -> list[str]:
    """The http_error findings naming the off-loopback image."""
    return [f for f in result.findings.get("http_error", ()) if "192.0.2.1" in f]


def webkit_refusal(platform: str) -> str:
    """The words WebKit's failure of a refused request carries on `platform` (`sys.platform`).

    Raises AssertionError on a platform with no measured wording, so it fails rather than passes.
    """
    if platform not in _WEBKIT_REFUSALS:
        raise AssertionError(f"no measured WebKit refusal for {platform!r}")
    return _WEBKIT_REFUSALS[platform]


def check_webkit_udp(
    platform: str, console: Sequence[str], datagrams: int
) -> str | None:
    """Hold WebKit's open-UDP claim on `platform`, from its probe's `console` and `datagrams`.

    Returns None when the claim held, or a note when it went unmeasured: where gathering is
    measured never to finish, a count of none is no verdict. Raises AssertionError when the
    claim failed -- gathering finished and sent nothing -- when a platform that gathers did
    not, or on a platform with no measured claim. A finished gathering is held to the count on
    every platform, so one that starts gathering and sends nothing is caught.
    """
    if platform not in _WEBKIT_GATHERS:
        raise AssertionError(f"no measured WebRTC claim for {platform!r}")
    if GATHERED in console:
        if datagrams == 0:
            raise AssertionError(
                f"WebKit gathered on {platform!r} but sent no STUN datagram"
            )
        return None
    if _WEBKIT_GATHERS[platform]:
        raise AssertionError(
            f"WebKit's WebRTC never finished gathering on {platform!r}"
        )
    if datagrams > 0:
        return None
    return (
        f"WebKit's open UDP is not measured on {platform!r}: its WebRTC never finished "
        "gathering, so its count of no datagrams proves nothing"
    )
