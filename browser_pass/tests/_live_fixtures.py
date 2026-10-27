"""Shared fixtures for browser_pass's live suites: the pinned engines, and the sample site.

`test_pass_live.py` drives the page-load detectors, `test_pass_late_live.py` the late-script
load and `test_pass_late_motion_live.py` its stilling of motion, `test_pass_shadow_motion_live.py`
both settles' stilling of motion inside shadow trees, `test_pass_states_live.py` a page reached in
a state, `test_pass_csp_live.py` the CSP detector, `test_pass_extension_live.py` a page measured
with an extension and `test_pass_loopback_live.py` one under the egress guard alone, all through the REAL Chromium, Firefox and WebKit builds against
`samples/site` or pages of their own, served on loopback. What points
Playwright at those builds and serves the sample lives here once, and so do the two things that
keep the launches few: `SharedEngines`, one browser per engine for the tests of a scope, and
each module's launch budget, which `LiveSite` enforces.

Without the engine builds a live module skips at setup, naming the install command in the skip
and in one line on stderr, so the suites run on a machine that has Playwright but no builds. With
`PAGE_PROOFS_REQUIRE_BROWSERS=1` it fails instead (`REQUIRE_BROWSERS_ENV`), so a run that must
measure the engines can never pass with them unmeasured.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import unittest
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from unittest import mock

import playwright
from playwright.sync_api import Browser, Playwright, sync_playwright

TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL_DIR / "src"))
# The shared runner and loopback server, each a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place, and
# put just after src/, ahead of every installed distribution, so none of the same name can
# shadow it.
sys.path.insert(1, str(TOOL_DIR.parent / "bounded_run"))
sys.path.insert(1, str(TOOL_DIR.parent / "loopback_site"))

import loopback_site  # noqa: E402 - a folder beside the tool's, via the insert above
import pass_engine  # noqa: E402 - the tool's private src/, via the insert above
import pass_facts  # noqa: E402 - the tool's private src/, via the insert above
import pass_late_load  # noqa: E402 - the tool's private src/, via the insert above
import pass_probe  # noqa: E402 - the tool's private src/, via the insert above
from pass_extension import NO_ISOLATION, Isolation  # noqa: E402 - the tool's private src/
from pass_report import RenderingResult  # noqa: E402 - the tool's private src/
from pass_targets import Rendering, State  # noqa: E402 - the tool's private src/
from pass_watchdog import Bounder, Watchdog  # noqa: E402 - the tool's private src/

SAMPLE_SITE = TOOL_DIR / "samples" / "site"

#: Bound on each page step in the live tests.
TIMEOUT = 30.0

#: Set to `1`, a live module FAILS where a pinned build it needs is not installed, rather than
#: skipping: a skip is a passing run, so a run that must measure the engines sets it. Unset or
#: empty is off, and any other value is refused.
REQUIRE_BROWSERS_ENV = "PAGE_PROOFS_REQUIRE_BROWSERS"

#: What opens the line a skipping live module writes to stderr.
SKIP_NOTICE = "live suite skipped: "

#: The installed Playwright package, inside which its hermetic mode keeps the builds.
PLAYWRIGHT_PACKAGE = Path(playwright.__file__).resolve().parent

#: The value of Playwright's builds variable that keeps the builds inside its own package.
HERMETIC_BROWSERS = "0"

#: The package folder of Playwright's driver, inside the installed package, and the folder in
#: it that hermetic mode installs the builds in.
HERMETIC_DRIVER = ("driver", "package")
HERMETIC_FOLDER = ".local-browsers"

#: What installs the headless builds the live suites launch.
INSTALL_COMMAND = (
    "python3.12 -m playwright install --only-shell chromium firefox webkit"
)

#: The folder name each engine's headless build is installed under, before its revision number.
BUILD_PREFIXES = {
    "chromium": "chromium_headless_shell-",
    "firefox": "firefox-",
    "webkit": "webkit-",
}

#: The full Chromium build an extension needs, by the same folder-name rule.
FULL_CHROMIUM = {"chromium": "chromium-"}


def builds_required() -> bool:
    """Whether absent engine builds fail a live module rather than skip it.

    Unset or empty is off and `1` is on. Any other value is refused, so a value meant as on is
    never read as off.
    """
    value = os.environ.get(REQUIRE_BROWSERS_ENV, "")
    if value not in ("", "1"):
        raise RuntimeError(
            f"{REQUIRE_BROWSERS_ENV} is {value!r}: set it to 1 to require the engine "
            "builds, or leave it unset or empty"
        )
    return value == "1"


def engines_missing(
    browsers: Path | None, prefixes: Mapping[str, str] = BUILD_PREFIXES
) -> list[str]:
    """Each engine with no build named by `prefixes` in `browsers`, all where it is no folder."""
    if browsers is None or not browsers.is_dir():
        return list(prefixes)
    return [
        engine
        for engine, prefix in prefixes.items()
        if not any(browsers.glob(f"{prefix}[0-9]*"))
    ]


def missing_builds(
    browsers: Path | None,
    package: Path,
    prefixes: Mapping[str, str] = BUILD_PREFIXES,
) -> tuple[Path | None, list[str]]:
    """Where the builds were looked for, and each engine of `prefixes` with no build there.

    A `browsers` of `0` is Playwright's hermetic mode, which keeps the builds inside its own
    package, `package`. Where that package lacks the driver folder the mode relies on, where the
    builds are cannot be known, so none is reported missing and the launch says what is wrong.
    """
    if browsers is not None and str(browsers) == HERMETIC_BROWSERS:
        driver = package.joinpath(*HERMETIC_DRIVER)
        if not driver.is_dir():
            return browsers, []
        browsers = driver / HERMETIC_FOLDER
    return browsers, engines_missing(browsers, prefixes)


def absent_builds(missing: str, command: str) -> Exception:
    """What a live module raises where `missing` is so: a skip naming `command`, or a failure.

    The failure is for a run with `REQUIRE_BROWSERS_ENV` set; either way the one line says what
    installs what is missing, and a skip also writes that line to stderr.
    """
    reason = f"{missing}; `{command}` installs them"
    if builds_required():
        return RuntimeError(reason)
    # unittest shows a skip's reason only when run verbosely, so a plain run would show a bare
    # count of skips: the line on stderr is what names the install command there.
    sys.stderr.write(f"{SKIP_NOTICE}{reason}\n")
    return unittest.SkipTest(reason)


#: The settle window the live suites render under, in place of production's. That one guards
#: what the stilling of animations before each poll cannot reach -- a page still building
#: itself, and motion a script drives -- and the pages these suites serve do neither for long:
#: the sample site's one timer fires once, at 0 ms, the page-load settle stills its CSS motion,
#: and the late-script load stills the motion sample's too, which moves only `transform`. So
#: three polls still read a page that has stopped, and the restless page, which a script
#: changes every 50 ms, still never settles. Every rendering pays the window once, so it is most
#: of what a run waits on.
TEST_SETTLE_QUIET_SECONDS = 0.3

#: A page whose layout changes every 50ms forever.
RESTLESS_PAGE = """<!doctype html><html lang="en"><head><title>restless</title></head>
<body><div id="grow"></div><script>
let size = 1;
setInterval(() => { size = (size % 200) + 1; document.getElementById("grow").style.height = size + "px"; }, 50);
</script></body></html>
"""


def scratch(case: unittest.TestCase) -> Path:
    """A throwaway folder removed when `case` ends."""
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    return Path(folder.name)


class LaunchCounter:
    """Stands in for `pass_engine.launch`: calls straight through, counting launches per engine.

    A real function rather than a mock, so a call spelling the engine either way is counted,
    and whatever the real launch returns or raises reaches the caller unchanged.
    """

    def __init__(self, launch: Callable[..., object]) -> None:
        """Count nothing yet; `launch` is the real one, captured before anything patches it."""
        self._launch = launch
        self.counts: Counter[str] = Counter()

    def __call__(self, playwright: object, engine: str, **options: object) -> object:
        """Count one launch of `engine`, then launch it for real."""
        self.counts[engine] += 1
        return self._launch(playwright, engine, **options)


def over_budget(
    launched: Mapping[str, int], budget: Mapping[str, int]
) -> dict[str, int]:
    """Each engine launched more often than `budget` allows, with its count; unlisted means none."""
    return {
        engine: count
        for engine, count in launched.items()
        if count > budget.get(engine, 0)
    }


class LiveSite:
    """The pinned engine builds pointed at, and the sample site served, for one live module.

    It also holds the module to its launch budget. An engine launch -- a driver start and a
    browser start -- is the dearest thing these suites do, and a test that quietly launches
    its own browser costs every test run after it, so every launch in the module is counted
    and `stop` fails the module when any engine went past the number it declared.
    """

    def __init__(self, *, launch_budget: Mapping[str, int]) -> None:
        """Nothing started yet: `start` belongs in the module's `setUpModule`."""
        self._stack = contextlib.ExitStack()
        self.base_url = ""
        self._budget = dict(launch_budget)
        self._launches = LaunchCounter(pass_engine.launch)

    def start(self) -> None:
        """Point Playwright at the builds, serve the sample, set the test settle window.

        The window is `TEST_SETTLE_QUIET_SECONDS`, restored by `stop`. With a pinned build
        missing, skip rather than start, or fail with `REQUIRE_BROWSERS_ENV` set. A value of that
        variable that is neither on nor off fails the start whatever is installed.
        """
        builds_required()
        browsers = pass_facts.default_browsers_path(
            os.environ, platform=sys.platform, home=pass_facts.home_folder()
        )
        looked_in, missing = missing_builds(browsers, PLAYWRIGHT_PACKAGE)
        if missing:
            raise absent_builds(
                f"the pinned engine builds are not installed (looked in {looked_in} for "
                f"{', '.join(missing)})",
                INSTALL_COMMAND,
            )
        # All or nothing: no tearDownModule follows a setUpModule that raised, so a step that
        # fails must undo the ones before it rather than leave them for later modules.
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                mock.patch.dict(os.environ, {pass_facts.BROWSERS_ENV: str(browsers)})
            )
            self.base_url = stack.enter_context(
                loopback_site.serve_directory(SAMPLE_SITE)
            )
            stack.enter_context(
                mock.patch.object(pass_engine, "launch", self._launches)
            )
            stack.enter_context(
                mock.patch.object(
                    pass_probe, "SETTLE_QUIET_SECONDS", TEST_SETTLE_QUIET_SECONDS
                )
            )
            self._stack = stack.pop_all()

    def stop(self) -> None:
        """Stop the server and restore the environment, then hold the module to its budget."""
        self._stack.close()
        over = over_budget(self._launches.counts, self._budget)
        if over:
            raise AssertionError(
                f"engine launches past this module's budget {self._budget}: {over}"
            )

    def run(
        self,
        case: unittest.TestCase,
        renderings: Sequence[Rendering],
        *,
        base_url: str | None = None,
        prove: bool = False,
        timeout: float = TIMEOUT,
        isolation: Isolation = NO_ISOLATION,
        states: Sequence[State] = (),
    ) -> tuple[dict[str, str], list[RenderingResult]]:
        """Run `renderings` through the real engines, collecting progress as it arrives.

        `isolation` is what each engine is launched with (`pass_extension`), and `states` the
        states a rendering may name.
        """
        seen: list[RenderingResult] = []
        versions, results = pass_engine.run_matrix(
            renderings,
            base_url or self.base_url,
            timeout=timeout,
            prove=prove,
            scratch=scratch(case),
            progress=seen.append,
            isolation=isolation,
            states=states,
        )
        case.assertEqual(seen, results)
        return versions, results


class SharedEngines:
    """One Playwright driver and one browser per engine, shared by the tests of one scope.

    The tool renders every page of an engine in one browser, each rendering in a fresh context
    (`pass_engine.run_matrix`), so tests sharing a browser that way measure what the tool
    measures. What sharing saves is a driver start and an engine launch per test -- the work a
    loaded machine slows most. A browser that has died is launched again on the next request,
    so one crashed engine fails its own test rather than every test after it (and shows up
    again as a launch past the module's budget).

    Playwright refuses a second sync driver on a thread while one runs, so nothing in a scope
    holding a started one may call `pass_engine.run_matrix`, or `LiveSite.run`.
    """

    def __init__(self, *, bounder: Bounder | None = None) -> None:
        """Nothing started yet: `start` goes after `LiveSite.start` has pointed at the builds.

        `bounder` bounds each rendering and each close exactly as `run_matrix` does (by default,
        the watchdog that abandons the run), since Playwright gives neither a timeout.
        """
        self._stack = contextlib.ExitStack()
        self._playwright: Playwright
        self._scratch = Path()
        self._browsers: dict[str, Browser] = {}
        self._bounder = bounder or Watchdog()

    def start(self) -> None:
        """Start the driver, with the scratch folder Firefox's per-user data is sent to."""
        with contextlib.ExitStack() as stack:
            self._scratch = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            self._playwright = stack.enter_context(sync_playwright())
            self._stack = stack.pop_all()

    def browser(self, engine: str) -> Browser:
        """This scope's browser for `engine`: launched on first use, and again if it has died."""
        browser = self._browsers.get(engine)
        if browser is None or not browser.is_connected():
            launched = pass_engine.launch(
                self._playwright, engine, scratch=self._scratch, timeout=TIMEOUT
            )
            # Launched with no isolation, so a browser: never one launch per context.
            assert isinstance(launched, Browser), launched
            browser = self._browsers[engine] = launched
        return browser

    def render(
        self,
        rendering: Rendering,
        base_url: str,
        *,
        timeout: float = TIMEOUT,
        prove: bool = False,
        state: State | None = None,
        require_csp: bool = False,
        missing: bool = False,
    ) -> RenderingResult:
        """`pass_engine.render` in this scope's browser for the rendering's engine, bounded.

        `state` is the one `rendering` names, reached before it is measured; `missing` says its
        page has no file, so its own document's 404 is expected.
        """
        browser = self.browser(rendering.engine)
        with self._bounder.bound(
            pass_engine.rendering_bound(timeout, state), f"rendering {rendering.label}"
        ):
            return pass_engine.render(
                browser,
                rendering,
                base_url,
                timeout=timeout,
                prove=prove,
                state=state,
                require_csp=require_csp,
                missing=missing,
            )

    def stop(self) -> None:
        """Close every browser while the driver still runs, then the driver and the scratch."""
        try:
            for engine, browser in self._browsers.items():
                with self._bounder.bound(
                    pass_engine.CLOSE_BOUND_SECONDS, f"closing {engine}"
                ):
                    browser.close()
        finally:
            self._browsers.clear()
            self._stack.close()


def late_load(
    engines: SharedEngines,
    rendering: Rendering,
    base_url: str,
    *,
    timeout: float = TIMEOUT,
    prove: bool = False,
) -> pass_late_load.LateOutcome:
    """The late-script load alone, in `engines`' browser for `rendering`'s engine."""
    context = pass_engine.open_context(engines.browser(rendering.engine), rendering)
    try:
        return pass_late_load.measure_late_shift(
            context, rendering, base_url, timeout=timeout, prove=prove
        )
    finally:
        # The shared browser outlives the test, so its context must not.
        context.close()


def served_late_load(
    case: unittest.TestCase,
    engines: SharedEngines,
    files: Mapping[str, str],
    *,
    timeout: float,
    prove: bool = False,
) -> pass_late_load.LateOutcome:
    """The late-script load in Chromium of the first of `files`, served with the rest.

    Each is written under its name to `case`'s scratch folder, which is served alone.
    """
    site = scratch(case)
    for name, text in files.items():
        (site / name).write_text(text, encoding="utf-8", newline="\n")
    page = next(iter(files))
    with loopback_site.serve_directory(site) as base_url:
        return late_load(
            engines,
            Rendering("chromium", f"/{page}", 390, 844, "light", "on"),
            base_url,
            timeout=timeout,
            prove=prove,
        )
