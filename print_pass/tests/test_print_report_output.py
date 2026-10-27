"""Suite: what is done with a finished report -- its exit code, normalization, summary and file.

Each case starts from the documented make report in `_print_report_fixtures` (or the read
and compare reports `print_report` builds) and changes only what it is about. Shaping the
report is the sibling `test_print_report`'s.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import copy
import json
import os
import stat
import sys
import tempfile
import typing
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_report  # noqa: E402 - the tool's private src/, via the insert above
import print_report_output  # noqa: E402 - as above
from _print_report_fixtures import (  # noqa: E402 - beside this module
    HEART,
    PDF_NAME,
    READ_NAME,
    comparison,
    make_json,
    pdf_report,
    state_json,
)
from print_record import Severity  # noqa: E402 - src/

_PDF = f"pdfs/{PDF_NAME}"
_ON = "pdfs/home--default--bg-on.pdf"


def _entries(report: dict[str, object], key: str) -> list[dict[str, object]]:
    """The list of JSON objects under `key`."""
    return cast(list[dict[str, object]], report[key])


def _compared(**changes: object) -> dict[str, object]:
    """The compare report of the fixed comparison, with `changes` made to the comparison."""
    return print_report.compare_report({**comparison(), **changes})


#: A place in a JSON report: an object's key or an array's index, in turn.
type _Path = tuple[str | int, ...]

#: Each key the normalization drops, after the path of the entry it sits in.
_VARYING_KEYS: typing.Final[tuple[tuple[_Path, str], ...]] = (
    (("states", 0), "browser_version"),
    (("states", 0), "timings"),
    (("pdfs", 0), "renders"),
    (("pdfs", 0, "metadata"), "producer"),
    (("pdfs", 0, "fonts", 0), "base_name"),
    (("pdfs", 0, "fonts", 0), "type"),
    (("pdfs", 0, "fonts", 0), "encoding"),
    (("pdfs", 0, "links", 0), "rect"),
    (("pdfs", 0, "text"), "log"),
    (("comparisons", 0, "pages", 0), "differing_pixels"),
    (("comparisons", 0, "pages", 0), "bbox_pt"),
)

#: Loopback hosts with a port, each as the normalized copy must read it: the port is the
#: server's, picked fresh on every run.
_LOOPBACK_TEXTS: typing.Final = (
    ("http://127.0.0.1:54321/other.html", "http://127.0.0.1:<port>/other.html"),
    (
        "fetch failed: http://localhost:8123/a.json",
        "fetch failed: http://localhost:<port>/a.json",
    ),
    ("ws://[::1]:9000/", "ws://[::1]:<port>/"),
    (
        "http://LOCALHOST:80/ and 127.0.0.1:1",
        "http://LOCALHOST:<port>/ and 127.0.0.1:<port>",
    ),
)

#: Texts that hold a host and a port, or a loopback host, and must come through unchanged.
_NOT_LOOPBACK_TEXTS: typing.Final = (
    "https://example.com:8443/",
    "http://127.0.0.10:8080/",
    "http://a.127.0.0.1:8080/",
    "http://mylocalhost:8080/",
    "http://my-localhost:8080/",
    "http://localhost.example:8080/",
    "http://127.0.0.1/",
    "http://127.0.0.1:<port>/",
)

#: The strings `_holding` sets: a state's problem and read value, a PDF's link, and a
#: comparison's file name -- each nested at its own depth.
_HELD_AT: typing.Final[tuple[_Path, ...]] = (
    ("states", 0, "problems", 0, "what"),
    ("states", 0, "read_back", "reads", READ_NAME, 0),
    ("pdfs", 0, "links", 0, "uri"),
    ("comparisons", 0, "b"),
)


def _at(report: object, path: _Path) -> object:
    """The value at `path` in `report`."""
    value = report
    for step in path:
        if isinstance(step, int):
            value = cast(list[object], value)[step]
        else:
            value = cast(dict[str, object], value)[step]
    return value


def _entry_at(report: object, path: _Path) -> dict[str, object]:
    """The JSON object at `path` in `report`."""
    return cast(dict[str, object], _at(report, path))


def _holding(text: str) -> dict[str, object]:
    """The fixed make report with `text` at each place `_HELD_AT` names."""
    report = make_json()
    for *parent, last in _HELD_AT:
        container = _at(report, tuple(parent))
        if isinstance(last, int):
            cast(list[object], container)[last] = text
        else:
            cast(dict[str, object], container)[last] = text
    return report


def _held(report: dict[str, object]) -> list[object]:
    """The strings at the places `_HELD_AT` names, in its order."""
    return [_at(report, path) for path in _HELD_AT]


class SeverityWordTests(unittest.TestCase):
    def test_the_two_severity_words_are_the_records_own_in_its_order(self) -> None:
        # Restated so the output half imports no engine module; drifted from the record's
        # Literal, they would count no finding and exit 0 on one.
        self.assertEqual(
            typing.get_args(Severity.__value__),
            (print_report_output.FINDING, print_report_output.WARNING),
        )


class ExitCodeTests(unittest.TestCase):
    def test_a_make_report_with_a_finding_exits_one(self) -> None:
        self.assertEqual(print_report_output.exit_code(make_json()), 1)

    def test_a_make_report_with_warnings_alone_exits_zero(self) -> None:
        report = make_json()
        report["totals"] = {"states": 1, "pdfs": 1, "findings": 0, "warnings": 4}
        self.assertEqual(print_report_output.exit_code(report), 0)

    def test_a_read_report_exits_one_on_a_finding_and_zero_without(self) -> None:
        found = print_report.read_report([pdf_report()])
        clean = print_report.read_report([])
        self.assertEqual(
            (
                print_report_output.exit_code(found),
                print_report_output.exit_code(clean),
            ),
            (1, 0),
        )

    def test_a_compare_report_exits_one_when_the_pdfs_differ(self) -> None:
        self.assertEqual(print_report_output.exit_code(_compared()), 1)

    def test_a_compare_report_exits_zero_when_they_are_identical(self) -> None:
        self.assertEqual(print_report_output.exit_code(_compared(identical=True)), 0)


class NormalizeTests(unittest.TestCase):
    def test_exactly_the_varying_keys_are_dropped_or_reduced(self) -> None:
        expected = make_json()
        state = _entries(expected, "states")[0]
        del state["browser_version"], state["timings"]
        pdf = _entries(expected, "pdfs")[0]
        del pdf["renders"]
        del cast(dict[str, object], pdf["metadata"])["producer"]
        font = _entries(pdf, "fonts")[0]
        del font["base_name"], font["type"], font["encoding"]
        del _entries(pdf, "links")[0]["rect"]
        text = cast(dict[str, object], pdf["text"])
        text["chars_per_page"] = 1
        text["non_ascii_glyph_fonts"] = [HEART]
        del text["log"]
        page = _entries(_entries(expected, "comparisons")[0], "pages")[0]
        del page["differing_pixels"], page["bbox_pt"]
        self.assertEqual(print_report_output.normalize_report(make_json()), expected)

    def test_each_varying_key_is_gone_from_the_normalized_copy(self) -> None:
        # One case per key, so a key the normalization keeps is named by the failure.
        normalized = print_report_output.normalize_report(make_json())
        for path, key in _VARYING_KEYS:
            with self.subTest(path=path, key=key):
                self.assertIn(key, _entry_at(make_json(), path))
                self.assertNotIn(key, _entry_at(normalized, path))

    def test_the_report_given_is_left_as_it_was(self) -> None:
        report = make_json()
        print_report_output.normalize_report(report)
        self.assertEqual(report, make_json())

    def test_a_pdf_with_no_text_layer_keeps_its_null(self) -> None:
        report = make_json()
        _entries(report, "pdfs")[0]["text"] = None
        normalized = print_report_output.normalize_report(report)
        self.assertIsNone(_entries(normalized, "pdfs")[0]["text"])

    def test_a_pdf_whose_metadata_was_not_read_keeps_its_null(self) -> None:
        report = make_json()
        _entries(report, "pdfs")[0]["metadata"] = None
        normalized = print_report_output.normalize_report(report)
        self.assertIsNone(_entries(normalized, "pdfs")[0]["metadata"])

    def test_a_compare_report_keeps_what_is_identical_and_drops_the_counts(
        self,
    ) -> None:
        pages = [{"page": 1, "identical": False}]
        self.assertEqual(
            print_report_output.normalize_report(_compared()), _compared(pages=pages)
        )

    def test_a_loopback_port_is_masked_in_every_string_wherever_it_sits(
        self,
    ) -> None:
        for raw, masked in _LOOPBACK_TEXTS:
            with self.subTest(raw=raw):
                normalized = print_report_output.normalize_report(_holding(raw))
                self.assertEqual(_held(normalized), [masked] * len(_HELD_AT))

    def test_a_host_and_port_off_loopback_is_left_alone(self) -> None:
        for text in _NOT_LOOPBACK_TEXTS:
            with self.subTest(text=text):
                normalized = print_report_output.normalize_report(_holding(text))
                self.assertEqual(_held(normalized), [text] * len(_HELD_AT))

    def test_a_normalized_report_survives_a_json_round_trip(self) -> None:
        loaded = json.loads(json.dumps(make_json()))
        self.assertEqual(
            print_report_output.normalize_report(loaded),
            print_report_output.normalize_report(make_json()),
        )


class SummaryTests(unittest.TestCase):
    def test_findings_then_warnings_then_pdfs_comparisons_and_totals(self) -> None:
        report = make_json()
        state = _entries(report, "states")[0]
        state["problems"] = [
            {"severity": "warning", "what": "fonts not loaded at print: loading"},
            {"severity": "finding", "what": "image broken: /a.png"},
        ]
        self.assertEqual(
            print_report_output.summary_lines(report),
            [
                "FINDING home/default: image broken: /a.png",
                f"FINDING {_PDF}: metadata:Author: names a person",
                "WARNING home/default: fonts not loaded at print: loading",
                f"{_PDF}: 1 page(s), 1 font(s), 1 image(s), 1 link(s)",
                f"{_PDF} vs {_ON}: page(s) 1 differ",
                "totals: states 1, pdfs 1, findings 1, warnings 1",
            ],
        )

    def test_a_report_read_back_from_its_sorted_file_prints_the_same_summary(
        self,
    ) -> None:
        # `report.json` is written with sorted keys, so a report loaded from it holds its
        # totals in another order than the one it was built in.
        for report in (make_json(), print_report.read_report([pdf_report()])):
            reloaded = json.loads(json.dumps(report, sort_keys=True))
            with self.subTest(mode=report["mode"]):
                self.assertEqual(
                    print_report_output.summary_lines(reloaded),
                    print_report_output.summary_lines(report),
                )

    def test_a_pdf_whose_pages_were_not_read_counts_them_unknown(self) -> None:
        report = print_report.read_report([pdf_report()])
        _entries(report, "pdfs")[0]["pages"] = None
        self.assertIn(
            f"{_PDF}: ? page(s), 1 font(s), 1 image(s), 1 link(s)",
            print_report_output.summary_lines(report),
        )

    def test_a_read_report_has_no_state_lines(self) -> None:
        self.assertEqual(
            print_report_output.summary_lines(print_report.read_report([pdf_report()])),
            [
                f"FINDING {_PDF}: metadata:Author: names a person",
                f"{_PDF}: 1 page(s), 1 font(s), 1 image(s), 1 link(s)",
                "totals: pdfs 1, findings 1, warnings 0",
            ],
        )

    def test_a_compare_report_is_its_comparison_alone(self) -> None:
        self.assertEqual(
            print_report_output.summary_lines(_compared(identical=True, pages=[])),
            [f"{_PDF} vs {_ON}: identical"],
        )

    def test_a_page_count_mismatch_is_named_before_the_pages_that_differ(self) -> None:
        pages = cast(list[object], comparison()["pages"])
        self.assertEqual(
            print_report_output.summary_lines(_compared(page_count=[2, 1])),
            [f"{_PDF} vs {_ON}: page counts 2 and 1; page(s) 1 differ"],
        )
        same = [{**cast(dict[str, object], pages[0]), "identical": True}]
        self.assertEqual(
            print_report_output.summary_lines(_compared(page_count=[2, 1], pages=same)),
            [f"{_PDF} vs {_ON}: page counts 2 and 1"],
        )


class WriteReportTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)

    def test_the_report_is_sorted_indented_utf8_json_ending_in_one_newline(
        self,
    ) -> None:
        path = print_report_output.write_report(make_json(), self.out)
        self.assertEqual(path, self.out / "report.json")
        expected = (
            json.dumps(make_json(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )
        self.assertEqual(path.read_bytes(), expected.encode("utf-8"))
        self.assertNotIn(b"\r", path.read_bytes())
        self.assertIn(HEART.encode("utf-8"), path.read_bytes())

    def test_an_earlier_report_is_replaced_whole(self) -> None:
        (self.out / "report.json").write_text(
            "x" * 100_000, encoding="utf-8", newline="\n"
        )
        path = print_report_output.write_report(
            print_report.compare_report(comparison()), self.out
        )
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8"))["mode"], "compare"
        )

    def test_a_failed_replace_leaves_neither_a_temp_file_nor_a_report(self) -> None:
        with (
            mock.patch.object(os, "replace", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            print_report_output.write_report(make_json(), self.out)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_report_that_is_not_json_writes_nothing(self) -> None:
        with self.assertRaises(TypeError):
            print_report_output.write_report(
                {"state": state_json(), "bad": object()}, self.out
            )
        self.assertEqual(list(self.out.iterdir()), [])

    @unittest.skipIf(sys.platform == "win32", "Windows keeps no POSIX mode bits")
    def test_the_report_is_readable_by_its_account_alone(self) -> None:
        # mkstemp makes the temp file 0600, and the replace moves it into place as it is.
        path = print_report_output.write_report(make_json(), self.out)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_a_lone_surrogate_the_page_chose_is_written_as_its_json_escape(
        self,
    ) -> None:
        # A page's title is any JavaScript string, and one may hold half a surrogate pair,
        # which no UTF-8 can encode. It is written as the escape JSON reads back as itself.
        report = {"title": f"{HEART} \ud800 \\"}
        path = print_report_output.write_report(report, self.out)
        written = path.read_bytes()
        self.assertIn(b'"\xe2\x99\xa5 \\ud800 \\\\"', written)
        self.assertEqual(json.loads(written.decode("utf-8")), report)

    def test_the_report_written_reads_back_equal(self) -> None:
        path = print_report_output.write_report(make_json(), self.out)
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")), copy.deepcopy(make_json())
        )


if __name__ == "__main__":
    unittest.main()
