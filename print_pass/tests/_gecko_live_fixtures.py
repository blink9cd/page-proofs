"""The live Firefox suite's fence: its Seatbelt profile, its shim and its PATH.

The Firefox engine keeps Firefox on loopback from inside: prefs, a dead proxy and a non-local
guard. A live run proves those hold only if nothing gets past them while it does, so the live
suite runs geckodriver, and so Firefox, inside a Seatbelt network fence the kernel enforces:
everything but loopback refused, name lookups refused at the resolver, and every read or
write under the folders it is given denied -- the reader's own Firefox folders, under both
the home `$HOME` names and the account's, and a canary folder of the suite's own. Seatbelt
matches resolved paths, so every folder is denied by its resolved path. The rules are
macOS's own, measured, and kept apart from the production engine, which a fence would cost
Firefox's own child sandboxes (they cannot nest under it, so the shim switches them off).

A fence that silently fails to apply looks exactly like one that works, so before anything
launches, `_gecko_proof_fixtures.prove_fence` proves it under the same profile file, and a run
is made only when it held. Then `arm_fence` writes the shim geckodriver the run's `PATH` finds
first: it records its pid -- geckodriver's, and its process group's id -- and its arguments,
sets Firefox's host-resolver log, and execs the real geckodriver under the profile.

Entry point assumptions: imported by a suite that unittest discovery loaded with this folder
as its top-level directory; the first import below puts the tool's folder and its private
`src/` on the path. macOS alone has `sandbox-exec`; elsewhere the live suite skips before
any of this runs, and the pure functions here run anywhere.
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
from _print_pass_live_fixtures import run_bound_seconds

import _gecko_canary as canary  # beside this module
import print_pass  # the CLI, via the fixtures' insert
from gecko_command import GECKODRIVER, find_geckodriver  # the tool's private src/
from print_targets import adhoc_targets  # as above

#: macOS's own runner of a program under a Seatbelt profile. Apple marks it deprecated.
SANDBOX_EXEC: Final = "/usr/bin/sandbox-exec"

#: Firefox, installed as the standard app bundle: where geckodriver finds it on macOS.
FIREFOX_APP: Final = Path("/Applications/Firefox.app")
_FIREFOX_EXECUTABLE: Final = FIREFOX_APP / "Contents" / "MacOS" / "firefox"

#: The one platform, as `sys.platform` names it, with `sandbox-exec` and the Firefox engine.
_MACOS: Final = "darwin"

#: The reader's own Firefox folders, under their home: its profiles, and its caches.
_FIREFOX_DATA: Final = (
    ("Library", "Application Support", "Firefox"),
    ("Library", "Caches", "Firefox"),
)

#: The network fence, as measured: everything allowed but outbound network, loopback and
#: unix sockets let through, and the resolver's socket and its lookup service refused --
#: each named by the constant the canary probes, or asks `sandbox_check` about, so a rule
#: and its proof cannot drift apart.
_NETWORK_FENCE: Final = (
    "(version 1)",
    "(allow default)",
    "(deny network-outbound)",
    '(allow network-outbound (remote ip "localhost:*"))',
    "(allow network-outbound (remote unix-socket))",
    "(deny network-outbound (remote unix-socket"
    f' (path-literal "{canary.RESOLVER_SOCKET}")))',
    f'(deny mach-lookup (global-name "{canary.DNSSD_SERVICE}"))',
)

#: What a folder in a profile's string literal must not hold: the literal's own escapes.
_UNQUOTABLE: Final = ('"', "\\")

#: Firefox's own child sandboxes, each switched off: none can apply under the fence.
_CHILD_SANDBOXES: Final = ("CONTENT", "SOCKET_PROCESS", "RDD", "UTILITY", "GMP", "GPU")

#: Firefox's host resolver, logged at its most verbose, each line written as it happens.
MOZ_LOG_MODULES: Final = "timestamp,sync,nsHostResolver:5"

#: What the shim exits with when it cannot record what the suite reads.
_SHIM_FAILED: Final = 3

#: The geckodriver option naming the folder each session's profile is made in.
_PROFILE_ROOT: Final = "--profile-root"

#: Bound on the live Firefox run: the one page given no targets file, in its default
#: state, at the default `--timeout`, the run being given none.
FIREFOX_RUN_BOUND_SECONDS: Final = run_bound_seconds(
    adhoc_targets([print_pass.DEFAULT_PAGE]),
    timeout=print_pass.DEFAULT_TIMEOUT_SECONDS,
)


@dataclass(frozen=True)
class Fence:
    """One run's fence, under a folder of its own: what is written there, and what is recorded.

    `moz_log` is the name given `MOZ_LOG_FILE`; each Firefox process logs to a file of its
    own beside it, the name extended.
    """

    profile: Path
    canary_file: Path
    proof: Path
    shim: Path
    pid_file: Path
    args_file: Path
    moz_log: Path


def missing_prerequisites(
    *, platform: str, geckodriver: str | None, firefox: bool, sandbox_exec: bool
) -> tuple[str, ...]:
    """What the live Firefox run needs and this host lacks, each named; empty when nothing."""
    needs = (
        (platform != _MACOS, f"macOS, not {platform!r}"),
        (geckodriver is None, f"{GECKODRIVER} on PATH"),
        (not firefox, f"Firefox, installed as {FIREFOX_APP}"),
        (not sandbox_exec, SANDBOX_EXEC),
    )
    return tuple(what for absent, what in needs if absent)


def live_firefox_host(
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str | None, tuple[str, ...]]:
    """The geckodriver `which` finds on `PATH`, and what this host lacks of what the run needs.

    Read before any `PATH` is changed: this is the real geckodriver the shim runs. It is
    made absolute here, since a relative entry on `PATH` finds it relative to this process's
    folder, and the shim runs it from the run's own.
    """
    found = find_geckodriver(which)
    geckodriver = None if found is None else os.path.abspath(found)
    missing = missing_prerequisites(
        platform=sys.platform,
        geckodriver=geckodriver,
        firefox=_FIREFOX_EXECUTABLE.is_file(),
        sandbox_exec=Path(SANDBOX_EXEC).is_file(),
    )
    return geckodriver, missing


def readers_homes() -> tuple[Path, ...]:
    """The reader's home as `$HOME` names it, then as the account record holds it.

    The two differ when `HOME` is redirected, and macOS's own frameworks find a user's
    folders from the account record, not from `HOME`: the fence denies Firefox's under both.
    """
    return (Path.home(), _account_home())


def _account_home() -> Path:
    """The home the account record holds for the user this process runs as."""
    if sys.platform == "win32":
        # Windows keeps no account record of this kind, and the live suite skips there
        # before this runs; the narrowing is what lets `pwd` type-check for Windows too.
        raise NotImplementedError("no account record")  # pragma: only-on win32
    import pwd  # POSIX only, so imported where the narrowing above has run

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def firefox_data_folders(homes: Sequence[Path]) -> tuple[Path, ...]:
    """The reader's own Firefox folders under each of `homes`, resolved, each named once.

    The fence denies every access to them. A folder that does not exist is resolved as
    far as its path does, so a home reached through a link is named by its target's path.

    Raises:
        ValueError: a home is not absolute -- a relative `$HOME`, say. Resolving it would
            name a folder under this process's own, not the one Firefox would use.
    """
    for home in homes:
        if not home.is_absolute():
            raise ValueError(f"a home is not absolute: {home}")
    folders = (
        home.joinpath(*parts).resolve() for home in homes for parts in _FIREFOX_DATA
    )
    return tuple(dict.fromkeys(folders))


def seatbelt_profile(denied: Sequence[Path]) -> str:
    """The fence's profile: the network rules, then every read and write under `denied` refused.

    Seatbelt matches a path once resolved, so each folder must be given resolved.

    Raises:
        ValueError: no folder is given -- a file rule with no folder would deny every file
            -- or one is not absolute, or holds what a profile string cannot.
    """
    if not denied:
        raise ValueError("a fence denies the reads under one folder at least")
    for folder in denied:
        _require_profile_literal(folder)
    subpaths = " ".join(f'(subpath "{folder}")' for folder in denied)
    return "\n".join((*_NETWORK_FENCE, f"(deny file-read* file-write* {subpaths})", ""))


def _require_profile_literal(folder: Path) -> None:
    """Refuse a folder that is not absolute, or that a profile's string literal cannot hold."""
    if not folder.is_absolute():
        raise ValueError(f"a denied folder is not absolute: {folder}")
    if any(character in str(folder) for character in _UNQUOTABLE):
        raise ValueError(f"a profile string cannot hold {str(folder)!r}")


def make_fence(root: Path, readers_folders: Sequence[Path]) -> Fence:
    """Write, under `root`, the profile fencing off `readers_folders` and a canary folder.

    Nothing launches here. `root` and every folder are resolved first: Seatbelt matches
    resolved paths, and a temporary folder lies under a symlink on macOS.

    Raises:
        ValueError: a folder is not absolute -- resolving would make it relative to this
            process's folder -- or a resolved one holds what a profile string cannot.
    """
    for folder in readers_folders:
        _require_profile_literal(folder)
    denied = tuple(folder.resolve() for folder in readers_folders)
    root = root.resolve()
    canary_folder = root / "canary"
    text = seatbelt_profile((*denied, canary_folder))
    canary_folder.mkdir()
    canary_file = canary_folder / "canary.txt"
    canary_file.write_text("canary\n", encoding="utf-8", newline="\n")
    profile = root / "fence.sb"
    profile.write_text(text, encoding="utf-8", newline="\n")
    for name in ("proof", "moz", "shim"):
        (root / name).mkdir()
    return Fence(
        profile=profile,
        canary_file=canary_file,
        proof=root / "proof",
        shim=root / "shim" / GECKODRIVER,
        pid_file=root / "shim.pid",
        args_file=root / "shim.args",
        moz_log=root / "moz" / "moz.log",
    )


def shim_text(
    *, profile: Path, geckodriver: str, pid_file: Path, args_file: Path, moz_log: Path
) -> str:
    """The shim: record its pid and arguments, set the log, exec `geckodriver` under `profile`.

    Its pid survives both execs, so it is geckodriver's, and, geckodriver being started at
    the head of a session of its own, its process group's id. The pid file is written whole
    or not at all: a watcher may read it at any moment.
    """
    quote = shlex.quote
    staged = quote(f"{pid_file}.partial")
    sandboxes = " ".join(f"MOZ_DISABLE_{name}_SANDBOX=1" for name in _CHILD_SANDBOXES)
    return "\n".join(
        (
            "#!/bin/sh",
            "# geckodriver, fenced: written by print_pass's live Firefox suite into its own",
            "# temporary folder. It records its pid and arguments, then runs the real driver",
            "# under the suite's Seatbelt profile, Firefox's own child sandboxes off.",
            f'echo "$$" > {staged} && mv {staged} {quote(str(pid_file))}'
            f" || exit {_SHIM_FAILED}",
            f"printf '%s\\n' \"$@\" > {quote(str(args_file))} || exit {_SHIM_FAILED}",
            f"export {sandboxes}",
            f"export MOZ_LOG={quote(MOZ_LOG_MODULES)} MOZ_LOG_FILE={quote(str(moz_log))}",
            f'exec {SANDBOX_EXEC} -f {quote(str(profile))} {quote(geckodriver)} "$@"',
            "",
        )
    )


def fenced_env(shim_folder: Path, base: Mapping[str, str]) -> dict[str, str]:
    """`base`, copied, its `PATH` searching `shim_folder` first."""
    path = base.get("PATH", os.defpath)
    return {**base, "PATH": f"{shim_folder}{os.pathsep}{path}"}


def arm_fence(
    fence: Fence, geckodriver: str, base: Mapping[str, str]
) -> dict[str, str]:
    """Write the shim over `geckodriver`; the run's environment, whose `PATH` finds it first.

    The run finds geckodriver as the tool does, on `PATH`, so the same search is made here
    first: a run that would find another geckodriver is refused before it starts.

    Raises:
        AssertionError: the run's `PATH` would find a geckodriver other than the shim.
    """
    fence.shim.write_text(
        shim_text(
            profile=fence.profile,
            geckodriver=geckodriver,
            pid_file=fence.pid_file,
            args_file=fence.args_file,
            moz_log=fence.moz_log,
        ),
        encoding="utf-8",
        newline="\n",
    )
    fence.shim.chmod(stat.S_IRWXU)
    env = fenced_env(fence.shim.parent, base)
    found = shutil.which(GECKODRIVER, path=env["PATH"])
    if found is None or Path(found).resolve() != fence.shim.resolve():
        raise AssertionError(
            f"the run's PATH finds {found}, not the fenced {fence.shim}: nothing launched"
        )
    return env


def driver_args(fence: Fence) -> tuple[str, ...]:
    """The arguments the shim was given, as it recorded them: geckodriver's own."""
    return tuple(fence.args_file.read_text(encoding="utf-8").splitlines())


def scratch_root(args: Sequence[str]) -> Path:
    """The run's scratch folder: the folder holding the one geckodriver makes profiles in.

    Raises:
        ValueError: `args` names no `--profile-root`.
    """
    listed = list(args)
    if _PROFILE_ROOT not in listed[:-1]:
        raise ValueError(f"geckodriver was given no {_PROFILE_ROOT}: {listed}")
    return Path(listed[listed.index(_PROFILE_ROOT) + 1]).parent
