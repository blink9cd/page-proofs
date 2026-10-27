"""Grouped suite: what a valid targets file parses into, and reading one from disk.

Every refusal `parse_targets` makes, and the path it names, lives in
`test_print_targets_refusals.py`; this suite holds what parses, the file reader, the
command-line forms (`adhoc_targets`, `parse_viewport`) and the never-crash property.
"""

from __future__ import annotations

import re
import sys
import tempfile
import tomllib
import unittest
from collections.abc import Mapping
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import print_targets  # noqa: E402 - the tool's private src/, via the insert above
from _print_targets_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    doc,
    hosts_doc,
    read_doc,
    state_doc,
    viewport_doc,
)

settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    # The in-suite example count lives here, never in an `@settings` decorator, over which
    # a fuzzing campaign could not apply its own settings.
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

# The design's own example targets file, verbatim, comments included: the file a reader is
# shown first must be one the parser accepts.
SPEC_EXAMPLE = """\
viewport = { width = 1280, height = 800 }  # optional; else --viewport
backgrounds = "both"                         # optional; else --backgrounds
expected_offsite_hosts = ["example.org"]     # optional; still blocked, but a warning

[[read]]                                     # applied to every state of every target
name = "mode attribute"
selector = ":root"
attribute = "data-mode"                      # exactly one of: attribute, property, text

[[target]]
name = "home"
page = "/"
storage_reads = ["mode"]                     # stored values read back in-page

[[target.state]]
name = "default"                             # every key below is optional

[[target.state]]
name = "after-start"
actions = [{ click = "#menu-toggle" }]   # click | wait_for (a selector) | wait_ms

[[target.state]]
name = "stored-dark"
storage = { mode = "dark" }                  # set through storage_state: no injected JS

[[target.state]]
name = "os-dark-emulated"
color_scheme = "dark"                        # native (default) | light | dark | native-dark

[[target.state]]
name = "caught-at-3.4s"
print_at_ms = 3400                           # print this long after navigation began
"""

Action = print_targets.Action
Read = print_targets.Read
Target = print_targets.Target
TargetsError = print_targets.TargetsError

# The longest wait or print moment a state may ask for, a day in milliseconds, spelled out.
_DAY_MS = 24 * 60 * 60 * 1000

_DEFAULT_STATE = print_targets.State(
    name=print_targets.DEFAULT_STATE_NAME,
    color_scheme="native",
    reduced_motion="native",
    storage=(),
    actions=(),
    print_at_ms=None,
)


def _one_target(data: Mapping[str, object]) -> print_targets.Target:
    """The one target `data` parses into."""
    (target,) = print_targets.parse_targets(data).targets
    return target


class ExportedValuesTests(unittest.TestCase):
    """The values other modules match on, pinned literally rather than read back."""

    def test_the_exported_values_are_exactly_these(self) -> None:
        schemes = ("native", "light", "dark", "native-dark")
        self.assertEqual(print_targets.COLOR_SCHEMES, schemes)
        self.assertEqual(print_targets.NATIVE_DARK, "native-dark")
        self.assertEqual(print_targets.REDUCED_MOTIONS, ("native", "reduce"))
        self.assertEqual(print_targets.BACKGROUNDS, ("off", "on", "both"))
        kinds = ("attribute", "property", "text")
        self.assertEqual(print_targets.READ_KINDS, kinds)
        self.assertEqual(print_targets.DEFAULT_STATE_NAME, "default")
        self.assertEqual(print_targets.MAX_MS, _DAY_MS)
        # US Letter's 8.5 in at CSS's 96 px an inch, and the narrowest phone width.
        self.assertEqual(print_targets.PX_PER_INCH, 96)
        self.assertEqual(print_targets.LETTER_WIDTH_PX, 816)
        self.assertEqual(print_targets.MIN_PAGE_WIDTH, 320)


class ParseTargetsTests(unittest.TestCase):
    """What a valid document parses into."""

    def test_the_spec_example_parses_into_its_targets_and_states(self) -> None:
        parsed = print_targets.parse_targets(tomllib.loads(SPEC_EXAMPLE))
        self.assertEqual(parsed.viewport, print_targets.Viewport(1280, 800))
        self.assertEqual(parsed.backgrounds, "both")
        self.assertEqual(parsed.expected_offsite_hosts, ("example.org",))
        (home,) = parsed.targets
        self.assertEqual(
            [s.name for s in home.states],
            [
                "default",
                "after-start",
                "stored-dark",
                "os-dark-emulated",
                "caught-at-3.4s",
            ],
        )
        click = Action("click", "#menu-toggle", None)
        self.assertEqual(home.states[1].actions, (click,))
        self.assertEqual(home.states[2].storage, (("mode", "dark"),))
        self.assertEqual(home.states[4].print_at_ms, 3400)
        mode = Read("mode attribute", ":root", "attribute", "data-mode", False)
        self.assertEqual(home.reads[0], mode)
        self.assertEqual(home.storage_reads, ("mode",))

    def test_the_spec_example_states_default_every_key_they_leave_out(self) -> None:
        home = _one_target(tomllib.loads(SPEC_EXAMPLE))
        self.assertEqual((home.name, home.page), ("home", "/"))
        self.assertEqual(home.states[0], _DEFAULT_STATE)
        self.assertEqual(home.states[3].color_scheme, "dark")
        self.assertEqual(home.states[3].reduced_motion, "native")

    def test_a_target_without_states_gets_one_default_state(self) -> None:
        parsed = print_targets.parse_targets(doc())
        (target,) = parsed.targets
        self.assertEqual(target.states, (_DEFAULT_STATE,))
        self.assertEqual((target.storage_reads, target.reads), ((), ()))
        self.assertEqual((parsed.viewport, parsed.backgrounds), (None, None))
        self.assertEqual(parsed.expected_offsite_hosts, ())

    def test_names_with_single_dots_and_hyphens_between_characters_parse(self) -> None:
        for name in ("caught-at-3.4s", "page-1", "os-dark-emulated"):
            with self.subTest(name=name):
                target = _one_target(doc(name=name, state=[{"name": name}]))
                self.assertEqual((target.name, target.states[0].name), (name, name))

    def test_every_state_key_is_carried_through_in_file_order(self) -> None:
        actions = [{"wait_for": "main"}, {"click": "#go"}, {"wait_ms": 0}]
        storage = {"b": "2", "a": ""}
        (state,) = _one_target(
            state_doc(
                color_scheme="light",
                reduced_motion="reduce",
                storage=storage,
                actions=actions,
                print_at_ms=0,
                page_width=675,
            )
        ).states
        self.assertEqual((state.name, state.color_scheme), ("s", "light"))
        self.assertEqual(state.reduced_motion, "reduce")
        self.assertEqual(state.storage, (("b", "2"), ("a", "")))
        self.assertEqual(state.print_at_ms, 0)
        self.assertEqual(state.page_width, 675)
        self.assertEqual(
            state.actions,
            (
                Action("wait_for", "main", None),
                Action("click", "#go", None),
                Action("wait_ms", None, 0),
            ),
        )

    def test_a_day_is_the_longest_wait_and_print_moment_taken(self) -> None:
        actions = [{"wait_ms": _DAY_MS}]
        (state,) = _one_target(state_doc(actions=actions, print_at_ms=_DAY_MS)).states
        self.assertEqual(state.actions, (Action("wait_ms", None, _DAY_MS),))
        self.assertEqual(state.print_at_ms, _DAY_MS)

    def test_a_page_width_from_the_narrowest_phone_to_a_pixel_under_letter(
        self,
    ) -> None:
        for width in (320, 815):
            with self.subTest(page_width=width):
                (state,) = _one_target(state_doc(page_width=width)).states
                self.assertEqual(state.page_width, width)

    def test_a_state_without_a_page_width_leaves_the_engines_margins(self) -> None:
        (state,) = _one_target(state_doc()).states
        self.assertIsNone(state.page_width)

    def test_the_file_reads_come_before_each_targets_own(self) -> None:
        own = {"name": "b", "selector": "img", "property": "currentSrc", "all": True}
        data = {
            "read": [{"name": "a", "selector": "p", "text": True}],
            "target": [
                {"name": "one", "page": "/"},
                {"name": "two", "page": "/two/", "read": [own]},
            ],
        }
        one, two = print_targets.parse_targets(data).targets
        file_read = Read("a", "p", "text", None, False)
        own_read = Read("b", "img", "property", "currentSrc", True)
        self.assertEqual(one.reads, (file_read,))
        self.assertEqual(two.reads, (file_read, own_read))

    def test_every_value_of_each_enum_is_accepted(self) -> None:
        for scheme in print_targets.COLOR_SCHEMES:
            with self.subTest(color_scheme=scheme):
                target = _one_target(state_doc(color_scheme=scheme))
                self.assertEqual(target.states[0].color_scheme, scheme)
        for motion in print_targets.REDUCED_MOTIONS:
            with self.subTest(reduced_motion=motion):
                target = _one_target(state_doc(reduced_motion=motion))
                self.assertEqual(target.states[0].reduced_motion, motion)
        for backgrounds in print_targets.BACKGROUNDS:
            with self.subTest(backgrounds=backgrounds):
                data = {"backgrounds": backgrounds, **doc()}
                parsed = print_targets.parse_targets(data)
                self.assertEqual(parsed.backgrounds, backgrounds)


class ExpectedOffsiteHostsTests(unittest.TestCase):
    """The file-level hosts off loopback a site is known to load from."""

    def hosts(self, listed: object) -> tuple[str, ...]:
        """What a document listing `listed` as its expected hosts parses them into."""
        return print_targets.parse_targets(hosts_doc(listed)).expected_offsite_hosts

    def test_the_hosts_are_kept_in_file_order(self) -> None:
        listed = ["img.example.org", "example.net", "cdn.example.com"]
        self.assertEqual(self.hosts(listed), tuple(listed))

    def test_an_empty_list_expects_no_host(self) -> None:
        self.assertEqual(self.hosts([]), ())

    def test_a_host_a_listed_one_already_covers_is_not_a_duplicate(self) -> None:
        # Only the same name twice is refused: a subdomain beside its parent is redundant,
        # never wrong.
        listed = ["example.org", "img.example.org"]
        self.assertEqual(self.hosts(listed), tuple(listed))

    def test_every_hostname_shape_is_accepted(self) -> None:
        longest_label = "a" * 63
        # Four labels of 61 characters and two of 2, joined by dots: 253 characters, the
        # longest a hostname may be.
        longest_name = ".".join(["b" * 61] * 4) + ".ab.cd"
        self.assertEqual(len(longest_name), 253)
        for host in (
            "example.org",
            "a-b.example.org",
            "xn--bcher-kva.example",
            "1.example.org",
            "example.a1",
            "0x1.example.org",
            f"{longest_label}.example",
            longest_name,
        ):
            with self.subTest(host=host[:20]):
                self.assertEqual(self.hosts([host]), (host,))


class LoadTargetsTests(unittest.TestCase):
    """Reading a targets file from disk."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "targets.toml"

    def write(self, text: str) -> None:
        """Write `text` as the targets file, with the same bytes on every host."""
        self.path.write_text(text, encoding="utf-8", newline="\n")

    def refused_naming_the_file(self) -> None:
        """`load_targets` refuses `self.path` with a message that names it."""
        with self.assertRaises(TargetsError) as caught:
            print_targets.load_targets(self.path)
        self.assertIn(str(self.path), str(caught.exception))

    def test_a_file_loads_into_its_targets(self) -> None:
        self.write(SPEC_EXAMPLE)
        expected = print_targets.parse_targets(tomllib.loads(SPEC_EXAMPLE))
        self.assertEqual(print_targets.load_targets(self.path), expected)

    def test_a_missing_file_is_refused_naming_it(self) -> None:
        self.refused_naming_the_file()

    def test_invalid_toml_is_refused_naming_the_file(self) -> None:
        self.write('[[target]]\nname = "a"\nname = "b"\n')
        self.refused_naming_the_file()

    def test_bytes_that_are_not_utf8_are_refused_naming_the_file(self) -> None:
        self.path.write_bytes(b'[[target]]\nname = "\xff"\n')
        self.refused_naming_the_file()

    def test_a_refused_key_in_a_file_is_named_by_its_path(self) -> None:
        self.write('[[target]]\nname = "a"\npage = "a"\n')
        with self.assertRaisesRegex(TargetsError, r"^target\[0\]\.page: "):
            print_targets.load_targets(self.path)


class AdhocTargetsTests(unittest.TestCase):
    """Pages given on the command line rather than in a file."""

    def test_each_page_becomes_a_numbered_target_in_the_default_state(self) -> None:
        parsed = print_targets.adhoc_targets(["/", "/archive/"])
        self.assertEqual((parsed.viewport, parsed.backgrounds), (None, None))
        self.assertEqual(parsed.expected_offsite_hosts, ())
        first = Target("page-1", "/", (), (), (_DEFAULT_STATE,))
        second = Target("page-2", "/archive/", (), (), (_DEFAULT_STATE,))
        self.assertEqual(parsed.targets, (first, second))
        # A page given on the command line is printed at the engine's own margins.
        self.assertIsNone(parsed.targets[0].states[0].page_width)

    def test_a_page_that_is_not_a_server_path_is_refused_naming_it(self) -> None:
        with self.assertRaisesRegex(TargetsError, r"^page 'archive': "):
            print_targets.adhoc_targets(["/", "archive"])

    def test_a_dot_inside_a_segment_or_after_the_path_is_no_dot_segment(self) -> None:
        pages = ["/a.html", "/.well-known/", "/a..b/", "/a?up=..", "/a#.."]
        parsed = print_targets.adhoc_targets(pages)
        self.assertEqual([target.page for target in parsed.targets], pages)

    def test_a_page_with_a_dot_segment_is_refused_naming_it(self) -> None:
        with self.assertRaisesRegex(TargetsError, r"^page '/a/\.\./b': "):
            print_targets.adhoc_targets(["/", "/a/../b"])

    def test_no_pages_is_refused(self) -> None:
        with self.assertRaisesRegex(TargetsError, r"^page: "):
            print_targets.adhoc_targets([])


class ParseViewportTests(unittest.TestCase):
    """`--viewport`'s `WIDTHxHEIGHT`."""

    def refused(self, text: str) -> None:
        """`parse_viewport(text)` raises a `TargetsError` that quotes `text`."""
        with self.assertRaisesRegex(TargetsError, re.escape(repr(text))):
            print_targets.parse_viewport(text)

    def test_width_by_height_parses(self) -> None:
        viewport = print_targets.parse_viewport("1280x800")
        self.assertEqual(viewport, print_targets.Viewport(1280, 800))

    def test_a_zero_side_is_refused(self) -> None:
        for text in ("0x5", "5x0"):
            with self.subTest(text=text):
                self.refused(text)

    def test_a_single_number_is_refused(self) -> None:
        self.refused("12")

    def test_letters_are_refused(self) -> None:
        self.refused("axb")

    def test_every_other_shape_is_refused(self) -> None:
        # Arabic-Indic digits are digits to `int`; a side past `int`'s digit limit would
        # raise a plain ValueError rather than a refusal.
        too_long = "9" * 5000 + "x1"
        for text in ("1280x800\n", " 1280x800", "1280X800", "-1x5", "1x2x3", too_long):
            with self.subTest(text=text[:12]):
                self.refused(text)
        with self.assertRaises(TargetsError):
            print_targets.parse_viewport(chr(0x661) + "x5")


# Each table's keys, listed here rather than read from the subject, so a key it stops
# accepting is still drawn, and still planted.
_TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "file": ("viewport", "backgrounds", "expected_offsite_hosts", "read", "target"),
    "viewport": ("width", "height"),
    "target": ("name", "page", "storage_reads", "read", "state"),
    "state": (
        "name",
        "color_scheme",
        "reduced_motion",
        "storage",
        "actions",
        "print_at_ms",
        "page_width",
    ),
    "read": ("name", "selector", "attribute", "property", "text", "all"),
    "action": ("click", "wait_for", "wait_ms"),
}

# Every key a targets file holds at any depth, drawn as a table's keys beside arbitrary
# text, so a drawn table is often one the unknown-key check lets through.
_KNOWN_KEYS = tuple(sorted({key for keys in _TABLE_KEYS.values() for key in keys}))

# A read naming no kind yet, and one that reads text: the bases a drawn read key replaces.
# A read's kind is checked before its name, so only a read with one kind reaches the rest.
_KINDLESS_READ = {"name": "m", "selector": "h1"}
_TEXT_READ = {**_KINDLESS_READ, "text": True}

# Values some key accepts -- a name, a page, a selector, an enum value, or a whole state,
# target or read table -- drawn beside arbitrary ones, so a drawn value often passes its
# own check and the next one runs.
_VALID_SHAPED: tuple[object, ...] = (
    *("home", "s", "/", ":root", "dark", "reduce", "both", "native", "example.org"),
    *({"name": "s"}, {"name": "home", "page": "/"}, _TEXT_READ),
)

_DOCUMENTS = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.text()
    | st.sampled_from(_VALID_SHAPED),
    lambda inner: (
        st.lists(inner, max_size=3)
        | st.dictionaries(st.sampled_from(_KNOWN_KEYS) | st.text(), inner, max_size=4)
    ),
    max_leaves=12,
)


def _placements(drawn: object) -> list[Mapping[str, object]]:
    """`drawn` as a whole document, then planted at each key of an otherwise valid one.

    A drawn document seldom reaches the deeper validators itself -- most drawn tables
    hold some key another table owns -- so planted, each validator reads it directly.
    """
    own_kind = ("attribute", "property")
    return [
        drawn if isinstance(drawn, dict) else {"target": drawn},
        # As each kind of table -- twice where tables are named, so a drawn table that
        # passes also meets the repeated-name refusal.
        {"target": [drawn, drawn]},
        doc(state=[drawn, drawn]),
        doc(read=[drawn, drawn]),
        state_doc(actions=[drawn]),
        # As the value at each key.
        *({key: drawn, **doc()} for key in _TABLE_KEYS["file"] if key != "target"),
        viewport_doc({"width": drawn, "height": 1}),
        *(doc(**{key: drawn}) for key in _TABLE_KEYS["target"]),
        *(state_doc(**{key: drawn}) for key in _TABLE_KEYS["state"]),
        *(state_doc(actions=[{key: drawn}]) for key in _TABLE_KEYS["action"]),
        *(read_doc({**_KINDLESS_READ, key: drawn}) for key in own_kind),
        *(
            read_doc({**_TEXT_READ, key: drawn})
            for key in _TABLE_KEYS["read"]
            if key not in own_kind
        ),
    ]


class PropertyTests(unittest.TestCase):
    """Never crash: any document either parses or is refused with a `TargetsError`."""

    @given(_DOCUMENTS)
    def test_fuzz_parse_targets(self, drawn: object) -> None:
        for data in _placements(drawn):
            try:
                parsed = print_targets.parse_targets(data)
            except TargetsError:
                continue
            self.assertIsInstance(parsed, print_targets.TargetsFile)


if __name__ == "__main__":
    unittest.main()
