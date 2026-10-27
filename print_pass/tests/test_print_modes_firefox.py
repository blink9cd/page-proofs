"""Grouped suite: a make run under `--engine firefox` -- its refusals, its engine, its report.

A Firefox run takes its own branch before Playwright is looked for: off macOS it refuses
first, with nothing looked up or started; without geckodriver it refuses naming how to
install it and Firefox; otherwise it serves the site and hands the Firefox engine the served
origin, a watchdog whose expiry is the run guard's, and the settings. An engine error is
exit 2 with no report; Firefox missing is one, naming how to install it. A run the watchdog
abandons writes no report and names the scratch folder it left. And no form of the scratch
folder's path reaches `report.json`, through the whole path from a state's log to the
written report. A Playwright run's refusal for a missing Playwright names what its own
engine needs.

`gecko_engine.run_targets` is replaced by a fake; where a case needs a real state, the fake
runs `gecko_state.run_state` against `_gecko_state_fixtures`' scripted session. Nothing here
starts geckodriver or Firefox.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import dataclasses
import functools
import io
import sys
import unittest
import urllib.request
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Final
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# ...and the tool's own folder, where the CLI the shared run fixtures import is found.
sys.path.insert(0, str(_TOOL_DIR))

import gecko_engine  # noqa: E402 - the tool's private src/, via the insert above
import gecko_launch  # noqa: E402 - as above
import gecko_state  # noqa: E402 - as above
import print_modes  # noqa: E402 - as above
import print_preflight  # noqa: E402 - as above
from _gecko_state_fixtures import (  # noqa: E402 - beside this module
    NO_FIREFOX,
    FakeClient,
    FakeDriver,
    FakeSession,
    webdriver_error,
)
from _print_engine_fixtures import VIEWPORT  # noqa: E402 - as above
from _print_pass_fixtures import PAGE, RunCase  # noqa: E402 - as above
from _print_playwright_fixtures import CallLog, FakeBounder, FakeClock  # noqa: E402
from print_plan import MakePlan  # noqa: E402 - the tool's private src/
from print_record import EngineError, EngineSettings, StateRecord  # noqa: E402
from print_targets import Action, TargetsFile  # noqa: E402 - as above

#: Where the fake geckodriver is found.
_EXECUTABLE: Final = "/opt/fake/bin/geckodriver"

#: The DPI each run reads at: small, since no PDF is read here.
_DPI: Final = 72


class _Abandoned(Exception):
    """Stands in for the watchdog's `os._exit`: the run ends where it is."""


class _KillLogged:
    """A driver as the run guard sees it: something to kill, each kill logged."""

    def __init__(self, log: CallLog) -> None:
        """Log each kill into `log`."""
        self.log = log

    def kill(self) -> None:
        """Log the kill."""
        self.log.append(("kill",))


class _FirefoxCase(RunCase):
    """A make run under `--engine firefox`, its output caught, its engine faked."""

    def plan(self, engine: str = "firefox") -> MakePlan:
        """The plan the CLI would hand over for `--engine <engine> --page /`."""
        settings = EngineSettings(
            engine=engine,  # type: ignore[arg-type]
            backgrounds="off",
            header_footer=False,
            viewport=VIEWPORT,
            timeout_s=5.0,
            pdf_dir=self.out / "pdfs",
        )
        return MakePlan(
            out=self.out,
            dpi=_DPI,
            serve=self.site,
            targets=None,
            pages=("/",),
            browsers=None,
            settings=settings,
        )

    def make(
        self,
        engine: Callable[..., tuple[StateRecord, ...]] | None = None,
        *,
        platform: str = "darwin",
        geckodriver: str | None = _EXECUTABLE,
        plan: MakePlan | None = None,
    ) -> tuple[int, str, str]:
        """Run the make run; its code, stdout and stderr. Playwright is never to be loaded.

        What it wrote is also kept on the case, for a run that ends by raising.
        """
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.find = mock.Mock(return_value=geckodriver)
        never = mock.Mock(side_effect=AssertionError("a Firefox run loaded Playwright"))
        with (
            mock.patch.object(gecko_engine, "run_targets", engine or self.unreached),
            mock.patch.object(gecko_launch, "find_geckodriver", self.find),
            mock.patch.object(print_preflight, "load_playwright", never),
            redirect_stdout(self.stdout),
            redirect_stderr(self.stderr),
        ):
            code = print_modes.run_make(plan or self.plan(), platform=platform)
        return code, self.stdout.getvalue(), self.stderr.getvalue()

    def unreached(self, *args: object, **kwargs: object) -> tuple[StateRecord, ...]:
        """An engine no case should reach."""
        raise AssertionError("the Firefox engine was reached")


class RefusalTests(_FirefoxCase):
    """The two refusals before anything starts, each exit 2, with nothing made."""

    def test_off_macos_refuses_before_looking_anything_up(self) -> None:
        # A targets file that is refused too, so a run that loaded it before the platform
        # check would say that instead.
        plan = dataclasses.replace(
            self.plan(), targets=self.root / "missing.toml", pages=()
        )
        code, stdout, stderr = self.make(platform="linux", plan=plan)
        self.find.assert_not_called()
        self.assertEqual(code, 2)
        self.assertIn("the Firefox engine runs on macOS only", stderr)
        self.assertNotIn("missing.toml", stderr)
        self.assertFalse(self.out.exists())
        self.assertEqual(stdout, "")

    def test_no_geckodriver_names_how_to_install_it_and_firefox(self) -> None:
        code, _, stderr = self.make(geckodriver=None)
        self.assertEqual(code, 2)
        self.assertIn("geckodriver was not found on PATH", stderr)
        self.assertIn("brew install geckodriver", stderr)
        self.assertIn("brew install --cask firefox", stderr)
        self.assertIn("Mozilla's installer", stderr)
        self.assertFalse(self.out.exists())

    def test_a_targets_file_that_does_not_load_is_refused(self) -> None:
        plan = dataclasses.replace(
            self.plan(), targets=self.root / "missing.toml", pages=()
        )
        code, _, _ = self.make(plan=plan)
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())


class EngineTests(_FirefoxCase):
    """What the engine is handed, and what its error becomes."""

    def test_the_engine_is_handed_the_served_site_a_guarded_watchdog_and_the_plan(
        self,
    ) -> None:
        seen: dict[str, object] = {}
        made: list[Callable[[str], None]] = []

        def engine(
            base_url: str,
            targets: TargetsFile,
            settings: EngineSettings,
            **kwargs: object,
        ) -> tuple[StateRecord, ...]:
            with urllib.request.urlopen(
                f"{base_url}/index.html", timeout=10
            ) as response:
                seen["served"] = response.read().decode("utf-8")
            seen.update(base_url=base_url, targets=targets, settings=settings, **kwargs)
            return ()

        def watchdog(on_expire: Callable[[str], None]) -> object:
            made.append(on_expire)
            return "the watchdog"

        with mock.patch.object(print_modes, "Watchdog", watchdog):
            code, stdout, _ = self.make(engine)
        self.assertEqual(code, 0)
        self.assertTrue(str(seen["base_url"]).startswith("http://127.0.0.1:"))
        self.assertEqual(seen["served"], PAGE)
        self.assertEqual(seen["executable"], _EXECUTABLE)
        self.assertEqual(seen["bounder"], "the watchdog")
        guard = seen["guard"]
        assert isinstance(guard, gecko_engine.RunGuard)
        self.assertEqual(made, [guard.on_expire])
        self.assertEqual(seen["settings"], self.plan().settings)
        self.assertTrue((self.out / "pdfs").is_dir())
        self.assertIn(f"geckodriver: {_EXECUTABLE}", stdout)
        self.assertNotIn("engine builds", stdout)

    def test_an_engine_error_is_exit_two_without_a_report(self) -> None:
        failing = mock.Mock(side_effect=EngineError("home/default: print failed: gone"))
        code, _, stderr = self.make(failing)
        self.assertEqual(code, 2)
        self.assertIn("home/default: print failed: gone", stderr)
        self.assertFalse((self.out / "report.json").exists())

    def test_firefox_missing_is_exit_two_naming_how_to_install_it(self) -> None:
        missing = webdriver_error("new session", "session not created", NO_FIREFOX)
        code, _, stderr = self.make(_running_a_state(self.root, failure=missing))
        self.assertEqual(code, 2)
        self.assertIn("Firefox did not start", stderr)
        self.assertIn("brew install --cask firefox", stderr)
        self.assertFalse((self.out / "report.json").exists())


class AbandonTests(_FirefoxCase):
    """A run the watchdog abandons writes no report, and names the scratch it left."""

    def test_an_abandoned_run_writes_no_report_and_names_the_scratch(self) -> None:
        root = self.root / "scratch-of-the-run"
        log: CallLog = []

        def abandon(what: str) -> None:
            raise _Abandoned(what)

        def engine(
            *args: object, guard: gecko_engine.RunGuard, **kwargs: object
        ) -> tuple[()]:
            guard.watch(_KillLogged(log), root)
            guard.on_expire("webdriver print")
            raise AssertionError("abandon returned")

        guard_class = functools.partial(gecko_engine.RunGuard, abandon=abandon)
        with (
            mock.patch.object(gecko_engine, "RunGuard", guard_class),
            self.assertRaises(_Abandoned),
        ):
            self.make(engine)
        self.assertFalse((self.out / "report.json").exists())
        self.assertEqual(log, [("kill",)])
        self.assertIn(
            f"the scratch folder was left in place: {root}", self.stderr.getvalue()
        )


class ScrubTests(_FirefoxCase):
    """No form of the scratch folder's path reaches the report, through the whole path."""

    def test_the_scratch_root_never_reaches_report_json(self) -> None:
        # The run is handed a symlink to the real root, so the form it was given and the
        # resolved form differ on every platform; a page-error line names each.
        real = self.root / "real-scratch"
        real.mkdir()
        given = self.root / "linked-scratch"
        given.symlink_to(real, target_is_directory=True)
        forms = (str(given), str(given.resolve()))
        self.assertEqual(forms[1], str(real))
        lines = (
            f'1791248040499\tmozrunner::runner\tINFO\tRunning command: "-profile" '
            f'"{forms[0]}/profiles/rust_mozprofileA1"\n'
            f"JavaScript error: {{base}}/app.js, line 1: Error: no {forms[0]}/home\n"
            f"JavaScript error: {{base}}/app.js, line 2: Error: no {forms[1]}/home\n"
        )
        code, stdout, stderr = self.make(_running_a_state(given, lines=lines))
        self.assertEqual(code, 1, stderr)
        report = (self.out / "report.json").read_text(encoding="utf-8")
        for form in forms:
            with self.subTest(form=form):
                self.assertNotIn(form, report)
                self.assertNotIn(form, stdout)
        states = self.report()["states"]
        assert isinstance(states, list)
        (recorded,) = states
        self.assertEqual(
            recorded["network"]["page_errors"],
            ["Error: no <scratch>/home"] * len(forms),
        )


def _running_a_state(
    root: Path, *, failure: Exception | None = None, lines: str = ""
) -> Callable[..., tuple[StateRecord, ...]]:
    """An engine running the run's one state for real, against a scripted session.

    The state's one action finds nothing to wait for, so it stops before any print, with
    the log holding `lines` (`{base}` the served origin) by then; `root` is the scratch's,
    and `failure` is New Session's.
    """

    def engine(
        base_url: str, targets: TargetsFile, settings: EngineSettings, **kwargs: object
    ) -> tuple[StateRecord, ...]:
        log: CallLog = []
        driver = FakeDriver(log)
        session = FakeSession(
            log,
            visible={"#never": [False]},
            timing_reply=[[[f"{base_url}/", 200]], []],
            on_navigate=lambda url: driver.write(lines.format(base=base_url)),
        )
        (target,) = targets.targets
        acting = dataclasses.replace(
            target.states[0], actions=(Action("wait_for", "#never", None),)
        )
        job = gecko_state.StateJob(
            target=target,
            state=acting,
            settings=dataclasses.replace(settings, timeout_s=0.25),
            base_url=base_url,
            dead_port=40001,
            expected_hosts=(),
            scratch_root=root,
            bounder=FakeBounder(log),
            clock=FakeClock(0.1),
            sleep=lambda seconds: None,
        )
        return (gecko_state.run_state(FakeClient(session, failure), driver, job),)

    return engine


class PlaywrightMissingTests(_FirefoxCase):
    """A Playwright run without Playwright names what its own engine needs."""

    def missing(self, engine: str) -> str:
        """Run `engine` with Playwright not importable; its stderr."""
        stderr = io.StringIO()
        with (
            mock.patch.object(print_preflight, "load_playwright", return_value=None),
            redirect_stdout(io.StringIO()),
            redirect_stderr(stderr),
        ):
            code = print_modes.run_make(self.plan(engine))
        self.assertEqual(code, 2)
        return stderr.getvalue()

    def test_the_shell_needs_the_pinned_builds(self) -> None:
        text = self.missing("shell")
        self.assertIn("Playwright is not installed", text)
        self.assertIn("python -m playwright install --only-shell chromium", text)

    def test_chrome_needs_installed_chrome_and_never_the_pinned_builds(self) -> None:
        text = self.missing("chrome")
        self.assertIn("Playwright is not installed", text)
        self.assertIn("installed Google Chrome", text)
        self.assertNotIn("pinned", text)
        self.assertNotIn("--only-shell", text)


if __name__ == "__main__":
    unittest.main()
