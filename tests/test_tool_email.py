"""``email_tool``: recipient guard, templates, idempotence (no mail server)."""

from dataclasses import dataclass, field

import pytest

from faktur_beres.domain.findings import Finding
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.tools.email_tool import (
    MAX_NOTE,
    MailUnavailable,
    OutgoingEmail,
    RecipientRefused,
    clarification_body,
    clarification_email,
    duplicate_notice,
    message_id_for,
    send_checked,
)

VENDOR = "tagihan@vendor.test"
KEY = "a" * 64
PRICE = Finding(rule="BR-08", reason=ReasonCode.PRICE_VARIANCE, message="Baris 1: harga naik.")
TAX = Finding(
    rule="BR-10", reason=ReasonCode.TAX_INVALID, message="NPWP 0990427957220000 tidak valid."
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
def test_one_section_per_reason_b_and_digits_are_masked() -> None:
    body = clarification_body(
        invoice_number="INV/1", findings=[PRICE, TAX, NEW, NOTE], approver_note=None
    )
    assert "Harga satuan" in body and "Data pajak" in body
    assert "0990427957220000" not in body and "0990********0000" in body
    assert "Vendor baru." not in body and "catatan internal" not in body


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
    assert "Sama dengan #9." in notice.body and "Pemberitahuan" in notice.subject
