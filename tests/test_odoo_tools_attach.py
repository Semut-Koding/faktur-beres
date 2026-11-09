"""attach_file, post_note, and the NSFP field check (no network)."""

import hashlib
from datetime import date

import pytest

from fake_odoo import FakeOdoo, gateway
from faktur_beres.tools.errors import ForbiddenOperation, VerificationFailed
from faktur_beres.tools.odoo_tools import (
    attach_file,
    bill_note_text,
    note_marker,
    post_note,
    vendor_bill_nsfp_field,
)

CONTENT = b"%PDF-1.4 faktur"
SHA1 = hashlib.sha1(CONTENT).hexdigest()
KEY = "b" * 64


def odoo(*, attachments=(), messages=(), writable=True) -> FakeOdoo:
    return FakeOdoo(
        {
            "account.move/read": [{"id": 42, "move_type": "in_invoice", "state": "draft"}],
            "account.move/has_access": writable,
            "ir.attachment/search_read": list(attachments),
            "ir.attachment/create": [9],
            "ir.attachment/read": [
                {"id": 9, "checksum": SHA1, "res_model": "account.move", "res_id": 42}
            ],
            "mail.message/search_read": list(messages),
            "account.move/message_post": [11],
            "account.move/fields_get": {"l10n_id_kode_transaksi": {"type": "selection"}},
        }
    )


def test_attach_creates_once_then_reuses() -> None:
    fake = odoo()
    first = attach_file(gateway(fake), 42, "faktur.pdf", CONTENT, "application/pdf")
    assert (first.attachment_id, first.created, first.checksum) == (9, True, SHA1)
    again = odoo(attachments=[{"id": 9, "checksum": SHA1}])
    second = attach_file(gateway(again), 42, "faktur.pdf", CONTENT, "application/pdf")
    assert (second.attachment_id, second.created) == (9, False)
    assert "ir.attachment/create" not in again.methods()


def test_same_name_other_content_is_a_new_attachment() -> None:
    fake = odoo(attachments=[{"id": 8, "checksum": "0" * 40}])
    assert attach_file(gateway(fake), 42, "faktur.pdf", CONTENT, "application/pdf").created


def test_no_attachment_or_note_outside_agent_drafts() -> None:
    fake = odoo(writable=False)
    with pytest.raises(ForbiddenOperation):
        attach_file(gateway(fake), 42, "faktur.pdf", CONTENT, "application/pdf")
    with pytest.raises(ForbiddenOperation):
        post_note(gateway(fake), 42, "catatan", KEY)
    assert not [p for p in fake.methods() if p.endswith(("/create", "/message_post"))]


def test_note_carries_the_key_and_is_posted_once() -> None:
    fake = odoo()
    first = post_note(gateway(fake), 42, "Draft dibuat.", KEY)
    assert (first.message_id, first.created, first.marker) == (11, True, f"[faktur-beres {KEY}]")
    body = next(b for p, b in fake.requests if p == "account.move/message_post")["body"]
    assert body.endswith(first.marker)
    again = odoo(messages=[{"id": 11}])
    second = post_note(gateway(again), 42, "Draft dibuat.", KEY)
    assert (second.message_id, second.created) == (11, False)
    assert "account.move/message_post" not in again.methods()


def test_key_must_be_a_sha256() -> None:
    with pytest.raises(ValueError):
        note_marker("kunci-pendek")


def test_nsfp_field_and_note_text() -> None:
    assert vendor_bill_nsfp_field(gateway(odoo())) is None
    text = bill_note_text("KSM/INV/2026/10/0458", "04002600007215090", date(2026, 10, 25), "f.pdf")
    assert "NSFP 04002600007215090, tanggal 2026-10-25" in text
    assert "(berkas f.pdf)" in text and "tidak memposting" in text
    assert "NSFP" not in bill_note_text("X", None, None, None)


@pytest.mark.parametrize(
    "stored",
    [
        {"id": 9, "checksum": "0" * 40, "res_model": "account.move", "res_id": 42},
        {"id": 9, "checksum": SHA1, "res_model": "account.move", "res_id": 43},
        {"id": 9, "checksum": SHA1, "res_model": "purchase.order", "res_id": 42},
    ],
)
def test_attachment_stored_differently_fails_verification(stored: dict) -> None:
    fake = odoo()
    fake.answers["ir.attachment/read"] = [stored]
    with pytest.raises(VerificationFailed) as exc:
        attach_file(gateway(fake), 42, "faktur.pdf", CONTENT, "application/pdf")
    assert exc.value.kind == "verification_failed"


def test_attachment_that_cannot_be_read_back_fails_verification() -> None:
    fake = odoo()
    fake.answers["ir.attachment/read"] = []
    with pytest.raises(VerificationFailed):
        attach_file(gateway(fake), 42, "faktur.pdf", CONTENT, "application/pdf")
