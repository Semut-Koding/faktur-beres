"""Render the robustness documents (M3.6): one generic layout with real-world variations.

ReportLab with built-in fonts, deterministic (``invariant = 1``; every random choice is seeded from
the document ID). Variations: paper size (A4, Letter, F4), Indonesian or English number and date
style, a table without product codes, prices that include PPN, the printed PPN label, separators in
NPWP and NSFP, a table that continues on a second page, extra pages (a self-made tax-invoice summary
and a delivery note), a fictitious company stamp and a scribbled signature over the text, a rubber
stamp or a handwritten note with an instruction, and raster versions (a scan without text layer, a
slightly rotated scan, a phone photo).

Nothing imitates an official mark: no QR code, no DJP layout, no meterai, no real logo. The stamp is
a circle with the company name from the dataset; the signature is a random curve. Every page and
every image carries the synthetic-document mark.
"""

from io import BytesIO

import pypdfium2 as pdfium
from PIL import Image, ImageChops
from reportlab import rl_config
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen.canvas import Canvas

from faktur_beres.generator.determinism import rng
from faktur_beres.generator.formatting import long_date
from faktur_beres.generator.model import InvoiceDoc
from faktur_beres.generator.render_image import _noise, _save_jpeg, render_photo
from faktur_beres.generator.render_pdf import CREATOR, SYNTHETIC_MARK
from faktur_beres.generator.robust_catalog import RobustSpec

rl_config.invariant = 1

PAPERS = {"A4": A4, "Letter": LETTER, "F4": (215 * mm, 330 * mm)}
EN_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MARGIN = 42.0
BOTTOM = 70.0
DPI = 150
INK = (0.08, 0.08, 0.1)
GREY = (0.4, 0.4, 0.4)
STAMP_BLUE = (0.12, 0.2, 0.62)
STAMP_RED = (0.78, 0.1, 0.12)
PEN_BLUE = (0.1, 0.18, 0.55)

LABELS = {
    False: {
        "title": "FAKTUR",
        "number": "Nomor",
        "date": "Tanggal",
        "due": "Jatuh Tempo",
        "po": "No. PO",
        "to": "Kepada:",
        "no": "No",
        "code": "Kode",
        "description": "Deskripsi",
        "qty": "Qty",
        "unit": "Satuan",
        "price": "Harga Satuan",
        "amount": "Jumlah",
        "subtotal": "Harga Jual",
        "dpp": "DPP Nilai Lain (11/12)",
        "total": "Total",
        "nsfp": "No. Seri Faktur Pajak",
        "tax_date": "Tanggal Faktur Pajak",
        "bank": "Bank",
        "account": "No. Rekening",
        "holder": "Atas Nama",
        "regards": "Hormat kami,",
        "finance": "Bagian Keuangan",
        "continued": "(lanjutan)",
        "page": "Halaman {n} dari {total}",
        "attached": "Faktur pajak terlampir.",
    },
    True: {
        "title": "INVOICE",
        "number": "Invoice No.",
        "date": "Date",
        "due": "Due Date",
        "po": "PO No.",
        "to": "Bill To:",
        "no": "No",
        "code": "Code",
        "description": "Description",
        "qty": "Qty",
        "unit": "Unit",
        "price": "Unit Price",
        "amount": "Amount",
        "subtotal": "Subtotal",
        "dpp": "DPP (11/12)",
        "total": "Total",
        "nsfp": "Tax Invoice Serial No.",
        "tax_date": "Tax Invoice Date",
        "bank": "Bank",
        "account": "Account No.",
        "holder": "Account Name",
        "regards": "Sincerely,",
        "finance": "Finance Department",
        "continued": "(continued)",
        "page": "Page {n} of {total}",
        "attached": "Tax invoice attached.",
    },
}


def thousands(n: int, sep: str) -> str:
    return f"{n:,}".replace(",", sep)


class Sheet:
    """A canvas with a cursor, page breaks, the footer, and the document's formats."""

    def __init__(self, doc: InvoiceDoc, spec: RobustSpec) -> None:
        self.doc = doc
        self.spec = spec
        self.width, self.height = PAPERS[spec.paper]
        self.buffer = BytesIO()
        self.c = Canvas(self.buffer, pagesize=(self.width, self.height), invariant=1)
        self.c.setTitle(f"Faktur {doc.number}")
        self.c.setAuthor(doc.seller.name)
        self.c.setCreator(CREATOR)
        self.label = LABELS[spec.english]
        self.y = self.height - MARGIN
        self.page_number = 1

    # -- formats -----------------------------------------------------------------------------

    def amount(self, n: int) -> str:
        return f"{thousands(n, ',')}.00" if self.spec.english else thousands(n, ".")

    def money(self, n: int) -> str:
        return f"IDR {self.amount(n)}" if self.spec.english else f"Rp {self.amount(n)}"

    def qty(self, n: int) -> str:
        return thousands(n, "," if self.spec.english else ".")

    def date(self, d) -> str:  # type: ignore[no-untyped-def]
        if self.spec.english:
            return f"{d.day} {EN_MONTHS[d.month - 1]} {d.year}"
        return long_date(d)

    # -- drawing -----------------------------------------------------------------------------

    def text(self, x: float, y: float, value: str, *, size: float = 9, bold: bool = False,
             color: tuple[float, float, float] = INK, align: str = "left") -> None:  # fmt: skip
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        self.c.setFillColorRGB(*color)
        if align == "right":
            self.c.drawRightString(x, y, value)
        elif align == "center":
            self.c.drawCentredString(x, y, value)
        else:
            self.c.drawString(x, y, value)

    def line(self, x1: float, y: float, x2: float, *, width: float = 0.5) -> None:
        self.c.setStrokeColorRGB(*INK)
        self.c.setLineWidth(width)
        self.c.line(x1, y, x2, y)

    def footer(self, total_pages: int | None = None) -> None:
        self.text(self.width / 2, 24, SYNTHETIC_MARK, size=7, color=GREY, align="center")
        if total_pages:
            page = self.label["page"].format(n=self.page_number, total=total_pages)
            self.text(self.width - MARGIN, 24, page, size=7, color=GREY, align="right")

    def new_page(self, total_pages: int | None = None) -> None:
        self.footer(total_pages)
        self.c.showPage()
        self.page_number += 1
        self.y = self.height - MARGIN

    def finish(self, total_pages: int | None = None) -> bytes:
        self.footer(total_pages)
        self.c.showPage()
        self.c.save()
        return self.buffer.getvalue()


# -- the invoice page(s) ------------------------------------------------------------------------


def _header(s: Sheet) -> None:
    d, label = s.doc, s.label
    left, right = MARGIN, s.width - MARGIN
    s.text(left, s.y, d.seller.name, size=14, bold=True)
    s.text(right, s.y, label["title"], size=18, bold=True, align="right")
    y = s.y - 16
    seller = [d.seller.street, f"{d.seller.city}, {d.seller.state} {d.seller.zip}"]
    if d.seller.npwp:
        seller.append(f"NPWP: {d.seller.npwp}")
    if d.seller.email:
        seller.append(f"Email: {d.seller.email}")
    for value in seller:
        s.text(left, y, value, size=8.5)
        y -= 11
    meta = [(label["number"], d.number), (label["date"], s.date(d.issue_date)),
            (label["due"], s.date(d.due_date))]  # fmt: skip
    if d.po_ref:
        meta.append((label["po"], d.po_ref))
    my = s.y - 20
    for key, value in meta:
        s.text(right - 170, my, key, size=8.5)
        s.text(right, my, value, size=8.5, bold=True, align="right")
        my -= 12
    s.y = min(y, my) - 12
    s.text(left, s.y, label["to"], size=8.5, bold=True)
    s.y -= 12
    buyer = [d.buyer.name, d.buyer.street, f"{d.buyer.city}, {d.buyer.state} {d.buyer.zip}"]
    buyer.append(f"NPWP: {d.buyer.npwp}")
    for value in buyer:
        s.text(left, s.y, value, size=8.5)
        s.y -= 11
    s.y -= 10


def _columns(s: Sheet) -> list[tuple[str, float, str]]:
    label = s.label
    cols = [(label["no"], 24, "left")]
    if s.spec.show_codes:
        cols.append((label["code"], 64, "left"))
    cols += [
        (label["description"], 0, "left"),  # takes the remaining width
        (label["qty"], 50, "right"),
        (label["unit"], 46, "left"),
        (label["price"], 82, "right"),
        (label["amount"], 92, "right"),
    ]
    rest = s.width - 2 * MARGIN - sum(width for _, width, _ in cols)
    return [(name, width or rest, align) for name, width, align in cols]


def _table_header(s: Sheet, cols: list[tuple[str, float, str]]) -> None:
    x = MARGIN
    s.line(MARGIN, s.y + 10, s.width - MARGIN, width=0.8)
    for name, width, align in cols:
        anchor = x + width - 4 if align == "right" else x + 3
        s.text(anchor, s.y, name, size=8.5, bold=True, align=align)
        x += width
    s.line(MARGIN, s.y - 4, s.width - MARGIN, width=0.8)
    s.y -= 16


def _table(s: Sheet, pages: int | None) -> None:
    d, spec = s.doc, s.spec
    cols = _columns(s)
    _table_header(s, cols)
    for index, line in enumerate(d.lines, start=1):
        cells = [str(index)]
        if spec.show_codes:
            cells.append(line.code or "-")
        description_width = next(w for name, w, _ in cols if name == s.label["description"])
        wrapped = simpleSplit(line.description, "Helvetica", 8.5, description_width - 6) or [""]
        cells += [wrapped[0], s.qty(line.qty), line.unit, s.amount(line.unit_price),
                  s.amount(line.amount)]  # fmt: skip
        x = MARGIN
        for (_, width, align), value in zip(cols, cells, strict=True):
            anchor = x + width - 4 if align == "right" else x + 3
            s.text(anchor, s.y, value, size=8.5, align=align)
            x += width
        for extra in wrapped[1:]:
            s.y -= 11
            s.text(MARGIN + sum(w for _, w, _ in cols[: 2 if spec.show_codes else 1]) + 3, s.y,
                   extra, size=8.5)  # fmt: skip
        s.y -= 14
        if spec.first_page_lines == index and index < len(d.lines):
            s.new_page(pages)
            s.text(MARGIN, s.y, f"{s.label['title']} {d.number} {s.label['continued']}", size=9,
                   bold=True)  # fmt: skip
            s.y -= 22
            _table_header(s, cols)
    s.line(MARGIN, s.y + 8, s.width - MARGIN, width=0.8)
    s.y -= 8


def _totals(s: Sheet) -> None:
    d, spec, label = s.doc, s.spec, s.label
    right = s.width - MARGIN
    rows: list[tuple[str, str, bool]] = []
    if spec.tax_inclusive:
        gross = sum(line.amount for line in d.lines)
        rows.append(("Jumlah (harga termasuk PPN)", s.money(gross), False))
        rows.append(("Harga Jual (sebelum PPN)", s.money(d.subtotal), False))
    else:
        rows.append((label["subtotal"], s.money(d.subtotal), False))
    if spec.print_dpp and d.dpp is not None:
        rows.append((label["dpp"], s.money(d.dpp), False))
    rows.append(("VAT 12%" if spec.english else spec.ppn_label, s.money(d.ppn), False))
    rows.append((label["total"], s.money(d.total), True))
    for name, value, bold in rows:
        s.text(right - 220, s.y, name, size=9, bold=bold)
        s.text(right, s.y, value, size=9, bold=bold, align="right")
        s.y -= 13
    if spec.tax_inclusive:
        s.text(MARGIN, s.y + 13 * len(rows) - 2, "Harga satuan sudah termasuk PPN.", size=8.5)
    s.y -= 6


def _closing(s: Sheet) -> tuple[float, float]:
    """Tax, bank, notes and signature; returns where the signature line is."""
    d, spec, label = s.doc, s.spec, s.label
    left = MARGIN
    lines: list[str] = []
    if spec.multi_document:
        lines.append(label["attached"])
    elif d.nsfp:
        lines.append(f"{label['nsfp']}: {d.nsfp}")
        if d.tax_invoice_date:
            lines.append(f"{label['tax_date']}: {s.date(d.tax_invoice_date)}")
    if d.bank:
        lines += [f"{label['bank']}: {d.bank.bank}", f"{label['account']}: {d.bank.number}",
                  f"{label['holder']}: {d.bank.holder}"]  # fmt: skip
    for note in d.notes:
        if note not in (spec.stamp_instruction, spec.handwritten_note):
            lines += simpleSplit(note, "Helvetica", 8.5, s.width / 2)
    y = s.y
    for value in lines:
        s.text(left, y, value, size=8.5)
        y -= 11
    x = s.width - MARGIN - 170
    sy = s.y
    s.text(x, sy, label["regards"], size=9)
    s.line(x, sy - 44, x + 140)
    s.text(x, sy - 56, label["finance"], size=9, bold=True)
    s.text(x, sy - 68, d.seller.name, size=8)
    s.y = min(y, sy - 80)
    return x, sy - 44


def _stamp(s: Sheet, x: float, y: float, key: str) -> None:
    """A fictitious round company stamp over the signature, and a scribbled signature."""
    r = rng(key, "stempel")
    c = s.c
    c.saveState()
    c.translate(x + 40, y + 18)
    c.rotate(r.uniform(-18, -8))
    c.setStrokeColorRGB(*STAMP_BLUE)
    c.setFillColorRGB(*STAMP_BLUE)
    c.setStrokeAlpha(0.8)
    c.setFillAlpha(0.8)
    c.setLineWidth(1.6)
    c.circle(0, 0, 38, stroke=1, fill=0)
    c.setLineWidth(0.8)
    c.circle(0, 0, 31, stroke=1, fill=0)
    words = s.doc.seller.name.split()
    rows = [" ".join(words[: len(words) // 2]), " ".join(words[len(words) // 2 :])]
    c.setFont("Helvetica-Bold", 6.5)
    for i, row in enumerate(rows):
        c.drawCentredString(0, 4 - i * 9, row.upper())
    c.restoreState()
    _scribble(s, x + 8, y + 6, key)


def _scribble(s: Sheet, x: float, y: float, key: str) -> None:
    r = rng(key, "tanda-tangan")
    c = s.c
    c.saveState()
    c.setStrokeColorRGB(*PEN_BLUE)
    c.setLineWidth(1.1)
    path = c.beginPath()
    px, py = x, y
    path.moveTo(px, py)
    for _ in range(5):
        nx, ny = px + r.uniform(14, 30), y + r.uniform(-10, 16)
        path.curveTo(px + r.uniform(-8, 18), py + r.uniform(8, 26), nx + r.uniform(-18, 8),
                     ny + r.uniform(-26, -6), nx, ny)  # fmt: skip
        px, py = nx, ny
    c.drawPath(path, stroke=1, fill=0)
    c.restoreState()


def _rubber_stamp(s: Sheet, text: str, key: str) -> None:
    """A red rectangular rubber stamp with ``text`` across the table area."""
    r = rng(key, "stempel-karet")
    c = s.c
    c.saveState()
    c.translate(s.width * 0.52, s.height * 0.42)
    c.rotate(r.uniform(6, 12))
    c.setStrokeColorRGB(*STAMP_RED)
    c.setFillColorRGB(*STAMP_RED)
    c.setStrokeAlpha(0.85)
    c.setFillAlpha(0.85)
    rows = simpleSplit(text, "Helvetica-Bold", 10, 210)
    height = 14 * len(rows) + 10
    c.setLineWidth(2)
    c.rect(-118, -height / 2, 236, height, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 10)
    for i, row in enumerate(rows):
        c.drawCentredString(0, height / 2 - 16 - i * 14, row)
    c.restoreState()


def _handwriting(s: Sheet, text: str, key: str) -> None:
    """A note written by hand in blue ink in the right margin."""
    r = rng(key, "tulisan-tangan")
    c = s.c
    c.saveState()
    c.translate(s.width - MARGIN - 205, s.height * 0.47)
    c.rotate(r.uniform(-6, -3))
    c.setFillColorRGB(*PEN_BLUE)
    c.setFont("Helvetica-Oblique", 11)
    for i, row in enumerate(simpleSplit(text, "Helvetica-Oblique", 11, 200)):
        c.drawString(0, -i * 15, row)
    c.restoreState()


def _tax_summary_page(s: Sheet, pages: int | None) -> None:
    d = s.doc
    s.new_page(pages)
    s.text(MARGIN, s.y, "RINGKASAN FAKTUR PAJAK", size=14, bold=True)
    s.y -= 16
    s.text(MARGIN, s.y, "Ringkasan internal penjual untuk lampiran faktur.", size=8.5, color=GREY)
    s.y -= 26
    rows = [
        ("Nomor Seri Faktur Pajak", d.nsfp or "-"),
        ("Tanggal Faktur Pajak", s.date(d.tax_invoice_date) if d.tax_invoice_date else "-"),
        ("Penjual", d.seller.name),
        ("NPWP Penjual", d.seller.npwp or "-"),
        ("Pembeli", d.buyer.name),
        ("NPWP Pembeli", d.buyer.npwp or "-"),
        ("Untuk Faktur Nomor", d.number),
        ("Harga Jual", s.money(d.subtotal)),
        ("DPP Nilai Lain", s.money(d.dpp) if d.dpp is not None else "-"),
        ("PPN", s.money(d.ppn)),
    ]
    for key, value in rows:
        s.text(MARGIN, s.y, key, size=9)
        s.text(MARGIN + 170, s.y, f": {value}", size=9, bold=True)
        s.y -= 15


def _delivery_note_page(s: Sheet, pages: int | None, key: str) -> None:
    d = s.doc
    s.new_page(pages)
    number = f"SJ-{d.issue_date:%y%m}-{rng(key, 'surat-jalan').randint(100, 999)}"
    s.text(MARGIN, s.y, "SURAT JALAN", size=14, bold=True)
    s.text(s.width - MARGIN, s.y, number, size=10, bold=True, align="right")
    s.y -= 18
    s.text(MARGIN, s.y, f"Kepada: {d.buyer.name}", size=9)
    s.y -= 12
    s.text(MARGIN, s.y, f"Alamat kirim: {d.buyer.street}, {d.buyer.city}", size=9)
    s.y -= 22
    s.text(MARGIN, s.y, "Barang", size=9, bold=True)
    s.text(s.width - MARGIN - 120, s.y, "Jumlah dikirim", size=9, bold=True)
    s.y -= 14
    for line in d.lines:
        s.text(MARGIN, s.y, line.description, size=9)
        s.text(s.width - MARGIN - 120, s.y, f"{s.qty(line.qty)} {line.unit}", size=9)
        s.y -= 13
    s.y -= 8
    boxes = rng(key, "koli").randint(2, 9)
    s.text(MARGIN, s.y, f"Jumlah koli: {boxes}", size=9)
    s.y -= 40
    for i, name in enumerate(("Pengirim", "Sopir", "Penerima")):
        x = MARGIN + i * 170
        s.text(x, s.y, name, size=9)
        s.line(x, s.y - 40, x + 120)


def render_pdf_document(doc: InvoiceDoc, spec: RobustSpec, key: str) -> bytes:
    """The vector PDF of a robustness document (before any raster step)."""
    pages = None
    if spec.multi_document:
        pages = 3
    elif spec.first_page_lines is not None:
        pages = 2
    s = Sheet(doc, spec)
    _header(s)
    _table(s, pages)
    _totals(s)
    x, y = _closing(s)
    if spec.stamp:
        _stamp(s, x, y, key)
    if spec.stamp_instruction:
        _rubber_stamp(s, spec.stamp_instruction, key)
    if spec.handwritten_note:
        _handwriting(s, spec.handwritten_note, key)
    if spec.multi_document:
        _tax_summary_page(s, pages)
        _delivery_note_page(s, pages, key)
    return s.finish(pages)


# -- raster versions ----------------------------------------------------------------------------


def rasterize(pdf: bytes, dpi: int = DPI) -> list[Image.Image]:
    document = pdfium.PdfDocument(pdf)
    try:
        return [
            document[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(document))
        ]
    finally:
        document.close()


def _scan(page: Image.Image, key: str, skew: float) -> Image.Image:
    grey = page.convert("L").point(lambda v: 12 + v * 234 // 255)
    turned = grey.rotate(skew, Image.Resampling.BICUBIC, expand=True, fillcolor=244)
    return ImageChops.add(turned, _noise(key, turned.size, 9), 1.0, -128)


def scanned_pdf(pdf: bytes, spec: RobustSpec, key: str) -> bytes:
    """Every page as a greyscale scan inside a PDF: no text layer at all."""
    width, height = PAPERS[spec.paper]
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=(width, height), invariant=1)
    canvas.setTitle("Scan")
    canvas.setCreator(CREATOR)
    for number, page in enumerate(rasterize(pdf), start=1):
        scan = _scan(page, f"{key}/{number}", rng(key, "miring", str(number)).uniform(-0.6, 0.6))
        image = BytesIO()
        scan.save(image, format="JPEG", quality=80, optimize=True, progressive=False)
        canvas.drawImage(ImageReader(BytesIO(image.getvalue())), 0, 0, width, height)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def scanned_image(pdf: bytes, key: str, skew: float) -> bytes:
    """The first page as a greyscale JPEG scan, rotated by ``skew`` degrees."""
    return _save_jpeg(_scan(rasterize(pdf)[0], key, skew))


def photo(pdf: bytes, key: str) -> bytes:
    """A phone photo of the printed first page (the generator's photo renderer)."""
    return render_photo(pdf, key)
