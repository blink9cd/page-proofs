"""Suite: pdf_text's pypdf readers take the links and metadata of PDFs built here.

Every PDF is synthetic, written by pypdf into a temporary directory, so no PDF is ever
committed. The pdfminer text reader has its own suite, `test_pdf_text`.

Entry point assumptions: run by `unittest` discovery from the tool folder, or by the
tool's aggregator; the bootstrap below names the tool's private `src/`, and discovery's
top-level directory puts the shared fixtures beside this module on the path.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from typing import Final, cast

import pypdf.annotations
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NullObject,
    NumberObject,
    PdfObject,
    StreamObject,
    TextStringObject,
)

_TOOL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOL_DIR / "src"))
import pdf_text  # noqa: E402 - the tool's private src/, via the insert above
from _pdf_text_fixtures import (  # noqa: E402 - beside this module, via the loader's top_level_dir
    PAGE_HEIGHT,
    TempDirCase,
    blank_writer,
    write_pdf,
)

#: Where the link annotations sit, as pypdf's writer is given them.
_LINK_RECT: Final = (10.0, 10.0, 100.0, 30.0)
_SECOND_RECT: Final = (10.0, 40.0, 100.0, 60.0)

#: A packet body small enough to read in a failure message.
_XMP_PACKET: Final = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
    "<xmp:CreatorTool>Fixture Tool</xmp:CreatorTool>"
    '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
)


def _raw_link(**entries: PdfObject) -> DictionaryObject:
    """A bare `/Link` annotation dictionary carrying `entries` beside its subtype."""
    annotation = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Link"),
        }
    )
    for key, value in entries.items():
        annotation[NameObject(f"/{key}")] = value
    return annotation


def _rect(values: tuple[float, ...]) -> ArrayObject:
    """A `/Rect` array of the given numbers."""
    return ArrayObject(FloatObject(value) for value in values)


def _go_to(destination: PdfObject) -> DictionaryObject:
    """A GoTo action whose `/D` is `destination`."""
    return DictionaryObject(
        {
            NameObject("/S"): NameObject("/GoTo"),
            NameObject("/D"): destination,
        }
    )


class ReadLinksTests(TempDirCase):
    """Link annotations: page, target, rectangle; everything else ignored."""

    def test_a_uri_link_is_read_with_its_page_and_rectangle(self) -> None:
        writer = blank_writer()
        writer.add_annotation(
            page_number=0,
            annotation=pypdf.annotations.Link(
                rect=_LINK_RECT, url="https://example.com/"
            ),
        )
        links = pdf_text.read_links(write_pdf(writer, self.folder / "uri.pdf"))
        self.assertEqual(
            links, (pdf_text.Link(1, "https://example.com/", None, _LINK_RECT),)
        )

    def test_an_explicit_dest_array_is_read_as_its_pdf_text(self) -> None:
        # The `/Dest` array is built by hand, not by `annotations.Link(target_page_index=)`:
        # what that writer emits changed across pypdf releases (6.17 wrote the bare page
        # index, 6.19 a page reference), and this test pins the reader, not the writer.
        # It is attached after `add_annotation`: older pypdf (6.17) assumed any `/Dest` it
        # was handed was `annotations.Link`'s placeholder and crashed on an array, while
        # 6.19 rewrites only the placeholder dict. Attaching afterward works under both.
        writer = blank_writer(pages=2)
        added = writer.add_annotation(
            page_number=0, annotation=_raw_link(Rect=_rect(_LINK_RECT))
        )
        added[NameObject("/Dest")] = ArrayObject([NumberObject(1), NameObject("/Fit")])
        links = pdf_text.read_links(write_pdf(writer, self.folder / "dest.pdf"))
        self.assertEqual(links, (pdf_text.Link(1, None, "[ 1 /Fit ]", _LINK_RECT),))

    def test_a_named_destination_in_a_go_to_action_is_read(self) -> None:
        writer = blank_writer()
        annotation = _raw_link(
            Rect=_rect(_LINK_RECT), A=_go_to(TextStringObject("chapter-1"))
        )
        writer.add_annotation(page_number=0, annotation=annotation)
        links = pdf_text.read_links(write_pdf(writer, self.folder / "named.pdf"))
        self.assertEqual(links, (pdf_text.Link(1, None, "chapter-1", _LINK_RECT),))

    def test_a_page_reference_is_spelled_as_a_reference_not_a_resolved_page(
        self,
    ) -> None:
        # str() of a pypdf reference resolves it and prints the whole page dictionary,
        # and repr() carries a process memory address: neither is stable text.
        writer = blank_writer(pages=2)
        target = ArrayObject(
            [
                writer.pages[1].indirect_reference,
                NameObject("/XYZ"),
                NumberObject(0),
                NumberObject(PAGE_HEIGHT),
                NumberObject(0),
            ]
        )
        annotation = _raw_link(Rect=_rect(_LINK_RECT), A=_go_to(target))
        writer.add_annotation(page_number=0, annotation=annotation)
        (link,) = pdf_text.read_links(write_pdf(writer, self.folder / "ref.pdf"))
        self.assertIsNotNone(link.destination)
        self.assertRegex(str(link.destination), r"^\[ \d+ 0 R /XYZ 0 792 0 \]$")

    def test_a_dest_that_is_no_array_or_string_is_read_as_its_class(self) -> None:
        # A dictionary's text would carry every value it holds, a path among them.
        writer = blank_writer()
        dest = DictionaryObject(
            {NameObject("/Path"): TextStringObject("a value it holds")}
        )
        annotation = _raw_link(Rect=_rect(_LINK_RECT), Dest=dest)
        writer.add_annotation(page_number=0, annotation=annotation)
        links = pdf_text.read_links(write_pdf(writer, self.folder / "dict.pdf"))
        self.assertEqual(
            links, (pdf_text.Link(1, None, "<DictionaryObject>", _LINK_RECT),)
        )

    def test_a_remote_go_to_or_launch_reads_its_file_as_a_file_url(self) -> None:
        # Each opens a file of the reader's, so it is read where a URI is, and judged so.
        spec = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Filespec"),
                NameObject("/F"): TextStringObject("old.pdf"),
                NameObject("/UF"): TextStringObject("plan.pdf"),
            }
        )
        for kind, target, read in (
            ("/GoToR", TextStringObject("../notes/plan.pdf"), "../notes/plan.pdf"),
            ("/Launch", spec, "plan.pdf"),
            ("/Launch", DictionaryObject({NameObject("/F"): spec["/F"]}), "old.pdf"),
        ):
            with self.subTest(kind=kind, read=read):
                writer = blank_writer()
                action = DictionaryObject(
                    {NameObject("/S"): NameObject(kind), NameObject("/F"): target}
                )
                annotation = _raw_link(Rect=_rect(_LINK_RECT), A=action)
                writer.add_annotation(page_number=0, annotation=annotation)
                pdf = write_pdf(writer, self.folder / "remote.pdf")
                (link,) = pdf_text.read_links(pdf)
                self.assertEqual(link.uri, f"file:{read}")

    def test_a_launch_naming_no_file_has_no_uri(self) -> None:
        # A /Launch may name its file per platform (/Win, /Mac, /Unix) and none plainly.
        writer = blank_writer()
        action = DictionaryObject(
            {
                NameObject("/S"): NameObject("/Launch"),
                NameObject("/F"): DictionaryObject(
                    {NameObject("/Type"): NameObject("/Filespec")}
                ),
            }
        )
        annotation = _raw_link(Rect=_rect(_LINK_RECT), A=action)
        writer.add_annotation(page_number=0, annotation=annotation)
        (link,) = pdf_text.read_links(write_pdf(writer, self.folder / "launch.pdf"))
        self.assertIsNone(link.uri)

    def test_a_page_without_annotations_contributes_nothing(self) -> None:
        writer = blank_writer(pages=2)
        writer.add_annotation(
            page_number=1,
            annotation=pypdf.annotations.Link(
                rect=_SECOND_RECT, url="https://example.com/b"
            ),
        )
        links = pdf_text.read_links(write_pdf(writer, self.folder / "second.pdf"))
        self.assertEqual(
            links, (pdf_text.Link(2, "https://example.com/b", None, _SECOND_RECT),)
        )

    def test_a_non_link_annotation_is_ignored(self) -> None:
        writer = blank_writer()
        writer.add_annotation(
            page_number=0,
            annotation=pypdf.annotations.Text(rect=_LINK_RECT, text="a note"),
        )
        writer.add_annotation(
            page_number=0,
            annotation=pypdf.annotations.Link(
                rect=_SECOND_RECT, url="https://example.com/"
            ),
        )
        links = pdf_text.read_links(write_pdf(writer, self.folder / "note.pdf"))
        self.assertEqual(
            links, (pdf_text.Link(1, "https://example.com/", None, _SECOND_RECT),)
        )

    def test_an_annotation_entry_that_is_not_a_dictionary_is_skipped(self) -> None:
        writer = blank_writer()
        writer.pages[0][NameObject("/Annots")] = ArrayObject([NullObject()])
        self.assertEqual(
            pdf_text.read_links(write_pdf(writer, self.folder / "null.pdf")), ()
        )

    def test_a_link_with_no_rectangle_is_a_read_error(self) -> None:
        writer = blank_writer()
        writer.pages[0][NameObject("/Annots")] = ArrayObject([_raw_link()])
        pdf = write_pdf(writer, self.folder / "norect.pdf")
        with self.assertRaises(pdf_text.PdfTextError) as caught:
            pdf_text.read_links(pdf)
        self.assert_read_error(caught.exception, pdf)

    def test_a_rectangle_that_is_not_four_numbers_is_a_read_error(self) -> None:
        for rect in (NumberObject(5), _rect((1.0, 2.0, 3.0))):
            with self.subTest(rect=rect):
                writer = blank_writer()
                writer.pages[0][NameObject("/Annots")] = ArrayObject(
                    [_raw_link(Rect=rect)]
                )
                pdf = write_pdf(writer, self.folder / "badrect.pdf")
                with self.assertRaises(pdf_text.PdfTextError) as caught:
                    pdf_text.read_links(pdf)
                self.assert_read_error(caught.exception, pdf)

    def test_a_corrupt_file_is_a_read_error_naming_the_file(self) -> None:
        pdf = self.folder / "corrupt.pdf"
        pdf.write_bytes(b"not a pdf at all")
        # pypdf logs its own complaint before raising; keep the suite's output clean.
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(pdf_text.PdfTextError) as caught,
        ):
            pdf_text.read_links(pdf)
        self.assert_read_error(caught.exception, pdf)

    def test_a_missing_file_names_the_file_but_not_its_directory(self) -> None:
        pdf = self.folder / "absent.pdf"
        with self.assertRaises(pdf_text.PdfTextError) as caught:
            pdf_text.read_links(pdf)
        self.assert_read_error(caught.exception, pdf)


class ReadMetadataTests(TempDirCase):
    """The Info dictionary as plain strings, and the raw XMP packet."""

    def test_info_keys_lose_their_slash_and_values_become_text(self) -> None:
        writer = blank_writer()
        writer.add_metadata({"/Author": "Fixture Person", "/Title": "A page"})
        info, xmp = pdf_text.read_metadata(write_pdf(writer, self.folder / "info.pdf"))
        self.assertEqual(info["Author"], "Fixture Person")
        self.assertEqual(info["Title"], "A page")
        self.assertTrue(all(not key.startswith("/") for key in info))
        self.assertIsNone(xmp)

    def test_the_xmp_packet_is_returned_raw(self) -> None:
        writer = blank_writer()
        writer.xmp_metadata = _XMP_PACKET.encode("utf-8")
        _, xmp = pdf_text.read_metadata(write_pdf(writer, self.folder / "xmp.pdf"))
        self.assertEqual(xmp, _XMP_PACKET)

    def test_a_malformed_xmp_packet_is_still_returned(self) -> None:
        # pypdf's own xmp_metadata PARSES the packet and raises on bad XML; a broken
        # packet can leak as well as a sound one, so it is read raw from the catalog.
        writer = blank_writer()
        writer.xmp_metadata = b"<x:xmpmeta never closed"
        _, xmp = pdf_text.read_metadata(write_pdf(writer, self.folder / "badxmp.pdf"))
        self.assertEqual(xmp, "<x:xmpmeta never closed")

    def test_a_metadata_entry_that_is_not_a_stream_counts_as_absent(self) -> None:
        writer = blank_writer()
        writer.root_object[NameObject("/Metadata")] = DictionaryObject()
        _, xmp = pdf_text.read_metadata(write_pdf(writer, self.folder / "dictxmp.pdf"))
        self.assertIsNone(xmp)

    def test_an_xmp_stream_in_a_filter_pypdf_lacks_is_a_read_error(self) -> None:
        # pypdf raises NotImplementedError for a filter it does not implement, as it
        # does for /Crypt with a /Name: the Identity crypt filter an encrypted PDF puts
        # on an unencrypted metadata stream. It must not escape the reader untyped.
        writer = blank_writer()
        writer.xmp_metadata = _XMP_PACKET.encode("utf-8")
        # Indexing a dictionary follows the reference, so this is the stream itself.
        stream = cast(StreamObject, writer.root_object["/Metadata"])
        stream[NameObject("/Filter")] = NameObject("/NoSuchDecode")
        pdf = write_pdf(writer, self.folder / "filter.pdf")
        with self.assertRaises(pdf_text.PdfTextError) as caught:
            pdf_text.read_metadata(pdf)
        self.assert_read_error(caught.exception, pdf)

    def test_a_file_with_no_info_dictionary_reads_as_empty(self) -> None:
        writer = blank_writer()
        writer.metadata = None
        info, xmp = pdf_text.read_metadata(
            write_pdf(writer, self.folder / "noinfo.pdf")
        )
        self.assertEqual((dict(info), xmp), ({}, None))

    def test_a_corrupt_file_is_a_read_error(self) -> None:
        pdf = self.folder / "corrupt.pdf"
        pdf.write_bytes(b"")
        with self.assertRaises(pdf_text.PdfTextError) as caught:
            pdf_text.read_metadata(pdf)
        self.assert_read_error(caught.exception, pdf)


if __name__ == "__main__":  # pragma: no cover - the discovery runner is the entry point
    unittest.main()
