"""Suite: the in-page probe's Python half -- its argument, the parse of its result, its call.

The probe itself is JavaScript and has its own node suite beside this one; nothing here runs
a browser. The page is a fake with Playwright's `evaluate` shape that records what it was
handed and returns a canned result.
"""

from __future__ import annotations

import copy
import dataclasses
import sys
import unittest
from pathlib import Path
from typing import Any, Final

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_probe  # noqa: E402 - the tool's private src/, via the insert above

# The determinism convention: a profile registered at import, never an
# `@settings` decorator, which a fuzzing campaign could not override. deadline=None and
# too_slow suppressed: a busy machine measures load, not correctness.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")


@dataclasses.dataclass(frozen=True)
class _Read:
    """A stand-in for a targets file's `[[read]]`, frozen as the real one is."""

    name: str
    selector: str
    kind: str
    key: str | None
    all: bool


_THEME = _Read(
    name="theme", selector="html", kind="attribute", key="data-theme", all=False
)
_ITEMS = _Read(name="items", selector="li", kind="text", key=None, all=True)


def _valid_raw() -> dict[str, Any]:
    """A result exactly as the probe returns one, touching every field."""
    return {
        "colorScheme": "dark",
        "reducedMotion": "no-preference",
        "readyState": "complete",
        "fontsStatus": "loaded",
        "title": "A page",
        "images": [
            {
                "src": "/a.png",
                "hasSrc": True,
                "complete": True,
                "loading": "lazy",
                "naturalWidth": 640,
            }
        ],
        "animations": [{"name": "fade", "target": "div#hero.card"}],
        "reads": [{"name": "theme", "values": ["dark", 3, 1.5, True, None]}],
        "storage": [["theme", "dark"], ["missing", None]],
    }


def _expected() -> print_probe.ProbeResult:
    """`_valid_raw` as the parse must return it."""
    return print_probe.ProbeResult(
        color_scheme="dark",
        reduced_motion="no-preference",
        ready_state="complete",
        fonts_status="loaded",
        title="A page",
        images=(
            print_probe.ImageState(
                src="/a.png",
                complete=True,
                loading="lazy",
                natural_width=640,
                has_src=True,
            ),
        ),
        animations=(print_probe.RunningAnimation(name="fade", target="div#hero.card"),),
        reads=(
            print_probe.ReadValue(name="theme", values=("dark", 3, 1.5, True, None)),
        ),
        storage=(("theme", "dark"), ("missing", None)),
    )


def _broken(path: tuple[str | int, ...], value: object) -> dict[str, Any]:
    """A valid result with the value at `path` replaced by `value`."""
    raw = copy.deepcopy(_valid_raw())
    parent: Any = raw
    for step in path[:-1]:
        parent = parent[step]
    parent[path[-1]] = value
    return raw


def _without(path: tuple[str | int, ...]) -> dict[str, Any]:
    """A valid result with the key at the end of `path` removed."""
    raw = copy.deepcopy(_valid_raw())
    parent: Any = raw
    for step in path[:-1]:
        parent = parent[step]
    del parent[path[-1]]
    return raw


#: The bounds every argument hands the probe, which the parse then holds it to.
_BOUNDS: Final = {"maxText": 1_000, "maxItems": 5_000}


class ProbeArgumentTests(unittest.TestCase):
    """The argument the probe is called with, built from the targets' reads."""

    def test_each_read_becomes_its_five_fields(self) -> None:
        argument = print_probe.probe_argument([_THEME, _ITEMS], ["theme"])
        self.assertEqual(
            argument,
            {
                "reads": [
                    {
                        "name": "theme",
                        "selector": "html",
                        "kind": "attribute",
                        "key": "data-theme",
                        "all": False,
                    },
                    {
                        "name": "items",
                        "selector": "li",
                        "kind": "text",
                        "key": None,
                        "all": True,
                    },
                ],
                "storageKeys": ["theme"],
                **_BOUNDS,
            },
        )

    def test_no_reads_and_no_keys_are_empty_lists(self) -> None:
        self.assertEqual(
            print_probe.probe_argument([], []),
            {"reads": [], "storageKeys": [], **_BOUNDS},
        )


class ParseTests(unittest.TestCase):
    """A result the probe returns becomes a typed record."""

    def test_a_full_result_parses_to_its_record(self) -> None:
        self.assertEqual(print_probe.parse_probe_result(_valid_raw()), _expected())

    def test_an_image_with_no_src_attribute_parses_as_having_none(self) -> None:
        raw = _broken(("images", 0, "hasSrc"), False)
        (image,) = print_probe.parse_probe_result(raw).images
        self.assertFalse(image.has_src)

    def test_empty_lists_parse_to_empty_tuples(self) -> None:
        raw = _valid_raw() | {
            "images": [],
            "animations": [],
            "reads": [],
            "storage": [],
        }
        result = print_probe.parse_probe_result(raw)
        self.assertEqual(
            (result.images, result.animations, result.reads, result.storage),
            ((), (), (), ()),
        )

    def test_every_documented_value_of_each_choice_parses(self) -> None:
        # An image's `loading` is empty with no attribute, else the IDL property, which
        # reads only `lazy` or `eager`.
        choices: dict[tuple[str | int, ...], tuple[str, ...]] = {
            ("colorScheme",): ("dark", "light", "no-preference"),
            ("reducedMotion",): ("reduce", "no-preference"),
            ("readyState",): ("loading", "interactive", "complete"),
            ("fontsStatus",): ("loaded", "loading", "unavailable"),
            ("images", 0, "loading"): ("", "lazy", "eager"),
        }
        for path, values in choices.items():
            for value in values:
                with self.subTest(path=path, value=value):
                    print_probe.parse_probe_result(_broken(path, value))


#: Every key a result, an image, an animation and a read must carry.
_REQUIRED_PATHS: tuple[tuple[str | int, ...], ...] = (
    ("colorScheme",),
    ("reducedMotion",),
    ("readyState",),
    ("fontsStatus",),
    ("title",),
    ("images",),
    ("animations",),
    ("reads",),
    ("storage",),
    ("images", 0, "src"),
    ("images", 0, "hasSrc"),
    ("images", 0, "complete"),
    ("images", 0, "loading"),
    ("images", 0, "naturalWidth"),
    ("animations", 0, "name"),
    ("animations", 0, "target"),
    ("reads", 0, "name"),
    ("reads", 0, "values"),
)

#: A value of the wrong type at each place a result holds one.
_WRONG_TYPES: tuple[tuple[tuple[str | int, ...], object], ...] = (
    (("colorScheme",), 1),
    (("reducedMotion",), None),
    (("readyState",), 3),
    (("fontsStatus",), []),
    (("title",), None),
    (("images",), {}),
    (("images", 0), "an image"),
    (("images", 0, "src"), 1),
    (("images", 0, "hasSrc"), "yes"),
    (("images", 0, "hasSrc"), 1),
    (("images", 0, "hasSrc"), None),
    (("images", 0, "complete"), "yes"),
    (("images", 0, "loading"), None),
    (("images", 0, "naturalWidth"), "640"),
    (("images", 0, "naturalWidth"), True),
    (("images", 0, "naturalWidth"), 1.5),
    (("images", 0, "naturalWidth"), -1),
    (("animations",), "fade"),
    (("animations", 0), ["fade", "div"]),
    (("animations", 0, "name"), 1),
    (("animations", 0, "target"), None),
    (("reads",), {"theme": "dark"}),
    (("reads", 0), None),
    (("reads", 0, "name"), 1),
    (("reads", 0, "values"), "dark"),
    (("reads", 0, "values", 0), ["nested"]),
    (("reads", 0, "values", 0), {"nested": True}),
    (("storage",), {"theme": "dark"}),
    (("storage", 0), "theme"),
    (("storage", 0), ["theme"]),
    (("storage", 0), ["theme", "dark", "extra"]),
    (("storage", 0, 0), 1),
    (("storage", 0, 1), 2),
)


class RefusalTests(unittest.TestCase):
    """Any shape the probe never returns is refused, naming where."""

    def test_a_result_that_is_not_an_object_is_refused(self) -> None:
        not_objects: tuple[object, ...] = (None, [], "result", 1)
        for raw in not_objects:
            with self.subTest(raw=raw), self.assertRaises(print_probe.ProbeError):
                print_probe.parse_probe_result(raw)

    def test_each_missing_key_is_refused(self) -> None:
        for path in _REQUIRED_PATHS:
            with self.subTest(path=path), self.assertRaises(print_probe.ProbeError):
                print_probe.parse_probe_result(_without(path))

    def test_an_unexpected_key_is_refused(self) -> None:
        for path in ((), ("images", 0), ("animations", 0), ("reads", 0)):
            raw = copy.deepcopy(_valid_raw())
            parent: Any = raw
            for step in path:
                parent = parent[step]
            parent["extra"] = 1
            with self.subTest(path=path), self.assertRaises(print_probe.ProbeError):
                print_probe.parse_probe_result(raw)

    def test_each_wrong_type_is_refused(self) -> None:
        for path, value in _WRONG_TYPES:
            with (
                self.subTest(path=path, value=value),
                self.assertRaises(print_probe.ProbeError),
            ):
                print_probe.parse_probe_result(_broken(path, value))

    def test_an_unknown_choice_is_refused(self) -> None:
        # The report leaves each of these unscrubbed as the browser's own fixed word, so a
        # word the browser never reads -- a path, a hostname -- must never pass here.
        for path in (
            ("colorScheme",),
            ("reducedMotion",),
            ("readyState",),
            ("fontsStatus",),
            ("images", 0, "loading"),
        ):
            with self.subTest(path=path), self.assertRaises(print_probe.ProbeError):
                print_probe.parse_probe_result(_broken(path, "sepia"))

    def test_a_refusal_names_where_it_failed(self) -> None:
        raw = _broken(("images", 0, "naturalWidth"), "640")
        with self.assertRaisesRegex(
            print_probe.ProbeError, r"images\[0\]\.naturalWidth"
        ):
            print_probe.parse_probe_result(raw)

    def test_an_image_from_a_probe_without_has_src_is_refused_by_name(self) -> None:
        # The field is required, never defaulted: a probe and parser out of step must fail
        # here, rather than judge every image as one that asked for its source.
        with self.assertRaisesRegex(print_probe.ProbeError, r"missing \['hasSrc'\]"):
            print_probe.parse_probe_result(_without(("images", 0, "hasSrc")))

    def test_a_probe_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(print_probe.ProbeError, ValueError))


class BoundTests(unittest.TestCase):
    """The probe cuts what a page hands it to the bounds, and the parse refuses past them."""

    def test_the_bounds_are_a_thousand_characters_and_five_thousand_items(self) -> None:
        self.assertEqual(print_probe.MAX_PROBE_TEXT, 1_000)
        self.assertEqual(print_probe.MAX_PROBE_ITEMS, 5_000)

    def test_text_at_the_bound_parses_and_past_it_is_refused(self) -> None:
        at, past = "t" * 1_000, "t" * 1_001
        for path in (
            ("title",),
            ("images", 0, "src"),
            ("animations", 0, "name"),
            ("reads", 0, "values", 0),
            ("storage", 0, 1),
        ):
            with self.subTest(path=path):
                print_probe.parse_probe_result(_broken(path, at))
                with self.assertRaisesRegex(
                    print_probe.ProbeError, "longer than 1,000 characters"
                ):
                    print_probe.parse_probe_result(_broken(path, past))

    def test_a_list_past_its_bound_is_refused(self) -> None:
        image = _valid_raw()["images"][0]
        print_probe.parse_probe_result(_broken(("images",), [image] * 5_000))
        for path, entry in (
            (("images",), image),
            (("reads", 0, "values"), "v"),
        ):
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(print_probe.ProbeError, "more than 5,000 items"),
            ):
                print_probe.parse_probe_result(_broken(path, [entry] * 5_001))


def _with_images(fonts_status: str, *complete: bool) -> print_probe.ProbeResult:
    """A parsed result whose fonts read `fonts_status`, one image per `complete` flag."""
    images = [
        {
            "src": "/a.png",
            "hasSrc": True,
            "complete": flag,
            "loading": "",
            "naturalWidth": 1,
        }
        for flag in complete
    ]
    return print_probe.parse_probe_result(
        _valid_raw() | {"fontsStatus": fonts_status, "images": images}
    )


class SettledTests(unittest.TestCase):
    """A page is settled once every image is complete and its fonts are loaded."""

    def test_every_image_complete_and_fonts_loaded_is_settled(self) -> None:
        self.assertTrue(_with_images("loaded", True, True).settled)

    def test_one_incomplete_image_is_not_settled(self) -> None:
        self.assertFalse(_with_images("loaded", True, False).settled)

    def test_fonts_still_loading_is_not_settled(self) -> None:
        self.assertFalse(_with_images("loading", True).settled)

    def test_no_font_set_is_not_settled(self) -> None:
        self.assertFalse(_with_images("unavailable").settled)

    def test_no_images_and_fonts_loaded_is_settled(self) -> None:
        self.assertTrue(_with_images("loaded").settled)

    def test_an_incomplete_image_with_no_src_attribute_does_not_hold_the_settle(
        self,
    ) -> None:
        # An image with no src attribute requested nothing, so nothing is on its way: the
        # settle would wait out the whole timeout for it.
        result = _with_images("loaded", False)
        unrequested = dataclasses.replace(result.images[0], has_src=False)
        self.assertTrue(dataclasses.replace(result, images=(unrequested,)).settled)


class _RecordingPage:
    """A page with Playwright's `evaluate` shape: records each call, returns `result`."""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, object]] = []

    def evaluate(self, expression: str, arg: object = None) -> object:
        """Record the call and return the canned result."""
        self.calls.append((expression, arg))
        return self.result


class RunProbeTests(unittest.TestCase):
    """One call evaluates the probe's source with the argument built from the reads."""

    def test_the_call_is_the_source_with_the_built_argument(self) -> None:
        page = _RecordingPage(_valid_raw())
        result = print_probe.run_probe(page, [_THEME], ["theme"])
        self.assertEqual(
            page.calls,
            [
                (
                    print_probe.PROBE_SOURCE,
                    print_probe.probe_argument([_THEME], ["theme"]),
                )
            ],
        )
        self.assertEqual(result, _expected())

    def test_a_malformed_result_is_refused(self) -> None:
        with self.assertRaises(print_probe.ProbeError):
            print_probe.run_probe(_RecordingPage(None), [], [])

    def test_the_source_is_the_probe_file_as_written(self) -> None:
        probe_file = _TOOL_DIR / "src" / "print_probe.js"
        self.assertEqual(
            print_probe.PROBE_SOURCE, probe_file.read_text(encoding="utf-8")
        )


#: Arbitrary JSON values -- what any page's evaluate can hand back.
_JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=5),
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=5), children, max_size=3)
    ),
    max_leaves=10,
)

#: Every place a valid result can be broken, so the property reaches the deep refusals.
_BREAK_PATHS = tuple(path for path, _ in _WRONG_TYPES) + _REQUIRED_PATHS


class PropertyTests(unittest.TestCase):
    """Invariants over the input space, not just the examples above."""

    @given(st.one_of(_JSON, st.tuples(st.sampled_from(_BREAK_PATHS), _JSON)))
    def test_fuzz_parse_probe_result(self, drawn: object) -> None:
        raw = _broken(*drawn) if isinstance(drawn, tuple) else drawn
        try:
            result = print_probe.parse_probe_result(raw)
        except print_probe.ProbeError:
            return
        self.assertIsInstance(result, print_probe.ProbeResult)


if __name__ == "__main__":
    unittest.main()
