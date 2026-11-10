# Faktur Beres

AI agent yang menuntaskan *exception* faktur pemasok di proses *accounts payable*. Agent mengekstrak faktur, mencocokkannya dengan purchase order dan penerimaan barang di Odoo, lalu memilih satu dari tiga jalur: membuat draft tagihan (A), meminta klarifikasi ke vendor (B), atau meminta persetujuan approver (C). Semua perhitungan dijalankan kode deterministik, dan setiap aksi tercatat di jejak audit.

Karya Tim **Daftar H-2 Jam** (Politeknik Negeri Subang) untuk **GTNIC 2026, kategori AI Agent**.

**Status:** M4a selesai (10 November 2026): stack lokal dan data Odoo (M0), 40 skenario uji berlabel (M1), lapisan alat agent dan aturan deterministik (M2), ekstraksi (M3), serta graph LangGraph dengan basis data aplikasi, perencana dan pemetaan baris LLM di dalam pagar, dan aksi jalur A, B, dan C dengan keputusan approver lewat CLI (M4a). Evaluasi graph mode LLM segar: 40/40 lulus saat pertama berhenti, 27/27 status akhir, dan set ketahanan 13/13; 13 skenario lain menunggu balasan vendor di M4b. Berikutnya M4b (email masuk, balasan vendor, RAG, dan API). Rencana lengkap ada di [RENCANA-TEKNIS §6](docs/RENCANA-TEKNIS.md#6-milestone).

## Prasyarat
- Linux dengan **Podman 6 rootless** dan **podman-compose ≥ 1.6** sebagai penyedia `podman compose`. Docker Compose v2 juga bisa dipakai.
- **uv** (Python 3.13 dipasang otomatis oleh uv).
- **Node.js 24 LTS** dan npm.
- git.

## Menjalankan secara lokal

```bash
npm run setup          # cek versi alat, buat .env dengan rahasia acak, pasang hook git
npm run services:up    # Odoo 19, PostgreSQL Odoo, PostgreSQL + pgvector, Mailpit (proyek compose fakturberes)
npm run odoo:init      # database fakturberes, modul, addon grup agent, perusahaan, user agent, API key
npm run odoo:seed      # data sintetis dari data/seed/dataset.yaml (aman diulang)
npm run odoo:check     # pastikan isi Odoo sama dengan dataset
npm run odoo:snapshot  # simpan kondisi seed untuk reset cepat
npm run db:migrate     # tabel aplikasi (Alembic) dan tabel checkpointer LangGraph
```

| Layanan | Alamat lokal |
|---|---|
| Odoo | http://localhost:28069 (login `admin`, sandi di `.env`) |
| Mailpit (email keluar dan masuk) | http://localhost:28025 |
| API Faktur Beres (mulai M4b) | http://localhost:28000 |
| Dashboard (mulai M5) | http://localhost:25173 |

Port sengaja dibuat berbeda dari proyek lain di mesin yang sama (aturan +20000, [RENCANA-TEKNIS §4.3](docs/RENCANA-TEKNIS.md#43-kontainer-lokal-dan-port)).

Kembalikan data Odoo ke kondisi seed kapan saja dengan `npm run odoo:reset`. Kosongkan data aplikasi dengan `npm run db:reset`.

Proses satu faktur lewat CLI (hanya berkas di dalam repo):

```bash
npm run agent:proses -- eval/berkas/COC-03/faktur.xml
npm run agent:status
npm run agent:jejak -- FB-000001
npm run agent:putuskan -- FB-000001 setujui --oleh "Nama Approver"
npm run agent:ulang -- FB-000001   # lanjutkan faktur berstatus failed
```

> **Peringatan:** jangan menjalankan `podman compose down -v`, `podman volume rm`, atau `podman system prune`. Perintah itu bisa menghapus data proyek lain di mesin yang sama. Panduan lengkap: [docs/deploy/lokal-podman.md](docs/deploy/lokal-podman.md).

## Tes dan pemeriksaan

```bash
npm run check          # lint, cek tipe (mypy), dan tes unit Python
npm run guard:secrets  # pastikan tidak ada nilai rahasia di berkas yang akan di-commit
npm run gen:periksa    # pastikan 40 skenario dan set ketahanan di eval/ sama isinya dengan keluaran generator
npm run eval:orakel    # aturan deterministik pada 40 label (tambahkan -- --odoo untuk ERP dari Odoo)
npm run test:integrasi # tes terhadap Odoo dengan key agent, dengan odoo:reset sebelum dan sesudahnya
npm run eval:injeksi -- --lapis1  # deteksi injeksi lapis 1 pada set frasa dev (tanpa LLM)
```

Perintah berikut memanggil Gemini, jadi memakai kuota tier gratis. Isi `GEMINI_API_KEY` di `.env` lebih dulu. Yang dikirim hanya data sintetis dari repo ([ADR-0008](docs/adr/0008-gemini-sdk-model-kuota-cache-data.md)).

```bash
npm run llm:model -- --uji   # periksa model terkonfigurasi (satu permintaan kecil per model)
npm run test:llm             # tes smoke Gemini
npm run eval:ekstraksi       # akurasi ekstraksi dan rantai ke aturan, tiga kelompok (sekitar 120 panggilan)
npm run eval:injeksi         # injeksi dua lapis pada set frasa dev (sekitar 100 panggilan)
npm run eval:perencana       # perencana identify pada 8 kasus (paling banyak 48 panggilan)
npm run eval:graph           # graph sungguhan, 40 skenario dan set ketahanan (sekitar 150 panggilan)
```

## Demo publik
Dashboard publik, video demo, dan cara mencoba agent ditambahkan di minggu 3 (M6).

## Struktur

```text
faktur-beres/
├─ src/faktur_beres/   # paket Python: klien Odoo, dataset, seed, generator, alat agent, graph, API
├─ tests/              # tes Python
├─ data/seed/          # dataset.yaml: sumber tunggal data sintetis
├─ data/kebijakan/     # parameter.yaml: ambang dan toleransi kebijakan (SPEC §15.5)
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
