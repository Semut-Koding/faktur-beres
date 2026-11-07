"""Findings of the rules and their structured evidence.

A ``Finding`` is either a reason (``reason`` set: the invoice cannot simply become a draft bill) or
a note (``reason`` is ``None``: worth telling the AP staff, not an exception). Every reason carries
evidence computed by code: the invoice figure, the ERP figure, and the difference. The e-mail
templates and the approval evidence pack of M4 are filled from these fields, never from LLM text.
"""

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import Field

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.reasons import ReasonCode

RuleId = Annotated[str, Field(pattern=r"^BR-\d{2}$")]


class LineAmountEvidence(DomainModel):
    kind: Literal["line_amount"] = "line_amount"
    line: int
    quantity: Decimal
    unit_price: Decimal
    printed: Decimal
    computed: Decimal
    difference: Decimal
    tolerance: Decimal


class DocumentSumEvidence(DomainModel):
    kind: Literal["document_sum"] = "document_sum"
    subject: Literal["subtotal", "total"]
    printed: Decimal
    computed: Decimal
    difference: Decimal
    tolerance: Decimal


class VendorEvidence(DomainModel):
    kind: Literal["vendor"] = "vendor"
    npwp_digits: str | None
    npwp_valid: bool
    by_npwp: tuple[int, ...]
    by_name: tuple[int, ...]
    by_email: tuple[int, ...]
    emails: tuple[str, ...]


class PurchaseOrderEvidence(DomainModel):
    kind: Literal["purchase_order"] = "purchase_order"
    printed: str | None
    found: str | None = None
    state: str | None = None
    owner_vendor_id: int | None = None
    candidates: tuple[str, ...] = ()


class LineMatchEvidence(DomainModel):
    kind: Literal["line_match"] = "line_match"
    line: int
    vendor_code: str | None
    description: str
    problem: Literal["no_match", "ambiguous"]
    purchase_order: str
    candidate_line_ids: tuple[int, ...] = ()


class PriceEvidence(DomainModel):
    kind: Literal["price"] = "price"
    line: int
    po_line_id: int
    product: str
    invoice_price: Decimal
    po_price: Decimal
    limit: Decimal
    difference: Decimal
    difference_percent: Decimal


class QuantityEvidence(DomainModel):
    kind: Literal["quantity"] = "quantity"
    line: int
    po_line_id: int
    product: str
    invoice_quantity: Decimal
    ordered: Decimal
    received: Decimal
    invoiced: Decimal
    excluded_duplicate: Decimal
    already_on_invoice: Decimal
    available: Decimal
    excess: Decimal
    problem: Literal["exceeds_received", "exceeds_ordered"]


class TaxIdEvidence(DomainModel):
    kind: Literal["tax_id"] = "tax_id"
    field: Literal["npwp_penjual", "npwp_pembeli", "nsfp"]
    printed: str | None
    digits: str | None
    problem: Literal["missing", "not_digits", "length", "check_digit", "mismatch"]
    expected: str | None = None


class TaxAmountEvidence(DomainModel):
    kind: Literal["tax_amount"] = "tax_amount"
    field: Literal["dpp_nilai_lain", "ppn"]
    subtotal: Decimal
    printed: Decimal
    expected: Decimal
    difference: Decimal
    tolerance: Decimal


class TransactionCodeEvidence(DomainModel):
    kind: Literal["transaction_code"] = "transaction_code"
    code: str
    expected: str


class IdentityEvidence(DomainModel):
    kind: Literal["identity"] = "identity"
    npwp_digits: str
    vendor_id: int
    vendor_npwp_digits: str | None


class DuplicateEvidence(DomainModel):
    kind: Literal["duplicate"] = "duplicate"
    key: Literal["number", "date_total"]
    source: Literal["odoo", "faktur_beres"]
    record: str
    ref: str | None
    state: str | None
    invoice_date: str | None
    total: Decimal
    invoice_total: Decimal
    difference: Decimal


class BankAccountEvidence(DomainModel):
    kind: Literal["bank_account"] = "bank_account"
    printed_number: str
    printed_digits: str
    printed_bank: str | None
    registered_digits: tuple[str, ...]
    other_owner_ids: tuple[int, ...] = ()
    problem: Literal["not_registered", "other_owner", "different_bank"]


class ThresholdEvidence(DomainModel):
    kind: Literal["threshold"] = "threshold"
    total: Decimal
    threshold: Decimal


class NewVendorEvidence(DomainModel):
    kind: Literal["new_vendor"] = "new_vendor"
    registered: bool
    posted_bills: int | None


class InjectionEvidence(DomainModel):
    kind: Literal["injection"] = "injection"
    source: str
    where: str | None
    patterns: tuple[str, ...]
    categories: tuple[str, ...]
    quote: str


Evidence = Annotated[
    LineAmountEvidence
    | DocumentSumEvidence
    | VendorEvidence
    | PurchaseOrderEvidence
    | LineMatchEvidence
    | PriceEvidence
    | QuantityEvidence
    | TaxIdEvidence
    | TaxAmountEvidence
    | TransactionCodeEvidence
    | IdentityEvidence
    | DuplicateEvidence
    | BankAccountEvidence
    | ThresholdEvidence
    | NewVendorEvidence
    | InjectionEvidence,
    Field(discriminator="kind"),
]


class Finding(DomainModel):
    """One result of a rule: a reason with evidence, or a note."""

    rule: RuleId
    reason: ReasonCode | None = None
    message: str
    evidence: Evidence | None = None

    @property
    def is_note(self) -> bool:
        return self.reason is None


def reasons_of(findings: Iterable[Finding]) -> tuple[ReasonCode, ...]:
    """The distinct reasons, in the order of SPEC §6.2."""
    found = {f.reason for f in findings if f.reason is not None}
    return tuple(code for code in ReasonCode if code in found)


def to_decimal(value: Fraction, places: int = 2) -> Decimal:
    """An exact fraction as a ``Decimal`` rounded half up (for evidence and messages only)."""
    quantum = Decimal(1).scaleb(-places)
    return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(quantum, ROUND_HALF_UP)


def rupiah(value: Decimal) -> str:
    """``Rp1.234.500`` or ``Rp1.234.500,50`` (Indonesian notation, for messages)."""
    sign = "-" if value < 0 else ""
    whole, _, cents = f"{abs(value):,.2f}".partition(".")
    text = whole.replace(",", ".")
    return f"{sign}Rp{text}" + ("" if cents == "00" else f",{cents}")


def number(value: Decimal) -> str:
    """``1.234`` or ``1.234,5`` (Indonesian notation, without trailing zeros)."""
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return f"{int(normalized):,}".replace(",", ".")
    whole, _, frac = f"{normalized:,f}".partition(".")
    return whole.replace(",", ".") + "," + frac


def percent(value: Decimal) -> str:
    """``+4,00%`` or ``-2,56%``."""
    return f"{value:+.2f}%".replace(".", ",")
