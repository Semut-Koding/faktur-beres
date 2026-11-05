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

## Catatan implementasi (M0, 5 November 2026)
- podman-compose 1.6.0 membuat kontainer `fakturberes_<layanan>_1`, pod `pod_fakturberes` tanpa berbagi namespace (`--infra=false --share=`), jaringan `fakturberes_default`, dan volume `fakturberes_odoo-data`, `fakturberes_odoo-db-data`, `fakturberes_app-db-data`, `fakturberes_mailpit-data`. Port 28069, 25432, 21025, dan 28025 hanya di `127.0.0.1`; `odoo-db` tidak dibuka ke host.
- Image Odoo mendeklarasikan `VOLUME /mnt/extra-addons`, dan Podman membuat volume anonim untuk VOLUME yang tidak di-mount. Folder kosong `infra/odoo/addons` di-bind ke sana (`:ro,Z`) agar tidak ada volume tanpa prefiks `fakturberes`.
- Sandi PostgreSQL Odoo diteruskan lewat env `PGPASSWORD`. Entrypoint image resmi tidak menambahkan `--db_password` bila `PGPASSWORD` ada, dan host, port, serta user dibaca dari `infra/odoo/odoo.conf`, sehingga sandi tidak muncul di argumen proses Odoo. Subperintah `odoo db`, `odoo module`, dan `odoo shell` memakai `PGPASSWORD` lewat libpq, jadi konfigurasi lokal di `var/` tidak dibutuhkan.
- podman-compose sendiri meneruskan nilai `environment` sebagai `-e NAME=nilai` ke `podman create`/`exec`/`run`; nilainya terlihat di daftar proses milik user yang sama selama perintah berjalan. Skrip tidak pernah memakai `--verbose` atau mencetak `podman compose config`.
- Skrip di `infra/scripts/` selalu memakai `podman compose -p fakturberes`. Perintah `podman inspect` dan `podman healthcheck run` hanya dijalankan pada kontainer yang namanya berawalan `fakturberes_`.
- Reset tanpa menghapus volume: `npm run odoo:reset` menghentikan hanya layanan `odoo`, menjalankan `odoo db duplicate --force fakturberes_seed fakturberes` di kontainer sekali jalan, lalu menyalakan Odoo lagi. Waktu terukur 10–17 detik.
- Cron Odoo dimatikan (`max_cron_threads = 0`) karena thread cron memproses semua database milik user `odoo`, termasuk snapshot. Membatasi dengan `db_name` tidak dipakai karena server lalu membuat database kosong otomatis saat mulai.
- Daftar kontainer non-`fakturberes` (ID, nama, waktu dibuat, status, port) diperiksa sebelum dan sesudah M0 dan sama persis.

## Konsekuensi
- Kedua proyek bisa berjalan bersamaan tanpa bentrok port atau nama.
- Aturan +20000 mudah diingat dan menjauhi port yang biasa dipakai alat lain (5433, 15432, 18000, 18080).
- Reset tanpa menghapus volume butuh skrip snapshot dan reset di level Odoo (M0).
- Deploy publik (M6) mungkin butuh susunan berbeda, misalnya Quadlet atau layanan terkelola; keputusannya ditulis sebagai ADR baru.

## Alternatif
- **Docker Engine:** tidak dipakai di laptop pemilik.
- **Port bawaan** (8069, 5432, 1025, 8025): 5432 bentrok dengan DombaTek, dan sisanya rawan bentrok dengan alat lain.
- **Pod Podman tanpa compose:** tidak bisa dipakai pengguna Docker dan lebih sulit didokumentasikan.
