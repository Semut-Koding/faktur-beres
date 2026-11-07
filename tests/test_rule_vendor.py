"""BR-05: vendor identification."""

import pytest

from factories import OTHER_VENDOR, VENDOR, invoice
from faktur_beres.domain.invoice import Channel, IntakeContext, Seller
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.vendor import (
    VendorCandidates,
    VendorStatus,
    identify_vendor,
    invoice_emails,
)

pytestmark = pytest.mark.aturan("BR-05")

UPLOAD = IntakeContext(channel=Channel.UPLOAD)


def seller(**changes: object) -> Seller:
    data = {"name": VENDOR.name, "npwp": VENDOR.vat, "email": VENDOR.email}
    data.update(changes)
    return Seller(**data)


def test_valid_npwp_identifies_the_vendor() -> None:
    result = identify_vendor(
        invoice(), UPLOAD, VendorCandidates(by_npwp=(VENDOR,), by_name=(VENDOR,))
    )
    assert (result.status, result.vendor, result.method) == (
        VendorStatus.IDENTIFIED,
        VENDOR,
        "npwp",
    )
    assert result.findings == ()


def test_npwp_with_separators_is_compared_as_digits() -> None:
    inv = invoice(seller=seller(npwp="0990.4279.5722-0000"))
    result = identify_vendor(inv, UPLOAD, VendorCandidates(by_npwp=(VENDOR,)))
    assert result.method == "npwp"


@pytest.mark.parametrize("field", ["by_name", "by_email"])
def test_npwp_of_one_vendor_and_name_or_email_of_another_conflict(field: str) -> None:
    candidates = VendorCandidates(by_npwp=(VENDOR,), **{field: (OTHER_VENDOR,)})
    inv = invoice(seller=seller(name=OTHER_VENDOR.name, email=OTHER_VENDOR.email))
    result = identify_vendor(inv, UPLOAD, candidates)
    assert result.status == VendorStatus.CONFLICT
    assert [f.reason for f in result.findings] == [ReasonCode.IDENTITY_MISMATCH]
    assert result.vendor is None


def test_missing_npwp_falls_back_to_name() -> None:
    inv = invoice(seller=seller(npwp=None, name="P.T.  Pemasok  Fiktif"))
    result = identify_vendor(inv, UPLOAD, VendorCandidates(by_name=(VENDOR,)))
    assert (result.status, result.method) == (VendorStatus.IDENTIFIED, "name_email")


def test_valid_but_unknown_npwp_falls_back_to_name_and_email() -> None:
    inv = invoice(seller=seller(npwp="0990992125111000"))
    result = identify_vendor(inv, UPLOAD, VendorCandidates(by_name=(VENDOR,), by_email=(VENDOR,)))
    assert (result.vendor, result.method) == (VENDOR, "name_email")


def test_invalid_npwp_is_never_used_for_matching() -> None:
    # 15 digits (old format) is invalid; a reader returning a vendor for it is ignored.
    inv = invoice(seller=seller(npwp="990427957220000", name="Nama Lain"))
    result = identify_vendor(inv, UPLOAD, VendorCandidates(by_npwp=(VENDOR,)))
    assert result.status == VendorStatus.NOT_REGISTERED


def test_email_sender_counts_on_the_email_channel_only() -> None:
    inv = invoice(seller=seller(npwp=None, name="Nama Tercetak Berbeda", email=None))
    email = IntakeContext(channel=Channel.EMAIL, sender_email="Tagihan <TAGIHAN@pemasok.test>")
    assert invoice_emails(inv, email) == ("tagihan@pemasok.test",)
    assert identify_vendor(inv, email, VendorCandidates(by_email=(VENDOR,))).vendor == VENDOR
    upload = IntakeContext(channel=Channel.UPLOAD, sender_email="tagihan@pemasok.test")
    assert invoice_emails(inv, upload) == ()
    result = identify_vendor(inv, upload, VendorCandidates(by_email=(VENDOR,)))
    assert result.status == VendorStatus.NOT_REGISTERED


def test_no_candidate_means_not_registered_with_a_note() -> None:
    result = identify_vendor(
        invoice(seller=seller(npwp="0990487266919000")), UPLOAD, VendorCandidates()
    )
    assert result.status == VendorStatus.NOT_REGISTERED
    assert [f.reason for f in result.findings] == [None]
    assert "belum terverifikasi" in result.findings[0].message


def test_two_candidates_are_ambiguous() -> None:
    candidates = VendorCandidates(by_name=(VENDOR,), by_email=(OTHER_VENDOR,))
    inv = invoice(seller=seller(npwp=None, email=OTHER_VENDOR.email))
    result = identify_vendor(inv, UPLOAD, candidates)
    assert result.status == VendorStatus.AMBIGUOUS
    assert [f.reason for f in result.findings] == [ReasonCode.IDENTITY_MISMATCH]


def test_loose_reader_results_are_filtered_again() -> None:
    inv = invoice(seller=seller(npwp=None, email=None))
    result = identify_vendor(inv, UPLOAD, VendorCandidates(by_name=(VENDOR, OTHER_VENDOR)))
    assert result.vendor == VENDOR
