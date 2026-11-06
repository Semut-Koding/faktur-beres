# Stack lokal dengan Podman rootless

Panduan menjalankan layanan Faktur Beres di laptop pengembang yang juga menjalankan DombaTek. Keputusan di baliknya ada di [ADR-0003](../adr/0003-podman-proyek-terpisah.md).

## Prasyarat
- Podman 6 rootless dan podman-compose ≥ 1.6 sebagai penyedia `podman compose` (cek: `podman --version`, `podman compose version`, `podman info --format '{{.Host.Security.Rootless}}'` harus `true`).
- uv, Node.js 24 LTS dengan npm, dan git.
- Port 28069, 25432, 21025, 28025, 28000, dan 25173 kosong di `127.0.0.1`.
- Ruang disk ±3 GB untuk image dan volume.

## Pertama kali

```bash
npm run setup          # cek alat, buat .env berisi rahasia acak, pasang hook pre-commit
npm run services:up    # tarik image (pertama kali), nyalakan odoo, odoo-db, app-db, mailpit, lalu tunggu sehat
npm run odoo:init      # database fakturberes, modul, bahasa id_ID, perusahaan, pajak, user agent, API key ke .env
npm run odoo:seed      # data sintetis dari data/seed/dataset.yaml
npm run odoo:check     # bandingkan isi Odoo dengan dataset; semua baris harus OK (INFO hanya laporan)
npm run odoo:snapshot  # simpan kondisi seed ke database fakturberes_seed
```

Pada laptop pemilik (5 November 2026) urutan ini memakan waktu sekitar 2 menit setelah image tersedia: `odoo:init` dari database kosong ±1 menit, `odoo:seed` ±35 detik, `odoo:check` ±1 detik, dan `odoo:snapshot` ±12 detik. Menjalankan `odoo:init` atau `odoo:seed` lagi tidak mengubah apa pun; setiap langkah dilaporkan "sudah ada".

Buka Odoo di http://localhost:28069 (login `admin`, sandi `ODOO_ADMIN_PASSWORD` di `.env`) dan Mailpit di http://localhost:28025.

## Sehari-hari

| Kebutuhan | Perintah |
|---|---|
| Nyalakan / matikan layanan | `npm run services:up` / `npm run services:down` |
| Status dan kesehatan layanan | `npm run services:status` |
| Log satu layanan (opsi `podman logs` boleh ditambahkan) | `npm run services:logs -- odoo`, `npm run services:logs -- --tail 100 odoo` |
| Kembalikan data Odoo ke kondisi seed | `npm run odoo:reset` |
| Isi ulang setelah dataset berubah | `npm run odoo:reset`, lalu `npm run odoo:seed`, `npm run odoo:check`, `npm run odoo:snapshot` |

`services:down` menghentikan dan menghapus kontainer, pod `pod_fakturberes`, dan jaringan `fakturberes_default`. Data tetap ada di volume bernama.

Contoh keluaran `npm run services:status`:

```text
LAYANAN   KONTAINER                    STATUS     KESEHATAN  PORT
odoo-db   fakturberes_odoo-db_1        running    healthy    -
app-db    fakturberes_app-db_1         running    healthy    127.0.0.1:25432->5432/tcp
mailpit   fakturberes_mailpit_1        running    healthy    127.0.0.1:21025->1025/tcp 127.0.0.1:28025->8025/tcp
odoo      fakturberes_odoo_1           running    healthy    127.0.0.1:28069->8069/tcp
```

## Berdampingan dengan DombaTek

| | DombaTek | Faktur Beres |
|---|---|---|
| Proyek compose | `dombatek` | `fakturberes` (percobaan: `fakturberes-<nama>`) |
| Port host | 3000, 3900–3903, 5432, 8081 | 28069, 25432, 21025, 28025 (kontainer); 28000, 25173 (host) |
| Prefiks volume | `dombatek_` | `fakturberes_` |
| Pod dan jaringan | `pod_dombatek`, `dombatek_default` | `pod_fakturberes`, `fakturberes_default` |

- Kedua stack boleh menyala bersamaan. Faktur Beres memakai sekitar 1 GB memori saat menyala.
- Jangan pernah menghentikan, menghapus, atau membuat ulang apa pun milik `dombatek`.
- Pemeriksaan cepat sebelum bekerja:

```bash
podman ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
ss -ltn | grep -E ':(28069|25432|21025|28025|28000|25173)\b' || echo "port Faktur Beres kosong"
```

## Reset dan snapshot
- `odoo:snapshot` menjalankan `odoo:check` lebih dulu dan berhenti bila ada yang gagal. Bila lulus, layanan `odoo` dihentikan sebentar dan `odoo db duplicate --force fakturberes fakturberes_seed` menyalin database beserta filestore-nya. Snapshot lama diganti.
- `odoo:reset` menghentikan layanan `odoo`, menjalankan `odoo db duplicate --force fakturberes_seed fakturberes` (database dan filestore lama dihapus lalu diganti salinan), menyalakan Odoo lagi, memulihkan `web.base.url` ke `http://localhost:28069`, dan memastikan kedua API key di `.env` masih berlaku. Waktu terukur 10–17 detik; dipakai harness evaluasi sebelum setiap percobaan.
- Volume tidak pernah dihapus. Bila snapshot belum ada, `odoo:reset` berhenti dengan pesan yang jelas.
- Cron Odoo dimatikan (`max_cron_threads = 0` di `infra/odoo/odoo.conf`) karena thread cron memproses setiap database milik user `odoo`, termasuk snapshot. Akibatnya antrean email Odoo juga tidak diproses.

## Rahasia dan API key
- `.env` dibuat `npm run setup` dari `.env.example`. Nilai yang sudah ada tidak ditimpa.
- `npm run odoo:init` menulis `ODOO_ADMIN_API_KEY` dan `ODOO_AGENT_API_KEY` ke `.env`. Key Odoo berlaku paling lama 3 bulan; jalankan `odoo:init` lagi untuk membuat key baru bila key lama kedaluwarsa (gejalanya galat 401 `Invalid apikey`).
- `GEMINI_API_KEY` diisi sendiri oleh pemilik (mulai M3).
- `.env` dibuat dengan izin `600`. Hook pre-commit dari `npm run setup` menjalankan `npm run guard:secrets` sebelum setiap commit.
- podman-compose meneruskan variabel dari `.env` sebagai argumen `-e` ke perintah `podman`. Selama perintah berjalan, nilainya terbaca di daftar proses (`ps`) oleh semua user lokal, kecuali `/proc` dipasang dengan `hidepid`. Ini diterima untuk laptop satu pengguna karena yang lewat compose hanya sandi PostgreSQL lokal. API key Odoo dan Gemini tidak pernah masuk env kontainer (diperiksa `npm run odoo:check`), dan server publik di M6 memakai `podman secret`. Jangan merekam layar saat menjalankan `ps`, `podman compose config`, atau podman-compose dengan `--verbose`.

## Lokasi repo
Repo berada di disk lokal (btrfs) dan tidak tersinkron ke layanan cloud; nama folder `Drive` hanya nama folder (dikonfirmasi 6 November 2026). Database berada di volume Podman, bukan di folder repo. Jangan memindahkan repo ke mount jaringan atau FUSE.

## Pemecahan masalah

| Gejala | Penyebab dan tindakan |
|---|---|
| `address already in use` saat `services:up` | Port dipakai proses lain. Cari dengan `ss -ltnp \| grep <port>`. Jangan mematikan proses DombaTek; laporkan ke pemilik. |
| `short-name resolution` saat menarik image | Nama image harus lengkap (`docker.io/...`) di `compose.yaml`. |
| Odoo menampilkan "database not found" | `odoo:init` belum dijalankan, atau `dbfilter` tidak cocok. |
| Galat 401 `Invalid apikey` | API key kedaluwarsa atau `.env` belum terbaca; jalankan `npm run odoo:init`. |
| Skrip `.venv/bin/...` gagal karena path berspasi | Jalankan lewat `uv run python -m <modul>`. |
| Odoo lambat setelah reset | Aset web dibuat ulang saat akses pertama; tunggu beberapa detik. |
| `odoo:init`: "database fakturberes ada tetapi negara perusahaan bukan ID" | Database dibuat di luar `odoo:init`, sehingga chart of accounts Indonesia tidak akan terpasang. Hapus hanya database itu (`podman compose -p fakturberes stop odoo`, lalu `podman compose -p fakturberes run --rm --no-deps -T odoo odoo db drop fakturberes`, lalu `podman compose -p fakturberes start odoo`), kemudian jalankan `odoo:init` lagi. |
| `odoo:reset`: "API key di .env tidak berlaku pada data hasil reset" | Key dibuat ulang setelah snapshot diambil. Jalankan `npm run odoo:init`, lalu `npm run odoo:snapshot`. |
| `odoo:snapshot` menolak karena `odoo:check` gagal | Jalankan `npm run odoo:check` untuk melihat baris yang gagal; bila perlu `npm run odoo:reset` atau bangun ulang database. |

## Yang tidak boleh dilakukan
`podman system reset`, `podman system prune`, `podman volume prune`, `podman volume rm`, `podman container prune`, `podman image prune`, `podman compose down -v`, opsi `--root` atau `--runroot`, serta mengubah konfigurasi Podman global, sysctl, atau unit systemd. Daftar lengkap di [CLAUDE.md](../../CLAUDE.md).
