"""Tests for findings, evidence, ERP records, and the message formatting helpers."""

from datetime import date
from decimal import Decimal
from fractions import Fraction

from faktur_beres.domain.erp import (
    BankAccountRecord,
    BillRecord,
    BillState,
    NoOtherInvoices,
    PoState,
    PurchaseOrderLineRecord,
    PurchaseOrderRecord,
)
from faktur_beres.domain.findings import (
    Finding,
    PriceEvidence,
    number,
    percent,
    reasons_of,
    rupiah,
    to_decimal,
)
from faktur_beres.domain.reasons import ReasonCode


def test_finding_with_evidence_round_trips() -> None:
    finding = Finding(
        rule="BR-08",
        reason=ReasonCode.PRICE_VARIANCE,
        message="harga di atas toleransi",
        evidence=PriceEvidence(
            line=2,
            po_line_id=7,
            product="Hot Melt Adhesive",
            invoice_price=Decimal(60840),
            po_price=Decimal(58500),
            limit=Decimal("59085.00"),
            difference=Decimal(2340),
            difference_percent=Decimal("4.00"),
        ),
    )
    again = Finding.model_validate_json(finding.model_dump_json())
    assert again == finding
    assert isinstance(again.evidence, PriceEvidence)
    assert not again.is_note
    assert Finding(rule="BR-08", message="catatan").is_note


def test_reasons_are_distinct_and_in_spec_order() -> None:
    findings = [
        Finding(rule="BR-15", reason=ReasonCode.PROMPT_INJECTION, message="a"),
        Finding(rule="BR-08", reason=ReasonCode.PRICE_VARIANCE, message="b"),
        Finding(rule="BR-15", reason=ReasonCode.PROMPT_INJECTION, message="c"),
        Finding(rule="BR-08", message="catatan"),
    ]
    assert reasons_of(findings) == (ReasonCode.PRICE_VARIANCE, ReasonCode.PROMPT_INJECTION)


def test_formatting_helpers() -> None:
    assert rupiah(Decimal(24975000)) == "Rp24.975.000"
    assert rupiah(Decimal("1234500.5")) == "Rp1.234.500,50"
    assert rupiah(Decimal(-300000)) == "-Rp300.000"
    assert number(Decimal("1680.000")) == "1.680"
    assert number(Decimal("12.5")) == "12,5"
    assert percent(Decimal("4")) == "+4,00%"
    assert percent(Decimal("-2.564")) == "-2,56%"
    assert to_decimal(Fraction(11, 12) * 8640000) == Decimal("7920000.00")
    assert to_decimal(Fraction(1, 3)) == Decimal("0.33")


def test_records() -> None:
    line = PurchaseOrderLineRecord(
        id=1,
        product_id=2,
        product_name="Barang",
        description="[X] Barang",
        uom_id=1,
        uom_name="kg",
        quantity=Decimal(2100),
        qty_received=Decimal(1680),
        qty_invoiced=Decimal(0),
        price_unit=Decimal(11200),
    )
    assert line.open_quantity == 1680
    po = PurchaseOrderRecord(
        id=1, number="P00027", vendor_id=3, state=PoState.PURCHASE, locked=True, lines=(line,)
    )
    assert po.confirmed
    assert not po.model_copy(update={"state": PoState.CANCEL}).confirmed
    acc = BankAccountRecord(id=1, partner_id=3, number="651-008-2689")
    assert acc.digits == "6510082689"
    bill = BillRecord(
        id=1,
        vendor_id=3,
        state=BillState.POSTED,
        amount_total=Decimal(1),
        invoice_date=date(2026, 10, 1),
    )
    assert BillRecord.model_validate_json(bill.model_dump_json()) == bill
    assert NoOtherInvoices().other_invoices(3, exclude_invoice_id=None) == ()
