"""Grouped suite: the live run on installed Chrome (a real dark setting, and lazy slides).

The sample page, with a carousel of lazy slides added to its body, is printed once on
installed Google Chrome in three states: the host's own scheme, a real dark setting, and an
emulated light scheme. Its cases share that one run, because every live run's bound is spent
from the aggregator's own ten-minute budget (`_live_run_bounds()`, in
`test_print_pass_live_sites.py`, counts it). The run is a process of its own, killed at its
bound, so a hang fails the cases that read it rather than hanging the suite.

Installed Chrome is where every lazy slide clipped off the window was measured to stay
unfetched, in a race the page's own script wins most of the time: the `native-dark` state
launches it whatever the engine, so the slides are checked in each of its states.

It skips, naming the install, where poppler or the headless shell Playwright pins is not
installed, or fails instead with `PAGE_PROOFS_REQUIRE_BROWSERS=1`; and it SKIPS on a host
without installed Google Chrome.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar, Final, cast

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
from _print_pass_live_fixtures import (
    CAROUSEL_FINDINGS,
    CHROME_OPTIONS,
    CHROME_TARGETS,
    EXIT_FINDINGS,
    REPORT_NAME,
    at,
    chrome_run_bound_seconds,
    load_json,
    make_run_process,
    problems,
    require_live_engine,
    served_sample,
    state_entry,
    with_carousel,
)

from _print_pass_fixtures import Outcome  # this folder's fixtures, as above
import print_pass  # the CLI, via the fixtures' insert
from print_report import compare_pdfs  # the tool's private src/

#: Where Playwright looks for installed Google Chrome, its `chrome` channel, per platform:
#: its own executable lookup, restated because Playwright exposes it only by launching.
_CHROME_EXECUTABLES: Final = {
    "darwin": ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",),
    "linux": ("/opt/google/chrome/chrome",),
}
_WINDOWS_CHROME: Final = ("Google", "Chrome", "Application", "chrome.exe")
_WINDOWS_ROOTS: Final = ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")

#: The states the run prints, each on installed Chrome.
_STATES: Final = ("default", "native-dark", "light")


def setUpModule() -> None:
    """Skip every case here, naming the install, without poppler or the real engine."""
    require_live_engine()


def _installed_chrome() -> bool:
    """Whether Google Chrome is installed where Playwright's `chrome` channel looks for it."""
    if sys.platform == "win32":
        roots = (os.environ.get(name) for name in _WINDOWS_ROOTS)
        return any(Path(root, *_WINDOWS_CHROME).is_file() for root in roots if root)
    return any(
        Path(path).is_file() for path in _CHROME_EXECUTABLES.get(sys.platform, ())
    )


@unittest.skipUnless(_installed_chrome(), "installed Google Chrome is not present")
class NativeDarkTest(unittest.TestCase):
    """Installed Chrome: the page reads dark and prints light, and every lazy slide arrives."""

    outcome: ClassVar[Outcome]
    out: ClassVar[Path]
    report: ClassVar[dict[str, object]]

    @classmethod
    def setUpClass(cls) -> None:
        """Serve the sample with its carousel and run the tool over it once, on Chrome.

        The folder is removed by a class cleanup, which runs even when this raises.
        """
        folder = tempfile.TemporaryDirectory(prefix="print_pass-live-")
        cls.addClassCleanup(folder.cleanup)
        root = Path(folder.name)
        cls.out = root / "out"
        targets = root / "targets.toml"
        targets.write_text(CHROME_TARGETS, encoding="utf-8", newline="\n")
        site = served_sample(root)
        with_carousel(site / "index.html")
        cls.outcome = make_run_process(
            site,
            cls.out,
            "--targets",
            str(targets),
            *CHROME_OPTIONS,
            timeout=chrome_run_bound_seconds(),
        )
        report_file = cls.out / REPORT_NAME
        if not report_file.is_file():
            raise RuntimeError(
                f"the run wrote no report (exit {cls.outcome.code}):\n"
                f"{cls.outcome.stderr}"
            )
        cls.report = load_json(report_file)

    def test_native_dark_on_installed_chrome_prints_like_light(self) -> None:
        """Every state on Chrome; native dark reads dark, and prints as default and light do.

        Chrome's default state reads the host's own scheme, which may itself be dark, so
        the emulated light state is the light print the native-dark one must also match.
        """
        for name in _STATES:
            self.assertEqual(
                at(state_entry(self.report, name), "engine"), "chrome", name
            )
        native = state_entry(self.report, "native-dark")
        self.assertEqual(at(native, "read_back", "color_scheme"), "dark")
        light = state_entry(self.report, "light")
        self.assertEqual(at(light, "read_back", "color_scheme"), "light")
        for other in ("default", "light"):
            with self.subTest(compared_to=other):
                comparison = compare_pdfs(
                    self.out / "pdfs" / f"sample--{other}--bg-off.pdf",
                    self.out / "pdfs" / "sample--native-dark--bg-off.pdf",
                    dpi=print_pass.DEFAULT_DPI,
                    timeout=print_pass.DEFAULT_TIMEOUT_SECONDS,
                    out_dir=self.out,
                )
                self.assertIs(comparison["identical"], True)

    def test_lazy_slides_far_outside_the_window_arrive_in_every_state(self) -> None:
        """Each state's only findings are the missing slide's: every served slide arrived.

        The engine widens Chromium's lazy margin at every launch, so no slide is read still
        loading, and the one no site serves is still a finding, broken. Those findings are
        the run's only ones (the totals count no other, and no PDF holds one), so it
        exits with findings for them alone.
        """
        self.assertEqual(self.outcome.code, EXIT_FINDINGS, self.outcome.stderr)
        for name in _STATES:
            with self.subTest(state=name):
                found = problems(state_entry(self.report, name))
                findings = [problem for problem in found if problem[0] == "finding"]
                self.assertCountEqual(findings, CAROUSEL_FINDINGS)
        self.assertEqual(
            at(self.report, "totals", "findings"),
            len(_STATES) * len(CAROUSEL_FINDINGS),
        )
        pdfs = cast(list[dict[str, object]], self.report["pdfs"])
        for pdf in pdfs:
            with self.subTest(pdf=pdf["file"]):
                self.assertNotIn("finding", [severity for severity, _ in problems(pdf)])


if __name__ == "__main__":
    unittest.main()
