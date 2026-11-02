"""Grouped suite: a rendering's persistent context with an extension, through a fake Playwright.

`PersistentBrowser` stands where a launched browser stands when an engine runs with an extension:
each context it opens is its own browser launch, in a fresh profile under the run's scratch
folder, confirmed to have loaded the extension before it is handed back. The stand-in browser
type in `_launch_fakes.py` records every launch, and its contexts play the engine's part (a
service worker that starts or never does, an `extensions.json` Firefox writes or never does), so
each branch is reached with no engine build installed. The live suite,
`test_pass_extension_live.py`, drives the real one; `test_pass_extension_tabs.py` the tabs and
faults around a launch.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ on sys.path
# for every import after it.
from _launch_fakes import (
    CONTEXT_OPTIONS,
    STORAGE,
    TIMEOUT,
    Browser,
    BrowserType,
    Context,
    LaunchCase,
    each_launch,
    registered,
    write_registry,
)

import json
import unittest
from collections.abc import Callable
from pathlib import Path

import pass_extension_launch
from _extension_fixtures import GECKO_ID
from pass_extension_launch import ExtensionFault
from pass_probe import MS_PER_SECOND


class ChromiumTests(LaunchCase):
    """Chromium: the unpacked folder loaded by its launch arguments, confirmed by its worker."""

    def test_each_context_is_a_confirmed_launch_with_the_extension(self) -> None:
        context = Context(self.log, workers=(self.worker_url(),), browser=Browser())
        browser_type = BrowserType([context])
        browser = self.persistent(
            browser_type, self.chromium, channel="chromium", args=["--guard"]
        )
        self.assertEqual(browser.version, "")
        opened = browser.new_context(storage_state=None, **CONTEXT_OPTIONS)
        self.assertIs(opened, context)
        (launch,) = browser_type.launches
        folder = self.chromium.path
        self.assertEqual(
            launch["args"],
            [
                "--guard",
                f"--disable-extensions-except={folder}",
                f"--load-extension={folder}",
            ],
        )
        self.assertEqual(launch["channel"], "chromium")
        self.assertIs(launch["headless"], True)
        self.assertEqual(launch["timeout"], TIMEOUT * MS_PER_SECOND)
        for key, value in CONTEXT_OPTIONS.items():
            self.assertEqual(launch[key], value, key)
        self.assertNotIn("storage_state", launch)
        # A fresh profile under the run's scratch folder, never anyone's own.
        profile = launch["user_data_dir"]
        self.assertEqual(
            profile.parent, self.scratch / pass_extension_launch.PROFILES_DIR_NAME
        )
        self.assertTrue(profile.name.startswith("chromium-"), profile)
        self.assertEqual(browser.version, "153.0.1")
        # The tab the launch opened is closed: only the page the pass opens is measured.
        self.assertEqual(self.log, ["close page about:blank"])

    def test_a_worker_starting_after_the_launch_is_waited_for(self) -> None:
        context = Context(self.log, arriving=self.worker_url())
        browser = self.persistent(BrowserType([context]), self.chromium)
        self.assertIs(browser.new_context(**CONTEXT_OPTIONS), context)
        self.assertEqual(self.log[0], f"wait serviceworker {TIMEOUT * MS_PER_SECOND}")

    def test_a_worker_that_never_starts_is_a_fault_naming_the_extension(self) -> None:
        context = Context(self.log)
        browser = self.persistent(BrowserType([context]), self.chromium)
        with self.assertRaises(ExtensionFault) as raised:
            browser.new_context(**CONTEXT_OPTIONS)
        self.assertEqual(
            str(raised.exception),
            f"chromium: the extension Fixture 2.5.1 did not load within {TIMEOUT:g}s",
        )
        self.assertIn("close context", self.log)

    def test_another_extension_s_worker_does_not_count(self) -> None:
        # Another script of ours, and a worker at our path in ANOTHER extension: Chromium names
        # an unpacked extension by its folder, so the id tells two such workers apart.
        other_path = self.worker_url().replace("/bg/worker.js", "/other.js")
        other_id = self.worker_url("abcdefghijklmnopabcdefghijklmnop")
        self.assertNotEqual(other_id, self.worker_url())
        for context in (
            Context(self.log, workers=(other_path,)),
            Context(self.log, arriving=other_path),
            Context(self.log, workers=(other_id,)),
            Context(self.log, arriving=other_id),
            Context(self.log, workers=("https://192.0.2.1/bg/worker.js",)),
        ):
            with self.subTest(workers=context.service_workers):
                browser = self.persistent(BrowserType([context]), self.chromium)
                with self.assertRaises(ExtensionFault):
                    browser.new_context(**CONTEXT_OPTIONS)

    def test_a_launch_that_fails_is_a_fault_with_its_first_line(self) -> None:
        browser_type = BrowserType(
            [], fails_with="\nExecutable doesn't exist at x\nmore"
        )
        browser = self.persistent(browser_type, self.chromium)
        with self.assertRaises(ExtensionFault) as raised:
            browser.new_context(**CONTEXT_OPTIONS)
        self.assertEqual(
            str(raised.exception),
            "chromium: cannot launch with the extension Fixture 2.5.1: "
            "Executable doesn't exist at x",
        )

    def test_stored_values_are_set_before_the_pass_opens_its_page(self) -> None:
        welcome = "chrome-error://chromewebdata/"
        context = Context(
            self.log, workers=(self.worker_url(),), pages=("about:blank", welcome)
        )
        browser = self.persistent(BrowserType([context]), self.chromium)
        browser.new_context(storage_state=STORAGE, **CONTEXT_OPTIONS)
        self.assertEqual(
            self.log,
            [
                f"storage {json.dumps(STORAGE)}",
                "close page about:blank",
                f"close page {welcome}",
            ],
        )

    def test_a_context_that_cannot_take_its_values_is_a_fault(self) -> None:
        context = Context(
            self.log, workers=(self.worker_url(),), fails=frozenset({"storage"})
        )
        browser = self.persistent(BrowserType([context]), self.chromium)
        with self.assertRaises(ExtensionFault) as raised:
            browser.new_context(storage_state=STORAGE, **CONTEXT_OPTIONS)
        self.assertEqual(
            str(raised.exception),
            "chromium: the context could not be prepared: "
            "Target page, context or browser has been closed",
        )
        self.assertEqual(self.log[-1], "close context")


class ProfileTests(LaunchCase):
    """Every context gets its own profile, removed once closed, and the run's close ends all."""

    def test_each_context_has_its_own_profile_removed_once_it_closes(self) -> None:
        first, second = (
            Context(self.log, workers=(self.worker_url(),)),
            Context(self.log, workers=(self.worker_url(),)),
        )
        browser_type = BrowserType([first, second])
        browser = self.persistent(browser_type, self.chromium)
        browser.new_context(**CONTEXT_OPTIONS)
        one = browser_type.launches[0]["user_data_dir"]
        self.assertTrue(one.is_dir())
        first.close()
        browser.new_context(**CONTEXT_OPTIONS)
        two = browser_type.launches[1]["user_data_dir"]
        self.assertNotEqual(one, two)
        self.assertFalse(one.exists(), "a closed context's profile is removed")
        self.assertTrue(two.is_dir(), "an open one's is kept")
        browser.close()
        self.assertEqual(self.log[-1], "close context")
        self.assertFalse(two.exists())

    def test_a_context_already_closed_is_not_closed_again_but_its_profile_goes(
        self,
    ) -> None:
        # Only a live test reached this branch, and it does not run on every host, so a
        # Windows run's coverage missed it.
        context = Context(self.log, workers=(self.worker_url(),))
        browser_type = BrowserType([context])
        browser = self.persistent(browser_type, self.chromium)
        browser.new_context(**CONTEXT_OPTIONS)
        profile = browser_type.launches[0]["user_data_dir"]
        context.close()
        closes = self.log.count("close context")
        browser.close()
        self.assertEqual(self.log.count("close context"), closes)
        self.assertFalse(profile.exists())

    def test_closing_with_nothing_open_closes_nothing(self) -> None:
        browser = self.persistent(BrowserType([]), self.chromium)
        browser.close()
        self.assertEqual(self.log, [])

    def test_the_bound_covers_two_contexts_and_their_launches(self) -> None:
        chromium = self.persistent(BrowserType([]), self.chromium)
        firefox = self.persistent(BrowserType([]), self.firefox)
        close = pass_extension_launch.PERSISTENT_CLOSE_SECONDS
        self.assertEqual(chromium.bound(10.0), 2 * 1 * (2 * 10.0 + close))
        self.assertEqual(firefox.bound(10.0), 2 * 2 * (2 * 10.0 + close))

    def test_the_extra_bound_is_none_for_a_plain_browser(self) -> None:
        chromium = self.persistent(BrowserType([]), self.chromium)
        self.assertEqual(
            pass_extension_launch.extra_bound(chromium, 3.0), chromium.bound(3.0)
        )
        self.assertEqual(pass_extension_launch.extra_bound(object(), 3.0), 0.0)


class FirefoxTests(LaunchCase):
    """Firefox: the signed xpi sideloaded into the profile, confirmed by `extensions.json`."""

    def test_the_xpi_is_placed_under_its_id_before_the_launch(self) -> None:
        seen: list[bool] = []

        def register(profile: Path, _launch: int) -> None:
            placed = profile / "extensions" / f"{GECKO_ID}.xpi"
            seen.append(placed.read_bytes() == self.firefox.path.read_bytes())
            registered(profile)

        browser_type = BrowserType([Context(self.log)], register=register)
        browser = self.persistent(
            browser_type,
            self.firefox,
            env={"MOZ_APP_DATA": "scratch"},
            firefox_user_prefs={"ui.useOverlayScrollbars": 1, "network.proxy.type": 1},
        )
        browser.new_context(**CONTEXT_OPTIONS)
        self.assertEqual(seen, [True])
        (launch,) = browser_type.launches
        self.assertEqual(launch["env"], {"MOZ_APP_DATA": "scratch"})
        self.assertEqual(
            launch["firefox_user_prefs"],
            {
                "ui.useOverlayScrollbars": 1,
                "network.proxy.type": 1,
                **pass_extension_launch.FIREFOX_SIDELOAD_PREFS,
            },
        )
        self.assertEqual(
            dict(pass_extension_launch.FIREFOX_SIDELOAD_PREFS),
            {
                "extensions.autoDisableScopes": 0,
                "extensions.enabledScopes": 15,
                "extensions.startupScanScopes": 15,
            },
        )
        self.assertNotIn("args", launch)
        self.assertTrue(launch["user_data_dir"].name.startswith("firefox-"))

    def test_an_add_on_registered_only_after_a_relaunch_is_relaunched_once(
        self,
    ) -> None:
        def register(profile: Path, launch: int) -> None:
            if launch == 2:
                registered(profile)

        contexts = [Context(self.log), Context(self.log)]
        browser_type = BrowserType(list(contexts), register=register)
        browser = self.persistent(browser_type, self.firefox)
        self.assertIs(browser.new_context(**CONTEXT_OPTIONS), contexts[1])
        first, second = browser_type.launches
        self.assertEqual(first["user_data_dir"], second["user_data_dir"])
        # The relaunch reuses the profile, so its closed first launch must not remove it.
        self.assertTrue(second["user_data_dir"].is_dir())

    def test_an_add_on_never_active_is_a_fault_after_one_relaunch(self) -> None:
        writes: dict[str, Callable[[Path], object]] = {
            "inactive": lambda p: registered(p, active=False),
            "elsewhere": lambda p: registered(p, location="app-system-defaults"),
            "torn": write_registry("{"),
            "no list": write_registry('{"addons": 1}'),
            "not a table": write_registry("[]"),
            "odd entry": write_registry('{"addons": [1]}'),
            "nothing": lambda _p: None,
        }
        for label, write in writes.items():
            with self.subTest(label):
                browser_type = BrowserType(
                    [Context(self.log), Context(self.log)],
                    register=each_launch(write),
                )
                browser = self.persistent(browser_type, self.firefox)
                with self.assertRaises(ExtensionFault) as raised:
                    browser.new_context(**CONTEXT_OPTIONS)
                self.assertEqual(
                    str(raised.exception),
                    f"firefox: the extension Fixture 4.9 ({GECKO_ID}) did not load "
                    f"within {TIMEOUT:g}s",
                )
                self.assertEqual(len(browser_type.launches), 2)

    def test_a_context_with_no_tab_gets_one_to_pause_in(self) -> None:
        context = Context(self.log, pages=())
        browser_type = BrowserType(
            [context], register=lambda profile, _n: registered(profile)
        )
        browser = self.persistent(browser_type, self.firefox)
        browser.new_context(**CONTEXT_OPTIONS)
        # The tab it opened to wait in is closed with the rest.
        self.assertEqual(self.log, ["close page about:blank"])


if __name__ == "__main__":
    unittest.main()
