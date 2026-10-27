"""Suite: pdf_privacy flags a PDF's metadata by where and class, never value, and reads the host.

Every case injects the shared fixture `Identity` of obviously fake values, so no test
reads this machine; `host_identity` itself is exercised with every host read patched.
The page's content, its links and its text, has its own suite,
`test_pdf_privacy_content`. The whole-matcher property lives here.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the
tool's aggregator; the bootstrap below names the tool's private `src/`, and discovery's
top-level directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import types
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Final
from unittest import mock

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
import pdf_privacy  # noqa: E402 - the tool's private src/, via the insert above
from _pdf_privacy_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    CLASSES,
    FULL_NAME,
    HOME,
    HOME_PATH,
    HOME_ROOT,
    HOSTNAMES,
    IDENTITY,
    LOGIN,
    USERS,
    flags,
    link,
    one,
)

# Derandomized so the in-suite run is reproducible; no deadline, because a per-example
# wall clock measures machine load rather than correctness.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

_PERSON: Final = pdf_privacy.Flag("metadata:Author", pdf_privacy.NAMES_A_PERSON)

#: Arbitrary text, often wrapped around something the matcher hunts, so the property
#: reaches the flagging paths rather than only the quiet ones.
_VALUES: Final = st.builds(
    lambda before, hunted, after: before + hunted + after,
    st.text(),
    st.sampled_from((LOGIN, FULL_NAME, *HOSTNAMES, HOME, "file://", "")),
    st.text(),
)

#: Info field names: the ones with rules of their own, and names wrapped around what
#: the matcher hunts, since a PDF's maker chooses its field names as freely as values.
_FIELDS: Final = st.one_of(st.sampled_from(("Title", "Author", "Subject")), _VALUES)

#: The `where` of an Info field whose NAME is flagged: the name is never repeated.
_FLAGGED_FIELD: Final = "metadata:(a field whose name is flagged)"


class VocabularyTests(unittest.TestCase):
    """The class wording is a contract with whoever reads a report."""

    def test_the_classes_are_worded_as_documented(self) -> None:
        self.assertEqual(
            (
                pdf_privacy.NAMES_A_PERSON,
                pdf_privacy.CONTAINS_LOGIN,
                pdf_privacy.CONTAINS_FULL_NAME,
                pdf_privacy.CONTAINS_HOSTNAME,
                pdf_privacy.HOME_ROOTED_PATH,
                pdf_privacy.FILE_URL,
            ),
            CLASSES,
        )


class AuthorTests(unittest.TestCase):
    """An `Author` names a person unless it is a generator's placeholder."""

    def test_an_author_names_a_person(self) -> None:
        self.assertEqual(flags(info={"Author": "Somebody Else"}), (_PERSON,))

    def test_a_known_generator_placeholder_passes_in_any_case(self) -> None:
        for author in ("anonymous", "Unknown", " ANONYMOUS "):
            with self.subTest(author=author):
                self.assertEqual(flags(info={"Author": author}), ())

    def test_an_empty_author_passes(self) -> None:
        for author in ("", "   "):
            with self.subTest(author=author):
                self.assertEqual(flags(info={"Author": author}), ())

    def test_the_account_s_own_name_as_author_is_flagged_twice(self) -> None:
        self.assertEqual(
            flags(info={"Author": FULL_NAME}),
            (
                _PERSON,
                pdf_privacy.Flag("metadata:Author", pdf_privacy.CONTAINS_FULL_NAME),
            ),
        )


class InfoTests(unittest.TestCase):
    """Every Info field is searched for each identifier and for home-rooted paths."""

    def test_the_login_inside_creator_is_flagged_case_insensitively(self) -> None:
        self.assertEqual(
            flags(info={"Creator": "built by " + LOGIN.upper() + "'s script"}),
            (pdf_privacy.Flag("metadata:Creator", pdf_privacy.CONTAINS_LOGIN),),
        )

    def test_the_home_path_in_a_field_is_flagged_with_the_login_inside_it(self) -> None:
        self.assertEqual(
            flags(info={"Subject": HOME + "/site/index.html"}),
            (
                pdf_privacy.Flag("metadata:Subject", pdf_privacy.CONTAINS_LOGIN),
                pdf_privacy.Flag("metadata:Subject", pdf_privacy.HOME_ROOTED_PATH),
            ),
        )

    def test_a_hostname_is_flagged_once_though_both_forms_match(self) -> None:
        self.assertEqual(
            flags(info={"Producer": "Fixture-Host.Example"}),
            (pdf_privacy.Flag("metadata:Producer", pdf_privacy.CONTAINS_HOSTNAME),),
        )

    def test_another_account_s_home_is_still_a_home_rooted_path(self) -> None:
        for path in (
            "/" + HOME_ROOT + "/someone/notes.txt",
            "C:" + "\\" + USERS + "\\" + "someone" + "\\" + "notes.txt",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    flags(info={"Subject": path}),
                    (
                        pdf_privacy.Flag(
                            "metadata:Subject", pdf_privacy.HOME_ROOTED_PATH
                        ),
                    ),
                )

    def test_the_home_string_counts_even_where_the_pattern_would_not(self) -> None:
        identity = pdf_privacy.Identity(LOGIN, None, (), "/var/lib/fixture-account")
        self.assertEqual(
            flags(identity, info={"Keywords": "/var/lib/fixture-account/cache"}),
            (pdf_privacy.Flag("metadata:Keywords", pdf_privacy.HOME_ROOTED_PATH),),
        )

    def test_an_ordinary_browser_info_dictionary_passes(self) -> None:
        info = {"Title": "A page", "Creator": "Chromium", "Producer": "Skia/PDF m153"}
        self.assertEqual(flags(info=info), ())

    def test_no_full_name_means_none_is_sought(self) -> None:
        identity = pdf_privacy.Identity(LOGIN, None, HOSTNAMES, HOME)
        self.assertEqual(flags(identity, info={"Subject": "None"}), ())

    def test_every_field_but_title_is_searched_for_names(self) -> None:
        for field in ("Subject", "Keywords", "Creator", "Producer"):
            with self.subTest(field=field):
                where = f"metadata:{field}"
                self.assertEqual(
                    flags(info={field: FULL_NAME + " / " + LOGIN}),
                    (
                        pdf_privacy.Flag(where, pdf_privacy.CONTAINS_LOGIN),
                        pdf_privacy.Flag(where, pdf_privacy.CONTAINS_FULL_NAME),
                    ),
                )


class FieldNameTests(unittest.TestCase):
    """A field's NAME is searched too, and a flagged name is never repeated in `where`."""

    def test_a_field_named_with_an_identifier_is_flagged_without_its_name(self) -> None:
        for field, what in (
            (LOGIN, pdf_privacy.CONTAINS_LOGIN),
            ("Printed-on-" + HOSTNAMES[1], pdf_privacy.CONTAINS_HOSTNAME),
            (HOME_PATH, pdf_privacy.HOME_ROOTED_PATH),
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    flags(info={field: "Chromium"}), one(_FLAGGED_FIELD, what)
                )

    def test_a_flagged_field_name_still_has_its_value_searched(self) -> None:
        self.assertEqual(
            flags(info={"by " + LOGIN: "on " + HOSTNAMES[0]}),
            (
                pdf_privacy.Flag(_FLAGGED_FIELD, pdf_privacy.CONTAINS_LOGIN),
                pdf_privacy.Flag(_FLAGGED_FIELD, pdf_privacy.CONTAINS_HOSTNAME),
            ),
        )


class TitleTests(unittest.TestCase):
    """`Title` is the page's own <title>: names pass there, the machine's marks do not."""

    def test_a_title_carrying_the_full_name_or_the_login_passes(self) -> None:
        # The browser copies a page's <title> here, and a personal site's title carries
        # its author's name, for the same reason the text layer is not searched for it.
        for title in (FULL_NAME + " | Home", "Notes by " + LOGIN):
            with self.subTest(title=title):
                self.assertEqual(flags(info={"Title": title}), ())

    def test_a_title_carrying_the_hostname_or_a_home_path_is_flagged(self) -> None:
        for title, what in (
            ("Served by " + HOSTNAMES[1], pdf_privacy.CONTAINS_HOSTNAME),
            (HOME + "/site/index.html", pdf_privacy.HOME_ROOTED_PATH),
        ):
            with self.subTest(title=title):
                self.assertEqual(
                    flags(info={"Title": title}), one("metadata:Title", what)
                )


class EmptyIdentifierTests(unittest.TestCase):
    """An identifier with nothing in it would match every value, so it is never sought."""

    def test_an_empty_login_never_matches(self) -> None:
        identity = pdf_privacy.Identity("", "", ("",), "")
        self.assertEqual(
            flags(
                identity,
                info={"Creator": "anything at all"},
                xmp="anything at all",
                text_pages=("anything at all",),
            ),
            (),
        )

    def test_a_root_or_relative_home_never_matches(self) -> None:
        for home in ("/", ".", "  "):
            with self.subTest(home=home):
                identity = pdf_privacy.Identity(LOGIN, None, (), home)
                self.assertEqual(flags(identity, info={"Title": "a/b. c"}), ())


class XmpTests(unittest.TestCase):
    """The XMP packet gets the same identifier and path checks as an Info field."""

    def test_the_hostname_in_xmp_is_flagged(self) -> None:
        packet = "<xmp:CreatorTool>" + HOSTNAMES[0] + "</xmp:CreatorTool>"
        self.assertEqual(
            flags(xmp=packet),
            (pdf_privacy.Flag("xmp", pdf_privacy.CONTAINS_HOSTNAME),),
        )

    def test_the_login_and_full_name_in_xmp_are_flagged(self) -> None:
        packet = "<dc:creator>" + FULL_NAME + " (" + LOGIN + ")</dc:creator>"
        self.assertEqual(
            flags(xmp=packet),
            (
                pdf_privacy.Flag("xmp", pdf_privacy.CONTAINS_LOGIN),
                pdf_privacy.Flag("xmp", pdf_privacy.CONTAINS_FULL_NAME),
            ),
        )

    def test_an_absent_packet_passes(self) -> None:
        self.assertEqual(flags(xmp=None), ())


class OrderTests(unittest.TestCase):
    """Flags come in a fixed order: Info, then XMP, then links, then text."""

    def test_locations_are_reported_in_order(self) -> None:
        found = flags(
            info={"Author": "Somebody Else"},
            xmp=HOSTNAMES[1],
            links=(link(1, "file:///tmp/a.html"),),
            text_pages=("file:///tmp/a.html",),
        )
        self.assertEqual(
            [flag.where for flag in found],
            ["metadata:Author", "xmp", "link on page 1", "text on page 1"],
        )

    @given(_FIELDS, _VALUES)
    def test_fuzz_privacy_flags(self, field: str, value: str) -> None:
        # Whatever the field name and value, a flag says only where and what: its where
        # is one of the locations it was given, its what is one of the documented
        # classes, and neither carries any identifier the matcher was hunting.
        found = flags(
            info={field: value},
            xmp=value,
            links=(link(1, value),),
            text_pages=(value,),
        )
        wheres = {
            f"metadata:{field}",
            _FLAGGED_FIELD,
            "xmp",
            "link on page 1",
            "text on page 1",
        }
        for flag in found:
            self.assertIn(flag.where, wheres)
            self.assertIn(flag.what, CLASSES)
            said = (flag.where + " " + flag.what).casefold()
            for secret in (LOGIN, FULL_NAME, *HOSTNAMES, HOME):
                self.assertNotIn(secret.casefold(), said)


class HostIdentityTests(unittest.TestCase):
    """The identity is read from the host through the standard library, patched here."""

    @contextlib.contextmanager
    def _host(
        self,
        *,
        gecos: str | None = "Fixture Person,Room 1,,",
        hostname: str = "Fixture-Host.Example",
    ) -> Iterator[None]:
        """Patch every host read; a `gecos` of None means the account has no passwd entry."""

        def getpwuid(uid: int) -> types.SimpleNamespace:
            if gecos is None:
                raise KeyError(f"getpwuid(): uid not found: {uid}")
            return types.SimpleNamespace(pw_gecos=gecos)

        # The passwd module is patched where it lives: only the POSIX arm imports it.
        with (
            mock.patch.object(pdf_privacy.getpass, "getuser", return_value=LOGIN),
            mock.patch("pwd.getpwuid", side_effect=getpwuid),
            mock.patch.object(pdf_privacy.socket, "gethostname", return_value=hostname),
            mock.patch.object(pdf_privacy.Path, "home", return_value=Path(HOME)),
        ):
            yield

    def test_every_identifier_is_read_and_normalized(self) -> None:
        with self._host():
            identity = pdf_privacy.host_identity()
        self.assertEqual(identity, IDENTITY)

    def test_an_empty_full_name_is_none(self) -> None:
        for gecos in ("", ",,,", "  ,Room 1"):
            with self.subTest(gecos=gecos), self._host(gecos=gecos):
                self.assertIsNone(pdf_privacy.host_identity().full_name)

    def test_an_account_with_no_passwd_entry_has_no_full_name(self) -> None:
        with self._host(gecos=None):
            self.assertIsNone(pdf_privacy.host_identity().full_name)

    def test_a_hostname_with_no_domain_is_listed_once(self) -> None:
        with self._host(hostname="Fixture-Host"):
            self.assertEqual(pdf_privacy.host_identity().hostnames, ("fixture-host",))

    def test_an_empty_hostname_lists_nothing(self) -> None:
        with self._host(hostname=""):
            self.assertEqual(pdf_privacy.host_identity().hostnames, ())

    def test_the_module_loads_where_there_is_no_passwd_module(self) -> None:
        # Windows has no passwd database and no `pwd` module; a None in `sys.modules`
        # makes importing it raise as it does there. A fresh copy of the module must
        # still load, and its Windows arm seek no full name.
        name = "pdf_privacy_without_pwd"
        spec = importlib.util.spec_from_file_location(name, pdf_privacy.__file__)
        if spec is None or spec.loader is None:
            self.fail(f"no loader for {pdf_privacy.__file__}")
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"pwd": None, name: module}):
            spec.loader.exec_module(module)
            windows = types.SimpleNamespace(platform="win32")
            with mock.patch.object(module, "sys", windows):
                self.assertIsNone(module._full_name())


if __name__ == "__main__":  # pragma: no cover - the discovery runner is the entry point
    unittest.main()
