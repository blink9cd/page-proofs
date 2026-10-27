"""Shared fixtures for browser_pass's extension suites: extensions written into a scratch folder.

`test_pass_extension.py` reads and validates them, `test_pass_extension_launch.py` launches them
through a fake Playwright, and `test_pass_cli_extension.py` hands them to the CLI. Each is written
fresh per test, so a suite can break one (drop its manifest, its gecko id, its worker) without
touching the committed fixture the live suite loads, `fixture_extension/`.

Entry point assumptions: the standard library only; a caller puts the tool's `src/` on `sys.path`
itself if it needs it.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Mapping
from pathlib import Path

#: The committed MV3 fixture the live Chromium suite loads.
FIXTURE_EXTENSION = Path(__file__).resolve().parent / "fixture_extension"

#: A Firefox add-on id as a signed one carries it.
GECKO_ID = "fixture@browser-pass.invalid"

#: A manifest for an unpacked Chromium extension: named, versioned, with a service worker.
CHROMIUM_MANIFEST: Mapping[str, object] = {
    "manifest_version": 3,
    "name": "Fixture",
    "version": "2.5.1",
    "background": {"service_worker": "bg/worker.js"},
}

#: A manifest for a Firefox add-on: named, versioned, with its gecko id.
FIREFOX_MANIFEST: Mapping[str, object] = {
    "manifest_version": 2,
    "name": "Fixture",
    "version": "4.9",
    "browser_specific_settings": {"gecko": {"id": GECKO_ID}},
}


def unpacked(
    folder: Path,
    manifest: Mapping[str, object] | str | None = CHROMIUM_MANIFEST,
    *,
    files: Mapping[str, str] | None = None,
) -> Path:
    """Write an unpacked extension into `folder`: its manifest (JSON, or raw text), and `files`.

    A `manifest` of None writes none.
    """
    folder.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        text = manifest if isinstance(manifest, str) else json.dumps(manifest)
        (folder / "manifest.json").write_text(text, encoding="utf-8", newline="\n")
    for name, text in (files or {}).items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return folder


def xpi(
    path: Path,
    manifest: Mapping[str, object] | str | None = FIREFOX_MANIFEST,
    *,
    files: Mapping[str, str] | None = None,
) -> Path:
    """Write an `.xpi` (a zip archive) at `path` holding `manifest` and `files`."""
    with zipfile.ZipFile(path, "w") as archive:
        if manifest is not None:
            text = manifest if isinstance(manifest, str) else json.dumps(manifest)
            archive.writestr("manifest.json", text)
        for name, text in (files or {}).items():
            archive.writestr(name, text)
    return path
