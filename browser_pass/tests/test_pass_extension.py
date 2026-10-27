"""Grouped suite: reading an extension, validating the options, and the facts line.

`pass_extension` is pure: it reads an unpacked Chromium extension's manifest or a Firefox `.xpi`'s,
refuses what cannot be measured (exit 2 in the CLI, before any browser opens), and says what each
engine is launched with under `--loopback-only` (`test_pass_extension_guard.py`). Every extension
here is written fresh into a scratch folder (`_extension_fixtures`), so a refusal can be reached
by breaking one.
"""

from __future__ import annotations

import copy
import json
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import IO, Literal
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pass_extension  # noqa: E402 - the tool's private src/, via the insert above
from _extension_fixtures import (  # noqa: E402 - beside this suite
    CHROMIUM_MANIFEST,
    FIREFOX_MANIFEST,
    FIXTURE_EXTENSION,
    GECKO_ID,
    unpacked,
    xpi,
)
from pass_extension import (  # noqa: E402 - the tool's private src/
    Extension,
    ExtensionFacts,
    Isolation,
)

_ALL = ("chromium", "firefox", "webkit")

#: A zip central directory entry's signature, and where in it the uncompressed size sits.
_CENTRAL_DIRECTORY_ENTRY = b"PK\x01\x02"
_UNCOMPRESSED_SIZE_OFFSET = 24

#: The archive class the tests stand in for, before any test replaces it.
_ZIP_FILE = zipfile.ZipFile


class _UnderstatedSizes(_ZIP_FILE):
    """An archive whose `getinfo` declares every entry empty, while its bytes are read whole.

    Opening an entry goes by its true record, so only the declared size lies.
    """

    def getinfo(self, name: str) -> zipfile.ZipInfo:
        """The entry's record, its size declared as zero."""
        declared = copy.copy(super().getinfo(name))
        declared.file_size = 0
        return declared

    def open(
        self,
        name: str | zipfile.ZipInfo,
        mode: Literal["r", "w"] = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ) -> IO[bytes]:
        """Open the entry by its true record."""
        record = name if isinstance(name, zipfile.ZipInfo) else super().getinfo(name)
        return super().open(record, mode, pwd, force_zip64=force_zip64)


class _Scratch(unittest.TestCase):
    """A throwaway folder per test."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)


class UnpackedTests(_Scratch):
    """`--chromium-extension`: a folder holding a manifest with a name, a version and a worker."""

    def test_the_committed_fixture_reads(self) -> None:
        extension = pass_extension.read_unpacked(FIXTURE_EXTENSION)
        assert isinstance(extension, Extension), extension
        self.assertEqual(
            extension.facts,
            ExtensionFacts("chromium", "browser_pass fixture extension", "1.0.0"),
        )
        self.assertEqual(extension.worker, "worker.js")
        self.assertTrue(extension.path.is_absolute())

    def test_a_folder_is_read_for_its_name_version_and_worker(self) -> None:
        extension = pass_extension.read_unpacked(unpacked(self.root / "ext"))
        assert isinstance(extension, Extension), extension
        self.assertEqual(
            extension.facts, ExtensionFacts("chromium", "Fixture", "2.5.1")
        )
        self.assertEqual(extension.worker, "bg/worker.js")
        self.assertEqual(extension.path, (self.root / "ext").resolve())

    def test_a_localized_name_is_read_from_its_default_locale(self) -> None:
        manifest = {
            **CHROMIUM_MANIFEST,
            "name": "__MSG_extName__",
            "default_locale": "en",
        }
        messages = {"extname": {"message": "Fixture, localized"}}
        folder = unpacked(
            self.root / "ext",
            manifest,
            files={"_locales/en/messages.json": json.dumps(messages)},
        )
        extension = pass_extension.read_unpacked(folder)
        assert isinstance(extension, Extension), extension
        self.assertEqual(extension.facts.name, "Fixture, localized")

    def test_a_localized_name_with_no_message_is_kept_as_written(self) -> None:
        manifest = {
            **CHROMIUM_MANIFEST,
            "name": "__MSG_extName__",
            "default_locale": "en",
        }
        messages = "_locales/en/messages.json"
        no_message = json.dumps({"other": {"message": "x"}})
        texts: tuple[dict[str, str], ...] = (
            {},
            {messages: "[]"},
            {messages: "{"},
            {messages: no_message},
        )
        for number, files in enumerate(texts):
            with self.subTest(files=files):
                folder = unpacked(self.root / f"ext{number}", manifest, files=files)
                extension = pass_extension.read_unpacked(folder)
                assert isinstance(extension, Extension), extension
                self.assertEqual(extension.facts.name, "__MSG_extName__")

    def test_a_localized_name_without_a_default_locale_is_kept_as_written(self) -> None:
        manifest = {**CHROMIUM_MANIFEST, "name": "__MSG_extName__"}
        extension = pass_extension.read_unpacked(unpacked(self.root / "ext", manifest))
        assert isinstance(extension, Extension), extension
        self.assertEqual(extension.facts.name, "__MSG_extName__")

    def test_refusals(self) -> None:
        cases = {
            "missing": (None, "is not a folder holding manifest.json"),
            "not json": ("{", "manifest.json is not JSON"),
            "not an object": ("[1]", "manifest.json is not a JSON object"),
            "no name": (
                {k: v for k, v in CHROMIUM_MANIFEST.items() if k != "name"},
                'manifest.json gives no "name"',
            ),
            "no version": (
                {**CHROMIUM_MANIFEST, "version": ""},
                'manifest.json gives no "version"',
            ),
            "no worker": (
                {k: v for k, v in CHROMIUM_MANIFEST.items() if k != "background"},
                "declares no background service worker",
            ),
            "worker not text": (
                {**CHROMIUM_MANIFEST, "background": {"service_worker": 3}},
                "declares no background service worker",
            ),
            "background not a table": (
                {**CHROMIUM_MANIFEST, "background": "worker.js"},
                "declares no background service worker",
            ),
        }
        for label, (manifest, reason) in cases.items():
            with self.subTest(label):
                folder = unpacked(self.root / label.replace(" ", "-"), manifest)
                refusal = pass_extension.read_unpacked(folder)
                assert isinstance(refusal, str), refusal
                self.assertTrue(
                    refusal.startswith(f"--chromium-extension {folder}"), refusal
                )
                self.assertIn(reason, refusal)

    def test_a_path_that_is_not_a_folder_is_refused(self) -> None:
        refusal = pass_extension.read_unpacked(self.root / "absent")
        self.assertEqual(
            refusal,
            f"--chromium-extension {self.root / 'absent'} is not a folder holding manifest.json",
        )

    def test_an_oversized_manifest_is_refused_unread(self) -> None:
        folder = unpacked(
            self.root / "big", " " * (pass_extension.MANIFEST_LIMIT_BYTES + 1)
        )
        refusal = pass_extension.read_unpacked(folder)
        assert isinstance(refusal, str), refusal
        self.assertIn("manifest.json is larger than", refusal)


class XpiTests(_Scratch):
    """`--firefox-extension`: an `.xpi` whose manifest has a name, a version and a gecko id."""

    def test_an_xpi_is_read_for_its_name_version_and_gecko_id(self) -> None:
        path = xpi(self.root / "fixture.xpi")
        extension = pass_extension.read_xpi(path)
        assert isinstance(extension, Extension), extension
        self.assertEqual(
            extension.facts, ExtensionFacts("firefox", "Fixture", "4.9", GECKO_ID)
        )
        self.assertIsNone(extension.worker)
        self.assertEqual(extension.path, path.resolve())

    def test_the_legacy_applications_key_gives_the_id_too(self) -> None:
        manifest = {
            **{
                k: v
                for k, v in FIREFOX_MANIFEST.items()
                if k != "browser_specific_settings"
            },
            "applications": {"gecko": {"id": GECKO_ID}},
        }
        extension = pass_extension.read_xpi(xpi(self.root / "legacy.xpi", manifest))
        assert isinstance(extension, Extension), extension
        self.assertEqual(extension.facts.gecko_id, GECKO_ID)

    def test_a_localized_name_is_read_from_inside_the_archive(self) -> None:
        manifest = {**FIREFOX_MANIFEST, "name": "__MSG_Name__", "default_locale": "de"}
        messages = {"name": {"message": "Fixture, lokalisiert"}}
        path = xpi(
            self.root / "localized.xpi",
            manifest,
            files={"_locales/de/messages.json": json.dumps(messages)},
        )
        extension = pass_extension.read_xpi(path)
        assert isinstance(extension, Extension), extension
        self.assertEqual(extension.facts.name, "Fixture, lokalisiert")

    def test_a_localized_name_with_no_messages_in_the_archive_is_kept(self) -> None:
        manifest = {**FIREFOX_MANIFEST, "name": "__MSG_Name__", "default_locale": "de"}
        extension = pass_extension.read_xpi(xpi(self.root / "bare.xpi", manifest))
        assert isinstance(extension, Extension), extension
        self.assertEqual(extension.facts.name, "__MSG_Name__")

    def test_refusals(self) -> None:
        without_id = {
            k: v
            for k, v in FIREFOX_MANIFEST.items()
            if k != "browser_specific_settings"
        }
        cases = {
            "no manifest": (None, "holds no manifest.json"),
            "not json": ("{", "manifest.json is not JSON"),
            "no id": (without_id, "names no gecko id"),
            "gecko not a table": (
                {**without_id, "browser_specific_settings": {"gecko": "x"}},
                "names no gecko id",
            ),
            "id a path": (
                {**without_id, "browser_specific_settings": {"gecko": {"id": "../x"}}},
                "is not a plain file name",
            ),
            "id hidden": (
                {**without_id, "browser_specific_settings": {"gecko": {"id": ".x"}}},
                "is not a plain file name",
            ),
            "no name": (
                {**FIREFOX_MANIFEST, "name": 7},
                'manifest.json gives no "name"',
            ),
        }
        for label, (manifest, reason) in cases.items():
            with self.subTest(label):
                path = xpi(self.root / f"{label.replace(' ', '-')}.xpi", manifest)
                refusal = pass_extension.read_xpi(path)
                assert isinstance(refusal, str), refusal
                self.assertTrue(
                    refusal.startswith(f"--firefox-extension {path}"), refusal
                )
                self.assertIn(reason, refusal)

    def test_a_missing_file_is_refused(self) -> None:
        self.assertEqual(
            pass_extension.read_xpi(self.root / "absent.xpi"),
            f"--firefox-extension {self.root / 'absent.xpi'} is not a file",
        )

    def test_a_file_that_is_not_a_zip_is_refused(self) -> None:
        path = self.root / "plain.xpi"
        path.write_text("not a zip", encoding="utf-8", newline="\n")
        self.assertEqual(
            pass_extension.read_xpi(path),
            f"--firefox-extension {path} is not an .xpi (a zip archive)",
        )

    def test_an_oversized_manifest_is_refused_unread(self) -> None:
        path = xpi(
            self.root / "big.xpi", " " * (pass_extension.MANIFEST_LIMIT_BYTES + 1)
        )
        refusal = pass_extension.read_xpi(path)
        assert isinstance(refusal, str), refusal
        self.assertIn("manifest.json is larger than", refusal)

    def test_a_manifest_bigger_than_its_archive_declares_is_refused(self) -> None:
        # The bound holds on the bytes read, not on the size the archive declares: here the
        # archive's own answer understates it, and the manifest is otherwise valid.
        padded = (
            json.dumps(FIREFOX_MANIFEST) + " " * pass_extension.MANIFEST_LIMIT_BYTES
        )
        path = xpi(self.root / "understated.xpi", padded)
        with mock.patch.object(zipfile, "ZipFile", _UnderstatedSizes):
            refusal = pass_extension.read_xpi(path)
        assert isinstance(refusal, str), refusal
        self.assertIn("manifest.json is larger than", refusal)

    def test_a_central_directory_understating_a_manifest_is_refused(self) -> None:
        # A real archive whose central directory declares the manifest smaller than it is:
        # the standard library's reader stops at the declared size and fails its checksum.
        path = xpi(
            self.root / "lying.xpi", " " * (pass_extension.MANIFEST_LIMIT_BYTES + 1)
        )
        data = bytearray(path.read_bytes())
        entry = data.rfind(_CENTRAL_DIRECTORY_ENTRY)
        struct.pack_into("<I", data, entry + _UNCOMPRESSED_SIZE_OFFSET, 2)
        path.write_bytes(bytes(data))
        refusal = pass_extension.read_xpi(path)
        assert isinstance(refusal, str), refusal
        self.assertTrue(refusal.startswith(f"--firefox-extension {path}"), refusal)


class PlanTests(_Scratch):
    """`plan_isolation`: which engines may run, with which extension, under which guard."""

    def setUp(self) -> None:
        super().setUp()
        self.chromium = unpacked(self.root / "ext")
        self.firefox = xpi(self.root / "fixture.xpi")

    def _plan(
        self,
        engines: tuple[str, ...],
        *,
        chromium: Path | None = None,
        firefox: Path | None = None,
        loopback_only: bool = False,
        base_url: str | None = None,
    ) -> Isolation | str:
        return pass_extension.plan_isolation(
            engines,
            chromium=chromium,
            firefox=firefox,
            loopback_only=loopback_only,
            base_url=base_url,
        )

    def test_no_option_is_no_isolation(self) -> None:
        self.assertEqual(self._plan(_ALL), pass_extension.NO_ISOLATION)
        self.assertFalse(pass_extension.NO_ISOLATION.loopback_only)
        self.assertEqual(pass_extension.NO_ISOLATION.facts, ())

    def test_loopback_only_alone_guards_every_engine_webkit_included(self) -> None:
        plan = self._plan(_ALL, loopback_only=True)
        self.assertEqual(plan, Isolation(loopback_only=True))

    def test_both_extensions_imply_the_guard(self) -> None:
        plan = self._plan(
            ("chromium", "firefox"), chromium=self.chromium, firefox=self.firefox
        )
        assert isinstance(plan, Isolation), plan
        self.assertTrue(plan.loopback_only)
        self.assertEqual(
            plan.facts,
            (
                ExtensionFacts("chromium", "Fixture", "2.5.1"),
                ExtensionFacts("firefox", "Fixture", "4.9", GECKO_ID),
            ),
        )
        chromium = plan.extension("chromium")
        assert chromium is not None
        self.assertEqual(chromium.path, self.chromium.resolve())
        self.assertIsNone(plan.extension("webkit"))

    def test_an_engine_with_no_extension_is_refused_naming_it(self) -> None:
        self.assertEqual(
            self._plan(("chromium", "firefox"), chromium=self.chromium),
            "--engines names firefox, which has no extension: pass --firefox-extension, "
            "or leave firefox out of --engines",
        )

    def test_webkit_with_an_extension_is_refused(self) -> None:
        self.assertEqual(
            self._plan(("chromium", "webkit"), chromium=self.chromium),
            "--engines names webkit, which cannot load an extension: leave webkit out of "
            "--engines to measure with one",
        )

    def test_an_extension_for_an_engine_not_run_is_refused(self) -> None:
        self.assertEqual(
            self._plan(("chromium",), chromium=self.chromium, firefox=self.firefox),
            "--firefox-extension is given, but --engines does not name firefox",
        )

    def test_a_bad_extension_is_refused_first(self) -> None:
        refusal = self._plan(("chromium",), chromium=self.root / "absent")
        assert isinstance(refusal, str)
        self.assertIn("is not a folder holding manifest.json", refusal)
        refusal = self._plan(("firefox",), firefox=self.root / "absent.xpi")
        assert isinstance(refusal, str)
        self.assertIn("is not a file", refusal)

    def test_a_base_url_off_loopback_cannot_be_guarded(self) -> None:
        refusal = (
            "--loopback-only lets through 127.0.0.1 alone, and the targets file's "
            "base_url is on localhost: pass --serve"
        )
        off_loopback = "http://localhost:8123"
        self.assertEqual(
            self._plan(("chromium",), loopback_only=True, base_url=off_loopback),
            refusal,
        )
        # An extension implies the guard, so it is refused the same way.
        self.assertEqual(
            self._plan(("chromium",), chromium=self.chromium, base_url=off_loopback),
            refusal,
        )

    def test_a_base_url_on_loopback_is_guarded(self) -> None:
        plan = self._plan(
            ("chromium",), loopback_only=True, base_url="http://127.0.0.1:8123"
        )
        self.assertEqual(plan, Isolation(loopback_only=True))

    def test_a_guarded_base_url_with_no_valid_port_is_refused(self) -> None:
        # The guard admits the served origin's port alone, so it must be one.
        base_url = "http://127.0.0.1:99999"
        self.assertEqual(
            self._plan(("chromium",), loopback_only=True, base_url=base_url),
            f"--loopback-only lets through the served origin alone, and the targets "
            f"file's base_url names no valid port: {base_url}",
        )

    def test_without_the_guard_a_base_url_is_never_judged(self) -> None:
        self.assertEqual(
            self._plan(("webkit",), base_url="http://localhost:8123"),
            pass_extension.NO_ISOLATION,
        )


class FactsLineTests(unittest.TestCase):
    """The report's header lines: the guard, then each engine's extension."""

    def test_no_guard_says_off_loopback_loads_were_allowed(self) -> None:
        self.assertEqual(
            pass_extension.isolation_lines(False, ()),
            ["- Off-loopback requests: allowed"],
        )

    def test_the_guard_and_each_extension_are_named(self) -> None:
        self.assertEqual(
            pass_extension.isolation_lines(
                True,
                (
                    ExtensionFacts("chromium", "Fixture", "2.5.1"),
                    ExtensionFacts("firefox", "Fixture", "4.9", GECKO_ID),
                ),
            ),
            [
                "- Off-loopback requests: refused (--loopback-only)",
                "- Extension in chromium: Fixture 2.5.1",
                f"- Extension in firefox: Fixture 4.9 ({GECKO_ID})",
            ],
        )


if __name__ == "__main__":
    unittest.main()
