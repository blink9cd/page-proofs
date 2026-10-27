"""Every context of an engine with an extension: a fresh persistent launch, confirmed loaded.

An extension loads into a browser's profile, so a rendering cannot take a fresh context from one
shared browser, as every other rendering does. `PersistentBrowser` stands where that browser
stands: `pass_engine.open_context` asks it for a context exactly as it asks a browser, and it
launches a persistent context in a fresh profile under the run's scratch folder -- never anyone's
own profile -- with the rendering's own options and the engine's extension. Then, in order:

1. **Confirm the extension loaded**, within the step timeout. Chromium: its manifest's service
   worker is running, at a `chrome-extension://` url on the id Chromium derives from the
   manifest's key, or without one from the folder's path (`pass_extension.read_unpacked`).
   Firefox: the profile's `extensions.json`
   lists the add-on as active in the profile's own location. Firefox registers a sideloaded
   add-on at startup, so one that is not active after the first launch is given ONE relaunch on
   the same profile. Not loaded by then, the context is closed and `ExtensionFault` names the
   engine and the extension: the rendering's instrument fault, never a clean measurement.
2. **Set the state's stored values**, through `BrowserContext.set_storage_state`. A persistent
   context takes no `storage_state` at launch; this is Playwright's own setter for a context
   already open, which writes each origin's `localStorage` from a page of its own whose requests
   it answers itself, so the values are in place before the page's own first script runs, as
   they are for a fresh context, and no script is injected into the page measured.
3. **Close every tab already open**: the launch's own, and any an extension opened on install
   (welcome pages, which fail under the egress guard). And from then on, close each tab an
   extension opens as it arrives: a page the pass opens arrives blank, before it navigates,
   while an extension's arrives at its own url.

Closing is the second line, not the first. A tab can report before its close lands, and every
page of a context shares its listeners, so the CSP collector keeps only the reports of the pages
the pass opened itself (`pass_csp.open_page`): what another tab reports is never the measured
page's finding, closed in time or not. The page-level detectors (console, page and HTTP errors,
failed requests) listen on the pass's own page alone.

Whatever fails in getting a context -- a launch, a confirmation, a close, a Playwright call
erroring other than by timing out, or the disk refusing a profile's files -- is `ExtensionFault`:
that rendering's instrument fault, and the run goes on. Closing a persistent context closes its
browser; `render` closes each inside its rendering's bound, which `bound` widens for these
launches. Each profile is removed once its context has closed.

Firefox loads a sideloaded add-on only when it is Mozilla-signed, and only with its profile scopes
enabled. The prefs below reach Firefox before it starts: Playwright writes every
`firefox_user_prefs` entry into the profile's `user.js` as it prepares the profile (measured in
Playwright 1.63's driver), so no `user.js` is written here -- it would be overwritten.

Entry point assumptions: Playwright is importable (the repository's `browser` extra); the
launch options it is handed are the engine's own and its guard's
(`pass_engine.launch_options`).
"""

from __future__ import annotations

import json
import posixpath
import shutil
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pass_checks import poll_until
from pass_extension import Extension
from pass_probe import MS_PER_SECOND, POLL_INTERVAL_SECONDS, pause
from playwright.sync_api import BrowserContext, BrowserType, Page, StorageState, Worker
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

#: The folder, in the run's scratch folder, holding every persistent context's profile.
PROFILES_DIR_NAME = "profiles"

#: Firefox's sideload scopes: every scope enabled and scanned at startup, none auto-disabled.
FIREFOX_SIDELOAD_PREFS: Mapping[str, int] = {
    "extensions.autoDisableScopes": 0,
    "extensions.enabledScopes": 15,
    "extensions.startupScanScopes": 15,
}

#: Where Firefox records a sideloaded add-on, and the location it records it in.
_FIREFOX_REGISTRY = "extensions.json"
_PROFILE_LOCATION = "app-profile"

#: How many launches each engine is given to load its extension: Firefox one relaunch more.
_LAUNCHES = {"chromium": 1, "firefox": 2}

#: Contexts a rendering opens: its own, and its late-script load's.
CONTEXTS_PER_RENDERING = 2

#: A persistent context that has not closed (and its browser with it) in this long has wedged.
PERSISTENT_CLOSE_SECONDS = 60.0

#: Where a page the pass opens arrives, before it navigates.
_BLANK = "about:blank"


class ExtensionFault(RuntimeError):
    """A context with the extension could not be had: the rendering's instrument fault."""


def _first_line(error: Exception) -> str:
    """The first line of an error, which is where it says what went wrong; else its type."""
    return (str(error).strip().splitlines() or [type(error).__name__])[0]


class PersistentBrowser:
    """Stands where a browser stands for an engine with an extension: one launch per context."""

    def __init__(
        self,
        browser_type: BrowserType,
        extension: Extension,
        *,
        scratch: Path,
        timeout: float,
        options: Mapping[str, Any],
    ) -> None:
        """Launch nothing yet: each `new_context` is its own launch, with `options` and more.

        Raises:
            ValueError: `extension` names no id to confirm its load by -- Chromium's extension
                id, or Firefox's gecko id -- which `pass_extension`'s readers always give: a
                defect in the pass, never a load that failed.
        """
        self._type = browser_type
        self._extension = extension
        self._confirm_by = _confirming_id(extension)
        self._profiles = scratch / PROFILES_DIR_NAME
        self._timeout = timeout
        self._options = _with_extension(dict(options), extension)
        self._launches = _LAUNCHES[extension.facts.engine]
        # Each profile, and how many of its contexts are open: one is removed once none is.
        self._open: dict[Path, int] = {}
        self._contexts: dict[Path, BrowserContext] = {}
        #: The engine's version, once a launch has reported one.
        self.version = ""

    @property
    def _named(self) -> str:
        """The extension as a fault names it: `Fixture 2.5.1`, with its gecko id for Firefox."""
        facts = self._extension.facts
        gecko = f" ({facts.gecko_id})" if facts.gecko_id else ""
        return f"{facts.name} {facts.version}{gecko}"

    @property
    def _engine(self) -> str:
        """The engine this browser launches."""
        return self._extension.facts.engine

    def bound(self, timeout: float) -> float:
        """What these launches add to a rendering's bound, in seconds.

        Each context may take a launch, a confirmation and a close, twice over for Firefox.
        """
        per_launch = 2 * timeout + PERSISTENT_CLOSE_SECONDS
        return CONTEXTS_PER_RENDERING * self._launches * per_launch

    def new_context(
        self, *, storage_state: StorageState | None = None, **options: Any
    ) -> BrowserContext:
        """A persistent context with the extension confirmed loaded, holding `storage_state`.

        `options` are the rendering's context options, as `Browser.new_context` takes them.

        Raises:
            ExtensionFault: it could not launch, the extension did not load, the stored values
                could not be set, or the engine or the disk failed on the way.
        """
        try:
            self._remove_closed()
            context = self._launch_confirmed(options)
            _prepare(context, storage_state)
        except (PlaywrightError, OSError) as error:
            raise ExtensionFault(
                f"{self._engine}: the context could not be prepared: {_first_line(error)}"
            ) from error
        return context

    def close(self) -> None:
        """Close any context still open, and remove every profile."""
        for profile, count in list(self._open.items()):
            if count:
                self._contexts[profile].close()
        self._remove_closed()

    def _launch_confirmed(self, options: Mapping[str, Any]) -> BrowserContext:
        """Launch in a fresh profile until the extension is confirmed, relaunching if allowed.

        Raises:
            ExtensionFault: a launch failed, or the extension never loaded.
        """
        self._profiles.mkdir(parents=True, exist_ok=True)
        profile = Path(tempfile.mkdtemp(prefix=f"{self._engine}-", dir=self._profiles))
        self._open[profile] = 0
        self._seed(profile)
        for _attempt in range(self._launches):
            context = self._launch(profile, options)
            try:
                loaded = self._loaded(context, profile)
            except PlaywrightError:
                context.close()
                raise
            if loaded:
                return context
            context.close()
        raise ExtensionFault(
            f"{self._engine}: the extension {self._named} did not load "
            f"within {self._timeout:g}s"
        )

    def _seed(self, profile: Path) -> None:
        """Firefox: place the `.xpi` in the profile's extensions folder, named by its id."""
        gecko_id = self._extension.facts.gecko_id
        if gecko_id is not None:
            folder = profile / "extensions"
            folder.mkdir()
            shutil.copyfile(self._extension.path, folder / f"{gecko_id}.xpi")

    def _launch(self, profile: Path, options: Mapping[str, Any]) -> BrowserContext:
        """One persistent launch in `profile`, counted open until its context closes.

        Raises:
            ExtensionFault: the launch failed.
        """
        try:
            context = self._type.launch_persistent_context(
                profile,
                headless=True,
                timeout=self._timeout * MS_PER_SECOND,
                **self._options,
                **options,
            )
        except PlaywrightError as error:
            raise ExtensionFault(
                f"{self._engine}: cannot launch with the extension {self._named}: "
                f"{_first_line(error)}"
            ) from error
        self._open[profile] += 1
        self._contexts[profile] = context
        context.on("close", lambda _context: self._closed(profile))
        if context.browser is not None:
            self.version = context.browser.version
        return context

    def _closed(self, profile: Path) -> None:
        """One of `profile`'s contexts has closed."""
        self._open[profile] -= 1

    def _remove_closed(self) -> None:
        """Remove every profile none of whose contexts is still open.

        Each is forgotten before it is removed, so one the disk refuses faults one rendering,
        never each after it.
        """
        for profile in [p for p, count in self._open.items() if count == 0]:
            del self._open[profile]
            self._contexts.pop(profile, None)
            shutil.rmtree(profile)

    def _loaded(self, context: BrowserContext, profile: Path) -> bool:
        """Whether the extension is confirmed loaded in `context`, within the step timeout."""
        worker = self._extension.worker
        if worker is not None:
            return _worker_running(
                context, self._confirm_by, worker, timeout=self._timeout
            )
        page = context.pages[0] if context.pages else context.new_page()
        return poll_until(
            lambda: addon_active(profile, self._confirm_by),
            pause=pause(page),
            clock=time.monotonic,
            within=self._timeout,
            interval=POLL_INTERVAL_SECONDS,
        )


def _confirming_id(extension: Extension) -> str:
    """The id `extension`'s load is confirmed by: a worker's extension id, else its gecko id.

    Raises:
        ValueError: it has none, so a confirmation would look for an id named "None".
    """
    if extension.worker is not None:
        if extension.extension_id is None:
            raise ValueError(
                f"{extension.facts.name} has a service worker but no extension id"
            )
        return extension.extension_id
    if extension.facts.gecko_id is None:
        raise ValueError(f"{extension.facts.name} has no gecko id")
    return extension.facts.gecko_id


def _with_extension(options: dict[str, Any], extension: Extension) -> dict[str, Any]:
    """`options` with what loads `extension`: Chromium's two arguments, or Firefox's prefs."""
    if extension.worker is not None:
        folder = extension.path
        loading = [
            f"--disable-extensions-except={folder}",
            f"--load-extension={folder}",
        ]
        return {**options, "args": [*options.get("args", ()), *loading]}
    prefs = {**options.get("firefox_user_prefs", {}), **FIREFOX_SIDELOAD_PREFS}
    return {**options, "firefox_user_prefs": prefs}


def _prepare(context: BrowserContext, storage_state: StorageState | None) -> None:
    """Set `context`'s stored values, close its tabs, and close each tab opened from now on.

    Closed whatever fails, since nothing else will close it.
    """
    try:
        if storage_state is not None:
            context.set_storage_state(storage_state)
        for page in list(context.pages):
            page.close()
        context.on("page", _close_foreign)
    except PlaywrightError:
        context.close()
        raise


def _close_foreign(page: Page) -> None:
    """Close a tab that arrives anywhere but blank: an extension's, never the pass's own."""
    if page.url == _BLANK:
        return
    try:
        page.close()
    except PlaywrightError:
        # It closed first, or its browser is closing: either way it is going, and while it
        # lasts the CSP collector keeps none of its reports, so nothing is lost by leaving it.
        return


def _worker_running(
    context: BrowserContext, extension_id: str, worker: str, *, timeout: float
) -> bool:
    """Whether the extension's service worker runs in `context`, or starts in `timeout`.

    Its url must name both the extension, by `extension_id`, and the worker, by its path.
    """
    path = posixpath.normpath(posixpath.join("/", worker))

    def ours(candidate: Worker) -> bool:
        parts = urlsplit(candidate.url)
        return (
            parts.scheme == "chrome-extension"
            and parts.hostname == extension_id
            and parts.path == path
        )

    if any(ours(candidate) for candidate in context.service_workers):
        return True
    try:
        context.wait_for_event(
            "serviceworker", predicate=ours, timeout=timeout * MS_PER_SECOND
        )
    except PlaywrightTimeoutError:
        return False
    return True


def addon_active(profile: Path, gecko_id: str) -> bool:
    """Whether Firefox's registry in `profile` lists `gecko_id` active in the profile itself.

    A registry not yet written, or caught mid-write, is not yet a yes.
    """
    try:
        registry = json.loads((profile / _FIREFOX_REGISTRY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    addons = registry.get("addons") if isinstance(registry, dict) else None
    return any(
        isinstance(addon, dict)
        and addon.get("id") == gecko_id
        and addon.get("location") == _PROFILE_LOCATION
        and addon.get("active") is True
        for addon in (addons if isinstance(addons, list) else ())
    )


def extra_bound(browser: object, timeout: float) -> float:
    """What `browser`'s launches add to each rendering's bound: nothing for a plain browser."""
    return browser.bound(timeout) if isinstance(browser, PersistentBrowser) else 0.0
