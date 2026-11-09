"""File type from magic bytes, and the size and page limits (M3.3)."""

import hashlib
import io

import pytest
from reportlab.pdfgen import canvas

from faktur_beres.config import REPO_ROOT
from faktur_beres.extraction.filetype import (
    MAX_FILE_BYTES,
    EmptyFile,
    FileKind,
    FileTooLarge,
    TooManyPages,
    UnreadablePdf,
    UnsupportedFileType,
    identify,
)

BERKAS = REPO_ROOT / "eval" / "berkas"


def pdf_with_pages(count: int) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for number in range(count):
        pdf.drawString(72, 720, f"Halaman uji {number + 1}")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("path", "kind", "mime"),
    [
        ("COC-01/faktur.pdf", FileKind.PDF, "application/pdf"),
        ("DUP-03/faktur.png", FileKind.PNG, "image/png"),
        ("INJ-05/faktur.jpg", FileKind.JPEG, "image/jpeg"),
        ("PJK-01/faktur.xml", FileKind.XML, "application/xml"),
    ],
)
def test_type_comes_from_the_content(path: str, kind: FileKind, mime: str) -> None:
    data = (BERKAS / path).read_bytes()
    found = identify(data)
    assert (found.kind, found.mime_type, found.size) == (kind, mime, len(data))
    assert found.sha256 == hashlib.sha256(data).hexdigest()
    assert (found.pages is not None) == (kind is FileKind.PDF)


def test_xml_with_byte_order_mark_leading_space_or_comment() -> None:
    assert identify(b"\xef\xbb\xbf \n<Invoice/>").kind is FileKind.XML
    assert identify(b"<!-- e-invoice --><Invoice/>").kind is FileKind.XML


@pytest.mark.parametrize(
    "data",
    [b"GIF89a\x01\x00", b"PK\x03\x04zip", b"faktur biasa", b"\x00\x01\x02", b"< spasi"],
    ids=["gif", "zip", "teks", "biner", "bukan-tag"],
)
def test_other_types_are_refused(data: bytes) -> None:
    with pytest.raises(UnsupportedFileType):
        identify(data)


def test_empty_and_too_large_files_are_refused() -> None:
    with pytest.raises(EmptyFile):
        identify(b"")
    with pytest.raises(FileTooLarge, match="15 MB"):
        identify(b"%PDF-" + b"0" * MAX_FILE_BYTES)


def test_page_limit() -> None:
    assert identify(pdf_with_pages(10)).pages == 10
    with pytest.raises(TooManyPages, match="11 halaman"):
        identify(pdf_with_pages(11))


def test_unreadable_pdf_is_refused() -> None:
    with pytest.raises(UnreadablePdf):
        identify(b"%PDF-1.7\nbukan PDF yang utuh")
