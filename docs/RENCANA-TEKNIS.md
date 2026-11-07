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
| `faktur_beres.generator` | Generator faktur PDF/gambar/UBL, label, dan balasan vendor | M1 |
| `faktur_beres.domain` | Model faktur kanonis `InvoiceData`, kode alasan dan jalur, record ERP dan protokol `ErpReader`, helper normalisasi, `PolicyConfig`, aturan deterministik (`rules/`, satu modul per kelompok BR) dan mesin `analyse()`. Kode agent: tidak mengimpor `evals`, `generator`, `dataset`, atau `seed` (tes arsitektur) | M2 |
| `faktur_beres.tools` | Alat agent: `AgentOdooGateway` (satu-satunya pintu kode agent ke Odoo, key agent, daftar operasi dan field yang diizinkan), `odoo_tools` dan `OdooErpReader`, `match_engine`, `tax_check` (M2); `extract_invoice`, `policy_rag`, `email_tool` (M3–M4) | M2–M4 |
| `faktur_beres.graph` | Node, state, dan routing LangGraph | M4 |
| `faktur_beres.api` | FastAPI, SSE, dan pemantau inbox | M4 |
| `faktur_beres.evals` | Kode evaluasi, satu-satunya yang membaca label dan dataset: konverter label ke `InvoiceData`, pembaca ERP dari dataset, evaluasi orakel (M2); harness 40 × 3 dan penilai state akhir (M5) | M2–M5 |

### 3.3 Penanganan kesalahan dan skala
- Panggilan Odoo dan LLM memakai retry dengan backoff eksponensial dan batas waktu. Kesalahan JSON-2 dipetakan dari objek error Odoo (`name`, `message`) ke exception Python yang jelas.
- Setiap panggilan JSON-2 adalah satu transaksi. Operasi yang harus atomik memakai satu metode Odoo (mis. `create` dengan baris sekaligus), dan kegagalan di tengah ditangani lewat kunci idempoten dan pemeriksaan ulang (SPEC BR-22).
- Bila kuota LLM habis, agent beralih ke model cadangan. Hasil ekstraksi di-cache per hash berkas untuk demo; cache dimatikan saat evaluasi.
- MVP memakai satu proses API yang menjalankan graph di latar. Karena state ada di PostgreSQL, worker bisa dipisah dan ditambah tanpa mengubah graph.

### 3.4 Keterlacakan aturan
Setiap aturan yang dihitung kode punya tes bertanda `@pytest.mark.aturan("BR-xx")`. Daftar aturannya adalah konstanta `faktur_beres.domain.rules.CODE_RULES`, dan `tests/test_traceability.py` memastikan tiap BR di daftar itu punya minimal satu tes. Jumlah di bawah dihitung saat M2 selesai (7 November 2026): fungsi tes, dan kasus setelah parametrisasi.

| BR | Fungsi atau modul | Berkas tes | Tes (kasus) |
|---|---|---|---|
| BR-03 | `rules/arithmetic.py`: `check_arithmetic` | `test_rule_arithmetic.py` | 6 (10) |
| BR-05 | `rules/vendor.py`: `identify_vendor`; `engine.analyse` melewati pemeriksaan bergantung vendor | `test_rule_vendor.py`, `test_engine.py` | 11 (12) |
| BR-06 | `rules/purchase_order.py`: `identify_purchase_order`, `is_strong_candidate` | `test_rule_purchase_order.py`, `test_engine.py` | 12 (16) |
| BR-07 | `rules/lines.py`: `map_line` | `test_rule_lines.py` | 4 (9) |
| BR-08 | `rules/lines.py`: `check_price`, `price_limit` | `test_rule_lines.py` | 4 (4) |
| BR-09 | `rules/lines.py`: `check_quantity`, `match_lines`; `tools/match_engine.py` | `test_rule_lines.py`, `test_engine.py`, `test_match_and_tax_tools.py` | 10 (10) |
| BR-10 | `rules/tax.py`: `check_tax`, `check_seller_identity`; `tax_ids.py`; `tools/tax_check.py` | `test_rule_tax.py`, `test_match_and_tax_tools.py` | 14 (22) |
| BR-11 | `rules/duplicates.py`: `find_duplicates` | `test_rule_duplicates.py` | 9 (9) |
| BR-12 | `rules/bank.py`: `check_bank_account` | `test_rule_bank.py` | 7 (7) |
| BR-13 | `rules/policy_checks.py`: `check_threshold` | `test_rule_policy_checks.py` | 1 (4) |
| BR-14 | `rules/policy_checks.py`: `check_new_vendor` | `test_rule_policy_checks.py` | 3 (3) |
| BR-15 | `rules/injection.py`: `PATTERNS`, `scan_fragments` (lapis pertama) | `test_rule_injection.py` | 8 (37) |
| BR-16 | `rules/routing.py`: `priority_route`, `initial_status` | `test_rule_routing.py` | 2 (6) |
| BR-17 | `rules/routing.py`: `allowed_routes`, `default_route`, `guard_route` | `test_rule_routing.py` | 4 (15) |
| BR-18 | `rules/bill_plan.py`: `plan_bill`, `BillPlan`; `tools/odoo_tools.py`: `create_draft_bill`, `verify_bill` | `test_rule_bill_plan.py` (+ `test_odoo_tools_draft.py`, `test_integrasi_draft.py`) | 5 (5) |
| BR-22 | `rules/safety.py`: `canonical_json`, `idempotency_key` | `test_rule_safety.py` | 3 (3) |
| BR-24 | `rules/safety.py`: `mask_npwp`, `mask_account`, `mask_params` (dipakai log gateway) | `test_rule_safety.py` (+ `test_odoo_gateway.py`) | 2 (2) |
| BR-25 | `rules/safety.py`: `check_recipient` | `test_rule_safety.py` | 2 (15) |
| BR-28 | `domain/policy.py`: `PolicyConfig`, `load_policy` | `test_policy.py` | 2 (2) |

Rangkaian lengkapnya diuji `tests/test_engine.py` dan evaluasi orakel (`tests/test_orakel.py`, 40/40). Batas antara kode agent dan kode evaluasi diuji `tests/test_architecture.py`.

---

## 4. Repositori, alat, dan kontainer

### 4.1 Struktur repositori

```text
faktur-beres/            # nama folder repo (lihat README)
├─ src/faktur_beres/     # paket Python (§3.2)
├─ tests/                # pytest
├─ data/
│  ├─ seed/dataset.yaml  # sumber tunggal data sintetis (SPEC §9.4)
│  └─ kebijakan/         # SOP AP dan kontrak vendor sintetis untuk RAG (M4)
├─ eval/
│  ├─ skenario/          # label 40 skenario (M1, hasil generator, di-commit)
│  ├─ berkas/            # faktur uji dan balasan vendor (M1, hasil generator, di-commit)
│  ├─ DAFTAR.md          # tabel ringkas 40 skenario (hasil generator)
│  ├─ README.md          # penjelasan folder (ditulis tangan)
│  └─ hasil/             # hasil evaluasi (tidak di-commit)
├─ web/                  # dashboard React + Vite (M5), workspace npm
├─ infra/
│  ├─ odoo/odoo.conf     # konfigurasi Odoo lokal (tanpa rahasia)
│  ├─ odoo/addons/       # folder addon Odoo (kosong sampai M2)
│  ├─ odoo/shell/        # skrip ORM untuk `odoo shell` (bootstrap odoo:init)
│  └─ scripts/           # skrip shell untuk Podman: init, snapshot, reset, tunggu layanan
├─ var/                  # berkas runtime lokal, mis. var/berkas/ (tidak di-commit)
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
| Lint dan tes | `npm run check` (gabungan `npm run lint` dan `npm test`) | M0 |
| Pastikan tidak ada nilai rahasia di berkas yang akan di-commit | `npm run guard:secrets` | M0 |
| Buat faktur uji, label, dan balasan vendor (tulis ulang `eval/skenario/`, `eval/berkas/`, `eval/DAFTAR.md`) | `npm run gen:faktur` | M1 |
| Pastikan berkas di `eval/` identik byte per byte dengan keluaran generator (SHA-256) | `npm run gen:periksa` | M1 |
| Jalankan API dan dashboard untuk pengembangan | `npm run dev:api` / `npm run dev:web` | M4 / M5 |
| Evaluasi 40 skenario × 3 | `npm run eval` | M5 |
| Tambah dependensi Python | `uv add <paket>` atau `uv add --dev <paket>` | |

npm hanya menjadi penjalan perintah dan pengelola dependensi dashboard. Skrip Python dipanggil lewat `uv run python -m ...`.

### 4.3 Kontainer lokal dan port

| Layanan | Image | Port host → kontainer | Volume | Catatan |
|---|---|---|---|---|
| `odoo` | `docker.io/library/odoo:19.0-20261102` | `127.0.0.1:28069` → 8069 | `odoo-data` (`/var/lib/odoo`) | `infra/odoo/odoo.conf` di-bind `:ro,Z`. `workers = 0`, jadi websocket lewat port yang sama. Folder kosong `infra/odoo/addons` di-bind ke `/mnt/extra-addons` (`:ro,Z`) agar Podman tidak membuat volume anonim. Sandi DB lewat env `PGPASSWORD`. Healthcheck `curl /web/health`. |
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
- **User:** `admin` (sandi dari `.env`, API key admin untuk skrip pengembang) dan `agen@arunika.test` (tanpa sandi, API key agent). Di M0, user agent memakai grup standar minimal yang cukup untuk membaca PO dan penerimaan serta membuat draft tagihan. Hak yang ikut terbawa (hapus, posting) dicatat dan diketatkan di M2 dengan grup khusus.
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
| **M2 Lapisan alat** | Grup Odoo khusus agent (addon `faktur_beres_agent`) dan key agent tanpa `sudo()`, konfigurasi kebijakan, model faktur kanonis `InvoiceData` dan helper normalisasi, aturan deterministik dan mesin analisis, `AgentOdooGateway` dan `odoo_tools` dengan daftar operasi yang diizinkan, `match_engine`, `tax_check`, evaluasi orakel, tes integrasi | Tes unit untuk setiap aturan yang dihitung kode, termasuk tes keterlacakan dan arsitektur; mypy `--strict` untuk paket baru; `odoo:check` lulus dengan matriks hak agent; `eval:orakel` 40/40 luring dan `--odoo`; `test:integrasi` lulus (penolakan hak, tiga draft tagihan sama dengan label, idempotensi) | 10–11 Nov |
| **M3 Ekstraksi** | Skema ekstraksi Gemini yang dipetakan ke `InvoiceData` (M2), parser UBL, perbaikan maksimal 2 kali, cache, injeksi lapis pertama atas teks hasil baca dan lapisan teks PDF, **set uji ketahanan bergaya dunia nyata** | Akurasi per field pada 40 faktur dan pada set ketahanan, dilaporkan terpisah | 11–12 Nov |
| **M4 Graph lengkap** | Node LangGraph, routing, interrupt, checkpointer, `email_tool` dan pemantau inbox, RAG kebijakan, FastAPI + SSE, tabel aplikasi | Jalur A, B, dan C berjalan ujung ke ujung untuk satu skenario per kategori | 13–16 Nov |
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

---

## 7. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Kuota atau biaya Gemini habis | Cache ekstraksi per hash berkas, model cadangan, evaluasi tidak dijalankan paralel berlebihan |
| Nama field Odoo 19 berbeda dari versi lama | Verifikasi `fields_get` di M0, satu modul klien, catatan field di §4.4 |
| Image Odoo berubah diam-diam | Tag bertanggal, naik versi secara sengaja |
| Bentrok dengan DombaTek (port, data, sumber daya) | Proyek compose dan port terpisah, aturan di CLAUDE.md, pemeriksaan awal di setiap tugas |
| Path repo mengandung spasi | Semua path dikutip; diuji di M0 |
| Folder repo di bawah `Drive` ikut tersinkron | **Ditutup (6 Nov 2026): folder lokal, tidak tersinkron.** Mount jaringan atau FUSE tetap dihindari (diperiksa M0) |
| Hasil LLM tidak deterministik | Temperature rendah, skema ketat, metrik pass^3 |
| Label evaluasi bocor ke agent | Aturan di CLAUDE.md dan ADR-0007, tes yang memastikan data Odoo dan berkas faktur tidak memuat ID skenario |
| Demo publik disalahgunakan | Kode akses, batas laju, dan reset berkala (M6) |
| Hak user agent di M0 masih terlalu luas | Sementara; diketatkan di M2, dan alat agent tetap hanya mengizinkan operasi tertentu |
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
