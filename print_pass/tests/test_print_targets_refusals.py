"""Grouped suite: every targets document `parse_targets` refuses, and the path it names.

A refusal is asserted by the PATH its message opens with, because that path is the whole of
what an operator is told about a targets file that was refused. What a valid document
parses into lives in `test_print_targets.py`, beside the file reader and the property test.
"""

from __future__ import annotations

import re
import sys
import unittest
from collections.abc import Mapping
from pathlib import Path

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

TargetsError = print_targets.TargetsError

# The paths the refusals below name most often: the first target, its first state, and
# that state's first action.
_T0 = "target[0]"
_S0 = f"{_T0}.state[0]"
_A0 = f"{_S0}.actions[0]"

# The file-level hosts off loopback a site expects, and the first of them.
_HOSTS = "expected_offsite_hosts"
_H0 = f"{_HOSTS}[0]"

# What an entry outside the hostname alphabet is told, whatever it holds instead.
_NOT_A_HOSTNAME = "must be a lowercase hostname"

# The longest wait or print moment a state may ask for, a day in milliseconds: spelled out
# rather than read from the subject, so a change to its ceiling fails here.
_DAY_MS = 24 * 60 * 60 * 1000

# What a page width outside the narrowest phone to a pixel under US Letter is told, spelled
# out rather than read from the subject.
_WIDTH_RANGE = "must be a whole number from 320 to 815"

# Two reads sharing one name. Read-backs are reported by name, so a second read of one name
# within a target -- the file's reads followed by its own -- is refused at the LATER read's
# path.
_FIRST_READ = {"name": "m", "selector": "h1", "text": True}
_SECOND_READ = {**_FIRST_READ, "selector": "h2"}


class _Refusals(unittest.TestCase):
    """The one assertion every refusal test makes."""

    def refused(self, data: Mapping[str, object], where: str) -> None:
        """`parse_targets(data)` raises a `TargetsError` whose message opens with `where`."""
        with self.assertRaisesRegex(TargetsError, rf"^{re.escape(where)}: "):
            print_targets.parse_targets(data)


class FileAndTargetRefusalTests(_Refusals):
    """The top level, each `[[target]]` and each `[[target.state]]`."""

    def width_refused(self, width: object) -> None:
        """A `page_width` of `width` is refused for its value, not as an unknown key."""
        where = re.escape(f"{_S0}.page_width")
        with self.assertRaisesRegex(TargetsError, rf"^{where}: {_WIDTH_RANGE}$"):
            print_targets.parse_targets(state_doc(page_width=width))

    def test_an_unknown_key_is_refused_by_its_path(self) -> None:
        pattern = r"target\[0\]\.state\[0\]\.scheme"
        target = {"name": "a", "page": "/", "state": [{"name": "s", "scheme": "dark"}]}
        with self.assertRaisesRegex(TargetsError, pattern):
            print_targets.parse_targets({"target": [target]})

    def test_an_unknown_top_level_key_is_refused(self) -> None:
        self.refused({"pages": ["/"], **doc()}, "pages")

    def test_no_targets_at_all_is_refused(self) -> None:
        self.refused({}, "target")
        self.refused({"target": []}, "target")

    def test_a_target_that_is_not_an_array_of_tables_is_refused(self) -> None:
        self.refused({"target": {"name": "home", "page": "/"}}, "target")
        self.refused({"target": ["home"]}, _T0)

    def test_a_target_missing_a_required_key_is_refused(self) -> None:
        self.refused({"target": [{"page": "/"}]}, f"{_T0}.name")
        self.refused({"target": [{"name": "home"}]}, f"{_T0}.page")

    def test_a_duplicate_target_name_is_refused(self) -> None:
        home = {"name": "home", "page": "/"}
        self.refused({"target": [home, {**home, "page": "/b/"}]}, "target[1].name")

    def test_a_target_name_outside_the_pattern_is_refused(self) -> None:
        # A name ends up in an output file's name; a trailing newline is the case a
        # `$`-anchored match would let through.
        for name in ("Home", "-home", ".home", "home page", "", "home\n", 7):
            with self.subTest(name=name):
                self.refused({"target": [{"name": name, "page": "/"}]}, f"{_T0}.name")

    def test_a_name_with_a_doubled_or_trailing_separator_is_refused(self) -> None:
        # Names are joined with `--` into an output file's name, so a `--` inside one
        # would let two target and state pairs name the same file.
        for name in ("a--b", "a..b", "a.-b", "home-", "home."):
            with self.subTest(name=name):
                self.refused({"target": [{"name": name, "page": "/"}]}, f"{_T0}.name")

    def test_a_state_name_with_a_doubled_separator_is_refused(self) -> None:
        self.refused(doc(state=[{"name": "b--c"}]), f"{_S0}.name")

    def test_a_page_that_is_not_a_server_path_is_refused(self) -> None:
        # Concatenated onto the origin's url, neither `//other/` nor `/\other/` moves the
        # host. RESOLVED as a url reference, `//other/` names ANOTHER host to `urljoin` and
        # a browser alike, and `/\other/` does to a WHATWG parser such as a browser's, the
        # only kind that reads `/\` as `//`. The refusal guards that route.
        for page in ("home.html", "", 1, "//other/", "/\\other/"):
            with self.subTest(page=page):
                self.refused({"target": [{"name": "t", "page": page}]}, f"{_T0}.page")

    def test_a_page_with_a_dot_segment_is_refused(self) -> None:
        # No page needs one, and a `..` can name a file outside the served folder. A
        # backslash separates segments too, as a server reading a Windows path takes it.
        for page in (
            "/..",
            "/a/../b",
            "/./a",
            "/a/.",
            "/a\\..\\b",
            "/a/..?q",
            "/a/.#f",
            # Percent-encoded, as a browser decodes them before it resolves the path.
            "/%2e%2e/x",
            "/a/%2E/b",
            "/%2e/",
        ):
            with self.subTest(page=page):
                where = f"{_T0}.page"
                with self.assertRaisesRegex(
                    TargetsError, rf"^{re.escape(where)}: .* \.\. "
                ):
                    print_targets.parse_targets(
                        {"target": [{"name": "t", "page": page}]}
                    )

    def test_storage_reads_must_be_an_array_of_non_empty_strings(self) -> None:
        self.refused(doc(storage_reads="mode"), f"{_T0}.storage_reads")
        self.refused(doc(storage_reads=["mode", ""]), f"{_T0}.storage_reads[1]")
        self.refused(doc(storage_reads=[3]), f"{_T0}.storage_reads[0]")

    def test_an_empty_or_malformed_state_array_is_refused(self) -> None:
        self.refused(doc(state=[]), f"{_T0}.state")
        self.refused(doc(state={"name": "s"}), f"{_T0}.state")
        self.refused(doc(state=["s"]), _S0)
        self.refused(doc(state=[{}]), f"{_S0}.name")

    def test_a_duplicate_state_name_is_refused(self) -> None:
        self.refused(doc(state=[{"name": "s"}, {"name": "s"}]), f"{_T0}.state[1].name")

    def test_a_state_name_outside_the_pattern_is_refused(self) -> None:
        self.refused(doc(state=[{"name": "Dark"}]), f"{_S0}.name")

    def test_a_color_scheme_outside_its_values_is_refused(self) -> None:
        self.refused(state_doc(color_scheme="night"), f"{_S0}.color_scheme")

    def test_a_reduced_motion_outside_its_values_is_refused(self) -> None:
        self.refused(state_doc(reduced_motion="none"), f"{_S0}.reduced_motion")

    def test_a_backgrounds_outside_its_values_is_refused(self) -> None:
        for backgrounds in ("yes", True):
            with self.subTest(backgrounds=backgrounds):
                self.refused({"backgrounds": backgrounds, **doc()}, "backgrounds")

    def test_a_print_at_ms_below_zero_or_not_whole_is_refused(self) -> None:
        for ms in (-1, 1.5, True, "3400"):
            with self.subTest(print_at_ms=ms):
                self.refused(state_doc(print_at_ms=ms), f"{_S0}.print_at_ms")

    def test_a_print_at_ms_past_a_day_is_refused(self) -> None:
        for ms in (_DAY_MS + 1, 10**15):
            with self.subTest(print_at_ms=ms):
                self.refused(state_doc(print_at_ms=ms), f"{_S0}.print_at_ms")

    def test_a_page_width_that_is_not_a_whole_number_is_refused(self) -> None:
        for width in (675.5, "675", True, False):
            with self.subTest(page_width=width):
                self.width_refused(width)

    def test_a_page_width_that_is_not_positive_is_refused(self) -> None:
        for width in (0, -1, -675):
            with self.subTest(page_width=width):
                self.width_refused(width)

    def test_a_page_width_narrower_than_any_phone_is_refused(self) -> None:
        for width in (1, 319):
            with self.subTest(page_width=width):
                self.width_refused(width)

    def test_a_page_width_leaving_letter_no_margin_is_refused(self) -> None:
        for width in (816, 817, 10**6):
            with self.subTest(page_width=width):
                self.width_refused(width)

    def test_storage_with_a_non_string_value_is_refused(self) -> None:
        self.refused(state_doc(storage={"mode": 1}), f"{_S0}.storage.mode")

    def test_storage_with_a_non_string_key_is_refused(self) -> None:
        self.refused(state_doc(storage={1: "dark"}), f"{_S0}.storage.1")

    def test_storage_that_is_not_a_table_is_refused(self) -> None:
        self.refused(state_doc(storage="mode=dark"), f"{_S0}.storage")

    def test_actions_that_are_not_an_array_are_refused(self) -> None:
        self.refused(state_doc(actions={"click": "#go"}), f"{_S0}.actions")


class ActionRefusalTests(_Refusals):
    """One action: a table holding exactly one of `click`, `wait_for`, `wait_ms`."""

    def action_refused(self, action: object, where: str) -> None:
        """A state whose one action is `action` is refused at `where`."""
        self.refused(state_doc(actions=[action]), where)

    def test_an_action_with_two_keys_is_refused(self) -> None:
        self.action_refused({"click": "#go", "wait_ms": 5}, _A0)

    def test_an_action_with_an_unknown_key_is_refused(self) -> None:
        self.action_refused({"hover": "#go"}, f"{_A0}.hover")

    def test_an_empty_action_is_refused(self) -> None:
        self.action_refused({}, _A0)

    def test_an_action_that_is_not_a_table_is_refused(self) -> None:
        self.action_refused("click #go", _A0)

    def test_a_click_or_wait_for_needs_a_non_empty_selector(self) -> None:
        for kind in ("click", "wait_for"):
            for selector in ("", 3):
                with self.subTest(kind=kind, selector=selector):
                    self.action_refused({kind: selector}, f"{_A0}.{kind}")

    def test_a_negative_wait_ms_is_refused(self) -> None:
        self.action_refused({"wait_ms": -1}, f"{_A0}.wait_ms")

    def test_a_boolean_or_fractional_wait_ms_is_refused(self) -> None:
        for ms in (True, 2.5):
            with self.subTest(ms=ms):
                self.action_refused({"wait_ms": ms}, f"{_A0}.wait_ms")

    def test_a_wait_ms_past_a_day_is_refused(self) -> None:
        for ms in (_DAY_MS + 1, 10**15):
            with self.subTest(ms=ms):
                self.action_refused({"wait_ms": ms}, f"{_A0}.wait_ms")


class ReadRefusalTests(_Refusals):
    """One `[[read]]`: a name, a selector, and exactly one of attribute, property, text."""

    def test_a_read_with_two_of_attribute_and_property_is_refused(self) -> None:
        read = {"name": "m", "selector": ":root", "attribute": "a", "property": "id"}
        self.refused(read_doc(read), "read[0]")

    def test_a_read_with_none_of_attribute_property_text_is_refused(self) -> None:
        self.refused(read_doc({"name": "m", "selector": ":root"}), "read[0]")

    def test_a_read_with_text_false_is_refused(self) -> None:
        for text in (False, "yes", 1):
            with self.subTest(text=text):
                read = {"name": "m", "selector": "h1", "text": text}
                self.refused(read_doc(read), "read[0].text")

    def test_an_empty_or_non_string_attribute_or_property_is_refused(self) -> None:
        for kind, key in (("attribute", ""), ("property", 5)):
            with self.subTest(kind=kind):
                read = {"name": "m", "selector": ":root", kind: key}
                self.refused(read_doc(read), f"read[0].{kind}")

    def test_a_read_needs_a_non_empty_name_and_selector(self) -> None:
        for read, where in (
            ({"selector": "h1"}, "read[0].name"),
            ({"name": "", "selector": "h1"}, "read[0].name"),
            ({"name": "m", "selector": ""}, "read[0].selector"),
        ):
            with self.subTest(read=read):
                self.refused(read_doc({**read, "text": True}), where)

    def test_a_read_all_that_is_not_a_boolean_is_refused(self) -> None:
        for every in ("yes", 1):
            with self.subTest(all=every):
                read = {"name": "m", "selector": "img", "text": True, "all": every}
                self.refused(read_doc(read), "read[0].all")

    def test_a_read_with_an_unknown_key_is_refused(self) -> None:
        read = {"name": "m", "selector": "h1", "text": True, "xpath": "//h1"}
        self.refused(read_doc(read), "read[0].xpath")

    def test_reads_that_are_not_an_array_of_tables_are_refused(self) -> None:
        self.refused({"read": {"name": "m"}, **doc()}, "read")
        self.refused(read_doc("h1"), "read[0]")

    def test_a_target_read_is_refused_by_its_own_path(self) -> None:
        self.refused(doc(read=[{"name": "m", "selector": "h1"}]), f"{_T0}.read[0]")

    def test_two_file_reads_with_one_name_are_refused(self) -> None:
        self.refused({"read": [_FIRST_READ, _SECOND_READ], **doc()}, "read[1].name")

    def test_two_target_reads_with_one_name_are_refused(self) -> None:
        self.refused(doc(read=[_FIRST_READ, _SECOND_READ]), f"{_T0}.read[1].name")

    def test_a_target_read_sharing_a_file_reads_name_is_refused(self) -> None:
        data = {"read": [_FIRST_READ], **doc(read=[_SECOND_READ])}
        self.refused(data, f"{_T0}.read[0].name")


class ViewportRefusalTests(_Refusals):
    """The optional `viewport` table: exactly `width` and `height`, whole pixels above zero."""

    def test_a_boolean_width_is_refused(self) -> None:
        self.refused(viewport_doc({"width": True, "height": 800}), "viewport.width")

    def test_a_zero_width_is_refused(self) -> None:
        self.refused(viewport_doc({"width": 0, "height": 800}), "viewport.width")

    def test_a_fractional_height_is_refused(self) -> None:
        self.refused(viewport_doc({"width": 1, "height": 800.5}), "viewport.height")

    def test_a_viewport_missing_its_height_is_refused(self) -> None:
        self.refused(viewport_doc({"width": 1280}), "viewport.height")

    def test_a_viewport_with_an_extra_key_is_refused(self) -> None:
        viewport = {"width": 1280, "height": 800, "rotates": True}
        self.refused(viewport_doc(viewport), "viewport.rotates")

    def test_a_viewport_that_is_not_a_table_is_refused(self) -> None:
        self.refused(viewport_doc("1280x800"), "viewport")


class ExpectedOffsiteHostRefusalTests(_Refusals):
    """`expected_offsite_hosts`: an array of lowercase hostnames, each listed once.

    Each case names the reason as well as the path: the hosts are one array of strings, so
    the path alone would not say which rule an entry broke.
    """

    def host_refused(self, host: object, reason: str) -> None:
        """A list whose one entry is `host` is refused at that entry, saying `reason`."""
        pattern = rf"^{re.escape(_H0)}: {reason}"
        with self.assertRaisesRegex(TargetsError, pattern):
            print_targets.parse_targets(hosts_doc([host]))

    def test_a_list_that_is_not_an_array_is_refused(self) -> None:
        # The reason as well as the path: a key the file does not know is refused at this
        # same path, so the path alone would pass with no such key at all.
        pattern = rf"^{re.escape(_HOSTS)}: must be an array"
        for value in ("example.org", {"host": "example.org"}):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(TargetsError, pattern),
            ):
                print_targets.parse_targets(hosts_doc(value))

    def test_an_entry_that_is_not_a_non_empty_string_is_refused(self) -> None:
        for host in ("", 3, True, ["example.org"]):
            with self.subTest(host=host):
                self.host_refused(host, "must be a non-empty string")

    def test_a_scheme_port_path_or_wildcard_is_refused(self) -> None:
        for host in (
            "https://example.org",
            "example.org:443",
            "example.org/img",
            "*.example.org",
            "example.org?v=1",
            "example.org#top",
        ):
            with self.subTest(host=host):
                self.host_refused(host, _NOT_A_HOSTNAME)

    def test_a_name_outside_the_hostname_alphabet_is_refused(self) -> None:
        # Lowercase letters, digits and hyphens inside a label, labels of 1 to 63 joined
        # by one dot each; `$`-anchored matching would let the trailing newline through.
        for host in (
            "Example.org",
            "EXAMPLE.ORG",
            "ex_ample.org",
            "-example.org",
            "example-.org",
            "example..org",
            ".example.org",
            "example.org.",
            " example.org",
            "example.org\n",
            "ex" + chr(0xE4) + "mple.org",
            "a" * 64 + ".example",
        ):
            with self.subTest(host=host):
                self.host_refused(host, _NOT_A_HOSTNAME)

    def test_an_internationalized_name_is_told_to_write_its_xn_form(self) -> None:
        # Every letter of `bücher` is a lowercase letter to a reader, so a refusal naming
        # only letters would read as satisfied: it says the name is written as `xn--`.
        host = "b" + chr(0xFC) + "cher.example"
        self.host_refused(host, rf"{_NOT_A_HOSTNAME}\b.*\bxn--")

    def test_a_single_label_is_refused(self) -> None:
        # One label would cover every host under a whole top-level domain.
        for host in ("org", "com", "example"):
            with self.subTest(host=host):
                self.host_refused(host, "must hold two labels or more")

    def test_a_name_past_the_longest_hostname_is_refused(self) -> None:
        host = ".".join(["b" * 61] * 4) + ".abc.de"
        self.assertEqual(len(host), 254)
        self.host_refused(host, "must be at most 253 characters")

    def test_an_ip_address_is_refused(self) -> None:
        # A browser reads a host whose last label is a number, decimal or `0x` hex, as an
        # IPv4 address: `1.2.3` and `0x7f.1` are addresses to it, never names.
        for host in (
            "192.0.2.1",
            "203.0.113.9",
            "1.2.3",
            "0x7f.1",
            "example.0x",
            "example.08",
            "2001:db8::1",
            "[2001:db8::1]",
        ):
            with self.subTest(host=host):
                self.host_refused(host, "must be a hostname, not an IP address")

    def test_a_loopback_name_is_refused(self) -> None:
        for host in ("localhost", "app.localhost", "a.b.localhost"):
            with self.subTest(host=host):
                self.host_refused(host, "is a loopback name")

    def test_a_host_listed_twice_is_refused_at_the_later_entry(self) -> None:
        listed = ["example.org", "img.example.org", "example.org"]
        pattern = rf"^{re.escape(_HOSTS)}\[2\]: 'example\.org' is already listed"
        with self.assertRaisesRegex(TargetsError, pattern):
            print_targets.parse_targets(hosts_doc(listed))


class ExplicitNoneRefusalTests(_Refusals):
    """An explicit None is a wrong type, never "absent".

    TOML has no null, so only a Python caller -- or the property test -- can send one; a
    key that is present is parsed, whatever it holds.
    """

    def test_a_none_viewport_is_refused(self) -> None:
        self.refused(viewport_doc(None), "viewport")

    def test_a_none_backgrounds_is_refused(self) -> None:
        self.refused({"backgrounds": None, **doc()}, "backgrounds")

    def test_a_none_state_array_is_refused(self) -> None:
        self.refused(doc(state=None), f"{_T0}.state")

    def test_a_none_print_at_ms_is_refused(self) -> None:
        self.refused(state_doc(print_at_ms=None), f"{_S0}.print_at_ms")

    def test_a_none_for_every_other_key_is_refused(self) -> None:
        for data, where in (
            ({"target": None}, "target"),
            ({"read": None, **doc()}, "read"),
            (hosts_doc(None), _HOSTS),
            (read_doc({**_FIRST_READ, "all": None}), "read[0].all"),
            (doc(read=None), f"{_T0}.read"),
            (doc(storage_reads=None), f"{_T0}.storage_reads"),
            (state_doc(color_scheme=None), f"{_S0}.color_scheme"),
            (state_doc(reduced_motion=None), f"{_S0}.reduced_motion"),
            (state_doc(storage=None), f"{_S0}.storage"),
            (state_doc(actions=None), f"{_S0}.actions"),
            (state_doc(page_width=None), f"{_S0}.page_width"),
        ):
            with self.subTest(where=where):
                self.refused(data, where)


if __name__ == "__main__":
    unittest.main()
