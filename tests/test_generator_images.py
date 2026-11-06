"""Tests for photo and scan rendering and for send names."""

import re
from datetime import date
from io import BytesIO

import pytest
from PIL import Image, ImageChops

from faktur_beres.dataset import load_dataset
from faktur_beres.generator.documents import build_catalog
from faktur_beres.generator.naming import pdf_name, photo_name, scan_name, ubl_name
from faktur_beres.generator.render_image import (
    MAX_WIDTH,
    perspective_coeffs,
    render_photo,
    render_scan,
)
from faktur_beres.generator.render_pdf import render_pdf
from faktur_beres.generator.styles import STYLES

LABEL_PATTERN = re.compile(r"\b(COC|HRG|QTY|DUP|NPO|PJK|REK|INJ)-\d{2}\b")


@pytest.fixture(scope="module")
def pdfs():
    built = {b.scenario.id: b for b in build_catalog(load_dataset())}
    return {
        sid: render_pdf(built[sid].doc, STYLES[built[sid].scenario.vendor])
        for sid in ("HRG-01", "NPO-02", "INJ-05")
    }, built


def opened(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


def test_photo_is_jpeg_without_exif(pdfs) -> None:
    rendered, _ = pdfs
    data = render_photo(rendered["HRG-01"], "HRG-01")
    image = opened(data)
    assert image.format == "JPEG" and image.mode == "RGB"
    assert image.width <= MAX_WIDTH
    assert len(image.getexif()) == 0 and "exif" not in image.info
    assert render_photo(rendered["HRG-01"], "HRG-01") == data  # deterministic


@pytest.mark.parametrize("png", [True, False])
def test_scan_is_greyscale_without_exif(pdfs, png: bool) -> None:
    rendered, _ = pdfs
    data = render_scan(rendered["NPO-02"], "NPO-02", png=png)
    image = opened(data)
    assert image.format == ("PNG" if png else "JPEG")
    assert image.mode == "L" and image.width <= MAX_WIDTH
    assert len(image.getexif()) == 0 and "exif" not in image.info
    assert render_scan(rendered["NPO-02"], "NPO-02", png=png) == data


def test_sticky_note_changes_the_photo(pdfs) -> None:
    rendered, built = pdfs
    note = built["INJ-05"].doc.photo_note
    with_note = opened(render_photo(rendered["INJ-05"], "INJ-05", note=note)).convert("RGB")
    without = opened(render_photo(rendered["INJ-05"], "INJ-05")).convert("RGB")
    box = ImageChops.difference(with_note, without).getbbox()
    assert box is not None
    assert box[0] > with_note.width * 0.4 and box[1] > with_note.height * 0.5  # lower right


def test_perspective_coeffs_map_corners() -> None:
    target = [(10, 20), (110, 25), (115, 160), (5, 150)]
    source = [(0, 0), (100, 0), (100, 140), (0, 140)]
    a, b, c, d, e, f, g, h = perspective_coeffs(target, source)
    for (x, y), (u, v) in zip(target, source, strict=True):
        w = g * x + h * y + 1
        assert abs((a * x + b * y + c) / w - u) < 1e-6
        assert abs((d * x + e * y + f) / w - v) < 1e-6


def test_send_names() -> None:
    assert pdf_name("KSM/INV/2026/10/0458") == "KSM-INV-2026-10-0458.pdf"
    assert ubl_name("CFP-INV-26-00337") == "CFP-INV-26-00337.xml"
    photo = photo_name("HRG-01", date(2026, 10, 29))
    assert re.fullmatch(r"IMG_20261029_(0[89]|1[0-6])[0-5]\d[0-5]\d\.jpg", photo)
    assert photo_name("HRG-01", date(2026, 10, 29)) == photo
    assert re.fullmatch(r"SCAN_20261103_\d{4}\.png", scan_name("NPO-02", date(2026, 11, 3), "png"))
    for name in (photo, pdf_name("SMJ.2611.054"), scan_name("DUP-03", date(2026, 10, 28), "png")):
        assert not LABEL_PATTERN.search(name)
