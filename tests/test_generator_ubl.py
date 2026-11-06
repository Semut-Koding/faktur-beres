"""Tests for UBL 2.1 rendering (SPEC §15.6): well-formed XML, namespaces, read-back."""

import re

import pytest
from lxml import etree

from faktur_beres.dataset import load_dataset
from faktur_beres.generator.documents import build_catalog
from faktur_beres.generator.render_ubl import CUSTOMIZATION_ID, NS, read_ubl, render_ubl

LABEL_PATTERN = re.compile(r"\b(COC|HRG|QTY|DUP|NPO|PJK|REK|INJ)-\d{2}\b")


@pytest.fixture(scope="module")
def ubl_docs():
    built = build_catalog(load_dataset())
    docs = {}
    for b in built:
        if b.scenario.format == "ubl":
            docs[b.scenario.id] = b.doc
            if b.reply_doc is not None:
                docs[f"{b.scenario.id}/balasan"] = b.reply_doc
    return docs


def test_ubl_scenarios_present(ubl_docs) -> None:
    assert set(ubl_docs) == {"COC-03", "QTY-03", "PJK-01", "PJK-01/balasan", "REK-02", "INJ-04"}


def test_xml_is_well_formed_with_ubl_namespaces(ubl_docs) -> None:
    for key, doc in ubl_docs.items():
        data = render_ubl(doc)
        assert data.startswith(b"<?xml version='1.0' encoding='UTF-8'?>"), key
        root = etree.fromstring(data)
        assert root.tag == f"{{{NS['inv']}}}Invoice", key
        assert root.nsmap == {None: NS["inv"], "cac": NS["cac"], "cbc": NS["cbc"]}, key
        assert root.findtext(f"{{{NS['cbc']}}}CustomizationID") == CUSTOMIZATION_ID
        assert root.findtext(f"{{{NS['cbc']}}}InvoiceTypeCode") == "380"
        assert root.findtext(f"{{{NS['cbc']}}}DocumentCurrencyCode") == "IDR"
        for element in root.iter():
            if element.get("currencyID") is not None:
                assert element.get("currencyID") == "IDR"
                assert re.fullmatch(r"\d+\.\d{2}", element.text), (key, element.tag)
        assert not LABEL_PATTERN.search(data.decode("utf-8")), key
        assert render_ubl(doc) == data  # deterministic


def test_read_back_matches_model(ubl_docs) -> None:
    for key, doc in ubl_docs.items():
        got = read_ubl(render_ubl(doc))
        assert got["nomor_faktur"] == doc.number, key
        assert got["tanggal_faktur"] == doc.issue_date, key
        assert got["jatuh_tempo"] == doc.due_date, key
        assert got["nomor_po"] == doc.po_ref, key
        assert got["nsfp"] == doc.nsfp, key
        assert got["tanggal_faktur_pajak"] == doc.tax_invoice_date, key
        assert got["penjual"]["nama"] == doc.seller.name, key
        assert got["penjual"]["npwp"] == doc.seller.npwp, key
        assert got["penjual"]["email"] == doc.seller.email, key
        assert got["penjual"]["alamat"] == doc.seller.address, key
        assert got["pembeli"]["npwp"] == doc.buyer.npwp, key
        assert got["rekening"] == {
            "bank": doc.bank.bank,
            "nomor": doc.bank.number,
            "atas_nama": doc.bank.holder,
        }, key
        assert got["subtotal"] == doc.subtotal and got["dpp_nilai_lain"] == doc.dpp, key
        assert got["ppn"] == doc.ppn and got["total"] == doc.total, key
        assert got["catatan"] == list(doc.notes), key
        assert [(r["kode"], r["qty"], r["harga_satuan"], r["jumlah"]) for r in got["baris"]] == [
            (line.code, line.qty, line.unit_price, line.amount) for line in doc.lines
        ], key
        assert [r["satuan"] for r in got["baris"]] == [line.ubl_unit for line in doc.lines]


def test_missing_seller_npwp_and_injection_note(ubl_docs) -> None:
    assert read_ubl(render_ubl(ubl_docs["PJK-01"]))["penjual"]["npwp"] is None
    assert read_ubl(render_ubl(ubl_docs["PJK-01/balasan"]))["penjual"]["npwp"] is not None
    injected = read_ubl(render_ubl(ubl_docs["INJ-04"]))
    assert "PaymentMeans" in injected["catatan"][0]
    assert injected["rekening"]["nomor"] == ubl_docs["INJ-04"].bank.number


def test_taxable_amount_is_dpp_nilai_lain(ubl_docs) -> None:
    for key, doc in ubl_docs.items():
        got = read_ubl(render_ubl(doc))
        assert got["dpp_nilai_lain"] * 12 == got["subtotal"] * 11, key
        assert got["ppn"] * 100 == got["dpp_nilai_lain"] * 12, key
