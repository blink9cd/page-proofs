"""Grouped suite: the targets file -- what it accepts, what it refuses -- and the matrix it expands to."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pass_targets  # noqa: E402 - the tool's private src/, via the insert above

# The determinism convention: a profile registered at import, never an `@settings`
# decorator, which a fuzzing run applying its own settings could not override.
# deadline=None: a per-example wall-clock deadline measures machine load, not
# correctness, so a busy machine would report a passing property as flaky.
# too_slow is suppressed for the same reason: on a cold .hypothesis cache the
# first draw also builds Hypothesis's unicode tables and scans the local modules
# for constants, which under load outlasts the limit that check allows even
# without a deadline.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: A minimal valid targets document, as `tomllib` would hand it over.
_VALID: dict[str, object] = {
    "pages": ["/", "/archive/"],
    "viewport": [
        {"width": 390, "height": 844, "rotates": True},
        {"width": 1280, "height": 800},
    ],
}


#: Every key a table in a targets document can hold: a viewport's, a state's and an action's.
_FUZZ_TABLE_KEYS = [
    "width",
    "height",
    "rotates",
    "name",
    "page",
    "storage",
    "actions",
    "click",
    "wait_for",
    "wait_ms",
]

#: A state holding every key a state may: a page with a query, stored values, three actions.
_STATE: dict[str, object] = {
    "name": "after-start",
    "page": "/?panel=1",
    "storage": {"mode": "dark"},
    "actions": [{"click": "#start"}, {"wait_for": "#grid"}, {"wait_ms": 300}],
}


def _with(**changes: object) -> dict[str, object]:
    """The valid document with `changes` applied; a value of None removes that key."""
    document = {**_VALID, **changes}
    return {key: value for key, value in document.items() if value is not None}


class ParseTargetsTests(unittest.TestCase):
    """What a targets document must hold, and each way it can be refused."""

    def test_reads_pages_viewports_and_an_absent_base_url(self) -> None:
        targets = pass_targets.parse_targets(_VALID)
        self.assertEqual(targets.pages, ("/", "/archive/"))
        self.assertEqual(
            targets.viewports,
            (
                pass_targets.Viewport(390, 844, True),
                pass_targets.Viewport(1280, 800, False),
            ),
        )
        self.assertIsNone(targets.base_url)

    def test_reads_a_base_url_without_its_trailing_slash(self) -> None:
        targets = pass_targets.parse_targets(_with(base_url="http://127.0.0.1:8123/"))
        self.assertEqual(targets.base_url, "http://127.0.0.1:8123")

    def test_a_page_named_twice_is_measured_once(self) -> None:
        targets = pass_targets.parse_targets(_with(pages=["/", "/archive/", "/"]))
        self.assertEqual(targets.pages, ("/", "/archive/"))

    def test_a_page_may_carry_a_fragment_and_keeps_it(self) -> None:
        # An anchored arrival lands scrolled to its target: a different reading of one page.
        targets = pass_targets.parse_targets(_with(pages=["/", "/index.html#latest"]))
        self.assertEqual(targets.pages, ("/", "/index.html#latest"))

    def test_a_page_may_carry_a_query_and_keeps_it(self) -> None:
        # The query reaches the server, and the page reads it: `?panel=1` opens a panel.
        targets = pass_targets.parse_targets(_with(pages=["/?panel=1", "/?panel=2"]))
        self.assertEqual(targets.pages, ("/?panel=1", "/?panel=2"))

    def test_refuses_a_page_that_is_not_a_server_path(self) -> None:
        for page in ("index.html", "#latest", "?panel=1"):
            with (
                self.subTest(page=page),
                self.assertRaisesRegex(pass_targets.TargetsError, "start it with /"),
            ):
                pass_targets.parse_targets(_with(pages=[page]))

    def test_refuses_a_page_with_a_dot_segment(self) -> None:
        # No page needs one, and a .. could name a file outside the served folder, which is
        # checked for and read before anything renders.
        for page in (
            "/..",
            "/../x.html",
            "/a/../../x.html",
            "/./",
            "/a/./b.html",
            "/a/..?q=1",
            # A backslash separates segments too, wherever a server reads it as one.
            "/\\..\\x",
            "/a\\..\\..\\x.html",
            "/a/.\\b.html",
        ):
            with (
                self.subTest(page=page),
                self.assertRaisesRegex(
                    pass_targets.TargetsError, r"a \. or \.\. segment"
                ),
            ):
                pass_targets.parse_targets(_with(pages=[page]))

    def test_a_dot_inside_a_segment_or_past_the_path_is_kept(self) -> None:
        pages = ["/a.html", "/.well-known/x", "/a..b/", "/?next=../x", "/#../x"]
        self.assertEqual(
            pass_targets.parse_targets(_with(pages=pages)).pages, tuple(pages)
        )

    def test_refuses_a_state_page_or_a_not_found_with_a_dot_segment(self) -> None:
        documents = {
            "state": _with(state=[{**_STATE, "page": "/a/../../x.html"}]),
            "not_found": _with(not_found="/../404.html"),
        }
        for key, document in documents.items():
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(
                    pass_targets.TargetsError, r"a \. or \.\. segment"
                ),
            ):
                pass_targets.parse_targets(document)

    def test_the_server_path_of_a_page_drops_its_query_and_fragment(self) -> None:
        cases = {
            "/index.html#latest": "/index.html",
            "/#latest-1": "/",
            "/archive/": "/archive/",
            "/a#b#c": "/a",
            "/?panel=1": "/",
            "/archive/?a=1#x": "/archive/",
            "/a#b?c": "/a",
        }
        for page, served in cases.items():
            with self.subTest(page=page):
                self.assertEqual(pass_targets.server_path(page), served)

    def test_refuses_no_pages(self) -> None:
        not_page_lists: tuple[object, ...] = ([], None, "/")
        for pages in not_page_lists:
            with (
                self.subTest(pages=pages),
                self.assertRaisesRegex(pass_targets.TargetsError, "pages"),
            ):
                pass_targets.parse_targets(_with(pages=pages))

    def test_refuses_a_page_that_is_not_text(self) -> None:
        with self.assertRaisesRegex(pass_targets.TargetsError, "pages"):
            pass_targets.parse_targets(_with(pages=["/", 3]))

    def test_refuses_a_base_url_that_is_not_http(self) -> None:
        for url in ("127.0.0.1:8123", "file:///site", 8123):
            with (
                self.subTest(url=url),
                self.assertRaisesRegex(pass_targets.TargetsError, "base_url"),
            ):
                pass_targets.parse_targets(_with(base_url=url))

    def test_refuses_no_viewports(self) -> None:
        not_viewport_arrays: tuple[object, ...] = (
            [],
            None,
            {"width": 390, "height": 844},
        )
        for viewports in not_viewport_arrays:
            with (
                self.subTest(viewports=viewports),
                self.assertRaisesRegex(pass_targets.TargetsError, "viewport"),
            ):
                pass_targets.parse_targets(_with(viewport=viewports))

    def test_refuses_a_viewport_that_is_not_a_table(self) -> None:
        with self.assertRaisesRegex(pass_targets.TargetsError, "viewport 1"):
            pass_targets.parse_targets(_with(viewport=[390]))

    def test_refuses_a_size_that_is_not_a_positive_whole_number(self) -> None:
        for bad in (0, -390, 390.5, "390", True):
            with (
                self.subTest(bad=bad),
                self.assertRaisesRegex(pass_targets.TargetsError, "width"),
            ):
                pass_targets.parse_targets(
                    _with(viewport=[{"width": bad, "height": 844}])
                )

    def test_refuses_a_rotates_flag_that_is_not_a_boolean(self) -> None:
        with self.assertRaisesRegex(pass_targets.TargetsError, "rotates"):
            pass_targets.parse_targets(
                _with(viewport=[{"width": 390, "height": 844, "rotates": "yes"}])
            )

    def test_refuses_a_square_viewport_that_rotates(self) -> None:
        with self.assertRaisesRegex(pass_targets.TargetsError, "square"):
            pass_targets.parse_targets(
                _with(viewport=[{"width": 600, "height": 600, "rotates": True}])
            )

    def test_refuses_an_unknown_key_rather_than_ignoring_a_typo(self) -> None:
        with self.assertRaisesRegex(pass_targets.TargetsError, "base_ulr"):
            pass_targets.parse_targets(_with(base_ulr="http://127.0.0.1:1"))
        with self.assertRaisesRegex(pass_targets.TargetsError, "rotate"):
            pass_targets.parse_targets(
                _with(viewport=[{"width": 390, "height": 844, "rotate": True}])
            )

    @given(
        st.dictionaries(
            st.sampled_from(
                [
                    "pages",
                    "viewport",
                    "base_url",
                    "state",
                    "not_found",
                    "redirects",
                    "extra",
                ]
            ),
            st.recursive(
                st.none() | st.booleans() | st.integers() | st.text(max_size=8),
                lambda inner: (
                    st.lists(inner, max_size=3)
                    | st.dictionaries(st.sampled_from(_FUZZ_TABLE_KEYS), inner)
                ),
                max_leaves=10,
            ),
        )
    )
    def test_fuzz_parse_targets(self, document: dict[str, object]) -> None:
        """Any shape parses or is refused as a TargetsError -- never another exception."""
        try:
            targets = pass_targets.parse_targets(document)
        except pass_targets.TargetsError:
            return
        self.assertTrue(targets.pages)
        self.assertTrue(targets.viewports)
        names = [state.name for state in targets.states]
        self.assertEqual(len(names), len(set(names)))
        for state in targets.states:
            self.assertTrue(state.page.startswith("/"))
        for page in (*pass_targets.served_pages(targets), targets.not_found or "/"):
            self.assertFalse({".", ".."} & set(page.split("/")), page)
        if targets.not_found is not None:
            self.assertTrue(targets.not_found.startswith("/"))
            self.assertFalse(targets.not_found.startswith("//"))
        self.assertLessEqual(set(targets.redirects), set(targets.pages))


class LoadTargetsTests(unittest.TestCase):
    """Reading the file itself."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name) / "targets.toml"

    def test_loads_a_valid_file(self) -> None:
        self.path.write_text(
            'pages = ["/"]\n[[viewport]]\nwidth = 390\nheight = 844\n',
            encoding="utf-8",
            newline="\n",
        )
        self.assertEqual(pass_targets.load_targets(self.path).pages, ("/",))

    def test_refuses_toml_that_does_not_parse(self) -> None:
        self.path.write_text("pages = [\n", encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(pass_targets.TargetsError, "not valid TOML"):
            pass_targets.load_targets(self.path)

    def test_refuses_bytes_that_are_not_utf8(self) -> None:
        # tomllib raises UnicodeDecodeError, not TOMLDecodeError, on bytes like these.
        self.path.write_bytes(b'pages = ["/\xff"]\n')
        with self.assertRaisesRegex(pass_targets.TargetsError, "not valid TOML"):
            pass_targets.load_targets(self.path)


class MatrixTests(unittest.TestCase):
    """Expanding the targets into one rendering per engine x page x size x mode x scripts."""

    def setUp(self) -> None:
        self.targets = pass_targets.parse_targets(_VALID)

    def test_a_rotating_viewport_runs_portrait_then_landscape(self) -> None:
        sizes = pass_targets.viewport_sizes(
            self.targets.viewports, ("portrait", "landscape")
        )
        self.assertEqual(sizes, [(390, 844), (844, 390), (1280, 800)])

    def test_portrait_is_the_taller_shape_however_the_file_writes_it(self) -> None:
        sideways = (pass_targets.Viewport(844, 390, True),)
        self.assertEqual(
            pass_targets.viewport_sizes(sideways, ("portrait",)), [(390, 844)]
        )

    def test_an_orientation_filter_applies_to_rotating_viewports_only(self) -> None:
        sizes = pass_targets.viewport_sizes(self.targets.viewports, ("landscape",))
        self.assertEqual(sizes, [(844, 390), (1280, 800)])

    def test_a_size_reached_twice_is_rendered_once(self) -> None:
        both = (
            pass_targets.Viewport(390, 844, True),
            pass_targets.Viewport(844, 390, False),
        )
        self.assertEqual(
            pass_targets.viewport_sizes(both, ("portrait", "landscape")),
            [(390, 844), (844, 390)],
        )

    def test_expands_every_axis_engine_outermost(self) -> None:
        renderings = pass_targets.expand_matrix(
            self.targets,
            engines=("chromium", "webkit"),
            orientations=("portrait", "landscape"),
            modes=("light", "dark"),
            scripts=("on", "off"),
        )
        self.assertEqual(len(renderings), 2 * 2 * 3 * 2 * 2)
        self.assertEqual(
            renderings[0],
            pass_targets.Rendering("chromium", "/", 390, 844, "light", "on"),
        )
        self.assertEqual(renderings[-1].engine, "webkit")
        self.assertEqual({r.engine for r in renderings[:24]}, {"chromium"})

    def test_a_rendering_names_its_orientation_and_its_cross_engine_key(self) -> None:
        wide = pass_targets.Rendering("firefox", "/archive/", 844, 390, "dark", "off")
        tall = pass_targets.Rendering("webkit", "/archive/", 390, 844, "dark", "off")
        self.assertEqual(
            (wide.orientation, tall.orientation), ("landscape", "portrait")
        )
        self.assertEqual(wide.key, ("/archive/", None, 844, 390, "dark", "off"))
        self.assertEqual(wide.label, "firefox /archive/ 844x390 dark scripts-off")

    def test_a_state_is_in_its_rendering_s_key_and_label(self) -> None:
        opened = pass_targets.Rendering(
            "chromium", "/?panel=1", 390, 844, "light", "on", "after-start"
        )
        self.assertEqual(
            opened.key, ("/?panel=1", "after-start", 390, 844, "light", "on")
        )
        self.assertEqual(
            opened.label, "chromium /?panel=1 [after-start] 390x844 light scripts-on"
        )
        self.assertEqual(opened.page_label, "/?panel=1 [after-start]")
        plain = pass_targets.Rendering("webkit", "/", 390, 844, "dark", "off")
        self.assertEqual(plain.page_label, "/")

    def test_a_state_renders_after_the_pages_with_scripts_on_only(self) -> None:
        targets = pass_targets.parse_targets(_with(state=[_STATE]))
        renderings = pass_targets.expand_matrix(
            targets,
            engines=("chromium", "webkit"),
            orientations=("portrait", "landscape"),
            modes=("light", "dark"),
            scripts=("on", "off"),
        )
        pages, states = 2 * 3 * 2 * 2, 3 * 2
        self.assertEqual(len(renderings), 2 * (pages + states))
        chromium = renderings[: pages + states]
        self.assertEqual({r.engine for r in chromium}, {"chromium"})
        self.assertEqual({r.state for r in chromium[:pages]}, {None})
        self.assertEqual(
            chromium[pages],
            pass_targets.Rendering(
                "chromium", "/?panel=1", 390, 844, "light", "on", "after-start"
            ),
        )
        self.assertEqual(
            {(r.state, r.scripts) for r in chromium[pages:]}, {("after-start", "on")}
        )

    def test_a_state_is_not_rendered_without_scripts(self) -> None:
        targets = pass_targets.parse_targets(_with(state=[_STATE]))
        renderings = pass_targets.expand_matrix(
            targets,
            engines=("chromium",),
            orientations=("portrait",),
            modes=("light",),
            scripts=("off",),
        )
        # Two pages at two sizes, and nothing of the state.
        self.assertEqual([r.state for r in renderings], [None] * 4)


class NameRenderingsTests(unittest.TestCase):
    """How a report line names several renderings: how many, then a handful by name."""

    def test_names_one_rendering_in_the_singular(self) -> None:
        self.assertEqual(pass_targets.name_renderings(["a"]), "1 rendering: a")

    def test_names_up_to_a_handful_and_counts_the_rest(self) -> None:
        labels = [f"r{n}" for n in range(pass_targets.NAMED_RENDERINGS + 2)]
        self.assertEqual(
            pass_targets.name_renderings(labels),
            f"{len(labels)} renderings: r0; r1; r2; r3; r4; and 2 more",
        )
        self.assertEqual(pass_targets.NAMED_RENDERINGS, 5)


if __name__ == "__main__":
    unittest.main()
