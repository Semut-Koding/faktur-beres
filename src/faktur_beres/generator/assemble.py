"""Assemble all generator outputs (labels, files, overview) in memory."""

from datetime import timedelta

from faktur_beres.dataset import get_dataset
from faktur_beres.generator.daftar import render_daftar
from faktur_beres.generator.documents import BuiltScenario, build_catalog
from faktur_beres.generator.formatting import rupiah_plain
from faktur_beres.generator.labels import dump_label, label_data
from faktur_beres.generator.model import InvoiceDoc, RenderedFile
from faktur_beres.generator.naming import pdf_name, photo_name, scan_name, ubl_name
from faktur_beres.generator import render_image, render_ubl
from faktur_beres.generator.render_pdf import PRINTED_PARTIES as PDF_PARTIES
from faktur_beres.generator.render_pdf import render_pdf
from faktur_beres.generator.styles import STYLES

#: Scans saved as PNG; the other scans are greyscale JPEG.
SCANS_AS_PNG = frozenset({"DUP-03"})
REPLY_PHOTO_DELAY = timedelta(days=2)


def render_file(b: BuiltScenario, doc: InvoiceDoc, *, reply: bool) -> RenderedFile:
    s = b.scenario
    key = f"{s.id}/balasan" if reply else s.id
    stem = f"berkas/{s.id}/{'balasan' if reply else 'faktur'}"
    if s.format == "ubl":
        return RenderedFile(
            f"{stem}.xml",
            ubl_name(doc.number),
            render_ubl.render_ubl(doc),
            render_ubl.PRINTED_PARTIES,
        )
    pdf = render_pdf(doc, STYLES[s.vendor])
    if s.format == "pdf":
        return RenderedFile(f"{stem}.pdf", pdf_name(doc.number), pdf, PDF_PARTIES)
    day = doc.issue_date + (REPLY_PHOTO_DELAY if reply else timedelta())
    if s.image_style == "foto":
        content = render_image.render_photo(pdf, key, note=doc.photo_note)
        printed = render_image.PRINTED_PARTIES
        return RenderedFile(f"{stem}.jpg", photo_name(key, day), content, printed)
    extension = "png" if s.id in SCANS_AS_PNG else "jpg"
    content = render_image.render_scan(pdf, key, png=extension == "png")
    name = scan_name(key, day, extension)
    return RenderedFile(f"{stem}.{extension}", name, content, render_image.PRINTED_PARTIES)


def daftar_row(b: BuiltScenario, original: RenderedFile, reply: RenderedFile | None) -> list[str]:
    s = b.scenario
    files = [f"[{original.path.rsplit('/', 1)[-1]}]({original.path})"]
    if reply is not None:
        files.append(f"[{reply.path.rsplit('/', 1)[-1]}]({reply.path})")
    fmt = f"{s.format} ({s.image_style})" if s.image_style else s.format
    return [
        f"[{s.id}](skenario/{s.id}.yaml)",
        s.category,
        s.vendor,
        f"`{b.doc.number}`",
        fmt,
        s.channel,
        ", ".join(files),
        rupiah_plain(b.doc.total),
        rupiah_plain(b.bill.total) if b.bill else "-",
        f"{s.route} → `{s.final_status}`",
    ]


def assemble() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    rows = []
    for b in build_catalog(get_dataset()):
        original = render_file(b, b.doc, reply=False)
        files[original.path] = original.content
        reply = None
        if b.reply_doc is not None:
            reply = render_file(b, b.reply_doc, reply=True)
            files[reply.path] = reply.content
        files[f"skenario/{b.scenario.id}.yaml"] = dump_label(label_data(b, original, reply))
        rows.append(daftar_row(b, original, reply))
    files["DAFTAR.md"] = render_daftar(rows).encode("utf-8")
    return files
