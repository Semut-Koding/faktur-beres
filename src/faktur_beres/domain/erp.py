"""ERP records the rules read, and the read-only ``ErpReader`` protocol that supplies them.

At runtime the reader is ``faktur_beres.tools.odoo_tools.OdooErpReader`` (agent key, JSON-2). The
oracle evaluation also has a reader built from the dataset (``faktur_beres.evals``). Rules never
call a reader themselves: the engine fetches the records and passes them to pure rule functions.

Quantities and amounts are ``Decimal``. Odoo ids are plain integers.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.normalize import account_digits


class CompanyRecord(DomainModel):
    id: int
    name: str
    vat: str | None = None


class VendorRecord(DomainModel):
    """A vendor (``res.partner`` with ``supplier_rank > 0``)."""

    id: int
    name: str
    vat: str | None = None
    email: str | None = None


class BankAccountRecord(DomainModel):
    """A bank account in the ERP (``res.partner.bank``)."""

    id: int
    partner_id: int
    number: str
    bank_name: str | None = None
    holder: str | None = None

    @property
    def digits(self) -> str:
        return account_digits(self.number)


class PoState(StrEnum):
    """``purchase.order.state`` in Odoo 19 (plus the boolean ``locked``)."""

    DRAFT = "draft"
    SENT = "sent"
    TO_APPROVE = "to approve"
    PURCHASE = "purchase"
    CANCEL = "cancel"


class PurchaseOrderLineRecord(DomainModel):
    id: int
    product_id: int
    product_code: str | None = None  # internal code (default_code)
    product_name: str
    vendor_codes: tuple[str, ...] = ()  # product.supplierinfo.product_code for the PO's vendor
    description: str  # purchase.order.line.name
    uom_id: int
    uom_name: str
    quantity: Decimal  # ordered (product_qty)
    qty_received: Decimal
    qty_invoiced: Decimal
    price_unit: Decimal
    tax_ids: tuple[int, ...] = ()

    @property
    def open_quantity(self) -> Decimal:
        """Received but not yet invoiced."""
        return self.qty_received - self.qty_invoiced


class PurchaseOrderRecord(DomainModel):
    id: int
    number: str
    vendor_id: int
    state: PoState
    locked: bool = False
    currency_id: int | None = None
    fiscal_position_id: int | None = None
    payment_term_id: int | None = None
    lines: tuple[PurchaseOrderLineRecord, ...] = ()

    @property
    def confirmed(self) -> bool:
        """Confirmed means state ``purchase``, locked or not (BR-06)."""
        return self.state == PoState.PURCHASE


class BillState(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    CANCEL = "cancel"


class BillLineRecord(DomainModel):
    id: int
    purchase_line_id: int | None = None
    product_id: int | None = None
    quantity: Decimal
    price_unit: Decimal


class BillRecord(DomainModel):
    """A vendor bill (``account.move`` with ``move_type = in_invoice``)."""

    id: int
    vendor_id: int
    ref: str | None = None
    invoice_date: date | None = None
    state: BillState
    amount_total: Decimal
    created_by: int | None = None
    lines: tuple[BillLineRecord, ...] = ()


class ErpReader(Protocol):
    """Read-only access to the ERP data the rules need."""

    def company(self) -> CompanyRecord: ...

    def vendors_by_vat(self, digits: str) -> Sequence[VendorRecord]:
        """Vendors whose NPWP, compared as digits, equals ``digits``."""
        ...

    def vendors_by_name(self, name: str) -> Sequence[VendorRecord]:
        """Vendors whose name equals ``name`` after BR-05 normalisation."""
        ...

    def vendors_by_email(self, email: str) -> Sequence[VendorRecord]:
        """Vendors whose billing e-mail equals ``email`` after normalisation."""
        ...

    def bank_accounts(self, vendor_id: int) -> Sequence[BankAccountRecord]: ...

    def bank_account_owners(self, digits: str) -> Sequence[BankAccountRecord]:
        """Every account in the ERP with these digits, whoever owns it."""
        ...

    def purchase_order(self, number: str) -> PurchaseOrderRecord | None:
        """The PO with exactly this (BR-06 normalised) number, in any state."""
        ...

    def confirmed_purchase_orders(self, vendor_id: int) -> Sequence[PurchaseOrderRecord]: ...

    def vendor_bills(self, vendor_id: int) -> Sequence[BillRecord]:
        """The vendor's bills in state draft or posted (cancelled bills are not returned)."""
        ...

    def posted_bill_count(self, vendor_id: int) -> int: ...


class RegisteredInvoice(DomainModel):
    """Another invoice already handled by Faktur Beres (application database, M4)."""

    invoice_id: str
    vendor_id: int
    invoice_number: str
    invoice_date: date
    total: Decimal


class InvoiceRegistry(Protocol):
    """BR-11 compares with "faktur lain di Faktur Beres"; the source is the application DB (M4)."""

    def other_invoices(self, vendor_id: int) -> Sequence[RegisteredInvoice]: ...


class NoOtherInvoices:
    """The M2 registry: there is no application database yet, so it is always empty."""

    def other_invoices(self, vendor_id: int) -> Sequence[RegisteredInvoice]:
        return ()
