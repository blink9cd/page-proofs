"""Suite: pdf_raster's renders, through a REAL fake `pdftoppm` on `PATH`.

The fake is a Python script whose shebang is this suite's own interpreter, so the subprocess
boundary is crossed for real while the far side stays scripted: it logs its argv and writes
small P5 / P6 / PNG files named as the real tool names them (poppler 26.08, measured:
`stem-1.png` for a 3-page PDF, `stem-01.png` for 12 pages).
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from hypothesis import HealthCheck, settings  # noqa: E402 - position only; keep below the bootstrap above
from pdf_raster import Raster, RasterError, compare_rasters, render_png, render_rasters  # noqa: E402 - position only; keep below the bootstrap above
from _pdf_raster_fixtures import DPI, gray, ink  # noqa: E402 - beside this module, via the loader's top_level_dir

# The determinism convention: a profile registered at import, never an `@settings`
# decorator, which a fuzzing campaign could not override. deadline=None: a per-example
# wall-clock deadline measures machine load, not correctness.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")


_TIMEOUT = 30.0


#: Long enough for the fake to start, far shorter than its hang.
_SHORT_TIMEOUT = 0.5


#: A fake `pdftoppm`. It logs its argv, then behaves as `FAKE_PDFTOPPM_MODE` says: `ok` writes
#: `FAKE_PDFTOPPM_PAGES` pages, zero-padded to the page count's width as the real tool pads
#: them unless `FAKE_PDFTOPPM_PAD` is `no`; `garbage` writes pages that are not PNM; `fail`
#: prints `FAKE_PDFTOPPM_STDERR` and exits 1; `hang` never returns. Page N of a PNM render is
#: 2 x 3 pixels of value N, so a test can read which page landed where. Pages are written
#: last first, so no test can pass on the order a folder happens to list them in. Given
#: `-singlefile`, it writes the one page `-f` names, unnumbered, when there are that many.
_FAKE_PDFTOPPM = """
import json, os, sys, time
args = sys.argv[1:]
with open(os.environ["FAKE_PDFTOPPM_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
mode = os.environ.get("FAKE_PDFTOPPM_MODE", "ok")
if mode == "hang":
    while True:
        time.sleep(1)
if mode == "fail":
    sys.stderr.write(os.environ.get("FAKE_PDFTOPPM_STDERR", ""))
    sys.exit(1)
pages = int(os.environ.get("FAKE_PDFTOPPM_PAGES", "1"))
pad = len(str(pages)) if os.environ.get("FAKE_PDFTOPPM_PAD", "yes") == "yes" else 1
if "-png" in args:
    suffix, magic, channels = ".png", b"", 0
elif "-gray" in args:
    suffix, magic, channels = ".pgm", b"P5", 1
else:
    suffix, magic, channels = ".ppm", b"P6", 3
single = int(args[args.index("-f") + 1]) if "-singlefile" in args else 0
for page in ([single] if single <= pages else []) if single else range(pages, 0, -1):
    if not magic:
        body = b"PNG stand-in, page " + str(page).encode()
    elif mode == "garbage":
        body = b"not a pnm"
    else:
        body = magic + b"\\n2 3\\n255\\n" + bytes([page]) * (2 * 3 * channels)
    tail = suffix if single else "-" + str(page).zfill(pad) + suffix
    with open(args[-1] + tail, "wb") as sink:
        sink.write(body)
"""


def _write_script(path: Path, body: str) -> Path:
    """Write `body` as an executable Python script run by this suite's own interpreter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class RenderTests(unittest.TestCase):
    """`pdftoppm` run for real -- a fake one -- and its files read back in page order."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.bin = self.root / "bin"
        _write_script(self.bin / "pdftoppm", _FAKE_PDFTOPPM)
        self.log = self.root / "pdftoppm.log"
        self.pdf = self.root / "in.pdf"
        self.out = self.root / "renders"

    def environment(self, **steering: str) -> dict[str, str]:
        """An environment whose `PATH` holds only the fake, steered by `steering`."""
        return {"PATH": str(self.bin), "FAKE_PDFTOPPM_LOG": str(self.log), **steering}

    def logged(self) -> list[list[str]]:
        """Every argv the fake was run with."""
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def png(self, **steering: str) -> tuple[Path, ...]:
        """Render `self.pdf` to PNG under `self.out` through the fake."""
        with mock.patch.dict(os.environ, self.environment(**steering)):
            return render_png(self.pdf, self.out, "home", dpi=DPI, timeout=_TIMEOUT)

    def rasters(
        self, *, gray: bool, pages: int | None = None, **steering: str
    ) -> tuple[Raster, ...]:
        """Render `self.pdf`'s `pages` -- by default as many as the fake writes -- to rasters."""
        count = (
            int(steering.get("FAKE_PDFTOPPM_PAGES", "1")) if pages is None else pages
        )
        with mock.patch.dict(os.environ, self.environment(**steering)):
            rendered = render_rasters(
                self.pdf, gray=gray, dpi=DPI, timeout=_TIMEOUT, pages=count
            )
            return tuple(rendered)

    def gray_rasters(
        self, pages: int | None = None, **steering: str
    ) -> tuple[Raster, ...]:
        """Render `self.pdf` to gray rasters through the fake."""
        return self.rasters(gray=True, pages=pages, **steering)

    def test_png_runs_pdftoppm_with_the_documented_argv(self) -> None:
        self.png()
        # `--` ends the options, so a PDF whose name starts with `-` is read as a file.
        expected = ["-png", "-r", "96", "--", str(self.pdf), str(self.out / "home")]
        self.assertEqual(self.logged(), [expected])

    def test_png_creates_its_folder_and_returns_unpadded_pages_in_order(self) -> None:
        names = [path.name for path in self.png(FAKE_PDFTOPPM_PAGES="3")]
        self.assertEqual(names, ["home-1.png", "home-2.png", "home-3.png"])

    def test_png_returns_zero_padded_pages_in_order(self) -> None:
        paths = self.png(FAKE_PDFTOPPM_PAGES="12")
        names = [path.name for path in paths]
        self.assertEqual(names[:2], ["home-01.png", "home-02.png"])
        self.assertEqual(paths[-1].read_bytes(), b"PNG stand-in, page 12")

    def test_png_orders_pages_by_number_never_by_name(self) -> None:
        paths = self.png(FAKE_PDFTOPPM_PAGES="12", FAKE_PDFTOPPM_PAD="no")
        expected = [f"home-{page}.png" for page in (9, 10, 11, 12)]
        self.assertEqual([path.name for path in paths][8:], expected)

    def test_png_ignores_other_files_in_its_folder(self) -> None:
        self.out.mkdir()
        (self.out / "other-1.png").write_bytes(b"")
        (self.out / "home-1.txt").write_bytes(b"")
        self.assertEqual([path.name for path in self.png()], ["home-1.png"])

    def test_png_refuses_a_folder_already_holding_its_renders(self) -> None:
        self.out.mkdir()
        (self.out / "home-1.png").write_bytes(b"")
        with self.assertRaisesRegex(RasterError, "^renders already holds") as caught:
            self.png()
        # A message becomes a report finding: it names the folder, never its path.
        self.assertNotIn(str(self.root), str(caught.exception))
        self.assertFalse(self.log.exists())

    def test_png_refuses_a_stem_that_is_not_a_bare_name(self) -> None:
        for stem in ("", "a/b"):
            with self.subTest(stem), self.assertRaisesRegex(RasterError, "stem"):
                render_png(self.pdf, self.out, stem, dpi=DPI, timeout=_TIMEOUT)
        self.assertFalse(self.out.exists())

    def test_every_call_taking_a_dpi_refuses_one_that_is_not_positive(self) -> None:
        page, out = gray(1, 1, bytes(1)), self.out
        calls = (
            lambda dpi: ink(page, dpi),
            lambda dpi: compare_rasters(1, page, page, dpi),
            lambda dpi: render_png(self.pdf, out, "a", dpi=dpi, timeout=_TIMEOUT),
            lambda dpi: render_rasters(
                self.pdf, gray=True, dpi=dpi, timeout=_TIMEOUT, pages=1
            ),
        )
        for dpi in (0, -1):
            for index, call in enumerate(calls):
                with (
                    self.subTest(dpi=dpi, call=index),
                    self.assertRaisesRegex(RasterError, f"dpi {dpi}:"),
                ):
                    call(dpi)
        self.assertFalse(self.out.exists())

    def test_png_and_rasters_refuse_a_run_that_wrote_no_page(self) -> None:
        for render in (self.png, self.gray_rasters):
            with self.subTest(render), self.assertRaisesRegex(RasterError, "no page"):
                render(FAKE_PDFTOPPM_PAGES="0")

    def test_gray_rasters_run_pdftoppm_gray_into_a_folder_removed_after(self) -> None:
        self.gray_rasters()
        (argv,) = self.logged()
        one_page = ["-f", "1", "-l", "1", "-singlefile"]
        self.assertEqual(
            argv[:10], ["-gray", "-r", "96", *one_page, "--", str(self.pdf)]
        )
        self.assertFalse(Path(argv[10]).parent.exists())

    def test_gray_rasters_come_back_parsed_in_page_order(self) -> None:
        rasters = self.gray_rasters(FAKE_PDFTOPPM_PAGES="12")
        self.assertEqual([raster.pixels[0] for raster in rasters], list(range(1, 13)))
        self.assertEqual(rasters[0], gray(2, 3, bytes([1]) * 6))

    def test_color_rasters_run_pdftoppm_without_gray(self) -> None:
        rasters = self.rasters(gray=False, FAKE_PDFTOPPM_PAGES="2")
        self.assertEqual(self.logged()[1][:6], ["-r", "96", "-f", "2", "-l", "2"])
        self.assertEqual(rasters[1], Raster(2, 3, 3, bytes([2]) * 18))

    def test_rasters_refuse_a_page_that_is_not_pnm(self) -> None:
        with self.assertRaisesRegex(RasterError, "P5"):
            self.gray_rasters(FAKE_PDFTOPPM_MODE="garbage")

    def test_a_failed_run_is_refused_with_its_last_stderr_line(self) -> None:
        stderr = "Syntax Warning: first\nI/O Error: Couldn't open file\n\n"
        for render in (self.png, self.gray_rasters):
            with self.subTest(render), self.assertRaises(RasterError) as caught:
                render(FAKE_PDFTOPPM_MODE="fail", FAKE_PDFTOPPM_STDERR=stderr)
            message = str(caught.exception)
            self.assertIn("exited 1: I/O Error: Couldn't open file", message)
            self.assertNotIn("Syntax Warning", message)

    def test_a_failed_run_with_no_stderr_says_so(self) -> None:
        with self.assertRaisesRegex(RasterError, "no error output"):
            self.png(FAKE_PDFTOPPM_MODE="fail")

    def test_a_run_past_its_timeout_is_refused(self) -> None:
        hanging = self.environment(FAKE_PDFTOPPM_MODE="hang")
        with (
            mock.patch.dict(os.environ, hanging),
            self.assertRaisesRegex(RasterError, "within 0.5s"),
        ):
            render_png(self.pdf, self.out, "home", dpi=DPI, timeout=_SHORT_TIMEOUT)

    def test_each_page_is_rendered_alone_and_only_when_it_is_asked_for(self) -> None:
        # A page is rendered, parsed and handed on before the next is started, so a
        # reader keeping only what it measures holds one page's pixels at a time.
        with mock.patch.dict(os.environ, self.environment(FAKE_PDFTOPPM_PAGES="3")):
            pages = render_rasters(
                self.pdf, gray=True, dpi=DPI, timeout=_TIMEOUT, pages=3
            )
            self.assertFalse(self.log.exists())
            first = next(iter(pages))
            self.assertEqual(len(self.logged()), 1)
            rest = list(pages)
        self.assertEqual([page.pixels[0] for page in (first, *rest)], [1, 2, 3])
        numbers = [(argv[4], argv[6]) for argv in self.logged()]
        self.assertEqual(numbers, [("1", "1"), ("2", "2"), ("3", "3")])

    def test_a_page_pdftoppm_did_not_write_is_refused(self) -> None:
        with self.assertRaisesRegex(
            RasterError, "^pdftoppm wrote no page 2 for in.pdf$"
        ):
            self.gray_rasters(pages=2, FAKE_PDFTOPPM_PAGES="1")

    def test_no_page_to_render_is_refused_before_pdftoppm_runs(self) -> None:
        with self.assertRaisesRegex(RasterError, "^in.pdf has no page to render$"):
            self.gray_rasters(pages=0)
        self.assertFalse(self.log.exists())

    def test_a_missing_pdftoppm_is_refused(self) -> None:
        (self.bin / "pdftoppm").unlink()
        with self.assertRaisesRegex(RasterError, "could not be started"):
            self.gray_rasters()
