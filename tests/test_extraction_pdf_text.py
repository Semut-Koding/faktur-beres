"""The PDF text layer, page by page (M3.3)."""

import io

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from faktur_beres.config import REPO_ROOT
from faktur_beres.domain.invoice import TextSource
from faktur_beres.evals.labels import load_label
from faktur_beres.extraction.filetype import identify
from faktur_beres.extraction.pdf_text import MIN_TEXT_CHARS, read_pdf_text, text_layer

BERKAS = REPO_ROOT / "eval" / "berkas"


def image_only_pdf() -> bytes:
    picture = Image.new("RGB", (400, 200), "white")
    for x in range(40, 360):
        picture.putpixel((x, 100), (0, 0, 0))
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawImage(ImageReader(picture), 72, 500, width=400, height=200)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def test_white_text_of_inj_01_is_read() -> None:
    hidden = load_label(REPO_ROOT / "eval" / "skenario" / "INJ-01.yaml").kebenaran_dokumen
    data = (BERKAS / "INJ-01" / "faktur.pdf").read_bytes()
    layer = text_layer(identify(data), data)
    assert layer.has_text_layer
    joined = " ".join(" ".join(f.text.split()) for f in layer.fragments())
    for text in hidden.teks_tersembunyi:
        assert " ".join(text.split()) in joined
    assert {f.source for f in layer.fragments()} == {TextSource.PDF_TEXT_LAYER}
    assert layer.fragments()[0].where == "halaman 1"


def test_image_only_pdf_has_no_text_layer() -> None:
    data = image_only_pdf()
    layer = text_layer(identify(data), data)
    assert not layer.has_text_layer and layer.char_count < MIN_TEXT_CHARS
    assert layer.fragments() == ()


def test_image_and_xml_files_have_no_text_layer() -> None:
    for path in ("DUP-03/faktur.png", "INJ-05/faktur.jpg", "PJK-01/faktur.xml"):
        data = (BERKAS / path).read_bytes()
        assert text_layer(identify(data), data).pages == ()


def test_every_page_is_its_own_fragment() -> None:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, "Halaman pertama berisi teks yang cukup panjang untuk diuji.")
    pdf.showPage()
    pdf.showPage()  # an empty page
    pdf.drawString(72, 720, "Halaman ketiga.")
    pdf.showPage()
    pdf.save()
    layer = read_pdf_text(buffer.getvalue())
    assert len(layer.pages) == 3 and layer.pages[1] == ""
    assert [f.where for f in layer.fragments()] == ["halaman 1", "halaman 3"]
    assert layer.has_text_layer
