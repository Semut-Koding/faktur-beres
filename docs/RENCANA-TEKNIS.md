# Faktur Beres: Rencana Teknis

| | |
|---|---|
| Status | **Disetujui 5 November 2026.** Ringkasan keputusan di §8, alasan lengkap di [ADR](adr/README.md). |
| Tanggal | 5 November 2026 |
| Acuan | [`SPEC.md`](SPEC.md) · [`lomba/gtnic-2026.md`](lomba/gtnic-2026.md) |

---

## 1. Kebutuhan teknis yang menentukan pilihan

1. **70% nilai lomba ada di perilaku agent:** otonomi, penalaran, penggunaan alat, dan arsitektur ([lomba](lomba/gtnic-2026.md#kriteria-penilaian)). Butuh graph eksplisit dengan *tool calling*, interrupt, checkpointer, dan jejak audit yang bisa ditampilkan.
2. **Angka keuangan harus benar.** Semua hitungan deterministik di Python dengan rupiah bulat atau `Decimal`, tanpa `float`.
3. **Alur bisa berhenti berhari-hari lalu dilanjutkan** tanpa mengulang aksi. Butuh state persisten dan kunci idempoten.
4. **ERP sungguhan.** Odoo 19 Community berjalan lokal dan diakses lewat API resminya.
5. **UI wajib di-deploy publik** dan repo wajib publik saat pengumpulan.
6. **Tiga minggu, satu pengembang dibantu AI.** Satu bahasa backend, sedikit layanan, dan perintah yang konsisten.
7. **Mesin dipakai bersama DombaTek** yang sering berjalan bersamaan. Nama proyek, port, dan volume harus terpisah.
8. **Data sintetis dan LLM tier gratis.**

---

## 2. Stack

| Lapisan | Pilihan | Alasan |
|---|---|---|
| Bahasa backend | **Python 3.13** | Ekosistem LangGraph, Gemini, PDF, dan gambar paling matang. Satu bahasa untuk agent, alat, seed, generator, dan evaluasi. |
| Pengelola paket Python | **uv** (`pyproject.toml` + `uv.lock`, `.python-version`) | Cepat, lockfile deterministik, dan memasang versi Python sendiri ([ADR-0002](adr/0002-monorepo-uv-npm.md)). |
| Orkestrasi agent | **LangGraph** + checkpointer PostgreSQL | Graph eksplisit, interrupt untuk persetujuan, dan resume dari titik terakhir ([ADR-0001](adr/0001-stack-inti.md)). |
| LLM | **Gemini** keluarga Flash dan Flash-Lite lewat SDK `google-genai` | Multimodal, keluaran terstruktur, dan punya tier gratis. Model persis dikunci di konfigurasi saat M3. |
| Skema dan validasi | **Pydantic v2** | Skema ekstraksi, parameter alat, dan konfigurasi. |
| API | **FastAPI** + SSE (Uvicorn) | REST dan status langsung untuk dashboard. |
| ERP | **Odoo 19 Community**, image resmi, dengan **PostgreSQL 17** sendiri | PO, penerimaan, dan tagihan vendor yang nyata ([ADR-0004](adr/0004-odoo-19-json2-api.md)). |
| Integrasi ERP | **External JSON-2 API** dengan **httpx** | API resmi Odoo 19. XML-RPC dan JSON-RPC sudah usang. |
| Basis data aplikasi | **PostgreSQL 18 + pgvector 0.8.7** | Checkpoint, audit, dan vektor RAG dalam satu basis data. |
| Akses DB dan migrasi | **SQLAlchemy 2** + **psycopg 3** + **Alembic** (mulai M2–M4) | Migrasi berversi. psycopg 3 juga dipakai checkpointer LangGraph. |
| Email | **Mailpit** (SMTP sandbox dan API) | Email klarifikasi nyata yang bisa dilihat juri tanpa terkirim ke luar. Inbox AP juga dibaca dari sini. |
| Faktur terstruktur | Parser **UBL 2.1** (EN 16931) dengan lxml | Jalur e-invoice. |
| Generator faktur (M1) | **ReportLab** (PDF), **Pillow** + **pypdfium2** (gambar dan efek foto), lxml (UBL) | Semua dipasang lewat pip, tanpa pustaka sistem tambahan. |
| Dashboard | **React + Vite + TypeScript** | Dashboard publik yang ringan. Deploy di M6. |
| Kualitas | **Ruff** (lint dan format), **pytest**, **mypy** (mulai M2) | |
| Kontainer | **Podman rootless** + `compose.yaml` | Tanpa daemon root, berdampingan dengan DombaTek ([ADR-0003](adr/0003-podman-proyek-terpisah.md)). |
| Penjalan perintah | **npm scripts** di `package.json` akar | Perintah sama dengan kebiasaan di DombaTek (`npm run setup`, `services:up`, dll.). |

> Versi yang diperiksa 5 November 2026: image Odoo `19.0-20261102` (tag `latest` di Docker Hub sudah Odoo 20, jadi tidak dipakai), pgvector `0.8.7-pg18-trixie`, Mailpit `v1.32.0`. Versi minor PostgreSQL 17 dan versi alat di laptop (Podman, podman-compose, uv, Node.js) dikunci dan dicatat di M0. Pustaka Python dan npm dikunci di lockfile.
>
> **Dikunci di M0 (5 November 2026):** PostgreSQL untuk Odoo `17.11` (Debian trixie, `PG_VERSION=17.11-1.pgdg13+2`; tag `17` saat itu menunjuk image yang sama), PostgreSQL aplikasi 18.6 dari image pgvector. Alat di laptop pemilik: Podman 6.1.4, podman-compose 1.6.0, uv 0.13.1, Python 3.13.16 (dipasang uv), Node.js 24.22.0, npm 11.19.1, git 2.56.0. Pustaka Python yang dipakai M0 (lihat `uv.lock`): httpx 0.28.1, Pydantic 2.14.3, pydantic-settings 2.16.2, PyYAML 6.0.3; dev: pytest 9.2.0, Ruff 0.17.3, python-stdnum 2.2.
>
> **Generator faktur (M1, 6 November 2026):** lxml 6.1.3 (dependensi utama, juga untuk parser UBL M3); dev: ReportLab 5.1.0, Pillow 13.0.0, pypdfium2 5.14.0 (PDFium 156.0.8076.0). Keluaran `gen:faktur` identik byte per byte untuk versi di `uv.lock` pada platform yang sama (diperiksa `gen:periksa`).
>
> **Graph dan basis data (M4a, 10 November 2026):** LangGraph 1.2.20, langgraph-checkpoint 4.3.1, langgraph-checkpoint-postgres 3.2.0, SQLAlchemy 2.1.8, psycopg 3.3.8, psycopg-pool 3.3.3, Alembic 1.20.1. LangGraph 1.0.10 ke atas wajib karena memuat perbaikan deserialisasi checkpoint (CVE-2026-28277).
>
> **Lapisan alat (M2, 7 November 2026):** python-stdnum 2.2 pindah ke dependensi utama (dipakai `tax_check` saat runtime); dev: mypy 2.4.1 dengan plugin Pydantic dan types-PyYAML 6.0.12.20261103. `npm run typecheck` menjalankan mypy `--strict` untuk paket `domain`, `tools`, dan `evals`. Modul M0–M1 (`config`, `textui`, `odoo`, `dataset`, `seed`, `generator`, `devtools`) dikecualikan lewat `[[tool.mypy.overrides]]` dengan `ignore_errors` di `pyproject.toml`: tetap dibaca mypy saat diimpor paket baru, tetapi galatnya sendiri tidak dilaporkan.

---

## 3. Arsitektur

### 3.1 Gambaran

```text
 Vendor ──email──▶ Mailpit (SMTP + inbox) ◀──API── pemantau inbox ─┐
 Staf AP / Approver ──▶ Dashboard React ──REST/SSE──▶ FastAPI ──────┤
                                                                    ▼
                                                  LangGraph runner (satu thread per faktur)
                                                  │  checkpointer, audit, aksi
                                                  ▼
                                    PostgreSQL 18 + pgvector (basis data aplikasi)
        alat:  extract_invoice ─▶ Gemini API
               odoo_tools ──────▶ Odoo 19 (JSON-2) ─▶ PostgreSQL 17 (Odoo)
               match_engine, tax_check (kode murni)
               policy_rag ──────▶ pgvector
               email_tool ──────▶ Mailpit SMTP
```

### 3.2 Modul Python

| Modul | Isi | Milestone |
|---|---|---|
| `faktur_beres.config` | Pengaturan dari `.env` (pydantic-settings) | M0 |
| `faktur_beres.odoo` | Klien JSON-2: header, error, retry; satu-satunya tempat kode bicara dengan Odoo | M0 |
| `faktur_beres.dataset` | Model Pydantic dan pemuat `data/seed/dataset.yaml`, beserta pemeriksaan konsistensinya | M0 |
| `faktur_beres.seed` | `init`, `seed`, `check`; dipakai lewat npm scripts | M0 |
| `faktur_beres.generator` | Generator faktur PDF/gambar/UBL, label, dan balasan vendor (M1); set uji ketahanan `KTH-xx` (M3) | M1, M3 |
| `faktur_beres.domain` | Model faktur kanonis `InvoiceData`, kode alasan dan jalur, record ERP dan protokol `ErpReader`, helper normalisasi, `PolicyConfig`, aturan deterministik (`rules/`, satu modul per kelompok BR) dan mesin `analyse()`. Kode agent: tidak mengimpor `evals`, `generator`, `dataset`, atau `seed` (tes arsitektur) | M2 |
| `faktur_beres.llm` | Akses Gemini untuk agent: protokol `StructuredLlm`, `GeminiClient` (keluaran terstruktur, retry, model cadangan, throttle), cache, catatan audit per panggilan dan biaya setara, prompt berversi, `llm:model`. Kode agent dengan aturan yang sama seperti `domain` dan `tools` | M3 |
| `faktur_beres.extraction` | Jenis berkas dari magic bytes dan batasnya, lapisan teks PDF, parser UBL SPEC §15.6, skema ekstraksi `ExtractedInvoice`, konversi ke `InvoiceData`, validasi dan pembacaan ulang BR-04, `ExtractionOutcome`. Kode agent | M3 |
| `faktur_beres.tools` | Alat agent: `AgentOdooGateway` (satu-satunya pintu kode agent ke Odoo, key agent, daftar operasi dan field yang diizinkan), `odoo_tools` dan `OdooErpReader`, `match_engine`, `tax_check` (M2); `extract_invoice` dan `injection_review` (M3); `email_tool`, `po_planner`, `line_mapping` (M4a); `policy_rag` (M4b) | M2–M4b |
| `faktur_beres.db` | Model SQLAlchemy, migrasi Alembic, `Store`, `db:migrate`, `db:reset`. Kode agent | M4a |
| `faktur_beres.graph` | Node, state, routing, runner, dan CLI LangGraph. Kode agent | M4a |
| `faktur_beres.api` | FastAPI, SSE, dan pemantau inbox | M4b |
| `faktur_beres.evals` | Kode evaluasi, satu-satunya yang membaca label dan dataset: konverter label ke `InvoiceData`, pembaca ERP dari dataset, evaluasi orakel (M2); `eval:injeksi`, `eval:ekstraksi`, cek silang orakel set ketahanan, dan penjaga path data yang boleh dikirim ke LLM (M3); harness 40 × 3 dan penilai state akhir (M5) | M2–M5 |

### 3.3 Penanganan kesalahan dan skala
- Panggilan Odoo dan LLM memakai retry dengan backoff eksponensial dan batas waktu. Kesalahan JSON-2 dipetakan dari objek error Odoo (`name`, `message`) ke exception Python yang jelas.
- Setiap panggilan JSON-2 adalah satu transaksi. Operasi yang harus atomik memakai satu metode Odoo (mis. `create` dengan baris sekaligus), dan kegagalan di tengah ditangani lewat kunci idempoten dan pemeriksaan ulang (SPEC BR-22).
- Bila kuota LLM habis, agent beralih ke model cadangan. Klien Gemini (M3) mematikan retry bawaan SDK dan mencatat setiap percobaan sendiri: 429 per menit menunggu `RetryInfo` atau backoff, paling banyak 3 kali; 429 per hari menandai model habis untuk sisa proses; 5xx dan timeout di-backoff paling banyak 3 kali; keluaran tidak valid diulang sekali di model yang sama; 400, 401, 403, dan 404 tidak diulang. Bila kedua model tidak tersedia: `LlmUnavailable` (BR-27). Jarak minimum antarpermintaan per model 60/RPM detik, dan panggilan berurutan.
- Cache LLM (`faktur_beres.llm.cache`) dipakai hanya bila pemanggil memintanya. Kuncinya SHA-256 atas model, tujuan, versi prompt, skema, instruksi sistem, setiap masukan (sebagai hash), dan konfigurasi generasi; hanya jawaban yang valid yang disimpan, dan entri rusak ditulis ulang. Evaluasi berjalan tanpa cache, kecuali `--pakai-cache` untuk pengembangan, dan hasil dari cache ditandai.
- MVP memakai satu proses API yang menjalankan graph di latar. Karena state ada di PostgreSQL, worker bisa dipisah dan ditambah tanpa mengubah graph.

### 3.4 Keterlacakan aturan
Setiap aturan yang dihitung kode punya tes bertanda `@pytest.mark.aturan("BR-xx")`. Daftar aturannya adalah konstanta `faktur_beres.domain.rules.CODE_RULES`, dan `tests/test_traceability.py` memastikan tiap BR di daftar itu punya minimal satu tes. Jumlah di bawah dihitung saat M4a selesai (10 November 2026): fungsi tes, dan kasus setelah parametrisasi; tes basis data bertanda `integrasi` ikut terhitung. Seluruhnya ada 1.357 kasus tes unit, ditambah 28 tes integrasi dan 2 tes smoke LLM.

| BR | Fungsi atau modul | Berkas tes | Tes (kasus) |
|---|---|---|---|
| BR-01 | `graph/runner.py`: `accept` (jenis berkas sebelum thread dibuat); node `intake` (hash sama); `graph/deps.py`: `FileStore` | `test_graph_flow.py` | 3 (3) |
| BR-02 | `extraction/convert.py`: `convert` (field wajib); `extraction/ubl.py` | `test_extraction_convert.py` | 3 (3) |
| BR-03 | `rules/arithmetic.py`: `check_arithmetic` | `test_rule_arithmetic.py` | 6 (10) |
| BR-04 | `extraction/reading.py`: `read_with_llm` (pembacaan ulang, konsistensi); `extraction/outcome.py` | `test_extraction_reading.py` | 11 (11) |
| BR-05 | `rules/vendor.py`: `identify_vendor`; `engine.analyse` melewati pemeriksaan bergantung vendor | `test_rule_vendor.py`, `test_engine.py` | 11 (12) |
| BR-06 | `rules/purchase_order.py`: `identify_purchase_order`, `is_strong_candidate` | `test_rule_purchase_order.py`, `test_engine.py` | 12 (16) |
| BR-07 | `rules/lines.py`: `map_line`, pemetaan tervalidasi di `match_lines`; `tools/line_mapping.py`: guard pemetaan LLM | `test_rule_lines.py`, `test_tool_line_mapping.py`, `test_graph_flow.py` | 10 (18) |
| BR-08 | `rules/lines.py`: `check_price`, `price_limit` | `test_rule_lines.py` | 4 (4) |
| BR-09 | `rules/lines.py`: `check_quantity`, `match_lines`; `tools/match_engine.py` | `test_rule_lines.py`, `test_engine.py`, `test_match_and_tax_tools.py` | 10 (10) |
| BR-10 | `rules/tax.py`: `check_tax`, `check_seller_identity`; `tax_ids.py`; `tools/tax_check.py` | `test_rule_tax.py`, `test_match_and_tax_tools.py` | 14 (22) |
| BR-11 | `rules/duplicates.py`: `find_duplicates`; registri tanpa faktur yang sedang diproses (`engine.analyse`) | `test_rule_duplicates.py`, `test_engine.py` | 11 (11) |
| BR-12 | `rules/bank.py`: `check_bank_account` (nama bank dibandingkan dengan bank terdaftar); `normalize.bank_name_key` | `test_rule_bank.py` | 9 (14) |
| BR-13 | `rules/policy_checks.py`: `check_threshold` | `test_rule_policy_checks.py` | 1 (4) |
| BR-14 | `rules/policy_checks.py`: `check_new_vendor` | `test_rule_policy_checks.py` | 3 (3) |
| BR-15 | `rules/injection.py`: `PATTERNS` kuat dan lemah, `judge_injection`, `document_fragments`; `tools/injection_review.py` (lapis kedua) | `test_rule_injection.py`, `test_rule_injection_layers.py`, `test_tool_injection_review.py` | 36 (97) |
| BR-16 | `rules/routing.py`: `priority_route`, `initial_status` | `test_rule_routing.py` | 2 (6) |
| BR-17 | `rules/routing.py`: `allowed_routes`, `default_route`, `guard_route` | `test_rule_routing.py` | 4 (15) |
| BR-18 | `rules/bill_plan.py`: `plan_bill`, `BillPlan`; `tools/odoo_tools.py`: `create_draft_bill`, `verify_bill`; node `act_bill` | `test_rule_bill_plan.py`, `test_graph_flow.py` (+ `test_odoo_tools_draft.py`, `test_integrasi_draft.py`) | 7 (7) |
| BR-19 | `tools/email_tool.py`: templat, subjek berreferensi; node `act_vendor_email` (bagian keluar; pengingat di M4b) | `test_tool_email.py`, `test_graph_flow.py` | 5 (5) |
| BR-21 | node `prepare_approval`: paket bukti dan pilihan approver | `test_graph_flow.py` | 2 (2) |
| BR-22 | `rules/safety.py`: `canonical_json`, `idempotency_key`; tabel `actions` (`db/store.py`: `begin_action`); Message-ID deterministik (`tools/email_tool.py`) | `test_rule_safety.py`, `test_db_store.py`, `test_graph_flow.py`, `test_tool_email.py` | 9 (9) |
| BR-23 | `llm/gemini.py`: catatan per percobaan di logger `faktur_beres.llm`; `llm/cost.py`; log gateway tanpa isi berkas; `audit_log` (`db/store.py`, `graph/audit.py`) | `test_llm_cache_audit_cost.py`, `test_odoo_gateway.py`, `test_db_store.py` | 9 (9) |
| BR-24 | `rules/safety.py`: `mask_npwp`, `mask_account`, `mask_params` (dipakai log gateway) | `test_rule_safety.py` (+ `test_odoo_gateway.py`) | 2 (2) |
| BR-25 | `rules/safety.py`: `check_recipient`; `tools/email_tool.py`: `send_checked`; penerima dari Odoo di node `act_vendor_email` | `test_rule_safety.py`, `test_tool_email.py`, `test_graph_flow.py` | 6 (21) |
| BR-27 | `llm/gemini.py`: `GeminiClient` (retry, backoff, model cadangan, `LlmUnavailable`); `graph/runner.py`: status `failed` dan `retry`; `RetryPolicy` node | `test_llm_gemini.py`, `test_graph_flow.py` | 13 (18) |
| BR-28 | `domain/policy.py`: `PolicyConfig`, `load_policy` | `test_policy.py` | 2 (2) |

Rangkaian lengkapnya diuji `tests/test_engine.py`, evaluasi orakel (`tests/test_orakel.py`, 40/40), dan cek silang set ketahanan (`tests/test_robustness_oracle.py`). Kesetaraan graph dengan `analyse()` diuji `tests/test_graph_equivalence.py` untuk 40 skenario dan 13 dokumen ketahanan. Batas antara kode agent dan kode evaluasi diuji `tests/test_architecture.py`, termasuk paket `llm`, `extraction`, `db`, dan `graph`, dan `domain` yang tidak mengimpor `llm`, `extraction`, atau `tools`.

---

## 4. Repositori, alat, dan kontainer

### 4.1 Struktur repositori

```text
faktur-beres/            # nama folder repo (lihat README)
├─ src/faktur_beres/     # paket Python (§3.2)
├─ tests/                # pytest
├─ data/
│  ├─ seed/dataset.yaml  # sumber tunggal data sintetis (SPEC §9.4)
│  ├─ kebijakan/
│  │  └─ parameter.yaml  # konfigurasi kebijakan SPEC §15.5 (M2); SOP AP dan kontrak sintetis untuk RAG menyusul (M4)
│  └─ llm/
│     └─ harga.yaml      # harga Gemini per 1 juta token untuk biaya setara (M3)
├─ eval/
│  ├─ skenario/          # label 40 skenario (M1, hasil generator, di-commit)
│  ├─ berkas/            # faktur uji dan balasan vendor (M1, hasil generator, di-commit)
│  ├─ DAFTAR.md          # tabel ringkas 40 skenario (hasil generator)
│  ├─ ketahanan/         # set uji ketahanan KTH-xx: skenario/, berkas/, DAFTAR.md (M3, hasil generator, di-commit)
│  ├─ injeksi/dev.yaml   # set frasa injeksi untuk pengembangan BR-15 (M3, ditulis tangan)
│  ├─ README.md          # penjelasan folder (ditulis tangan)
│  └─ hasil/             # hasil evaluasi orakel (M2), injeksi dan ekstraksi (M3), graph dan perencana (M4a), dan penuh (M5), tidak di-commit
├─ web/                  # dashboard React + Vite (M5), workspace npm
├─ infra/
│  ├─ odoo/odoo.conf     # konfigurasi Odoo lokal (tanpa rahasia)
│  ├─ odoo/addons/       # addon faktur_beres_agent: grup dan hak user agent (M2), tanpa logika agent
│  ├─ odoo/shell/        # skrip ORM untuk `odoo shell` (bootstrap odoo:init)
│  └─ scripts/           # skrip shell untuk Podman: init, snapshot, reset, tunggu layanan, tes integrasi
├─ var/                  # berkas runtime lokal: var/berkas/<invoice_id>/, var/evaluasi-berkas/, cache LLM var/cache/llm/ (tidak di-commit)
├─ docs/
├─ compose.yaml
├─ pyproject.toml, uv.lock, .python-version
├─ package.json, package-lock.json
├─ .env.example          # contoh variabel; .env dibuat `npm run setup`
├─ CLAUDE.md
└─ README.md
```

### 4.2 Perintah
Semua dijalankan dari akar repo.

| Tujuan | Perintah | Mulai |
|---|---|---|
| Siapkan mesin: cek versi alat, buat `.env` dengan rahasia acak (aman diulang, nilai yang sudah ada tidak diubah), pasang hook pre-commit `guard:secrets` | `npm run setup` | M0 |
| Nyalakan, matikan, atau lihat status layanan | `npm run services:up` / `npm run services:down` / `npm run services:status` | M0 |
| Log layanan | `npm run services:logs -- odoo` | M0 |
| Inisialisasi Odoo: database, modul, bahasa, perusahaan, pajak, user agent, API key (aman diulang) | `npm run odoo:init` | M0 |
| Isi data sintetis dari dataset (idempoten) | `npm run odoo:seed` | M0 |
| Periksa isi Odoo sama dengan dataset dan kebutuhan skenario | `npm run odoo:check` | M0 |
| Simpan kondisi seed / kembalikan Odoo ke kondisi itu | `npm run odoo:snapshot` / `npm run odoo:reset` | M0 |
| Lint, cek tipe, dan tes unit (tes integrasi tidak ikut) | `npm run check` (gabungan `npm run lint`, `npm run typecheck`, dan `npm test`) | M0, cek tipe sejak M2 |
| Cek tipe mypy `--strict` untuk paket `domain`, `tools`, dan `evals` (M2), `llm` dan `extraction` (M3), serta `db` dan `graph` (M4a) | `npm run typecheck` | M2 |
| Pastikan tidak ada nilai rahasia di berkas yang akan di-commit | `npm run guard:secrets` | M0 |
| Buat faktur uji, label, dan balasan vendor (tulis ulang `eval/skenario/`, `eval/berkas/`, `eval/DAFTAR.md`) | `npm run gen:faktur` | M1 |
| Buat set uji ketahanan (tulis ulang `eval/ketahanan/`) | `npm run gen:ketahanan` | M3 |
| Pastikan 40 skenario dan set ketahanan sama **isinya** dengan keluaran generator: PDF per halaman (ukuran, teks, render 50 DPI), gambar per piksel, label tanpa nilai `sha256` (nilai itu tetap harus sama dengan hash berkas); `-- --byte` membandingkan byte | `npm run gen:periksa` | M1, isi sejak M3 |
| Evaluasi orakel 40 skenario: aturan deterministik pada `kebenaran_dokumen` label, ERP dari dataset; hasil JSON di `eval/hasil/` | `npm run eval:orakel` | M2 |
| Evaluasi orakel dengan ERP dari Odoo hasil seed lewat user agent (baca saja; hasil harus sama dengan mode luring) | `npm run eval:orakel -- --odoo` | M2 |
| Tes integrasi terhadap Odoo dengan key agent (`pytest -m integrasi`), dengan `odoo:reset` sebelum dan sesudahnya | `npm run test:integrasi` | M2 |
| Daftar model Gemini yang mendukung `generateContent` untuk key ini; `-- --uji` mengirim satu permintaan kecil berkeluaran terstruktur ke setiap model terkonfigurasi | `npm run llm:model` | M3 |
| Tes smoke Gemini (`pytest -m llm`, satu panggilan per model; dilewati tanpa `GEMINI_API_KEY`) | `npm run test:llm` | M3 |
| Evaluasi injeksi dua lapis pada set frasa (`--lapis1` tanpa LLM, `--berkas` untuk set lain, `--pakai-cache`) | `npm run eval:injeksi` | M3 |
| Evaluasi ekstraksi tiga kelompok (`faktur`, `balasan`, `ketahanan`): akurasi per field dan rantai ekstraksi → aturan, dengan `--set`, `--id`, `--model`, `--banding`, `--tanpa-penilai`, `--maks-panggilan`, `--lanjut`, `--pakai-cache` | `npm run eval:ekstraksi` | M3 |
| Jalankan API dan dashboard untuk pengembangan | `npm run dev:api` / `npm run dev:web` | M4 / M5 |
| Migrasi basis data aplikasi dan tabel checkpointer | `npm run db:migrate` | M4a |
| Kosongkan data aplikasi dan checkpoint (menolak database yang namanya tidak berawalan `fakturberes`) | `npm run db:reset` | M4a |
| Proses satu berkas, lihat status dan jejak, kirim keputusan approver, coba lagi yang `failed` | `npm run agent:proses -- <berkas>`, `agent:status`, `agent:jejak -- <id>`, `agent:putuskan -- <id> setujui\|tolak\|klarifikasi`, `agent:ulang -- <id>` | M4a |
| Tulis diagram Mermaid graph ke `docs/diagram-graph.md` | `npm run graph:diagram` | M4a |
| Evaluasi graph sungguhan (`--set`, `--id`, `--ekstraksi orakel\|llm`, `--penilai`, `--tanpa-llm`, `--pakai-cache`, `--tanpa-reset`, `--lanjut`, `--maks-panggilan`) | `npm run eval:graph` | M4a |
| Evaluasi perencana `identify` pada 8 kasus | `npm run eval:perencana` | M4a |
| Evaluasi 40 skenario × 3 | `npm run eval` | M5 |
| Tambah dependensi Python | `uv add <paket>` atau `uv add --dev <paket>` | |

npm hanya menjadi penjalan perintah dan pengelola dependensi dashboard. Skrip Python dipanggil lewat `uv run python -m ...`.

### 4.3 Kontainer lokal dan port

| Layanan | Image | Port host → kontainer | Volume | Catatan |
|---|---|---|---|---|
| `odoo` | `docker.io/library/odoo:19.0-20261102` | `127.0.0.1:28069` → 8069 | `odoo-data` (`/var/lib/odoo`) | `infra/odoo/odoo.conf` di-bind `:ro,Z`. `workers = 0`, jadi websocket lewat port yang sama. Folder `infra/odoo/addons` (addon `faktur_beres_agent`, sejak M2) di-bind ke `/mnt/extra-addons` (`:ro,Z`) dan tercantum di `addons_path`; bind ini juga mencegah Podman membuat volume anonim. Sandi DB lewat env `PGPASSWORD`. Healthcheck `curl /web/health`. |
| `odoo-db` | `docker.io/library/postgres:17.11` | tidak dibuka ke host | `odoo-db-data` (`/var/lib/postgresql/data`) | Superuser `odoo`, khusus untuk Odoo. Healthcheck `pg_isready`. |
| `app-db` | `docker.io/pgvector/pgvector:0.8.7-pg18-trixie` | `127.0.0.1:25432` → 5432 | `app-db-data` (`/var/lib/postgresql`) | Database `fakturberes`. Ekstensi `vector` dibuat lewat migrasi (M4). PostgreSQL 18 menyimpan data di `/var/lib/postgresql/18/docker`, jadi volume di-mount ke `/var/lib/postgresql`. Healthcheck `pg_isready`. |
| `mailpit` | `docker.io/axllent/mailpit:v1.32.0` | `127.0.0.1:21025` → 1025 (SMTP), `127.0.0.1:28025` → 8025 (UI dan API) | `mailpit-data` (`/data`) | `MP_DATABASE=/data/mailpit.db`. SMTP lokal tanpa TLS; autentikasi apa pun diterima (`MP_SMTP_AUTH_ACCEPT_ANY`, `MP_SMTP_AUTH_ALLOW_INSECURE`). Image tanpa shell; healthcheck `/mailpit readyz`. |

Digest image yang ditarik di M0 (5 November 2026):

| Image | Digest |
|---|---|
| `docker.io/library/odoo:19.0-20261102` | `sha256:08374de09bab5d80851c5709896aa49917e6c6a893b39025d6669db908e238b0` |
| `docker.io/library/postgres:17.11` | `sha256:996e9dc583ba9f0265a2c75547a9c95fb22c251e394749cab205a0a00e1856a1` |
| `docker.io/pgvector/pgvector:0.8.7-pg18-trixie` | `sha256:d624420861924efe8691ecb2060205a9c6bd949aace5cc754da0c907171ca742` |
| `docker.io/axllent/mailpit:v1.32.0` | `sha256:938ce54b7b946ef3f45a03e119952cf7e67a7969d61f45a3ed055442a77e00f4` |

Proses di host (bukan kontainer): API di `127.0.0.1:28000` (M4) dan Vite di `127.0.0.1:25173` (M5).

Aturan:
- **Proyek compose `fakturberes`**: `name: fakturberes` di `compose.yaml` dan `-p fakturberes` di semua skrip. Percobaan memakai `fakturberes-<nama>`.
- **Port = port standar + 20000**, hanya di `127.0.0.1`. Port DombaTek yang dihindari: 3000, 3900–3903, 5432, 8081.
- Hanya fitur compose yang didukung podman-compose dan Docker Compose v2: `services`, `image`, `command`, `environment`, `ports`, volume bernama, `healthcheck`, dan `depends_on` bentuk dasar. Menunggu kesiapan layanan dilakukan skrip, bukan `condition:`.
- Image ditulis dengan nama lengkap (`docker.io/...`) agar Podman tidak menebak registri.
- Bind mount diberi `:Z` (diabaikan di sistem tanpa SELinux). Volume bernama tanpa label.
- Rahasia berasal dari `.env` di akar repo (dibaca compose untuk substitusi variabel). `.env.example` di-commit, `.env` tidak.
- Perkiraan memori saat menyala: Odoo 0,4–0,8 GB, dua PostgreSQL 0,1–0,2 GB, Mailpit < 50 MB.
- podman-compose 1.6 membungkus layanan dalam pod `pod_fakturberes` tanpa berbagi namespace (`--infra=false --share=`), sehingga `odoo-db` dan `app-db` sama-sama memakai port 5432 di kontainer masing-masing. `services:down` menghapus kontainer, pod, dan jaringan `fakturberes_default`, tetapi tidak volume.

### 4.4 Odoo
- **Database** `fakturberes`, `dbfilter = ^fakturberes$`, `list_db = False` (manajer database di web nonaktif), `workers = 0`.
- **Modul:** `purchase`, `stock`, `account`, ditambah lokalisasi Indonesia (`l10n_id`) bila tersedia. Bahasa `id_ID` dimuat. Data demo tidak dipasang (bawaan Odoo 19 adalah tanpa demo).
- **Perusahaan** dari dataset: Indonesia, IDR, NPWP, dan alamat.
- **Pajak pembelian** "PPN Masukan 12% (DPP Nilai Lain 11/12)", efektif 11% atas harga. Pajak dari lokalisasi dipakai bila hasil hitungnya sama; bila tidak, seed membuat pajak sendiri. Pilihan dicatat di M0.
- **Kontrol tagihan** produk: kuantitas diterima.
- **User:** `admin` (sandi dari `.env`, API key admin untuk skrip pengembang) dan `agen@arunika.test` (tanpa sandi, API key agent). Di M0, user agent memakai grup standar minimal yang cukup untuk membaca PO dan penerimaan serta membuat draft tagihan. Hak yang ikut terbawa (hapus, posting) dicatat dan diketatkan di M2 dengan grup khusus `faktur_beres_agent.group_agent` (catatan implementasi M2 di bawah).
- **API key** berlaku paling lama 3 bulan (batas Odoo) dan disimpan di `.env`. `npm run odoo:init` membuat key baru bila key lama tidak ada atau kedaluwarsa.
- **Seed idempoten** dengan external ID modul `fb_seed` di `ir.model.data`. Nama external ID netral (`fb_seed.po_o11`, `fb_seed.vendor_v01`) dan tidak memuat ID skenario.
- **Sequence PO** dimajukan ke paling sedikit P00100 setelah seed.
- **Snapshot dan reset** memakai database `fakturberes_seed` beserta filestore-nya. Cara yang diutamakan: `odoo db duplicate` dan `odoo db drop` (tersedia sejak Odoo 16) saat server Odoo berhenti. Target reset kurang dari 1 menit.

**Catatan field Odoo 19** (diisi M0 dari `fields_get`, 5 November 2026):

| Kebutuhan | Model.field yang dipakai |
|---|---|
| NPWP vendor | `res.partner.vat` (divalidasi `base_vat` untuk negara ID) |
| Pajak di baris PO | `purchase.order.line.tax_ids` (Odoo 19; `taxes_id` sudah tidak ada) |
| Kuantitas diterima dan ditagih di baris PO | `purchase.order.line.qty_received` (dari `stock.move`, `qty_received_method = stock_moves`) dan `purchase.order.line.qty_invoiced` |
| Tautan baris tagihan ke baris PO | `account.move.line.purchase_line_id` (ada juga `purchase_order_id`, readonly) |
| Kontrol tagihan produk | `product.template.purchase_method = 'receive'` |
| Satuan beli produk | `product.template.uom_id` (Odoo 19 tidak punya `uom_po_id`); di baris PO `purchase.order.line.product_uom_id`, di info pemasok `product.supplierinfo.product_uom_id` |
| Barang yang stoknya dilacak | `product.template.type = 'consu'` dengan `is_storable = True` |
| Pajak pembelian produk | `product.template.supplier_taxes_id` |
| Status penerimaan PO | `purchase.order.receipt_status` (`pending`, `partial`, `full`) |
| Tanggal konfirmasi PO | `purchase.order.date_approve` (readonly di UI, bisa ditulis lewat API) |
| Termin PO | `purchase.order.payment_term_id`; UI mengisinya lewat onchange dari `res.partner.property_supplier_payment_term_id`, panggilan API harus mengisinya sendiri |
| Penerimaan | `stock.picking.date_done`, `backorder_id`; `stock.move.quantity` dan `picked`; validasi `stock.picking.button_validate` |
| Grup user | `res.users.group_ids` (Odoo 19; dulu `groups_id`) dan `all_group_ids` |
| Pemeriksaan hak akses | `has_access(operation)` pada recordset (Odoo 18+); `check_access` privat, `check_access_rights` usang |
| Rekening tersanitasi (M2) | `res.partner.bank.sanitized_acc_number` (tanpa pemisah), dipakai mencari pemilik nomor rekening |
| Checksum lampiran (M2) | `ir.attachment.checksum` = SHA-1 isi yang tersimpan; gambar di atas `base.image_autoresize_max_px` (1920x1920) dikecilkan Odoo kecuali context `image_no_postprocess` |
| Pembuat tagihan (M2) | `account.move.create_uid`, dipakai aturan rekaman grup agent |
| Field e-Faktur di tagihan (M2) | `l10n_id_coretax_*` dan `l10n_id_kode_transaksi` dari `l10n_id_efaktur_coretax` hanya untuk `out_invoice`; tidak ada field NSFP untuk tagihan vendor |

**Catatan implementasi M0 (5 November 2026)**
- `infra/odoo/odoo.conf` memuat `max_cron_threads = 0`. Thread cron Odoo memproses setiap database milik user `odoo`, termasuk `fakturberes_seed`, sehingga snapshot bisa berubah diam-diam. `db_name` tidak dipakai untuk membatasinya karena perintah `odoo server` membuat database kosong otomatis bila `db_name` diisi. Faktur Beres tidak bergantung pada cron Odoo; efek sampingnya, antrean email Odoo tidak diproses.
- JSON-2 ada di modul `rpc` (terpasang otomatis). Panggilan tanpa `context` berjalan dengan konteks kosong (tanpa `lang` dan `tz`), jadi skrip mengirim `{"lang": "id_ID", "tz": "Asia/Jakarta"}` untuk aksi. Galat dipetakan dari status 401, 403, 404, 409, 422, dan 500. Membaca `account.move.needed_terms` lewat JSON-2 menghasilkan galat 500 (nilai tidak bisa diserialisasi), jadi field itu tidak dibaca.
- Database dibuat dengan `odoo db init fakturberes --language id_ID --country ID` lalu `odoo module install` saat layanan `odoo` dihentikan. Karena negara perusahaan sudah ID sebelum `account` dipasang, `l10n_id` memasang chart `id` dan jurnal tagihan `TAGIH`. Terpasang juga modul otomatis, antara lain `l10n_id_efaktur_coretax`, `purchase_stock`, dan `stock_account`.
- Pajak pembelian memakai pajak lokalisasi `account.1_tax_PT4` "12% (Non-Luxury Good)": 11% atas harga (= 12% × DPP nilai lain 11/12), PPN Rp110.000 untuk harga Rp1.000.000, tidak termasuk harga, dan sudah menjadi pajak pembelian bawaan perusahaan. Seed dan check merujuknya lewat external ID `fb_seed.tax_ppn_masukan`. Nama Odoo-nya tidak diubah.
  Pemilik mengonfirmasi pada tinjauan M0 (6 November 2026) bahwa nama pajak lokalisasi tetap seperti itu. Rujukan selalu lewat `fb_seed.tax_ppn_masukan`, dan kode tidak pernah mencocokkan pajak lewat nama, karena nama pajak diterjemahkan sesuai `lang`. `tax_check` (M2) menghitung PPN dari konfigurasi kebijakan, bukan dari tarif pajak di Odoo.
- User agent `agen@arunika.test` tanpa sandi, dengan grup `purchase.group_purchase_user`, `stock.group_stock_user`, `account.group_account_invoice`, dan `mail.group_mail_notification_type_inbox` (cara Odoo 19 menyimpan notifikasi "inbox", agar bot tidak dikirimi email). Hak yang terbawa (dari `has_access`): `account.move` baca, buat, ubah, hapus; `purchase.order` dan `stock.picking` ubah dan hapus; `res.partner.bank` baca saja. Grup `account.group_account_invoice` juga mengizinkan posting tagihan. Semua ini diketatkan di M2.
- API key dibuat lewat `odoo shell` dengan `res.users.apikeys._generate("rpc", nama, sekarang + 90 hari)` dalam `sudo()` (tanpa `sudo`, durasi user agent dibatasi `api_key_duration` grup). Key ditulis langsung ke `.env`. Key baru hanya dibuat bila key di `.env` kosong atau tidak valid; key lama buatan alat ini untuk user yang sama dihapus.
- Seed memakai tanggal dataset pukul 10.00 WIB (03.00 UTC) dan menulis ulang `purchase.order.date_approve`, `stock.picking.date_done`, `stock.move.date`, dan `stock.move.line.date`. Tagihan riwayat diberi `date` (tanggal akuntansi) sama dengan `invoice_date`, karena aturan bawaan Odoo memindahkan tagihan bulan lalu ke akhir bulan. Yang tetap bertanggal saat seed: `create_date`/`write_date` semua record dan tanggal pesan chatter (`mail.message.date`).
- `odoo:snapshot` menjalankan `odoo:check` dulu dan hanya membuat snapshot bila lulus. Snapshot dan reset memakai `odoo db duplicate --force` (database dan filestore target dihapus lalu disalin). Duplikasi membuat ulang beberapa parameter sistem di salinan, termasuk `web.base.url` (kembali ke port 8069), sehingga reset memulihkannya lewat JSON-2. Waktu reset terukur 10–17 detik.

**Catatan implementasi M2 (7 November 2026)**
- **Addon `faktur_beres_agent`** 19.0.1.0.0 (LGPL-3) di `infra/odoo/addons`, tanpa logika agent. `infra/odoo/odoo.conf` kini memuat `addons_path = /mnt/extra-addons` (addon bawaan Odoo tetap dimuat otomatis). `npm run odoo:init` memasangnya, atau meng-upgrade bila sudah terpasang, lewat `odoo module install|upgrade` saat layanan odoo dihentikan.
- **Grup `faktur_beres_agent.group_agent`** mengimplikasikan `base.group_user` dan `account.group_account_readonly`, dengan `api_key_duration` 90 hari. Grup eksplisit user agent persis grup ini dan `mail.group_mail_notification_type_inbox`; dengan yang tersirat ada 7 grup (`account.group_account_readonly`, `base.group_multi_currency`, `base.group_no_one`, `base.group_user`, `faktur_beres_agent.group_agent`, `mail.group_mail_notification_type_inbox`, `purchase.group_send_reminder`). User agent tidak berada di `account.group_account_invoice` (atau grup yang mengimplikasikannya), `purchase.group_purchase_user`, maupun `stock.group_stock_user`; `odoo:check` memeriksanya.
- **ACL grup:** baca `stock.picking` dan `stock.move`; baca, buat, dan ubah `account.move` dan `account.move.line`, tanpa hapus. Hak baca model lain datang dari `base.group_user` dan `account.group_account_readonly`.
- **Aturan rekaman:** buat dan ubah `account.move` hanya `[('move_type', '=', 'in_invoice'), ('state', '=', 'draft'), ('create_uid', '=', user.id)]`, dan setara untuk `account.move.line` lewat `move_id`. Aturan penolak `[(0, '=', 1)]` untuk buat, ubah, dan hapus `stock.move.line`, serta hapus `ir.attachment` dan `mail.message` (ACL `base.group_user` memberi CRUD penuh pada ketiganya). Pemeriksaan akses khusus `ir.attachment` dan `mail.message` memanggil pemeriksaan ACL dan aturan bawaan lebih dulu, jadi aturan penolak berlaku (terbukti di tes integrasi).
- **Pengaman status:** override `account.move.write` menolak `state` di nilai yang ditulis user grup agent di luar `sudo` (`AccessError`). Aturan rekaman diperiksa sebelum `write`, sehingga tanpa pengaman ini `button_cancel` pada draft milik agent sendiri lolos.
- **Matriks hak** diperiksa `odoo:check` lewat `has_access` (tingkat ACL pada recordset kosong dan tingkat aturan pada record seed), dan penolakannya dibuktikan `npm run test:integrasi` (24 kasus). Hasil yang perlu dicatat: `button_draft` pada draft ditolak Odoo dengan `UserError` 422 karena hanya entri terposting atau batal yang bisa di-reset; `create` dengan `state` posted ditolak `UserError` 422 dari pemeriksaan bawaan `account.move.create`; `button_validate` pada penerimaan yang sudah selesai tidak berbuat apa-apa (Odoo menyaring yang `done`), sedangkan pada penerimaan terbuka ditolak 403.
- **Key agent tanpa `sudo()`:** `env["res.users.apikeys"].with_user(agent)._generate(...)`, sehingga Odoo menerapkan `api_key_duration` grup. Key agent diputar sekali lewat jalur ini pada 7 November 2026 (key lama buatan `sudo` dihapus) dan berlaku sampai 5 Februari 2027. Key admin tidak berubah (dibuat dengan `sudo`, berlaku sampai 3 Februari 2027).
- **Hak baca `res.bank` (M3):** agent membaca bank terdaftar (`id`, `name`, `bic`) untuk BR-12. Haknya datang dari grup bawaan tanpa mengubah addon; `odoo:check` memeriksa baris matriksnya (69 pemeriksaan).
- **Gateway:** kode agent hanya bicara dengan Odoo lewat `AgentOdooGateway`, dengan daftar field per model, context `lang`/`tz`, dan galat bertipe. Lampiran berkas asli dibuat dengan context `image_no_postprocess`: tes integrasi menemukan Odoo mengecilkan foto 1500×2000 px dan meng-encode ulang JPEG-nya, sehingga checksum berkas berubah.
- **Draft tagihan** dibuat dalam satu `create`: kepala `move_type`, `partner_id`, `currency_id`, `fiscal_position_id` (bila ada), `invoice_origin`, `invoice_payment_term_id`, `ref`, `invoice_date`, `partner_bank_id`; baris (perintah `[0, 0, {...}]` di `invoice_line_ids`) `display_type` product, `product_id`, `product_uom_id`, `tax_ids`, `purchase_line_id`, `name`, `quantity`, `price_unit`. Odoo mengisi sendiri tanggal akuntansi dan baris pajak; nomor draft tetap kosong sampai diposting. `qty_invoiced` baris PO bertambah saat draft dibuat (field terhitung yang dihitung `sudo`).
- **NSFP:** tagihan vendor tidak punya field NSFP. Field `l10n_id_efaktur_coretax` di `account.move` hanya berlaku untuk `out_invoice` (`_compute_l10n_id_coretax_efaktur_available`), jadi NSFP dan tanggal faktur pajak ditulis di catatan chatter draft (`odoo_tools.bill_note_text`).
- **Catatan chatter:** `account.move._mail_post_access` bernilai `'read'`, jadi Odoo mengizinkan catatan di tagihan mana pun yang bisa dibaca. Gateway hanya menulis catatan dan lampiran pada draft yang boleh diubah agent menurut aturan rekaman Odoo (`has_access("write")`).
- **Angka:** klien JSON-2 mengirim `Decimal` bulat sebagai bilangan bulat JSON dan `Decimal` lain sebagai string (field float Odoo mengonversinya sendiri), lalu gateway membaca bilangan pecahan dari JSON sebagai `Decimal` (`parse_float`). Kode agent tidak pernah memegang `float`.

### 4.5 Demo publik (M6)
Dashboard di Vercel. API, Odoo, dan basis data di server yang ditentukan di M6, dengan data seed yang direset berkala dan akses dibatasi (kode akses atau batas laju). Keputusannya dicatat sebagai ADR baru.

---

## 5. Alternatif yang dipertimbangkan

| Alternatif | Kelebihan | Alasan tidak dipilih |
|---|---|---|
| **ERPNext** | Open source, API REST lengkap | Lebih berat dijalankan dan disiapkan dalam tiga minggu; Odoo lebih dikenal di industri |
| **XML-RPC / JSON-RPC Odoo** | Banyak contoh lama | Usang sejak Odoo 19 dan dijadwalkan dihapus ([ADR-0004](adr/0004-odoo-19-json2-api.md)) |
| **n8n atau Flowise** | Cepat dibuat, didukung ketentuan lomba | Interrupt, idempotensi, dan tes otomatis lebih sulit dikendalikan; arsitektur kode lebih mudah dinilai |
| **CrewAI atau AutoGen** (multi-agent) | Populer | Sulit dibuat deterministik; satu proses AP tidak butuh banyak agent |
| **OpenAI atau Claude API** | Kualitas tinggi | Berbayar sejak awal; Gemini punya tier gratis multimodal |
| **Satu PostgreSQL untuk Odoo dan aplikasi** | Hemat memori | Mencampur sistem pencatatan dengan aplikasi; Odoo butuh superuser untuk mengelola database |
| **Docker Engine** | Paling umum | Tidak dipakai di laptop pemilik; `compose.yaml` tetap kompatibel |
| **Next.js** seperti DombaTek | Satu framework web | Backend sudah Python; dashboard cukup SPA |

---

## 6. Milestone

| Milestone | Isi | Kriteria selesai | Target |
|---|---|---|---|
| **M0 Fondasi dan data Odoo** ✅ selesai 5 Nov 2026 | Kerangka repo, `compose.yaml`, init Odoo, klien JSON-2, pemuat dataset, seed, check, snapshot dan reset, `guard:secrets` | `odoo:check` lulus dari database kosong; reset < 1 menit; tes lulus; dokumen diperbarui | 6–7 Nov |
| **M1 Generator faktur** ✅ selesai 6 Nov 2026 | 40 skenario: berkas PDF/gambar/UBL, label, dan balasan vendor | Label lengkap sesuai SPEC §15.3–15.4; generator deterministik; tes | 8–9 Nov |
| **M2 Lapisan alat** ✅ selesai 7 Nov 2026 | Grup Odoo khusus agent (addon `faktur_beres_agent`) dan key agent tanpa `sudo()`, konfigurasi kebijakan, model faktur kanonis `InvoiceData` dan helper normalisasi, aturan deterministik dan mesin analisis, `AgentOdooGateway` dan `odoo_tools` dengan daftar operasi yang diizinkan, `match_engine`, `tax_check`, evaluasi orakel, tes integrasi | Tes unit untuk setiap aturan yang dihitung kode, termasuk tes keterlacakan dan arsitektur; mypy `--strict` untuk paket baru; `odoo:check` lulus dengan matriks hak agent; `eval:orakel` 40/40 luring dan `--odoo`; `test:integrasi` lulus (penolakan hak, tiga draft tagihan sama dengan label, idempotensi) | 10–11 Nov |
| **M3 Ekstraksi** ✅ selesai 9 Nov 2026 | Perbaikan dari tinjauan M2; klien Gemini (`faktur_beres.llm`: model per peran, retry, model cadangan, throttle, cache, audit, biaya setara); jenis berkas dan lapisan teks PDF; parser UBL; skema ekstraksi Gemini yang dinormalisasi ke `InvoiceData` dengan helper M2; validasi dan pembacaan ulang maksimal 2 kali (BR-04); injeksi dua lapis (pola kuat dan lemah, penilai LLM); **set uji ketahanan bergaya dunia nyata**; `eval:ekstraksi` dan `eval:injeksi` | UBL 100% sama dengan label; akurasi per field pada 40 faktur, 11 lampiran balasan, dan set ketahanan dilaporkan terpisah, beserta rantai ekstraksi → aturan dibanding orakel; `eval:orakel` tetap 40/40; pola kuat tanpa positif palsu di set dev; model dipilih lewat uji banding (ADR-0008) | 11–12 Nov |
| **M4a Graph inti, basis data, dan aksi** | Label versi 2; klien LLM (peran cadangan, batas waktu per tujuan, function calling multi-giliran); ekstraksi v2; migrasi Alembic pertama (`invoices`, `invoice_lines`, `match_results`, `actions`, `approvals`, `audit_log`); node LangGraph dan checkpointer PostgreSQL; perencana `identify` dan pemetaan baris oleh LLM di dalam pagar; aksi jalur A, B keluar (`email_tool`), dan C (interrupt, keputusan approver lewat CLI); `eval:graph` dan `eval:perencana` | `eval:graph` mode orakel: 40/40 pemeriksaan pertama berhenti, 27/27 status akhir, 13 B "menunggu M4b", ketahanan 13/13; graph dengan LLM dimatikan setara `analyse()` untuk 40 + 13; mode LLM dilaporkan; `alembic check` bersih; ADR-0009 dan ADR-0010 | 10–12 Nov |
| **M4b Balasan vendor, RAG, dan API** | Pemantau inbox dan email masuk (F-02); balasan vendor yang melanjutkan thread (F-06, BR-20); RAG kebijakan dan pilihan jalur oleh LLM dengan kutipan kebijakan (BR-17, BR-21); FastAPI + SSE dan API keputusan approver; pengingat dan daftar perhatian BR-19, `vendor_memory`, rekap harian (BR-26) | Jalur A, B, dan C berjalan ujung ke ujung, termasuk 13 skenario B yang menunggu balasan | 13–16 Nov |
| **M5 Dashboard dan evaluasi** | Dashboard (SPEC §10), harness 40 × 3, laporan metrik | Metrik terisi; target tercapai atau selisihnya dijelaskan | 16–19 Nov |
| **M6 Publik dan finalisasi** | Deploy publik, pengetatan guardrail, README demo, bahan video dan proposal | Tautan live dan repo publik; daftar periksa lomba lengkap | 20–26 Nov |
| Cadangan | Perbaikan | | 27–28 Nov |
| Pengumpulan | | | 29 Nov (batas 30 Nov) |

### Masukan dari tinjauan M0 untuk M2 (6 November 2026)
- **Grup Odoo khusus agent** lewat modul kecil di `infra/odoo/addons/faktur_beres_agent`:
  - hak baca PO, penerimaan, vendor, rekening, produk, dan pajak;
  - buat dan ubah draft tagihan, lampiran, dan catatan chatter;
  - tanpa hak hapus;
  - tanpa `account.group_account_invoice`, sehingga posting ditolak Odoo sendiri (wajib diuji);
  - `api_key_duration` 90 hari, sehingga key agent dibuat tanpa `sudo()`.
- **Alat agent** selalu mengirim context `lang` dan `tz`, selalu menyebut field secara eksplisit (jangan membaca `needed_terms`), dan mengisi sendiri field yang di UI diisi onchange (mis. termin pembayaran).
- **Pajak** dirujuk lewat external ID (`fb_seed.tax_ppn_masukan`), bukan nama.
- **e-Faktur:** periksa field tambahan `l10n_id_efaktur_coretax` di tagihan vendor (mis. nomor faktur pajak) dan isi bila ada.
- **Dependensi:** `python-stdnum` dipindah ke dependensi utama bila `tax_check` memakainya saat runtime.

### Masukan dari tinjauan M1 untuk M3–M4 (7 November 2026)
- **Set uji ketahanan bergaya dunia nyata (M3).** Selain 40 skenario, M3 menambah set kecil dokumen sintetis yang meniru ciri umum faktur lapangan:
  - PDF hasil pindai tanpa lapisan teks;
  - satu PDF berisi beberapa dokumen: faktur, ringkasan faktur pajak buatan sendiri (tanpa QR dan tanpa tata letak resmi DJP), dan surat jalan;
  - harga termasuk PPN, dan cetakan tarif "PPN 11%" dengan angka yang sama dengan 12% × DPP nilai lain;
  - stempel perusahaan fiktif dan tanda tangan coretan yang menimpa teks, tanpa meniru meterai atau tanda resmi lain;
  - tabel tanpa kode produk, sehingga baris harus dipetakan dari deskripsi;
  - NPWP dan NSFP dengan pemisah, halaman sedikit miring, dan ukuran kertas selain A4.

  Setiap dokumen punya label `kebenaran_dokumen` dan jalur yang diharapkan. Hasilnya dilaporkan terpisah dari metrik utama. Di proposal, set ini disebut "dokumen sintetis yang meniru ciri umum dokumen lapangan", bukan dokumen nyata.
- **Injeksi lapis pertama (M3)** dijalankan atas semua teks hasil baca LLM (termasuk teks di gambar dan pindaian), lapisan teks PDF (termasuk teks yang tidak terlihat), dan seluruh teks bebas UBL. Di M4, subjek dan isi email ikut diperiksa.
- **Skema ekstraksi (M3)** untuk Gemini terpisah dari `InvoiceData` M2, dan hasilnya dinormalisasi dengan helper M2. Format angka yang ambigu menjadi kegagalan validasi (BR-04), bukan ditebak.
- **Parser UBL (M3)** mengikuti SPEC §15.6, termasuk tanggal faktur pajak yang boleh tidak ada.
- **Balasan teks vendor (M4)** bisa menyebut lebih dari satu nomor PO, misalnya PO batal dan penggantinya. Isi balasan diekstrak sebagai data, lalu BR-06 dijalankan ulang untuk memutuskan.
- **Evaluasi penuh (M5)** dilaporkan berdampingan dengan evaluasi orakel. Selisih keduanya menunjukkan kesalahan yang berasal dari ekstraksi atau LLM.

### Masukan dari tinjauan M2 untuk M3–M4 (9 November 2026)
- **Pintu `call()` gateway hanya baca (M3).** Tagihan, lampiran, dan catatan dibuat hanya lewat `create_draft_bill`, `attach_file`, dan `post_note`, yang memakai rencana tagihan (BR-18) dan kunci idempoten (BR-22). Alat Odoo yang dibuka ke LLM di M4 hanya membaca, dengan batas jumlah baris.
- **Log gateway (M3)** mencatat ukuran dan SHA-256 berkas lampiran, bukan isinya.
- **Lampiran (M3).** `checksum` dibaca ulang dari Odoo setelah lampiran dibuat, lalu dibandingkan dengan berkas asli. `image_no_postprocess` hanya dipasang gateway saat membuat lampiran. Ukuran berkas dibatasi saat faktur masuk (alat ekstraksi M3, intake M4).
- **`fields_get` dan `has_access`** hanya dipakai kode, pada model yang diizinkan, dan tidak dibuka sebagai alat LLM.
- **Galat Odoo di node aksi (M4).** `Rejected` (422) menjadi `validation_failed`; `Unavailable` dicoba ulang dengan jeda (BR-27); `AccessDenied` dicatat sebagai sinyal keamanan.
- **Addon (M3)** di-upgrade hanya bila versinya naik. Isi addon dikunci tes sidik terhadap versi manifest, dan `odoo:reset` memperingatkan snapshot yang memakai addon versi lama.
- **"Faktur lain di Faktur Beres" untuk BR-11 (M4)** tidak memuat faktur yang sedang diproses, yaitu yang `invoice_id`-nya sama.
- **Rekening (BR-12, SPEC 0.4).** Nama bank tercetak yang berbeda dengan nomor yang sama hanya menjadi catatan, kecuali nama itu cocok dengan bank lain yang terdaftar di Odoo.
- **Injeksi dua lapis (BR-15, SPEC 0.4, M3).** Pola kuat langsung menjadi C5. Pola lemah dinilai penilai LLM yang membaca semua teks bebas, dan kutipannya dicocokkan kode. Bila penilai tidak tersedia, berlaku gagal aman. Di M4, jalur A tidak dijalankan sebelum lapis 2 berhasil menilai dokumen. Set frasa dev ada di repo; set tertutup milik perencana dijalankan saat tinjauan. Keterbatasan yang diukur: instruksi di gambar yang meminta pembaca tidak menyalinnya, karena penilai hanya melihat teks hasil baca. Mitigasi lanjutan dipertimbangkan di M6.
- **Model Gemini (M3, ADR-0008)** dipilih per peran dan lewat uji banding, karena kuota tier gratis per model kecil dan berbeda-beda.
- **`gen:periksa` (M3)** membandingkan isi berkas (halaman PDF dan piksel gambar), karena keluaran kompresi zlib bisa berbeda antar-mesin walaupun isinya sama.

### Masukan dari M3 untuk M4 (9 November 2026)
- **Peran cadangan LLM.** Di tier gratis, peran utama dan kuat sama-sama memakai Flash-Lite, karena Flash 3.8 menjawab 503 dan butuh sekitar 195 detik per dokumen (ADR-0008). Selama kedua peran sama, tidak ada model cadangan, dan gangguan Flash-Lite membuat faktur menunggu sesuai BR-27. M4 menambah peran cadangan yang terpisah dari peran kuat, dengan batas waktu sendiri dan model yang kuotanya terpisah.
- **Label email pembeli (M4.0).** Kebenaran label 40 skenario memuat email pembeli, padahal PDF dan gambar tidak mencetaknya; hanya UBL yang memuatnya. `eval:ekstraksi` menilai field itu hanya pada UBL. Label diperbaiki di M4.0 dengan persetujuan pemilik.
- **Thought signature.** Gemini 3 mewajibkan *thought signature* dikembalikan pada percakapan multi-giliran dengan function calling (ADR-0008).

---

### Masukan dari tinjauan M3 untuk M4a–M4b (10 November 2026)
- **M4 dipecah dua.** M4a mencakup semua langkah sampai agent pertama kali berhenti (kondisi `sebelum_simulasi` di SPEC §15.4), ditambah keputusan approver lewat CLI. M4b mencakup email masuk, balasan vendor, RAG dan pilihan jalur oleh LLM, API, serta pengingat dan rekap. Dashboard tetap di M5.
- **Label versi 2.** `kebenaran_dokumen.pembeli.email` hanya diisi bila dokumen mencetaknya (saat ini hanya UBL), untuk 40 faktur, lampiran balasan, dan set ketahanan. Berkas dokumen tidak berubah.
- **Batas waktu.** `.env` pemilik memakai `GEMINI_TIMEOUT_SECONDS=120`; nilai 300 sebelumnya keliru.
- **Peran cadangan LLM** memakai `gemini-3.6-flash` (kuota AI Studio dicek pemilik 10 November 2026: 5 permintaan per menit dan 20 per hari; Flash-Lite 15 per menit dan 500 per hari; reset harian pukul 15.00 WIB). `gemini-3.7-flash` tidak dipakai, karena menurut halaman deprecations Gemini API endpoint itu diarahkan otomatis ke `gemini-3.8-flash`, model yang di M3 menjawab 503 dan butuh sekitar 195 detik per dokumen.
- **Embedding RAG (M4b)** memakai `gemini-embedding-2`, dengan kuota 100 permintaan per menit dan 1.000 per hari.
- **Set frasa injeksi tertutup** dijalankan pemilik pada 10 November 2026; hasilnya di [eval/README](../eval/README.md#set-frasa-injeksi). Set itu tetap dipegang perencana dan dijalankan lagi sebelum pengumpulan.

## 7. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Kuota atau biaya Gemini habis | Cache ekstraksi per hash berkas, model cadangan, evaluasi tidak dijalankan paralel berlebihan |
| Kuota tier gratis Gemini kecil dan berbeda per model (Flash 20 permintaan per hari, Flash-Lite 500) | Kedua peran memakai Flash-Lite di tier gratis (ADR-0008); throttle per model dari RPM; 429 per hari menandai model habis; jendela kuota direset 15.00 WIB; `eval:ekstraksi --lanjut` untuk run yang terputus; panggilan dalam jumlah banyak hanya setelah izin pemilik |
| Tidak ada model cadangan selama peran utama dan kuat memakai model yang sama | Gangguan Flash-Lite menjadi `LlmUnavailable` (BR-27): run evaluasi berhenti dan dilanjutkan dengan `--lanjut`. M4 menambah peran cadangan yang terpisah dari peran kuat, dengan batas waktu sendiri dan model berkuota terpisah (ADR-0008) |
| Nama field Odoo 19 berbeda dari versi lama | Verifikasi `fields_get` di M0, satu modul klien, catatan field di §4.4 |
| Image Odoo berubah diam-diam | Tag bertanggal, naik versi secara sengaja |
| Bentrok dengan DombaTek (port, data, sumber daya) | Proyek compose dan port terpisah, aturan di CLAUDE.md, pemeriksaan awal di setiap tugas |
| Path repo mengandung spasi | Semua path dikutip; diuji di M0 |
| Folder repo di bawah `Drive` ikut tersinkron | **Ditutup (6 Nov 2026): folder lokal, tidak tersinkron.** Mount jaringan atau FUSE tetap dihindari (diperiksa M0) |
| Keluaran LLM bervariasi antar-run | Skema ketat dan validasi kode, pembacaan ulang BR-04 yang menerima nilai hanya bila dua pembacaan konsisten, temperature dibiarkan bawaan sesuai saran Gemini 3, evaluasi tanpa cache, metrik pass^3 (M5) |
| Instruksi di gambar yang meminta pembaca tidak menyalinnya | Penilai injeksi hanya melihat teks hasil baca, jadi instruksi yang tidak disalin tidak pernah dinilai. Prompt ekstraksi meminta semua teks bebas disalin; set ketahanan (KTH-13) mengukur keterbatasan ini; mitigasi lanjutan dipertimbangkan di M6 |
| Label evaluasi bocor ke agent | Aturan di CLAUDE.md dan ADR-0007, tes yang memastikan data Odoo dan berkas faktur tidak memuat ID skenario |
| Demo publik disalahgunakan | Kode akses, batas laju, dan reset berkala (M6) |
| Hak user agent di M0 masih terlalu luas | **Ditutup (7 Nov 2026):** grup khusus `faktur_beres_agent.group_agent` tanpa hapus dan posting, aturan rekaman hanya untuk draft tagihan vendor buatan agent, pengaman perubahan status, dan `AgentOdooGateway`; diperiksa `odoo:check` dan dibuktikan `test:integrasi` (§4.4) |
| Kuota model cadangan hanya 20 permintaan per hari | Cadangan hanya untuk gangguan singkat dan paling akhir di rantai; perencana menolaknya; 5xx dan timeout dicoba ulang sekali (ADR-0010) |
| Waktu proses foto di tier gratis | `GEMINI_TIMEOUT_FALLBACK_SECONDS` terpisah, waktu sampai jalur dicatat per format di `eval:graph` |
| Waktu tiga minggu | Milestone kecil, commit kecil, cadangan 27–28 November |

---

## 8. Keputusan

### 8.1 Ringkasan (5 November 2026)

| Keputusan | ADR |
|---|---|
| Stack inti: Python 3.13, LangGraph, FastAPI, Gemini, PostgreSQL + pgvector, React + Vite | [0001](adr/0001-stack-inti.md) |
| Satu repo: uv untuk Python, npm sebagai penjalan perintah dan workspace dashboard | [0002](adr/0002-monorepo-uv-npm.md) |
| Podman rootless dengan proyek compose `fakturberes` dan port +20000, berdampingan dengan DombaTek | [0003](adr/0003-podman-proyek-terpisah.md) |
| Odoo 19 Community lewat External JSON-2 API dengan user bot dan dua API key | [0004](adr/0004-odoo-19-json2-api.md) |
| LLM merencanakan dan memutuskan di dalam pagar; kode menghitung dan menjalankan aksi | [0005](adr/0005-llm-memutuskan-kode-menghitung.md) |
| Persetujuan lewat interrupt, aksi di node terpisah, dan kunci idempoten | [0006](adr/0006-persetujuan-dan-idempotensi.md) |
| Data sintetis, dataset tunggal, dan katalog 40 skenario berlabel | [0007](adr/0007-data-sintetis-dan-skenario.md) |

### 8.2 Masih terbuka
Model Gemini persis (M3), dimensi embedding (M4), hosting demo publik dan pembatasan aksesnya (M6), serta lisensi repo (M6). Rincian di [SPEC §14](SPEC.md#14-asumsi-dan-pertanyaan-terbuka).
