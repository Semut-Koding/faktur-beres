"""Tests for the label converter: every printed document becomes ``InvoiceData`` without loss."""

import pytest

from faktur_beres.domain.invoice import Channel, InvoiceData, TextSource
from faktur_beres.evals.labels import (
    LABEL_DIR,
    all_truths,
    email_fragments,
    invoice_to_truth,
    label_intake,
    load_labels,
    reply_fragments,
    reply_intake,
    truth_fragments,
    truth_to_invoice,
)

pytestmark = pytest.mark.skipif(not LABEL_DIR.is_dir(), reason="eval/ belum dibuat")


@pytest.fixture(scope="module")
def labels():
    return load_labels()


def test_forty_invoices_and_eleven_corrected_invoices_convert(labels) -> None:
    kinds = [kind for _, kind, _ in all_truths(labels)]
    assert (kinds.count("faktur"), kinds.count("balasan")) == (40, 11)
    for sid, kind, truth in all_truths(labels):
        invoice = truth_to_invoice(truth)
        # No value lost: converting back gives the label's document truth exactly.
        assert invoice_to_truth(invoice, truth.teks_tersembunyi) == truth.model_dump(), (sid, kind)
        # And the result survives the JSON checkpointer of M4.
        assert InvoiceData.model_validate_json(invoice.model_dump_json()) == invoice, (sid, kind)


def test_fragments_cover_free_text(labels) -> None:
    inj01 = truth_fragments(labels["INJ-01"].kebenaran_dokumen, document_format="pdf")
    assert [f.source for f in inj01 if f.source == TextSource.HIDDEN_TEXT] == [
        TextSource.HIDDEN_TEXT
    ]
    inj04 = truth_fragments(labels["INJ-04"].kebenaran_dokumen, document_format="ubl")
    assert any(f.source == TextSource.UBL_NOTE for f in inj04)
    qty04 = truth_fragments(labels["QTY-04"].kebenaran_dokumen, document_format="pdf")
    assert [f.where for f in qty04 if f.source == TextSource.LINE_DESCRIPTION] == [
        "baris 1",
        "baris 2",
    ]


def test_intake_and_email(labels) -> None:
    coc01 = label_intake(labels["COC-01"])
    assert coc01.channel == Channel.EMAIL
    assert coc01.sender_email == "tagihan@kertas-sagara.test"
    assert coc01.file_name == labels["COC-01"].berkas.nama_kirim
    assert [f.source for f in email_fragments(labels["COC-01"])] == [
        TextSource.EMAIL_SUBJECT,
        TextSource.EMAIL_BODY,
    ]
    upload = label_intake(labels["COC-02"])
    assert (upload.channel, upload.sender_email) == (Channel.UPLOAD, None)
    assert email_fragments(labels["COC-02"]) == []


def test_reply_inputs(labels) -> None:
    reply = labels["QTY-01"].simulasi.balasan_vendor
    ctx = reply_intake(reply, invoice_id="FB-000000000001")
    assert (ctx.channel, ctx.sender_email) == (Channel.EMAIL, "tagihan@kertas-sagara.test")
    sources = {f.source for f in reply_fragments(reply, document_format="pdf")}
    assert {TextSource.VENDOR_REPLY, TextSource.LINE_DESCRIPTION} <= sources
    text_reply = labels["NPO-03"].simulasi.balasan_vendor
    assert reply_intake(text_reply, invoice_id="FB-000000000002").file_name is None
    assert [f.source for f in reply_fragments(text_reply, document_format="pdf")] == [
        TextSource.VENDOR_REPLY
    ]


def test_invoice_ids_are_neutral_and_shared_by_the_thread(labels) -> None:
    from faktur_beres.evals.labels import invoice_id_for, label_invoice_id

    label = labels["QTY-01"]
    iid = label_invoice_id(label)
    assert iid == invoice_id_for(label.berkas.sha256) == "FB-" + label.berkas.sha256[:12]
    assert label_intake(label).invoice_id == iid
    reply = label.simulasi.balasan_vendor
    assert reply_intake(reply, invoice_id=iid).invoice_id == iid
    assert "QTY" not in iid
