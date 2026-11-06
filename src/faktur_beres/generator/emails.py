"""E-mail metadata for invoices that arrive by e-mail (channel ``email``).

Sender ``Bagian Penagihan <vendor> <billing e-mail>``, recipient ``ap@arunika.test``, a neutral
subject and short Indonesian body, a send time between 08.00 and 16.00 WIB on the invoice date,
and a deterministic Message-ID. Nothing here mentions the scenario or the expected answer.
"""

from faktur_beres.dataset import Dataset
from faktur_beres.generator.catalog import Scenario
from faktur_beres.generator.determinism import rng, short_hash
from faktur_beres.generator.formatting import long_date, rupiah_plain
from faktur_beres.generator.model import EmailMeta, InvoiceDoc

AP_ADDRESS = "ap@arunika.test"

_BODIES = (
    "Yth. Bagian Keuangan {pembeli},\n\n"
    "Bersama email ini kami kirimkan faktur nomor {nomor} tanggal {tanggal} senilai {total}. "
    "{lampiran}\n\n"
    "Atas perhatian dan kerja samanya kami ucapkan terima kasih.\n\n"
    "Hormat kami,\nBagian Penagihan\n{vendor}",
    "Selamat pagi,\n\n"
    "Terlampir faktur {nomor} tertanggal {tanggal} dengan total {total}. {lampiran}\n\n"
    "Terima kasih.\n\n"
    "Salam,\nBagian Penagihan {vendor}",
    "Dengan hormat,\n\n"
    "Kami sampaikan faktur nomor {nomor} ({tanggal}) sebesar {total} untuk {pembeli}. "
    "{lampiran}\n\n"
    "Hormat kami,\nBagian Penagihan\n{vendor}",
)


def email_for(s: Scenario, doc: InvoiceDoc, ds: Dataset) -> EmailMeta | None:
    if s.channel != "email":
        return None
    seller = doc.seller
    r = rng(s.id, "email")
    hour, minute, second = r.randint(8, 15), r.randint(0, 59), r.randint(0, 59)
    sent = f"{doc.issue_date.isoformat()}T{hour:02d}:{minute:02d}:{second:02d}+07:00"
    domain = (seller.email or "vendor.test").split("@", 1)[1]
    stamp = f"{doc.issue_date:%Y%m%d}{hour:02d}{minute:02d}{second:02d}"
    template = _BODIES[int(short_hash(s.vendor, "template-email"), 16) % len(_BODIES)]
    attachment = (
        "Berkas e-invoice (UBL) terlampir." if s.format == "ubl" else "Berkas faktur terlampir."
    )
    body = template.format(
        pembeli=ds.company.name,
        nomor=doc.number,
        tanggal=long_date(doc.issue_date),
        total=rupiah_plain(doc.total),
        lampiran=attachment,
        vendor=seller.name,
    )
    return EmailMeta(
        sender=f"Bagian Penagihan {seller.name} <{seller.email}>",
        to=AP_ADDRESS,
        subject=f"Faktur {doc.number} – {seller.name}",
        body=body,
        sent_at=sent,
        message_id=f"<{stamp}.{short_hash(s.id, 'message-id', length=12)}@{domain}>",
    )
