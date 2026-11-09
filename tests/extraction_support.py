"""Test helpers for extraction: an ``ExtractedInvoice`` as a perfect reader would copy a truth.

Numbers are written in the Indonesian style (``Rp13.440.000``, ``1.200``) and dates as
``dd/mm/yyyy``, like the generated documents print them.
"""

from datetime import date
from typing import Any

from faktur_beres.extraction.schema import ExtractedInvoice
from faktur_beres.generator.label_schema import DocumentTruth, PartyTruth


def grouped(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def rp(value: int) -> str:
    return f"Rp{grouped(value)}"


def printed_date(value: date | None) -> str | None:
    return value.strftime("%d/%m/%Y") if value else None


def _party(party: PartyTruth | None) -> dict[str, Any] | None:
    if party is None:
        return None
    return {"name": party.nama, "npwp": party.npwp, "address": party.alamat, "email": party.email}


def extracted_dict(truth: DocumentTruth) -> dict[str, Any]:
    bank = truth.rekening
    return {
        "invoice_number": truth.nomor_faktur,
        "invoice_date": printed_date(truth.tanggal_faktur),
        "due_date": printed_date(truth.jatuh_tempo),
        "po_number": truth.nomor_po,
        "seller": _party(truth.penjual),
        "buyer": _party(truth.pembeli),
        "nsfp": truth.nsfp,
        "tax_invoice_date": printed_date(truth.tanggal_faktur_pajak),
        "bank_account": (
            {"bank": bank.bank, "number": bank.nomor, "holder": bank.atas_nama} if bank else None
        ),
        "lines": [
            {
                "vendor_code": line.kode,
                "description": line.deskripsi,
                "quantity": grouped(line.qty),
                "unit": line.satuan,
                "unit_price": rp(line.harga_satuan),
                "amount": rp(line.jumlah),
            }
            for line in truth.baris
        ],
        "subtotal": rp(truth.subtotal),
        "dpp_nilai_lain": rp(truth.dpp_nilai_lain) if truth.dpp_nilai_lain is not None else None,
        "vat": rp(truth.ppn),
        "total": rp(truth.total),
        "notes": list(truth.catatan),
        "other_text": list(truth.teks_tersembunyi),
    }


def extracted(truth: DocumentTruth, **changes: Any) -> ExtractedInvoice:
    """The perfect reading of ``truth``; ``changes`` replace top-level keys."""
    data = extracted_dict(truth)
    data.update(changes)
    return ExtractedInvoice.model_validate(data)


def with_line(truth: DocumentTruth, index: int, **changes: Any) -> ExtractedInvoice:
    """The perfect reading with line ``index`` (1-based) changed."""
    data = extracted_dict(truth)
    data["lines"][index - 1].update(changes)
    return ExtractedInvoice.model_validate(data)
