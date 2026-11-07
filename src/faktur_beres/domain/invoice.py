"""The canonical invoice (``InvoiceData``) and the context it arrived in.

``InvoiceData`` is the target of Gemini extraction and of the UBL parser (M3) and the input of every
rule. It holds the values **as printed**, including values that are wrong on purpose (an amount that
does not add up, an NPWP of the wrong length): the rules decide what is wrong, the model only
refuses what cannot be an invoice at all. Required and optional fields follow BR-02.

Hidden text, the PDF text layer, UBL notes, and e-mail text are not invoice data; they reach the
injection check (BR-15) as ``TextFragment`` values.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from faktur_beres.domain.base import DomainModel


class Party(DomainModel):
    """Seller or buyer as printed. NPWP keeps its printed separators."""

    name: str | None = None
    npwp: str | None = None
    address: str | None = None
    email: str | None = None


class Seller(Party):
    """The seller's name is required (BR-02); everything else is optional."""

    name: str = Field(min_length=1)


class BankAccount(DomainModel):
    """Payment details as printed; ``number`` may contain separators (BR-12 normalises)."""

    bank: str | None = None
    number: str = Field(min_length=1)
    holder: str | None = None


class InvoiceLine(DomainModel):
    """One printed line. ``amount`` is the printed line subtotal, not a recomputed one."""

    vendor_code: str | None = None
    description: str = Field(min_length=1)
    quantity: Decimal
    unit: str = Field(min_length=1)
    unit_price: Decimal
    amount: Decimal


class InvoiceData(DomainModel):
    """A supplier invoice with every value as printed (BR-02)."""

    invoice_number: str = Field(min_length=1)
    invoice_date: date
    due_date: date | None = None
    po_number: str | None = None
    seller: Seller
    buyer: Party | None = None
    nsfp: str | None = None
    tax_invoice_date: date | None = None
    bank_account: BankAccount | None = None
    lines: tuple[InvoiceLine, ...] = Field(min_length=1)
    subtotal: Decimal
    dpp_nilai_lain: Decimal | None = None
    vat: Decimal  # PPN as printed
    total: Decimal
    notes: tuple[str, ...] = ()


class Channel(StrEnum):
    UPLOAD = "upload"
    EMAIL = "email"


class IntakeContext(DomainModel):
    """How the invoice arrived: channel, e-mail sender, file name, and SHA-256 of the file."""

    channel: Channel
    sender_email: str | None = None
    file_name: str | None = None
    file_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class TextSource(StrEnum):
    """Where a piece of free text came from (BR-15 checks every source)."""

    NOTE = "note"
    LINE_DESCRIPTION = "line_description"
    HIDDEN_TEXT = "hidden_text"
    DOCUMENT_FIELD = "document_field"
    PDF_TEXT_LAYER = "pdf_text_layer"
    UBL_NOTE = "ubl_note"
    READ_TEXT = "read_text"
    EMAIL_SUBJECT = "email_subject"
    EMAIL_BODY = "email_body"
    VENDOR_REPLY = "vendor_reply"


class TextFragment(DomainModel):
    """A piece of free text from the document or its e-mail, checked for injection (BR-15)."""

    source: TextSource
    text: str
    where: str | None = None  # e.g. "baris 2" or "penjual.nama"
