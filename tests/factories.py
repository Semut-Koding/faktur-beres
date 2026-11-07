"""Builders for rule tests: invoices, ERP records, and the policy, all synthetic.

Names and numbers are fictitious and follow the dataset conventions (NPWP 0990..., ``.test``
e-mail domains); none is copied from a real document.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

from faktur_beres.domain.erp import (
    BankAccountRecord,
    BillLineRecord,
    BillRecord,
    BillState,
    CompanyRecord,
    PoState,
    PurchaseOrderLineRecord,
    PurchaseOrderRecord,
    VendorRecord,
)
from faktur_beres.domain.invoice import BankAccount, InvoiceData, InvoiceLine, Party, Seller
from faktur_beres.domain.policy import PolicyConfig, load_policy

POLICY: PolicyConfig = load_policy()
COMPANY = CompanyRecord(id=1, name="PT Pembeli Fiktif", vat="0990229163926000")
VENDOR = VendorRecord(
    id=10, name="PT Pemasok Fiktif", vat="0990427957220000", email="tagihan@pemasok.test"
)
OTHER_VENDOR = VendorRecord(
    id=11, name="CV Pemasok Lain", vat="0990286171486000", email="finance@lain.test"
)
VENDOR_ACCOUNT = BankAccountRecord(
    id=100, partner_id=10, number="5450717927", bank_name="Bank Fiktif Satu", holder=VENDOR.name
)
OTHER_ACCOUNT = BankAccountRecord(
    id=101, partner_id=11, number="1897077695", bank_name="Bank Fiktif Dua", holder="CV Lain"
)


def D(value: Any) -> Decimal:
    return Decimal(str(value))


def line(
    qty: Any = 100,
    price: Any = 10000,
    *,
    code: str | None = "KD-01",
    description: str = "Barang Uji Satu (KD-01)",
    unit: str = "kg",
    amount: Any = None,
) -> InvoiceLine:
    quantity, unit_price = D(qty), D(price)
    return InvoiceLine(
        vendor_code=code,
        description=description,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        amount=quantity * unit_price if amount is None else D(amount),
    )


def invoice(lines: Sequence[InvoiceLine] | None = None, **changes: Any) -> InvoiceData:
    """A consistent invoice (PPN 12% × 11/12) unless ``changes`` say otherwise."""
    lines = tuple(lines) if lines is not None else (line(),)
    subtotal = sum((ln.amount for ln in lines), Decimal(0))
    dpp = subtotal * 11 / 12
    vat = subtotal * Decimal("0.11")
    data: dict[str, Any] = {
        "invoice_number": "PF/INV/2026/10/0001",
        "invoice_date": date(2026, 10, 25),
        "due_date": date(2026, 11, 24),
        "po_number": "P00900",
        "seller": Seller(
            name=VENDOR.name, npwp=VENDOR.vat, address="Jl. Fiktif 1", email=VENDOR.email
        ),
        "buyer": Party(name=COMPANY.name, npwp=COMPANY.vat, address="Jl. Fiktif 2"),
        "nsfp": "04002600007215090",
        "tax_invoice_date": date(2026, 10, 25),
        "bank_account": BankAccount(
            bank=VENDOR_ACCOUNT.bank_name, number=VENDOR_ACCOUNT.number, holder=VENDOR.name
        ),
        "lines": lines,
        "subtotal": subtotal,
        "dpp_nilai_lain": dpp if dpp == dpp.to_integral() else dpp.quantize(Decimal("0.01")),
        "vat": vat,
        "total": subtotal + vat,
    }
    data.update(changes)
    return InvoiceData(**data)


def po_line(
    line_id: int = 1,
    product_id: int = 501,
    *,
    qty: Any = 100,
    received: Any = None,
    invoiced: Any = 0,
    price: Any = 10000,
    code: str | None = "INT-01",
    vendor_codes: tuple[str, ...] = ("KD-01",),
    name: str = "Barang Uji Satu",
) -> PurchaseOrderLineRecord:
    return PurchaseOrderLineRecord(
        id=line_id,
        product_id=product_id,
        product_code=code,
        product_name=name,
        vendor_codes=vendor_codes,
        description=f"[{code}] {name}" if code else name,
        uom_id=1,
        uom_name="kg",
        quantity=D(qty),
        qty_received=D(qty if received is None else received),
        qty_invoiced=D(invoiced),
        price_unit=D(price),
        tax_ids=(2,),
    )


def po(
    lines: Sequence[PurchaseOrderLineRecord] | None = None,
    *,
    po_id: int = 900,
    number: str = "P00900",
    vendor_id: int = VENDOR.id,
    state: PoState = PoState.PURCHASE,
    locked: bool = False,
) -> PurchaseOrderRecord:
    return PurchaseOrderRecord(
        id=po_id,
        number=number,
        vendor_id=vendor_id,
        state=state,
        locked=locked,
        currency_id=12,
        fiscal_position_id=None,
        payment_term_id=4,
        lines=tuple(lines) if lines is not None else (po_line(),),
    )


def bill(
    *,
    bill_id: int = 70,
    vendor_id: int = VENDOR.id,
    ref: str = "PF/INV/2026/09/0001",
    invoice_date: date = date(2026, 9, 25),
    state: BillState = BillState.POSTED,
    total: Any = 1110000,
    lines: Sequence[BillLineRecord] = (),
) -> BillRecord:
    return BillRecord(
        id=bill_id,
        vendor_id=vendor_id,
        ref=ref,
        invoice_date=invoice_date,
        state=state,
        amount_total=D(total),
        lines=tuple(lines),
    )


def exact(value: Fraction) -> Decimal:
    return Decimal(value.numerator) / Decimal(value.denominator)
