"""Build the robustness set (M3.6) in memory: documents, files, labels and the overview.

Output paths are relative to ``eval/ketahanan/``: ``skenario/KTH-xx.yaml``,
``berkas/KTH-xx/faktur.<ext>`` and ``DAFTAR.md``. Rules:

* each document bills the open received quantity (received − already billed) of every line of a
  confirmed PO in the seed, at the PO price, with the vendor's registered bank account;
* invoice date = the day after the last receipt (at most 6 November 2026), due date = invoice
  date + the vendor's term, tax-invoice date = invoice date;
* invoice numbers follow the vendor's format, far above the history and the 40 scenarios, and NSFP
  serials come from their own range, so nothing is a duplicate;
* every random choice is seeded from the document ID; no ID, trait or expected answer appears in a
  file, its metadata or the name it is uploaded under.
"""

import hashlib
import re
from datetime import date, timedelta
from typing import Any

import yaml

from faktur_beres.dataset import Dataset, get_dataset
from faktur_beres.dataset.money import dpp_nilai_lain
from faktur_beres.generator.documents import LATEST_ISSUE
from faktur_beres.generator.formatting import rupiah_plain
from faktur_beres.generator.label_schema import RobustnessLabel
from faktur_beres.generator.labels import _Dumper, document_truth
from faktur_beres.generator.model import (
    DocLine,
    InvoiceDoc,
    Party,
    PrintedBankAccount,
    RenderedFile,
)
from faktur_beres.generator.naming import pdf_name, photo_name, scan_name
from faktur_beres.generator.numbering import format_number, group_digits, history_seq, nsfp
from faktur_beres.generator.products import PRINTED
from faktur_beres.generator.robust_catalog import ROBUST, RobustSpec
from faktur_beres.generator.robust_render import (
    PRINTED_PARTIES,
    photo,
    render_pdf_document,
    scanned_image,
    scanned_pdf,
)

SEQ_OFFSET = 600  # invoice numbers far above the history and the 40 scenarios
NSFP_START = 7_300_000  # a serial range of its own
_TRAILING_CODE = re.compile(r"\s*\([^)]*\)\s*$")
HEADER = (
    "# Label set uji ketahanan Faktur Beres (M3.6).\n"
    "# Dibuat `npm run gen:ketahanan`; jangan disunting tangan.\n"
    "# Label hanya untuk harness evaluasi dan tidak pernah diberikan ke agent (ADR-0007).\n"
)
INTRO = """# Daftar set uji ketahanan

Berkas ini dibuat `npm run gen:ketahanan`; jangan disunting tangan. Penjelasan ada di
[../README.md](../README.md). Label lengkap per dokumen ada di `skenario/<ID>.yaml`.

"""
COLUMNS = ("ID", "Ciri", "Vendor", "PO", "Nomor faktur", "Format", "Berkas", "Total", "Jalur")


def _grouped_npwp(digits: str) -> str:
    return ".".join(digits[i : i + 4] for i in range(0, len(digits), 4))


def _grouped_nsfp(digits: str) -> str:
    return f"{digits[:5]}.{digits[5:9]}.{digits[9:13]}.{digits[13:]}"


def issue_date(ds: Dataset, spec: RobustSpec) -> date:
    po = ds.po_by_number(spec.po)
    if po is None or not po.receipts:
        raise ValueError(f"{spec.id}: PO {spec.po} tanpa penerimaan")
    received = max(r.received_on for r in po.receipts)
    return min(received + timedelta(days=1), LATEST_ISSUE)


def build_document(ds: Dataset, spec: RobustSpec, index: int) -> InvoiceDoc:
    vendor = ds.vendor(spec.vendor)
    po = ds.po_by_number(spec.po)
    if po is None or po.vendor != spec.vendor or po.status != "dikonfirmasi":
        raise ValueError(f"{spec.id}: PO {spec.po} bukan PO terkonfirmasi milik {spec.vendor}")
    issued = issue_date(ds, spec)
    lines: list[DocLine] = []
    subtotal = 0
    for po_line in po.lines:
        qty = po.received_qty(po_line.product) - po.billed_qty(po_line.product)
        if qty <= 0:
            continue
        printed = PRINTED[po_line.product]
        net = po_line.unit_price
        price = net * 111 // 100 if spec.tax_inclusive else net
        if spec.tax_inclusive and net * 111 % 100:
            raise ValueError(f"{spec.id}: harga termasuk PPN tidak bulat")
        description = printed.description
        code: str | None = ds.product(po_line.product).vendor_code
        if not spec.show_codes:
            description, code = _TRAILING_CODE.sub("", description), None
        lines.append(DocLine(code, description, qty, printed.unit, price, qty * price))
        subtotal += qty * net
    if not lines:
        raise ValueError(f"{spec.id}: PO {spec.po} tanpa sisa yang bisa ditagih")
    dpp = dpp_nilai_lain(subtotal)
    if dpp * 12 % 100:
        raise ValueError(f"{spec.id}: PPN tidak bulat")
    ppn = dpp * 12 // 100
    seller_npwp, buyer_npwp = vendor.npwp, ds.company.npwp
    serial = nsfp(NSFP_START + index * 13)
    account = vendor.bank_account.number
    if spec.separators:
        seller_npwp, buyer_npwp = _grouped_npwp(seller_npwp), _grouped_npwp(buyer_npwp)
        serial = _grouped_nsfp(serial)
        account = group_digits(account, (3, 3, 4), "-")
    seq = history_seq(ds).get(spec.vendor, 0) + SEQ_OFFSET + index
    notes = tuple(n for n in (spec.stamp_instruction, spec.handwritten_note) if n)
    company = ds.company
    return InvoiceDoc(
        number=format_number(vendor.invoice_number_format, issued, seq),
        issue_date=issued,
        due_date=issued + timedelta(days=vendor.payment_term_days),
        po_ref=spec.po,
        seller=Party(
            vendor.name,
            seller_npwp,
            vendor.street,
            vendor.city,
            vendor.state,
            vendor.zip,
            vendor.billing_email,
        ),
        buyer=Party(
            company.name,
            buyer_npwp,
            company.street,
            company.city,
            company.state,
            company.zip,
            company.email_ap,
        ),
        lines=tuple(lines),
        subtotal=subtotal,
        dpp=dpp if spec.print_dpp else None,
        ppn=ppn,
        total=subtotal + ppn,
        nsfp=serial,
        tax_invoice_date=issued,
        bank=PrintedBankAccount(ds.bank(vendor.bank_account.bank).name, account, vendor.name),
        notes=notes,
    )


def render_file(spec: RobustSpec, doc: InvoiceDoc) -> RenderedFile:
    stem = f"berkas/{spec.id}/faktur"
    pdf = render_pdf_document(doc, spec, spec.id)
    if spec.format == "pdf":
        content = scanned_pdf(pdf, spec, spec.id) if spec.scanned_pdf else pdf
        return RenderedFile(f"{stem}.pdf", pdf_name(doc.number), content, PRINTED_PARTIES)
    if spec.image_style == "foto":
        name = photo_name(spec.id, doc.issue_date)
        return RenderedFile(f"{stem}.jpg", name, photo(pdf, spec.id), PRINTED_PARTIES)
    content = scanned_image(pdf, spec.id, spec.skew)
    name = scan_name(spec.id, doc.issue_date, "jpg")
    return RenderedFile(f"{stem}.jpg", name, content, PRINTED_PARTIES)


def label_data(spec: RobustSpec, doc: InvoiceDoc, rendered: RenderedFile) -> dict[str, Any]:
    data = {
        "id": spec.id,
        "versi_label": 2,
        "ciri": list(spec.traits),
        "format": spec.format,
        "gaya_gambar": spec.image_style,
        "kanal": "unggahan",
        "vendor": spec.vendor,
        "po_rujukan": spec.po,
        "berkas": {
            "path": f"eval/ketahanan/{rendered.path}",
            "nama_kirim": rendered.send_name,
            "sha256": hashlib.sha256(rendered.content).hexdigest(),
        },
        "kebenaran_dokumen": document_truth(doc, rendered.printed, ubl=False, image=False),
        "harapan": {
            "jalur_awal": spec.route,
            "alasan_wajib": list(spec.reasons),
            "alasan_boleh_tambahan": list(spec.optional),
            "catatan": spec.note,
            "menunggu": spec.waiting,
        },
    }
    RobustnessLabel.model_validate(data)  # never write a label that does not fit the schema
    return data


def dump_label(data: dict[str, Any]) -> bytes:
    text = yaml.dump(
        data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=10_000, indent=2
    )
    return (HEADER + text).encode("utf-8")


def render_daftar(rows: list[list[str]]) -> str:
    lines = [INTRO, "| " + " | ".join(COLUMNS) + " |\n", "|" + "---|" * len(COLUMNS) + "\n"]
    lines += ["| " + " | ".join(row) + " |\n" for row in rows]
    return "".join(lines)


def assemble_robustness() -> dict[str, bytes]:
    ds = get_dataset()
    files: dict[str, bytes] = {}
    rows = []
    for index, spec in enumerate(ROBUST, start=1):
        doc = build_document(ds, spec, index)
        rendered = render_file(spec, doc)
        files[rendered.path] = rendered.content
        files[f"skenario/{spec.id}.yaml"] = dump_label(label_data(spec, doc, rendered))
        name = rendered.path.rsplit("/", 1)[-1]
        style = f"{spec.format} ({spec.image_style})" if spec.image_style else spec.format
        route = spec.route + (f" (menunggu {spec.waiting})" if spec.waiting else "")
        rows.append(
            [
                f"[{spec.id}](skenario/{spec.id}.yaml)",
                ", ".join(spec.traits),
                spec.vendor,
                spec.po,
                f"`{doc.number}`",
                style,
                f"[{name}]({rendered.path})",
                rupiah_plain(doc.total),
                route,
            ]
        )
    files["DAFTAR.md"] = render_daftar(rows).encode("utf-8")
    return files
