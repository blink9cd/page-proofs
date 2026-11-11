"""Suite: the report's JSON, shaped from fixed records (make, read and compare), and its paths.

Every case builds the report from the fixed state, PDF reading and comparison in
`_print_report_fixtures` and compares it with the documented shape there, whole. What is done
with a finished report (its summary, exit code, normalization and writing) is the sibling
`test_print_report_output`'s.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import unittest
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath
from typing import cast

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_report  # noqa: E402 - the tool's private src/, via the insert above
import print_report_output  # noqa: E402 - as above
import print_report_pdf  # noqa: E402 - as above
import print_report_scrub  # noqa: E402 - as above
from _pdf_privacy_fixtures import (  # noqa: E402 - beside this module
    FULL_NAME,
    HOME,
    HOSTNAMES,
    IDENTITY,
    LOGIN,
    USERS_PATH,
)
from _print_report_fixtures import (  # noqa: E402 - as above
    DPI,
    PDF_NAME,
    READ_NAME,
    TIMEOUT,
    comparison,
    fake_readers,
    make_json,
    pdf_info,
    pdf_json,
    pdf_report,
    probe_result,
    read,
    state_record,
    targets,
)
from pdf_tools import PopplerError  # noqa: E402 - src/
from print_engine import (  # noqa: E402 - src/
    MARGIN_INCHES,
    NetworkRecord,
    Problem,
    StateRecord,
)
from print_probe import (  # noqa: E402 - src/
    ImageState,
    ProbeResult,
    ReadValue,
    RunningAnimation,
)
from print_targets import Action, TargetsFile  # noqa: E402 - src/

#: The report keys whose values are paths (or lists of them) the report writes.
_PATH_KEYS = frozenset({"file", "pdfs", "files", "a", "b"})


def _at(value: object, *keys: str) -> object:
    """The value under `keys` in turn, each level a JSON object."""
    for key in keys:
        value = cast(dict[str, object], value)[key]
    return value


def _first_pdf(report: dict[str, object], *keys: str) -> object:
    """The value under `keys` in the report's first PDF entry."""
    return _at(cast(list[object], report["pdfs"])[0], *keys)


class ReportCase(unittest.TestCase):
    """A fresh out dir per case, and the make report built over it."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)

    def make(
        self,
        records: Sequence[StateRecord] | None = None,
        *,
        targets_file: TargetsFile | None = None,
        header_footer: bool = False,
    ) -> dict[str, object]:
        return print_report.make_report(
            [state_record(self.out)] if records is None else records,
            {self.out / "pdfs" / PDF_NAME: pdf_report()},
            [comparison()],
            out_dir=self.out,
            targets=targets(read()) if targets_file is None else targets_file,
            engine="shell",
            header_footer=header_footer,
            identity=IDENTITY,
        )

    def state(
        self, record: StateRecord, *keys: str, targets_file: TargetsFile | None = None
    ) -> object:
        states = cast(
            list[object], self.make([record], targets_file=targets_file)["states"]
        )
        return _at(states[0], *keys)


class FacadeTests(unittest.TestCase):
    def test_every_public_name_of_both_halves_is_importable_from_the_report(
        self,
    ) -> None:
        halves = {
            print_report_pdf: (
                "PdfReport",
                "compare_pdfs",
                "read_pdf",
                "report_path",
            ),
            print_report_scrub: ("scrub",),
            print_report_output: (
                "REPORT_NAME",
                "exit_code",
                "normalize_report",
                "summary_lines",
                "write_report",
            ),
        }
        for module, names in halves.items():
            for name in names:
                with self.subTest(name=name):
                    self.assertIn(name, print_report.__all__)
                    self.assertIs(getattr(print_report, name), getattr(module, name))


class MakeReportTests(ReportCase):
    def test_the_make_report_is_the_documented_shape(self) -> None:
        self.assertEqual(self.make(), make_json())

    def test_the_expected_offsite_hosts_are_recorded_once_for_the_run(self) -> None:
        # The file's own list, so a reader sees why a blocked request is only a warning:
        # once for the run, beside no state's network record, so there is one source.
        hosts = ("example.org", "img.example.net")
        report = self.make(targets_file=targets(read(), hosts=hosts))
        self.assertEqual(report["targets"], {"expected_offsite_hosts": list(hosts)})
        network = cast(dict[str, object], self.state(state_record(self.out), "network"))
        self.assertNotIn("expected_offsite_hosts", network)

    def test_an_expected_host_naming_this_machine_is_scrubbed(self) -> None:
        report = self.make(targets_file=targets(read(), hosts=(HOSTNAMES[0],)))
        self.assertEqual(
            _at(report, "targets", "expected_offsite_hosts"), ["<hostname>"]
        )

    def test_the_header_and_footer_are_reported_as_printed(self) -> None:
        self.assertIs(
            _at(self.make(header_footer=True), "print", "header_footer"), True
        )

    def test_a_first_match_read_of_nothing_is_null_an_every_match_read_an_empty_list(
        self,
    ) -> None:
        reads = (
            ReadValue("first", ()),
            ReadValue("every", ()),
            ReadValue("kept", ("dark",)),
        )
        record = dataclasses.replace(
            state_record(self.out),
            probe=dataclasses.replace(probe_result(), reads=reads),
        )
        declared = targets(read("first"), read("every", every=True), read("kept"))
        self.assertEqual(
            self.state(record, "read_back", "reads", targets_file=declared),
            {"first": None, "every": [], "kept": ["dark"]},
        )

    def test_a_record_of_a_target_the_file_does_not_hold_is_refused(self) -> None:
        record = dataclasses.replace(state_record(self.out), target="elsewhere")
        with self.assertRaises(KeyError):
            self.make([record])

    def test_a_state_stopped_before_its_read_back_has_none(self) -> None:
        record = dataclasses.replace(state_record(self.out), probe=None)
        self.assertIsNone(self.state(record, "read_back"))

    def test_each_action_is_named_by_its_kind_and_what_it_waits_on(self) -> None:
        actions = (Action("wait_for", "#ready", None), Action("wait_ms", None, 300))
        record = dataclasses.replace(state_record(self.out), actions=actions)
        self.assertEqual(
            self.state(record, "actions"), ["wait_for #ready", "wait_ms 300"]
        )

    def test_problems_and_the_network_and_images_they_quote_are_scrubbed(self) -> None:
        leaked = f"file://{HOME}/a.png"
        record = dataclasses.replace(
            state_record(self.out),
            problems=(
                Problem("finding", f"blocked off-loopback request: {leaked}"),
                Problem("finding", f"page error: {self.out}/x and {USERS_PATH}/y"),
            ),
            network=NetworkRecord(
                (leaked,),
                (leaked,),
                (leaked,),
                (f"{USERS_PATH}/y",),
                (f"img-src blocked {leaked} on /",),
            ),
            probe=dataclasses.replace(
                probe_result(),
                images=(ImageState(leaked, True, "", 64, has_src=True),),
            ),
        )
        self.assertEqual(
            self.state(record, "problems"),
            [
                {
                    "severity": "finding",
                    "what": "blocked off-loopback request: file://~/a.png",
                },
                {"severity": "finding", "what": "page error: <out>/x and <home>/y"},
            ],
        )
        self.assertEqual(
            self.state(record, "network"),
            {
                "failed": ["file://~/a.png"],
                "http_errors": ["file://~/a.png"],
                "blocked": ["file://~/a.png"],
                "page_errors": ["<home>/y"],
                "csp_violations": ["img-src blocked file://~/a.png on /"],
            },
        )
        self.assertEqual(
            self.state(record, "read_back", "images"),
            [
                {
                    "src": "file://~/a.png",
                    "has_src": True,
                    "complete": True,
                    "loading": "",
                    "natural_width": 64,
                }
            ],
        )

    def test_a_request_to_this_machines_hostname_is_scrubbed_where_it_is_quoted(
        self,
    ) -> None:
        url = f"http://{HOSTNAMES[0]}:8123/x.png"
        scrubbed = "http://<hostname>:8123/x.png"
        record = dataclasses.replace(
            state_record(self.out),
            problems=(Problem("finding", f"blocked off-loopback request: {url}"),),
            network=NetworkRecord((), (), (url,), (), ()),
            probe=dataclasses.replace(
                probe_result(), images=(ImageState(url, False, "", 0, has_src=True),)
            ),
        )
        self.assertEqual(
            self.state(record, "problems"),
            [
                {
                    "severity": "finding",
                    "what": f"blocked off-loopback request: {scrubbed}",
                }
            ],
        )
        self.assertEqual(self.state(record, "network", "blocked"), [scrubbed])
        images = cast(list[object], self.state(record, "read_back", "images"))
        self.assertEqual(_at(images[0], "src"), scrubbed)

    def test_totals_count_every_finding_and_warning_in_states_and_pdfs(self) -> None:
        record = dataclasses.replace(
            state_record(self.out),
            problems=(
                Problem("finding", "a"),
                Problem("finding", "b"),
                Problem("warning", "c"),
            ),
        )
        self.assertEqual(
            self.make([record, state_record(self.out)])["totals"],
            {"states": 2, "pdfs": 1, "findings": 3, "warnings": 2},
        )


class ReadBackTests(ReportCase):
    """What the page held is written as safe to share as the PDF it printed."""

    def read_back(self, probe: ProbeResult, key: str) -> object:
        record = dataclasses.replace(state_record(self.out), probe=probe)
        return self.state(record, "read_back", key)

    def title(self, title: str) -> object:
        return self.read_back(dataclasses.replace(probe_result(), title=title), "title")

    def test_a_title_holding_a_home_rooted_path_is_withheld(self) -> None:
        # Chromium copies the page's title into the PDF's Title, withheld there when flagged.
        for path in (HOME, USERS_PATH):
            with self.subTest(path=path):
                self.assertEqual(
                    self.title(f"Notes kept in {path}/notes"), print_report_pdf.WITHHELD
                )

    def test_a_title_holding_the_hostname_is_withheld(self) -> None:
        for hostname in HOSTNAMES:
            with self.subTest(hostname=hostname):
                self.assertEqual(
                    self.title(f"Printed on {hostname}"), print_report_pdf.WITHHELD
                )

    def test_a_title_naming_only_the_full_name_or_the_login_is_kept(self) -> None:
        # A site's title carries its author's name as legitimately as its text does.
        for title in (f"{FULL_NAME}: notes", f"Notes by {LOGIN}"):
            with self.subTest(title=title):
                self.assertEqual(self.title(title), title)

    def test_a_read_value_holding_a_home_path_is_scrubbed_and_other_values_kept(
        self,
    ) -> None:
        reads = (ReadValue(READ_NAME, (f"{HOME}/a.png", 3, None)),)
        probe = dataclasses.replace(probe_result(), reads=reads)
        self.assertEqual(
            self.read_back(probe, "reads"), {READ_NAME: ["~/a.png", 3, None]}
        )

    def test_a_storage_value_holding_a_home_path_is_scrubbed(self) -> None:
        storage = (("last", f"{USERS_PATH}/b.pdf"), ("mode", None))
        probe = dataclasses.replace(probe_result(), storage=storage)
        self.assertEqual(
            self.read_back(probe, "storage"), {"last": "<home>/b.pdf", "mode": None}
        )

    def test_a_read_storage_key_holding_a_home_path_is_scrubbed(self) -> None:
        storage = ((f"{USERS_PATH}/key", "dark"),)
        probe = dataclasses.replace(probe_result(), storage=storage)
        self.assertEqual(self.read_back(probe, "storage"), {"<home>/key": "dark"})

    def test_each_image_says_whether_it_had_a_src_attribute(self) -> None:
        # One without is "never requested": the judge raises nothing for it, and this key
        # is how a reader of the report tells it from an image that loaded.
        images = (
            ImageState("/a.png", True, "", 64, has_src=True),
            ImageState("", True, "lazy", 0, has_src=False),
        )
        probe = dataclasses.replace(probe_result(), images=images)
        self.assertEqual(
            [_at(image, "has_src") for image in self.images(probe)], [True, False]
        )

    def images(self, probe: ProbeResult) -> list[object]:
        return cast(list[object], self.read_back(probe, "images"))

    def test_an_animations_name_and_target_holding_a_home_path_are_scrubbed(
        self,
    ) -> None:
        running = RunningAnimation(f"slide {HOME}/x", f'img[src="{USERS_PATH}/a.png"]')
        probe = dataclasses.replace(probe_result(), animations=(running,))
        self.assertEqual(
            self.read_back(probe, "animations"),
            [{"name": "slide ~/x", "target": 'img[src="<home>/a.png"]'}],
        )


class ReadReportTests(unittest.TestCase):
    def read_one(self, report: print_report.PdfReport, *keys: str) -> object:
        return _first_pdf(print_report.read_report([report]), *keys)

    def test_the_read_report_holds_its_pdfs_and_totals_only(self) -> None:
        self.assertEqual(
            print_report.read_report([pdf_report()]),
            {
                "tool": "print_pass",
                "schema": 2,
                "mode": "read",
                "pdfs": [pdf_json()],
                "totals": {"pdfs": 1, "findings": 1, "warnings": 0},
            },
        )

    def test_what_a_pdf_could_not_be_read_for_is_null(self) -> None:
        unread = dataclasses.replace(pdf_report(), info=None, text=None, text_file=None)
        for key in ("pages", "page_sizes", "metadata", "text"):
            with self.subTest(key=key):
                self.assertIsNone(self.read_one(unread, key))

    def test_no_images_have_no_densities(self) -> None:
        bare = dataclasses.replace(pdf_report(), images=())
        self.assertEqual(
            self.read_one(bare, "images"),
            {"items": [], "min_ppi": None, "max_ppi": None},
        )

    def test_the_densities_span_every_images_both_axes(self) -> None:
        image = pdf_report().images[0]
        images = (
            dataclasses.replace(image, x_ppi=72, y_ppi=300),
            dataclasses.replace(image, x_ppi=150, y_ppi=96),
        )
        entry = self.read_one(
            dataclasses.replace(pdf_report(), images=images), "images"
        )
        self.assertEqual((_at(entry, "min_ppi"), _at(entry, "max_ppi")), (72, 300))

    def test_an_author_is_reported_present_never_by_name(self) -> None:
        info = dataclasses.replace(pdf_info(), author_present=True)
        self.assertEqual(
            self.read_one(
                dataclasses.replace(pdf_report(), info=info), "metadata", "author"
            ),
            "present",
        )


class CompareReportTests(unittest.TestCase):
    def test_the_compare_report_holds_its_one_comparison_only(self) -> None:
        self.assertEqual(
            print_report.compare_report(comparison()),
            {
                "tool": "print_pass",
                "schema": 2,
                "mode": "compare",
                "comparisons": [comparison()],
            },
        )


def _strings_under(value: object, keys: frozenset[str]) -> Iterator[str]:
    """Every string held, at any depth, under one of `keys` (a list's strings included)."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys:
                items = item if isinstance(item, list) else [item]
                yield from (entry for entry in items if isinstance(entry, str))
            yield from _strings_under(item, keys)
    elif isinstance(value, list):
        for item in value:
            yield from _strings_under(item, keys)


class NoAbsolutePathTests(ReportCase):
    def test_no_absolute_path_ever_reaches_report_json(self) -> None:
        # Everything the make half hands the report is absolute: each PDF's path, a reader's
        # message naming it, a state's problem naming it and the home.
        pdf = self.out / "pdfs" / PDF_NAME
        backgrounds_on = self.out / "pdfs" / "home--default--bg-on.pdf"
        with fake_readers(read_fonts=PopplerError(f"cannot open {pdf}")):
            reading = print_report.read_pdf(
                pdf,
                self.out,
                dpi=DPI,
                timeout=TIMEOUT,
                identity=IDENTITY,
                margin_inches=MARGIN_INCHES,
            )
        # The defaults size both PDFs and render each in color.
        with fake_readers():
            compared = print_report.compare_pdfs(
                pdf, backgrounds_on, dpi=DPI, timeout=TIMEOUT, out_dir=self.out
            )
        record = dataclasses.replace(
            state_record(self.out), problems=(Problem("finding", f"{pdf} and {HOME}"),)
        )
        report = print_report.make_report(
            [record],
            {pdf: reading},
            [compared],
            out_dir=self.out,
            targets=targets(read()),
            engine="shell",
            header_footer=False,
            identity=IDENTITY,
        )
        written = print_report.write_report(report, self.out).read_text(
            encoding="utf-8"
        )
        for form in {str(self.out), str(self.out.resolve()), HOME}:
            self.assertNotIn(form, written)
        paths = list(_strings_under(json.loads(written), _PATH_KEYS))
        # A state's PDF, the PDF's own file, its text file and render, and the pair compared.
        self.assertEqual(len(paths), 6, paths)
        for path in paths:
            self.assertFalse(PurePosixPath(path).is_absolute(), path)


if __name__ == "__main__":
    unittest.main()
