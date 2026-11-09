"""BR-12: bank account on the invoice against the vendor's accounts in Odoo."""

import pytest

from factories import OTHER_ACCOUNT, VENDOR, VENDOR_ACCOUNT, invoice
from faktur_beres.domain.erp import BankAccountRecord, BankRecord
from faktur_beres.domain.findings import BankAccountEvidence
from faktur_beres.domain.invoice import BankAccount
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.bank import check_bank_account

pytestmark = pytest.mark.aturan("BR-12")


def check(number: str | None, bank: str | None = "Bank Fiktif Satu", owners=(), banks=()):
    account = None if number is None else BankAccount(bank=bank, number=number, holder="x")
    return check_bank_account(
        invoice(bank_account=account), VENDOR, [VENDOR_ACCOUNT], owners, banks
    )


def test_registered_account_passes() -> None:
    assert check("5450717927") == ()


def test_separators_are_normalised_with_a_note() -> None:
    (note,) = check("545-071-7927")
    assert note.is_note and "******7927" in note.message


def test_other_number_at_the_same_bank_is_mismatch() -> None:
    (finding,) = check("5502719597")
    assert finding.reason == ReasonCode.BANK_ACCOUNT_MISMATCH
    assert isinstance(finding.evidence, BankAccountEvidence)
    assert finding.evidence.problem == "not_registered"
    assert finding.evidence.registered_digits == ("5450717927",)
    assert "5502719597" not in finding.message  # BR-24: last four digits only


def test_account_of_another_vendor_is_mismatch() -> None:
    (finding,) = check(OTHER_ACCOUNT.number, owners=[OTHER_ACCOUNT])
    assert finding.reason == ReasonCode.BANK_ACCOUNT_MISMATCH
    assert finding.evidence.problem == "other_owner"
    assert finding.evidence.other_owner_ids == (OTHER_ACCOUNT.partner_id,)


def test_registered_number_printed_with_another_registered_bank_is_mismatch() -> None:
    banks = [BankRecord(id=1, name="Bank Fiktif Satu"), BankRecord(id=2, name="Bank Fiktif Dua")]
    (finding,) = check("5450717927", bank="Bank Fiktif Dua", banks=banks)
    assert finding.reason == ReasonCode.BANK_ACCOUNT_MISMATCH
    ev = finding.evidence
    assert (ev.problem, ev.registered_bank, ev.matched_bank) == (
        "different_bank",
        "Bank Fiktif Satu",
        "Bank Fiktif Dua",
    )


def test_unknown_bank_name_with_the_registered_number_is_a_note() -> None:
    (note,) = check("5450717927", bank="Bank Fiktif Dua")  # not a bank registered in Odoo
    assert note.is_note and "dicatat" in note.message


def test_bank_name_is_compared_after_normalisation() -> None:
    assert check("5450717927", bank="BANK FIKTIF SATU.") == ()
    assert check("5450717927", bank=None) == ()


def test_invoice_without_account_is_no_exception() -> None:
    (note,) = check(None)
    assert note.is_note


MANDALA = BankAccountRecord(
    id=200, partner_id=VENDOR.id, number="5450717927", bank_name="Bank Mandala Raya", holder="x"
)
REGISTERED = [
    BankRecord(id=2, name="Bank Mandala Raya"),
    BankRecord(id=3, name="Bank Kencana Utama"),
    BankRecord(id=4, name="Bank Kirana Digital"),
]


@pytest.mark.parametrize(
    ("printed", "result"),
    [
        ("PT Bank Mandala Raya Tbk", "lulus"),
        ("BANK MANDALA RAYA", "lulus"),
        ("Bank Mandala Raya (Persero)", "lulus"),
        ("Mandala Raya", "lulus"),
        ("Bank Mandala", "catatan"),
        ("Bank Kencana Utama", "C3"),
    ],
)
def test_six_spellings_of_the_bank_of_a_registered_account(printed: str, result: str) -> None:
    inv = invoice(bank_account=BankAccount(bank=printed, number="5450717927", holder="x"))
    findings = check_bank_account(inv, VENDOR, [MANDALA], [], REGISTERED)
    if result == "lulus":
        assert findings == ()
    elif result == "catatan":
        assert [f.is_note for f in findings] == [True]
    else:
        (finding,) = findings
        assert finding.reason == ReasonCode.BANK_ACCOUNT_MISMATCH
        assert finding.evidence.matched_bank == "Bank Kencana Utama"
