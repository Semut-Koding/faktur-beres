"""Tests for PDF rendering: content in the text layer, metadata, styles, determinism."""

import re

import pytest
from PIL import ImageChops

from faktur_beres.dataset import load_dataset
from faktur_beres.generator.documents import build_catalog
from faktur_beres.generator.formatting import date_text, money
from faktur_beres.generator.pdfinfo import pdf_metadata, pdf_page_count, pdf_text, render_page
from faktur_beres.generator.render_pdf import SYNTHETIC_MARK, render_pdf
from faktur_beres.generator.styles import STYLES

LABEL_PATTERN = re.compile(r"\b(COC|HRG|QTY|DUP|NPO|PJK|REK|INJ)-\d{2}\b")
# One scenario per vendor that renders as PDF or image (v04 only sends UBL).
SAMPLES = ("COC-01", "HRG-01", "HRG-03", "DUP-05", "COC-02", "HRG-02", "HRG-04", "COC-04",
           "HRG-05", "NPO-05", "INJ-03", "INJ-02", "QTY-04", "PJK-03", "PJK-02")  # fmt: skip


@pytest.fixture(scope="module")
def built():
    return {b.scenario.id: b for b in build_catalog(load_dataset())}


@pytest.fixture(scope="module")
def rendered(built):
    return {sid: render_pdf(built[sid].doc, STYLES[built[sid].scenario.vendor]) for sid in SAMPLES}


@pytest.mark.parametrize("sid", SAMPLES)
def test_text_layer_has_key_values(built, rendered, sid: str) -> None:
    b = built[sid]
    doc, style = b.doc, STYLES[b.scenario.vendor]
    text = pdf_text(rendered[sid])
    assert pdf_page_count(rendered[sid]) == 1
    assert SYNTHETIC_MARK in text
    for value in (doc.number, doc.seller.name, doc.buyer.name, doc.buyer.npwp):
        assert value in text, value
    assert money(doc.total, style.number_format) in text
    assert date_text(doc.issue_date, style.date_format) in text
    if doc.nsfp:
        assert doc.nsfp in text
    else:
        assert "Faktur Pajak" not in text
    if doc.po_ref:
        assert doc.po_ref in text
    for line in doc.lines:
        assert line.code in text
    if doc.bank:
        assert doc.bank.number in text
    for note in doc.notes:
        assert note.split()[0] in text
    assert not LABEL_PATTERN.search(text)


@pytest.mark.parametrize("sid", SAMPLES)
def test_metadata(built, rendered, sid: str) -> None:
    meta = pdf_metadata(rendered[sid])
    assert meta["Title"] == f"Faktur {built[sid].doc.number}"
    assert meta["Author"] == built[sid].doc.seller.name
    assert meta["Creator"] == "Faktur Beres generator"
    assert not LABEL_PATTERN.search(" ".join(meta.values()))


def test_rendering_is_deterministic(built) -> None:
    for sid in ("COC-01", "HRG-05", "INJ-01"):
        b = built[sid]
        style = STYLES[b.scenario.vendor]
        assert render_pdf(b.doc, style) == render_pdf(b.doc, style)


def test_hidden_text_is_in_text_layer_but_invisible(built) -> None:
    b = built["INJ-01"]
    style = STYLES[b.scenario.vendor]
    with_hidden = render_pdf(b.doc, style)
    without_hidden = render_pdf(b.doc, style, include_hidden=False)
    assert b.doc.hidden_texts[0][:40] in pdf_text(with_hidden).replace("\n", " ")
    assert b.doc.hidden_texts[0][:40] not in pdf_text(without_hidden)
    difference = ImageChops.difference(render_page(with_hidden), render_page(without_hidden))
    assert difference.getbbox() is None  # pixel-identical: the white text cannot be seen


def test_style_variety() -> None:
    used = {STYLES[v] for v in STYLES}
    assert {s.layout for s in used} == {"klasik", "modern", "ringkas", "dotmatrix"}
    assert {s.number_format for s in used} == {"rp", "idr"}
    assert {s.date_format for s in used} == {"dmy_slash", "long", "dmon"}
    assert {s.terbilang for s in used} == {True, False}
    assert len({s.initials for s in used}) == len(STYLES)
