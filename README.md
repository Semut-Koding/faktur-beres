# Faktur Beres

AI agent yang menuntaskan *exception* faktur pemasok di proses *accounts payable*. Agent mengekstrak faktur, mencocokkannya dengan purchase order dan penerimaan barang di Odoo, lalu memilih satu dari tiga jalur: membuat draft tagihan (A), meminta klarifikasi ke vendor (B), atau meminta persetujuan approver (C). Semua perhitungan dijalankan kode deterministik, dan setiap aksi tercatat di jejak audit.

Karya Tim **Daftar H-2 Jam** (Politeknik Negeri Subang) untuk **GTNIC 2026, kategori AI Agent**.

**Status:** M1 selesai (6 November 2026): stack lokal dan data Odoo (M0), serta 40 skenario uji berlabel dengan berkas faktur dan balasan vendor di [`eval/`](eval/README.md). Berikutnya M2 (lapisan alat agent). Rencana lengkap ada di [RENCANA-TEKNIS §6](docs/RENCANA-TEKNIS.md#6-milestone).

## Prasyarat
- Linux dengan **Podman 6 rootless** dan **podman-compose ≥ 1.6** sebagai penyedia `podman compose`. Docker Compose v2 juga bisa dipakai.
- **uv** (Python 3.13 dipasang otomatis oleh uv).
- **Node.js 24 LTS** dan npm.
- git.

## Menjalankan secara lokal

```bash
npm run setup          # cek versi alat, buat .env dengan rahasia acak, pasang hook git
npm run services:up    # Odoo 19, PostgreSQL Odoo, PostgreSQL + pgvector, Mailpit (proyek compose fakturberes)
npm run odoo:init      # database fakturberes, modul, perusahaan, user agent, dan API key
npm run odoo:seed      # data sintetis dari data/seed/dataset.yaml (aman diulang)
npm run odoo:check     # pastikan isi Odoo sama dengan dataset
npm run odoo:snapshot  # simpan kondisi seed untuk reset cepat
```

| Layanan | Alamat lokal |
|---|---|
| Odoo | http://localhost:28069 (login `admin`, sandi di `.env`) |
| Mailpit (email keluar dan masuk) | http://localhost:28025 |
| API Faktur Beres (mulai M4) | http://localhost:28000 |
| Dashboard (mulai M5) | http://localhost:25173 |

Port sengaja dibuat berbeda dari proyek lain di mesin yang sama (aturan +20000, [RENCANA-TEKNIS §4.3](docs/RENCANA-TEKNIS.md#43-kontainer-lokal-dan-port)).

Kembalikan data Odoo ke kondisi seed kapan saja dengan `npm run odoo:reset`.

> **Peringatan:** jangan menjalankan `podman compose down -v`, `podman volume rm`, atau `podman system prune`. Perintah itu bisa menghapus data proyek lain di mesin yang sama. Panduan lengkap: [docs/deploy/lokal-podman.md](docs/deploy/lokal-podman.md).

## Tes dan pemeriksaan

```bash
npm run check          # lint dan tes Python
npm run guard:secrets  # pastikan tidak ada nilai rahasia di berkas yang akan di-commit
npm run gen:periksa    # pastikan berkas uji di eval/ sama persis dengan keluaran generator
```

## Demo publik
Dashboard publik, video demo, dan cara mencoba agent ditambahkan di minggu 3 (M6).

## Struktur

```text
faktur-beres/
├─ src/faktur_beres/   # paket Python: klien Odoo, dataset, seed, generator, alat agent, graph, API
├─ tests/              # tes Python
├─ data/seed/          # dataset.yaml: sumber tunggal data sintetis
├─ eval/               # katalog skenario, berkas faktur uji, hasil evaluasi
├─ web/                # dashboard React + Vite (M5)
├─ infra/              # konfigurasi Odoo dan skrip Podman
├─ docs/               # SPEC, RENCANA-TEKNIS, ADR, lomba, deploy, tugas
├─ compose.yaml        # layanan lokal
├─ pyproject.toml      # proyek Python (uv)
└─ package.json        # penjalan perintah (npm run …) dan workspace dashboard
```

## Dokumentasi
Indeks ada di [docs/README.md](docs/README.md). Aturan untuk agen koding ada di [CLAUDE.md](CLAUDE.md).

## Aturan repo
- Semua data **sintetis**. Jangan menambah data perusahaan, faktur, atau orang yang nyata.
- Commit kecil, satu butir per commit, dengan pesan berbahasa Indonesia.
- Keputusan arsitektur dicatat di [docs/adr/](docs/adr/README.md).
