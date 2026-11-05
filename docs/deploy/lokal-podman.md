# Stack lokal dengan Podman rootless

Panduan menjalankan layanan Faktur Beres di laptop pengembang yang juga menjalankan DombaTek. Keputusan di baliknya ada di [ADR-0003](../adr/0003-podman-proyek-terpisah.md).

> Status: rencana M0. Perintah dan keluaran di bawah diperbarui setelah M0 diterapkan dan diuji.

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
npm run odoo:check     # bandingkan isi Odoo dengan dataset; semua baris harus OK
npm run odoo:snapshot  # simpan kondisi seed ke database fakturberes_seed
```

Buka Odoo di http://localhost:28069 (login `admin`, sandi `ODOO_ADMIN_PASSWORD` di `.env`) dan Mailpit di http://localhost:28025.

## Sehari-hari

| Kebutuhan | Perintah |
|---|---|
| Nyalakan / matikan layanan | `npm run services:up` / `npm run services:down` |
| Status dan kesehatan layanan | `npm run services:status` |
| Log satu layanan | `npm run services:logs -- odoo` |
| Kembalikan data Odoo ke kondisi seed | `npm run odoo:reset` |
| Isi ulang setelah dataset berubah | `npm run odoo:reset`, lalu `npm run odoo:seed`, `npm run odoo:check`, `npm run odoo:snapshot` |

`services:down` hanya menghentikan kontainer. Data tetap ada di volume bernama.

## Berdampingan dengan DombaTek

| | DombaTek | Faktur Beres |
|---|---|---|
| Proyek compose | `dombatek` | `fakturberes` (percobaan: `fakturberes-<nama>`) |
| Port host | 3000, 3900–3903, 5432, 8081 | 28069, 25432, 21025, 28025 (kontainer); 28000, 25173 (host) |
| Prefiks volume | `dombatek_` | `fakturberes_` |

- Kedua stack boleh menyala bersamaan. Faktur Beres memakai sekitar 1 GB memori saat menyala.
- Jangan pernah menghentikan, menghapus, atau membuat ulang apa pun milik `dombatek`.
- Pemeriksaan cepat sebelum bekerja:

```bash
podman ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
ss -ltn | grep -E ':(28069|25432|21025|28025|28000|25173)\b' || echo "port Faktur Beres kosong"
```

## Reset dan snapshot
- `odoo:snapshot` menyalin database `fakturberes` beserta filestore-nya menjadi `fakturberes_seed`. Jalankan setelah seed dan check lulus.
- `odoo:reset` menghentikan server Odoo, mengganti `fakturberes` dengan salinan `fakturberes_seed`, lalu menyalakan server lagi. Targetnya kurang dari 1 menit, dan dipakai harness evaluasi sebelum setiap percobaan.
- Volume tidak pernah dihapus. Bila snapshot belum ada, `odoo:reset` berhenti dengan pesan yang jelas.

## Rahasia dan API key
- `.env` dibuat `npm run setup` dari `.env.example`. Nilai yang sudah ada tidak ditimpa.
- `npm run odoo:init` menulis `ODOO_ADMIN_API_KEY` dan `ODOO_AGENT_API_KEY` ke `.env`. Key Odoo berlaku paling lama 3 bulan; jalankan `odoo:init` lagi untuk membuat key baru bila key lama kedaluwarsa (gejalanya galat 401 `Invalid apikey`).
- `GEMINI_API_KEY` diisi sendiri oleh pemilik (mulai M3).

## Bila folder repo tersinkron ke cloud
Bila folder `Drive` disinkronkan ke layanan cloud, kecualikan `.venv/`, `node_modules/`, `var/`, dan `eval/hasil/` dari sinkronisasi agar tidak membebani sinkronisasi dan tidak menimbulkan konflik. Database berada di volume Podman, bukan di folder repo. Jangan menaruh repo di mount jaringan atau FUSE.

## Pemecahan masalah

| Gejala | Penyebab dan tindakan |
|---|---|
| `address already in use` saat `services:up` | Port dipakai proses lain. Cari dengan `ss -ltnp \| grep <port>`. Jangan mematikan proses DombaTek; laporkan ke pemilik. |
| `short-name resolution` saat menarik image | Nama image harus lengkap (`docker.io/...`) di `compose.yaml`. |
| Odoo menampilkan "database not found" | `odoo:init` belum dijalankan, atau `dbfilter` tidak cocok. |
| Galat 401 `Invalid apikey` | API key kedaluwarsa atau `.env` belum terbaca; jalankan `npm run odoo:init`. |
| Skrip `.venv/bin/...` gagal karena path berspasi | Jalankan lewat `uv run python -m <modul>`. |
| Odoo lambat setelah reset | Aset web dibuat ulang saat akses pertama; tunggu beberapa detik. |

## Yang tidak boleh dilakukan
`podman system reset`, `podman system prune`, `podman volume prune`, `podman volume rm`, `podman container prune`, `podman image prune`, `podman compose down -v`, opsi `--root` atau `--runroot`, serta mengubah konfigurasi Podman global, sysctl, atau unit systemd. Daftar lengkap di [CLAUDE.md](../../CLAUDE.md).
