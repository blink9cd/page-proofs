"""Grouped suite: the live sample (the whole tool, run once over `samples/` on the pinned shell).

A fake browser cannot show what this tool exists to measure: whether a stored mode is read
back, whether an emulated dark scheme reaches the paper, whether a lazy image arrived before
the print, what poppler, pypdf and pdfminer read out of a PDF Chromium really made. So this
module serves a copy of `samples/site` on loopback, runs the CLI over it with
`samples/targets.toml` once, in `setUpModule`, and checks that one run's report and PDFs.
The run is a process of its own, killed at a bound, so a run that hangs fails the module
rather than hanging the suite.
Its first check is the sample's own contract: the report, normalized, is
`samples/expected_report.json` exactly. The pages no sample should carry, and installed
Chrome, are `test_print_pass_live_sites.py`'s.

The run is on the default engine and the default `--timeout`. It skips, naming the install,
where poppler or the headless shell Playwright pins is not installed, or fails instead with
`PAGE_PROOFS_REQUIRE_BROWSERS=1`.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
from _print_pass_live_fixtures import (
    EXIT_CLEAN,
    EXIT_FINDINGS,
    EXPECTED_REPORT,
    PIXEL_WIDTH,
    PYTHON_TIMEOUT_SECONDS,
    REPORT_NAME,
    SAMPLE_README,
    SAMPLE_TARGETS,
    TOOL_DIR,
    at,
    load_json,
    make_run_process,
    pdf_entry,
    problems,
    require_live_engine,
    sample_run_bound_seconds,
    served_sample,
    state_entry,
)

import bounded_run  # the shared runner, a folder beside the tool's, via the CLI's own insert
import print_pass  # the CLI, via the fixtures' insert
from print_engine import EMULATED_DARK_WARNING  # the tool's private src/
from print_judge import running_animations_warning  # as above
from print_report import compare_pdfs  # as above

#: The PDFs the checks read: each state's, backgrounds off.
_DEFAULT_PDF: Final = "pdfs/sample--default--bg-off.pdf"
_EMULATED_DARK_PDF: Final = "pdfs/sample--emulated-dark--bg-off.pdf"

#: The sample page's two paragraphs, as its text layer must carry them.
_PARAGRAPHS: Final = (
    "This page is the print_pass sample.",
    "Its other link goes to example.com.",
)

#: The sample's two links, in the page's order: back to the page itself, at the loopback
#: address and port it was served on, and off the site.
_SAME_ORIGIN_URI: Final = re.compile(r"\Ahttp://127\.0\.0\.1:[0-9]+/\Z")
_LINK: Final = "https://example.com/"

#: The density the sample's image prints at: the page draws the 64-pixel image 64 CSS
#: pixels wide, and a CSS pixel is 1/96 inch.
_IMAGE_PPI: Final = 96

#: The README's command that writes the expected report: a program run with `-c` on a
#: run's report, its output sent to the path after `>`, relative to the repository root.
_REGENERATE_COMMAND: Final = re.compile(r"-c '([^']*)' \"\$OUT/report\.json\" > (\S+)")

#: The sample prints on two Letter pages: its 1400-pixel spacer is taller than the 980 CSS
#: pixels a Letter page holds inside its 1 cm margins, so the image prints on the second.
_PAGES: Final = 2
_LETTER: Final = {"width_pt": 612.0, "height_pt": 792.0, "name": "Letter"}

#: The warning a running animation draws, naming the sample's one.
_SPIN_WARNING: Final = running_animations_warning(("spin",))


@dataclass(frozen=True)
class _SampleRun:
    """The one run of the sample: its exit code and stderr, its out folder, its report."""

    code: int | str | None
    stderr: str
    out: Path
    report: dict[str, object]


#: The module's one run, made by `setUpModule`.
_RUN: list[_SampleRun] = []


def setUpModule() -> None:
    """Require poppler and the engine, then serve a copy of the sample and run the tool once.

    The folder is removed by a module cleanup, which runs even when this raises.
    """
    require_live_engine()
    folder = tempfile.TemporaryDirectory(prefix="print_pass-live-")
    unittest.addModuleCleanup(folder.cleanup)
    root = Path(folder.name)
    out = root / "out"
    outcome = make_run_process(
        served_sample(root),
        out,
        "--targets",
        str(SAMPLE_TARGETS),
        timeout=sample_run_bound_seconds(),
    )
    report_file = out / REPORT_NAME
    if not report_file.is_file():
        raise RuntimeError(
            f"the sample run wrote no report (exit {outcome.code}):\n{outcome.stderr}"
        )
    _RUN.append(_SampleRun(outcome.code, outcome.stderr, out, load_json(report_file)))


def _run() -> _SampleRun:
    """The module's one run of the sample."""
    return _RUN[0]


class SampleReportTest(unittest.TestCase):
    """The sample's report: what it says of each state, and that it is the committed one."""

    def test_the_sample_run_matches_expected_report(self) -> None:
        """The README's command, run on this run's report, writes the committed file exactly.

        Compared as text, not as parsed JSON: `true`, `1` and `1.0` load as equal values,
        so a value whose type drifted would pass a parsed comparison while a regeneration
        rewrote the file. Running the README's own command tests that command too.
        """
        run = _run()
        expected = EXPECTED_REPORT.read_bytes().decode("utf-8")
        findings = at(json.loads(expected), "totals", "findings")
        self.assertEqual(
            run.code, EXIT_FINDINGS if findings else EXIT_CLEAN, run.stderr
        )
        program, root = _regeneration_command()
        answer = bounded_run.run_bounded_bytes(
            [sys.executable, "-I", "-c", program, str(run.out / REPORT_NAME)],
            timeout=PYTHON_TIMEOUT_SECONDS,
            cwd=root,
        )
        self.assertEqual(answer.returncode, 0, answer.stderr)
        self.assertMultiLineEqual(answer.stdout.decode("utf-8"), expected)

    def test_stored_dark_reads_back_dark(self) -> None:
        """The stored mode reaches the page's own script; the default state stores none."""
        stored = at(state_entry(_run().report, "stored-dark"), "read_back")
        self.assertEqual(at(stored, "reads", "data-mode"), ["dark"])
        self.assertEqual(at(stored, "storage", "mode"), "dark")
        default = at(state_entry(_run().report, "default"), "read_back")
        self.assertEqual(at(default, "reads", "data-mode"), ["light"])
        self.assertIsNone(at(default, "storage", "mode"))

    def test_the_lazy_image_was_complete_before_printing(self) -> None:
        """Every state's read-back holds the lazy image complete, at its natural width."""
        for state in ("default", "stored-dark", "emulated-dark", "reduced"):
            with self.subTest(state=state):
                read_back = at(state_entry(_run().report, state), "read_back")
                image = at(read_back, "images", 0)
                self.assertEqual(at(image, "loading"), "lazy")
                self.assertIs(at(image, "complete"), True)
                self.assertEqual(at(image, "natural_width"), PIXEL_WIDTH)

    def test_the_running_animation_is_reported(self) -> None:
        """The default state warns of the spin; the reduced state reads back `reduce`."""
        default = state_entry(_run().report, "default")
        self.assertIn(("warning", _SPIN_WARNING), problems(default))
        reduced = state_entry(_run().report, "reduced")
        self.assertEqual(at(reduced, "read_back", "reduced_motion"), "reduce")
        self.assertEqual(at(default, "read_back", "reduced_motion"), "no-preference")


class SamplePdfTest(unittest.TestCase):
    """The sample's PDFs, as the real readers read them and as their renders compare."""

    def test_emulated_dark_is_flagged_and_prints_differently_from_native_light(
        self,
    ) -> None:
        """The emulated-dark state is warned of, and its print is not the light one."""
        run = _run()
        emulated = state_entry(run.report, "emulated-dark")
        self.assertIn(("warning", EMULATED_DARK_WARNING), problems(emulated))
        default = state_entry(run.report, "default")
        self.assertNotIn(("warning", EMULATED_DARK_WARNING), problems(default))
        comparison = compare_pdfs(
            run.out / _DEFAULT_PDF,
            run.out / _EMULATED_DARK_PDF,
            dpi=print_pass.DEFAULT_DPI,
            timeout=print_pass.DEFAULT_TIMEOUT_SECONDS,
            out_dir=run.out,
        )
        self.assertIs(comparison["identical"], False)

    def test_real_poppler_pypdf_and_pdfminer_read_the_pdf(self) -> None:
        """The default print, as each reader reads it: pages, paper, tags, links, image, text."""
        run = _run()
        pdf = pdf_entry(run.report, _DEFAULT_PDF)
        self.assertEqual(at(pdf, "pages"), _PAGES)
        self.assertEqual(at(pdf, "page_sizes"), [_LETTER] * _PAGES)
        self.assertIs(at(pdf, "metadata", "tagged"), True)
        dates = at(pdf, "metadata", "dates")
        self.assertEqual(dates, {"CreationDate": "utc", "ModDate": "utc"})
        links = _list(pdf, "links")
        self.assertEqual([at(link, "page") for link in links], [1, 1])
        # The raw report names the port: the normalized copy is what masks it.
        self.assertRegex(str(at(links[0], "uri")), _SAME_ORIGIN_URI)
        self.assertEqual(at(links[1], "uri"), _LINK)
        images = _list(pdf, "images", "items")
        self.assertEqual(len(images), 1)
        self.assertEqual(
            (at(images[0], "x_ppi"), at(images[0], "y_ppi")), (_IMAGE_PPI, _IMAGE_PPI)
        )
        text = (run.out / str(at(pdf, "text", "file"))).read_text(encoding="utf-8")
        for paragraph in _PARAGRAPHS:
            self.assertIn(paragraph, text)
        for entry in _list(run.report, "pdfs"):
            with self.subTest(pdf=at(entry, "file")):
                self.assertEqual(at(entry, "privacy"), [])


def _regeneration_command() -> tuple[str, Path]:
    """The README's regeneration program, and the folder it runs from: the repository root.

    The root is the folder from which the README's output path names the committed expected
    report, so a README writing any other file finds no root, and fails.
    """
    readme = SAMPLE_README.read_text(encoding="utf-8")
    match = _REGENERATE_COMMAND.search(readme)
    if match is None:
        raise AssertionError("the README holds no command writing the expected report")
    program, target = match.groups()
    expected = EXPECTED_REPORT.resolve()
    roots = [
        folder for folder in TOOL_DIR.parents if (folder / target).resolve() == expected
    ]
    if not roots:
        raise AssertionError(f"the README writes {target}, not the expected report")
    return program, roots[0]


def _list(value: object, *keys: str) -> list[object]:
    """The JSON array under `keys`."""
    return cast(list[object], at(value, *keys))


if __name__ == "__main__":
    unittest.main()
