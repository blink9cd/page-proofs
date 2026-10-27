"""Grouped suite: the scripts a Firefox state runs in its page, and what it accepts back.

Each script returns plain arrays of strings and numbers, never a Promise, and each answer is
type-checked here before anything is built from it: a page can redefine `performance`,
`innerWidth` or anything else a script reads, so an answer is data from the page, not a
fact. The probe is the Chromium engine's own, called as a synchronous script through an
adapter. The two checks on what reaches a record are here too: whether the document is the
served page, and the scratch folder's path taken out of the log.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import gecko_page  # noqa: E402 - the tool's private src/, via the insert above
from print_probe import PROBE_SOURCE  # noqa: E402 - as above
from print_record import EngineError  # noqa: E402 - as above

_BASE = "http://127.0.0.1:8123"


class ViewportTests(unittest.TestCase):
    """The viewport read back: two whole numbers, or refused."""

    def test_a_width_and_height_are_read_as_they_are(self) -> None:
        self.assertEqual(gecko_page.viewport_size([1280, 800]), (1280, 800))

    def test_anything_else_is_no_size(self) -> None:
        for raw in (
            None,
            [1280],
            [1280, 800, 1],
            [1280, "800"],
            [True, 800],
            [1280.0, 800],
            {"width": 1280},
            "1280x800",
            [-1, 800],
            [0, 800],
        ):
            with self.subTest(raw=raw), self.assertRaises(EngineError) as caught:
                gecko_page.viewport_size(raw)
            self.assertEqual(
                str(caught.exception), "the viewport read back as no width and height"
            )


class SeedTests(unittest.TestCase):
    """The storage seed page is the loopback server's own script-free 404, or not."""

    def test_the_path_is_reserved_and_on_the_served_origin(self) -> None:
        self.assertEqual(gecko_page.SEED_PATH, "/.print-pass-storage-seed")

    def test_a_404_without_scripts_is_the_servers_own_error_page(self) -> None:
        self.assertTrue(gecko_page.seed_confirmed([404, 0]))

    def test_anything_else_is_not(self) -> None:
        for raw in (
            [200, 0],
            [404, 1],
            [None, 0],
            [404.0, 0],
            [True, 0],
            [404, False],
            [404],
            [404, 0, 0],
            None,
            "404",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(gecko_page.seed_confirmed(raw))


class TimingTests(unittest.TestCase):
    """Resource Timing read back: one navigation row and the resource rows, each checked."""

    def test_well_formed_rows_are_read_as_entries(self) -> None:
        document, entries = gecko_page.timing_rows(
            [[[f"{_BASE}/", 200]], [[f"{_BASE}/a.png", 0], [f"{_BASE}/b.css", 404]]]
        )
        self.assertEqual(document, {"name": f"{_BASE}/", "responseStatus": 200})
        self.assertEqual(
            entries,
            (
                {"name": f"{_BASE}/a.png", "responseStatus": 0},
                {"name": f"{_BASE}/b.css", "responseStatus": 404},
            ),
        )

    def assert_malformed(self, raw: object, problem: str) -> None:
        """`raw` is refused, naming `problem`."""
        with self.assertRaises(EngineError) as caught:
            gecko_page.timing_rows(raw)
        self.assertEqual(
            str(caught.exception), f"Resource Timing read back malformed: {problem}"
        )

    def test_a_read_that_is_not_two_lists_is_refused(self) -> None:
        malformed: tuple[object, ...] = (
            None,
            [],
            [[]],
            [[], [], []],
            {"navigation": []},
            [[], "rows"],
        )
        for raw in malformed:
            with self.subTest(raw=raw):
                self.assert_malformed(raw, "not a navigation list and a resource list")

    def test_a_document_without_exactly_one_navigation_row_is_refused(self) -> None:
        for navigation in ([], [[f"{_BASE}/", 200], [f"{_BASE}/", 200]]):
            with self.subTest(navigation=navigation):
                self.assert_malformed(
                    [navigation, []], f"{len(navigation)} navigation rows, not 1"
                )

    def test_a_row_that_is_not_a_name_and_a_status_is_refused(self) -> None:
        for row in (
            None,
            "x",
            [f"{_BASE}/"],
            [f"{_BASE}/", 200, 0],
            [1, 200],
            [f"{_BASE}/", "200"],
            [f"{_BASE}/", 200.0],
            [f"{_BASE}/", True],
            [f"{_BASE}/", None],
        ):
            with self.subTest(row=row):
                self.assert_malformed(
                    [[[f"{_BASE}/", 200]], [row]], "a row is not a URL and a status"
                )

    def test_a_status_out_of_range_is_refused(self) -> None:
        for status in (-1, 1000, 2**53):
            with self.subTest(status=status):
                self.assert_malformed(
                    [[[f"{_BASE}/", status]], []],
                    f"a status of {status} is no HTTP status",
                )

    def test_the_status_range_is_inclusive(self) -> None:
        for status in (0, 999):
            with self.subTest(status=status):
                document, _ = gecko_page.timing_rows([[[f"{_BASE}/", status]], []])
                self.assertEqual(document["responseStatus"], status)


class ScriptTests(unittest.TestCase):
    """Every script is synchronous, and the probe is the Chromium engine's own."""

    def test_no_script_returns_a_promise(self) -> None:
        for name in (
            "VIEWPORT_SCRIPT",
            "SEED_SCRIPT",
            "STORE_SCRIPT",
            "VISIBLE_SCRIPT",
            "TIMING_SCRIPT",
            "PROBE_SCRIPT",
        ):
            script = getattr(gecko_page, name)
            for word in ("async", "await", "Promise", ".then("):
                with self.subTest(name=name, word=word):
                    self.assertNotIn(word, script)

    def test_every_script_returns_a_value(self) -> None:
        for name in (
            "VIEWPORT_SCRIPT",
            "SEED_SCRIPT",
            "STORE_SCRIPT",
            "VISIBLE_SCRIPT",
            "TIMING_SCRIPT",
            "PROBE_SCRIPT",
        ):
            with self.subTest(name=name):
                self.assertIn("return ", getattr(gecko_page, name))

    def test_the_probe_is_called_on_its_argument(self) -> None:
        script = gecko_page.probe_script("(arg) => arg;\n")
        self.assertEqual(script, "return (\n(arg) => arg\n)(arguments[0]);")

    def test_the_probe_script_is_the_probes_own_source(self) -> None:
        self.assertEqual(gecko_page.PROBE_SCRIPT, gecko_page.probe_script(PROBE_SOURCE))
        self.assertIn(PROBE_SOURCE.strip().removesuffix(";"), gecko_page.PROBE_SCRIPT)

    def test_the_timing_script_reads_both_lists_as_plain_arrays(self) -> None:
        script = gecko_page.TIMING_SCRIPT
        for part in (
            "getEntriesByType('navigation')",
            "getEntriesByType('resource')",
            "responseStatus",
            "name",
        ):
            with self.subTest(part=part):
                self.assertIn(part, script)

    def test_the_visibility_script_reads_box_and_visibility(self) -> None:
        for part in (
            "querySelector(arguments[0])",
            "getBoundingClientRect()",
            "visibility",
        ):
            with self.subTest(part=part):
                self.assertIn(part, gecko_page.VISIBLE_SCRIPT)


class _Session:
    """A session as the probe's adapter needs it: `execute`, logged."""

    def __init__(self) -> None:
        """Log nothing yet."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, script: str, *args: object) -> object:
        """Log the script and its arguments; answer with a marker."""
        self.calls.append((script, args))
        return "answer"


class ProbePageTests(unittest.TestCase):
    """The probe's adapter runs the probe as a synchronous script on its argument."""

    def test_evaluate_executes_the_wrapped_source_with_the_argument(self) -> None:
        session = _Session()
        answer = gecko_page.ProbePage(session).evaluate(PROBE_SOURCE, {"reads": []})
        self.assertEqual(answer, "answer")
        self.assertEqual(session.calls, [(gecko_page.PROBE_SCRIPT, ({"reads": []},))])


class ServedTests(unittest.TestCase):
    """Whether the document's own URL is on the served origin: scheme, host and port."""

    def test_a_page_on_the_served_origin_is_served(self) -> None:
        for url in (
            f"{_BASE}/",
            f"{_BASE}/about/?q=1#x",
            f"{_BASE}/.print-pass-storage-seed",
        ):
            with self.subTest(url=url):
                self.assertTrue(gecko_page.is_served(url, _BASE))

    def test_anything_else_is_not(self) -> None:
        for url in (
            "about:blank",
            "about:srcdoc",
            "data:text/html,<p>x</p>",
            f"blob:{_BASE}/0d4b6f2e",
            "http://127.0.0.1:9/",
            "https://127.0.0.1:8123/",
            "http://localhost:8123/",
            "http://192.0.2.1:8123/",
            "http://127.0.0.1:x/",
            "",
            "http://[::1]:8123/",
        ):
            with self.subTest(url=url):
                self.assertFalse(gecko_page.is_served(url, _BASE))

    def test_no_url_is_served_from_a_base_that_does_not_parse(self) -> None:
        self.assertFalse(
            gecko_page.is_served("http://127.0.0.1:x/", "http://127.0.0.1:x")
        )
        self.assertFalse(gecko_page.is_served("about:blank", "about:blank"))


class ScratchTests(unittest.TestCase):
    """The scratch folder's path, in every form given, becomes one token."""

    def test_each_form_is_replaced_the_longest_first(self) -> None:
        text = "/private/var/t/tmpab/home, /var/t/tmpab/log"
        self.assertEqual(
            gecko_page.without_scratch(text, ("/var/t/tmpab", "/private/var/t/tmpab")),
            "<scratch>/home, <scratch>/log",
        )

    def test_text_naming_no_form_is_unchanged(self) -> None:
        self.assertEqual(
            gecko_page.without_scratch("/var/t", ("/var/t/tmpab",)), "/var/t"
        )

    def test_the_forms_are_the_root_as_given_and_resolved(self) -> None:
        root = Path(__file__).resolve().parent / "missing-scratch"
        self.assertEqual(
            gecko_page.scratch_forms(root), (str(root), str(root.resolve()))
        )


if __name__ == "__main__":
    unittest.main()
