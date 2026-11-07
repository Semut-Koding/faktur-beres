{
    "name": "Faktur Beres Agent",
    "version": "19.0.1.0.0",
    "summary": "Grup dan hak akses user bot Faktur Beres (baca ERP, draft tagihan vendor)",
    "description": """
Grup khusus user bot Faktur Beres (RENCANA-TEKNIS §4.4, ADR-0004).

* Baca: perusahaan, vendor, rekening, produk, PO, penerimaan, pajak, jurnal, termin, tagihan.
* Buat dan ubah hanya draft tagihan vendor (in_invoice) buatan user itu sendiri, beserta barisnya.
* Lampiran dan catatan chatter pada draft itu.
* Tanpa hapus, tanpa posting, tanpa mengubah status tagihan, PO, penerimaan, atau data master.

Logika agent tidak ada di sini; agent berjalan di Python dan memakai External JSON-2 API.
""",
    "category": "Hidden",
    "author": "Tim Daftar H-2 Jam",
    "license": "LGPL-3",
    "depends": ["account", "purchase", "stock", "mail"],
    "data": [
        "security/faktur_beres_agent_security.xml",
        "security/ir.model.access.csv",
    ],
    "installable": True,
    "application": False,
}
