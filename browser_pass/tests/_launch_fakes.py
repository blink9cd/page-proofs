"""Stand-ins for browser_pass's persistent-launch suites: a fake Playwright browser type.

`PersistentBrowser` (`pass_extension_launch`) launches each context of an engine with an
extension. The stand-in browser type here records every launch, and its contexts play the
engine's part -- a service worker that starts or never does, an `extensions.json` Firefox writes
or never does, a tab an extension opens, a call that fails -- so every branch is reached with no
engine build installed. `test_pass_extension_launch.py` drives the launches and their
confirmation, `test_pass_extension_tabs.py` the tabs and faults around them.

Entry point assumptions: imported by a suite in this folder; it puts the tool's `src/` on
`sys.path`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pass_extension  # noqa: E402 - the tool's private src/, via the insert above
from _extension_fixtures import GECKO_ID, unpacked, xpi  # noqa: E402 - beside this module
from pass_extension import Extension  # noqa: E402 - the tool's private src/
from pass_extension_launch import PersistentBrowser  # noqa: E402 - the tool's private src/
from playwright.sync_api import Error as PlaywrightError  # noqa: E402 - after the insert
from playwright.sync_api import (  # noqa: E402 - after the insert
    TimeoutError as PlaywrightTimeoutError,
)

#: Short, so a confirmation that never succeeds gives up quickly.
TIMEOUT = 0.05

CONTEXT_OPTIONS: dict[str, Any] = {
    "viewport": {"width": 390, "height": 844},
    "color_scheme": "dark",
    "reduced_motion": "no-preference",
    "java_script_enabled": True,
    "device_scale_factor": 1,
}

STORAGE: Any = {
    "cookies": [],
    "origins": [
        {
            "origin": "http://127.0.0.1:1",
            "localStorage": [{"name": "mode", "value": "dark"}],
        }
    ],
}

#: The first line of every error a stand-in raises, as a fault quotes it.
FAILED = "Target page, context or browser has been closed"


class Worker:
    """A service worker, as Playwright names one by its url."""

    def __init__(self, url: str) -> None:
        self.url = url


class Page:
    """A tab: closed by the pass unless it is the pass's own."""

    def __init__(
        self,
        url: str,
        log: list[str],
        *,
        close_fails: bool = False,
        pause_fails: bool = False,
    ) -> None:
        self.url = url
        self._log = log
        self._close_fails = close_fails
        self._pause_fails = pause_fails

    def close(self) -> None:
        self._log.append(f"close page {self.url}")
        if self._close_fails:
            raise PlaywrightError(f"{FAILED}\nlog")

    def wait_for_timeout(self, timeout: float) -> None:
        """No time passes: the poll's clock is the real one, and its bound is short."""
        if self._pause_fails:
            raise PlaywrightError(f"{FAILED}\nlog")


class Browser:
    """The browser a persistent context lives in."""

    version = "153.0.1"


class Context:
    """A persistent context: its pages, its workers, the tabs opened in it, and its close.

    Each `fails` names a call that raises a Playwright error that is not a timeout: `wait`,
    `new_page`, `close`, `storage`, or `pause` (a page's).
    """

    def __init__(
        self,
        log: list[str],
        *,
        workers: tuple[str, ...] = (),
        arriving: str | None = None,
        pages: tuple[str, ...] = ("about:blank",),
        browser: Browser | None = None,
        fails: frozenset[str] = frozenset(),
    ) -> None:
        self._log = log
        self.service_workers = [Worker(url) for url in workers]
        self._arriving = arriving
        self.browser = browser
        self._fails = fails
        self.pages = [self._page(url) for url in pages]
        self._handlers: dict[str, list[Callable[[Any], None]]] = {}

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        assert event in ("close", "page"), event
        self._handlers.setdefault(event, []).append(handler)

    def open_tab(self, url: str, *, close_fails: bool = False) -> Page:
        """Open a tab as an extension does: the context's page handlers see it arrive."""
        page = self._page(url, close_fails=close_fails)
        self.pages.append(page)
        for handler in self._handlers.get("page", ()):
            handler(page)
        return page

    def _page(self, url: str, *, close_fails: bool = False) -> Page:
        return Page(
            url, self._log, close_fails=close_fails, pause_fails="pause" in self._fails
        )

    def _fail(self, call: str) -> None:
        if call in self._fails:
            raise PlaywrightError(f"{FAILED}\nlog")

    def wait_for_event(
        self, event: str, *, predicate: Callable[[Worker], bool], timeout: float
    ) -> Worker:
        self._log.append(f"wait {event} {timeout}")
        self._fail("wait")
        if self._arriving is not None:
            worker = Worker(self._arriving)
            if predicate(worker):
                return worker
        raise PlaywrightTimeoutError("Timeout exceeded while waiting for event")

    def set_storage_state(self, storage: object) -> None:
        self._log.append(f"storage {json.dumps(storage)}")
        self._fail("storage")

    def new_page(self) -> Page:
        self._fail("new_page")
        return self.open_tab("about:blank")

    def close(self) -> None:
        self._log.append("close context")
        for handler in self._handlers.get("close", ()):
            handler(self)
        self._fail("close")


class BrowserType:
    """Launches each persistent context from `contexts`, in order, recording what it was given.

    `register` plays Firefox: given the profile, it writes what Firefox would have written there
    by the time the launch returns, for the launch it numbers (from one).
    """

    def __init__(
        self,
        contexts: list[Context],
        *,
        register: Callable[[Path, int], None] | None = None,
        fails_with: str | None = None,
    ) -> None:
        self.contexts = contexts
        self.launches: list[dict[str, Any]] = []
        self._register = register
        self._fails_with = fails_with

    def launch_persistent_context(self, user_data_dir: Path, **options: Any) -> Context:
        self.launches.append({"user_data_dir": user_data_dir, **options})
        if self._fails_with is not None:
            raise PlaywrightError(self._fails_with)
        if self._register is not None:
            self._register(user_data_dir, len(self.launches))
        return self.contexts.pop(0)


def registered(
    profile: Path, *, active: bool = True, location: str = "app-profile"
) -> None:
    """Write `extensions.json` into `profile` as Firefox does once it has the add-on."""
    addon = {"id": GECKO_ID, "location": location, "active": active}
    (profile / "extensions.json").write_text(
        json.dumps({"addons": [addon]}), encoding="utf-8", newline="\n"
    )


def each_launch(write: Callable[[Path], object]) -> Callable[[Path, int], None]:
    """A `register` that does `write` to the profile at every launch."""

    def register(profile: Path, _launch: int) -> None:
        write(profile)

    return register


def write_registry(text: str) -> Callable[[Path], object]:
    """Write `text` as the profile's `extensions.json`."""
    return lambda profile: (profile / "extensions.json").write_text(
        text, encoding="utf-8", newline="\n"
    )


class LaunchCase(unittest.TestCase):
    """A scratch folder, and the two extensions read from it."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.log: list[str] = []
        chromium = pass_extension.read_unpacked(unpacked(self.root / "ext"))
        firefox = pass_extension.read_xpi(xpi(self.root / "fixture.xpi"))
        assert isinstance(chromium, Extension) and isinstance(firefox, Extension)
        self.chromium, self.firefox = chromium, firefox

    def worker_url(self, extension_id: str | None = None) -> str:
        """The fixture's worker as Chromium names it: at its own id unless another is given."""
        named = extension_id or self.chromium.extension_id
        return f"chrome-extension://{named}/bg/worker.js"

    def loaded(self, **options: Any) -> Context:
        """A Chromium context whose worker is running."""
        return Context(self.log, workers=(self.worker_url(),), **options)

    def persistent(
        self, browser_type: BrowserType, extension: Extension, **options: Any
    ) -> PersistentBrowser:
        return PersistentBrowser(
            browser_type,  # type: ignore[arg-type] # a recording stand-in for the browser type
            extension,
            scratch=self.scratch,
            timeout=TIMEOUT,
            options=options,
        )
