"""Suite: the scrub every text the report carries passes through.

The scrub is checked case by case and by a property: whatever text a library or a page hands
it, no home-rooted path survives, no home directory in any case unless more of a name follows
it, and no hostname the privacy matcher would find standing alone. A make report's actions
and stored values, which a targets file names, pass through it too. The report's paths and
comparing two PDFs are the sibling `test_print_report_pdf_compare`'s.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the tool's
aggregator; the bootstrap below names the tool's private `src/`, and discovery's top-level
directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import dataclasses
import re
import string
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Final, cast

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))

import pdf_privacy  # noqa: E402 - the tool's private src/, via the insert above
import print_report  # noqa: E402 - as above
import print_report_scrub  # noqa: E402 - as above
from _print_report_fixtures import read, state_record, targets  # noqa: E402 - beside it
from print_actions import Action  # noqa: E402 - src/
from _pdf_privacy_fixtures import (  # noqa: E402 - beside this module
    HOME,
    HOME_PATH,
    HOME_ROOT,
    HOSTNAMES,
    IDENTITY,
    LOGIN,
    SOMEONE,
    USERS,
    USERS_PATH,
)

# Derandomized so the in-suite run is reproducible; no deadline, because a per-example wall
# clock measures machine load rather than correctness.
settings.register_profile(
    "fuzz_gate_deterministic",
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("fuzz_gate_deterministic")

#: An out dir that is not under the fixture home, spelled as the scrub would meet it.
_OUT: Final = Path("/" + "srv" + "/" + "print-out")

#: Where the home's last name goes no further: no word character or hyphen follows it, a
#: dot being no part of it. Stricter than the scrub's path end, because the home past a dot
#: that no home-rooted path claims is this account's too. Restated, not imported, so the
#: property states the contract rather than borrowing the scrub's pattern.
_HOME_ENDS: Final = r"(?![\w-])"

#: A backup's folder, no mount the home-rooted pattern knows: a home copied under it
#: follows a word character, as in a web folder.
_BACKUP: Final = "/" + "Backups" + "/" + "Data"

#: Another account's home, in the drive form: it opens with a letter, not a slash.
_DRIVE_HOME: Final = "C:" + "\\" + USERS + "\\" + SOMEONE

#: The same, as Python quotes it: each backslash doubled.
_DOUBLED_DRIVE_HOME: Final = _DRIVE_HOME.replace("\\", "\\\\")


def _identity(
    *, home: str = HOME, hostnames: tuple[str, ...] = HOSTNAMES
) -> pdf_privacy.Identity:
    """The fixture identity, or one with another home or hostnames."""
    return dataclasses.replace(IDENTITY, home=home, hostnames=hostnames)


def _scrub(
    text: str,
    *,
    out_dir: Path = _OUT,
    home: str = HOME,
    hostnames: tuple[str, ...] = HOSTNAMES,
) -> str:
    """`text` scrubbed for the fixture identity, or one with another home or hostnames."""
    identity = _identity(home=home, hostnames=hostnames)
    return print_report_scrub.scrub(text, out_dir=out_dir, identity=identity)


def _matcher_finds_hostname(scrubbed: str) -> bool:
    """Whether the privacy matcher, reading `scrubbed` as a page's text, finds a hostname."""
    found = pdf_privacy.privacy_flags(
        IDENTITY, info={}, xmp=None, links=(), text_pages=(scrubbed,)
    )
    return pdf_privacy.CONTAINS_HOSTNAME in {flag.what for flag in found}


def _tokens(identity: pdf_privacy.Identity) -> list[str]:
    """The token each of the scrub's passes writes for `identity`, in pass order.

    Asked of the passes rather than of `scrub`, so an identity that would keep the scrub
    rewriting forever fails its test instead of hanging it.
    """
    return [token for _, token in print_report_scrub._scrub_passes(_OUT, identity)]


class ScrubTests(unittest.TestCase):
    def test_the_out_dir_reads_as_out(self) -> None:
        self.assertEqual(
            _scrub(f"cannot open {_OUT}/pdfs/a.pdf"), "cannot open <out>/pdfs/a.pdf"
        )

    def test_the_out_dir_is_matched_resolved_as_well_as_given(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            linked = Path(folder) / "link"
            real = Path(folder) / "real"
            real.mkdir()
            linked.symlink_to(real)
            text = f"{real.resolve()}/a.pdf and {linked}/b.pdf"
            self.assertEqual(
                _scrub(text, out_dir=linked), "<out>/a.pdf and <out>/b.pdf"
            )

    def test_a_longer_name_sharing_the_out_dirs_prefix_is_not_the_out_dir(self) -> None:
        self.assertEqual(_scrub(f"{_OUT}put/a.pdf"), f"{_OUT}put/a.pdf")

    def test_a_relative_out_dir_never_replaces_a_bare_word(self) -> None:
        self.assertEqual(
            _scrub("pdftoppm timed out", out_dir=Path("out")), "pdftoppm timed out"
        )

    def test_this_home_reads_as_a_tilde_in_any_case(self) -> None:
        self.assertEqual(
            _scrub(f"{HOME}/a.pdf and {HOME.upper()}/b.pdf"), "~/a.pdf and ~/b.pdf"
        )

    def test_an_account_whose_name_extends_the_homes_is_another_home(self) -> None:
        # Matched as a mere prefix, the home would leave the other account's `my` behind.
        home = "/" + USERS + "/" + "sam"
        other = "/" + USERS + "/" + "sammy"
        self.assertEqual(
            _scrub(f"{other}/x and {home}/y", home=home), "<home>/x and ~/y"
        )

    def test_this_home_ending_a_sentence_reads_as_a_tilde_after_any_character(
        self,
    ) -> None:
        # A word character before the home refuses the home-rooted pass (a web folder's
        # shape), so the full stop after it must end the name for this pass to take it.
        for before in ("a", "https://example.com", _BACKUP):
            with self.subTest(before=before):
                self.assertEqual(
                    _scrub(f"saved to {before}{HOME}."), f"saved to {before}~."
                )

    def test_an_account_whose_name_continues_past_a_dot_is_another_home(self) -> None:
        # A word character after the dot makes it part of the name: `sam.bak` is not `sam`.
        home = "/" + USERS + "/" + "sam"
        self.assertEqual(_scrub(f"{home}.bak/x", home=home), "<home>/x")

    def test_this_home_in_no_shape_the_home_rooted_pattern_knows_reads_as_a_tilde(
        self,
    ) -> None:
        # A root after a host's last letter is no shape the home-rooted pattern labels,
        # so the home past a dot there is this account's.
        self.assertEqual(
            _scrub(f"https://example.com{HOME}.html"), "https://example.com~.html"
        )

    def test_this_home_past_a_dot_under_a_root_in_another_case_reads_as_home(
        self,
    ) -> None:
        # A root in any case is a home's shape, so the name past the dot is another's.
        lower = "/" + USERS.lower() + "/" + LOGIN
        for text in (f"saved to {lower}.log", f"saved to {HOME.upper()}.0"):
            with self.subTest(text=text):
                self.assertEqual(_scrub(text), "saved to <home>")

    def test_every_shape_of_another_account_s_home_reads_as_home(self) -> None:
        # In any case; under WSL's, Git Bash's or Cygwin's drive mounts, the macOS data
        # volume or another disk's; after the colon of a one-slash `file:` URL, whose `e:`
        # is no drive letter; a backslash's drive straight after a word, which is one; and
        # a drive path as Python quotes it, each backslash doubled.
        drive = "c:" + "\\" + USERS.lower() + "\\" + SOMEONE
        cases = {
            drive + "\\doc.docx": "<home>\\doc.docx",
            "mybox" + drive + "\\x": "mybox<home>\\x",
            "/" + USERS.lower() + "/" + SOMEONE + "/x": "<home>/x",
            "/mnt/c" + USERS_PATH + "/doc.pdf": "<home>/doc.pdf",
            "/System/Volumes/Data" + USERS_PATH + "/x": "<home>/x",
            "file:" + USERS_PATH + "/x": "file:<home>/x",
            "/C" + USERS_PATH + "/x": "<home>/x",
            "/cygdrive/c" + USERS_PATH + "/x": "<home>/x",
            "/Volumes/Backup Disk" + USERS_PATH + "/x": "<home>/x",
            _DOUBLED_DRIVE_HOME + "\\\\x": "<home>\\\\x",
        }
        for text, scrubbed in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_scrub(text), scrubbed)

    def test_a_bare_volume_keeps_the_text_after_it(self) -> None:
        # A volume's name ends at its own space-separated words: the text past it, and a
        # home further along, are read on their own, never swallowed into the volume.
        after = USERS_PATH + "/x"
        cases = {
            "copied /Volumes/Backup and "
            + after: "copied /Volumes/Backup and <home>/x",
            "copied /Volumes/Backup\n" + after: "copied /Volumes/Backup\n<home>/x",
        }
        for text, scrubbed in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_scrub(text), scrubbed)

    def test_a_relative_path_or_a_web_folder_is_no_other_home(self) -> None:
        for text in (
            "../" + HOME_PATH.removeprefix("/") + "/x",
            "https://example.com" + HOME_PATH + "/about",
            "https://example.com/mnt/c" + USERS_PATH + "/list",
            "https://example.com/c/" + USERS + "/about",
            "https://example.com/cygdrive/c" + USERS_PATH,
            "https://example.com/Volumes/Backup" + USERS_PATH,
            "example.com/" + HOME_ROOT + "/about",
        ):
            with self.subTest(text=text):
                self.assertEqual(_scrub(text), text)

    def test_an_account_whose_name_extends_the_homes_is_left_whole_in_a_web_folder(
        self,
    ) -> None:
        # No shape the home-rooted pattern knows, and no tilde either: `~my/x` would be
        # a partial account name no pass recognizes.
        home = "/" + USERS + "/" + "sam"
        text = "https://example.com/" + USERS + "/" + "sammy/x"
        self.assertEqual(_scrub(text, home=home), text)

    def test_the_out_dir_ending_a_sentence_reads_as_out(self) -> None:
        self.assertEqual(_scrub(f"wrote {_OUT}."), "wrote <out>.")

    def test_an_out_dir_under_home_reads_as_out(self) -> None:
        out = Path(HOME) / "print-out"
        self.assertEqual(_scrub(f"{out}/a.pdf", out_dir=out), "<out>/a.pdf")

    def test_any_other_home_rooted_path_reads_as_home(self) -> None:
        self.assertEqual(
            _scrub(f"{USERS_PATH}/a {HOME_PATH}/b {_DRIVE_HOME}\\c"),
            "<home>/a <home>/b <home>\\c",
        )

    def test_a_replacement_that_forms_a_new_home_root_is_replaced_too(self) -> None:
        # One pass leaves `/Users/<home>`, itself shaped like a home, so the scrub repeats.
        self.assertEqual(_scrub("/" + USERS + "/" + USERS_PATH), "<home>")

    def test_a_hollow_home_or_out_dir_is_never_sought(self) -> None:
        self.assertEqual(_scrub("a/b", out_dir=Path("/"), home="/"), "a/b")

    def test_a_relative_home_is_never_sought(self) -> None:
        # A relative `$HOME` may hold no path separator for a replacement to remove, so its
        # tilde and a hostname's token could keep spelling each other forever.
        for home in ("name", "work/" + LOGIN, "C:" + LOGIN):
            with self.subTest(home=home):
                identity = _identity(home=home, hostnames=("host",))
                self.assertNotIn(print_report_scrub.HOME_TOKEN, _tokens(identity))

    def test_an_absolute_home_is_sought_in_either_form(self) -> None:
        drive = "C:" + "\\" + USERS + "\\" + LOGIN
        for home in (HOME, drive, drive.replace("\\", "/")):
            with self.subTest(home=home):
                tokens = _tokens(_identity(home=home))
                self.assertEqual(tokens.count(print_report_scrub.HOME_TOKEN), 2)
        self.assertEqual(_scrub(f"{drive}\\a.pdf", home=drive), "~\\a.pdf")

    def test_text_with_nothing_to_hide_is_unchanged(self) -> None:
        self.assertEqual(_scrub("pdfinfo exited 1: bad"), "pdfinfo exited 1: bad")

    def test_a_hostname_standing_alone_reads_as_hostname(self) -> None:
        # A blocked request to this machine's own name, as the engine's finding quotes it.
        for hostname in (*HOSTNAMES, HOSTNAMES[0].upper()):
            with self.subTest(hostname=hostname):
                self.assertEqual(
                    _scrub(f"blocked off-loopback request: http://{hostname}:8123/x"),
                    "blocked off-loopback request: http://<hostname>:8123/x",
                )

    def test_a_hostname_inside_a_longer_name_is_left_alone(self) -> None:
        # As the privacy matcher reads a page's text: `mac` is never `machine` or `mac-mini`.
        short = HOSTNAMES[1]
        text = f"{short}s, my-{short} and {short}_2"
        self.assertEqual(_scrub(text), text)

    def test_no_character_beside_a_hostname_leaves_it_for_the_matcher(self) -> None:
        # The scrub takes the matcher's name character and sought hostnames, so the two
        # agree by construction; this checks it in effect, asking the matcher itself of
        # every printable ASCII character on either side of each hostname.
        for hostname in HOSTNAMES:
            for character in string.printable:
                for text in (character + hostname, hostname + character):
                    with self.subTest(text=text):
                        self.assertFalse(_matcher_finds_hostname(_scrub(text)))

    def test_localhost_and_a_hollow_hostname_are_never_sought(self) -> None:
        # A hollow name sought would match everywhere, so the scrub would never finish.
        identity = _identity(hostnames=("localhost", "LocalHost", "", " ", "/."))
        self.assertEqual(print_report_scrub._sought_hostnames(identity), ())
        self.assertNotIn(print_report_scrub.HOSTNAME_TOKEN, _tokens(identity))

    def test_a_machine_named_as_the_token_is_never_sought(self) -> None:
        # Each replacement would spell the name again, so the scrub would never finish.
        identity = _identity(hostnames=("HostName",))
        self.assertEqual(print_report_scrub._sought_hostnames(identity), ())
        self.assertNotIn(print_report_scrub.HOSTNAME_TOKEN, _tokens(identity))

    def test_each_pass_that_uncovers_anothers_match_is_repeated(self) -> None:
        short = HOSTNAMES[1]
        # A home that is no home-rooted shape.
        home = "/var/lib/" + LOGIN
        cases = (
            # The hostname hid the web folder after it; named, the folder is a home's shape.
            (f"http://{short}{USERS_PATH}/x", "http://<hostname><home>/x", HOSTNAMES),
            # A drive letter hid the hostname's end; replaced, the hostname stands alone.
            (f"{short}{_DRIVE_HOME}", "<hostname><home>", HOSTNAMES),
            # The hostname after a dot continued the home's name; replaced, the home ends.
            (f"a{HOME}.{short}", "a~.<hostname>", HOSTNAMES),
        )
        for text, scrubbed, hostnames in cases:
            with self.subTest(text=text):
                self.assertEqual(_scrub(text, hostnames=hostnames), scrubbed)
        # The home hid a root before it, the last pass took it past its dot, and the root
        # and its tilde are a home's shape.
        text = "/" + USERS + "/" + home + ".0"
        self.assertEqual(_scrub(text, home=home), "<home>")

    @given(
        st.lists(
            st.one_of(
                st.text(),
                st.sampled_from(
                    (
                        HOME,
                        HOME.lower(),
                        HOME.upper(),
                        USERS_PATH,
                        HOME_PATH,
                        str(_OUT),
                        "/" + USERS + "/",
                        _DRIVE_HOME,
                        _DOUBLED_DRIVE_HOME,
                        "/c" + USERS_PATH,
                        LOGIN,
                        *HOSTNAMES,
                        HOSTNAMES[1].upper(),
                        ".",
                    )
                ),
            ),
            max_size=8,
        )
    )
    def test_fuzz_scrub(self, pieces: list[str]) -> None:
        # The home followed by more of a name is another account's, and survives only where
        # no home-rooted path is shaped either: after one of the characters the home-rooted
        # pattern refuses before a root slash (`[\w.%-]`, a host's, port's or folder's last
        # character), as in a web folder. A root in another case is a home's shape too, so
        # followed by anything else, in any case and under a root in any case, it never
        # survives.
        scrubbed = _scrub("".join(pieces))
        self.assertIsNone(pdf_privacy.HOME_ROOTED.search(scrubbed))
        home = re.escape(HOME) + _HOME_ENDS
        self.assertIsNone(re.search(home, scrubbed, re.IGNORECASE))
        # A hostname is judged by the privacy matcher itself, reading the text as a page's.
        self.assertFalse(_matcher_finds_hostname(scrubbed), scrubbed)


class StateScrubTests(unittest.TestCase):
    """What a state set, as the make report writes it, passes through the scrub too."""

    def test_the_actions_and_storage_a_state_set_are_scrubbed(self) -> None:
        # A targets file may name a home path in a selector, a value or a stored value.
        actions = (
            Action("click", f"[href='{HOME}/a']", None),
            Action("set_attribute", ":root", None, ("data-x", f"{USERS_PATH}/b")),
        )
        storage = (("last", f"{HOME}/c.pdf"), ("mode", "dark"))
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            record = dataclasses.replace(
                state_record(out), actions=actions, storage=storage
            )
            report = print_report.make_report(
                [record],
                {},
                [],
                out_dir=out,
                targets=targets(read()),
                engine="shell",
                header_footer=False,
                identity=IDENTITY,
            )
        (written,) = cast(list[dict[str, object]], report["states"])
        self.assertEqual(
            written["actions"],
            ["click [href='~/a']", 'set_attribute :root data-x="<home>/b"'],
        )
        self.assertEqual(written["storage"], {"last": "~/c.pdf", "mode": "dark"})

    def test_a_storage_key_holding_a_home_path_is_scrubbed(self) -> None:
        # A key is named by the targets file just as its value is.
        storage = ((f"{HOME}/key", "dark"),)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            record = dataclasses.replace(state_record(out), storage=storage)
            report = print_report.make_report(
                [record],
                {},
                [],
                out_dir=out,
                targets=targets(read()),
                engine="shell",
                header_footer=False,
                identity=IDENTITY,
            )
        (written,) = cast(list[dict[str, object]], report["states"])
        self.assertEqual(written["storage"], {"~/key": "dark"})


if __name__ == "__main__":
    unittest.main()
