# ADR-0004: Odoo 19 Community lewat External JSON-2 API dengan user bot dan dua API key

- Status: Diterima
- Tanggal: 2026-11-05
- Terkait: [RENCANA-TEKNIS §4.4](../RENCANA-TEKNIS.md#44-odoo), [SPEC §5.2, §9.1](../SPEC.md#52-alat), proposal §5.5

## Konteks
- Agent butuh ERP sungguhan untuk vendor, PO, penerimaan barang, dan tagihan vendor. Proposal menetapkan **Odoo 19 Community**.
- Odoo 19 memperkenalkan **External JSON-2 API**: `POST /json/2/<model>/<method>`, API key sebagai `Authorization: bearer <key>`, header `X-Odoo-Database` bila server punya beberapa database, dan semua argumen dikirim dengan nama. Setiap panggilan adalah satu transaksi SQL; beberapa panggilan tidak bisa digabung dalam satu transaksi.
- Endpoint XML-RPC dan JSON-RPC (`/xmlrpc`, `/xmlrpc/2`, `/jsonrpc`) usang sejak Odoo 19. Halaman External RPC API dokumentasi 19.0 menyebut penghapusan di Odoo 22 (musim gugur 2028) dan Odoo Online 21.1 (musim dingin 2027). Halaman JSON-2 versi yang sama masih menyebut Odoo 20 (musim gugur 2026). Keduanya sepakat bahwa XML-RPC tidak boleh dipakai untuk kode baru.
- Dokumentasi Odoo menyarankan **user bot khusus** untuk integrasi: hak minimum, tanpa sandi, dan jejak akses atas nama bot.
- API key Odoo berlaku paling lama tiga bulan dan hanya ditampilkan sekali saat dibuat.
- Per 5 November 2026, tag `latest` image Odoo di Docker Hub sudah menunjuk Odoo 20.

## Keputusan
- **Odoo 19 Community** dari image resmi dengan **tag bertanggal** (`19.0-YYYYMMDD`), plus PostgreSQL 17 tersendiri. Odoo 20 tidak dipakai di lomba ini.
- Integrasi **hanya lewat JSON-2**. Semua panggilan melewati satu modul klien (`faktur_beres.odoo`) yang mengatur header, batas waktu, retry, dan pemetaan error.
- **Dua identitas:**
  - **admin** dengan API key admin, hanya untuk skrip pengembang (init, seed, check, reset);
  - **user bot `agen@arunika.test`** tanpa sandi, dengan API key agent, untuk semua kode agent. Kode agent tidak boleh memakai key admin.
- Hak user agent dibatasi sejauh yang memungkinkan Odoo. Di M0 memakai grup standar minimal, dan di M2 diketatkan dengan grup khusus tanpa hak hapus dan posting. Alat agent juga hanya mengizinkan operasi tertentu (SPEC §5.2).
- Nama field dan metode diverifikasi lewat `fields_get` dan dicatat di RENCANA-TEKNIS §4.4.
- API key disimpan di `.env` dan dibuat ulang `npm run odoo:init` sebelum kedaluwarsa.

## Catatan implementasi (M0, 5 November 2026)
- JSON-2 disediakan modul `rpc` (terpasang otomatis bersama `base`); modul yang sama masih membuka `/xmlrpc` dan `/jsonrpc`, tetapi keduanya tidak dipakai. Autentikasi bearer memeriksa key dengan scope `rpc`.
- Klien `faktur_beres.odoo.OdooClient` mengirim `Authorization: bearer`, `X-Odoo-Database`, `Content-Type: application/json`, dan `User-Agent`. Retry dengan backoff hanya untuk metode baca (galat koneksi dan 5xx); `create`, `write`, dan metode aksi tidak di-retry. Galat dipetakan dari status Odoo 19: 401 key tidak valid, 403 `AccessError`/`AccessDenied`, 404 model atau metode tidak ada dan `MissingError`, 409 `LockError`, 422 `UserError`/`ValidationError`, 500 lainnya.
- Panggilan JSON-2 tanpa `context` berjalan dengan konteks kosong (tanpa `lang` dan `tz`), sehingga skrip mengirim konteks bila hasilnya bergantung pada bahasa atau zona waktu. Kode agent di M2–M4 perlu melakukan hal yang sama.
- User bot `agen@arunika.test` dibuat tanpa sandi dengan grup `purchase.group_purchase_user`, `stock.group_stock_user`, `account.group_account_invoice`, dan `mail.group_mail_notification_type_inbox`. Hak yang terbawa menurut `has_access`: `account.move` baca, buat, ubah, hapus; `purchase.order` dan `stock.picking` ubah dan hapus; `res.partner.bank` baca saja. `account.group_account_invoice` juga mengizinkan posting tagihan (`account.move._post`). Hak hapus dan posting ini diketatkan di M2 dengan grup khusus.
- API key admin dan agent dibuat `npm run odoo:init` lewat `odoo shell` dengan `res.users.apikeys._generate("rpc", nama, sekarang + 90 hari)` dalam `sudo()`, lalu ditulis langsung ke `.env` tanpa tampil di terminal. Key baru hanya dibuat bila key di `.env` kosong atau tidak valid lagi; key lama buatan alat yang sama dihapus. Key saat ini berlaku sampai 3 Februari 2027.
- Bootstrap yang belum bisa memakai key admin (database, modul, data perusahaan, pajak, sandi admin, user agent, API key) memakai CLI Odoo (`odoo db`, `odoo module`, `odoo shell`) di kontainer. Seed, check, dan langkah pasca-reset memakai JSON-2 dengan key admin, dan key agent hanya dipakai untuk uji asap dan pemeriksaan hak.
- Nama field Odoo 19 yang dipakai tercatat di [RENCANA-TEKNIS §4.4](../RENCANA-TEKNIS.md#44-odoo).

## Konsekuensi
- Integrasi memakai API yang didukung jangka panjang dan bisa dijelaskan di proposal sebagai integrasi API resmi.
- Karena tidak ada transaksi lintas panggilan, operasi bertahap (membuat draft lalu melampirkan berkas) harus aman diulang. Ini ditangani kunci idempoten ([ADR-0006](0006-persetujuan-dan-idempotensi.md)).
- Lapisan alat bisa diganti untuk ERP lain tanpa mengubah graph.
- Rotasi API key perlu diingat bila proyek berjalan lebih dari tiga bulan.

## Alternatif
- **XML-RPC atau JSON-RPC:** banyak contoh, tetapi usang dan dijadwalkan dihapus.
- **Akses langsung ke database Odoo:** melewati aturan akses dan logika bisnis Odoo, dan berisiko merusak data.
- **Modul Odoo kustom berisi logika agent:** mengikat agent ke Odoo dan mempersulit pengujian.
- **Odoo 20:** lebih baru, tetapi proposal sudah menyebut 19, dan risiko perubahan menjelang tenggat lebih tinggi.
