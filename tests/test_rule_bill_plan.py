"""BR-18: the draft bill plan."""

from datetime import date
from decimal import Decimal

import pytest

from factories import POLICY, VENDOR, VENDOR_ACCOUNT, invoice, line, po, po_line
from faktur_beres.domain.erp import BankAccountRecord
from faktur_beres.domain.invoice import BankAccount
from faktur_beres.domain.rules.bill_plan import BillPlan, choose_bank_account, plan_bill
from faktur_beres.domain.rules.lines import match_lines

pytestmark = pytest.mark.aturan("BR-18")

PO = po(
    [
        po_line(1, 501, qty=60, price=185000),
        po_line(
            2,
            502,
            qty=40,
            price=135000,
            code="INT-02",
            vendor_codes=("KD-02",),
            name="Barang Uji Dua",
        ),
    ]
)
INV = invoice(
    [line(60, 185900), line(40, 135000, code="KD-02", description="Barang Uji Dua")],
    invoice_number="TWL/INV/2610/0168",
    invoice_date=date(2026, 10, 29),
)


def plan(inv=INV, accounts=(VENDOR_ACCOUNT,)) -> BillPlan | None:
    return plan_bill(inv, VENDOR, PO, match_lines(inv, PO, POLICY), accounts)


def test_plan_takes_header_from_po_and_amounts_from_invoice() -> None:
    p = plan()
    assert p is not None
    assert (p.move_type, p.partner_id, p.invoice_origin, p.invoice_payment_term_id) == (
        "in_invoice",
        VENDOR.id,
        "P00900",
        4,
    )
    assert (p.ref, p.invoice_date, p.currency_id, p.partner_bank_id) == (
        "TWL/INV/2610/0168",
        date(2026, 10, 29),
        12,
        VENDOR_ACCOUNT.id,
    )
    first = p.lines[0]
    assert (first.purchase_line_id, first.product_id, first.product_uom_id, first.tax_ids) == (
        1,
        501,
        1,
        (2,),
    )
    assert (first.quantity, first.price_unit) == (60, 185900)  # invoice price, not PO price
    assert first.name == "[INT-01] Barang Uji Satu"
    assert p.expected_total == INV.total and p.expected_untaxed == INV.subtotal


def test_planned_total_follows_the_policy_rate() -> None:
    p = plan()
    assert p is not None
    assert p.untaxed == 60 * 185900 + 40 * 135000
    assert p.planned_total(POLICY) == Decimal(p.untaxed) * Decimal("1.11")


def test_odoo_values_create_all_lines_in_one_call() -> None:
    p = plan()
    assert p is not None
    values = p.odoo_values()
    assert "fiscal_position_id" not in values  # empty values are left to Odoo
    commands = values["invoice_line_ids"]
    assert [c[:2] for c in commands] == [[0, 0], [0, 0]]
    assert commands[0][2]["tax_ids"] == [[6, 0, [2]]]
    assert set(commands[0][2]) == {
        "display_type",
        "product_id",
        "product_uom_id",
        "tax_ids",
        "purchase_line_id",
        "name",
        "quantity",
        "price_unit",
    }


def test_no_plan_without_a_counterpart_for_every_line() -> None:
    extra = invoice([*INV.lines, line(1, 450000, code="KRM-01", description="Ongkos kirim")])
    assert plan(extra) is None


SECOND = BankAccountRecord(id=102, partner_id=VENDOR.id, number="9988776655", bank_name="Lain")


def test_bank_account_comes_from_odoo_never_from_the_invoice() -> None:
    printed_other = invoice(bank_account=BankAccount(bank="X", number="1111222233"))
    assert choose_bank_account(printed_other, [VENDOR_ACCOUNT]) == VENDOR_ACCOUNT.id
    assert choose_bank_account(printed_other, [VENDOR_ACCOUNT, SECOND]) is None
    printed_second = invoice(bank_account=BankAccount(bank="Lain", number="9988-7766-55"))
    assert choose_bank_account(printed_second, [VENDOR_ACCOUNT, SECOND]) == SECOND.id
    assert choose_bank_account(invoice(bank_account=None), [VENDOR_ACCOUNT, SECOND]) is None
    assert choose_bank_account(printed_other, []) is None
