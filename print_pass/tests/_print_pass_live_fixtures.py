"""Shared by print_pass's live suites: the real engine required, a site served, a run made.

`test_print_pass_live.py` runs the committed sample through the whole tool once and checks
what it reports; `test_print_pass_live_sites.py` runs the page no sample should carry -- an
image and two WebSockets off loopback, motion in shadow trees, and a carousel of lazy slides
-- and `test_print_pass_live_chrome.py` the sample, with that carousel, on installed Chrome
with a real dark setting; `test_print_pass_live_redirects.py` runs a meta-refresh stub,
declared and undeclared, at a short timeout of its own. What they share lives here: the
check that the real engine is there at all, the PNG the sample page draws (generated, since
no binary is committed), the carousel and what it fails a state with, the Chrome run's
targets and options, the served copy of a site, a make run -- always in a process of its
own, killed at a bound, so a run that hangs fails its case rather than hanging the suite --
and the lookups into the report it writes. `test_print_pass_live_firefox.py` makes its run
through the same `make_run_process`, given an environment of its own, and serves its page
the same way.

Without poppler's readers, or without the headless shell Playwright pins, `require_live_engine`
skips the live case, naming the install in the skip and in one line on stderr, so the suites
run on a machine without them; with `PAGE_PROOFS_REQUIRE_BROWSERS=1` it fails instead, so a
run that must measure the engine can never pass with it unmeasured. Playwright itself missing
always fails: the test extra installs it. The Firefox engine needs none of Playwright, so the
Firefox suite asks for what it needs itself, poppler through `require_poppler`.

Entry point assumptions: imported by a suite that unittest discovery loaded with this folder
as its top-level directory; the bootstrap below puts the tool's folder and its private
`src/` on the path, and `print_pass`'s own bootstrap adds the shared utilities.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tomllib
import unittest
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

TOOL_DIR: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL_DIR / "src"))
sys.path.insert(0, str(TOOL_DIR))
import print_pass  # noqa: E402, F401 - the CLI, whose bootstrap adds the shared utilities

import bounded_run  # noqa: E402 - a folder beside the tool's, via the CLI's own insert
import print_preflight  # noqa: E402 - the tool's private src/, via the insert above
from _print_pass_fixtures import Outcome  # noqa: E402 - this folder's fixtures
from print_targets import (  # noqa: E402 - the tool's private src/
    TargetsFile,
    load_targets,
    parse_targets,
)

#: The committed sample: its site, its targets and the report it must normalize to.
SAMPLES: Final = TOOL_DIR / "samples"
SAMPLE_SITE: Final = SAMPLES / "site"
SAMPLE_TARGETS: Final = SAMPLES / "targets.toml"
EXPECTED_REPORT: Final = SAMPLES / "expected_report.json"
SAMPLE_README: Final = SAMPLES / "README.md"

#: What installs the engine build a make run prints with.
INSTALL_COMMAND: Final = "python -m playwright install --only-shell chromium"

#: Set to `1`, a live case FAILS where the engine build or poppler it needs is absent, rather
#: than skipping: a skip is a passing run, so a run that must measure the engine sets it.
#: Unset or empty is off, and any other value is refused.
REQUIRE_BROWSERS_ENV: Final = "PAGE_PROOFS_REQUIRE_BROWSERS"

#: What opens the line a skipping live case writes to stderr.
SKIP_NOTICE: Final = "live suite skipped: "

#: The image the sample page draws: its name beside the page, and its size in pixels.
PIXEL_NAME: Final = "pixel.png"
PIXEL_WIDTH: Final = 64
PIXEL_HEIGHT: Final = 32

#: The report a make run writes into its out folder.
REPORT_NAME: Final = "report.json"

#: The two exit codes a run that measured can end with.
EXIT_CLEAN: Final = 0
EXIT_FINDINGS: Final = 1

#: The sites suite's motion in shadow trees, by script, since its page's policy refuses inline
#: styles: an open root, an open root nested in it, and a closed root, each running one
#: endless animation named by its id.
SHADOW_SCRIPT: Final = """const root = (mode, parent) =>
  parent.appendChild(document.createElement("span")).attachShadow({ mode });
const spin = (tree, id) => tree.appendChild(document.createElement("i"))
  .animate([{ opacity: 0 }, { opacity: 1 }], { id, duration: 1000, iterations: Infinity });
const open = root("open", document.body);
spin(open, "shadow-open");
spin(root("open", open), "shadow-nested");
spin(root("closed", document.body), "shadow-closed");"""

#: The lazy slide no site serves, so its request is answered 404.
MISSING_SLIDE: Final = "/missing-slide.png"

#: A column of lazy slides that its script turns into a horizontal scroller after parsing,
#: as a carousel does, each slide 4,000 px to the right of the one before, so all but the
#: first sit clipped far outside the window. Styled by script, never by an attribute or a
#: `<style>`, so a page whose policy refuses inline styles carries it unrefused. Every slide
#: but the missing one is the sample's image, each at an address of its own, so no slide is
#: complete from another's fetch. Chromium's own lazy margin never reached them (measured,
#: the shell and installed Chrome alike: each read incomplete at the print).
CAROUSEL_HTML: Final = f"""<div id="slides">
<div><img src="{PIXEL_NAME}?slide=1" alt="slide 1" loading="lazy" width="64" height="32"></div>
<div><img src="{PIXEL_NAME}?slide=2" alt="slide 2" loading="lazy" width="64" height="32"></div>
<div><img src="{PIXEL_NAME}?slide=3" alt="slide 3" loading="lazy" width="64" height="32"></div>
<div><img src="{MISSING_SLIDE}" alt="slide 4" loading="lazy" width="64" height="32"></div>
</div>
<script>Object.assign(document.getElementById("slides").style, {{
  display: "flex", gap: "4000px", overflowX: "auto", width: "600px",
}});</script>
"""

#: What the carousel fails its state with, and all it does: the missing slide, fetched and
#: answered 404, so broken rather than incomplete; every served slide arrived.
CAROUSEL_FINDINGS: Final = (
    ("finding", f"image broken: {MISSING_SLIDE}"),
    ("finding", f"HTTP 404: {MISSING_SLIDE}"),
)

#: The finding a slide the probe found still loading draws, by the start of its text.
NOT_COMPLETE: Final = "image not complete at print"

#: Bound on each fresh interpreter a suite starts to run one of the README's commands.
PYTHON_TIMEOUT_SECONDS: Final = 60.0

# The PNG's fixed parts: the signature, one 8-bit gray channel, and scanlines that open
# with filter byte 0, so each row is its raw bytes. The gray is mid-level.
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_BIT_DEPTH: Final = 8
_GRAYSCALE: Final = 0
_DEFAULT_METHODS: Final = (0, 0, 0)  # compression, filter, interlace
_NO_FILTER: Final = b"\x00"
_MID_GRAY: Final = 0x80

# Where Playwright's own package names the engine builds it pins, and the one it prints with.
_BROWSERS_MANIFEST: Final = ("driver", "package", "browsers.json")
_HEADLESS_SHELL: Final = "chromium-headless-shell"

# Playwright's own default folder for the builds, per platform, used when nothing names one;
# and where in its own package print_preflight's HERMETIC_BROWSERS keeps them.
_DEFAULT_CACHE: Final = "ms-playwright"
_HERMETIC_FOLDER: Final = ("driver", "package", ".local-browsers")

# A live run's bound is one of the run's own `--timeout` for each state it prints, and this
# many beside them, for the engine's launch and the reading of every PDF the states made.
# It bounds a hang, not a slow run: a healthy run of the whole sample took up to 31.1 s at
# load averages of 40 to 130, about one timeout. It is kept that tight for the aggregator's
# own ten-minute budget for the one process that runs every suite here: the live runs'
# bounds must fit inside it together, beside the other suites, so that a CI or coverage
# timeout of that length still sees each hung case fail by name.
# `test_print_pass_live_sites.py` checks that they fit.
_BOUND_BESIDE_STATES: Final = 1

#: How many times the default `--timeout` the Chrome case runs at. Installed Chrome's
#: `browser.close` is the slowest call in any live run, and a heavily loaded machine
#: is not the test's to rule out. Measured on 10 cores: a close took under
#: half a second at a load average below 15, but as the load climbed from about 30 to above
#: 70 some took 30.1 to 30.2 s, and none took longer, up to a load of 161; one whole run
#: that made two such closes took 83.2 s. A close takes no timeout of its own, so the
#: engine bounds it at twice the run's `--timeout`, which clears the longest even at the
#: default. The case raises its timeout all the same for its OUTER bound, a timeout per
#: state and one beside, which a run making two such closes on a loaded machine spends much
#: of. The case tests that native dark prints like light, not how fast Chrome closes, and
#: `--timeout` is the tool's own setting for a slow host.
CHROME_TIMEOUT_MULTIPLE: Final = 1.5
CHROME_TIMEOUT_SECONDS: Final = (
    CHROME_TIMEOUT_MULTIPLE * print_pass.DEFAULT_TIMEOUT_SECONDS
)

#: The sample page in three states on installed Chrome: the host's own scheme, a real dark
#: setting, and an emulated light scheme -- the light print a real dark one is compared to.
CHROME_TARGETS: Final = """[[target]]
name = "sample"
page = "/"

[[target.state]]
name = "default"

[[target.state]]
name = "native-dark"
color_scheme = "native-dark"

[[target.state]]
name = "light"
color_scheme = "light"
"""

#: What the Chrome case runs the tool with, beside its site, its out folder and its
#: targets file: installed Chrome's engine, at the Chrome case's own timeout.
CHROME_OPTIONS: Final = (
    "--engine",
    "chrome",
    "--timeout",
    str(CHROME_TIMEOUT_SECONDS),
)


#: The redirect run's site: `archive/` is the archive stub, a refresh at once to the year page
#: beside it, which is a page with nothing to fetch.
REDIRECT_PAGES: Final = {
    "archive/index.html": (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="0; url=2026/"><title>archive</title></head>'
        "<body></body></html>\n"
    ),
    "archive/2026/index.html": (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>2026</title></head><body><p>The year page.</p></body></html>\n"
    ),
}

#: The redirect run's targets: the stub declared, which prints the year page, and the same
#: stub undeclared, which is a finding and prints nothing.
REDIRECT_TARGETS: Final = """[[target]]
name = "archive"
page = "/archive/"
follows_redirect = true

[[target]]
name = "undeclared"
page = "/archive/"
"""

#: The redirect run's `--timeout`: a fraction of the default, since the run prints one
#: tiny page, and the aggregator's budget for every live run together leaves little beside
#: the others (`test_print_pass_live_sites.py` checks that they fit).
REDIRECT_TIMEOUT_SECONDS: Final = 5.0


def builds_required() -> bool:
    """Whether an absent engine build or poppler fails a live case rather than skip it.

    Unset or empty is off and `1` is on. Any other value is refused, so a value meant as on
    is never read as off.
    """
    value = os.environ.get(REQUIRE_BROWSERS_ENV, "")
    if value not in ("", "1"):
        raise RuntimeError(
            f"{REQUIRE_BROWSERS_ENV} is {value!r}: set it to 1 to require the engine "
            "builds and poppler, or leave it unset or empty"
        )
    return value == "1"


def absent(reason: str) -> Exception:
    """What a live case raises where `reason` is so: a skip, or with the opt-in a failure."""
    if builds_required():
        return RuntimeError(reason)
    # unittest shows a skip's reason only when run verbosely, so a plain run would show a
    # bare count of skips: the line on stderr is what names the install there.
    sys.stderr.write(f"{SKIP_NOTICE}{reason}\n")
    return unittest.SkipTest(reason)


def require_poppler() -> None:
    """Skip, or fail with the opt-in, naming the install, unless poppler's readers are found."""
    builds_required()
    missing = print_preflight.missing_poppler()
    if missing is not None:
        raise absent(missing)


def require_live_engine() -> None:
    """Skip, or fail, unless poppler is found and the shell Playwright pins is installed.

    The shell is looked for where a make run would launch it from: the folder
    `print_preflight.browsers_dir` resolves, else Playwright's own default.

    Raises:
        RuntimeError: Playwright is not installed, which the test extra installs; or with
            `REQUIRE_BROWSERS_ENV` set, poppler or the headless shell is missing.
        unittest.SkipTest: poppler or the headless shell is missing, the opt-in unset.
    """
    if importlib.util.find_spec("playwright") is None:
        raise RuntimeError(
            "Playwright is not installed: pip install '.[test]' installs it, then "
            f"{INSTALL_COMMAND}"
        )
    require_poppler()
    shell = pinned_shell()
    if not shell.is_dir():
        raise absent(
            f"the headless shell Playwright pins is not installed at {shell}; "
            f"`{INSTALL_COMMAND}` installs it"
        )


def pinned_shell() -> Path:
    """The folder the headless shell Playwright pins is installed in, when it is."""
    browsers = print_preflight.browsers_dir(None, os.environ)
    if browsers is not None and str(browsers) == print_preflight.HERMETIC_BROWSERS:
        browsers = _playwright_package().joinpath(*_HERMETIC_FOLDER)
    folder = _playwright_default() if browsers is None else browsers
    return folder / f"{_HEADLESS_SHELL.replace('-', '_')}-{_pinned_revision()}"


def _playwright_package() -> Path:
    """The installed Playwright package's folder."""
    spec = importlib.util.find_spec("playwright")
    if spec is None or spec.origin is None:
        raise RuntimeError(f"Playwright is not installed: {INSTALL_COMMAND}")
    return Path(spec.origin).parent


def _pinned_revision() -> str:
    """The headless shell's revision, as the installed Playwright's own manifest pins it."""
    manifest = _playwright_package().joinpath(*_BROWSERS_MANIFEST)
    browsers = json.loads(manifest.read_text(encoding="utf-8"))["browsers"]
    return next(
        str(build["revision"]) for build in browsers if build["name"] == _HEADLESS_SHELL
    )


def _playwright_default() -> Path:
    """The folder Playwright installs its builds in when no variable names one."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / _DEFAULT_CACHE
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / _DEFAULT_CACHE
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / _DEFAULT_CACHE


def pixel_png() -> bytes:
    """The sample page's image: a mid-gray PNG, `PIXEL_WIDTH` by `PIXEL_HEIGHT`."""
    header = struct.pack(
        ">IIBBBBB",
        PIXEL_WIDTH,
        PIXEL_HEIGHT,
        _BIT_DEPTH,
        _GRAYSCALE,
        *_DEFAULT_METHODS,
    )
    rows = (_NO_FILTER + bytes([_MID_GRAY]) * PIXEL_WIDTH) * PIXEL_HEIGHT
    return (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    """One PNG chunk: its length, its kind, its data, and the CRC of kind and data."""
    length = struct.pack(">I", len(data))
    return length + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def served_sample(root: Path) -> Path:
    """A copy of the sample site under `root`, with the image its page draws beside it."""
    site = root / "site"
    shutil.copytree(SAMPLE_SITE, site)
    (site / PIXEL_NAME).write_bytes(pixel_png())
    return site


def served_pages(root: Path, pages: Mapping[str, str]) -> Path:
    """A site under `root` holding each of `pages`, a path under it to HTML."""
    site = root / "site"
    site.mkdir()
    for name, html in pages.items():
        (site / name).parent.mkdir(parents=True, exist_ok=True)
        (site / name).write_text(html, encoding="utf-8", newline="\n")
    return site


def with_carousel(page: Path) -> None:
    """Add `CAROUSEL_HTML` to the end of the body of the served `page`, in place.

    A page with no `</body>` is left without it, which fails the case reading its findings.
    """
    html = page.read_text(encoding="utf-8")
    page.write_text(
        html.replace("</body>", f"{CAROUSEL_HTML}</body>", 1),
        encoding="utf-8",
        newline="\n",
    )


def run_bound_seconds(targets: TargetsFile, *, timeout: float) -> float:
    """How long a live run over `targets` may take, in seconds, before it counts as hung.

    `timeout` is the run's own `--timeout`, so a case that raises it raises its bound too.
    """
    states = sum(len(target.states) for target in targets.targets)
    return float((states + _BOUND_BESIDE_STATES) * timeout)


def sample_run_bound_seconds() -> float:
    """The bound on the sample's live run: its targets, at the default `--timeout`."""
    return run_bound_seconds(
        load_targets(SAMPLE_TARGETS), timeout=print_pass.DEFAULT_TIMEOUT_SECONDS
    )


def chrome_run_bound_seconds() -> float:
    """The bound on the Chrome case's run: its states, at the timeout its run is given."""
    return run_bound_seconds(
        parse_targets(tomllib.loads(CHROME_TARGETS)), timeout=CHROME_TIMEOUT_SECONDS
    )


def redirect_run_bound_seconds() -> float:
    """The bound on the redirect run: the states it PRINTS, at its own timeout.

    The undeclared target's states are never handed to the engine, so only the declared
    target's are counted.
    """
    targets = parse_targets(tomllib.loads(REDIRECT_TARGETS))
    printed = tuple(target for target in targets.targets if target.follows_redirect)
    return run_bound_seconds(
        dataclasses.replace(targets, targets=printed),
        timeout=REDIRECT_TIMEOUT_SECONDS,
    )


def make_run_process(
    site: Path,
    out: Path,
    *options: str,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> Outcome:
    """Run `--mode make` over `site` into `out` in a process of its own, killed at `timeout`.

    A process of its own, for two reasons: a hang inside a run -- a Playwright dispatcher
    spinning outside every bound the run keeps -- can be ended only from outside it; and a
    run that abandons an overrunning call leaves through `os._exit`, which in this process
    would end the whole suite with no case named. The run's exit code and streams come
    back; its report and PDFs are read from `out`. `env` is the run's whole environment,
    this process's own when None: the Firefox case puts its fenced geckodriver first on
    the run's `PATH` this way.

    Raises:
        AssertionError: the run did not end within `timeout`; its process tree is killed.
    """
    argv = ["--mode", "make", "--serve", str(site), "--out", str(out), *options]
    try:
        answer = bounded_run.run_bounded(
            [sys.executable, str(TOOL_DIR / "print_pass.py"), *argv],
            timeout=timeout,
            cwd=out.parent,
            env=env,
        )
    except subprocess.TimeoutExpired as expired:
        raise AssertionError(f"the run did not end within {timeout} s") from expired
    return Outcome(answer.returncode, answer.stdout, answer.stderr)


def load_json(path: Path) -> dict[str, object]:
    """The JSON object in `path`."""
    loaded: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def at(value: object, *keys: str | int) -> object:
    """The value under `keys` in turn: a name for a JSON object, an index for an array."""
    for key in keys:
        if isinstance(key, int):
            value = cast(list[object], value)[key]
        else:
            value = cast(dict[str, object], value)[key]
    return value


def state_entry(report: Mapping[str, object], name: str) -> dict[str, object]:
    """The report's entry for the state called `name`."""
    states = cast(list[dict[str, object]], report["states"])
    return next(state for state in states if state["state"] == name)


def pdf_entry(report: Mapping[str, object], file: str) -> dict[str, object]:
    """The report's entry for the PDF whose report path is `file`."""
    pdfs = cast(list[dict[str, object]], report["pdfs"])
    return next(pdf for pdf in pdfs if pdf["file"] == file)


def problems(entry: Mapping[str, object]) -> list[tuple[object, object]]:
    """A state's or a PDF's problems, each as (severity, text)."""
    listed = cast(list[dict[str, object]], entry["problems"])
    return [(problem["severity"], problem["what"]) for problem in listed]
