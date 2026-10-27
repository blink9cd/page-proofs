"""Suite: geckodriver's process group -- one SIGTERM to all of it, and the probe for what is left.

Both functions are proven on real child processes this suite starts in a session of their own,
so the only group either ever signals is one the suite made. The answers asserted are macOS's,
the one platform the Firefox engine runs on; a case whose answer differs elsewhere says so.

Entry point assumptions: standalone, or discovered by `unittest discover`. The inserts below
reach the subject's private `src/` beside this folder and the shared `bounded_run` beside
the tool's own, never the working directory.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
# The shared library: a folder beside this tool's own, named rather than searched for, so a
# same-named folder above the clone is never imported in its place, and put near the front
# of the path, ahead of every installed distribution, so none of the same name can shadow
# it.
sys.path.insert(1, str(Path(__file__).resolve().parents[2] / "bounded_run"))

import bounded_run  # noqa: E402 - a folder beside the tool's, via the insert above
import gecko_process  # noqa: E402 - the tool's private src/, via the insert above

#: Bound on any child process or wait here: far longer than each takes.
_TIMEOUT_S = 30.0

#: How often a wait on a child's state looks again.
_POLL_S = 0.02

#: A leader that starts a grandchild in its own group, writes the grandchild's pid to the
#: file named by argv[1] (whole, through a rename), then waits to be signaled.
_LEADER_SCRIPT = (
    "import os, subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "with open(sys.argv[1] + '.part', 'w', newline='\\n') as out:\n"
    "    out.write(str(child.pid))\n"
    "os.replace(sys.argv[1] + '.part', sys.argv[1])\n"
    "time.sleep(60)\n"
)


def _read_pid(path: Path) -> int:
    """The pid a leader wrote to `path`, once it is there; fails the case past the bound."""
    deadline = time.monotonic() + _TIMEOUT_S
    while time.monotonic() < deadline:
        with contextlib.suppress(FileNotFoundError):
            return int(path.read_text(encoding="utf-8"))
        time.sleep(_POLL_S)
    raise AssertionError(f"no pid was written within {_TIMEOUT_S} s")


def _gone(pid: int) -> bool:
    """Whether `pid` has ended and been reaped, waiting up to the bound for it."""
    deadline = time.monotonic() + _TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(_POLL_S)
    return False


def _await_unreaped_exit(pid: int) -> None:
    """Wait until child `pid` has exited, leaving it unreaped: a zombie still holding its group."""
    if sys.platform == "win32":
        raise unittest.SkipTest("Windows keeps no zombies")  # pragma: only-on win32
    # ps, not os.waitid: macOS gained os.waitid only in Python 3.13, below this folder's floor.
    deadline = time.monotonic() + _TIMEOUT_S
    while time.monotonic() < deadline:
        # Each call is bounded by what is left of the one deadline, so the whole wait is too.
        state = bounded_run.run_bounded(
            ["ps", "-o", "stat=", "-p", str(pid)],
            timeout=max(_POLL_S, deadline - time.monotonic()),
        ).stdout.strip()
        if state.startswith("Z"):
            return
        time.sleep(_POLL_S)
    raise AssertionError(f"the child had not exited within {_TIMEOUT_S} s")


def _kill_group(pgid: int) -> None:
    """SIGKILL whatever is left in group `pgid`, one this suite made; nothing left is no error."""
    if sys.platform == "win32":
        raise unittest.SkipTest("no process groups")  # pragma: only-on win32
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


def _group_ends(pgid: int) -> bool:
    """Whether `group_alive` finds group `pgid` empty, waiting up to the bound for it."""
    deadline = time.monotonic() + _TIMEOUT_S
    while time.monotonic() < deadline:
        if not gecko_process.group_alive(pgid):
            return True
        time.sleep(_POLL_S)
    return False


def _start_leader(*argv: str) -> subprocess.Popen[bytes]:
    """A Python child leading a session and group of its own, its streams closed."""
    return subprocess.Popen(
        [sys.executable, "-c", *argv],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _end_leader(leader: subprocess.Popen[bytes]) -> None:
    """Kill what a failed case left of the leader's tree, and reap the leader."""
    if leader.poll() is None:
        bounded_run.kill_tree(leader.pid)
    leader.wait(timeout=_TIMEOUT_S)


class TerminateGroupTests(unittest.TestCase):
    """SIGTERM reaches every process in the group; a group with nothing to signal is no error."""

    @unittest.skipIf(sys.platform == "win32", "Windows has no process groups to signal")
    def test_the_leader_and_its_child_both_end(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pid_file = Path(folder) / "grandchild"
            leader = _start_leader(_LEADER_SCRIPT, str(pid_file))
            self.addCleanup(_end_leader, leader)
            grandchild = _read_pid(pid_file)
            gecko_process.terminate_group(leader.pid)
            self.assertEqual(leader.wait(timeout=_TIMEOUT_S), -signal.SIGTERM)
            self.assertTrue(
                _gone(grandchild), "the grandchild outlived the group's SIGTERM"
            )

    def test_a_group_left_with_only_its_unreaped_leader_is_no_error(self) -> None:
        # macOS answers EPERM, not ESRCH, for a group whose one member is a zombie.
        leader = _start_leader("pass")
        self.addCleanup(_end_leader, leader)
        _await_unreaped_exit(leader.pid)
        gecko_process.terminate_group(leader.pid)
        self.assertEqual(leader.wait(timeout=_TIMEOUT_S), 0)

    def test_a_group_with_no_member_left_is_no_error(self) -> None:
        # ESRCH: the leader is reaped and nothing else was ever in its group.
        leader = _start_leader("pass")
        leader.wait(timeout=_TIMEOUT_S)
        gecko_process.terminate_group(leader.pid)


class GroupAliveTests(unittest.TestCase):
    """A group is alive while a member runs, its leader reaped or not; zombies run nothing."""

    def test_a_member_outliving_its_reaped_leader_keeps_the_group_alive(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pid_file = Path(folder) / "grandchild"
            leader = _start_leader(_LEADER_SCRIPT, str(pid_file))
            self.addCleanup(_end_leader, leader)
            _read_pid(pid_file)
            self.addCleanup(_kill_group, leader.pid)
            # The leader alone: its child stays in the group, a straggler, as Firefox would.
            leader.kill()
            leader.wait(timeout=_TIMEOUT_S)
            self.assertTrue(gecko_process.group_alive(leader.pid))
            _kill_group(leader.pid)
            self.assertTrue(_group_ends(leader.pid), "the straggler outlived SIGKILL")

    @unittest.skipUnless(sys.platform == "darwin", "the zombie's answer is macOS's")
    def test_a_group_whose_one_member_is_a_zombie_is_not_alive(self) -> None:
        # macOS answers the null signal with EPERM, not ESRCH, while only zombies remain.
        leader = _start_leader("pass")
        self.addCleanup(_end_leader, leader)
        _await_unreaped_exit(leader.pid)
        self.assertFalse(gecko_process.group_alive(leader.pid))

    def test_a_group_with_no_member_left_is_not_alive(self) -> None:
        leader = _start_leader("pass")
        leader.wait(timeout=_TIMEOUT_S)
        self.assertFalse(gecko_process.group_alive(leader.pid))


if __name__ == "__main__":
    unittest.main()
