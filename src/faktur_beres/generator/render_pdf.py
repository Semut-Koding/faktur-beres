"""Render an :class:`InvoiceDoc` as an A4 PDF with ReportLab (built-in fonts only).

Deterministic: ``rl_config.invariant = 1`` fixes timestamps and document IDs, and all content
comes from the model and the vendor style. Metadata: Title ``Faktur <nomor>``, Author the vendor
name, Creator ``Faktur Beres generator``. Every page carries the synthetic-document mark.
"""

from collections.abc import Callable
from dataclasses import replace
from io import BytesIO

from reportlab import rl_config
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen.canvas import Canvas

from faktur_beres.generator.formatting import amount, date_text, money, quantity
from faktur_beres.generator.model import InvoiceDoc
from faktur_beres.generator.styles import VendorStyle
from faktur_beres.generator.terbilang import terbilang_rupiah

rl_config.invariant = 1

SYNTHETIC_MARK = "DOKUMEN SINTETIS – UNTUK PENGUJIAN"
CREATOR = "Faktur Beres generator"
PAGE_W, PAGE_H = A4
MARGIN = 40.0
CONTENT_W = PAGE_W - 2 * MARGIN
BLACK = (0.0, 0.0, 0.0)
GREY = (0.35, 0.35, 0.35)
WHITE = (1.0, 1.0, 1.0)
LIGHT = (0.95, 0.95, 0.95)

Color = tuple[float, float, float]


def tint(color: Color, amount: float) -> Color:
    """Mix ``color`` with white (amount 0..1 of white)."""
    return tuple(c + (1 - c) * amount for c in color)  # type: ignore[return-value]


class Page:
    """Small drawing helper around a canvas with the vendor's fonts and formats."""

    def __init__(self, canvas: Canvas, doc: InvoiceDoc, style: VendorStyle) -> None:
        self.c = canvas
        self.doc = doc
        self.style = style
        self.regular, self.bold, self.italic = style.fonts

    # -- formatting -------------------------------------------------------------------------

    def date(self, value) -> str:
        return date_text(value, self.style.date_format)

    def money(self, value: int) -> str:
        return money(value, self.style.number_format)

    def amount(self, value: int) -> str:
        return amount(value, self.style.number_format)

    def qty(self, value: int) -> str:
        return quantity(value, self.style.number_format)

    @property
    def dpp_label(self) -> str:
        return "DPP Nilai Lain (11/12)" if self.doc.dpp_basis == "nilai_lain" else "DPP"

    @property
    def title(self) -> str:
        return self.doc.title or self.style.title

    # -- drawing primitives -------------------------------------------------------------------

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        font: str | None = None,
        size: float = 9,
        color: Color = BLACK,
        align: str = "left",
    ) -> None:
        self.c.setFont(font or self.regular, size)
        self.c.setFillColorRGB(*color)
        if align == "right":
            self.c.drawRightString(x, y, value)
        elif align == "center":
            self.c.drawCentredString(x, y, value)
        else:
            self.c.drawString(x, y, value)

    def wrap(self, value: str, width: float, *, font: str | None = None, size: float = 9):
        return simpleSplit(value, font or self.regular, size, width) or [""]

    def paragraph(
        self,
        x: float,
        y: float,
        value: str,
        width: float,
        *,
        size: float = 9,
        leading=None,
        font: str | None = None,
        color: Color = BLACK,
    ) -> float:
        """Draw wrapped text from baseline ``y`` downwards; return the next free baseline."""
        leading = leading or size * 1.3
        for line in self.wrap(value, width, font=font, size=size):
            self.text(x, y, line, font=font, size=size, color=color)
            y -= leading
        return y

    def rule(self, x1, y, x2, *, width: float = 0.6, color: Color = BLACK, dash=None) -> None:
        self.c.setStrokeColorRGB(*color)
        self.c.setLineWidth(width)
        self.c.setDash(*(dash or ()))
        self.c.line(x1, y, x2, y)
        self.c.setDash()

    def box(
        self,
        x,
        y,
        w,
        h,
        *,
        fill: Color | None = None,
        stroke: Color | None = BLACK,
        width: float = 0.6,
    ) -> None:
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        if stroke is not None:
            self.c.setStrokeColorRGB(*stroke)
            self.c.setLineWidth(width)
        self.c.rect(x, y, w, h, stroke=int(stroke is not None), fill=int(fill is not None))

    def logo(self, x: float, y: float, size: float, *, fill: Color, ink: Color) -> None:
        """Geometric logo with the vendor initials; (x, y) is the lower-left corner."""
        c, s = self.c, size
        c.setFillColorRGB(*fill)
        c.setStrokeColorRGB(*fill)
        shape = self.style.shape
        if shape == "lingkaran":
            c.circle(x + s / 2, y + s / 2, s / 2, stroke=0, fill=1)
        elif shape == "persegi":
            c.roundRect(x, y, s, s, s * 0.18, stroke=0, fill=1)
        else:
            if shape == "belah_ketupat":
                points = [(0.5, 0.0), (1.0, 0.5), (0.5, 1.0), (0.0, 0.5)]
            elif shape == "segitiga":
                points = [(0.0, 0.05), (1.0, 0.05), (0.5, 1.0)]
            else:  # segienam
                points = [
                    (0.25, 0.0),
                    (0.75, 0.0),
                    (1.0, 0.5),
                    (0.75, 1.0),
                    (0.25, 1.0),
                    (0.0, 0.5),
                ]
            path = c.beginPath()
            path.moveTo(x + points[0][0] * s, y + points[0][1] * s)
            for px, py in points[1:]:
                path.lineTo(x + px * s, y + py * s)
            path.close()
            c.drawPath(path, stroke=0, fill=1)
        size_text = s * (0.26 if len(self.style.initials) > 2 else 0.34)
        baseline = y + s * (0.32 if shape == "segitiga" else 0.5) - size_text * 0.35
        self.text(
            x + s / 2,
            baseline,
            self.style.initials,
            font=self.bold,
            size=size_text,
            color=ink,
            align="center",
        )

    def footer(self) -> None:
        self.rule(MARGIN, 34, PAGE_W - MARGIN, width=0.3, color=GREY)
        self.text(PAGE_W / 2, 22, SYNTHETIC_MARK, size=7, color=GREY, align="center")

    def hidden(self, x: float, y: float) -> None:
        """White text in the text layer: invisible on the white page (prompt-injection test)."""
        for i, value in enumerate(self.doc.hidden_texts):
            self.text(x, y - i * 5, value, font="Helvetica", size=3.5, color=WHITE)

    # -- shared content -------------------------------------------------------------------------

    def seller_lines(self) -> list[str]:
        s = self.doc.seller
        lines = [s.street, f"{s.city}, {s.state} {s.zip}"]
        if s.npwp:
            lines.append(f"NPWP: {s.npwp}")
        if s.email:
            lines.append(f"Email: {s.email}")
        return lines

    def buyer_lines(self) -> list[str]:
        b = self.doc.buyer
        return [b.name, b.street, f"{b.city}, {b.state} {b.zip}", f"NPWP: {b.npwp}"]

    def meta_rows(self) -> list[tuple[str, str]]:
        d = self.doc
        rows = [
            ("Nomor", d.number),
            ("Tanggal", self.date(d.issue_date)),
            ("Jatuh Tempo", self.date(d.due_date)),
        ]
        if d.po_ref:
            rows.append(("No. PO", d.po_ref))
        return rows

    def total_rows(self, subtotal_label: str = "Harga Jual") -> list[tuple[str, str]]:
        d = self.doc
        rows = [(subtotal_label, self.money(d.subtotal))]
        if d.dpp is not None:
            rows.append((self.dpp_label, self.money(d.dpp)))
        rows += [("PPN 12%", self.money(d.ppn)), ("Total", self.money(d.total))]
        return rows

    def tax_lines(self) -> list[str]:
        d = self.doc
        if not d.nsfp:
            return []
        lines = [f"No. Seri Faktur Pajak: {d.nsfp}"]
        if d.tax_invoice_date:
            lines.append(f"Tanggal Faktur Pajak: {self.date(d.tax_invoice_date)}")
        return lines

    def bank_lines(self) -> list[str]:
        b = self.doc.bank
        if b is None:
            return []
        return [f"Bank: {b.bank}", f"No. Rekening: {b.number}", f"Atas Nama: {b.holder}"]

    def terbilang(self) -> str | None:
        if not self.style.terbilang:
            return None
        words = terbilang_rupiah(self.doc.total)
        return f"Terbilang: {words[0].upper()}{words[1:]}"

    def signature(self, x: float, y: float, *, size: float = 9) -> None:
        self.text(x, y, "Hormat kami,", size=size)
        self.rule(x, y - 42, x + 130, width=0.5)
        self.text(x, y - 54, "Bagian Keuangan", font=self.bold, size=size)
        self.text(x, y - 54 - size * 1.3, self.doc.seller.name, size=size - 1)

    def table_rows(self) -> list[tuple[str, str, str, str, str, str, str]]:
        rows = []
        for i, line in enumerate(self.doc.lines, start=1):
            rows.append(
                (
                    str(i),
                    line.code or "-",
                    line.description,
                    self.qty(line.qty),
                    line.unit,
                    self.amount(line.unit_price),
                    self.amount(line.amount),
                )
            )
        return rows


# -- layout families ----------------------------------------------------------------------------

COLUMNS = (("No", 22), ("Kode", 62), ("Deskripsi", 183), ("Qty", 52), ("Satuan", 42),
           ("Harga Satuan", 72), ("Jumlah", 82))  # fmt: skip
RIGHT_ALIGNED = {3, 5, 6}


def draw_grid_table(
    p: Page,
    top: float,
    *,
    header_fill: Color | None,
    header_ink: Color,
    grid: bool,
    zebra: Color | None,
    size: float = 8.5,
) -> float:
    """Line-item table; returns the y below it."""
    x0 = MARGIN
    header_h, pad = size * 2.1, 3.5
    y = top
    if header_fill is not None:
        p.box(x0, y - header_h, CONTENT_W, header_h, fill=header_fill, stroke=None)
    x = x0
    for i, (name, width) in enumerate(COLUMNS):
        align_x = x + width - pad if i in RIGHT_ALIGNED else x + pad
        p.text(
            align_x,
            y - header_h + size * 0.75,
            name,
            font=p.bold,
            size=size,
            color=header_ink,
            align="right" if i in RIGHT_ALIGNED else "left",
        )
        x += width
    if grid:
        p.box(x0, y - header_h, CONTENT_W, header_h, stroke=BLACK, width=0.6)
    else:
        p.rule(x0, y - header_h, x0 + CONTENT_W, width=0.8)
    y -= header_h
    for index, row in enumerate(p.table_rows()):
        desc_lines = p.wrap(row[2], COLUMNS[2][1] - 2 * pad, size=size)
        row_h = max(1, len(desc_lines)) * size * 1.25 + size * 0.9
        if zebra is not None and index % 2 == 1:
            p.box(x0, y - row_h, CONTENT_W, row_h, fill=zebra, stroke=None)
        x = x0
        for i, (_, width) in enumerate(COLUMNS):
            baseline = y - size * 1.25
            if i == 2:
                for n, line in enumerate(desc_lines):
                    p.text(x + pad, baseline - n * size * 1.25, line, size=size)
            elif i in RIGHT_ALIGNED:
                p.text(x + width - pad, baseline, row[i], size=size, align="right")
            else:
                p.text(x + pad, baseline, row[i], size=size)
            x += width
        if grid:
            p.box(x0, y - row_h, CONTENT_W, row_h, stroke=BLACK, width=0.4)
        y -= row_h
    if grid:
        x = x0
        for _, width in COLUMNS[:-1]:
            x += width
            p.c.setLineWidth(0.4)
            p.c.line(x, top, x, y)
    else:
        p.rule(x0, y, x0 + CONTENT_W, width=0.8)
    return y


def draw_totals(
    p: Page,
    top: float,
    *,
    label_x: float,
    value_x: float,
    size: float = 9.5,
    total_fill: Color | None = None,
    total_ink: Color = BLACK,
    boxed: bool = False,
    subtotal_label: str = "Harga Jual",
) -> float:
    y = top
    rows = p.total_rows(subtotal_label)
    leading = size * 1.6
    if boxed:
        p.box(
            label_x - 6,
            y - leading * len(rows) - 4,
            value_x - label_x + 12,
            leading * len(rows) + 8,
            stroke=BLACK,
            width=0.6,
        )
    for i, (label, value) in enumerate(rows):
        last = i == len(rows) - 1
        if last and total_fill is not None:
            p.box(
                label_x - 6,
                y - leading + size * 0.1,
                value_x - label_x + 12,
                leading,
                fill=total_fill,
                stroke=None,
            )
        font = p.bold if last else p.regular
        ink = total_ink if last else BLACK
        p.text(label_x, y - leading + size * 0.55, label, font=font, size=size, color=ink)
        p.text(
            value_x,
            y - leading + size * 0.55,
            value,
            font=font,
            size=size,
            color=ink,
            align="right",
        )
        y -= leading
    return y


def draw_info_blocks(
    p: Page, top: float, width: float, *, size: float = 8.5, heading_color: Color = BLACK
) -> float:
    """Tax invoice, payment, and notes blocks on the left; returns the y below them."""
    y = top
    for title, lines in (("Faktur Pajak", p.tax_lines()), ("Pembayaran", p.bank_lines())):
        if not lines:
            continue
        p.text(MARGIN, y, title, font=p.bold, size=size, color=heading_color)
        y -= size * 1.35
        for line in lines:
            p.text(MARGIN, y, line, size=size)
            y -= size * 1.3
        y -= size * 0.6
    if p.doc.notes:
        p.text(MARGIN, y, "Catatan", font=p.bold, size=size, color=heading_color)
        y -= size * 1.35
        for note in p.doc.notes:
            y = p.paragraph(MARGIN, y, note, width, size=size)
    return y


def layout_modern(p: Page) -> None:
    accent = p.style.accent
    band_h = 96
    p.box(0, PAGE_H - band_h, PAGE_W, band_h, fill=accent, stroke=None)
    p.logo(MARGIN, PAGE_H - 78, 52, fill=WHITE, ink=accent)
    p.text(MARGIN + 64, PAGE_H - 40, p.doc.seller.name, font=p.bold, size=15, color=WHITE)
    y = PAGE_H - 54
    for line in p.seller_lines():
        p.text(MARGIN + 64, y, line, size=8, color=WHITE)
        y -= 10
    p.text(PAGE_W - MARGIN, PAGE_H - 44, p.title, font=p.bold, size=24, color=WHITE, align="right")

    top = PAGE_H - band_h - 26
    p.text(MARGIN, top, "Ditagihkan kepada", font=p.bold, size=9, color=accent)
    y = top - 13
    for i, line in enumerate(p.buyer_lines()):
        p.text(MARGIN, y, line, font=p.bold if i == 0 else p.regular, size=9)
        y -= 12
    my = top
    for label, value in p.meta_rows():
        p.text(PAGE_W - MARGIN - 190, my, label, size=9, color=GREY)
        p.text(PAGE_W - MARGIN, my, value, font=p.bold, size=9, align="right")
        my -= 13
    table_top = min(y, my) - 14
    y = draw_grid_table(
        p, table_top, header_fill=accent, header_ink=WHITE, grid=False, zebra=tint(accent, 0.9)
    )
    totals_y = draw_totals(
        p,
        y - 8,
        label_x=PAGE_W - MARGIN - 204,
        value_x=PAGE_W - MARGIN - 6,
        total_fill=accent,
        total_ink=WHITE,
    )
    words = p.terbilang()
    if words:
        totals_y = p.paragraph(
            PAGE_W - MARGIN - 210, totals_y - 12, words, 210, font=p.italic, size=8
        )
    info_y = draw_info_blocks(p, y - 22, 270, heading_color=accent)
    p.signature(PAGE_W - MARGIN - 150, min(totals_y, info_y) - 24)
    p.hidden(MARGIN, min(totals_y, info_y) - 110)
    p.footer()


def layout_klasik(p: Page) -> None:
    accent = p.style.accent
    p.logo(MARGIN, PAGE_H - 92, 54, fill=accent, ink=WHITE)
    p.text(
        PAGE_W / 2 + 20,
        PAGE_H - 54,
        p.doc.seller.name,
        font=p.bold,
        size=17,
        align="center",
        color=accent,
    )
    y = PAGE_H - 70
    for line in p.seller_lines():
        p.text(PAGE_W / 2 + 20, y, line, size=9, align="center")
        y -= 11
    p.rule(MARGIN, y - 4, PAGE_W - MARGIN, width=1.2, color=accent)
    p.rule(MARGIN, y - 7, PAGE_W - MARGIN, width=0.4, color=accent)
    y -= 34
    p.text(PAGE_W / 2, y, p.title, font=p.bold, size=18, align="center")
    title_w = p.c.stringWidth(p.title, p.bold, 18)
    p.rule(PAGE_W / 2 - title_w / 2, y - 3, PAGE_W / 2 + title_w / 2, width=0.8)

    box_top, box_h = y - 18, 70
    half = (CONTENT_W - 12) / 2
    p.box(MARGIN, box_top - box_h, half, box_h)
    p.box(MARGIN + half + 12, box_top - box_h, half, box_h)
    p.text(MARGIN + 6, box_top - 12, "Kepada Yth.", font=p.italic, size=9)
    by = box_top - 24
    for i, line in enumerate(p.buyer_lines()):
        p.text(MARGIN + 6, by, line, font=p.bold if i == 0 else p.regular, size=9)
        by -= 11
    my = box_top - 14
    for label, value in p.meta_rows():
        p.text(MARGIN + half + 18, my, f"{label}", size=9)
        p.text(MARGIN + half + 90, my, f": {value}", font=p.bold, size=9)
        my -= 13
    y = draw_grid_table(
        p,
        box_top - box_h - 16,
        header_fill=LIGHT,
        header_ink=BLACK,
        grid=True,
        zebra=None,
        size=8.8,
    )
    totals_y = draw_totals(
        p, y - 6, label_x=PAGE_W - MARGIN - 214, value_x=PAGE_W - MARGIN - 6, boxed=True, size=9.5
    )
    words = p.terbilang()
    if words:
        totals_y = p.paragraph(MARGIN, totals_y - 16, words, CONTENT_W, font=p.italic, size=9)
    info_y = draw_info_blocks(p, totals_y - 18, 300, size=9, heading_color=accent)
    p.signature(PAGE_W - MARGIN - 150, totals_y - 24, size=9.5)
    p.hidden(MARGIN, info_y - 30)
    p.footer()


def layout_ringkas(p: Page) -> None:
    accent = p.style.accent
    p.logo(MARGIN, PAGE_H - 70, 30, fill=accent, ink=WHITE)
    p.text(MARGIN + 38, PAGE_H - 52, p.doc.seller.name, font=p.bold, size=12)
    seller = " | ".join(p.seller_lines()[:2])
    p.text(MARGIN + 38, PAGE_H - 63, seller, size=7.5, color=GREY)
    p.text(MARGIN + 38, PAGE_H - 73, " | ".join(p.seller_lines()[2:]), size=7.5, color=GREY)
    p.text(PAGE_W - MARGIN, PAGE_H - 52, p.title, font=p.bold, size=14, align="right", color=accent)
    my = PAGE_H - 64
    for label, value in p.meta_rows():
        p.text(PAGE_W - MARGIN, my, f"{label}: {value}", size=7.5, align="right")
        my -= 9.5
    top = min(my, PAGE_H - 84) - 10
    p.rule(MARGIN, top, PAGE_W - MARGIN, width=0.5, color=accent)
    p.text(MARGIN, top - 13, "Kepada:", font=p.bold, size=8)
    buyer = p.buyer_lines()
    p.text(MARGIN + 38, top - 13, buyer[0], font=p.bold, size=8)
    p.text(MARGIN + 38, top - 23, f"{buyer[1]}, {buyer[2]}", size=7.5)
    p.text(MARGIN + 38, top - 33, buyer[3], size=7.5)
    y = draw_grid_table(
        p, top - 44, header_fill=None, header_ink=accent, grid=False, zebra=None, size=7.8
    )
    totals_y = draw_totals(
        p,
        y - 4,
        label_x=PAGE_W - MARGIN - 190,
        value_x=PAGE_W - MARGIN,
        size=8.5,
        subtotal_label="Subtotal",
    )
    p.rule(PAGE_W - MARGIN - 196, totals_y + 13, PAGE_W - MARGIN, width=0.5)
    words = p.terbilang()
    if words:
        totals_y = p.paragraph(
            PAGE_W - MARGIN - 190, totals_y - 10, words, 190, font=p.italic, size=7.5
        )
    info_y = draw_info_blocks(p, y - 16, 260, size=7.8, heading_color=accent)
    p.signature(PAGE_W - MARGIN - 150, min(totals_y, info_y) - 20, size=8.5)
    p.hidden(MARGIN, min(totals_y, info_y) - 100)
    p.footer()


def layout_dotmatrix(p: Page) -> None:
    size, lead = 8.6, 11.0
    char_w = p.c.stringWidth("M", p.regular, size)
    cols = int(CONTENT_W // char_w)
    x = MARGIN
    y = PAGE_H - 52

    def line(value: str = "", *, bold: bool = False) -> None:
        nonlocal y
        p.text(x, y, value, font=p.bold if bold else p.regular, size=size)
        y -= lead

    p.logo(PAGE_W - MARGIN - 34, PAGE_H - 78, 34, fill=p.style.accent, ink=WHITE)
    line(p.doc.seller.name.upper(), bold=True)
    for value in p.seller_lines():
        line(value.upper() if not value.startswith("Email") else value)
    line("=" * cols)
    line(f"{p.title:^{cols}}", bold=True)
    line("=" * cols)
    for label, value in p.meta_rows():
        line(f"{label.upper():<13}: {value}")
    line("-" * cols)
    buyer = p.buyer_lines()
    line(f"{'KEPADA':<13}: {buyer[0]}")
    line(f"{'':<13}  {buyer[1]}")
    line(f"{'':<13}  {buyer[2]}")
    line(f"{'':<13}  {buyer[3]}")
    line("-" * cols)
    widths = (3, 10, 34, 9, 7, 14, 16)
    header = ("NO", "KODE", "DESKRIPSI", "QTY", "SAT", "HARGA", "JUMLAH")

    def row(cells: tuple[str, ...]) -> str:
        out = []
        for i, (cell, width) in enumerate(zip(cells, widths, strict=True)):
            out.append(cell.rjust(width) if i in RIGHT_ALIGNED else cell.ljust(width))
        return " ".join(out)

    line(row(header), bold=True)
    line("-" * cols)
    for cells in p.table_rows():
        desc = p.wrap(cells[2], widths[2] * char_w, size=size)
        # dot-matrix printers wrap by characters; keep the code and amounts on the first line
        first = (cells[0], cells[1], desc[0][: widths[2]], cells[3], cells[4], cells[5], cells[6])
        line(row(first))
        for more in desc[1:]:
            line(row(("", "", more[: widths[2]], "", "", "", "")))
    line("-" * cols)
    label_w = cols - 22
    subtotal_label = "JUMLAH HARGA JUAL"
    for label, value in p.total_rows(subtotal_label):
        line(f"{label.upper():>{label_w}}  {value:>20}", bold=label == "Total")
    line("=" * cols)
    words = p.terbilang()
    if words:
        for part in p.wrap(words, CONTENT_W, size=size):
            line(part)
        line()
    for value in p.tax_lines() + [""] + p.bank_lines():
        line(value)
    if p.doc.notes:
        line()
        line("CATATAN:", bold=True)
        for note in p.doc.notes:
            for part in p.wrap(note, CONTENT_W, size=size):
                line(part)
    line()
    p.signature(PAGE_W - MARGIN - 150, y - 6, size=size)
    p.hidden(MARGIN, y - 90)
    p.footer()


LAYOUTS: dict[str, Callable[[Page], None]] = {
    "modern": layout_modern,
    "klasik": layout_klasik,
    "ringkas": layout_ringkas,
    "dotmatrix": layout_dotmatrix,
}


def render_pdf(doc: InvoiceDoc, style: VendorStyle, *, include_hidden: bool = True) -> bytes:
    """One-page A4 PDF of ``doc`` in the vendor ``style``."""
    if not include_hidden and doc.hidden_texts:
        doc = replace(doc, hidden_texts=())
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=A4, invariant=1, pageCompression=1)
    canvas.setTitle(f"Faktur {doc.number}")
    canvas.setAuthor(doc.seller.name)
    canvas.setCreator(CREATOR)
    LAYOUTS[style.layout](Page(canvas, doc, style))
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()
