"""Grouped suite: what the live Firefox suite's fence is built from, proven without Firefox.

The live Firefox suite runs geckodriver through a shim of its own writing, inside a Seatbelt
network fence whose canaries it proves first. Each part is checked here: the profile's text,
the reader's Firefox folders it denies, under every spelling of their home, and the resolved
paths it names them by; the shim's text and what it does under `sandbox-exec`; the run's
`PATH` and environment; what the suite skips on; and where it finds the run's scratch. The
canaries and the proof are `test_gecko_live_canary.py`'s and `test_gecko_live_proof.py`'s,
and the watch beside the run is `test_gecko_live_watch.py`'s.

Entry point assumptions: discovered by the folder's aggregator, or run alone from this
folder; the fixtures' bootstrap puts the tool's folder and its private `src/` on the path.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final
from unittest import mock

# First, beside this module via the loader's top-level folder: it puts the tool's folder
# and its private src/ on the path for every import after it.
from _gecko_live_fixtures import (
    SANDBOX_EXEC,
    fenced_env,
    firefox_data_folders,
    live_firefox_host,
    make_fence,
    missing_prerequisites,
    readers_homes,
    scratch_root,
    seatbelt_profile,
    shim_text,
)

import _gecko_canary as canary  # beside this module
import _print_pass_live_fixtures  # as above
import bounded_run  # the shared runner, a folder beside the tool's, via the CLI's own insert

#: Stand-in folders: absolute, and naming no real machine.
_DENIED: Final = (Path("/srv/fence/denied"), Path("/srv/fence/with space"))

#: The profile those folders give, line by line.
_PROFILE_LINES: Final = (
    "(version 1)",
    "(allow default)",
    "(deny network-outbound)",
    '(allow network-outbound (remote ip "localhost:*"))',
    "(allow network-outbound (remote unix-socket))",
    "(deny network-outbound (remote unix-socket"
    ' (path-literal "/private/var/run/mDNSResponder")))',
    '(deny mach-lookup (global-name "com.apple.dnssd.service"))',
    '(deny file-read* file-write* (subpath "/srv/fence/denied")'
    ' (subpath "/srv/fence/with space"))',
)

#: The reader's Firefox folders under a home, as parts below it.
_FIREFOX_PARTS: Final = (
    ("Library", "Application Support", "Firefox"),
    ("Library", "Caches", "Firefox"),
)

#: A bound on the shim's behavioral run: a shell, `sandbox-exec` and a stand-in driver.
_SHIM_RUN_SECONDS: Final = 30.0

#: Whether this host can run a program under a Seatbelt profile: macOS, with its runner.
_HAS_SANDBOX_EXEC: Final = sys.platform == "darwin" and Path(SANDBOX_EXEC).is_file()


def _under(home: Path) -> tuple[Path, ...]:
    """The reader's two Firefox folders under `home`, as written."""
    return tuple(home.joinpath(*parts) for parts in _FIREFOX_PARTS)


class SeatbeltProfileTest(unittest.TestCase):
    """The fence's text: the measured network rules, then every read under each folder denied."""

    def test_the_fence_is_the_network_rules_then_the_denied_folders(self) -> None:
        self.assertEqual(seatbelt_profile(_DENIED), "\n".join((*_PROFILE_LINES, "")))

    def test_the_fence_denies_the_socket_the_canary_probes_and_the_service_checked(
        self,
    ) -> None:
        """One name each, shared: the rule and what proves it cannot drift apart."""
        text = seatbelt_profile(_DENIED)
        self.assertIn(f'(path-literal "{canary.RESOLVER_SOCKET}")', text)
        self.assertIn(f'(global-name "{canary.DNSSD_SERVICE}")', text)

    def test_a_fence_denying_no_folder_is_refused(self) -> None:
        # An empty filter list would deny every file operation, not none.
        with self.assertRaisesRegex(ValueError, "one folder at least"):
            seatbelt_profile(())

    def test_a_relative_folder_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "not absolute"):
            seatbelt_profile((Path("relative/folder"),))

    def test_a_folder_no_profile_string_can_hold_is_refused(self) -> None:
        for character in ('"', "\\"):
            with self.subTest(character=character):
                with self.assertRaisesRegex(ValueError, "cannot hold"):
                    seatbelt_profile((Path(f"/srv/a{character}b"),))


class ReadersFoldersTest(unittest.TestCase):
    """The reader's Firefox folders: under every home given, each resolved, each named once."""

    def setUp(self) -> None:
        """A folder of real paths: a home, and a link to it; and an account's home, named."""
        folder = tempfile.TemporaryDirectory(prefix="print_pass-homes-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.link = self.root / "link"
        self.link.symlink_to(self.home, target_is_directory=True)
        self.account = self.root / "account"

    def account_record(self) -> mock.MagicMock:
        """A stand-in for the account database's lookup by uid: its home is `self.account`."""
        return mock.MagicMock(
            return_value=types.SimpleNamespace(pw_dir=str(self.account))
        )

    def test_a_folder_reached_through_a_link_is_named_by_its_targets_path(self) -> None:
        self.assertEqual(firefox_data_folders((self.link,)), _under(self.home))

    def test_two_homes_give_both_sets_of_folders(self) -> None:
        other = Path("/srv/account-home")
        self.assertEqual(
            firefox_data_folders((self.home, other)),
            (*_under(self.home), *_under(other)),
        )

    def test_two_spellings_of_one_home_give_its_folders_once(self) -> None:
        self.assertEqual(
            firefox_data_folders((self.home, self.link)), _under(self.home)
        )

    @unittest.skipIf(sys.platform == "win32", "Windows keeps no account record")
    def test_a_home_other_than_the_accounts_is_kept_beside_it(self) -> None:
        """`$HOME` and the account record name two homes: both sets are denied, in order."""
        with (
            mock.patch.dict(os.environ, {"HOME": str(self.home)}),
            mock.patch("pwd.getpwuid", self.account_record()),
        ):
            homes = readers_homes()
        self.assertEqual(homes, (self.home, self.account))
        self.assertEqual(
            firefox_data_folders(homes), (*_under(self.home), *_under(self.account))
        )

    def test_the_account_home_is_the_one_recorded_for_this_processs_user(
        self,
    ) -> None:
        if sys.platform == "win32":
            self.skipTest("Windows keeps no account record")
        record = self.account_record()
        with (
            mock.patch.dict(os.environ, {"HOME": str(self.home)}),
            mock.patch("pwd.getpwuid", record),
        ):
            homes = readers_homes()
        record.assert_called_once_with(os.getuid())
        self.assertEqual(homes[1], self.account)

    def test_a_home_that_is_not_absolute_is_refused(self) -> None:
        """Resolved, a relative home would name a folder under this process's own."""
        with self.assertRaisesRegex(ValueError, "not absolute"):
            firefox_data_folders((self.home, Path("relative/home")))

    def test_the_fence_denies_a_linked_folder_under_its_targets_path(self) -> None:
        fence = make_fence(self.root, (self.link / "Library",))
        text = fence.profile.read_text(encoding="utf-8")
        self.assertIn(f'(subpath "{self.home / "Library"}")', text)
        self.assertNotIn(str(self.link), text)

    def test_the_fence_refuses_a_relative_folder_before_writing_anything(self) -> None:
        with self.assertRaisesRegex(ValueError, "not absolute"):
            make_fence(self.root, (Path("relative/folder"),))
        self.assertEqual(sorted(self.root.iterdir()), [self.home, self.link])


class ShimTest(unittest.TestCase):
    """The shim: its pid recorded, its arguments kept, then the real driver under the fence."""

    def test_the_shim_records_then_execs_the_driver_under_the_profile(self) -> None:
        text = shim_text(
            profile=Path("/srv/run/fence.sb"),
            geckodriver="/srv/bin/gecko driver",
            pid_file=Path("/srv/run/shim.pid"),
            args_file=Path("/srv/run/shim.args"),
            moz_log=Path("/srv/run/moz/moz.log"),
        )
        lines = text.splitlines()
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertTrue(text.endswith("\n"))
        self.assertIn(
            'echo "$$" > /srv/run/shim.pid.partial'
            " && mv /srv/run/shim.pid.partial /srv/run/shim.pid || exit 3",
            lines,
        )
        self.assertIn("printf '%s\\n' \"$@\" > /srv/run/shim.args || exit 3", lines)
        self.assertIn(
            "export MOZ_DISABLE_CONTENT_SANDBOX=1 MOZ_DISABLE_SOCKET_PROCESS_SANDBOX=1"
            " MOZ_DISABLE_RDD_SANDBOX=1 MOZ_DISABLE_UTILITY_SANDBOX=1"
            " MOZ_DISABLE_GMP_SANDBOX=1 MOZ_DISABLE_GPU_SANDBOX=1",
            lines,
        )
        self.assertIn(
            "export MOZ_LOG=timestamp,sync,nsHostResolver:5"
            " MOZ_LOG_FILE=/srv/run/moz/moz.log",
            lines,
        )
        self.assertEqual(
            lines[-1],
            "exec /usr/bin/sandbox-exec -f /srv/run/fence.sb '/srv/bin/gecko driver' \"$@\"",
        )

    @unittest.skipUnless(
        _HAS_SANDBOX_EXEC, "sandbox-exec is macOS's, and this host lacks it"
    )
    def test_the_shim_runs_a_driver_under_the_fence_with_its_pid_and_arguments(
        self,
    ) -> None:
        """Run under `sandbox-exec`, a stand-in driver keeps the shim's pid and every argument.

        The stand-in writes its own pid, the variables the shim set, and its arguments, one
        per line: the pid the shim recorded is the driver's, and an argument with a space in
        it arrives whole, through both the shell's quoting and `sandbox-exec`.
        """
        folder = tempfile.TemporaryDirectory(prefix="print_pass-shim-")
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        seen = root / "seen.txt"
        driver = root / "stand in driver"
        driver.write_text(
            "#!/bin/sh\n"
            f'printf \'%s\\n\' "$$" "$MOZ_LOG_FILE" "$MOZ_DISABLE_GPU_SANDBOX" "$@"'
            f" > '{seen}'\n",
            encoding="utf-8",
            newline="\n",
        )
        driver.chmod(stat.S_IRWXU)
        profile = root / "fence.sb"
        profile.write_text(
            seatbelt_profile((root / "denied",)), encoding="utf-8", newline="\n"
        )
        shim = root / "geckodriver"
        shim.write_text(
            shim_text(
                profile=profile,
                geckodriver=str(driver),
                pid_file=root / "shim.pid",
                args_file=root / "shim.args",
                moz_log=root / "moz.log",
            ),
            encoding="utf-8",
            newline="\n",
        )
        shim.chmod(stat.S_IRWXU)
        answer = bounded_run.run_bounded(
            [str(shim), "--port", "0", "an argument"], timeout=_SHIM_RUN_SECONDS
        )
        self.assertEqual(answer.returncode, 0, answer.stderr)
        pid, log, gpu, *args = seen.read_text(encoding="utf-8").splitlines()
        self.assertEqual((root / "shim.pid").read_text(encoding="utf-8").strip(), pid)
        self.assertEqual((log, gpu), (str(root / "moz.log"), "1"))
        self.assertEqual(args, ["--port", "0", "an argument"])
        self.assertEqual(
            (root / "shim.args").read_text(encoding="utf-8").splitlines(), args
        )


class RunSetupTest(unittest.TestCase):
    """What the run is given: its `PATH`, what it skips on, and where its scratch was."""

    def test_the_runs_path_puts_the_shim_folder_first_and_keeps_the_rest(self) -> None:
        base = {"PATH": f"/srv/a{os.pathsep}/srv/b", "OTHER": "kept"}
        env = fenced_env(Path("/srv/shim"), base)
        self.assertEqual(
            env,
            {"PATH": f"/srv/shim{os.pathsep}/srv/a{os.pathsep}/srv/b", "OTHER": "kept"},
        )
        self.assertEqual(base["PATH"], f"/srv/a{os.pathsep}/srv/b")

    def test_a_base_with_no_path_searches_the_shim_folder_then_the_default(
        self,
    ) -> None:
        env = fenced_env(Path("/srv/shim"), {})
        self.assertEqual(env["PATH"], f"/srv/shim{os.pathsep}{os.defpath}")

    def test_a_host_with_everything_skips_nothing(self) -> None:
        self.assertEqual(
            missing_prerequisites(
                platform="darwin",
                geckodriver="/srv/bin/geckodriver",
                firefox=True,
                sandbox_exec=True,
            ),
            (),
        )

    def test_each_thing_missing_is_named(self) -> None:
        self.assertEqual(
            missing_prerequisites(
                platform="linux", geckodriver=None, firefox=False, sandbox_exec=False
            ),
            (
                "macOS, not 'linux'",
                "geckodriver on PATH",
                "Firefox, installed as /Applications/Firefox.app",
                "/usr/bin/sandbox-exec",
            ),
        )

    def test_the_scratch_is_the_folder_holding_the_profile_root(self) -> None:
        args = ("--host", "127.0.0.1", "--profile-root", "/srv/scratch/profiles")
        self.assertEqual(scratch_root(args), Path("/srv/scratch"))

    def test_a_driver_given_no_profile_root_names_no_scratch(self) -> None:
        with self.assertRaisesRegex(ValueError, "--profile-root"):
            scratch_root(("--host", "127.0.0.1"))

    def test_a_geckodriver_found_on_a_relative_path_entry_is_made_absolute(
        self,
    ) -> None:
        """The shim runs it from the run's own folder, where a relative path means another."""
        geckodriver, _ = live_firefox_host(which=lambda name: f"bin/{name}")
        self.assertEqual(geckodriver, os.path.abspath("bin/geckodriver"))

    def test_no_geckodriver_on_path_is_named_missing(self) -> None:
        geckodriver, missing = live_firefox_host(which=lambda name: None)
        self.assertIsNone(geckodriver)
        self.assertIn("geckodriver on PATH", missing)


#: What a stand-in run records for an environment it was not passed at all.
_NOT_PASSED: Final = object()


class _RunRecorder:
    """A stand-in bounded run: records the environment each call passed, and answers exit 0."""

    def __init__(self) -> None:
        """No call yet."""
        self.envs: list[object] = []

    def __call__(
        self, argv: Sequence[str], **given: object
    ) -> subprocess.CompletedProcess[str]:
        """Keep the environment the run was passed, or `_NOT_PASSED`."""
        self.envs.append(given.get("env", _NOT_PASSED))
        return subprocess.CompletedProcess(list(argv), 0, "", "")


class RunEnvironmentTest(unittest.TestCase):
    """A make run is passed the environment the case gives, and None when it gives none."""

    def run_given(self, **env: Mapping[str, str]) -> object:
        """The environment a make run passes its bounded run, nothing launched."""
        recorder = _RunRecorder()
        with mock.patch.object(bounded_run, "run_bounded", recorder):
            _print_pass_live_fixtures.make_run_process(
                Path("/srv/site"), Path("/srv/out"), timeout=1.0, **env
            )
        self.assertEqual(len(recorder.envs), 1)
        return recorder.envs[0]

    def test_an_environment_given_is_the_runs(self) -> None:
        env = {"PATH": "/srv/shim"}
        self.assertIs(self.run_given(env=env), env)

    def test_no_environment_given_leaves_the_run_this_processs_own(self) -> None:
        self.assertIsNone(self.run_given())


if __name__ == "__main__":
    unittest.main()
