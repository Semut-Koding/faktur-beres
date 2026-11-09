"""Read scenario labels and turn their document truth into agent inputs (evaluation code only).

``kebenaran_dokumen`` holds the values exactly as printed, so converting it gives the
``InvoiceData`` a perfect extraction would produce (the oracle of SPEC §12.1). Free text that is
not invoice data (hidden text, notes, line descriptions, other text fields, e-mail text) becomes
``TextFragment`` values for the injection check (BR-15).

Only the document truth, the channel and e-mail, and the simulation inputs are converted here. The
expectations (``harapan``), category, variant, vendor key, and ``po_rujukan`` are never turned into
agent inputs; they are only read by the scoring code.
"""

from collections.abc import Iterator, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from faktur_beres.config import REPO_ROOT
from faktur_beres.domain.invoice import (
    BankAccount,
    Channel,
    IntakeContext,
    InvoiceData,
    InvoiceLine,
    Party,
    Seller,
    TextFragment,
    TextSource,
)
from faktur_beres.domain.normalize import normalize_email
from faktur_beres.generator.label_schema import DocumentTruth, Label, PartyTruth, VendorReply

LABEL_DIR = REPO_ROOT / "eval" / "skenario"
CHANNELS = {"unggahan": Channel.UPLOAD, "email": Channel.EMAIL}


def load_label(path: Path) -> Label:
    with path.open(encoding="utf-8") as fh:
        return Label.model_validate(yaml.safe_load(fh))


def load_labels(directory: Path = LABEL_DIR) -> dict[str, Label]:
    """All labels by scenario ID, in ID order."""
    labels = {}
    for path in sorted(directory.glob("*.yaml")):
        label = load_label(path)
        if path.stem != label.id:
            raise ValueError(f"{path.name} berisi label {label.id}")
        labels[label.id] = label
    return labels


def _party(truth: PartyTruth) -> dict[str, Any]:
    return {"name": truth.nama, "npwp": truth.npwp, "address": truth.alamat, "email": truth.email}


def truth_to_invoice(truth: DocumentTruth) -> InvoiceData:
    """The ``InvoiceData`` of a perfect extraction of the document."""
    bank = truth.rekening
    return InvoiceData(
        invoice_number=truth.nomor_faktur,
        invoice_date=truth.tanggal_faktur,
        due_date=truth.jatuh_tempo,
        po_number=truth.nomor_po,
        seller=Seller(**_party(truth.penjual)),
        buyer=Party(**_party(truth.pembeli)),
        nsfp=truth.nsfp,
        tax_invoice_date=truth.tanggal_faktur_pajak,
        bank_account=(
            BankAccount(bank=bank.bank, number=bank.nomor, holder=bank.atas_nama) if bank else None
        ),
        lines=tuple(
            InvoiceLine(
                vendor_code=line.kode,
                description=line.deskripsi,
                quantity=Decimal(line.qty),
                unit=line.satuan,
                unit_price=Decimal(line.harga_satuan),
                amount=Decimal(line.jumlah),
            )
            for line in truth.baris
        ),
        subtotal=Decimal(truth.subtotal),
        dpp_nilai_lain=None if truth.dpp_nilai_lain is None else Decimal(truth.dpp_nilai_lain),
        vat=Decimal(truth.ppn),
        total=Decimal(truth.total),
        notes=tuple(truth.catatan),
    )


def invoice_to_truth(invoice: InvoiceData, hidden_texts: Sequence[str]) -> dict[str, Any]:
    """The inverse of ``truth_to_invoice``, used by tests to prove that no value is lost."""

    def party(p: Party | None) -> dict[str, Any] | None:
        if p is None:
            return None
        return {"nama": p.name, "npwp": p.npwp, "alamat": p.address, "email": p.email}

    bank = invoice.bank_account
    return {
        "nomor_faktur": invoice.invoice_number,
        "tanggal_faktur": invoice.invoice_date,
        "jatuh_tempo": invoice.due_date,
        "nomor_po": invoice.po_number,
        "penjual": party(invoice.seller),
        "pembeli": party(invoice.buyer),
        "nsfp": invoice.nsfp,
        "tanggal_faktur_pajak": invoice.tax_invoice_date,
        "rekening": (
            {"bank": bank.bank, "nomor": bank.number, "atas_nama": bank.holder} if bank else None
        ),
        "baris": [
            {
                "kode": line.vendor_code,
                "deskripsi": line.description,
                "qty": line.quantity,
                "satuan": line.unit,
                "harga_satuan": line.unit_price,
                "jumlah": line.amount,
            }
            for line in invoice.lines
        ],
        "subtotal": invoice.subtotal,
        "dpp_nilai_lain": invoice.dpp_nilai_lain,
        "ppn": invoice.vat,
        "total": invoice.total,
        "catatan": list(invoice.notes),
        "teks_tersembunyi": list(hidden_texts),
    }


def truth_fragments(truth: DocumentTruth, *, document_format: str) -> list[TextFragment]:
    """Every free text of the document, for the injection check (BR-15)."""
    note_source = TextSource.UBL_NOTE if document_format == "ubl" else TextSource.NOTE
    fragments = [
        TextFragment(source=note_source, text=text, where=f"catatan {i}")
        for i, text in enumerate(truth.catatan, start=1)
    ]
    fragments += [
        TextFragment(source=TextSource.HIDDEN_TEXT, text=text, where=f"teks tersembunyi {i}")
        for i, text in enumerate(truth.teks_tersembunyi, start=1)
    ]
    fragments += [
        TextFragment(source=TextSource.LINE_DESCRIPTION, text=line.deskripsi, where=f"baris {i}")
        for i, line in enumerate(truth.baris, start=1)
    ]
    fields: list[tuple[str, str | None]] = [
        ("penjual.nama", truth.penjual.nama),
        ("penjual.alamat", truth.penjual.alamat),
        ("pembeli.nama", truth.pembeli.nama),
        ("pembeli.alamat", truth.pembeli.alamat),
    ]
    if truth.rekening:
        fields += [
            ("rekening.bank", truth.rekening.bank),
            ("rekening.atas_nama", truth.rekening.atas_nama),
        ]
    fragments += [
        TextFragment(source=TextSource.DOCUMENT_FIELD, text=text, where=where)
        for where, text in fields
        if text
    ]
    return fragments


def invoice_id_for(file_sha256: str) -> str:
    """The neutral invoice id used by evaluation code: ``FB-`` + the first 12 hex of the file hash.

    Nothing in it names the scenario, so it may reach Odoo (notes, idempotency keys).
    """
    return "FB-" + file_sha256[:12]


def label_invoice_id(label: Label) -> str:
    """The thread id of a scenario: from the hash of its original invoice file."""
    return invoice_id_for(label.berkas.sha256)


def label_intake(label: Label) -> IntakeContext:
    """Thread id, channel, sender, file name, and hash of the original invoice."""
    sender = normalize_email(label.email.dari) if label.email else None
    return IntakeContext(
        invoice_id=label_invoice_id(label),
        channel=CHANNELS[label.kanal],
        sender_email=sender,
        file_name=label.berkas.nama_kirim,
        file_sha256=label.berkas.sha256,
    )


def email_fragments(label: Label) -> list[TextFragment]:
    """Subject and body of the e-mail that carried the invoice (email channel only)."""
    if label.email is None:
        return []
    return [
        TextFragment(source=TextSource.EMAIL_SUBJECT, text=label.email.subjek, where="subjek"),
        TextFragment(source=TextSource.EMAIL_BODY, text=label.email.isi, where="isi email"),
    ]


def reply_intake(reply: VendorReply, *, invoice_id: str) -> IntakeContext:
    """The vendor's reply arrives by e-mail in the thread ``invoice_id`` (BR-11: the corrected
    invoice replaces the thread's invoice); an attachment carries a file name and hash."""
    return IntakeContext(
        invoice_id=invoice_id,
        channel=Channel.EMAIL,
        sender_email=normalize_email(reply.dari),
        file_name=reply.berkas.nama_kirim if reply.berkas else None,
        file_sha256=reply.berkas.sha256 if reply.berkas else None,
    )


def reply_fragments(reply: VendorReply, *, document_format: str) -> list[TextFragment]:
    """The reply text and, for an attachment, the corrected document's free text."""
    fragments = [TextFragment(source=TextSource.VENDOR_REPLY, text=reply.isi, where="balasan")]
    if reply.kebenaran_dokumen is not None:
        fragments += truth_fragments(reply.kebenaran_dokumen, document_format=document_format)
    return fragments


def all_truths(labels: dict[str, Label]) -> Iterator[tuple[str, str, DocumentTruth]]:
    """``(scenario id, "faktur" | "balasan", truth)`` for every printed document."""
    for sid, label in labels.items():
        yield sid, "faktur", label.kebenaran_dokumen
        reply = label.simulasi.balasan_vendor
        if reply is not None and reply.kebenaran_dokumen is not None:
            yield sid, "balasan", reply.kebenaran_dokumen
