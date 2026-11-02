"""Grouped suite: one Firefox state end to end (its session, window, storage, page and print).

A state opens a session asking for the isolation and its own prefs, notes the Firefox it
names, sizes the window until the page reads the viewport asked, seeds storage from the
loopback server's own error page when the state stores anything, loads the page, reads back
its network, refuses a page that left loopback or is not the served one, prints, and records.
Whatever happens, the session's crash helper is recorded and then the session deleted.
Actions, settling and the print moment are `test_gecko_state_actions.py`'s, and the
refusals of a page that left loopback `test_gecko_state_isolation.py`'s. Each case runs the
real `run_state` against `_gecko_state_fixtures`' scripted session and driver.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Final
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

from _gecko_state_fixtures import (  # noqa: E402 - beside this module
    DEAD_PORT,
    FIREFOX_PID,
    NO_FIREFOX,
    PDF,
    VERSION,
    StateCase,
    timing,
    webdriver_error,
)
from _print_engine_fixtures import BASE_URL, VIEWPORT, state, target  # noqa: E402
from _print_playwright_fixtures import call, probe_dict  # noqa: E402 - as above
import gecko_state  # noqa: E402 - the tool's private src/
from gecko_budget import state_seconds  # noqa: E402 - as above
from gecko_print import MARGIN_INCHES  # noqa: E402 - as above
from gecko_launch import (  # noqa: E402 - as above
    capabilities,
    isolation_prefs,
    state_prefs,
)
from print_probe import parse_probe_result  # noqa: E402 - as above
from print_record import (  # noqa: E402 - as above
    EngineError,
    MadePdf,
    NetworkRecord,
    Problem,
    StateRecord,
)

#: The margin each Firefox PDF records it was printed with: the one the command is sent.
_MARGIN: Final = MARGIN_INCHES

#: The argument the probe is called with for a target with no reads, its bounds included.
_PROBE_ARG: Final = {"reads": [], "storageKeys": [], "maxText": 1000, "maxItems": 5000}

#: The seed path's whole URL on the served origin.
_SEED_URL = f"{BASE_URL}/.print-pass-storage-seed"


class OrderTests(StateCase):
    """A quiet state, from its session to its deletion, and the record it makes."""

    def test_a_state_runs_in_the_order_a_print_dialog_would_meet_it(self) -> None:
        self.run_state()
        self.assertEqual(
            self.calls("new_session"),
            [
                call("note_firefox", FIREFOX_PID),
                call("set_window_size", VIEWPORT.width, VIEWPORT.height),
                call("execute viewport"),
                call("navigate", f"{BASE_URL}/"),
                call("execute probe", _PROBE_ARG),
                call("execute probe", _PROBE_ARG),
                call("execute timing"),
                call("print", False),
                call("record_helpers"),
                call("delete"),
            ],
        )

    def test_the_session_asks_for_the_isolation_and_the_states_prefs(self) -> None:
        dark = state(color_scheme="dark", reduced_motion="reduce")
        self.run_state(job=self.job(dark))
        prefs = isolation_prefs(DEAD_PORT, served=BASE_URL) | state_prefs(
            color_scheme="dark", reduced_motion="reduce"
        )
        asked = capabilities(prefs=prefs, timeout_ms=5000)
        self.assertEqual(self.log[1], call("new_session", asked))

    def test_a_timeout_shorter_than_a_millisecond_asks_for_one(self) -> None:
        self.run_state(job=self.job(timeout_s=0.0001))
        sent = [entry for entry in self.log if entry[0] == "new_session"]
        self.assertIn("'pageLoad': 1,", sent[0][1])

    def test_the_whole_state_runs_inside_one_bound_counting_every_step(self) -> None:
        self.run_state()
        what = "firefox state home/default"
        seconds = state_seconds(state(), timeout_s=5.0, prints=1)
        self.assertEqual(self.log[0], call("bound", what, seconds))
        self.assertEqual(self.log[-1], call("unbound", what))
        self.assertEqual([entry[0] for entry in self.log].count("bound"), 1)

    def test_the_record_is_firefoxs_with_nothing_emulated(self) -> None:
        record = self.run_state()
        quiet = NetworkRecord(
            failed=(), http_errors=(), blocked=(), page_errors=(), csp_violations=()
        )
        expected = StateRecord(
            target="home",
            state="default",
            page="/",
            engine="firefox",
            browser_version=VERSION,
            viewport=VIEWPORT,
            color_scheme="native",
            color_scheme_emulated=False,
            reduced_motion="native",
            reduced_motion_emulated=False,
            storage=(),
            actions=(),
            print_at_ms=None,
            page_width=None,
            margin_inches=_MARGIN,
            probe=parse_probe_result(probe_dict()),
            network=quiet,
            load_ms=1000,
            printed_at_ms=2000,
            pdfs=(MadePdf(False, self.pdf_dir / "home--default--bg-off.pdf", _MARGIN),),
            problems=(),
        )
        self.assertEqual(record, expected)

    def test_a_dark_state_is_not_given_the_emulated_dark_warning(self) -> None:
        dark = probe_dict(colorScheme="dark")
        record = self.run_state(
            self.session(probes=[dark]), self.job(state(color_scheme="dark"))
        )
        self.assertIs(record.color_scheme_emulated, False)
        self.assertEqual(record.problems, ())

    def test_a_native_dark_state_stays_firefoxs(self) -> None:
        light = probe_dict(colorScheme="light")
        record = self.run_state(
            self.session(probes=[light]), self.job(state(color_scheme="native-dark"))
        )
        self.assertEqual(record.engine, "firefox")
        self.assertEqual(
            record.problems,
            (Problem("finding", "native dark not honored: the page reads light"),),
        )


class ViewportTests(StateCase):
    """The window is sized until the page reads the viewport asked, exactly, or refused."""

    def test_a_viewport_short_of_the_window_is_corrected_once(self) -> None:
        session = self.session(viewports=[[1280, 676], [1280, 800]])
        self.run_state(session)
        sized = [entry for entry in self.log if entry[0] == "set_window_size"]
        self.assertEqual(
            sized,
            [call("set_window_size", 1280, 800), call("set_window_size", 1280, 924)],
        )

    def test_a_viewport_still_off_after_the_correction_is_refused(self) -> None:
        session = self.session(viewports=[[1280, 676], [1280, 799]])
        with self.assertRaises(EngineError) as caught:
            self.run_state(session)
        self.assertEqual(
            str(caught.exception),
            "home/default: the viewport is 1280x799 after one correction, "
            "not the 1280x800 asked",
        )
        self.assertNotIn(call("navigate", f"{BASE_URL}/"), self.log)
        self.assertEqual(self.calls()[-2:], [call("record_helpers"), call("delete")])

    def test_a_viewport_read_back_as_no_size_is_refused(self) -> None:
        for reply in (
            None,
            [1280],
            [1280, "800"],
            [True, 800],
            [1280.5, 800],
            "1280x800",
        ):
            with self.subTest(reply=reply):
                self.log.clear()
                with self.assertRaises(EngineError) as caught:
                    self.run_state(self.session(viewports=[reply]))
                self.assertIn(
                    "the viewport read back as no width and height",
                    str(caught.exception),
                )


class StorageTests(StateCase):
    """Stored values are set on the server's own error page, before the page's scripts run."""

    def test_storage_is_set_on_the_seed_page_before_the_page_loads(self) -> None:
        stored = state(storage=(("mode", "dark"), ("seen", "1")))
        self.run_state(job=self.job(stored))
        self.assertEqual(
            self.calls("new_session", "note_firefox", "set_window_size")[1:5],
            [
                call("navigate", _SEED_URL),
                call("execute seed"),
                call("execute store", [["mode", "dark"], ["seen", "1"]]),
                call("navigate", f"{BASE_URL}/"),
            ],
        )

    def test_a_state_storing_nothing_never_visits_the_seed_page(self) -> None:
        self.run_state()
        self.assertNotIn(call("navigate", _SEED_URL), self.log)

    def test_a_seed_page_that_is_not_the_servers_own_error_page_is_refused(
        self,
    ) -> None:
        stored = state(storage=(("mode", "dark"),))
        for seed in ([200, 0], [404, 2], [None, 0], [404.0, 0], [True, 0], None, [404]):
            with self.subTest(seed=seed):
                self.log.clear()
                with self.assertRaises(EngineError) as caught:
                    self.run_state(self.session(seed=seed), self.job(stored))
                self.assertIn("/.print-pass-storage-seed", str(caught.exception))
                self.assertNotIn(call("navigate", f"{BASE_URL}/"), self.log)
                self.assertEqual(
                    self.calls()[-2:], [call("record_helpers"), call("delete")]
                )


class SessionTests(StateCase):
    """The session's start and end: Firefox missing, its process, and record-then-delete."""

    def test_a_firefox_geckodriver_cannot_find_names_how_to_install_it(self) -> None:
        missing = webdriver_error("new session", "session not created", NO_FIREFOX)
        with self.assertRaises(EngineError) as caught:
            self.run_state(failure=missing)
        text = str(caught.exception)
        self.assertTrue(text.startswith("home/default: Firefox did not start: "), text)
        self.assertIn("brew install --cask firefox", text)
        self.assertIn("Mozilla's installer", text)
        self.assertEqual(self.calls("new_session"), [])

    def test_another_new_session_failure_is_named_without_the_install_hint(
        self,
    ) -> None:
        refused = webdriver_error("new session", "invalid argument", "bad prefs")
        with self.assertRaises(EngineError) as caught:
            self.run_state(failure=refused)
        self.assertEqual(
            str(caught.exception),
            "home/default: new session: invalid argument: bad prefs",
        )

    def test_a_session_naming_no_firefox_process_is_refused_and_ended(self) -> None:
        with self.assertRaises(EngineError) as caught:
            self.run_state(self.session(process_id=None))
        self.assertIn("named no Firefox process", str(caught.exception))
        self.assertEqual(
            self.calls("new_session"), [call("record_helpers"), call("delete")]
        )

    def test_a_firefox_not_in_the_drivers_group_ends_the_session(self) -> None:
        self.driver = type(self.driver)(
            self.log, fail={"note_firefox": EngineError("Firefox 4321 is not running")}
        )
        with self.assertRaises(EngineError):
            self.run_state()
        self.assertEqual(
            self.calls("new_session"),
            [call("note_firefox", FIREFOX_PID), call("record_helpers"), call("delete")],
        )

    def test_a_failed_state_records_its_helpers_then_deletes_its_session(self) -> None:
        failing = webdriver_error("navigate", "timeout", "page load timed out")
        session = self.session(fail={f"navigate {BASE_URL}/": failing})
        with self.assertRaises(EngineError) as caught:
            self.run_state(session)
        self.assertEqual(
            str(caught.exception),
            "home/default: / did not load: navigate: timeout: page load timed out",
        )
        self.assertEqual(self.calls()[-2:], [call("record_helpers"), call("delete")])

    def test_a_delete_that_fails_after_a_good_state_fails_the_state(self) -> None:
        gone = webdriver_error("delete session", "invalid session id")
        with self.assertRaises(EngineError) as caught:
            self.run_state(self.session(fail={"delete": gone}))
        self.assertEqual(
            str(caught.exception),
            "home/default: delete session: invalid session id: it failed",
        )

    def test_a_listing_that_fails_still_deletes_the_session(self) -> None:
        self.driver = type(self.driver)(
            self.log, fail={"record_helpers": EngineError("the listing failed")}
        )
        with self.assertRaises(EngineError) as caught:
            self.run_state()
        self.assertEqual(str(caught.exception), "home/default: the listing failed")
        self.assertEqual(self.calls()[-2:], [call("record_helpers"), call("delete")])

    def test_an_end_that_fails_after_a_failure_names_both(self) -> None:
        failing = webdriver_error("navigate", "unknown error", "browser gone")
        gone = webdriver_error("delete session", "invalid session id", "no session")
        session = self.session(fail={f"navigate {BASE_URL}/": failing, "delete": gone})
        with self.assertRaises(EngineError) as caught:
            self.run_state(session)
        self.assertEqual(
            str(caught.exception),
            "home/default: / did not load: navigate: unknown error: browser gone; and the "
            "session could not be ended: delete session: invalid session id: no session",
        )


class NetworkTests(StateCase):
    """The record is the rows read back and the state's own slice of the log."""

    def test_the_rows_and_the_logs_page_errors_make_the_record(self) -> None:
        session = self.session(
            timing_reply=timing(
                ["http://192.0.2.1/x.png", 0],
                [f"{BASE_URL}/missing.png", 404],
                [f"{BASE_URL}/font.woff2", 0],
            ),
            on_navigate=lambda url: self.driver.write(
                f"JavaScript error: {BASE_URL}/app.js, line 3: ReferenceError: x is not "
                "defined\nJavaScript error: resource://gre/x.mjs, line 1: TypeError: no\n"
            ),
        )
        record = self.run_state(session)
        self.assertEqual(
            record.network,
            NetworkRecord(
                failed=("/font.woff2 (no response)",),
                http_errors=("404 /missing.png",),
                blocked=("http://192.0.2.1/x.png",),
                page_errors=("ReferenceError: x is not defined",),
                csp_violations=(),
            ),
        )

    def test_page_errors_logged_before_the_state_are_not_its_own(self) -> None:
        self.driver.write(f"JavaScript error: {BASE_URL}/old.js, line 1: Error: old\n")
        record = self.run_state()
        self.assertEqual(record.network.page_errors, ())

    def test_a_full_resource_buffer_is_warned_of(self) -> None:
        rows = [[f"{BASE_URL}/{index}.png", 200] for index in range(250)]
        record = self.run_state(self.session(timing_reply=timing(*rows)))
        self.assertIn("network record may be incomplete", record.problems[0].what)

    def test_a_log_slice_past_the_read_cap_is_warned_of(self) -> None:
        # A page that floods its console fills the log; past the cap, page errors may be
        # missing from the record, and a reader is told so.
        session = self.session(on_navigate=lambda url: self.driver.write("x" * 11))
        with mock.patch.object(gecko_state, "MAX_LOG_READ_BYTES", 10):
            record = self.run_state(session)
        self.assertIn(
            Problem(
                "warning",
                "page errors may be incomplete: only the last 10 bytes of the "
                "state's log were read",
            ),
            record.problems,
        )

    def test_the_scratch_root_never_reaches_the_record(self) -> None:
        # Both forms a log can name the scratch by: as the run made it, and resolved, as
        # macOS's temporary folder is reached through a symlink. The state is handed a
        # symlink to the real root, so the two differ on every platform.
        real = self.scratch_root.with_name("real-scratch")
        real.mkdir()
        self.scratch_root.symlink_to(real, target_is_directory=True)
        root, resolved = self.scratch_root, self.scratch_root.resolve()
        self.assertNotIn(str(root), str(resolved))
        session = self.session(
            on_navigate=lambda url: self.driver.write(
                f'1791248040499\tmozrunner::runner\tINFO\tRunning command: "firefox" '
                f'"-profile" "{root}/profiles/rust_mozprofileA1"\n'
                f"JavaScript error: {BASE_URL}/app.js, line 1: Error: no {root}/home or "
                f"{resolved}/home\n"
            )
        )
        record = self.run_state(session)
        self.assertEqual(
            record.network.page_errors, ("Error: no <scratch>/home or <scratch>/home",)
        )


class PrintTests(StateCase):
    """Each backgrounds side is printed from the one load, and written where the record says."""

    def test_both_sides_print_from_one_load_into_the_pdf_folder(self) -> None:
        record = self.run_state(job=self.job(backgrounds="both"))
        self.assertEqual(
            [entry for entry in self.log if entry[0] in ("navigate", "print")],
            [
                call("navigate", f"{BASE_URL}/"),
                call("print", False),
                call("print", True),
            ],
        )
        off = self.pdf_dir / "home--default--bg-off.pdf"
        on = self.pdf_dir / "home--default--bg-on.pdf"
        self.assertEqual(
            record.pdfs, (MadePdf(False, off, _MARGIN), MadePdf(True, on, _MARGIN))
        )
        self.assertEqual((off.read_bytes(), on.read_bytes()), (PDF, PDF))

    def test_a_print_that_fails_fails_the_state_and_ends_its_session(self) -> None:
        failed = webdriver_error("print", "unknown error", "printing failed")
        with self.assertRaises(EngineError) as caught:
            self.run_state(self.session(fail={"print": failed}))
        self.assertEqual(
            str(caught.exception),
            "home/default: print failed: print: unknown error: printing failed",
        )
        self.assertEqual(self.calls()[-2:], [call("record_helpers"), call("delete")])

    def test_a_pdf_that_cannot_be_written_fails_the_state(self) -> None:
        self.pdf_dir.rmdir()
        with self.assertRaises(EngineError) as caught:
            self.run_state()
        self.assertIn(
            "home--default--bg-off.pdf could not be written", str(caught.exception)
        )
        self.assertEqual(self.calls()[-2:], [call("record_helpers"), call("delete")])

    def test_a_target_on_its_own_page_is_loaded_there(self) -> None:
        about = target("about", page="/about/", states=(state(),))
        session = self.session(
            timing_reply=timing(document=[f"{BASE_URL}/about/", 200])
        )
        record = self.run_state(session, self.job(the_target=about))
        self.assertIn(call("navigate", f"{BASE_URL}/about/"), self.log)
        self.assertEqual(record.pdfs[0].path.name, "about--default--bg-off.pdf")


if __name__ == "__main__":
    unittest.main()
