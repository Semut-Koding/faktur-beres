"""The text layer of a PDF, page by page (M3.3).

Every page with text becomes a ``pdf_text_layer`` fragment (``where="halaman N"``) for the
injection check (BR-15), so text that does not show on the rendered page (white text, text under an
image) is still checked. A PDF "has a text layer" when its pages hold at least 50 characters other
than whitespace; a scanned PDF and an image file have none.
"""

from dataclasses import dataclass

from faktur_beres.domain.invoice import TextFragment, TextSource
from faktur_beres.extraction.filetype import FileKind, IncomingFile, open_pdf

MIN_TEXT_CHARS = 50


@dataclass(frozen=True)
class PdfText:
    pages: tuple[str, ...] = ()

    @property
    def char_count(self) -> int:
        """Characters other than whitespace, over all pages."""
        return sum(1 for page in self.pages for char in page if not char.isspace())

    @property
    def has_text_layer(self) -> bool:
        return self.char_count >= MIN_TEXT_CHARS

    def fragments(self) -> tuple[TextFragment, ...]:
        return tuple(
            TextFragment(source=TextSource.PDF_TEXT_LAYER, text=text, where=f"halaman {number}")
            for number, text in enumerate(self.pages, start=1)
            if text
        )


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()


def read_pdf_text(data: bytes) -> PdfText:
    """The text of every page, in page order (empty string for a page without text)."""
    pdf = open_pdf(data)
    try:
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                textpage = page.get_textpage()
                try:
                    pages.append(_clean(str(textpage.get_text_range())))
                finally:
                    textpage.close()
            finally:
                page.close()
        return PdfText(pages=tuple(pages))
    finally:
        pdf.close()


def text_layer(file: IncomingFile, data: bytes) -> PdfText:
    """The text layer of the file: read for a PDF; an image or an XML file has none."""
    return read_pdf_text(data) if file.kind is FileKind.PDF else PdfText()
