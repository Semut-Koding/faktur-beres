# Faktur Beres

AI agent yang menuntaskan *exception* faktur pemasok di proses *accounts payable*. Agent mengekstrak faktur, mencocokkannya dengan purchase order dan penerimaan barang di Odoo, lalu memilih satu dari tiga jalur: membuat draft tagihan (A), meminta klarifikasi ke vendor (B), atau meminta persetujuan approver (C). Semua perhitungan dijalankan kode deterministik, dan setiap aksi tercatat di jejak audit.

Karya Tim **Daftar H-2 Jam** (Politeknik Negeri Subang) untuk **GTNIC 2026, kategori AI Agent**.

**Status:** M4b selesai, ditambah API, dashboard, dan deploy publik yang dikerjakan pada 10 November 2026 dengan lingkup darurat (lihat [Coba demo](#coba-demo)). Yang sudah ada: stack lokal dan data Odoo (M0), 40 skenario uji berlabel (M1), lapisan alat agent dan aturan deterministik (M2), ekstraksi (M3), graph LangGraph dengan aksi jalur A, B, dan C (M4a), email masuk, balasan vendor, RAG kebijakan, dan pilihan jalur oleh LLM di dalam pagar (M4b), API FastAPI dengan SSE (M4c.1), dashboard React (M5.1), dan deploy VPS (M6.1). Evaluasi graph mode LLM segar sampai status akhir: 40/40 skenario dan set ketahanan 13/13. Yang ditunda ke setelah lomba ada di [RENCANA-TEKNIS §6](docs/RENCANA-TEKNIS.md#6-milestone).

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
| API dan dashboard hasil build (`npm run api`) | http://127.0.0.1:28000 |
| Dashboard mode pengembangan (`npm run dashboard:dev`) | http://127.0.0.1:25173 |

Port sengaja dibuat berbeda dari proyek lain di mesin yang sama (aturan +20000, [RENCANA-TEKNIS §4.3](docs/RENCANA-TEKNIS.md#43-kontainer-lokal-dan-port)).

Kembalikan data Odoo ke kondisi seed kapan saja dengan `npm run odoo:reset`. Kosongkan data aplikasi dengan `npm run db:reset`; indeks kebijakan tetap ada. Setelah `db:reset`, pesan lama di Mailpit akan terbaca lagi oleh `agent:inbox`, jadi saat menyiapkan demo kosongkan Mailpit bersama `db:reset`.

Jalankan API dan dashboard di laptop:

```bash
npm run dashboard:install   # sekali: pasang dependensi dashboard
npm run dashboard:build     # hasil build disajikan API dari /
npm run api                 # API dan dashboard di http://127.0.0.1:28000
npm run demo:balas -- FB-000003 eval/berkas/QTY-01/balasan.pdf   # alat presenter: balasan vendor tiruan lewat Mailpit
```

Kode akses peran (`DEMO_KODE_AP`, `DEMO_KODE_APPROVER`) dibuat acak oleh `npm run setup` di `.env`. Pemantau inbox di proses API menyala bila `INBOX_INTERVAL_DETIK` diisi; bila kosong, balasan vendor dibaca dengan `npm run agent:inbox`.

Proses satu faktur lewat CLI (hanya berkas di dalam repo):

```bash
npm run agent:proses -- eval/berkas/COC-03/faktur.xml
npm run agent:inbox            # baca inbox AP sekali; -- --terus untuk berulang
npm run agent:status
npm run agent:jejak -- FB-000001
npm run agent:putuskan -- FB-000001 setujui --oleh "Nama Approver"
npm run agent:ulang -- FB-000001   # lanjutkan faktur berstatus failed
```

> **Peringatan:** jangan menjalankan `podman compose down -v`, `podman volume rm`, atau `podman system prune`. Perintah itu bisa menghapus data proyek lain di mesin yang sama. Panduan lengkap: [docs/deploy/lokal-podman.md](docs/deploy/lokal-podman.md).

## Tes dan pemeriksaan

```bash
npm run check          # lint, cek tipe (mypy), tes unit Python, lalu tsc dan build dashboard
npm run guard:secrets  # pastikan tidak ada nilai rahasia di berkas yang akan di-commit
npm run gen:periksa    # pastikan 40 skenario dan set ketahanan di eval/ sama isinya dengan keluaran generator
npm run eval:orakel    # aturan deterministik pada 40 label (tambahkan -- --odoo untuk ERP dari Odoo)
npm run test:integrasi # tes terhadap Odoo dengan key agent, dengan odoo:reset sebelum dan sesudahnya
npm run eval:injeksi -- --lapis1  # deteksi injeksi lapis 1 pada set frasa dev (tanpa LLM)
```

Perintah berikut memanggil Gemini, jadi memakai kuota tier gratis. Isi `GEMINI_API_KEY` di `.env` lebih dulu. Yang dikirim hanya data sintetis dari repo ([ADR-0008](docs/adr/0008-gemini-sdk-model-kuota-cache-data.md)).

```bash
npm run llm:model -- --uji   # periksa model terkonfigurasi (satu permintaan kecil per model)
npm run llm:kuota            # permintaan per model sejak reset harian terakhir (15.00 WIB)
npm run rag:indeks           # buat indeks kebijakan (hanya potongan baru atau berubah)
npm run rag:cari -- "ongkos kirim"   # cari di kebijakan
npm run test:llm             # tes smoke Gemini
npm run eval:ekstraksi       # akurasi ekstraksi dan rantai ke aturan, tiga kelompok (sekitar 120 panggilan)
npm run eval:injeksi         # injeksi dua lapis pada set frasa dev (sekitar 100 panggilan)
npm run eval:perencana       # perencana identify pada 8 kasus (paling banyak 48 panggilan)
npm run eval:graph           # graph sungguhan, 40 skenario dan set ketahanan (sekitar 150 panggilan)
```

## Coba demo

**Tautan live:** <https://fakturberes.farhanginting.my.id>

Semua data di demo sintetis: vendor, NPWP, rekening, PO, dan faktur dibuat generator di repo ini. Jangan mengunggah dokumen nyata, karena isi berkas dikirim ke LLM.

Tanpa kode akses, siapa pun bisa membaca:

- **Antrean**: faktur yang masuk, jalur (A, B, atau C), status, dan alasannya, diperbarui langsung.
- **Detail faktur**: berkas asli berdampingan dengan hasil ekstraksi, pencocokan tiga arah per baris, temuan, aksi yang dijalankan (draft tagihan Odoo, email beserta Message-ID), dan **jejak penalaran** per node dan alat.
- **Metrik**: jumlah per jalur dan status, *touchless rate*, median waktu sampai jalur ditentukan, biaya LLM setara, dan hasil evaluasi terakhir.

NPWP ditampilkan 4 digit awal dan 4 digit akhir, rekening 4 digit akhir (BR-24).

Aksi memerlukan kode akses, yang tidak ditulis di repo dan dicantumkan di proposal:

| Peran | Yang bisa dilakukan |
|---|---|
| Staf AP | Mengunggah faktur (PDF, JPG, PNG, atau XML UBL, paling besar 15 MB, 10 unggahan per jam per IP) dan menjalankan ulang faktur yang gagal |
| Approver | Memutuskan faktur jalur C: **Setujui**, **Tolak**, atau **Minta klarifikasi vendor**, sesuai pilihan di paket bukti |

Cara mencoba:

1. Buka Antrean, lalu pilih faktur berstatus "Menunggu persetujuan". Paket bukti memuat alasan, kutipan kebijakan, dan pilihan yang tersedia.
2. Masukkan kode approver, lalu putuskan. Status dan jejak berubah tanpa memuat ulang halaman.
3. Untuk faktur baru, buka Unggah, pilih faktur sintetis dari `eval/berkas/` di repo ini, dan masukkan kode staf AP.

Layanan pendukung, keduanya di balik basic auth (kredensial di proposal):

- Odoo: <https://odoo.fakturberes.farhanginting.my.id> (draft tagihan yang dibuat agent);
- Mailpit: <https://mail.fakturberes.farhanginting.my.id> (email klarifikasi ke vendor; kotak surat sandbox, tidak ada email yang keluar).

### Hasil evaluasi (M4b, 10 November 2026)

| Mode | Pertama berhenti | Status akhir | Set ketahanan |
|---|---|---|---|
| Orakel | 40/40 | 40/40 | 13/13 |
| LLM segar | 40/40 | 40/40 | 13/13 |

Pada mode LLM segar, median waktu sampai jalur ditentukan 11,5 detik (p95 36,1 detik, n = 53), dan biaya setara USD 0,0034 per faktur. Rincian dan batas tafsirnya ada di [eval/README.md](eval/README.md).

### Batasan demo

- Satu run evaluasi per mode; harness 40 skenario × 3 dan set eskalasi belum dijalankan.
- Pengingat ke vendor (BR-19), rekap harian (BR-26), dan `vendor_memory` belum ada.
- Demo berjalan di VPS kecil (RAM 2 GB) dengan kuota LLM tier gratis, jadi faktur diproses satu atau dua sekaligus dan bisa gagal saat kuota habis. Faktur yang gagal bisa dijalankan ulang oleh staf AP.
- Kode akses adalah pembatas demo, bukan sistem login: tidak ada akun per orang.

Cara deploy dan reset demo ada di [infra/deploy/README.md](infra/deploy/README.md).

## Struktur

```text
faktur-beres/
├─ src/faktur_beres/   # paket Python: klien Odoo, dataset, seed, generator, alat agent, graph, API
├─ tests/              # tes Python
├─ data/seed/          # dataset.yaml: sumber tunggal data sintetis
├─ data/kebijakan/     # parameter.yaml: ambang dan toleransi kebijakan (SPEC §15.5)
├─ eval/               # katalog skenario, berkas faktur uji, hasil evaluasi
├─ dashboard/          # dashboard React + Vite + TypeScript
├─ infra/              # konfigurasi Odoo, skrip Podman, dan deploy VPS (infra/deploy)
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
