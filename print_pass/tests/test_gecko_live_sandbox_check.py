"""Grouped suite: the fence's check from outside -- libSystem's `sandbox_check`, and its verdict.

Before anything launches, the proof asks `sandbox_check` about the canary process while it
runs under the profile: the canary must read as sandboxed, and the asking process as not, so
the unfiltered question is shown to tell the two apart; the canary's lookup of the resolver's
mach service and its read under the canary folder must be denied; and a lookup the profile
allows must not be (the control). Here the C function is a stand-in that records each call,
so what is asked, how, and what each answer means are proven on any host; that the real
library answers is proven on macOS, where it lives, and live in `test_gecko_live_proof.py`.
The binding itself is the canary's, which asks the same function about itself.

Entry point assumptions: discovered by the folder's aggregator, or run alone from this
folder; the fixtures' bootstrap puts the tool's folder and its private `src/` on the path.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Final

# First, beside this module via the loader's top-level folder. It imports the canary
# beside it, and the standard library alone.
import _gecko_sandbox_check as check

import _gecko_canary as canary  # beside this module

#: A stand-in flag for "do not report", as libSystem exports it, and two pids.
_NO_REPORT: Final = 0x40
_CANARY_PID: Final = 10
_OWN_PID: Final = 20

#: A stand-in canary file, under a folder naming no real machine.
_CANARY_FILE: Final = Path("/srv/fence/canary/canary.txt")

#: Every answer as the fence that holds gives it.
_HELD: Final = dict(check.REQUIRED_ANSWERS)


class _Recorder:
    """A stand-in `sandbox_check`: records each call, and answers from a table."""

    def __init__(self, answers: dict[bytes | int, int]) -> None:
        """Keep the answers: a filtered call's keyed by its value, an unfiltered one's by pid."""
        self.answers = answers
        self.calls: list[tuple[object, ...]] = []

    def __call__(
        self, pid: int, operation: bytes | None, kind: int, *args: bytes
    ) -> int:
        """Record the call; answer by its filter's value, or by its pid when it has none."""
        self.calls.append((pid, operation, kind, *args))
        return self.answers.get(args[0] if args else pid, -1)


def _held_recorder(own_pid: int = _OWN_PID) -> _Recorder:
    """A stand-in answering as a held fence does, for this suite's canary and file.

    `own_pid` is the asker's: a stand-in one, or, where the checker asks, this process's.
    """
    return _Recorder(
        {
            _CANARY_PID: 1,
            canary.DNSSD_SERVICE.encode(): 1,
            str(_CANARY_FILE).encode(): 1,
            check.CONTROL_SERVICE.encode(): 0,
            own_pid: 0,
        }
    )


class SandboxCheckCallsTest(unittest.TestCase):
    """What is asked of `sandbox_check`, and in what form libSystem takes it."""

    def test_each_question_is_asked_as_libsystem_takes_it(self) -> None:
        recorder = _held_recorder()
        answers = check.canary_answers(
            canary.SandboxCheck(recorder, _NO_REPORT),
            _CANARY_PID,
            _CANARY_FILE,
            _OWN_PID,
        )
        self.assertEqual(
            recorder.calls,
            [
                (_CANARY_PID, None, 0),
                (
                    _CANARY_PID,
                    b"mach-lookup",
                    2 | _NO_REPORT,
                    b"com.apple.dnssd.service",
                ),
                (
                    _CANARY_PID,
                    b"file-read-data",
                    1 | _NO_REPORT,
                    str(_CANARY_FILE).encode(),
                ),
                (
                    _CANARY_PID,
                    b"mach-lookup",
                    2 | _NO_REPORT,
                    b"com.apple.cfprefsd.daemon",
                ),
                (_OWN_PID, None, 0),
            ],
        )
        self.assertEqual(answers, _HELD)

    def test_a_library_that_is_not_there_cannot_be_loaded(self) -> None:
        with self.assertRaises(OSError):
            canary.load_sandbox_check("/srv/no-such-library.dylib")

    @unittest.skipUnless(sys.platform == "darwin", "libSystem is macOS's")
    def test_libsystem_answers_that_this_process_is_not_sandboxed(self) -> None:
        self.assertEqual(canary.load_sandbox_check().is_sandboxed(os.getpid()), 0)


class SandboxVerdictTest(unittest.TestCase):
    """The answers pass only as a held fence gives them; any other answer fails, named."""

    def test_the_answers_of_a_held_fence_pass(self) -> None:
        self.assertEqual(check.answer_failures(_HELD), ())

    def test_each_other_answer_fails_naming_its_question(self) -> None:
        for question, required in check.REQUIRED_ANSWERS:
            for wrong in (1 - required, -1):
                with self.subTest(question=question, wrong=wrong):
                    self.assertEqual(
                        check.answer_failures({**_HELD, question: wrong}),
                        (
                            f"sandbox_check: {question} answered {wrong},"
                            f" not {required}",
                        ),
                    )

    def test_a_question_never_answered_fails(self) -> None:
        question, required = check.REQUIRED_ANSWERS[0]
        answers = {key: value for key, value in _HELD.items() if key != question}
        self.assertEqual(
            check.answer_failures(answers),
            (f"sandbox_check: {question} answered None, not {required}",),
        )


class CanaryCheckerTest(unittest.TestCase):
    """The checker waits for the canary's report, asks about its pid, then releases it."""

    def setUp(self) -> None:
        """A folder for the canary's report and the checker's marker."""
        folder = tempfile.TemporaryDirectory(prefix="print_pass-check-")
        self.addCleanup(folder.cleanup)
        self.report = Path(folder.name) / "canary.json"
        self.marker = canary.checked_marker(self.report)

    def write_report(self) -> None:
        """The canary's report, naming its pid."""
        canary.write_report(self.report, {"pid": _CANARY_PID, "ppid": 1, "results": []})

    def checker(
        self, recorder: _Recorder, *, bound: float = 5.0
    ) -> check.CanaryChecker:
        """A checker over `recorder`, as if libSystem had been loaded."""
        return check.CanaryChecker(
            self.report,
            _CANARY_FILE,
            bound_seconds=bound,
            load=lambda: canary.SandboxCheck(recorder, _NO_REPORT),
            poll_seconds=0.0,
        )

    def test_a_held_fence_passes_and_the_canary_is_released(self) -> None:
        """Asked unfiltered, the canary reads sandboxed and this process not: both asked."""
        self.write_report()
        recorder = _held_recorder(os.getpid())
        with self.checker(recorder) as checker:
            pass
        self.assertEqual(checker.failures, ())
        self.assertEqual(checker.answers, _HELD)
        unfiltered = [call for call in recorder.calls if call[1] is None]
        self.assertEqual(unfiltered, [(_CANARY_PID, None, 0), (os.getpid(), None, 0)])
        self.assertTrue(self.marker.is_file())

    def test_answers_a_held_fence_would_not_give_fail_the_check(self) -> None:
        self.write_report()
        with self.checker(_Recorder({})) as checker:
            pass
        self.assertEqual(
            checker.failures,
            tuple(
                f"sandbox_check: {question} answered -1, not {required}"
                for question, required in check.REQUIRED_ANSWERS
            ),
        )

    def test_a_library_that_cannot_be_reached_fails_and_never_skips(self) -> None:
        """No library, no function in it, or no flag: each fails, and the canary is let go."""
        self.write_report()
        for error in (
            OSError("dlopen: no such library"),
            AttributeError("no sandbox_check"),
            ValueError("no SANDBOX_CHECK_NO_REPORT"),
        ):
            with self.subTest(error=type(error).__name__):
                self.marker.unlink(missing_ok=True)

                def unreachable(error: Exception = error) -> canary.SandboxCheck:
                    raise error

                checker = check.CanaryChecker(
                    self.report, _CANARY_FILE, bound_seconds=5.0, load=unreachable
                )
                with checker:
                    pass
                self.assertEqual(
                    checker.failures,
                    (f"sandbox_check cannot be reached on this host: {error}",),
                )
                self.assertTrue(self.marker.is_file())

    def test_a_canary_that_wrote_no_report_fails_the_check(self) -> None:
        with self.checker(_held_recorder(os.getpid())) as checker:
            pass
        self.assertEqual(
            checker.failures,
            ("sandbox_check: the canary wrote no report naming its pid",),
        )
        self.assertFalse(self.marker.exists())

    def test_the_wait_for_the_report_ends_at_its_bound(self) -> None:
        checker = self.checker(_held_recorder(os.getpid()), bound=0.0)
        checker.check()
        self.assertEqual(
            checker.failures,
            ("sandbox_check: the canary wrote no report naming its pid",),
        )

    def test_a_checker_that_never_ran_fails_closed(self) -> None:
        checker = self.checker(_held_recorder(os.getpid()))
        self.assertEqual(checker.failures, ("sandbox_check never finished",))

    def test_the_report_read_is_the_one_the_canary_wrote(self) -> None:
        self.report.write_text(json.dumps({"pid": 77}), encoding="utf-8", newline="\n")
        recorder = _held_recorder(os.getpid())
        with self.checker(recorder):
            pass
        self.assertEqual({call[0] for call in recorder.calls}, {77, os.getpid()})


if __name__ == "__main__":
    unittest.main()
