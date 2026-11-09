"""Label version 2: a party's e-mail is in the truth exactly when its document prints it (M4a.1)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from lxml import etree

from faktur_beres.config import REPO_ROOT
from faktur_beres.dataset import load_dataset
from faktur_beres.generator import render_image, render_pdf, render_ubl, robust_render
from faktur_beres.generator.assemble import render_file
from faktur_beres.generator.documents import build_catalog
from faktur_beres.generator.pdfinfo import pdf_text
from faktur_beres.generator.styles import STYLES

MIN_TEXT_LAYER = 200  # a scanned PDF has no text layer to compare
UBL_NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}


def _documents() -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    for folder in ("eval/skenario", "eval/ketahanan/skenario"):
        for path in sorted((REPO_ROOT / folder).glob("*.yaml")):
            label = yaml.safe_load(path.read_text(encoding="utf-8"))
            yield label["id"], label["berkas"], label["kebenaran_dokumen"]
            reply = (label.get("simulasi") or {}).get("balasan_vendor")
            if reply and reply["berkas"] and reply["kebenaran_dokumen"]:
                yield f"{label['id']}/balasan", reply["berkas"], reply["kebenaran_dokumen"]


DOCUMENTS = list(_documents())


def _with_suffix(*suffixes: str) -> list[tuple[str, Path, dict[str, Any]]]:
    return [
        (name, REPO_ROOT / ref["path"], truth)
        for name, ref, truth in DOCUMENTS
        if ref["path"].rsplit(".", 1)[-1] in suffixes
    ]


@pytest.mark.parametrize(("name", "path", "truth"), _with_suffix("pdf"), ids=lambda v: str(v)[:12])
def test_pdf_label_has_an_email_iff_its_text_layer_prints_it(
    name: str, path: Path, truth: dict[str, Any]
) -> None:
    text = pdf_text(path.read_bytes())
    if len(text) < MIN_TEXT_LAYER:
        pytest.skip(f"{name}: tanpa lapisan teks")
    company_email = "ap@arunika.test"
    assert truth["pembeli"]["email"] == (company_email if company_email in text else None)
    seller = truth["penjual"]["email"]
    assert seller is None or seller in text


@pytest.mark.parametrize(("name", "path", "truth"), _with_suffix("xml"), ids=lambda v: str(v)[:12])
def test_ubl_label_equals_the_buyer_contact_of_the_file(
    name: str, path: Path, truth: dict[str, Any]
) -> None:
    del name
    found = etree.fromstring(path.read_bytes()).xpath(
        "cac:AccountingCustomerParty//cbc:ElectronicMail/text()", namespaces=UBL_NS
    )
    assert [truth["pembeli"]["email"]] == found


@pytest.mark.parametrize(
    ("name", "path", "truth"), _with_suffix("jpg", "png"), ids=lambda v: str(v)[:12]
)
def test_image_label_follows_its_renderer(name: str, path: Path, truth: dict[str, Any]) -> None:
    del name, path
    assert render_image.PRINTED_PARTIES.buyer_email is False
    assert truth["pembeli"]["email"] is None


def test_every_renderer_declares_what_it_prints() -> None:
    assert render_pdf.PRINTED_PARTIES.buyer_email is False
    assert robust_render.PRINTED_PARTIES.buyer_email is False
    assert render_ubl.PRINTED_PARTIES.buyer_email is True


def test_the_source_page_of_an_image_does_not_print_the_buyer_email() -> None:
    images = [b for b in build_catalog(load_dataset()) if b.scenario.format == "gambar"]
    assert images
    for b in images:
        rendered = render_file(b, b.doc, reply=False)
        assert rendered.printed == render_image.PRINTED_PARTIES
        page = pdf_text(render_pdf.render_pdf(b.doc, STYLES[b.scenario.vendor]))
        assert b.doc.buyer.email not in page
