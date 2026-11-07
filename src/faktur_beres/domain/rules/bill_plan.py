"""BR-18: the plan of the one draft vendor bill (route A, or route C after approval).

The values mirror what Odoo 19 prepares when a bill is made from a PO
(``purchase.order._prepare_invoice`` and ``purchase.order.line._prepare_account_move_line``):

* header: ``move_type`` ``in_invoice``, ``partner_id``, currency, fiscal position,
  ``invoice_origin`` = PO number, and ``invoice_payment_term_id`` from the PO (the API does not run
  the UI onchange, so the plan fills it); ``ref`` = invoice number as printed and ``invoice_date``
  = invoice date; ``partner_bank_id`` from the vendor's accounts **in Odoo**, never from the
  invoice: the only account, or with several the one equal to the printed number, else empty;
* lines: ``display_type`` product, ``product_id``, ``product_uom_id`` and ``tax_ids`` from the PO
  line, ``purchase_line_id``, the PO line's name, and ``quantity`` and ``price_unit`` from the
  invoice.

Not copied from Odoo's preparation: ``narration`` (the PO's internal note) and ``company_id`` (the
user's company is the only company). The plan keeps the printed total for the check after the
action (the draft's total must equal it within Rp1). A plan exists only when every invoice line
matched its own PO line.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any, Literal

from pydantic import Field

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.erp import BankAccountRecord, PurchaseOrderRecord, VendorRecord
from faktur_beres.domain.findings import to_decimal
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.normalize import account_digits
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.rules.lines import LinesResult

RULE = "BR-18"


class BillLinePlan(DomainModel):
    display_type: Literal["product"] = "product"
    product_id: int
    product_uom_id: int
    tax_ids: tuple[int, ...]
    purchase_line_id: int
    name: str
    quantity: Decimal
    price_unit: Decimal
    # Traceability only; never sent to Odoo.
    po_number: str
    product_code: str | None = None

    def odoo_values(self) -> dict[str, Any]:
        return {
            "display_type": self.display_type,
            "product_id": self.product_id,
            "product_uom_id": self.product_uom_id,
            "tax_ids": [[6, 0, list(self.tax_ids)]],
            "purchase_line_id": self.purchase_line_id,
            "name": self.name,
            "quantity": self.quantity,
            "price_unit": self.price_unit,
        }


class BillPlan(DomainModel):
    move_type: Literal["in_invoice"] = "in_invoice"
    partner_id: int
    currency_id: int | None = None
    fiscal_position_id: int | None = None
    invoice_origin: str
    invoice_payment_term_id: int | None = None
    ref: str
    invoice_date: date
    partner_bank_id: int | None = None
    lines: tuple[BillLinePlan, ...] = Field(min_length=1)
    expected_total: Decimal  # printed total, checked after the action
    expected_untaxed: Decimal  # printed subtotal

    @property
    def untaxed(self) -> Decimal:
        return sum((line.quantity * line.price_unit for line in self.lines), Decimal(0))

    def planned_total(self, policy: PolicyConfig) -> Decimal:
        """Untaxed amount plus PPN at the policy rate (12% × 11/12), to the cent."""
        untaxed = Fraction(self.untaxed)
        return to_decimal(untaxed + untaxed * policy.tax.effective_rate)

    def odoo_values(self) -> dict[str, Any]:
        """``account.move`` values for one ``create`` call with all lines."""
        values: dict[str, Any] = {
            "move_type": self.move_type,
            "partner_id": self.partner_id,
            "invoice_origin": self.invoice_origin,
            "ref": self.ref,
            "invoice_date": self.invoice_date,
            "invoice_line_ids": [[0, 0, line.odoo_values()] for line in self.lines],
        }
        optional = {
            "currency_id": self.currency_id,
            "fiscal_position_id": self.fiscal_position_id,
            "invoice_payment_term_id": self.invoice_payment_term_id,
            "partner_bank_id": self.partner_bank_id,
        }
        values.update({k: v for k, v in optional.items() if v is not None})
        return values


def choose_bank_account(invoice: InvoiceData, accounts: Sequence[BankAccountRecord]) -> int | None:
    """The ERP account for the bill: the only one, or the one equal to the printed number."""
    if len(accounts) == 1:
        return accounts[0].id
    if accounts and invoice.bank_account is not None:
        digits = account_digits(invoice.bank_account.number)
        same = [a for a in accounts if digits and a.digits == digits]
        if len(same) == 1:
            return same[0].id
    return None


def plan_bill(
    invoice: InvoiceData,
    vendor: VendorRecord,
    po: PurchaseOrderRecord,
    lines: LinesResult,
    accounts: Sequence[BankAccountRecord],
) -> BillPlan | None:
    """The BR-18 plan, or ``None`` when not every line has its own PO line."""
    if not lines.all_matched or lines.purchase_order != po.number:
        return None
    ids = [m.po_line_id for m in lines.matches]
    if None in ids or len(set(ids)) != len(ids) or len(ids) != len(invoice.lines):
        return None
    by_id = {pl.id: pl for pl in po.lines}
    planned = []
    for match, line in zip(lines.matches, invoice.lines, strict=True):
        assert match.po_line_id is not None
        po_line = by_id[match.po_line_id]
        planned.append(
            BillLinePlan(
                product_id=po_line.product_id,
                product_uom_id=po_line.uom_id,
                tax_ids=po_line.tax_ids,
                purchase_line_id=po_line.id,
                name=po_line.description,
                quantity=line.quantity,
                price_unit=line.unit_price,
                po_number=po.number,
                product_code=po_line.product_code,
            )
        )
    return BillPlan(
        partner_id=vendor.id,
        currency_id=po.currency_id,
        fiscal_position_id=po.fiscal_position_id,
        invoice_origin=po.number,
        invoice_payment_term_id=po.payment_term_id,
        ref=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        partner_bank_id=choose_bank_account(
            invoice, [a for a in accounts if a.partner_id == vendor.id]
        ),
        lines=tuple(planned),
        expected_total=invoice.total,
        expected_untaxed=invoice.subtotal,
    )
