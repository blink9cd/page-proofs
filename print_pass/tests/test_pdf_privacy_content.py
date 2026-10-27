"""Suite: pdf_privacy flags a PDF's content, its link URIs and its text, by where and class.

The page's own content is searched differently from its metadata: a personal site's
words legitimately carry its author's name, so only a machine's marks are sought here.
The home-rooted pattern and `localhost` are pinned here in every location, metadata
included, because their false positives come from a page's own web addresses. The form
cases read real PDFs, written by pypdf into a temporary directory, through `pdf_text`:
text a form draws. The metadata half of the matcher has its own suite, `test_pdf_privacy`.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the
tool's aggregator; the bootstrap below names the tool's private `src/`, and discovery's
top-level directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
import pdf_privacy  # noqa: E402 - the tool's private src/, via the insert above
import pdf_text  # noqa: E402 - as above
from _pdf_privacy_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    FULL_NAME,
    HOME,
    HOME_PATH,
    HOME_ROOT,
    HOSTNAMES,
    LOGIN,
    SOMEONE,
    USERS,
    USERS_PATH,
    flags,
    link,
    one,
)
from _pdf_text_fixtures import Line, TempDirCase, figure_pdf  # noqa: E402 - as above

#: The Windows path separator, which Python's repr of a path doubles.
_BACKSLASH = "\\"


class LinkTests(unittest.TestCase):
    """A link URI is flagged for a `file:` scheme and for a home-rooted path."""

    def test_a_file_link_is_flagged_on_its_page(self) -> None:
        self.assertEqual(
            flags(links=(link(2, "file:///tmp/page.html"),)),
            (pdf_privacy.Flag("link on page 2", pdf_privacy.FILE_URL),),
        )

    def test_the_scheme_is_matched_in_any_case(self) -> None:
        self.assertEqual(
            flags(links=(link(1, "FILE:///tmp/page.html"),)),
            (pdf_privacy.Flag("link on page 1", pdf_privacy.FILE_URL),),
        )

    def test_a_file_link_under_a_home_is_flagged_for_both(self) -> None:
        self.assertEqual(
            flags(links=(link(1, "file://" + HOME + "/site/index.html"),)),
            (
                pdf_privacy.Flag("link on page 1", pdf_privacy.HOME_ROOTED_PATH),
                pdf_privacy.Flag("link on page 1", pdf_privacy.FILE_URL),
            ),
        )

    def test_a_web_link_passes(self) -> None:
        self.assertEqual(flags(links=(link(1, "https://example.com/"),)), ())

    def test_a_link_to_this_machine_by_name_is_flagged(self) -> None:
        uri = "http://" + HOSTNAMES[1] + ".local:8123/"
        self.assertEqual(
            flags(links=(link(1, uri),)),
            one("link on page 1", pdf_privacy.CONTAINS_HOSTNAME),
        )

    def test_a_uri_no_url_parser_accepts_is_still_checked(self) -> None:
        # urllib's urlsplit raises on an unclosed IPv6 bracket; a PDF's link can hold
        # any text at all, so the scheme test must not parse the URI.
        self.assertEqual(
            flags(links=(link(1, "http://[unclosed"), link(2, "file://[unclosed"))),
            (pdf_privacy.Flag("link on page 2", pdf_privacy.FILE_URL),),
        )

    def test_an_internal_link_has_no_uri_to_check(self) -> None:
        self.assertEqual(flags(links=(link(1, None),)), ())

    def test_a_destination_is_judged_as_a_uri_is(self) -> None:
        # A named destination is the PDF maker's own text, and can name a home folder.
        named = pdf_text.Link(4, None, USERS_PATH + "/notes", (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(
            flags(links=(named,)),
            one("link on page 4", pdf_privacy.HOME_ROOTED_PATH),
        )

    def test_several_file_links_on_one_page_are_one_flag(self) -> None:
        links = (link(3, "file:///tmp/a.html"), link(3, "file:///tmp/b.html"))
        self.assertEqual(
            flags(links=links),
            (pdf_privacy.Flag("link on page 3", pdf_privacy.FILE_URL),),
        )


class TextTests(unittest.TestCase):
    """The text layer is searched for file URLs, home paths and the hostname only."""

    def test_a_file_url_in_the_text_is_flagged(self) -> None:
        self.assertEqual(
            flags(text_pages=("printed from FILE:///tmp/page.html",)),
            (pdf_privacy.Flag("text on page 1", pdf_privacy.FILE_URL),),
        )

    def test_the_full_name_and_login_in_the_text_pass(self) -> None:
        # A personal site's own words legitimately carry its author's name.
        self.assertEqual(flags(text_pages=(FULL_NAME + ", " + LOGIN,)), ())

    def test_the_hostname_in_the_text_is_flagged_on_its_page(self) -> None:
        self.assertEqual(
            flags(text_pages=("", "served by " + HOSTNAMES[1])),
            (pdf_privacy.Flag("text on page 2", pdf_privacy.CONTAINS_HOSTNAME),),
        )

    def test_a_home_path_in_the_text_is_flagged_without_the_login(self) -> None:
        self.assertEqual(
            flags(text_pages=("saved to " + HOME + "/notes",)),
            (pdf_privacy.Flag("text on page 1", pdf_privacy.HOME_ROOTED_PATH),),
        )


class FigureTextTests(TempDirCase):
    """Text a form XObject draws is the page's text, searched as the rest of it is."""

    def form_flags(self, *lines: Line) -> tuple[pdf_privacy.Flag, ...]:
        """The flags of a PDF whose only text, `lines`, a form draws, read by pdf_text."""
        pdf = figure_pdf(self.folder / "form.pdf", *lines)
        return flags(text_pages=pdf_text.read_text(pdf).pages)

    def test_a_home_path_drawn_inside_a_form_is_flagged(self) -> None:
        # pdfminer lays a form's glyphs out in a figure, outside every text box.
        self.assertEqual(
            self.form_flags("in-figure " + USERS_PATH + "/x"),
            one("text on page 1", pdf_privacy.HOME_ROOTED_PATH),
        )

    def test_a_path_after_a_form_s_other_words_is_flagged(self) -> None:
        # A figure's bare glyphs once ran together, so the words before a path on the line
        # above, or in the run before it, hid its root or its drive from the matcher.
        drive = "C:" + "\\" + USERS + "\\" + SOMEONE + "\\" + "x"
        for lines in (
            ("Saved in", USERS_PATH + "/x"),
            ("Saved in", drive),
            (("Saved to", HOME_PATH + "/x"),),
        ):
            with self.subTest(lines=lines):
                self.assertEqual(
                    self.form_flags(*lines),
                    one("text on page 1", pdf_privacy.HOME_ROOTED_PATH),
                )

    def test_a_hostname_ending_a_form_s_line_or_run_is_flagged(self) -> None:
        # Run into the next word, the hostname stood inside a longer name.
        built = "built on " + HOSTNAMES[1]
        for lines in (((built, "yesterday"),), (built, "yesterday")):
            with self.subTest(lines=lines):
                self.assertEqual(
                    self.form_flags(*lines),
                    one("text on page 1", pdf_privacy.CONTAINS_HOSTNAME),
                )


class HomeRootedPatternTests(unittest.TestCase):
    """A home-rooted path starts at a root; a web URL's `/home/` folder is not one."""

    def test_a_web_url_with_a_folder_named_like_a_home_root_passes(self) -> None:
        # A drive's mount (`/c`) inside a web address is the site's own folder too.
        for value in (
            "https://example.com/" + HOME_ROOT + "/about",
            "https://example.com/" + USERS + "/list",
            "Docs (https://docs.example.org/en/" + HOME_ROOT + "/start)",
            "https://example.com/c/" + USERS + "/about",
            "example.com/" + HOME_ROOT + "/about",
            "../" + HOME_ROOT + "/" + SOMEONE,
        ):
            with self.subTest(value=value):
                found = flags(
                    info={"Subject": value},
                    xmp=value,
                    links=(link(1, value),),
                    text_pages=(value,),
                )
                self.assertEqual(found, ())

    def test_a_file_url_a_saved_path_and_a_drive_path_are_still_flagged(self) -> None:
        drive = "C:" + "\\" + USERS + "\\" + SOMEONE
        self.assertEqual(
            flags(
                info={"Subject": drive},
                links=(link(1, "file://" + USERS_PATH + "/page.html"),),
                text_pages=("saved to " + HOME_PATH + "/notes",),
            ),
            (
                pdf_privacy.Flag("metadata:Subject", pdf_privacy.HOME_ROOTED_PATH),
                pdf_privacy.Flag("link on page 1", pdf_privacy.HOME_ROOTED_PATH),
                pdf_privacy.Flag("link on page 1", pdf_privacy.FILE_URL),
                pdf_privacy.Flag("text on page 1", pdf_privacy.HOME_ROOTED_PATH),
            ),
        )

    def test_a_drive_path_straight_after_a_word_is_flagged(self) -> None:
        # Only a forward slash's drive may be a `file:` URL's `e:`; a backslash's is a
        # drive whatever comes before it, as when a hostname runs straight into it.
        drive = "C:" + "\\" + USERS + "\\" + SOMEONE
        for text in ("mybox" + drive, HOSTNAMES[1] + drive + "\\" + "x"):
            with self.subTest(text=text):
                self.assertEqual(
                    flags(text_pages=(text,)),
                    one("text on page 1", pdf_privacy.HOME_ROOTED_PATH),
                )

    def test_a_drive_path_with_forward_slashes_is_still_flagged(self) -> None:
        # The shape a Windows file: URL carries.
        drive = "C:/" + USERS + "/" + SOMEONE
        self.assertEqual(
            flags(
                links=(link(1, "file:///" + drive + "/page.html"),),
                text_pages=(drive,),
            ),
            (
                pdf_privacy.Flag("link on page 1", pdf_privacy.HOME_ROOTED_PATH),
                pdf_privacy.Flag("link on page 1", pdf_privacy.FILE_URL),
                pdf_privacy.Flag("text on page 1", pdf_privacy.HOME_ROOTED_PATH),
            ),
        )

    def test_every_shape_of_another_account_s_home_is_flagged(self) -> None:
        # A Mac's and a Windows file system ignore case; WSL mounts a drive under
        # /mnt/<letter>, Git Bash under /<letter> and Cygwin under /cygdrive/<letter>,
        # and macOS shows its homes under the data volume, and another disk's under
        # /Volumes/<disk>. A `file:` URL with one slash puts a colon before the root, where
        # no drive letter stands, and Python quotes a drive path's backslashes doubled.
        doubled = _BACKSLASH * 2
        for path in (
            "c:" + "\\" + USERS.lower() + "\\" + SOMEONE + "\\" + "doc.docx",
            "/" + USERS.lower() + "/" + SOMEONE + "/notes",
            "/" + HOME_ROOT.upper() + "/" + SOMEONE + "/notes",
            "/mnt/c" + USERS_PATH + "/doc.pdf",
            "/MNT/C/" + USERS.lower() + "/" + SOMEONE,
            "/System/Volumes/Data" + USERS_PATH + "/x",
            "file:" + USERS_PATH + "/x",
            "/c" + USERS_PATH + "/x",
            "/D/" + USERS.upper() + "/" + SOMEONE,
            "/cygdrive/c" + USERS_PATH + "/x",
            "/CYGDRIVE/D/" + USERS.lower() + "/" + SOMEONE,
            "/Volumes/Backup" + USERS_PATH + "/x",
            "/volumes/backup disk/" + USERS.lower() + "/" + SOMEONE,
            "C:" + doubled + USERS + doubled + SOMEONE + doubled + "x",
            "c:" + doubled + USERS.upper() + doubled + SOMEONE,
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    flags(text_pages=("saved to " + path,)),
                    one("text on page 1", pdf_privacy.HOME_ROOTED_PATH),
                )

    def test_a_relative_path_or_a_web_folder_in_another_case_passes(self) -> None:
        # `../home/<name>` is relative to wherever it is read from: no home, on purpose.
        for value in (
            "../" + HOME_ROOT + "/" + SOMEONE + "/notes",
            "https://example.com/" + HOME_ROOT.upper() + "/about",
            "https://example.com/mnt/c" + USERS_PATH + "/list",
            "https://example.com/System/Volumes/Data" + USERS_PATH,
            "https://example.com/C/" + USERS.lower() + "/about",
            "https://example.com/cygdrive/c" + USERS_PATH,
            "https://example.com/Volumes/Backup" + USERS_PATH,
            "notes/c" + USERS_PATH + "/x",
        ):
            with self.subTest(value=value):
                self.assertEqual(flags(text_pages=(value,)), ())


class HostnameTests(unittest.TestCase):
    """In text and links a hostname is a whole name; `localhost` is never this machine."""

    def test_a_hostname_inside_a_longer_name_passes_in_text_and_links(self) -> None:
        for value in (
            "the " + HOSTNAMES[1] + "ess",
            "https://www.my" + HOSTNAMES[1] + ".example.org/",
            HOSTNAMES[1] + "-2 and " + HOSTNAMES[1] + "_old",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    flags(links=(link(1, value),), text_pages=(value,)), ()
                )

    def test_a_hostname_between_punctuation_is_flagged_in_any_case(self) -> None:
        self.assertEqual(
            flags(text_pages=("(served by " + HOSTNAMES[0].upper() + ")",)),
            one("text on page 1", pdf_privacy.CONTAINS_HOSTNAME),
        )

    def test_localhost_is_never_sought_anywhere(self) -> None:
        # The tool serves every page over loopback, so that address names no machine.
        identity = pdf_privacy.Identity(LOGIN, None, ("LocalHost", "localhost"), HOME)
        page = "http://localhost:8123/index.html"
        found = flags(
            identity,
            info={"Subject": page},
            xmp=page,
            links=(link(1, page),),
            text_pages=(page,),
        )
        self.assertEqual(found, ())

    def test_a_real_hostname_beside_localhost_is_still_sought(self) -> None:
        identity = pdf_privacy.Identity(LOGIN, None, ("localhost", HOSTNAMES[1]), HOME)
        self.assertEqual(
            flags(identity, text_pages=("served by " + HOSTNAMES[1],)),
            one("text on page 1", pdf_privacy.CONTAINS_HOSTNAME),
        )


if __name__ == "__main__":  # pragma: no cover - the discovery runner is the entry point
    unittest.main()
