"""Shared fixtures for lighthouse_run's grouped suites: reports, fake executables, a project.

Nothing here reaches the host's Chrome, its Lighthouse or any network beyond loopback. The
fake executables are real processes (Python scripts whose shebang is the interpreter
running the suite), so the runner's subprocess boundary is crossed for real while every
behavior on the far side stays scripted through environment variables. The audit reports
under `fixtures/` are real ones, minimized and sanitized: see `AUDIT_REPORTS`.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: The absolute microsecond timestamp the fake report's network clock starts at. Only
#: differences matter; the size matches what a real trace carries.
NETWORK_START_TS = 381_635_913_081

#: First contentful paint, in milliseconds after the first request, as observed on a site.
FCP_OFFSET_MS = 60.0

#: Font bytes that finished before paint in the default report: the two first-paint faces.
#: 67,914 is the figure a real measurement of those two faces recorded, so a join that
#: reproduces it here is the join that measured it there.
FONT_BYTES_BEFORE_FCP = 32_953 + 34_961

#: The default pages every run serves, as files under `src/`.
PAGE_FILES: Mapping[str, str] = {
    "index.html": "<!doctype html><title>home</title>\n",
    "archive/index.html": "<!doctype html><title>archive</title>\n",
    "404.html": "<!doctype html><title>not found</title>\n",
}

#: Appended to every page of a built site, so a served body names the tree it came from.
PACKAGE_MARK = "<!-- package -->\n"

#: A fake Lighthouse. It fetches the url it was given, so a run proves the server was live,
#: logs its argv, the Chrome it was handed and the page body it was served (so a test can
#: tell which tree answered), then behaves as `FAKE_LH_MODE` says.
FAKE_LIGHTHOUSE = """
import json, os, sys, time, urllib.request
args = sys.argv[1:]
out = next(a.split("=", 1)[1] for a in args if a.startswith("--output-path="))
with urllib.request.urlopen(args[0], timeout=10) as response:
    status = response.status
    body = response.read().decode("utf-8")
with open(os.environ["FAKE_LH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": args, "chrome": os.environ.get("CHROME_PATH"),
                          "status": status, "body": body}) + "\\n")
mode = os.environ.get("FAKE_LH_MODE", "ok")
if mode == "fail-desktop" and "--preset=desktop" in args:
    sys.stderr.write("Launching Chrome\\nChrome crashed\\n")
    sys.exit(3)
if mode == "hang":
    while True:
        time.sleep(1)
with open(out, "w", encoding="utf-8") as sink:
    if mode == "garbage":
        sink.write("not json")
    else:
        with open(os.environ["FAKE_LH_REPORT"], encoding="utf-8") as source:
            sink.write(source.read())
"""

#: A fake `node`: prints the Chrome path it is told to, or fails as `FAKE_NODE_MODE` says.
FAKE_NODE = """
import os, sys
if os.environ.get("FAKE_NODE_MODE") == "fail":
    sys.stderr.write("cannot resolve chrome-launcher\\n")
    sys.exit(1)
sys.stdout.write(os.environ.get("FAKE_CHROME_PATH", ""))
"""

#: A fake Chrome that answers `--version`, or fails as `FAKE_CHROME_MODE` says.
FAKE_CHROME = """
import os, sys
if os.environ.get("FAKE_CHROME_MODE") == "fail":
    sys.exit(1)
print("Fake Chrome 999.0.0")
"""

#: What the fake Chrome prints.
FAKE_CHROME_VERSION = "Fake Chrome 999.0.0"

#: The commit the fake `git` reports as HEAD.
FAKE_COMMIT = "0123456789abcdef0123456789abcdef01234567"

#: A fake `git`, installed for every whole run so none reaches the host's. It logs each call
#: together with the folder it ran in and how many Lighthouse runs had been made when it was
#: asked (so a test can tell which tree the commit was read for, and when), then answers as
#: `FAKE_GIT_MODE` says: `clean` (the default), `dirty` (`status` lists an edit),
#: `fail-status` (HEAD resolves, `status` fails), `outside` (every call exits 128, as git does
#: in a folder no repository holds) or `hang`.
FAKE_GIT = """
import json, os, sys, time
runs_path = os.environ["FAKE_LH_LOG"]
runs = 0
if os.path.exists(runs_path):
    with open(runs_path, encoding="utf-8") as runs_log:
        runs = len(runs_log.readlines())
with open(os.environ["FAKE_GIT_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
                          "runs_before": runs}) + "\\n")
mode = os.environ.get("FAKE_GIT_MODE", "clean")
if mode == "hang":
    while True:
        time.sleep(1)
if mode == "outside":
    sys.stderr.write("fatal: not a git repository\\n")
    sys.exit(128)
if "rev-parse" in sys.argv:
    print("@COMMIT@")
elif mode == "dirty":
    print(" M index.html")
elif mode == "fail-status":
    sys.stderr.write("fatal: unable to read the index\\n")
    sys.exit(128)
""".replace("@COMMIT@", FAKE_COMMIT)


def font_request(transfer_size: float, end_ms: float) -> dict[str, object]:
    """One `Font` row of the network-requests audit, finished `end_ms` after the first request."""
    return {
        "resourceType": "Font",
        "transferSize": transfer_size,
        "networkEndTime": end_ms,
    }


def other_request(
    transfer_size: float, end_ms: float, resource_type: str = "Script"
) -> dict[str, object]:
    """One non-font row of the network-requests audit."""
    return {
        "resourceType": resource_type,
        "transferSize": transfer_size,
        "networkEndTime": end_ms,
    }


def default_requests() -> list[dict[str, object]]:
    """The document, the two first-paint faces, and the italic that arrives after paint."""
    return [
        other_request(20_542, 3.6, "Document"),
        font_request(32_953, 54.95),
        font_request(34_961, 54.55),
        font_request(26_325, 125.28),
    ]


def fake_report(
    *,
    score: object = 0.9,
    fcp_ms: object = 2710.0,
    lcp_ms: object = 2860.0,
    tbt_ms: object = 0.0,
    cls: object = 0.0001,
    total_bytes: object = 884_981.0,
    requests: list[dict[str, object]] | None = None,
    version: object = "13.4.1",
) -> dict[str, object]:
    """A Lighthouse 13 report carrying only the fields the runner reads, shaped as the CLI writes them."""
    return {
        "lighthouseVersion": version,
        "categories": {"performance": {"score": score}},
        "audits": {
            "first-contentful-paint": {"numericValue": fcp_ms},
            "largest-contentful-paint": {"numericValue": lcp_ms},
            "total-blocking-time": {"numericValue": tbt_ms},
            "cumulative-layout-shift": {"numericValue": cls},
            "total-byte-weight": {"numericValue": total_bytes},
            "metrics": {
                "details": {
                    "items": [
                        {
                            "observedFirstContentfulPaintTs": NETWORK_START_TS
                            + FCP_OFFSET_MS * 1000
                        }
                    ]
                }
            },
            "network-requests": {
                "details": {
                    "items": default_requests() if requests is None else requests,
                    "debugData": {"networkStartTimeTs": NETWORK_START_TS},
                }
            },
        },
    }


def write_script(path: Path, body: str) -> Path:
    """Write `body` as an executable Python script run by this suite's own interpreter.

    Returns the path that launches it, which `launch_path` says.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return launch_path(path)


def launch_path(script: Path) -> Path:
    """The path that runs `script`: itself on POSIX, a `.cmd` shim beside it on Windows.

    Windows honors no shebang, so CreateProcess refuses the script (`[WinError 193]`), and its
    PATH lookup tries only names carrying a PATHEXT extension, so a bare stand-in there is
    passed over for the real tool further down PATH. The shim runs the script with this
    interpreter, the way npm puts a `.cmd` beside each script in `node_modules/.bin`.
    """
    if sys.platform == "win32":  # pragma: only-on win32
        shim = script.with_name(f"{script.name}.cmd")
        shim.write_text(
            f'@"{sys.executable}" "%~dp0{script.name}" %*\n',
            encoding="utf-8",
            newline="\r\n",
        )
        return shim
    return script  # pragma: only-on posix


def write_pages(src: Path) -> None:
    """Write the default pages under `src`."""
    for relative, text in PAGE_FILES.items():
        target = src / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


def make_package(folder: Path) -> Path:
    """A built package outside the project: the default pages, each ending in `PACKAGE_MARK`."""
    write_pages(folder)
    for relative in PAGE_FILES:
        with (folder / relative).open("a", encoding="utf-8", newline="\n") as page:
            page.write(PACKAGE_MARK)
    return folder


def make_project(root: Path) -> Path:
    """A throwaway web project: the default pages and a fake pinned Lighthouse."""
    write_pages(root / "src")
    write_script(root / "node_modules" / ".bin" / "lighthouse", FAKE_LIGHTHOUSE)
    return root


def fake_toolchain(bin_dir: Path) -> dict[str, str]:
    """Fake `node` and Chrome in `bin_dir`; returns the environment that puts them first."""
    chrome = write_script(bin_dir / "fake-chrome", FAKE_CHROME)
    write_script(bin_dir / "node", FAKE_NODE)
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_CHROME_PATH": str(chrome),
    }


def fake_git(bin_dir: Path, log: Path) -> dict[str, str]:
    """A fake `git` in `bin_dir` (already first on PATH) logging to `log`; returns its env."""
    write_script(bin_dir / "git", FAKE_GIT)
    return {"FAKE_GIT_LOG": str(log)}


#: Real Lighthouse 13.4.1 reports of a built site, measured on loopback (the home page
#: on both presets and the 404 page on desktop), because a shape guessed from documentation is
#: exactly what a fake cannot catch. Each keeps the report's version and urls and the audits
#: `lighthouse_audits` reads; Lighthouse's UI strings and every field carrying page text
#: (`nodeLabel`, `snippet`) are dropped, the loopback origin is rewritten to `AUDIT_ORIGIN`,
#: each third-party url is replaced by a name under `example.invalid`, and every asset path,
#: feature name and selector is a neutral demo name.
AUDIT_REPORTS = Path(__file__).resolve().parent / "fixtures"

#: The page origin every audit report was measured at.
AUDIT_ORIGIN = "http://127.0.0.1:8000"

#: The home page's LCP element: an image on desktop, a paragraph on mobile.
IMAGE_SELECTOR = "div.panel > figure.photo > div.frame > img.slide"
TEXT_SELECTOR = "main#main > section#intro > div.lead__text > p"

#: Three desktop rounds of the home page, measured one after another.
HOME_DESKTOP_ROUNDS = ("home-desktop-run1", "home-desktop-run2", "home-desktop-run3")

#: What a report holding none of the audits is recorded as: every field unknown, none refused.
NO_AUDITS: dict[str, Any] = {
    "render_blocking": {"fcp_saving_ms": None, "lcp_saving_ms": None, "requests": None},
    "dependency_chain": {"longest_ms": None, "depth": None},
    "unused_css_bytes": None,
    "lcp": {"selector": None, "subparts": None, "discovery": None},
    "cache": {"wasted_bytes": None, "first_party_wasted_bytes": None},
    "document_latency": {
        "server_response_ms": None,
        "redirect_ms": None,
        "uncompressed_bytes": None,
        "uses_compression": None,
    },
}


def audit_report(name: str) -> dict[str, Any]:
    """One report under `AUDIT_REPORTS`, loaded afresh so a test may break it."""
    text = (AUDIT_REPORTS / f"{name}.json").read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def report_details(report: Mapping[str, Any], audit: str) -> dict[str, Any]:
    """One audit's `details` in a loaded report, to read or edit in place."""
    found: dict[str, Any] = report["audits"][audit]["details"]
    return found


def recorded(value: object) -> Any:
    """`value` as `summary.json` records it: through a JSON round trip."""
    return json.loads(json.dumps(value))
