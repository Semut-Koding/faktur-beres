"""BR-11: duplicates against Odoo bills and other Faktur Beres invoices."""

from datetime import date
from decimal import Decimal

import pytest

from factories import OTHER_VENDOR, POLICY, VENDOR, bill, invoice, line
from faktur_beres.domain.erp import BillLineRecord, BillState, RegisteredInvoice
from faktur_beres.domain.findings import DuplicateEvidence
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.duplicates import find_duplicates

pytestmark = pytest.mark.aturan("BR-11")

INV = invoice(
    [line(50, 192000), line(45, 185000, code="KD-02", description="Dua")],
    invoice_number="TWL/INV/2610/0153",
    invoice_date=date(2026, 10, 16),
)


def run(bills, others=()):
    return find_duplicates(INV, VENDOR.id, bills, others, POLICY)


def reasons(result):
    return [(f.reason, f.evidence.key) for f in result.findings]


def test_exact_copy_is_confirmed() -> None:
    result = run([bill(ref="TWL/INV/2610/0153", total=INV.total, invoice_date=date(2026, 10, 16))])
    assert reasons(result) == [(ReasonCode.DUPLICATE_CONFIRMED, "number")]
    assert len(result.comparators) == 1


def test_number_with_other_separators_is_the_same_key() -> None:
    result = run([bill(ref="twl-inv-2610-0153", total=INV.total + 1)])  # total within Rp1
    assert reasons(result) == [(ReasonCode.DUPLICATE_CONFIRMED, "number")]


def test_same_number_other_total_is_suspected() -> None:
    result = run([bill(ref="TWL/INV/2610/0153", total=INV.total - 1650000)])
    assert reasons(result) == [(ReasonCode.DUPLICATE_SUSPECTED, "number")]
    assert isinstance(result.findings[0].evidence, DuplicateEvidence)
    assert result.findings[0].evidence.difference == 1650000


def test_same_date_and_total_with_other_number_is_suspected() -> None:
    result = run([bill(ref="TWL/INV/2610/0150", total=INV.total, invoice_date=date(2026, 10, 16))])
    assert reasons(result) == [(ReasonCode.DUPLICATE_SUSPECTED, "date_total")]


def test_same_number_from_another_vendor_is_not_a_duplicate() -> None:
    other = bill(vendor_id=OTHER_VENDOR.id, ref="TWL/INV/2610/0153", total=INV.total)
    assert run([other]).findings == ()


def test_cancelled_bills_do_not_count_but_drafts_do() -> None:
    cancelled = bill(ref="TWL/INV/2610/0153", total=INV.total, state=BillState.CANCEL)
    assert run([cancelled]).findings == ()
    draft = bill(ref="TWL/INV/2610/0153", total=INV.total, state=BillState.DRAFT)
    assert reasons(run([draft])) == [(ReasonCode.DUPLICATE_CONFIRMED, "number")]


def test_unrelated_bill_is_not_a_duplicate() -> None:
    assert run([bill(ref="TWL/INV/2608/0097", total=26973000)]).findings == ()


def test_other_invoices_in_faktur_beres_are_compared_too() -> None:
    other = RegisteredInvoice(
        invoice_id="FB-000007",
        vendor_id=VENDOR.id,
        invoice_number="TWL INV 2610 0153",
        invoice_date=date(2026, 10, 16),
        total=INV.total,
    )
    result = run([], [other])
    assert reasons(result) == [(ReasonCode.DUPLICATE_CONFIRMED, "number")]
    assert result.findings[0].evidence.source == "faktur_beres"
    assert result.comparators == ()


def test_comparator_quantities_per_po_line() -> None:
    lines = [
        BillLineRecord(id=1, purchase_line_id=31, quantity=Decimal(50), price_unit=Decimal(1)),
        BillLineRecord(id=2, purchase_line_id=32, quantity=Decimal(45), price_unit=Decimal(1)),
        BillLineRecord(id=3, purchase_line_id=None, quantity=Decimal(1), price_unit=Decimal(1)),
    ]
    result = run([bill(ref="TWL/INV/2610/0153", total=INV.total, lines=lines)])
    assert result.excluded_quantities() == {31: 50, 32: 45}
