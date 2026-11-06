"""Read back generated PDFs with pypdfium2 (text layer, metadata, rendering)."""

import pypdfium2 as pdfium
from PIL import Image


def pdf_text(data: bytes) -> str:
    """Text layer of every page, pages separated by a form feed."""
    pdf = pdfium.PdfDocument(data)
    try:
        parts = []
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            parts.append(textpage.get_text_range())
            textpage.close()
            page.close()
        return "\f".join(parts).replace("\r\n", "\n")
    finally:
        pdf.close()


def pdf_metadata(data: bytes) -> dict[str, str]:
    pdf = pdfium.PdfDocument(data)
    try:
        return {k: v for k, v in pdf.get_metadata_dict().items() if v}
    finally:
        pdf.close()


def pdf_page_count(data: bytes) -> int:
    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_page(data: bytes, dpi: float = 150, page_index: int = 0) -> Image.Image:
    """Rasterize one page to an RGB image."""
    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[page_index]
        bitmap = page.render(scale=dpi / 72)
        image = bitmap.to_pil().convert("RGB")
        page.close()
        return image
    finally:
        pdf.close()
