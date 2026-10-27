"""Suite: the platform, where geckodriver is, its scratch, environment and command, its port.

Every case here is a pure value, or a folder made under a temporary root: which platform the
engine runs on, what geckodriver is started with, and how its log is read. No case starts
geckodriver, Firefox or any other process, so a unit run never depends on a browser.

Entry point assumptions: standalone, or discovered by `unittest discover`. The insert below
reaches the subject's private `src/` beside this folder, never the working directory.
"""

from __future__ import annotations

import dataclasses
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_command  # noqa: E402 - the tool's private src/, via the insert above
import gecko_launch  # noqa: E402 - as above
from print_record import EngineError  # noqa: E402 - the tool's private src/

#: geckodriver's own line naming its port, as it writes it (a timestamp, then tabs).
_REAL_LINE = "1791229212618\tgeckodriver\tINFO\tListening on 127.0.0.1:65170\n"

#: The clause every refusal of another platform carries, word for word, so that the refusal
#: and the install instructions give the reader one reason.
_MEASURED_ONLY_ON_MACOS = (
    "runs on macOS only: its isolation of Firefox's own folders is measured there alone"
)

#: Firefox's tripwire: with it set, a direct TCP connection to an address it counts as
#: non-local ends the browser.
_NONLOCAL = "MOZ_DISABLE_NONLOCAL_CONNECTIONS"

#: A digit `int()` reads as 4 that no port line holds: ARABIC-INDIC DIGIT FOUR.
_NON_ASCII_FOUR = chr(0x0664)

#: The real `Path.mkdir`, which a refusal's spy calls through to, so that a folder made before
#: the refusal would be made for real and seen.
_REAL_MKDIR = Path.mkdir


def _scratch(root: Path) -> gecko_launch.Scratch:
    """A scratch whose folders are never made: the pure functions only read its paths."""
    return gecko_launch.Scratch(
        root=root,
        home=root / "h",
        app_data=root / "a",
        local_app_data=root / "l",
        profiles=root / "p",
        log=root / "g.log",
    )


class InterfaceTests(unittest.TestCase):
    """`gecko_launch` gives each of these as the very object `gecko_command` defines."""

    def test_each_is_the_same_object_there(self) -> None:
        names = (
            "GECKODRIVER",
            "require_measured_platform",
            "find_geckodriver",
            "driver_env",
            "driver_command",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertIs(getattr(gecko_launch, name), getattr(gecko_command, name))


class PlatformTests(unittest.TestCase):
    """The engine runs only where its isolation of Firefox's own folders was measured: macOS."""

    def test_macos_is_measured_so_it_runs(self) -> None:
        # Passing is returning: the refusal is the only thing it does.
        gecko_launch.require_measured_platform("darwin")

    def test_every_other_platform_is_refused_in_one_line(self) -> None:
        for platform in ("linux", "win32", "cygwin", "freebsd14", ""):
            with self.subTest(platform=platform):
                with self.assertRaises(EngineError) as raised:
                    gecko_launch.require_measured_platform(platform)
                message = str(raised.exception)
                self.assertIn(_MEASURED_ONLY_ON_MACOS, message)
                self.assertIn(repr(platform), message)
                self.assertNotIn("\n", message)

    def test_the_platform_it_judges_by_default_is_this_one(self) -> None:
        signature = inspect.signature(gecko_launch.require_measured_platform)
        self.assertEqual(signature.parameters["platform"].default, sys.platform)


class FindGeckodriverTests(unittest.TestCase):
    """geckodriver is found by name on PATH, through the lookup handed in."""

    def test_the_lookup_is_asked_for_geckodriver(self) -> None:
        asked: list[str] = []

        def which(name: str) -> str | None:
            asked.append(name)
            return "bin/geckodriver"

        self.assertEqual(gecko_launch.find_geckodriver(which), "bin/geckodriver")
        self.assertEqual(asked, ["geckodriver"])

    def test_none_when_it_is_not_installed(self) -> None:
        self.assertIsNone(gecko_launch.find_geckodriver(lambda name: None))


class MakeScratchTests(unittest.TestCase):
    """Every folder the run redirects Firefox into exists under the root, and nowhere else.

    A root that is relative, a symlink or no folder is refused before anything is made.
    """

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name) / "run"

    def test_each_folder_is_made_under_the_root(self) -> None:
        scratch = gecko_launch.make_scratch(self.root)
        folders = (
            scratch.home,
            scratch.app_data,
            scratch.local_app_data,
            scratch.profiles,
        )
        self.assertEqual(scratch.root, self.root)
        self.assertEqual(len(set(folders)), len(folders))
        for folder in folders:
            with self.subTest(folder=folder.name):
                self.assertTrue(folder.is_dir())
                self.assertEqual(folder.parent, self.root)

    def test_the_log_lies_under_the_root_and_is_left_for_the_driver(self) -> None:
        scratch = gecko_launch.make_scratch(self.root)
        self.assertEqual(scratch.log.parent, self.root)
        self.assertFalse(scratch.log.exists())

    def test_the_home_already_holds_the_folders_macos_would_lock(self) -> None:
        # Made by the system under a scratch home, each carries an everyone-deny-delete ACL,
        # and a plain recursive removal of the scratch then fails: Library and Downloads as
        # the session starts, and Documents as it stopped in 2 of 11 measured runs.
        home = gecko_launch.make_scratch(self.root).home
        for name in ("Library", "Downloads", "Documents"):
            with self.subTest(folder=name):
                self.assertTrue((home / name).is_dir())

    def test_a_scratch_made_twice_is_the_same_scratch(self) -> None:
        first = gecko_launch.make_scratch(self.root)
        self.assertEqual(gecko_launch.make_scratch(self.root), first)

    def test_a_relative_root_is_refused_and_nothing_made(self) -> None:
        # Resolved against whatever the working directory is, it could name anything, and
        # the removal at the end of the run would refuse it.
        for root in (Path("scratch"), Path(".")):
            with (
                self.subTest(root=str(root)),
                mock.patch.object(Path, "mkdir") as mkdir,
            ):
                with self.assertRaises(EngineError) as raised:
                    gecko_launch.make_scratch(root)
                message = str(raised.exception)
                self.assertIn("is not absolute", message)
                self.assertIn(str(root), message)
                self.assertNotIn("\n", message)
                mkdir.assert_not_called()

    def refusal(self, root: Path) -> str:
        """The one-line message `make_scratch` refuses `root` with, having made no folder.

        `Path.mkdir` is spied on, each call passed through, so a folder made on the way would
        be both counted and made.
        """
        with mock.patch.object(
            Path, "mkdir", autospec=True, side_effect=_REAL_MKDIR
        ) as mkdir:
            with self.assertRaises(EngineError) as raised:
                gecko_launch.make_scratch(root)
        mkdir.assert_not_called()
        message = str(raised.exception)
        self.assertIn(str(root), message)
        self.assertNotIn("\n", message)
        return message

    def test_a_symlinked_root_is_refused_and_nothing_made_in_its_target(self) -> None:
        # A scratch made through the link would land in whatever it points at, and the removal
        # at the end of the run refuses a symlinked root.
        target = self.root.parent / "elsewhere"
        target.mkdir()
        self.root.symlink_to(target, target_is_directory=True)
        self.assertIn("is a symlink", self.refusal(self.root))
        self.assertEqual(list(target.iterdir()), [])

    def test_a_dangling_symlinked_root_is_refused_and_its_target_not_made(self) -> None:
        target = self.root.parent / "elsewhere"
        self.root.symlink_to(target, target_is_directory=True)
        self.assertIn("is a symlink", self.refusal(self.root))
        self.assertFalse(target.exists())

    def test_a_root_that_is_a_file_is_refused(self) -> None:
        self.root.write_bytes(b"")
        self.assertIn("is not a folder", self.refusal(self.root))
        self.assertEqual(self.root.read_bytes(), b"")

    def test_a_root_that_cannot_be_examined_is_refused(self) -> None:
        # Under a file, a path names nothing that can be looked at, which is not the same as
        # naming nothing yet: the system says ENOTDIR, not ENOENT.
        self.root.write_bytes(b"")
        message = self.refusal(self.root / "scratch")
        self.assertIn("could not be examined", message)

    def test_a_scratch_is_frozen(self) -> None:
        scratch = gecko_launch.make_scratch(self.root)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            scratch.root = self.root.parent  # type: ignore[misc]  # the assignment under test


class DriverEnvTests(unittest.TestCase):
    """Firefox's folders move into the scratch, and its non-local guard is always set.

    With the guard set, a direct TCP connection to an address Firefox counts as non-local is
    fatal to the browser.
    """

    def test_each_redirect_names_its_scratch_folder(self) -> None:
        scratch = _scratch(Path("scratch"))
        env = gecko_launch.driver_env({}, scratch)
        self.assertEqual(
            env,
            {
                "CFFIXED_USER_HOME": str(scratch.home),
                "MOZ_APP_DATA": str(scratch.app_data),
                "MOZ_LOCAL_APP_DATA": str(scratch.local_app_data),
                "TMPDIR": str(scratch.root),
                _NONLOCAL: "1",
            },
        )

    def test_the_base_cannot_lift_the_non_local_tripwire(self) -> None:
        for value in ("0", "", "no"):
            with self.subTest(value=value):
                env = gecko_launch.driver_env({_NONLOCAL: value}, _scratch(Path("s")))
                self.assertEqual(env[_NONLOCAL], "1")

    def test_the_rest_of_the_base_is_kept_and_the_base_left_alone(self) -> None:
        base = {"PATH": "bin", "TMPDIR": "elsewhere"}
        env = gecko_launch.driver_env(base, _scratch(Path("scratch")))
        self.assertEqual(env["PATH"], "bin")
        self.assertEqual(env["TMPDIR"], "scratch")
        self.assertEqual(base, {"PATH": "bin", "TMPDIR": "elsewhere"})

    def test_firefoxs_own_switches_and_logs_in_the_base_never_reach_it(self) -> None:
        # Each would change what Firefox does, or where it writes: a log file of its own,
        # a crash report's destination, its content sandbox off, TLS keys written out.
        base = {
            "PATH": "bin",
            "MOZ_LOG_FILE": "log",
            "MOZ_CRASHREPORTER_URL": "http://192.0.2.1/",
            "MOZ_DISABLE_CONTENT_SANDBOX": "1",
            "MOZ_FORCE_DISABLE_E10S": "1",
            "NSPR_LOG_MODULES": "all:5",
            "NSPR_LOG_FILE": "nspr",
            "SSLKEYLOGFILE": "keys",
            "XMOZ_KEPT": "kept",
        }
        env = gecko_launch.driver_env(base, _scratch(Path("scratch")))
        self.assertEqual(
            sorted(env),
            [
                "CFFIXED_USER_HOME",
                "MOZ_APP_DATA",
                _NONLOCAL,
                "MOZ_LOCAL_APP_DATA",
                "PATH",
                "TMPDIR",
                "XMOZ_KEPT",
            ],
        )


class DriverCommandTests(unittest.TestCase):
    """geckodriver listens on loopback, on a port the system picks, with profiles in the scratch."""

    def test_the_whole_command(self) -> None:
        scratch = _scratch(Path("scratch"))
        self.assertEqual(
            gecko_launch.driver_command("bin/geckodriver", scratch),
            [
                "bin/geckodriver",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--profile-root",
                str(scratch.profiles),
                "--log",
                "info",
            ],
        )


class ListeningPortTests(unittest.TestCase):
    """The port comes from a whole `Listening on 127.0.0.1:<port>` line, and only that."""

    def test_geckodrivers_own_line(self) -> None:
        self.assertEqual(gecko_launch.listening_port(_REAL_LINE), 65170)

    def test_a_line_still_being_written_names_nothing(self) -> None:
        # Its last digits may not have arrived, so a port read now could be a prefix of it.
        self.assertIsNone(gecko_launch.listening_port(_REAL_LINE.rstrip("\n")))

    def test_a_carriage_return_before_the_newline_is_accepted(self) -> None:
        line = "Listening on 127.0.0.1:4444\r\n"
        self.assertEqual(gecko_launch.listening_port(line), 4444)

    def test_no_text_and_no_line_name_nothing(self) -> None:
        for text in ("", "geckodriver\tINFO\tStarting\n"):
            with self.subTest(text=text):
                self.assertIsNone(gecko_launch.listening_port(text))

    def test_any_other_host_names_nothing(self) -> None:
        for host in ("0.0.0.0", "127.0.0.10", "127.0.0.2", "localhost", "[::1]"):
            with self.subTest(host=host):
                text = f"Listening on {host}:4444\n"
                self.assertIsNone(gecko_launch.listening_port(text))

    def test_the_ends_of_the_port_range(self) -> None:
        for port in (1, 65535):
            with self.subTest(port=port):
                text = f"Listening on 127.0.0.1:{port}\n"
                self.assertEqual(gecko_launch.listening_port(text), port)

    def test_a_port_out_of_range_or_padded_names_nothing(self) -> None:
        for port in ("0", "65536", "99999", "123456", "080", "-1", "", "4e3"):
            with self.subTest(port=port):
                text = f"Listening on 127.0.0.1:{port}\n"
                self.assertIsNone(gecko_launch.listening_port(text))

    def test_the_first_line_naming_a_port_decides(self) -> None:
        text = (
            "Listening on 127.0.0.1:65536\n"
            "Listening on 127.0.0.1:4444\n"
            "Listening on 127.0.0.1:5555\n"
        )
        self.assertEqual(gecko_launch.listening_port(text), 4444)

    def test_digits_beyond_ascii_are_not_a_port(self) -> None:
        # int() reads these as 4444, but geckodriver never writes them.
        text = f"Listening on 127.0.0.1:{_NON_ASCII_FOUR * 4}\n"
        self.assertIsNone(gecko_launch.listening_port(text))


if __name__ == "__main__":
    unittest.main()
