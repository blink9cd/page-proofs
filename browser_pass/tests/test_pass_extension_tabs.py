"""Grouped suite: around a persistent launch (the tabs an extension opens, and what can fail).

A context with an extension is the pass's for one rendering, but an extension can open a tab in
it at any time, a welcome page after install, say. `PersistentBrowser` closes each one as it
arrives, and the CSP collector keeps only the reports of the pages the pass opened itself
(`test_pass_csp.py`), so a close that loses a race still cannot make a finding. And every way a
launch can fail is that rendering's instrument fault, never the run's end: an engine error that
is not a timeout, or the disk refusing a profile's files. The id a Chromium extension is
confirmed by is derived here too, from its manifest's key or else its path, and an extension
with no id to confirm by is the pass's own defect. The stand-ins are `_launch_fakes.py`'s;
`test_pass_extension_launch.py` drives the launches themselves.
"""

from __future__ import annotations

# First, beside this module via the loader's top_level_dir: it puts the tool's src/ on sys.path
# for every import after it.
from _launch_fakes import (
    CONTEXT_OPTIONS,
    FAILED,
    STORAGE,
    BrowserType,
    Context,
    LaunchCase,
)

import dataclasses
import shutil
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath

import pass_extension
from _extension_fixtures import CHROMIUM_MANIFEST, unpacked, xpi
from pass_extension_launch import ExtensionFault, PersistentBrowser


class ForeignTabTests(LaunchCase):
    """A tab the pass did not open is closed as it arrives; the pass's own are kept."""

    def setUp(self) -> None:
        super().setUp()
        self.context = self.loaded()
        browser = self.persistent(BrowserType([self.context]), self.chromium)
        browser.new_context(**CONTEXT_OPTIONS)
        self.log.clear()

    def test_a_tab_an_extension_opens_later_is_closed(self) -> None:
        welcome = self.worker_url().replace("bg/worker.js", "welcome.html")
        self.context.open_tab(welcome)
        self.assertEqual(self.log, [f"close page {welcome}"])

    def test_the_pages_the_pass_opens_are_kept(self) -> None:
        page = self.context.new_page()
        self.assertEqual(self.log, [])
        self.assertIn(page, self.context.pages)

    def test_a_tab_that_will_not_close_is_left_to_the_attribution(self) -> None:
        # The tab had closed itself, or its browser was going: either way it is not measured.
        self.context.open_tab("http://192.0.2.1/welcome.html", close_fails=True)
        self.assertEqual(self.log, ["close page http://192.0.2.1/welcome.html"])


#: A real manifest `key`, and the id Chromium gave the extension carrying it: the `key` of
#: Stylus 2.4.16's published Chrome build, its public key (no secret). Loaded unpacked in a
#: live run, it was named by this id, so the derivation agrees with Chromium, not with itself.
_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2ypG+Z/beZtoYrxxwXYhMwQiAiwRVnSHqdpOSzJdjsXV"
    "WdvJjlgEuZcU8kte75w58P45LsRUrwvU6N9x12S6eW84KNEBC8rlZj0RGNoxuhSAcdxneYzjJ9tBkZKOidVedYHH"
    "si3LeaXiLuTNTBR+2lf3uCNcP0ebaFML9uDLdYTGEW4eL3hnEKYPSmT1/xkh4bSGTToCg4YNuWWWoTA0beEOpBWY"
    "kPVMarLDQgPzMN5Byu5w3lOS2zL0PPJcmdyk3ez/ZsB4PZKU+h17fVA6+YTvUfxUqLde5i2RiuZhEb6Coo5/W90Z"
    "W1yCDC9osjWrxMGYeUMQWHPIgFtDhk4K6QIDAQAB"
)
_KEY_ID = "clngdbkpkpeebahjckkjfobafhncgmne"

#: The same key PEM-wrapped, which Chromium loads too: its markers and whitespace dropped.
_PEM_KEY = "\n".join(
    (
        "-----BEGIN PUBLIC KEY-----",
        *(_KEY[start : start + 64] for start in range(0, len(_KEY), 64)),
        "-----END PUBLIC KEY-----",
        "",
    )
)


class ExtensionIdTests(unittest.TestCase):
    """The id Chromium gives an unpacked extension: its manifest's key, else its path, hashed."""

    def test_a_posix_path_s_id(self) -> None:
        # Chromium's own rule: SHA-256 of the path's bytes, the first 32 hex digits, 0-f as a-p.
        # The rule was checked against the id Chromium gave a folder in a live run, and the live
        # suite checks it every run: the fixture is confirmed loaded only at the id derived.
        # A pure POSIX path keeps its slashes on every host; `Path` would be a Windows path there.
        self.assertEqual(
            pass_extension.chromium_id(
                PurePosixPath("/opt/extensions/fixture"), windows=False
            ),
            "oekjifdllfmnjigfeppdlccccffciegk",
        )

    def test_a_windows_path_is_hashed_as_utf_16_with_its_drive_upper_cased(
        self,
    ) -> None:
        lower = pass_extension.chromium_id(
            PureWindowsPath(r"c:\extensions\fixture"), windows=True
        )
        self.assertEqual(lower, "hibhlbohgbdekngpimanffhgbgaajeol")
        self.assertEqual(
            pass_extension.chromium_id(
                PureWindowsPath(r"C:\extensions\fixture"), windows=True
            ),
            lower,
        )

    def test_an_unpacked_extension_carries_its_id_and_an_xpi_none(self) -> None:
        folder = Path(__file__).resolve().parent / "fixture_extension"
        extension = pass_extension.read_unpacked(folder)
        assert isinstance(extension, pass_extension.Extension), extension
        self.assertEqual(
            extension.extension_id,
            pass_extension.chromium_id(folder, windows=pass_extension.ON_WINDOWS),
        )
        with tempfile.TemporaryDirectory() as scratch:
            add_on = pass_extension.read_xpi(xpi(Path(scratch) / "fixture.xpi"))
            assert isinstance(add_on, pass_extension.Extension), add_on
            self.assertIsNone(add_on.extension_id)

    def test_a_manifest_key_names_the_extension_wherever_it_is(self) -> None:
        # Chromium's rule for a key: SHA-256 of its base64-decoded bytes, cut and lettered as a
        # path's is. The id is the one Chromium itself gave the published extension the key is
        # taken from, wherever the folder is and whether or not the key is PEM-wrapped.
        with tempfile.TemporaryDirectory() as scratch:
            for name, key in (("one", _KEY), ("two", _KEY), ("pem", _PEM_KEY)):
                with self.subTest(name=name):
                    folder = unpacked(
                        Path(scratch) / name, {**CHROMIUM_MANIFEST, "key": key}
                    )
                    extension = pass_extension.read_unpacked(folder)
                    assert isinstance(extension, pass_extension.Extension), extension
                    self.assertEqual(extension.extension_id, _KEY_ID)

    def test_a_key_that_is_not_base64_text_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            # A non-ASCII string too, which base64 decoding refuses with another error; and a
            # PEM wrapping with nothing inside it.
            bad = ("not base64!", "", 7, ["a"], "é", "-----BEGIN PUBLIC KEY-----")
            for number, key in enumerate(bad):
                with self.subTest(key=key):
                    manifest = {**CHROMIUM_MANIFEST, "key": key}
                    folder = unpacked(Path(scratch) / str(number), manifest)
                    refusal = pass_extension.read_unpacked(folder)
                    self.assertEqual(
                        refusal,
                        f'--chromium-extension {folder}: manifest.json\'s "key" is not '
                        "base64 text, so the id Chromium gives the extension is unknown",
                    )


class MissingIdTests(LaunchCase):
    """An extension read with no id to confirm it by is the pass's own defect, not a fault."""

    def test_a_chromium_extension_with_no_id_is_refused_before_any_launch(self) -> None:
        nameless = dataclasses.replace(self.chromium, extension_id=None)
        browser_type = BrowserType([self.loaded()])
        with self.assertRaises(ValueError) as raised:
            self.persistent(browser_type, nameless)
        self.assertIn("no extension id", str(raised.exception))
        self.assertEqual(browser_type.launches, [])

    def test_a_firefox_add_on_with_no_gecko_id_is_refused_too(self) -> None:
        facts = dataclasses.replace(self.firefox.facts, gecko_id=None)
        nameless = dataclasses.replace(self.firefox, facts=facts)
        with self.assertRaises(ValueError) as raised:
            self.persistent(BrowserType([]), nameless)
        self.assertIn("no gecko id", str(raised.exception))


class LaunchFaultTests(LaunchCase):
    """Whatever fails in getting a context is that rendering's fault, naming the engine."""

    def assert_fault(self, browser_type: BrowserType, *, firefox: bool = False) -> str:
        """`new_context` raises an `ExtensionFault`; its text."""
        extension = self.firefox if firefox else self.chromium
        browser = self.persistent(browser_type, extension)
        with self.assertRaises(ExtensionFault) as raised:
            browser.new_context(storage_state=STORAGE, **CONTEXT_OPTIONS)
        return str(raised.exception)

    def test_a_wait_for_the_worker_that_errors_closes_the_context(self) -> None:
        fault = self.assert_fault(
            BrowserType([Context(self.log, fails=frozenset({"wait"}))])
        )
        self.assertEqual(
            fault, f"chromium: the context could not be prepared: {FAILED}"
        )
        self.assertEqual(self.log[-1], "close context")

    def test_a_tab_firefox_cannot_open_to_wait_in(self) -> None:
        context = Context(self.log, pages=(), fails=frozenset({"new_page"}))
        fault = self.assert_fault(BrowserType([context]), firefox=True)
        self.assertEqual(fault, f"firefox: the context could not be prepared: {FAILED}")
        self.assertEqual(self.log[-1], "close context")

    def test_a_pause_that_errors_while_firefox_registers(self) -> None:
        context = Context(self.log, fails=frozenset({"pause"}))
        fault = self.assert_fault(BrowserType([context]), firefox=True)
        self.assertEqual(fault, f"firefox: the context could not be prepared: {FAILED}")

    def test_a_close_that_errors_after_the_extension_did_not_load(self) -> None:
        context = Context(self.log, fails=frozenset({"close"}))
        fault = self.assert_fault(BrowserType([context]))
        self.assertEqual(
            fault, f"chromium: the context could not be prepared: {FAILED}"
        )

    def test_a_tab_that_will_not_close_before_the_pass_opens_its_page(self) -> None:
        context = self.loaded(pages=())
        context.open_tab("about:blank", close_fails=True)
        fault = self.assert_fault(BrowserType([context]))
        self.assertEqual(
            fault, f"chromium: the context could not be prepared: {FAILED}"
        )
        self.assertEqual(self.log[-1], "close context")

    def test_an_xpi_gone_before_its_copy_into_the_profile(self) -> None:
        self.firefox.path.unlink()
        fault = self.assert_fault(BrowserType([]), firefox=True)
        self.assertTrue(
            fault.startswith("firefox: the context could not be prepared: "), fault
        )

    def test_a_closed_profile_the_disk_will_not_remove_faults_one_rendering(
        self,
    ) -> None:
        first, second, third = (self.loaded() for _ in range(3))
        browser_type = BrowserType([first, second, third])
        browser = self.persistent(browser_type, self.chromium)
        browser.new_context(**CONTEXT_OPTIONS)
        first.close()
        shutil.rmtree(browser_type.launches[0]["user_data_dir"])
        fault = self.assert_fault_of(browser)
        self.assertTrue(
            fault.startswith("chromium: the context could not be prepared: "), fault
        )
        # The profile it could not remove is not tried again: the next rendering is had.
        self.assertIs(browser.new_context(**CONTEXT_OPTIONS), second)

    def assert_fault_of(self, browser: PersistentBrowser) -> str:
        """`browser.new_context` raises an `ExtensionFault`; its text."""
        with self.assertRaises(ExtensionFault) as raised:
            browser.new_context(**CONTEXT_OPTIONS)
        return str(raised.exception)

    def test_an_error_that_is_neither_the_engine_s_nor_the_disk_s_is_not_caught(
        self,
    ) -> None:
        def broken(profile: Path, _launch: int) -> None:
            raise ValueError("a defect in the pass itself")

        browser_type = BrowserType([self.loaded()], register=broken)
        browser = self.persistent(browser_type, self.chromium)
        with self.assertRaises(ValueError):
            browser.new_context(**CONTEXT_OPTIONS)


if __name__ == "__main__":
    unittest.main()
