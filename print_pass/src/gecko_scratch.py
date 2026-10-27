"""The run's scratch folder: the folders Firefox would find for itself, redirected under one root.

`make_scratch` names and makes the folders geckodriver's environment points Firefox at -- a home
for macOS's frameworks, the application-data folders, the temporary folder, and the root
geckodriver makes each session's profile in -- so that what Firefox writes to each of those
lands here rather than in the reader's own, and `remove_scratch` removes it all once the run
is done. One folder is not among them: Firefox's crash helper is handed one in the system's
per-user temporary folder, which `TMPDIR` does not redirect (`gecko_command` says more).
Both are kept apart from geckodriver's process: a scratch is plain folders, which a test can
make and read without starting a browser.

macOS makes some folders in a home it is pointed at with an everyone-deny-delete ACL, and a
plain recursive removal of the scratch then fails on each (measured). Two measures answer it,
and both are kept. `make_scratch` makes the folders macOS was seen to make first, as ordinary
folders, so that a scratch left behind by a run that died can still be removed by hand. And
`remove_scratch` strips every ACL in the tree before it removes it, since macOS may make
another such folder at any point in a session -- `Documents` was first seen made as a session
stopped -- and a list made ahead of time cannot be known to be whole.

Every behavior claimed here was measured on macOS, the one platform the Firefox engine runs on.

Entry point assumptions: the standard library, the shared `bounded_run` (its bounded runner),
found by the sibling insert below, and this folder's `gecko_process` and `print_record`. The
strip is macOS's own `/bin/chmod`, by its full path.
"""

from __future__ import annotations

import itertools
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# The shared tree-killing subprocess runner: a folder beside this tool's own, named rather
# than searched for, so a same-named folder above the clone is never imported in its place,
# and put near the front of the path, ahead of every installed distribution, so none of the
# same name can shadow it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run
from gecko_process import RunBounded
from print_record import EngineError, first_line

#: The log geckodriver writes into the scratch, with Firefox's console in it.
_LOG_NAME: Final = "geckodriver.log"

#: The folders macOS was seen to make in a home it is pointed at, each with an
#: everyone-deny-delete ACL: Library and Downloads as a session starts, and Documents as one
#: stopped, in 2 of 11 measured runs. Made here first, all three stayed ordinary folders
#: through a session and its stop in the runs that checked.
_LOCKED_HOME_FOLDERS: Final = ("Library", "Downloads", "Documents")

#: The strip's command, before the paths it is handed: macOS's chmod, by its full path, since
#: `-N`, which removes every ACL entry, is its own and not GNU's; `-h` changes a symlink itself,
#: never what it points at. Not `-R`: a recursive strip also strips the ACL of whatever a
#: symlink in the tree points at, outside the tree too, and `-R` refuses `-h`. A scratch does
#: hold such a symlink: a print leaves one in it to the printer's description file, a system
#: file outside it (all measured). No `--` ends the options: after `-N`, this chmod takes it for
#: a path in the working directory, and exits 1 for want of one (measured). Every path it is
#: handed is absolute instead, so none begins with `-` to be read as an option.
_STRIP: Final = ("/bin/chmod", "-h", "-N")

#: How long one strip may take: it changes a few hundred entries at most.
STRIP_TIMEOUT_SECONDS: Final = 30.0

#: The most paths one strip is handed, far inside the limit on a command's arguments.
PATHS_PER_STRIP: Final = 256


@dataclass(frozen=True)
class Scratch:
    """The run's scratch folder, and each folder in it that Firefox is redirected into."""

    root: Path  # TMPDIR, and the parent of every other entry
    home: Path  # CFFIXED_USER_HOME: the home macOS's frameworks resolve
    app_data: Path  # MOZ_APP_DATA: Profiles, pending pings and crash reports
    local_app_data: Path  # MOZ_LOCAL_APP_DATA: its local, cache-side counterpart
    profiles: Path  # --profile-root: where geckodriver makes each session's profile
    log: Path  # geckodriver's output, with Firefox's console in it


def make_scratch(root: Path) -> Scratch:
    """Name the scratch folders under `root` and make them; the log is left to the start.

    `root` must be absolute and, if it is there already, a folder that is not a symlink, as
    `remove_scratch` requires: so a run never makes a scratch that removal would refuse, nor
    makes one through a symlink, in whatever it points at. A root not there yet is made. All
    of this is checked before anything is made. Making one that exists already changes
    nothing.

    The caller passes a fresh folder, for example one from `tempfile.mkdtemp()`:
    `remove_scratch` removes the whole tree at `root`, including anything already in it.

    Raises:
        EngineError: `root` is relative, could not be examined, or is a symlink or not a
            folder, and nothing was made. The one-line message names `root`.
    """
    _require_real_folder(root, _not_made, missing_ok=True)
    scratch = Scratch(
        root=root,
        home=root / "home",
        app_data=root / "app-data",
        local_app_data=root / "local-app-data",
        profiles=root / "profiles",
        log=root / _LOG_NAME,
    )
    homes = (scratch.home / name for name in _LOCKED_HOME_FOLDERS)
    for folder in (scratch.app_data, scratch.local_app_data, scratch.profiles, *homes):
        folder.mkdir(parents=True, exist_ok=True)
    return scratch


def remove_scratch(
    root: Path,
    *,
    run: RunBounded = bounded_run.run_bounded,
    remove: Callable[[Path], None] = shutil.rmtree,
) -> None:
    """Remove the scratch at `root`, every ACL in it stripped first.

    `root` must be an absolute path to a real folder, never a symlink, and is checked before
    anything runs: a walk from a symlinked root, and each strip under it, would reach
    whatever it points at. Every entry of the tree is then named to the strip, a symlink as
    itself and never followed, a batch of paths at a time, each strip bounded by
    `STRIP_TIMEOUT_SECONDS`; then `remove` removes the tree. Nothing is removed unless every
    strip succeeded.

    Its precondition is that nothing writes into the scratch from the stop on. The strip names
    each entry by the path the walk found, so a folder swapped for a symlink in between would
    carry the strip to the link's target, and a hard link to a file outside would have that
    file's ACL stripped too. Neither can be told from here: this rests on that precondition,
    and on nothing the run starts making a hard link to a file outside, which was not
    measured.

    Raises:
        EngineError: `root` is relative, could not be examined, or is a symlink or not a
            folder; the tree could not be walked; a strip could not run, timed out or
            failed; or the removal failed. The one-line message names `root`.
    """
    _require_real_folder(root, _not_removed)
    try:
        entries = _every_entry(root)
    except OSError as error:
        raise _not_removed(
            root, f"it could not be walked: {first_line(error)}"
        ) from error
    for batch in itertools.batched(entries, PATHS_PER_STRIP):
        _strip_acls(root, batch, run)
    try:
        remove(root)
    except OSError as error:
        raise _not_removed(root, first_line(error)) from error


def _require_real_folder(
    root: Path,
    refused: Callable[[Path, str], EngineError],
    *,
    missing_ok: bool = False,
) -> None:
    """Refuse `root`, by the error `refused` gives, unless it is an absolute path to a folder.

    The folder must not be a symlink, and is examined as itself, never followed. With
    `missing_ok`, a root not there at all passes too, for `make_scratch` to make. A relative
    path is resolved against whatever the working directory is, so what it names would hang on
    where the run was started rather than on what it was handed.
    """
    if not root.is_absolute():
        raise refused(root, "its path is not absolute")
    try:
        mode = root.lstat().st_mode
    except OSError as error:
        if missing_ok and isinstance(error, FileNotFoundError):
            return
        raise refused(root, f"it could not be examined: {first_line(error)}") from error
    if stat.S_ISLNK(mode):
        raise refused(root, "it is a symlink, which is never followed")
    if not stat.S_ISDIR(mode):
        raise refused(root, "it is not a folder")


def _every_entry(root: Path) -> list[str]:
    """`root` first, then every entry under it: a symlink is named, never followed."""

    def fail(error: OSError) -> None:
        raise error

    entries = [str(root)]
    for folder, folders, files in os.walk(root, onerror=fail):
        entries.extend(os.path.join(folder, name) for name in (*folders, *files))
    return entries


def _strip_acls(root: Path, paths: tuple[str, ...], run: RunBounded) -> None:
    """Strip every ACL entry from each of `paths`, each a symlink itself, never its target."""
    tool = _STRIP[0]
    try:
        stripped = run([*_STRIP, *paths], timeout=STRIP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise _not_removed(
            root, f"{tool} hit its {STRIP_TIMEOUT_SECONDS:g} s timeout"
        ) from error
    except OSError as error:
        raise _not_removed(
            root, f"{tool} could not run: {first_line(error)}"
        ) from error
    if stripped.returncode != 0:
        said = first_line(stripped.stderr) or "nothing on stderr"
        raise _not_removed(root, f"{tool} exited {stripped.returncode}: {said}")


def _not_made(root: Path, reason: str) -> EngineError:
    """The error a scratch that was refused before any of it was made is reported with."""
    return EngineError(f"the scratch folder {root} was not made: {reason}")


def _not_removed(root: Path, reason: str) -> EngineError:
    """The error a scratch that could not be removed is reported with, naming its root."""
    return EngineError(f"the scratch folder {root} was not removed: {reason}")
