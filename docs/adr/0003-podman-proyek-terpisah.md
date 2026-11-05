# ADR-0003: Podman rootless, proyek compose `fakturberes`, dan port +20000, berdampingan dengan DombaTek

- Status: Diterima
- Tanggal: 2026-11-05
- Terkait: [RENCANA-TEKNIS §4.3](../RENCANA-TEKNIS.md#43-kontainer-lokal-dan-port), [deploy/lokal-podman.md](../deploy/lokal-podman.md), [CLAUDE.md](../../CLAUDE.md)

## Konteks
- Pemilik memakai **Podman 6 rootless** di Arch Linux dengan podman-compose sebagai penyedia `podman compose`, dan tidak memakai Docker.
- Laptop yang sama menjalankan stack DombaTek (proyek compose `dombatek`: PostgreSQL di 5432, Garage di 3900, web di 3000, gerbang perangkat di 8081), **sering bersamaan** dengan pekerjaan ini.
- Podman rootless milik satu pengguna berbagi penyimpanan, sehingga perintah pembersihan global bisa menghapus data proyek lain.
- File compose sebaiknya tetap bisa dipakai pengguna Docker (misalnya juri yang ingin menjalankan repo).

## Keputusan
- Semua layanan lokal berada di proyek compose **`fakturberes`** (`name:` di `compose.yaml` dan `-p fakturberes` di setiap skrip). Percobaan memakai `fakturberes-<nama>`.
- Layanan: `odoo`, `odoo-db`, `app-db`, dan `mailpit`. API dan dashboard berjalan di host.
- **Port = port standar + 20000**, hanya di `127.0.0.1`: Odoo 28069, PostgreSQL aplikasi 25432, Mailpit SMTP 21025 dan UI 28025, API 28000, Vite 25173. PostgreSQL milik Odoo tidak dibuka ke host.
- Image ditulis lengkap (`docker.io/...`) dengan tag yang dikunci. Odoo memakai tag bertanggal.
- Hanya fitur compose yang didukung podman-compose dan Docker Compose v2. Bind mount diberi `:Z`; volume bernama tanpa label.
- Perintah yang bisa menyentuh proyek lain dilarang (daftar di CLAUDE.md). Data Odoo dikembalikan lewat `npm run odoo:reset`, bukan dengan menghapus volume.

## Konsekuensi
- Kedua proyek bisa berjalan bersamaan tanpa bentrok port atau nama.
- Aturan +20000 mudah diingat dan menjauhi port yang biasa dipakai alat lain (5433, 15432, 18000, 18080).
- Reset tanpa menghapus volume butuh skrip snapshot dan reset di level Odoo (M0).
- Deploy publik (M6) mungkin butuh susunan berbeda, misalnya Quadlet atau layanan terkelola; keputusannya ditulis sebagai ADR baru.

## Alternatif
- **Docker Engine:** tidak dipakai di laptop pemilik.
- **Port bawaan** (8069, 5432, 1025, 8025): 5432 bentrok dengan DombaTek, dan sisanya rawan bentrok dengan alat lain.
- **Pod Podman tanpa compose:** tidak bisa dipakai pengguna Docker dan lebih sulit didokumentasikan.
