# infra/odoo/addons

Folder ini di-bind ke `/mnt/extra-addons` di kontainer Odoo (read-only) dan tercantum di
`addons_path` (`infra/odoo/odoo.conf`). Isinya hanya addon kecil yang mengatur hak akses, bukan
logika agent ([ADR-0004](../../../docs/adr/0004-odoo-19-json2-api.md)):

| Addon | Isi |
|---|---|
| [`faktur_beres_agent`](faktur_beres_agent) | Grup `faktur_beres_agent.group_agent` untuk user bot `agen@arunika.test`: hak baca data ERP, buat dan ubah draft tagihan vendor buatan sendiri, lampiran, dan catatan; tanpa hapus dan posting. Lisensi LGPL-3. |

Addon dipasang atau di-upgrade oleh `npm run odoo:init`. Logika agent tetap di Python
(`faktur_beres.tools`) dan berbicara dengan Odoo hanya lewat External JSON-2 API.
