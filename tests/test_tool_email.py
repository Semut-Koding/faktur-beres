"""``email_tool``: recipient guard, templates, idempotence (no mail server)."""

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import resources
from typing import Any

import pytest

from faktur_beres.domain.findings import (
    Finding,
    LineAmountEvidence,
    LineMatchEvidence,
    PriceEvidence,
    PurchaseOrderEvidence,
    QuantityEvidence,
    TaxAmountEvidence,
    TaxIdEvidence,
)
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.tools.email_tool import (
    MAX_NOTE,
    MailpitMailer,
    MailUnavailable,
    OutgoingEmail,
    RecipientRefused,
    clarification_body,
    clarification_email,
    duplicate_notice,
    message_id_for,
    send_checked,
    vendor_sentence,
)

V1_FINGERPRINT = "09d0d3e8c2b491cd2e11777daae6b109b58c11c36a121e739b74c88c6d3c3327"
VENDOR = "tagihan@vendor.test"
KEY = "a" * 64
PRICE = Finding(
    rule="BR-08",
    reason=ReasonCode.PRICE_VARIANCE,
    message="Baris 1: harga naik, di atas batas Rp4.898,50.",
    evidence=PriceEvidence(
        line=1,
        po_line_id=9,
        product="Kraft Liner",
        invoice_price=Decimal(4950),
        po_price=Decimal(4850),
        limit=Decimal("4898.5"),
        difference=Decimal(100),
        difference_percent=Decimal("2.06"),
    ),
)
TAX = Finding(
    rule="BR-10",
    reason=ReasonCode.TAX_INVALID,
    message="NPWP 0990427957220000 tidak valid.",
    evidence=TaxIdEvidence(
        field="npwp_penjual",
        printed="0990427957220000",
        digits="0990427957220000",
        problem="check_digit",
    ),
)
NEW = Finding(rule="BR-14", reason=ReasonCode.NEW_VENDOR, message="Vendor baru.")
NOTE = Finding(rule="BR-08", message="catatan internal")


@dataclass
class Box:
    seen: list[str] = field(default_factory=list)
    keeps: bool = True

    def exists(self, message_id: str) -> bool:
        return message_id in self.seen

    def send(self, mail: OutgoingEmail) -> None:
        if self.keeps:
            self.seen.append(mail.message_id)


def mail(to: str = VENDOR) -> OutgoingEmail:
    return clarification_email(
        invoice_id="FB-000123",
        invoice_number="INV/1",
        sender="ap@arunika.test",
        to=to,
        key=KEY,
        findings=[PRICE],
        approver_note=None,
    )


def send(box: Box, target: str = VENDOR) -> bool:
    return send_checked(box, mail(target), vendor_emails=[VENDOR], internal_domain="arunika.test")


@pytest.mark.aturan("BR-19")
def test_subject_carries_the_thread_reference_and_number() -> None:
    assert mail().subject == "[FB-000123] Klarifikasi faktur INV/1"


def test_message_id_is_deterministic() -> None:
    assert mail().message_id == message_id_for(KEY) == mail().message_id


@pytest.mark.aturan("BR-25")
@pytest.mark.parametrize(
    "address",
    ["audit@pemeriksa-eksternal.test", "a@vendor.test, b@vendor.test", "tidak-valid"],
)
def test_other_addresses_are_refused(address: str) -> None:
    box = Box()
    with pytest.raises(RecipientRefused):
        send(box, address)
    assert box.seen == []


@pytest.mark.aturan("BR-25")
def test_internal_address_is_allowed() -> None:
    assert send(Box(), "finance@arunika.test") is True


@pytest.mark.aturan("BR-22")
def test_a_message_that_exists_is_not_sent_again() -> None:
    box = Box()
    assert send(box) is True
    assert send(box) is False
    assert len(box.seen) == 1


def test_a_message_not_read_back_is_an_error() -> None:
    with pytest.raises(MailUnavailable):
        send(Box(keeps=False))


@pytest.mark.aturan("BR-19")
def test_one_section_per_reason_b_built_from_the_evidence() -> None:
    body = clarification_body(
        invoice_number="INV/1", findings=[PRICE, TAX, NEW, NOTE], approver_note=None
    )
    assert "Harga satuan" in body and "Data pajak" in body
    assert "harga di faktur Rp4.950, harga di pesanan pembelian Rp4.850." in body
    assert "NPWP penjual tidak valid." in body and "0990" not in body
    assert "batas" not in body and "Rp4.898" not in body
    assert "Vendor baru." not in body and "catatan internal" not in body
    assert "\n\n\n" not in body


@pytest.mark.aturan("BR-19")
@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (
            QuantityEvidence(
                line=2,
                po_line_id=1,
                product="Lem",
                invoice_quantity=Decimal(10),
                ordered=Decimal(20),
                received=Decimal(8),
                invoiced=Decimal(0),
                excluded_duplicate=Decimal(0),
                already_on_invoice=Decimal(0),
                available=Decimal(8),
                excess=Decimal(2),
                problem="exceeds_received",
            ),
            "ditagih 10, sedangkan yang sudah diterima dan belum ditagih 8.",
        ),
        (
            LineMatchEvidence(
                line=2,
                vendor_code=None,
                description="Ongkos kirim",
                problem="no_match",
                purchase_order="P1",
            ),
            "'Ongkos kirim' tidak tercantum pada pesanan P1.",
        ),
        (
            TaxAmountEvidence(
                field="ppn",
                subtotal=Decimal(100),
                printed=Decimal(12),
                expected=Decimal(11),
                difference=Decimal(1),
                tolerance=Decimal(1),
            ),
            "PPN pada faktur Rp12, seharusnya Rp11.",
        ),
        (
            LineAmountEvidence(
                line=1,
                quantity=Decimal(2),
                unit_price=Decimal(5),
                printed=Decimal(11),
                computed=Decimal(10),
                difference=Decimal(1),
                tolerance=Decimal(1),
            ),
            "Baris 1: 2 × Rp5 = Rp10, tercetak Rp11.",
        ),
        (
            PurchaseOrderEvidence(printed="P9", candidates=("P1", "P2")),
            "P9 tidak kami temukan. Pesanan yang mungkin terkait: P1, P2.",
        ),
        (
            PurchaseOrderEvidence(printed="P9", found="P9", state="cancel"),
            "Pesanan pembelian P9 berstatus batal.",
        ),
        (PurchaseOrderEvidence(printed=None), "tidak mencantumkan nomor pesanan pembelian"),
    ],
)
def test_vendor_sentences_come_from_the_evidence(evidence: Any, expected: str) -> None:
    finding = Finding(
        rule="BR-09", reason=ReasonCode.QUANTITY_VARIANCE, message="internal", evidence=evidence
    )
    text = vendor_sentence(finding)
    assert expected in text and "internal" not in text


def test_templates_v1_stay_as_they_were() -> None:
    folder = resources.files("faktur_beres.tools").joinpath("email_templates", "v1")
    digest = hashlib.sha256()
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        digest.update(entry.name.encode() + b"\0" + entry.read_bytes() + b"\0")
    assert digest.hexdigest() == V1_FINGERPRINT


def test_the_message_carries_a_date_header(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    class Smtp:
        def __init__(self, *_a: Any, **_k: Any) -> None: ...

        def __enter__(self) -> "Smtp":
            return self

        def __exit__(self, *_a: Any) -> None: ...

        def send_message(self, message: Any) -> None:
            sent.append(message)

    monkeypatch.setattr("faktur_beres.tools.email_tool.smtplib.SMTP", Smtp)
    MailpitMailer(smtp_host="h", smtp_port=1, api_url="http://h").send(mail())
    assert sent[0]["Date"] and sent[0]["Message-ID"] == mail().message_id


def test_approver_note_is_cut_to_the_limit() -> None:
    body = clarification_body(
        invoice_number="INV/1", findings=[], approver_note="y" * (MAX_NOTE + 50)
    )
    assert "y" * MAX_NOTE in body and "y" * (MAX_NOTE + 1) not in body


def test_notice_without_clarification_text() -> None:
    finding = Finding(
        rule="BR-11", reason=ReasonCode.DUPLICATE_CONFIRMED, message="Sama dengan #9."
    )
    notice = duplicate_notice(
        invoice_id="FB-000123",
        invoice_number="INV/1",
        sender="ap@arunika.test",
        to=VENDOR,
        key=KEY,
        findings=[finding],
    )
    assert "Pemberitahuan" in notice.subject and "Mohon periksa" in notice.body
