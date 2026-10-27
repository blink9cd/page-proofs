"""Suite: what the engine hands Playwright -- print and context options -- and the loopback rule.

Nothing here runs a browser. The options are compared as whole dictionaries, so a key added,
dropped or renamed fails here rather than as a print that silently differs from the dialog's.
The fake Playwright the engine's suites run against takes exactly the keywords Playwright's
own methods take, so a key the engine misspells fails those suites too.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_playwright  # noqa: E402 - the tool's private src/, via the insert above
from _print_playwright_fixtures import FakePlaywright  # noqa: E402 - beside this module
from print_targets import Action, State, Viewport  # noqa: E402 - as above

# The determinism convention: a profile registered at import, never an
# `@settings` decorator. deadline=None and too_slow suppressed: a busy machine measures load.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

_BASE_URL = "http://127.0.0.1:8123"
_VIEWPORT = Viewport(width=1280, height=800)
#: The unit Playwright's margin is spelled in, and how many of it make an inch, exactly.
_CM = "cm"
_CM_PER_INCH = Fraction(254, 100)

# Hosts a URL is built on for the label's property test, so the served branch is met far
# more often than from text alone: the served origin, loopback on another port and on none,
# another host, and an unclosed bracket urlsplit refuses.
_LABEL_HOSTS = (
    f"{print_playwright.LOOPBACK_HOST}:8123",
    f"{print_playwright.LOOPBACK_HOST}:8124",
    print_playwright.LOOPBACK_HOST,
    "example.net",
    "[::1",
)


def _state(
    *,
    color_scheme: str = "native",
    reduced_motion: str = "native",
    storage: tuple[tuple[str, str], ...] = (),
    actions: tuple[Action, ...] = (),
) -> State:
    """A state with every key at its default but those given."""
    return State(
        name="s",
        color_scheme=color_scheme,
        reduced_motion=reduced_motion,
        storage=storage,
        actions=actions,
        print_at_ms=None,
    )


class PrintOptionsTests(unittest.TestCase):
    """The print dialog's defaults, with backgrounds and the header and footer as asked."""

    def test_backgrounds_off_and_no_header(self) -> None:
        self.assertEqual(
            print_playwright.print_options(backgrounds=False, header_footer=False),
            {
                "format": "Letter",
                "margin": {
                    "top": "1cm",
                    "right": "1cm",
                    "bottom": "1cm",
                    "left": "1cm",
                },
                "tagged": True,
                "print_background": False,
                "display_header_footer": False,
                "prefer_css_page_size": False,
                "scale": 1,
            },
        )

    def test_backgrounds_on_and_the_header(self) -> None:
        self.assertEqual(
            print_playwright.print_options(backgrounds=True, header_footer=True),
            {
                "format": "Letter",
                "margin": {
                    "top": "1cm",
                    "right": "1cm",
                    "bottom": "1cm",
                    "left": "1cm",
                },
                "tagged": True,
                "print_background": True,
                "display_header_footer": True,
                "prefer_css_page_size": False,
                "scale": 1,
            },
        )

    def test_each_call_gets_its_own_margin(self) -> None:
        # A shared nested dictionary would let one caller's edit reach the next print.
        first = print_playwright.print_options(backgrounds=False, header_footer=False)
        second = print_playwright.print_options(backgrounds=False, header_footer=False)
        self.assertIsNot(first["margin"], second["margin"])

    def test_the_margin_measured_is_the_margin_printed(self) -> None:
        # Two spellings of one margin: `page.pdf` is handed centimeters, and the ink extent
        # measures a Chromium PDF against `MARGIN_INCHES`. Each side sent must be that.
        options = print_playwright.print_options(backgrounds=False, header_footer=False)
        sent = options["margin"]
        assert isinstance(sent, dict)
        self.assertEqual(print_playwright.MARGIN_INCHES, 1 / _CM_PER_INCH)
        for side, spelled in sent.items():
            with self.subTest(side=side):
                self.assertTrue(spelled.endswith(_CM), spelled)
                inches = Fraction(spelled.removesuffix(_CM)) / _CM_PER_INCH
                self.assertEqual(inches, print_playwright.MARGIN_INCHES)


class FakeSignatureTests(unittest.TestCase):
    """The fake refuses a keyword Playwright's own method would, with the same TypeError."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pdf_path = Path(folder.name) / "out.pdf"

    def test_launch(self) -> None:
        chromium = FakePlaywright().chromium
        with self.assertRaises(TypeError):
            chromium.launch(timeout=1.0, channels="chrome")
        self.assertEqual(chromium.browsers, [])

    def test_new_context(self) -> None:
        browser = FakePlaywright().chromium.launch(timeout=1.0)
        with self.assertRaises(TypeError):
            browser.new_context(color_schemes="dark")
        self.assertEqual(browser.contexts, [])

    def test_pdf(self) -> None:
        page = FakePlaywright().chromium.launch(timeout=1.0).new_context().new_page()
        with self.assertRaises(TypeError):
            page.pdf(path=self.pdf_path, print_backgrounds=True)
        self.assertFalse(self.pdf_path.exists())

    def test_what_the_engine_passes_is_taken(self) -> None:
        browser = FakePlaywright().chromium.launch(
            channel="chrome", args=["--force-dark-mode"], timeout=1.0
        )
        stored = _state(color_scheme="dark", storage=(("mode", "dark"),))
        context = browser.new_context(
            **print_playwright.context_options(stored, _VIEWPORT, _BASE_URL)
        )
        context.new_page().pdf(
            path=self.pdf_path,
            **print_playwright.print_options(backgrounds=True, header_footer=True),
        )
        self.assertTrue(self.pdf_path.exists())


class ContextOptionsTests(unittest.TestCase):
    """A fresh context per state: its window, scale, emulation and stored values."""

    def options(self, state: State) -> dict[str, object]:
        return print_playwright.context_options(state, _VIEWPORT, _BASE_URL)

    def test_native_emulates_nothing(self) -> None:
        self.assertEqual(
            self.options(_state()),
            {
                "viewport": {"width": 1280, "height": 800},
                "device_scale_factor": 1,
                "color_scheme": "null",
                "reduced_motion": "null",
                # A service worker's own requests pass no context route, so none registers.
                "service_workers": "block",
            },
        )

    def test_light_and_dark_are_emulated(self) -> None:
        for scheme in ("light", "dark"):
            with self.subTest(scheme=scheme):
                options = self.options(_state(color_scheme=scheme))
                self.assertEqual(options["color_scheme"], scheme)
                self.assertEqual(options["reduced_motion"], "null")

    def test_native_dark_emulates_nothing(self) -> None:
        # Its dark comes from Chrome's own --force-dark-mode, never from emulation.
        options = self.options(_state(color_scheme="native-dark"))
        self.assertEqual(options["color_scheme"], "null")

    def test_reduce_is_emulated(self) -> None:
        options = self.options(_state(reduced_motion="reduce"))
        self.assertEqual(options["reduced_motion"], "reduce")
        self.assertEqual(options["color_scheme"], "null")

    def test_storage_becomes_the_origins_local_storage(self) -> None:
        options = self.options(_state(storage=(("mode", "dark"), ("seen", "1"))))
        self.assertEqual(
            options["storage_state"],
            {
                "cookies": [],
                "origins": [
                    {
                        "origin": _BASE_URL,
                        "localStorage": [
                            {"name": "mode", "value": "dark"},
                            {"name": "seen", "value": "1"},
                        ],
                    }
                ],
            },
        )

    def test_no_storage_sets_no_storage_state(self) -> None:
        state = _state(actions=(Action("wait_ms", None, 5),))
        self.assertNotIn("storage_state", self.options(state))


class EmulationTests(unittest.TestCase):
    """Which requested settings are emulated, the one rule the record and the context share."""

    def test_color_schemes(self) -> None:
        emulated = {
            scheme: print_playwright.color_scheme_emulated(scheme)
            for scheme in ("native", "light", "dark", "native-dark")
        }
        self.assertEqual(
            emulated,
            {"native": False, "light": True, "dark": True, "native-dark": False},
        )

    def test_reduced_motion(self) -> None:
        self.assertFalse(print_playwright.reduced_motion_emulated("native"))
        self.assertTrue(print_playwright.reduced_motion_emulated("reduce"))


class IsLoopbackTests(unittest.TestCase):
    """Only the served origin, its literal address and its port, and no network request."""

    def test_the_served_origin_over_http_and_websocket(self) -> None:
        for url in (
            f"{_BASE_URL}/",
            f"{_BASE_URL}/a.png?x=1",
            f"{_BASE_URL}",
            "ws://127.0.0.1:8123/socket",
            "WS://127.0.0.1:8123/socket",
        ):
            with self.subTest(url=url):
                self.assertTrue(print_playwright.is_loopback(url, served=_BASE_URL))

    def test_what_is_no_network_request(self) -> None:
        for url in (
            "data:image/png;base64,AAAA",
            "blob:http://127.0.0.1:8123/5f2c",
            "about:blank",
        ):
            with self.subTest(url=url):
                self.assertTrue(print_playwright.is_loopback(url, served=_BASE_URL))

    def test_another_port_on_loopback_is_another_server(self) -> None:
        # A dev server, a local API or a debugger the user runs: never the page's to reach.
        for url in (
            "http://127.0.0.1:8080/admin",
            "ws://127.0.0.1:1/",
            "http://127.0.0.1/",
            "http://127.0.0.1:0/",
            "http://127.0.0.1:99999/",
            "http://127.0.0.1:x/",
        ):
            with self.subTest(url=url):
                self.assertFalse(print_playwright.is_loopback(url, served=_BASE_URL))

    def test_everything_else_leaves_loopback(self) -> None:
        for url in (
            # A name, resolved by the host: only the literal address counts.
            "http://localhost:8123/",
            "http://127.0.0.2:8123/",
            "http://[::1]:8123/",
            # The served port, but not its origin: the server speaks plain HTTP alone.
            "https://127.0.0.1:8123/a.png",
            "wss://127.0.0.1:8123/socket",
            "https://example.com/",
            "http://127.0.0.1.example.com/",
            "http://127.0.0.1:8123@example.com/",
            "file:///etc/hosts",
            "ftp://127.0.0.1:8123/",
            "",
            "no scheme at all",
        ):
            with self.subTest(url=url):
                self.assertFalse(print_playwright.is_loopback(url, served=_BASE_URL))

    def test_a_url_that_does_not_parse_leaves_loopback(self) -> None:
        # urlsplit raises on an unclosed IPv6 bracket; the route must block, not crash.
        self.assertFalse(print_playwright.is_loopback("http://[::1/", served=_BASE_URL))

    @given(st.text())
    def test_fuzz_is_loopback(self, url: str) -> None:
        # Never raises, and a network url it lets through names the served host and port.
        if print_playwright.is_loopback(url, served=_BASE_URL):
            parts = urlsplit(url)
            if parts.scheme not in {"data", "blob", "about"}:
                self.assertEqual(
                    (parts.hostname, parts.port), (print_playwright.LOOPBACK_HOST, 8123)
                )

    @given(
        st.sampled_from(["http", "https", "ws", "wss", "file", "ftp"]),
        st.text(min_size=1),
        st.text(),
    )
    def test_url_shapes_pass_only_on_the_served_origin(
        self, scheme: str, host: str, path: str
    ) -> None:
        # Shaped like a url, so the host check is reached far more often than from text.
        url = f"{scheme}://{host}/{path}"
        if print_playwright.is_loopback(url, served=_BASE_URL):
            parts = urlsplit(url)
            self.assertEqual(
                (parts.hostname, parts.port), (print_playwright.LOOPBACK_HOST, 8123)
            )
            self.assertIn(scheme, {"http", "ws"})


class RequestLabelTests(unittest.TestCase):
    """A URL as a record names it: on the served origin by its path and query alone."""

    def test_a_served_url_is_its_path_and_query(self) -> None:
        for url, label in (
            (f"{_BASE_URL}/a.png?v=2", "/a.png?v=2"),
            (f"{_BASE_URL}/a.png#top", "/a.png"),
            (f"{_BASE_URL}/fonts/a.woff2", "/fonts/a.woff2"),
            (f"{_BASE_URL}?x=1", "/?x=1"),
            (f"{_BASE_URL}", "/"),
            ("ws://127.0.0.1:8123/live", "/live"),
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    print_playwright.request_label(url, served=_BASE_URL), label
                )

    def test_anything_else_is_kept_whole(self) -> None:
        for url in (
            # Another port on loopback keeps its port: it names another server.
            "http://127.0.0.1:9/a.png",
            "http://127.0.0.1/a.png",
            "https://127.0.0.1:8123?q",
            "https://example.net/x.png",
            "http://localhost:8123/a.css",
            "data:font/woff2;base64,AA",
            # What a CSP violation names in place of a URL.
            "inline",
            "eval",
            "",
            # urlsplit raises on an unclosed IPv6 bracket: kept as written.
            "http://[::1/x",
        ):
            with self.subTest(url=url):
                self.assertEqual(
                    print_playwright.request_label(url, served=_BASE_URL), url
                )

    @given(
        st.text()
        | st.builds(
            "http://{}{}".format,
            st.sampled_from(_LABEL_HOSTS),
            st.text(),
        )
    )
    def test_fuzz_request_label(self, url: str) -> None:
        # Never raises; it shortens exactly the URLs on the served origin, each to a path.
        label = print_playwright.request_label(url, served=_BASE_URL)
        try:
            parts = urlsplit(url)
            served = (parts.scheme, parts.hostname, parts.port) == (
                "http",
                print_playwright.LOOPBACK_HOST,
                8123,
            )
        except ValueError:
            served = False
        self.assertEqual(label != url, served)
        if served:
            self.assertTrue(label.startswith("/"), label)


if __name__ == "__main__":
    unittest.main()
