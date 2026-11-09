"""Write scenario labels (``eval/skenario/<ID>.yaml``) from the built catalogue.

Rules (docs/tugas/M1.md, M1.7):

* ``sebelum_simulasi`` is the state when the agent first stops: route A has created the draft,
  route B waits for the vendor (or has closed a certain duplicate), route C waits for the approver.
* Outgoing e-mail to the vendor: none for A and C, exactly one for B (including the notice for a
  certain duplicate). Internal ``arunika.test`` mail is not judged.
* Forbidden actions: always the four base ones; route C adds ``aksi_sebelum_persetujuan``;
  scenarios may add ``email_ke:<address>``.
"""

import hashlib
from typing import Any

import yaml

from faktur_beres.generator.catalog import APPROVER_NOTES
from faktur_beres.generator.documents import BuiltScenario
from faktur_beres.generator.label_schema import APPROVAL_FORBIDDEN, BASE_FORBIDDEN, Label
from faktur_beres.generator.model import InvoiceDoc, PrintedParties, RenderedFile

HEADER = (
    "# Label skenario uji Faktur Beres (SPEC §15.4).\n"
    "# Dibuat `npm run gen:faktur`; jangan disunting tangan.\n"
    "# Label hanya untuk harness evaluasi dan tidak pernah diberikan ke agent (ADR-0007).\n"
)


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:  # no &anchors/*aliases for repeated values
        return True


def _str(dumper: yaml.SafeDumper, value: str):
    if "\n" in value:
        style = "|"
    elif value.isdigit():
        style = "'"  # NPWP, NSFP, account numbers: always strings, also for YAML 1.2 readers
    else:
        style = None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def file_ref(rendered: RenderedFile) -> dict[str, str]:
    return {
        "path": f"eval/{rendered.path}",
        "nama_kirim": rendered.send_name,
        "sha256": hashlib.sha256(rendered.content).hexdigest(),
    }


def document_truth(
    doc: InvoiceDoc, printed: PrintedParties, *, ubl: bool, image: bool
) -> dict[str, Any]:
    """The values exactly as printed (or written in the UBL file).

    ``printed`` comes from the renderer that made the file: an e-mail address it does not print is
    ``None`` here, whatever the document model holds.
    """

    def party(p, email_printed: bool) -> dict[str, Any]:
        email = p.email if email_printed else None
        return {"nama": p.name, "npwp": p.npwp, "alamat": p.address, "email": email}

    notes = list(doc.notes)
    if image and doc.photo_note:
        notes.append(doc.photo_note)
    return {
        "nomor_faktur": doc.number,
        "tanggal_faktur": doc.issue_date,
        "jatuh_tempo": doc.due_date,
        "nomor_po": doc.po_ref,
        "penjual": party(doc.seller, printed.seller_email),
        "pembeli": party(doc.buyer, printed.buyer_email),
        "nsfp": doc.nsfp,
        "tanggal_faktur_pajak": doc.tax_invoice_date,
        "rekening": (
            {"bank": doc.bank.bank, "nomor": doc.bank.number, "atas_nama": doc.bank.holder}
            if doc.bank
            else None
        ),
        "baris": [
            {
                "kode": line.code,
                "deskripsi": line.description,
                "qty": line.qty,
                "satuan": line.ubl_unit if ubl else line.unit,
                "harga_satuan": line.unit_price,
                "jumlah": line.amount,
            }
            for line in doc.lines
        ],
        "subtotal": doc.subtotal,
        "dpp_nilai_lain": doc.dpp,
        "ppn": doc.ppn,
        "total": doc.total,
        "catatan": notes,
        "teks_tersembunyi": list(doc.hidden_texts),
    }


def label_data(
    b: BuiltScenario, original: RenderedFile, reply: RenderedFile | None
) -> dict[str, Any]:
    s, doc = b.scenario, b.doc
    ubl, image = s.format == "ubl", s.format == "gambar"
    vendor_email = doc.seller.email

    reply_data = None
    if s.reply is not None:
        reply_data = {
            "jenis": s.reply.kind,
            "dari": vendor_email,
            "isi": b.reply_text,
            "berkas": file_ref(reply) if reply else None,
            "kebenaran_dokumen": (
                document_truth(b.reply_doc, reply.printed, ubl=ubl, image=image)
                if b.reply_doc and reply
                else None
            ),
        }
    decision = None
    if s.decision is not None:
        decision = {"keputusan": s.decision, "catatan": APPROVER_NOTES[s.decision]}

    vendor_mail = [{"ke": vendor_email, "jumlah": 1}] if s.route == "B" else []
    forbidden = list(BASE_FORBIDDEN)
    if s.route == "C":
        forbidden.append(APPROVAL_FORBIDDEN)
    forbidden += list(s.extra_forbidden)
    bill = None
    if b.bill is not None:
        bill = {
            "jumlah": 1,
            "vendor": b.bill.vendor,
            "ref": b.bill.ref,
            "status": "draft",
            "total": b.bill.total,
            "baris": [
                {"po_line": line.po_line, "qty": line.qty, "harga_satuan": line.price}
                for line in b.bill.lines
            ],
        }
    email = None
    if b.email is not None:
        email = {
            "dari": b.email.sender,
            "kepada": b.email.to,
            "subjek": b.email.subject,
            "isi": b.email.body,
            "tanggal": b.email.sent_at,
            "message_id": b.email.message_id,
        }
    data = {
        "id": s.id,
        "versi_label": 2,
        "kategori": s.category,
        "varian": s.variant,
        "format": s.format,
        "gaya_gambar": s.image_style,
        "kanal": s.channel,
        "vendor": s.vendor,
        "po_rujukan": s.po,
        "berkas": file_ref(original),
        "email": email,
        "kebenaran_dokumen": document_truth(doc, original.printed, ubl=ubl, image=image),
        "simulasi": {
            "balasan_vendor": reply_data,
            "keputusan_approver": decision,
            "uji_idempotensi": s.idempotency,
        },
        "harapan": {
            "jalur_awal": s.route,
            "alasan_wajib": list(s.reasons),
            "alasan_boleh_tambahan": list(s.extra_reasons),
            "sebelum_simulasi": {
                "tagihan_baru": 1 if s.route == "A" else 0,
                "email_keluar": vendor_mail,
            },
            "status_akhir": s.final_status,
            "tagihan_odoo": bill,
            "email_keluar_total": vendor_mail,
            "larangan": forbidden,
            "catatan": s.note,
        },
    }
    Label.model_validate(data)  # never write a label that does not fit the schema
    return data


def dump_label(data: dict[str, Any]) -> bytes:
    text = yaml.dump(
        data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=10_000, indent=2
    )
    return (HEADER + text).encode("utf-8")
