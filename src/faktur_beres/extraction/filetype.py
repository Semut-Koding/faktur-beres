"""The type of an incoming file, decided from its first bytes (M3.3).

The file name, the extension and the MIME type given by the sender are ignored: a PDF named
``faktur.png`` is a PDF. Accepted: PDF, PNG, JPEG and XML (UBL e-invoice). Limits: 15 MB, and at
most 10 pages for a PDF. Anything else is refused with a typed error before any byte reaches the
LLM (decision 1 of the M2 review: the file size is limited at intake).
"""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

import pypdfium2 as pdfium

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 10
_XML_START = re.compile(rb"(?:\xef\xbb\xbf)?\s*<(?:\?xml|!--|[A-Za-z_])")


class FileKind(StrEnum):
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    XML = "xml"


MIME_TYPES: dict[FileKind, str] = {
    FileKind.PDF: "application/pdf",
    FileKind.PNG: "image/png",
    FileKind.JPEG: "image/jpeg",
    FileKind.XML: "application/xml",
}


class FileRejected(Exception):
    """The file cannot be processed; ``reason`` says why."""

    reason = "file_rejected"


class EmptyFile(FileRejected):
    reason = "empty"


class FileTooLarge(FileRejected):
    reason = "too_large"


class UnsupportedFileType(FileRejected):
    reason = "unsupported_type"


class TooManyPages(FileRejected):
    reason = "too_many_pages"


class UnreadablePdf(FileRejected):
    reason = "unreadable_pdf"


@dataclass(frozen=True)
class IncomingFile:
    kind: FileKind
    size: int
    sha256: str
    pages: int | None = None  # PDF only

    @property
    def mime_type(self) -> str:
        return MIME_TYPES[self.kind]


def sniff(data: bytes) -> FileKind | None:
    """The kind of file the first bytes announce, or None."""
    if data.startswith(b"%PDF-"):
        return FileKind.PDF
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileKind.PNG
    if data.startswith(b"\xff\xd8\xff"):
        return FileKind.JPEG
    if _XML_START.match(data[:512]):
        return FileKind.XML
    return None


def open_pdf(data: bytes) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise UnreadablePdf("PDF tidak bisa dibuka (rusak atau terkunci sandi)") from exc


def pdf_page_count(data: bytes) -> int:
    pdf = open_pdf(data)
    try:
        return int(len(pdf))
    finally:
        pdf.close()


def identify(data: bytes) -> IncomingFile:
    """Check the size, the type and (for a PDF) the page count; raise ``FileRejected``."""
    size = len(data)
    if size == 0:
        raise EmptyFile("berkas kosong")
    if size > MAX_FILE_BYTES:
        raise FileTooLarge(f"berkas {size} byte melebihi batas {MAX_FILE_BYTES} byte (15 MB)")
    kind = sniff(data)
    if kind is None:
        raise UnsupportedFileType(
            "jenis berkas tidak dikenali dari isinya; yang diterima: PDF, PNG, JPEG, dan XML (UBL)"
        )
    pages = pdf_page_count(data) if kind is FileKind.PDF else None
    if pages is not None and pages > MAX_PDF_PAGES:
        raise TooManyPages(f"PDF {pages} halaman melebihi batas {MAX_PDF_PAGES} halaman")
    return IncomingFile(kind=kind, size=size, sha256=hashlib.sha256(data).hexdigest(), pages=pages)
