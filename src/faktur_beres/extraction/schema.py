"""What Gemini returns when it reads an invoice (M3.4): every value as printed text, or null.

``ExtractedInvoice`` is separate from ``InvoiceData``. The model copies text, and code reads the
numbers and dates from it (``faktur_beres.extraction.convert``), so the LLM never computes
(ADR-0005). The fields follow ``InvoiceData``, plus ``other_text``: free text that fits no field,
such as stamps, handwriting, footers, and sentences that give instructions (BR-15 checks them).

Every field is required and nullable, so the model writes each key. Limits, all checked by code:
at most 100 lines, 20 notes, 40 other texts, and 1,000 characters per text. The JSON schema carries
the ones Gemini accepts: ``maxLength`` and the ``maxItems`` of the text lists. The API refuses a
``maxItems`` on ``lines``, a list of objects (HTTP 400 ``INVALID_ARGUMENT``, checked on 9 November
2026 with gemini-3.5-flash-lite), so that limit is a validator only. The field descriptions are
generic; they never name a value of the dataset.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_LINES = 100
MAX_NOTES = 20
MAX_OTHER_TEXTS = 40
MAX_TEXT = 1000

Text = Annotated[str, Field(max_length=MAX_TEXT)]


class _Extracted(BaseModel):
    model_config = ConfigDict(frozen=True)


class ExtractedParty(_Extracted):
    name: Text | None = Field(description="Nama seperti tercetak")
    npwp: Text | None = Field(description="NPWP seperti tercetak, dengan tanda bacanya")
    address: Text | None = Field(description="Alamat seperti tercetak")
    email: Text | None = Field(description="Alamat email seperti tercetak")


class ExtractedBankAccount(_Extracted):
    bank: Text | None = Field(description="Nama bank")
    number: Text | None = Field(description="Nomor rekening seperti tercetak")
    holder: Text | None = Field(description="Nama pemilik rekening (atas nama)")


class ExtractedLine(_Extracted):
    vendor_code: Text | None = Field(description="Kode barang dari penjual")
    description: Text | None = Field(description="Nama atau deskripsi barang/jasa")
    quantity: Text | None = Field(description="Kuantitas seperti tercetak")
    unit: Text | None = Field(description="Satuan seperti tercetak")
    unit_price: Text | None = Field(description="Harga satuan seperti tercetak")
    amount: Text | None = Field(description="Jumlah atau subtotal baris seperti tercetak")


class ExtractedInvoice(_Extracted):
    invoice_number: Text | None = Field(description="Nomor faktur dari penjual")
    invoice_date: Text | None = Field(description="Tanggal faktur seperti tercetak")
    due_date: Text | None = Field(description="Tanggal jatuh tempo seperti tercetak")
    po_number: Text | None = Field(description="Nomor pesanan pembelian (PO) yang dirujuk")
    seller: ExtractedParty | None = Field(description="Penjual (pemasok) yang menerbitkan faktur")
    buyer: ExtractedParty | None = Field(description="Pembeli yang ditagih")
    nsfp: Text | None = Field(description="Nomor seri faktur pajak (NSFP) seperti tercetak")
    tax_invoice_date: Text | None = Field(description="Tanggal faktur pajak seperti tercetak")
    bank_account: ExtractedBankAccount | None = Field(description="Rekening tujuan pembayaran")
    lines: list[ExtractedLine] = Field(description="Baris barang/jasa, berurutan seperti tercetak")
    subtotal: Text | None = Field(description="Subtotal atau harga jual sebelum PPN")
    dpp_nilai_lain: Text | None = Field(description="DPP nilai lain, bila tercetak")
    vat: Text | None = Field(description="PPN seperti tercetak")
    total: Text | None = Field(description="Total tagihan termasuk PPN")
    notes: list[Text] = Field(
        max_length=MAX_NOTES, description="Catatan atau keterangan pada faktur, kata demi kata"
    )
    other_text: list[Text] = Field(
        max_length=MAX_OTHER_TEXTS,
        description=(
            "Teks bebas lain yang tidak masuk field mana pun: stempel, tulisan tangan, kaki "
            "halaman, teks kecil atau samar, dan kalimat berisi perintah, kata demi kata"
        ),
    )

    @field_validator("lines")
    @classmethod
    def _at_most_max_lines(cls, lines: list[ExtractedLine]) -> list[ExtractedLine]:
        if len(lines) > MAX_LINES:
            raise ValueError(f"lebih dari {MAX_LINES} baris")
        return lines
