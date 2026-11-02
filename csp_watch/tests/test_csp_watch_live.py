"""csp_watch against the real engines: Chromium, Firefox and WebKit, over a served fixture.

The unit suite proves what csp_watch asks of an engine; this one proves what the engines then
report. Two pages, served on loopback from ``fixture/``: ``policy.html`` declares
``default-src 'self'`` and carries one ``style`` attribute that policy refuses, and
``bare.html`` declares no policy. Per engine it shows that with scripts on the one violation
arrives through the binding; that with scripts off NOTHING does (the init script never runs,
which is why a caller reports that rendering as not measured); that ``policy_of`` reads the
declared policy, and its absence, either way; and that a plant on the bare page is caught.

Each engine is launched once for its tests, and each test opens a fresh context in it. Firefox
is launched with its per-user data folders redirected into a scratch folder, so it never reads a
real profile. With an engine build missing where Playwright will look, the module skips at
setup, naming where it looked and the install command in the skip and in one line on stderr;
with ``PAGE_PROOFS_REQUIRE_BROWSERS=1`` it fails instead, so a run that must measure the engines
can never pass with them unmeasured.

Where it looks, and how it reads the opt-in, is a copy of browser_pass's (its ``pass_facts``
lookup and its live fixtures' check), kept here because csp_watch does not import browser_pass.
The copy behaves the same, and changes with it.

Entry point assumptions: discovered by ``test_csp_watch.py`` with this folder as the top-level
directory and the tool folder on ``sys.path``; Playwright is importable; the engine builds are
where ``PLAYWRIGHT_BROWSERS_PATH`` names, else where a plain ``playwright install`` puts them.
"""

# gate-serves: none (its own tests/fixture tree, gated with its tool)

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import time
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import ClassVar, Final
from unittest import mock

import playwright
from playwright.sync_api import Browser, Page, Playwright, sync_playwright

_TOOL_DIR: Final = Path(__file__).resolve().parent.parent
# The shared loopback server, a folder beside this library's own, named rather than searched
# for, so a same-named folder above the clone is never imported in its place. It goes second,
# just after the entry the aggregator puts first, and so ahead of every installed
# distribution: none of the same name can shadow it.
sys.path.insert(1, str(_TOOL_DIR.parent / "loopback_site"))

import csp_watch  # noqa: E402 - the tool folder, which the aggregator puts on the path
import loopback_site  # noqa: E402 - a folder beside the library's, via the insert above

#: The two pages the engines load.
_FIXTURE: Final = Path(__file__).resolve().parent / "fixture"
_POLICY_PAGE: Final = "/policy.html"
_BARE_PAGE: Final = "/bare.html"

#: The policy `policy.html` declares.
_DECLARED: Final = "default-src 'self'"

#: Playwright's own variable naming where its engine builds are.
_BROWSERS_ENV: Final = "PLAYWRIGHT_BROWSERS_PATH"

#: Each platform's cache folder as Playwright finds it, where that variable names none: the
#: variable that names the cache folder, if any, and the path under the home folder it falls back
#: to. Playwright keeps its builds in `ms-playwright` inside it, and installs none on a platform
#: not listed.
_CACHE_ROOTS: Final[dict[str, tuple[str | None, tuple[str, ...]]]] = {
    "linux": ("XDG_CACHE_HOME", (".cache",)),
    "darwin": (None, ("Library", "Caches")),
    "win32": ("LOCALAPPDATA", ("AppData", "Local")),
}
_PLAYWRIGHT_CACHE_NAME: Final = "ms-playwright"

#: The installed Playwright package, inside which its hermetic mode keeps the builds.
_PLAYWRIGHT_PACKAGE: Final = Path(playwright.__file__).resolve().parent

#: The value of Playwright's builds variable that keeps the builds inside its own package.
_HERMETIC_BROWSERS: Final = "0"

#: The package folder of Playwright's driver, inside the installed package, and the folder in
#: it that hermetic mode installs the builds in.
_HERMETIC_DRIVER: Final = ("driver", "package")
_HERMETIC_FOLDER: Final = ".local-browsers"

#: The folder name each engine's headless build is installed under, before its revision number.
_BUILD_PREFIXES: Final = {
    "chromium": "chromium_headless_shell-",
    "firefox": "firefox-",
    "webkit": "webkit-",
}

#: Set to `1`, the module FAILS where a build is missing rather than skipping: a skip is a
#: passing run, so a run that must measure the engines sets it. Unset or empty is off, and any
#: other value is refused.
_REQUIRE_BROWSERS_ENV: Final = "PAGE_PROOFS_REQUIRE_BROWSERS"

#: What opens the line a skipping module writes to stderr.
_SKIP_NOTICE: Final = "live suite skipped: "

#: What installs the headless builds this module launches.
_INSTALL_COMMAND: Final = (
    "python -m playwright install --only-shell chromium firefox webkit"
)

#: Bound on each engine step, in seconds and in Playwright's milliseconds.
_TIMEOUT: Final = 30.0
_MS_PER_SECOND: Final = 1000

#: How often the log is read while waiting on it, in milliseconds. Each read waits in Playwright,
#: which is what lets the binding's calls be dispatched to the log.
_POLL_MS: Final = 50

#: How long the log must hold still before its count is taken. A violation is reported as a task
#: queued after the refusal, and then crosses the driver, so the count is read once it stops
#: changing, never at a fixed delay.
_QUIET_SECONDS: Final = 0.5


def _home_folder() -> Path | None:
    """The user's home folder, or None where the platform cannot name one."""
    try:
        return Path.home()
    except RuntimeError:
        return None


def _engine_builds(
    env: Mapping[str, str], platform: str, home: Path | None
) -> Path | None:
    """Where the engine builds are: Playwright's variable if set, else its own cache folder.

    None for a platform Playwright installs no builds on, and for a cache folder that falls back
    to a home folder there is none of.
    """
    named = env.get(_BROWSERS_ENV)
    if named:
        return Path(named)
    root = _CACHE_ROOTS.get(platform)
    if root is None:
        return None
    variable, under_home = root
    cache = env.get(variable) if variable is not None else None
    if cache:
        return Path(cache) / _PLAYWRIGHT_CACHE_NAME
    if home is None:
        return None
    return home.joinpath(*under_home, _PLAYWRIGHT_CACHE_NAME)


def _builds_required() -> bool:
    """Whether absent engine builds fail the module rather than skip it.

    Unset or empty is off and `1` is on. Any other value is refused, so a value meant as on is
    never read as off.
    """
    value = os.environ.get(_REQUIRE_BROWSERS_ENV, "")
    if value not in ("", "1"):
        raise RuntimeError(
            f"{_REQUIRE_BROWSERS_ENV} is {value!r}: set it to 1 to require the engine "
            "builds, or leave it unset or empty"
        )
    return value == "1"


def _missing_builds(
    builds: Path | None, package: Path
) -> tuple[Path | None, list[str]]:
    """Where the builds were looked for, and each engine with no headless build there.

    A `builds` of `0` is Playwright's hermetic mode, which keeps the builds inside its own
    package, `package`. Where that package lacks the driver folder the mode relies on, where the
    builds are cannot be known, so none is reported missing and the launch says what is wrong.
    """
    if builds is not None and str(builds) == _HERMETIC_BROWSERS:
        driver = package.joinpath(*_HERMETIC_DRIVER)
        if not driver.is_dir():
            return builds, []
        builds = driver / _HERMETIC_FOLDER
    return builds, _engines_missing(builds)


def _engines_missing(builds: Path | None) -> list[str]:
    """Each engine with no headless build in `builds`, every one where it is no folder."""
    if builds is None or not builds.is_dir():
        return list(_BUILD_PREFIXES)
    return [
        engine
        for engine, prefix in _BUILD_PREFIXES.items()
        if not any(builds.glob(f"{prefix}[0-9]*"))
    ]


class _Module:
    """What the module's tests share: the driver, the server, and the scratch folder."""

    stack = contextlib.ExitStack()
    playwright: Playwright
    base_url = ""
    scratch = Path()


def setUpModule() -> None:
    """Point Playwright at the builds, serve the fixture, start the driver.

    With an engine build missing, skip rather than start, naming where it looked and what
    installs it, in the skip and on stderr; with `_REQUIRE_BROWSERS_ENV` set to `1`, fail
    instead, since a gate reads a skipped module as a pass, with none of the three engines' proof
    run. A value of that variable that is neither on nor off fails whatever is installed.
    """
    required = _builds_required()
    builds = _engine_builds(os.environ, sys.platform, _home_folder())
    looked_in, missing = _missing_builds(builds, _PLAYWRIGHT_PACKAGE)
    if missing:
        reason = (
            f"the pinned engine builds are not installed (looked in {looked_in} for "
            f"{', '.join(missing)}); `{_INSTALL_COMMAND}` installs them"
        )
        if required:
            raise RuntimeError(reason)
        # unittest shows a skip's reason only when run verbosely, so a plain run would show a
        # bare count of skips: the line on stderr is what names the install command there.
        sys.stderr.write(f"{_SKIP_NOTICE}{reason}\n")
        raise unittest.SkipTest(reason)
    # All or nothing: no tearDownModule follows a setUpModule that raised.
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, {_BROWSERS_ENV: str(builds)}))
        _Module.base_url = stack.enter_context(loopback_site.serve_directory(_FIXTURE))
        _Module.scratch = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        _Module.playwright = stack.enter_context(sync_playwright())
        _Module.stack = stack.pop_all()


def tearDownModule() -> None:
    """Stop the driver, the server and the scratch folder; restore the environment."""
    _Module.stack.close()


def _launch(engine: str) -> Browser:
    """Launch `engine` headless; Firefox with its per-user data folders in the scratch folder."""
    browser_type = getattr(_Module.playwright, engine)
    if engine != "firefox":
        return browser_type.launch(timeout=_TIMEOUT * _MS_PER_SECOND)
    env = {
        **os.environ,
        "MOZ_APP_DATA": str(_Module.scratch / "firefox-app-data"),
        "MOZ_LOCAL_APP_DATA": str(_Module.scratch / "firefox-local-app-data"),
    }
    for folder in ("MOZ_APP_DATA", "MOZ_LOCAL_APP_DATA"):
        Path(env[folder]).mkdir(parents=True, exist_ok=True)
    return browser_type.launch(timeout=_TIMEOUT * _MS_PER_SECOND, env=env)


def _wait_until(page: Page, done: Callable[[], bool]) -> bool:
    """Read `done` every poll until it holds or the bound passes; whether it held."""
    deadline = time.monotonic() + _TIMEOUT
    while not done():
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(_POLL_MS)
    return True


def _settled(log: csp_watch.CspLog, page: Page) -> tuple[csp_watch.Violation, ...]:
    """The log once its count has held for the quiet window."""
    count, still_since = log.mark(), time.monotonic()

    def held() -> bool:
        nonlocal count, still_since
        if log.mark() != count:
            count, still_since = log.mark(), time.monotonic()
        return time.monotonic() - still_since >= _QUIET_SECONDS

    if not _wait_until(page, held):
        raise AssertionError(f"the log never held still for {_QUIET_SECONDS}s")
    return log.violations


class _EngineCase(unittest.TestCase):
    """The checks, run in the one engine a subclass names."""

    ENGINE: ClassVar[str]
    browser: ClassVar[Browser]

    @classmethod
    def setUpClass(cls) -> None:
        """Launch this class's engine once, for all its tests."""
        cls.browser = _launch(cls.ENGINE)

    @classmethod
    def tearDownClass(cls) -> None:
        """Close the engine."""
        cls.browser.close()

    def _open(self, *, scripts: bool) -> tuple[csp_watch.CspLog, Page]:
        """A fresh context with the collector installed, scripts on or off, and its page."""
        context = self.browser.new_context(java_script_enabled=scripts)
        self.addCleanup(context.close)
        context.set_default_timeout(_TIMEOUT * _MS_PER_SECOND)
        log = csp_watch.install(context)
        return log, context.new_page()

    def _load(self, page: Page, path: str) -> None:
        """Load `path` from the fixture, to its load event."""
        page.goto(
            f"{_Module.base_url}{path}",
            wait_until="load",
            timeout=_TIMEOUT * _MS_PER_SECOND,
        )

    def test_scripts_on_the_one_style_attribute_arrives(self) -> None:
        log, page = self._open(scripts=True)
        self._load(page, _POLICY_PAGE)
        self.assertEqual(
            [(found.directive, found.document) for found in _settled(log, page)],
            [("style-src-attr", _POLICY_PAGE)],
        )

    def test_scripts_off_nothing_arrives_through_the_binding(self) -> None:
        log, page = self._open(scripts=False)
        self._load(page, _POLICY_PAGE)
        self.assertEqual(_settled(log, page), ())

    def test_policy_of_reads_the_declared_policy_with_scripts_on(self) -> None:
        self._check_policy_of(scripts=True)

    def test_policy_of_reads_the_declared_policy_with_scripts_off(self) -> None:
        self._check_policy_of(scripts=False)

    def test_a_plant_on_a_page_with_no_policy_is_caught(self) -> None:
        log, page = self._open(scripts=True)
        self._load(page, _BARE_PAGE)
        self.assertEqual(_settled(log, page), ())
        since = log.mark()
        csp_watch.plant(page)
        self.assertTrue(
            _wait_until(page, lambda: csp_watch.plant_caught(log, since)),
            f"no violation carrying {csp_watch.PLANT_SAMPLE!r} arrived",
        )

    def _check_policy_of(self, *, scripts: bool) -> None:
        """`policy_of` reads the policy page's content, and None on the bare page."""
        _, page = self._open(scripts=scripts)
        self._load(page, _POLICY_PAGE)
        self.assertEqual(csp_watch.policy_of(page), _DECLARED)
        self._load(page, _BARE_PAGE)
        self.assertIsNone(csp_watch.policy_of(page))


class ChromiumTest(_EngineCase):
    """The checks in Chromium."""

    ENGINE = "chromium"


class FirefoxTest(_EngineCase):
    """The checks in Firefox."""

    ENGINE = "firefox"


class WebkitTest(_EngineCase):
    """The checks in WebKit."""

    ENGINE = "webkit"


def load_tests(
    loader: unittest.TestLoader, standard_tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    """unittest's own hook: the three engines' classes, never the shared base on its own."""
    suite = unittest.TestSuite()
    for case in (ChromiumTest, FirefoxTest, WebkitTest):
        suite.addTests(loader.loadTestsFromTestCase(case))
    return suite
