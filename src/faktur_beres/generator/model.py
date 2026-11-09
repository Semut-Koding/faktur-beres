"""Document model of one invoice, shared by every renderer (PDF, image, UBL) and the labels.

The model stores the values **as printed**, including values that are wrong on purpose (a line
amount that does not match qty × price, a PPN computed on the wrong base). Money is whole rupiah.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

DppBasis = Literal["nilai_lain", "harga_jual"]


@dataclass(frozen=True)
class Party:
    name: str
    npwp: str | None  # None: not printed
    street: str
    city: str
    state: str
    zip: str
    email: str | None = None

    @property
    def address(self) -> str:
        return f"{self.street}, {self.city}, {self.state} {self.zip}"


@dataclass(frozen=True)
class PrintedBankAccount:
    bank: str
    number: str  # exactly as printed (may contain separators)
    holder: str


@dataclass(frozen=True)
class DocLine:
    code: str | None  # vendor product code; None for a free line (e.g. shipping)
    description: str
    qty: int
    unit: str  # printed unit (kg, lembar, roll, pcs, set, ...)
    unit_price: int
    amount: int  # printed line amount
    ubl_unit: str = "H87"  # UN/ECE rec. 20 code for UBL: KGM or H87


@dataclass(frozen=True)
class InvoiceDoc:
    number: str
    issue_date: date
    due_date: date
    po_ref: str | None  # printed purchase-order reference; None: not printed
    seller: Party
    buyer: Party
    lines: tuple[DocLine, ...]
    subtotal: int  # harga jual
    dpp: int | None  # printed tax base; None: not printed
    ppn: int
    total: int
    nsfp: str | None  # printed tax-invoice serial; None: not printed
    tax_invoice_date: date | None
    bank: PrintedBankAccount | None
    notes: tuple[str, ...] = ()  # visible notes
    hidden_texts: tuple[str, ...] = ()  # in the text layer but not visible (white text)
    photo_note: str | None = None  # sticky note drawn on a photo (image format only)
    dpp_basis: DppBasis = "nilai_lain"  # how the printed DPP row is labelled
    title: str | None = None  # document title override (default from the vendor style)

    # -- checks used by tests and the catalogue -------------------------------------------------

    def arithmetic_consistent(self, tolerance: int = 1) -> bool:
        """BR-03 (SPEC 0.2): line amounts, subtotal, and subtotal + PPN = total."""
        for line in self.lines:
            if abs(line.qty * line.unit_price - line.amount) > tolerance:
                return False
        if abs(sum(line.amount for line in self.lines) - self.subtotal) > tolerance * len(
            self.lines
        ):
            return False
        return abs(self.subtotal + self.ppn - self.total) <= tolerance

    def tax_rule_consistent(self, tolerance: int = 1) -> bool:
        """BR-10 (SPEC 0.2): DPP nilai lain = 11/12 x subtotal and PPN = 12% x DPP."""
        expected_dpp = self.subtotal * 11 / 12
        if self.dpp is not None:
            if abs(self.dpp - expected_dpp) > tolerance:
                return False
            return abs(self.ppn - self.dpp * 12 / 100) <= tolerance
        return abs(self.ppn - expected_dpp * 12 / 100) <= tolerance


@dataclass(frozen=True)
class EmailMeta:
    """Metadata of the e-mail that carries a document (channel ``email``)."""

    sender: str
    to: str
    subject: str
    body: str
    sent_at: str  # ISO 8601 with +07:00
    message_id: str


@dataclass(frozen=True)
class PrintedParties:
    """Which party fields a renderer really prints; the label truth follows this, not the format."""

    seller_email: bool
    buyer_email: bool


@dataclass(frozen=True)
class RenderedFile:
    """A rendered document file, the name it is sent under, and what its renderer prints."""

    path: str  # relative to eval/
    send_name: str
    content: bytes = field(repr=False)
    printed: PrintedParties
