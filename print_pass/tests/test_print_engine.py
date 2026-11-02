"""Suite: the engine's run over a targets file (launching, the record of a state, closing).

Everything runs against the fake Playwright in `_print_playwright_fixtures`, whose one log
shows what the engine called and in what order. What a single state does between load and
print is the sibling `test_print_engine_state.py`'s, and what its page asks the network for
`test_print_engine_network.py`'s; this suite holds the run around it: which browser each state
gets and when it is launched, the settings a targets file overrides, the record of a plain
state, and that every browser closes however the run ends.
"""

from __future__ import annotations

import math
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Literal

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_engine  # noqa: E402 - the tool's private src/, via the insert above
import print_probe  # noqa: E402 - as above
from _print_engine_fixtures import (  # noqa: E402 - beside this module
    BASE_URL,
    BLINK_SETTINGS_SWITCH,
    TIMED_BOUND_S,
    TIMEOUT_MS,
    TIMEOUT_S,
    VIEWPORT,
    FakeRun,
    engine_settings,
    state,
    target,
    targets_file,
    timed_bound,
)
from _print_playwright_fixtures import (  # noqa: E402 - as above
    CHROME_VERSION,
    PLACEHOLDER_PDF,
    SHELL_VERSION,
    FakeScript,
    call,
    probe_dict,
)
from print_targets import Action, State, Viewport  # noqa: E402 - the tool's private src/

_FORCE_DARK = "--force-dark-mode"

#: The switch every launch carries so that no hostname resolves: spelled out, not imported,
#: so a typo in the engine's own copy fails here. It excludes the address the site is served
#: on, never `localhost`: Chromium applies the rules to an IP literal too.
_NO_RESOLVING = "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1"

#: Each launch a run can make, exactly, with the switches every launch carries: the shell,
#: installed Chrome, and Chrome forced dark, its own switch first.
_ARGS = [_NO_RESOLVING, BLINK_SETTINGS_SWITCH]
_SHELL_LAUNCH = call("chromium.launch", args=_ARGS, timeout=TIMEOUT_MS)
_CHROME_LAUNCH = call(
    "chromium.launch", args=_ARGS, channel="chrome", timeout=TIMEOUT_MS
)
_DARK_LAUNCH = call(
    "chromium.launch", args=[_FORCE_DARK, *_ARGS], channel="chrome", timeout=TIMEOUT_MS
)

#: Every Playwright call the engine makes, across every kind of state it can print.
_EVERY_CALL = frozenset(
    """
    chromium.launch browser.new_context browser.close context.expose_binding
    context.add_init_script context.set_default_timeout context.route
    context.route_web_socket context.new_page context.close page.on page.goto
    route.abort page.click page.wait_for_selector page.wait_for_timeout page.evaluate
    page.pdf
    """.split()
)


class EngineTestCase(unittest.TestCase):
    """A fresh folder for PDFs per case."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.pdf_dir = Path(folder.name) / "pdfs"

    def fake(self, script: FakeScript | None = None) -> FakeRun:
        return FakeRun(self.pdf_dir, script)


class DefaultStateTests(EngineTestCase):
    """One target in its default state: what is called, and what is recorded."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_run = self.fake()
        (self.record,) = self.fake_run.run()

    def test_one_pdf_with_backgrounds_off(self) -> None:
        path = self.pdf_dir / "home--default--bg-off.pdf"
        self.assertEqual(
            self.record.pdfs,
            (
                print_engine.MadePdf(
                    backgrounds=False,
                    path=path,
                    margin_inches=print_engine.MARGIN_INCHES,
                ),
            ),
        )
        self.assertEqual(path.read_bytes(), PLACEHOLDER_PDF)

    def test_the_page_is_loaded_once_from_the_base_url(self) -> None:
        self.assertEqual(
            self.fake_run.named("page.goto"),
            [call("page.goto", BASE_URL + "/", wait_until="load", timeout=TIMEOUT_MS)],
        )

    def test_the_shell_is_launched_with_a_timeout_and_every_switch(self) -> None:
        self.assertEqual(self.fake_run.named("chromium.launch"), [_SHELL_LAUNCH])

    def test_the_context_is_opened_with_the_states_options(self) -> None:
        (context,) = self.fake_run.playwright.chromium.browsers[0].contexts
        self.assertEqual(
            context.options,
            print_engine.context_options(state(), VIEWPORT, BASE_URL),
        )
        self.assertIn(
            call("context.set_default_timeout", TIMEOUT_MS), self.fake_run.calls
        )

    def test_the_record(self) -> None:
        self.assertEqual(
            self.record,
            print_engine.StateRecord(
                target="home",
                state="default",
                page="/",
                engine="shell",
                browser_version=SHELL_VERSION,
                viewport=VIEWPORT,
                color_scheme="native",
                color_scheme_emulated=False,
                reduced_motion="native",
                reduced_motion_emulated=False,
                storage=(),
                actions=(),
                print_at_ms=None,
                page_width=None,
                margin_inches=print_engine.MARGIN_INCHES,
                probe=print_probe.parse_probe_result(probe_dict()),
                network=print_engine.NetworkRecord(
                    failed=(),
                    http_errors=(),
                    blocked=(),
                    page_errors=(),
                    csp_violations=(),
                ),
                # The clock reads 0 at navigation, 1 s after load, 2 s at the print.
                load_ms=1000,
                printed_at_ms=2000,
                pdfs=self.record.pdfs,
                problems=(),
            ),
        )

    def test_the_context_and_browser_close_in_the_watchdog(self) -> None:
        self.assertEqual(self.fake_run.named("context.close"), [call("context.close")])
        self.assertEqual(
            self.fake_run.named("browser.close"), [call("browser.close", SHELL_VERSION)]
        )
        self.assertEqual(self.fake_run.unbounded(), [])

    def test_each_close_is_bounded_at_twice_the_timeout(self) -> None:
        # A close takes no timeout, and installed Chrome's has taken just over 30 s under
        # load: a bound at the timeout would abandon a run at its very end.
        closed = self.fake_run.around(call("context.close"))
        self.assertEqual(closed, timed_bound("context.close home/default"))
        closed = self.fake_run.around(call("browser.close", SHELL_VERSION))
        self.assertEqual(closed, timed_bound("browser.close shell"))

    def test_a_bound_is_the_timeout_or_twice_it_for_a_call_handed_it(self) -> None:
        bounds = self.fake_run.named("bound")
        seconds = {entry[1]: entry[2] for entry in bounds}
        closes = {"context.close home/default", "browser.close shell"}
        timed = {"chromium.launch shell", "page.goto home/default", *closes}
        self.assertEqual({seconds[name] for name in timed}, {str(TIMED_BOUND_S)})
        untimed = {value for name, value in seconds.items() if name not in timed}
        self.assertEqual(untimed, {str(TIMEOUT_S)})
        self.assertIn(call("bound", "page.pdf home/default", TIMEOUT_S), bounds)


class PdfNameTests(unittest.TestCase):
    """A PDF is named for its target, state and backgrounds."""

    def test_both_backgrounds(self) -> None:
        self.assertEqual(
            print_engine.pdf_name("home", "after-start", False),
            "home--after-start--bg-off.pdf",
        )
        self.assertEqual(
            print_engine.pdf_name("home", "after-start", True),
            "home--after-start--bg-on.pdf",
        )


class LaunchTests(EngineTestCase):
    """Which browser each state gets, launched once, on first need."""

    def test_the_chrome_engine_launches_installed_chrome(self) -> None:
        run = self.fake()
        (record,) = run.run(settings=engine_settings(self.pdf_dir, engine="chrome"))
        self.assertEqual(run.named("chromium.launch"), [_CHROME_LAUNCH])
        self.assertEqual(
            (record.engine, record.browser_version), ("chrome", CHROME_VERSION)
        )

    def test_native_dark_launches_chrome_forced_dark_once(self) -> None:
        run = self.fake(FakeScript(probes=[probe_dict(colorScheme="dark")]))
        states = (
            state(),
            state("dark-1", color_scheme="native-dark"),
            state("dark-2", color_scheme="native-dark"),
        )
        records = run.run(targets_file(target(states=states)))
        self.assertEqual(run.named("chromium.launch"), [_SHELL_LAUNCH, _DARK_LAUNCH])
        self.assertEqual(
            [(record.engine, record.browser_version) for record in records],
            [
                ("shell", SHELL_VERSION),
                ("chrome", CHROME_VERSION),
                ("chrome", CHROME_VERSION),
            ],
        )
        self.assertEqual([record.problems for record in records], [(), (), ()])

    def test_native_dark_is_its_own_launch_under_the_chrome_engine_too(self) -> None:
        run = self.fake(FakeScript(probes=[probe_dict(colorScheme="dark")]))
        states = (state(), state("dark", color_scheme="native-dark"))
        run.run(
            targets_file(target(states=states)),
            engine_settings(self.pdf_dir, engine="chrome"),
        )
        self.assertEqual(run.named("chromium.launch"), [_CHROME_LAUNCH, _DARK_LAUNCH])

    def test_nothing_is_launched_before_it_is_needed(self) -> None:
        run = self.fake(FakeScript(probes=[probe_dict(colorScheme="dark")]))
        dark_only = target(states=(state("dark", color_scheme="native-dark"),))
        run.run(targets_file(dark_only))
        self.assertEqual(run.named("chromium.launch"), [_DARK_LAUNCH])

    def test_a_launch_failure_is_an_engine_error_naming_the_engine(self) -> None:
        cases: tuple[tuple[Literal["shell", "chrome"], State, str], ...] = (
            ("shell", state(), "shell"),
            ("chrome", state(), "chrome"),
            ("shell", state("dark", color_scheme="native-dark"), _FORCE_DARK),
        )
        for engine, the_state, named in cases:
            with self.subTest(engine=engine, state=the_state.name):
                script = FakeScript(launch_error="Executable doesn't exist\nat a path")
                run = self.fake(script)
                with self.assertRaises(print_engine.EngineError) as caught:
                    run.run(
                        targets_file(target(states=(the_state,))),
                        engine_settings(self.pdf_dir, engine=engine),
                    )
                message = str(caught.exception)
                self.assertIn(named, message)
                self.assertIn("Executable doesn't exist", message)
                self.assertNotIn("at a path", message)
                self.assertIsInstance(caught.exception.__cause__, RuntimeError)
                self.assertEqual(run.unbounded(), [])

    def test_each_launch_is_bounded_at_twice_its_timeout(self) -> None:
        states = (state(), state("dark", color_scheme="native-dark"))
        for settings, launched in (
            (engine_settings(self.pdf_dir), _SHELL_LAUNCH),
            (engine_settings(self.pdf_dir, engine="chrome"), _CHROME_LAUNCH),
        ):
            with self.subTest(engine=settings.engine):
                run = self.fake(FakeScript(probes=[probe_dict(colorScheme="dark")]))
                run.run(targets_file(target(states=states)), settings)
                self.assertEqual(
                    run.around(launched),
                    timed_bound(f"chromium.launch {settings.engine}"),
                )
                self.assertEqual(
                    run.around(_DARK_LAUNCH), timed_bound("chromium.launch native-dark")
                )


class ClosingTests(EngineTestCase):
    """Every context and browser closes, in the watchdog, however the run ends."""

    def test_every_browser_closes_when_a_state_raises(self) -> None:
        broken = BASE_URL + "/broken"
        script = FakeScript(
            probes=[probe_dict(colorScheme="dark")],
            goto_errors={broken: "net::ERR_CONNECTION_REFUSED\nCall log: ..."},
        )
        run = self.fake(script)
        targets = targets_file(
            target(states=(state("dark", color_scheme="native-dark"),)),
            target("other", page="/broken"),
        )
        with self.assertRaises(print_engine.EngineError) as caught:
            run.run(targets)
        self.assertIn("other/default", str(caught.exception))
        self.assertIn("net::ERR_CONNECTION_REFUSED", str(caught.exception))
        self.assertNotIn("Call log", str(caught.exception))
        self.assertEqual(len(run.named("context.close")), 2)
        self.assertEqual(
            sorted(run.named("browser.close")),
            sorted(
                [
                    call("browser.close", CHROME_VERSION),
                    call("browser.close", SHELL_VERSION),
                ]
            ),
        )
        self.assertEqual(run.unbounded(), [])

    def test_a_second_launch_failing_still_closes_the_first_browser(self) -> None:
        script = FakeScript(
            launch_error="Chromium distribution 'chrome' is not found",
            launches_before_error=1,
        )
        run = self.fake(script)
        states = (state(), state("dark", color_scheme="native-dark"))
        with self.assertRaises(print_engine.EngineError) as caught:
            run.run(targets_file(target(states=states)))
        self.assertIn(_FORCE_DARK, str(caught.exception))
        self.assertEqual(run.named("chromium.launch"), [_SHELL_LAUNCH, _DARK_LAUNCH])
        # The shell printed its state, then closed with the run although the next launch
        # failed.
        self.assertEqual(len(run.named("page.pdf")), 1)
        self.assertEqual(
            run.named("browser.close"), [call("browser.close", SHELL_VERSION)]
        )
        self.assertEqual(run.unbounded(), [])

    def test_the_browsers_close_after_the_last_state(self) -> None:
        run = self.fake()
        run.run(targets_file(target(), target("two", page="/two")))
        names = [
            entry[0] for entry in run.calls if entry[0] not in {"bound", "unbound"}
        ]
        self.assertEqual(names[-1], "browser.close")
        self.assertEqual(names.count("browser.close"), 1)
        self.assertEqual(names.count("context.close"), 2)


class TimeoutTests(EngineTestCase):
    """A timeout Playwright or the watchdog could not honor is refused before any launch."""

    def test_a_bad_timeout_is_refused_first(self) -> None:
        for timeout_s in (0.0, -1.0, math.nan, math.inf, threading.TIMEOUT_MAX * 2):
            with self.subTest(timeout_s=timeout_s):
                run = self.fake()
                with self.assertRaises(ValueError):
                    run.run(settings=engine_settings(self.pdf_dir, timeout_s=timeout_s))
                self.assertEqual(run.calls, [])

    def test_a_timeout_the_watchdog_could_not_keep_twice_over_is_refused_first(
        self,
    ) -> None:
        # A call handed the timeout is bounded at twice it, so half the most a lock can
        # wait is the longest timeout every bound can keep.
        refused = self.fake()
        with self.assertRaises(ValueError):
            refused.run(
                settings=engine_settings(self.pdf_dir, timeout_s=threading.TIMEOUT_MAX)
            )
        self.assertEqual(refused.calls, [])
        kept = self.fake()
        (record,) = kept.run(
            settings=engine_settings(self.pdf_dir, timeout_s=threading.TIMEOUT_MAX / 2)
        )
        self.assertEqual(len(record.pdfs), 1)


class EveryCallBoundedTests(EngineTestCase):
    """Every Playwright call the engine makes runs in a bound of its own.

    Inside SOME bound is not enough. Each open bound runs its own timer, and only a bound of
    the call's own is sized for it and names it on stderr; an enclosing call's bound gives it
    that call's remaining budget and name. What makes a bound a call's own is the guard's
    rule, which `test_print_engine_fixtures.py` pins.
    """

    def test_every_kind_of_call_runs_inside_its_own_bound(self) -> None:
        script = FakeScript(
            requests=["https://example.com/x.png"],
            web_sockets=["wss://example.com/live"],
        )
        actions = (
            Action("click", "#start", None),
            Action("wait_for", "#grid", None),
            Action("wait_ms", None, 250),
        )
        states = (
            state("acted", actions=actions),
            state("dark", color_scheme="native-dark"),
        )
        run = self.fake(script)
        run.run(targets_file(target(states=states), backgrounds="both"))
        # The run made every kind of call, so none can have escaped the sweep unseen.
        self.assertEqual(
            {entry[0] for entry in run.calls} - {"bound", "unbound"}, _EVERY_CALL
        )
        self.assertEqual(run.unbounded(), [])


class EffectiveSettingsTests(EngineTestCase):
    """A key the targets file sets wins over the matching engine setting."""

    def test_the_files_viewport_and_backgrounds_win(self) -> None:
        wide = Viewport(width=1600, height=900)
        effective = print_engine.effective_settings(
            engine_settings(self.pdf_dir),
            targets_file(viewport=wide, backgrounds="both"),
        )
        self.assertEqual(
            effective,
            engine_settings(self.pdf_dir, viewport=wide, backgrounds="both"),
        )

    def test_the_settings_stand_where_the_file_is_silent(self) -> None:
        settings = engine_settings(self.pdf_dir, backgrounds="on")
        self.assertEqual(
            print_engine.effective_settings(settings, targets_file()), settings
        )

    def test_a_backgrounds_value_the_engine_cannot_print_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            print_engine.effective_settings(
                engine_settings(self.pdf_dir), targets_file(backgrounds="sideways")
            )

    def test_a_run_uses_the_files_keys(self) -> None:
        wide = Viewport(width=1600, height=900)
        run = self.fake()
        (record,) = run.run(targets_file(viewport=wide, backgrounds="both"))
        self.assertEqual(record.viewport, wide)
        self.assertEqual([pdf.backgrounds for pdf in record.pdfs], [False, True])
        (context,) = run.playwright.chromium.browsers[0].contexts
        self.assertEqual(context.options["viewport"], {"width": 1600, "height": 900})


class OrderTests(EngineTestCase):
    """One record per state, in file order: targets, then each target's states."""

    def test_records_follow_the_file(self) -> None:
        run = self.fake()
        targets = targets_file(
            target(states=(state("a"), state("b"))),
            target("two", page="/two", states=(state("c"),)),
        )
        records = run.run(targets)
        self.assertEqual(
            [(record.target, record.state, record.page) for record in records],
            [("home", "a", "/"), ("home", "b", "/"), ("two", "c", "/two")],
        )
        self.assertEqual(
            [entry[1] for entry in run.named("page.goto")],
            [BASE_URL + "/", BASE_URL + "/", BASE_URL + "/two"],
        )


class EmulateMediaTests(EngineTestCase):
    """`emulate_media` is not the print path, so the engine never calls it."""

    def test_the_trap_is_armed(self) -> None:
        run = self.fake()
        run.run()
        (page,) = run.pages()
        with self.assertRaises(AssertionError):
            page.emulate_media(media="print")

    def test_no_state_kind_calls_it(self) -> None:
        # Every scheme and motion setting runs to its PDF, so none reached the trap.
        run = self.fake(FakeScript(probes=[probe_dict(colorScheme="dark")]))
        states = tuple(
            state(scheme, color_scheme=scheme, reduced_motion=motion)
            for scheme, motion in (
                ("native", "native"),
                ("light", "reduce"),
                ("dark", "native"),
                ("native-dark", "reduce"),
            )
        )
        records = run.run(targets_file(target(states=states)))
        self.assertEqual([len(record.pdfs) for record in records], [1, 1, 1, 1])


if __name__ == "__main__":
    unittest.main()
