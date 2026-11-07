"""BR-10: tax_check on the printed figures."""

from decimal import Decimal

import pytest
from stdnum.id import npwp as stdnum_npwp

from factories import COMPANY, OTHER_VENDOR, POLICY, VENDOR, invoice, line
from faktur_beres.domain.findings import (
    IdentityEvidence,
    TaxAmountEvidence,
    TaxIdEvidence,
    TransactionCodeEvidence,
    reasons_of,
)
from faktur_beres.domain.invoice import Party, Seller
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.tax import check_seller_identity, check_tax
from faktur_beres.domain.rules.vendor import VendorIdentification, VendorStatus

pytestmark = pytest.mark.aturan("BR-10")


def seller(npwp: str | None) -> Seller:
    return Seller(name=VENDOR.name, npwp=npwp, email=VENDOR.email)


def problems(inv) -> list[tuple[str, str]]:
    return [
        (f.evidence.field, f.evidence.problem)
        for f in check_tax(inv, COMPANY, POLICY)
        if isinstance(f.evidence, TaxIdEvidence)
    ]


def test_valid_invoice_passes() -> None:
    assert check_tax(invoice([line(1200, 18750)]), COMPANY, POLICY) == ()


@pytest.mark.parametrize(
    ("npwp", "problem"),
    [
        (None, "missing"),
        ("  ", "missing"),
        ("990427957220000", "length"),  # old 15-digit format
        ("09904279572200001", "length"),
        ("0990-4279-572X-0000", "not_digits"),
    ],
)
def test_seller_npwp_problems(npwp: str | None, problem: str) -> None:
    assert problems(invoice(seller=seller(npwp))) == [("npwp_penjual", problem)]


def test_seller_npwp_check_digit() -> None:
    wrong = "0990427958220000"
    assert len(wrong) == 16 and not stdnum_npwp.is_valid(wrong)
    assert problems(invoice(seller=seller(wrong))) == [("npwp_penjual", "check_digit")]
    assert problems(invoice(seller=seller("09.90.427957-2.20000"))) == []


def test_buyer_npwp_must_be_the_company() -> None:
    other = invoice(buyer=Party(name=COMPANY.name, npwp=OTHER_VENDOR.vat))
    assert problems(other) == [("npwp_pembeli", "mismatch")]
    assert problems(invoice(buyer=Party(name=COMPANY.name))) == [("npwp_pembeli", "missing")]
    assert problems(invoice(buyer=None)) == [("npwp_pembeli", "missing")]


@pytest.mark.parametrize(
    ("nsfp", "problem"),
    [(None, "missing"), ("040.002-26.64847281", "length"), ("0400260000721509", "length")],
)
def test_nsfp_problems(nsfp: str | None, problem: str) -> None:
    assert problems(invoice(nsfp=nsfp)) == [("nsfp", problem)]


def test_nsfp_with_separators_is_valid() -> None:
    assert problems(invoice(nsfp="04.00.26.00007215090")) == []


def test_other_transaction_code_is_only_a_note() -> None:
    (finding,) = check_tax(invoice(nsfp="07002600007215090"), COMPANY, POLICY)
    assert finding.is_note
    assert isinstance(finding.evidence, TransactionCodeEvidence)
    assert finding.evidence.code == "07"


def test_ppn_on_the_selling_price_is_tax_invalid() -> None:
    # 12% x harga jual instead of 12% x 11/12 x harga jual.
    inv = invoice([line(50, 165000)], dpp_nilai_lain=8250000, vat=990000, total=9240000)
    findings = check_tax(inv, COMPANY, POLICY)
    assert reasons_of(findings) == (ReasonCode.TAX_INVALID,)
    by_field = {f.evidence.field: f.evidence for f in findings}
    assert isinstance(by_field["dpp_nilai_lain"], TaxAmountEvidence)
    assert by_field["dpp_nilai_lain"].expected == Decimal("7562500.00")
    assert by_field["ppn"].expected == Decimal("907500.00")
    assert by_field["ppn"].difference == Decimal("82500.00")


@pytest.mark.parametrize(("off", "triggers"), [(1, False), (-1, False), (2, True)])
def test_ppn_tolerance_is_one_rupiah(off: int, triggers: bool) -> None:
    inv = invoice([line(50, 165000)], dpp_nilai_lain=None)
    changed = inv.model_copy(update={"vat": inv.vat + off, "total": inv.total + off})
    assert bool(check_tax(changed, COMPANY, POLICY)) is triggers


def test_without_printed_dpp_only_ppn_is_checked() -> None:
    assert check_tax(invoice(dpp_nilai_lain=None), COMPANY, POLICY) == ()


def test_base_is_the_printed_subtotal() -> None:
    # Line 2 printed 3.240.000 instead of 2.940.000; subtotal, DPP, and PPN follow the printed
    # subtotal of 8.640.000, so BR-10 has nothing to say (BR-03 reports the line).
    inv = invoice([line(40, 135000), line(30, 98000, amount=3240000)])
    assert inv.subtotal == 8640000
    assert check_tax(inv, COMPANY, POLICY) == ()


def identified(method: str) -> VendorIdentification:
    return VendorIdentification(status=VendorStatus.IDENTIFIED, vendor=VENDOR, method=method)


def test_valid_npwp_of_someone_else_is_identity_mismatch() -> None:
    inv = invoice(seller=seller("0990992125111000"))
    finding = check_seller_identity(inv, identified("name_email"))
    assert finding is not None and finding.reason == ReasonCode.IDENTITY_MISMATCH
    assert isinstance(finding.evidence, IdentityEvidence)
    assert check_tax(inv, COMPANY, POLICY) == ()  # valid NPWP: no tax_invalid


def test_invalid_or_matching_npwp_is_no_identity_mismatch() -> None:
    assert check_seller_identity(invoice(seller=seller(None)), identified("name_email")) is None
    assert (
        check_seller_identity(invoice(seller=seller("990992125111000")), identified("name_email"))
        is None
    )
    assert check_seller_identity(invoice(), identified("name_email")) is None
    assert (
        check_seller_identity(invoice(seller=seller("0990992125111000")), identified("npwp"))
        is None
    )
