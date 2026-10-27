"""Shared fixtures for print_pass's suites: fake executables, and poppler's captured output.

A fake here is a Python script whose shebang is the interpreter running the suite, in a folder
that is the whole of `PATH`, so a module that shells out crosses the subprocess boundary for
real while everything on the far side stays scripted. Nothing here reaches the host's own
poppler, Chrome or any network: a tool without a fake is simply not found.

The captured text is real `pdfinfo -rawdates` / `pdffonts` / `pdfimages -list` output from a
Chromium 153 PDF under poppler 26.08, with each date replaced by an obviously fake one. The row
builders reproduce poppler's own layout, so a suite can add rows the capture lacks.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

#: `pdfinfo -rawdates` of a one-page Letter PDF (the dates are fake; the shape is real).
INFO_TEXT = """\
Title:           Format Probe
Creator:         Chromium
Producer:        Skia/PDF m153
CreationDate:    D:20000101000000+00'00'
ModDate:         D:20000101000000+00'00'
Custom Metadata: no
Metadata Stream: no
Tagged:          yes
UserProperties:  no
Suspects:        no
Form:            none
JavaScript:      no
Pages:           1
Encrypted:       no
Page    1 size:  612 x 792 pts (letter)
Page    1 rot:   0
File size:       29273 bytes
Optimized:       no
PDF version:     1.4
"""

#: `pdffonts`' two header lines: the column names, and one dashed run per column.
FONT_HEADER = "name                                 type              encoding         emb sub uni object ID\n"
FONT_RULE = "------------------------------------ ----------------- ---------------- --- --- --- ---------\n"

#: `pdffonts` of the same PDF.
FONTS_TEXT = (
    FONT_HEADER
    + FONT_RULE
    + "AAAAAA+Times-Roman                   CID TrueType      Identity-H       yes yes yes      4  0\n"
    + "BAAAAA+TimesNewRomanPSMT             CID TrueType      Identity-H       yes yes yes      5  0\n"
    + "EAAAAA+Menlo-Regular                 CID TrueType      Identity-H       yes yes yes      8  0\n"
)

#: `pdfimages -list`'s two header lines. Its rule is ONE unbroken run, unlike pdffonts'.
IMAGE_HEADER = "page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio\n"
IMAGE_RULE = "--------------------------------------------------------------------------------------------\n"

#: The one image row of the same PDF, and the whole `pdfimages -list` output.
IMAGE_ROW = "   1     0 image      64    32  icc     3   8  image  no         9  0    64    64  203B 3.3%\n"
IMAGES_TEXT = IMAGE_HEADER + IMAGE_RULE + IMAGE_ROW

#: pdffonts' `%-36s`: the name column's width, which poppler counts in BYTES.
_NAME_BYTES = 36


def font_line(
    name: str,
    kind: str = "Type 1",
    emb: str = "no",
    sub: str = "no",
    uni: str = "no",
    object_id: str = f"{7:6d} {0:2d}",
) -> str:
    """One pdffonts row in poppler's own layout, the name padded by its UTF-8 byte length.

    `object_id` is `num gen` in poppler's `%6d %2d`, or the `[none]` it prints for a font
    dictionary that is no object of its own.
    """
    padding = " " * max(0, _NAME_BYTES - len(name.encode("utf-8")))
    return f"{name}{padding} {kind:<17} {'Standard':<16} {emb:<3} {sub:<3} {uni:<3} {object_id}\n"


def image_line(
    size: str = "203B",
    object_id: str = "    9  0",
    x_ppi: str = "64",
    y_ppi: str = "64",
) -> str:
    """The captured pdfimages row with its size, object ID and density columns replaced."""
    return f"   1     0 image      64    32  icc     3   8  image  no   {object_id} {x_ppi:>5} {y_ppi:>5} {size:>5} 3.3%\n"


def write_script(path: Path, body: str) -> Path:
    """Write `body` as an executable Python script run by this suite's own interpreter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # LF newlines on every host: a CR left on the shebang line names no interpreter.
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def fake_bin(bin_dir: Path, name: str, body: str) -> dict[str, str]:
    """Write a fake `name` into `bin_dir`; return the environment whose PATH is `bin_dir` alone.

    Nothing inherited is kept: a fake's shebang names its interpreter absolutely, so it needs
    no PATH, and a tool no fake stands in for is then not found at all rather than reaching
    the host's own copy. (bounded_run's timeout kill cannot find `ps` either, and falls back
    to killing the fake's own process group, which is the whole of a one-process fake.)
    """
    write_script(bin_dir / name, body)
    return {"PATH": str(bin_dir)}
