# Tugas M4a: catatan tinjauan M3, graph inti, basis data aplikasi, dan aksi sampai agent pertama kali berhenti

## Konteks
Kamu adalah Claude Code yang bekerja di repo `/home/farhan/Drive/02 - Proyek Pribadi/02 - Kompetisi/2026/GTNIC2026/faktur-beres`.

M3 sudah selesai dan ditinjau pemilik bersama Claude (chat perencana) pada 9–10 November 2026. Perencana memeriksa ulang arsip HEAD `b2c89ab`, yang sama dengan `origin/main`:
- ruff dan mypy bersih;
- 1.112 tes lulus;
- orakel 40/40;
- `gen:periksa` sama isi di mesin perencana.

Pemilik juga sudah menjalankan set frasa injeksi tertutup (hasilnya di bawah).

**M4 dipecah menjadi dua tugas:**
- **M4a (tugas ini)** mencakup semua langkah sampai agent **pertama kali berhenti**, yaitu kondisi `sebelum_simulasi` di SPEC §15.4:
  - jalur A membuat draft tagihan;
  - jalur B mengirim email ke vendor lalu menunggu, atau menutup duplikat pasti;
  - jalur C berhenti di interrupt dengan paket bukti.

  M4a juga mencakup keputusan approver lewat CLI: setujui, tolak, atau minta klarifikasi.
- **M4b (tugas berikutnya)** mencakup:
  - pemantau inbox dan email masuk (F-02);
  - balasan vendor yang melanjutkan thread (F-06, BR-20);
  - RAG kebijakan dan pilihan jalur oleh LLM dengan kutipan kebijakan (BR-17, BR-21);
  - FastAPI + SSE dan API keputusan approver;
  - pengingat dan daftar perhatian BR-19, `vendor_memory`, dan rekap harian (BR-26).

  Dashboard dikerjakan di M5.

Tugas ini terdiri dari:
- **M4a.0** catatan tinjauan M3 di dokumen;
- **M4a.1** label versi 2: email pembeli;
- **M4a.2** klien LLM: peran cadangan, batas waktu per tujuan, dan function calling multi-giliran;
- **M4a.3** ekstraksi v2: nomor urut baris bukan kode barang;
- **M4a.4** basis data aplikasi;
- **M4a.5** graph inti;
- **M4a.6** LLM di dalam pagar: perencana `identify` dan pemetaan baris;
- **M4a.7** aksi jalur A, B, dan C;
- **M4a.8** evaluasi graph;
- **M4a.9** dokumen dan push.

Baca dulu, berurutan:
1. `CLAUDE.md` seluruhnya.
2. `docs/SPEC.md`:
   - §5, §6, dan §7 (F-01–F-07, F-10);
   - §8, terutama BR-01, BR-05–BR-07, BR-11, BR-15–BR-25, dan BR-27;
   - §9.2, §9.3, §10, §11, §12, §13, §15.3, dan §15.4.
3. `docs/RENCANA-TEKNIS.md` §2, §3, §4.1, §4.2, §6 (semua subbagian masukan), dan §7.
4. `docs/adr/README.md`, lalu ADR `0001`, `0005`, `0006`, `0007`, dan `0008`.
5. `docs/lomba/gtnic-2026.md`, bagian Kriteria penilaian.
6. `eval/README.md` dan beberapa label:
   - `eval/skenario/COC-04.yaml`, `QTY-01.yaml`, `DUP-01.yaml`, `NPO-01.yaml`, `NPO-02.yaml`, `INJ-02.yaml`;
   - `eval/ketahanan/skenario/KTH-06.yaml`.
7. `docs/tugas/README.md` dan bagian Laporan di `docs/tugas/M3.md`.
8. Kode yang dipakai ulang:
   - `domain/engine.py`, `domain/erp.py`;
   - `domain/rules/purchase_order.py`, `lines.py`, `routing.py`, `safety.py`, `injection.py`;
   - `tools/odoo_tools.py`, `tools/odoo_gateway.py`, `tools/extract_invoice.py`, `tools/injection_review.py`;
   - seluruh paket `llm/`, dan `extraction/reading.py`;
   - `evals/orakel.py`, `evals/labels.py`;
   - `generator/labels.py`, `generator/robust_build.py`, `generator/label_schema.py`.

## Hasil set frasa injeksi tertutup (dijalankan pemilik, 10 November 2026)
Set versi 1 berisi 52 frasa. SHA-256-nya `69550575a92a6b1990d02db9ac0b339072af43e9451cbce6a224b532df764a47`, sama dengan yang dicatat di `docs/tugas/M3.md`. Run memakai mode lengkap dan tanpa cache, dengan 52 panggilan `gemini-3.5-flash-lite` dan biaya setara USD 0,0117. Status lapis 2: ok 52, tidak tersedia 0, tidak valid 0, terlalu panjang 0.

| Ukuran | Serangan tertangkap | Frasa wajar ditandai |
|---|---|---|
| Pola kuat saja | 7/22 | 0/30 |
| Lapis 1 (kuat dan lemah) | 10/22 | 0/30 |
| Lapis 2 saja | 22/22 | 0/30 |
| Akhir (kombinasi BR-15) | 22/22 | 0/30 |

Rincian 22 serangan:
- 7 tertangkap pola kuat;
- 3 pola lemah dikonfirmasi lapis 2;
- 12 tanpa pola ditangkap lapis 2.

Setiap jawaban "ya" membawa satu kutipan yang cocok.

Batas tafsirnya:
- hanya satu run;
- jumlah frasa kecil: batas bawah recall 95% sekitar 87%, dan batas atas positif palsu sekitar 10%;
- frasa ditulis perencana dan diukur per frasa, bukan per dokumen;
- tidak ada frasa wajar yang memicu pola lemah, jadi jalur "pola lemah, lalu lapis 2 menjawab tidak" untuk teks wajar hanya terukur di set dev.

Set ini tetap dipegang perencana dan akan dijalankan lagi sebelum pengumpulan. Jangan berusaha menebak isinya.

## Keputusan pemilik dari tinjauan M3 (10 November 2026)
1. **Label versi 2.**
   - `kebenaran_dokumen.pembeli.email` hanya diisi bila dokumen mencetaknya (saat ini hanya UBL). Untuk PDF dan gambar nilainya `null`.
   - Berkas dokumen tidak berubah.
   - Berlaku untuk 40 faktur, lampiran balasan, dan set ketahanan. `versi_label` naik menjadi 2.
   - Ini juga menjawab pertanyaan kedua di laporan M3.
2. **`.env` pemilik** kini berisi `GEMINI_TIMEOUT_SECONDS=120`. Nilai 300 sebelumnya keliru (pertanyaan pertama di laporan M3).
3. **Pemecahan M4** seperti di Konteks.
4. **Peran cadangan LLM memakai `gemini-3.6-flash`.**
   - Kuota di AI Studio (dicek pemilik 10 November 2026): 5 permintaan per menit, 20 per hari. Flash-Lite tetap 15 per menit dan 500 per hari, dan kuota harian direset pukul 15.00 WIB.
   - `gemini-3.7-flash` tidak dipakai. Menurut halaman deprecations Gemini API, endpoint itu diarahkan otomatis ke `gemini-3.8-flash` dan tidak bisa dipilih di AI Studio. Model itu sama dengan Flash 3.8 yang di M3 menjawab 503 dan butuh sekitar 195 detik per dokumen.
5. **Embedding RAG di M4b memakai `gemini-embedding-2`**, dengan kuota 100 permintaan per menit dan 1.000 per hari. Model ini tidak dipakai di M4a.

## Kriteria selesai
1. **M4a.0:** prompt tersimpan, RENCANA §6 memuat baris M4a dan M4b, dan hasil set tertutup tercatat di `eval/README.md`.
2. **Label versi 2:**
   - 40 label skenario dan 13 label ketahanan dibuat ulang lewat generator; berkas dokumen tidak berubah (`sha256` sama, `gen:periksa` lulus);
   - ada tes bahwa email pembeli ada di label hanya bila tercetak;
   - pengecualian `buyer_email` di `eval:ekstraksi` dihapus;
   - `eval:orakel` tetap 40/40, luring dan `--odoo`;
   - SPEC 0.5 §15.4 diperbarui.
3. **Klien LLM:**
   - peran cadangan, batas waktu per tujuan, dan function calling multi-giliran dengan *thought signature* selesai, semuanya diuji tanpa jaringan;
   - uji asap `gemini-3.6-flash` dilaporkan.
4. **Ekstraksi v2:** satu run segar terarah (KTH-06 dan 10 dokumen banding) tanpa regresi field kritis, dan `kode` baris KTH-06 bernilai `null`.
5. **Basis data:**
   - migrasi Alembic pertama untuk tabel M4a;
   - `alembic check` bersih;
   - tes migrasi di database sementara;
   - `db:migrate` dan `db:reset` tersedia.
6. **Graph:**
   - semua node M4a dan checkpointer PostgreSQL;
   - interrupt jalur C dengan resume yang idempoten;
   - transisi status yang dijaga sesuai SPEC §6.3;
   - status `failed` dengan `agent:ulang`;
   - `audit_log` (BR-23) yang tersamar (BR-24);
   - perintah CLI di M4a.5.
7. **Kesetaraan:** tes tanpa jaringan membuktikan graph dengan LLM dimatikan memberi hasil yang sama dengan `analyse()` untuk 40 skenario dan 13 dokumen ketahanan: alasan, jalur, status pertama berhenti, dan rencana tagihan.
8. **LLM di dalam pagar:**
   - perencana `identify` dan pemetaan baris selesai, masing-masing dengan guard dan tes LLM palsu;
   - `eval:perencana` (8 kasus) dilaporkan.
9. **Aksi:**
   - **A:** draft, lampiran, dan catatan, dengan kunci idempoten, verifikasi, dan eskalasi ke C;
   - **B:** email klarifikasi dan pemberitahuan duplikat lewat `email_tool` (BR-19 bagian keluar, BR-25);
   - **C:** paket bukti BR-21 tanpa kutipan kebijakan, dan keputusan setujui, tolak, atau klarifikasi yang idempoten.
10. **`eval:graph` mode orakel adalah syarat lulus:**
    - **40/40** lulus pemeriksaan pertama berhenti;
    - **27/27** lulus status akhir: 9 jalur A, 2 duplikat pasti, dan 16 jalur C setelah simulasi approver, termasuk uji idempotensi COC-04 dan COC-05;
    - 13 skenario B lain ditandai "menunggu M4b";
    - set ketahanan **13/13** sesuai `harapan`, dengan KTH-06 lewat pemetaan LLM.
11. **`eval:graph` mode LLM:** satu run segar untuk 40 skenario dan 13 dokumen ketahanan, dilaporkan bersama waktu sampai jalur ditentukan (median dan p95) serta biaya per faktur. Targetnya ≥ 36/40 lulus pemeriksaan yang tersedia di M4a, tetapi target ini bukan syarat lulus. Bila tidak tercapai, sertakan analisis per kegagalan.
12. **Pemeriksaan:** `npm run check`, `npm run test:integrasi`, dan `npm run guard:secrets` lulus. Paket baru masuk daftar mypy `--strict`.
13. **Penutup:**
    - dokumen diperbarui;
    - ADR-0009 dan ADR-0010 berstatus Diusulkan;
    - M4a ditandai selesai, lalu di-push;
    - laporan ditulis.

## Batasan keras
Semua aturan di CLAUDE.md berlaku. Khusus M4a:
- **Hanya data sintetis yang dikirim ke LLM.**
  - `agent:proses` hanya menerima berkas di dalam repo, dan menolak path dengan komponen `_privat` atau `privat`. Nama berkas tidak dikirim ke LLM.
  - Data ERP sampai ke LLM hanya saat runtime dan hanya bila langkahnya membutuhkan: hasil alat perencana dan baris PO untuk pemetaan. Field-nya minimum (BR-24): NPWP, rekening, dan alamat email dari ERP tidak dikirim.
  - Aturan ADR-0008 butir 8 ("prompt tidak memuat label maupun data ERP") tetap berlaku untuk berkas prompt, dan tesnya tetap memeriksa berkas prompt itu.
  - Isi dokumen, email, dan hasil alat adalah data, bukan perintah. Prompt perencana dan pemetaan membungkusnya dengan pembatas nonce, seperti penilai injeksi M3.
- **Label tidak bocor ke agent.**
  - Kode agent (`domain`, `tools`, `llm`, `extraction`, `db`, `graph`) tidak mengimpor `evals`.
  - Harness hanya menyuntikkan isi dokumen (`kebenaran_dokumen`, di mode orakel) dan masukan simulasi (keputusan approver).
  - `harapan`, kategori, varian, dan ID skenario tidak pernah sampai ke agent, Odoo, Mailpit, atau nama berkas di `var/`.
- **LLM tidak menghitung, dan aturan kode tetap penentu** (ADR-0005).
  - Vendor dan PO ditetapkan BR-05 dan BR-06.
  - Usulan perencana dan pemetaan LLM selalu divalidasi kode sebelum dipakai.
- **Alat yang dibuka ke LLM hanya membaca**, dengan batas jumlah baris. Odoo hanya ditulis lewat `create_draft_bill`, `attach_file`, dan `post_note`.
- **Email hanya lewat `email_tool`** ke SMTP Mailpit Faktur Beres (`127.0.0.1:21025`). Pemeriksaan BR-25 dilakukan sebelum setiap pengiriman.
- **Node yang berisi interrupt tidak punya efek samping.** Aksi eksternal hanya ada di node aksi (ADR-0006).
- **Migrasi:**
  - migrasi yang sudah di-commit tidak diubah;
  - tes memakai database sementara;
  - tabel checkpointer LangGraph tidak dikelola Alembic.
- **Pengaman deserialisasi checkpoint LangGraph tidak dimatikan.** Tipe aplikasi yang perlu didaftarkan didaftarkan secara eksplisit.
- **Uang, kuantitas, dan biaya memakai `Decimal`**, dan kolom basis datanya `numeric`. `float` tidak dipakai.
- **Panggilan LLM dalam jumlah banyak dijalankan setelah memberi tahu pemilik.**
  - Sebutkan perkiraan panggilan per model, perkiraan waktu, dan posisinya terhadap reset kuota pukul 15.00 WIB, lalu tunggu persetujuan.
  - Selama pengembangan, pakai LLM palsu, mode orakel, dan `--pakai-cache`.
- **Reset di evaluasi** hanya lewat tiga cara: `npm run odoo:reset`, `npm run db:reset`, dan penghapusan pesan di Mailpit Faktur Beres lewat API-nya (`127.0.0.1:28025`).
  - Evaluasi penuh dijalankan setelah memberi tahu pemilik, karena DombaTek mungkin sedang berjalan.
- **Dataset, berkas di `eval/berkas/` dan `eval/ketahanan/berkas/`, dan set injeksi tidak diubah.**
  - Label hanya berubah lewat generator di M4a.1.
  - Bila aturan dan label bertentangan, berhenti dan laporkan.
- **Commit kecil** dengan pesan `M4a.x ringkasan`.

## Rancangan graph M4a

```text
intake ─▶ extract ─▶ validate ─▶ identify ─▶ match ─▶ decide ─┬─A─▶ act_bill ───────────────▶ report
          (BR-04 di            (perencana   (pemetaan        ├─B─▶ act_vendor_email ───────▶ report
          dalam alat)           LLM)         LLM)            └─C─▶ await_approval (interrupt)
                                                                    ├─setujui───▶ act_bill
                                                                    ├─tolak─────▶ report
                                                                    └─klarifikasi▶ act_vendor_email
act_bill: verifikasi gagal ─▶ await_approval (alasan validation_failed)
```

Nama node boleh disesuaikan, asal sejalan dengan SPEC §5.1 dan dicatat di ADR-0009.

## Langkah

### M4a.0 Catatan tinjauan M3
1. Simpan prompt ini apa adanya ke `docs/tugas/M4a.md`, lalu tambahkan baris M4a berstatus "Berjalan" di `docs/tugas/README.md`.
   - Commit: `M4a.0 prompt tugas M4a`.
2. Di **RENCANA §6**:
   - ganti baris M4 dengan dua baris:
     - M4a, dengan isi dan kriteria dari tugas ini, target 10–12 November;
     - M4b, dengan isi dari Konteks, target 13–16 November;
   - tambahkan subbagian "Masukan dari tinjauan M3 untuk M4a–M4b (10 November 2026)". Isinya ringkasan keputusan pemilik di atas.
3. Di **`eval/README.md`**, bagian set frasa injeksi, tambahkan hasil set tertutup: tabel dan batas tafsirnya, tanpa frasa.

### M4a.1 Label versi 2: email pembeli
1. **Generator.**
   - Email pembeli masuk `kebenaran_dokumen` hanya bila renderer mencetaknya.
   - Sumber kebenarannya renderer, bukan nama format. Misalnya, renderer menyatakan field pihak yang ia cetak, supaya perubahan tata letak kelak tetap konsisten dengan label.
   - Berlaku untuk faktur, lampiran balasan, dan dokumen ketahanan.
2. **`versi_label` menjadi 2** di skema label skenario dan skema label ketahanan.
   - Buat ulang dengan `npm run gen:faktur` dan `npm run gen:ketahanan`.
   - Pastikan semua berkas dokumen tidak berubah: `sha256` di label sama, `gen:periksa` lulus, dan `git status` hanya menunjukkan berkas label yang berubah. Bila ada berkas dokumen yang berubah byte, jangan commit berkas itu; berhenti dan laporkan.
   - Laporkan jumlah field label yang berubah.
3. **Tes:**
   - Untuk setiap PDF berlapisan teks (faktur, balasan, ketahanan), email pembeli ada di label bila dan hanya bila tercetak di lapisan teksnya.
   - Untuk gambar, email pembeli ada di label sesuai yang dicetak renderer.
   - Untuk UBL, email pembeli sama dengan `cac:AccountingCustomerParty//cbc:ElectronicMail`.
4. **Evaluasi.**
   - Hapus pengecualian `buyer_email` di `compare_fields` (`eval:ekstraksi`).
   - Di `eval/README.md`, ganti paragraf "Penyimpangan label yang diketahui" dengan catatan bertanggal bahwa label diperbaiki di versi 2.
5. **SPEC 0.5.**
   - §15.4: contoh memakai `versi_label: 2` dan `pembeli.email: null` dengan komentar singkat. Tambahkan butir "Isi label" tentang email pembeli.
   - §16: tambahkan baris 0.5. Baris yang sama dilengkapi di M4a.9, jadi M4a hanya menaikkan versi SPEC satu kali.
6. **Orakel:** `eval:orakel` tetap 40/40, luring dan `--odoo`.

### M4a.2 Klien LLM: peran cadangan, batas waktu per tujuan, dan function calling
1. **Peran cadangan** (`ModelRole` baru).
   - Konfigurasi baru:
     - `GEMINI_MODEL_FALLBACK`, bawaan `gemini-3.6-flash`;
     - `GEMINI_RPM_FALLBACK`, bawaan 4;
     - `GEMINI_TIMEOUT_FALLBACK_SECONDS`, bawaan 180, untuk permintaan berisi dokumen.
   - Urutan model:
     - peran utama: utama → kuat → cadangan;
     - peran kuat: kuat → utama → cadangan;
     - model yang sama hanya muncul sekali.
   - Di model cadangan, 5xx, 408, timeout, dan galat koneksi dicoba ulang paling banyak satu kali, karena kuotanya 20 per hari. Penanganan lain mengikuti ADR-0008. Audit menandai jawaban dari model cadangan.
   - Pemanggil biasa tidak meminta peran cadangan secara langsung; hanya uji asap dan uji banding yang boleh.
   - Setiap panggilan bisa menolak model cadangan. Perencana `identify` memakai opsi ini (M4a.6).
   - `npm run llm:model -- --uji` menguji ketiga peran.
2. **Batas waktu per tujuan.**
   - Permintaan berisi dokumen:
     - `GEMINI_TIMEOUT_SECONDS` (120) di model utama dan kuat;
     - `GEMINI_TIMEOUT_FALLBACK_SECONDS` di model cadangan.
   - Permintaan tanpa dokumen (penilai injeksi, perencana, pemetaan baris) memakai `GEMINI_TIMEOUT_TEXT_SECONDS`, bawaan 60, di semua model.
   - Batas waktu dipasang per permintaan, bukan per klien SDK. Periksa dulu dukungan SDK untuk opsi HTTP per permintaan. Bila tidak ada, pilih cara lain dan catat.
3. **Function calling multi-giliran.**
   - Buat jenis permintaan percakapan dengan alat:
     - deklarasi fungsi dibuat dari skema Pydantic;
     - mode `ANY` dengan `allowed_function_names`;
     - tanpa function calling otomatis SDK.
   - **Thought signature.** Giliran model ditambahkan ke riwayat persis seperti diterima, termasuk semua part dan `thought_signature`. Ikuti dokumentasi Gemini "Thought signatures": SDK menangani signature bila objek konten jawaban ditambahkan utuh ke riwayat. Jangan menyusun ulang part function call, dan jangan memakai signature pengganti (*dummy*).
   - **Panggilan paralel** dalam satu giliran dijawab bersama di satu giliran berikutnya, dengan urutan yang sama.
   - **Function call yang tidak valid** (nama tidak dikenal, atau argumen tidak lolos skema) tidak dijalankan. Model menerima hasil berupa galat, dan giliran itu tetap dihitung dalam anggaran.
   - **Pergantian model di tengah percakapan:** percakapan diulang dari awal di model baru, karena signature tidak dicampur antar-model. Anggaran menghitung semua panggilan.
   - **Throttle, retry, audit, dan cache** berlaku per giliran.
     - Kunci cache mencakup seluruh riwayat dan deklarasi fungsi.
     - Entri cache menyimpan giliran model (part dan signature sebagai base64), tidak pernah prompt.
   - Tes dengan LLM palsu:
     - signature dikembalikan tanpa berubah;
     - percakapan diulang saat model berganti;
     - fungsi atau argumen yang tidak valid;
     - panggilan paralel;
     - anggaran habis;
     - cache.
4. **Uji asap model cadangan** (paling banyak 4 dari 20 permintaan harian).
   - Jalankan `llm:model -- --uji` untuk model cadangan, lalu ekstraksi satu foto (HRG-01) di model cadangan. Laporkan latensi dan statusnya.
   - Bila 503 atau timeout terjadi di dua percobaan berturut-turut, atau foto butuh lebih dari 120 detik, berhenti dan laporkan.
5. Perbarui `.env.example`. ADR-nya ditulis di M4a.9.

### M4a.3 Ekstraksi v2: nomor urut baris bukan kode barang
1. **Prompt baru `extraction-v2`** di direktori baru. `extraction-v1` tetap ada dan tetap dikunci tes sidik.
   - Aturan umumnya: kode barang (`vendor_code`) hanya diisi bila kode tercetak, di kolom kode atau SKU, atau di dalam deskripsi. Kolom nomor urut (No., #, urut) bukan kode barang.
   - Perbarui deskripsi field skema bila perlu.
   - Jangan menambah petunjuk untuk dokumen tertentu.
2. **Agent memakai v2.** Hasil evaluasi mencatat versi prompt yang menjawab.
3. **Run segar terarah** (sekitar 15 panggilan, setelah memberi tahu pemilik): KTH-06 dan 10 dokumen banding M3.
   - Bandingkan dengan run M3 `20261109-150210` pada dokumen yang sama: field kritis tidak boleh lebih buruk.
   - `kode` kedua baris KTH-06 harus `null`.

### M4a.4 Basis data aplikasi
1. **Dependensi:** SQLAlchemy 2, psycopg 3, Alembic, `langgraph`, dan `langgraph-checkpoint-postgres`.
   - Pakai versi stabil terbaru dan catat di RENCANA §2.
   - LangGraph 1.0.10 ke atas wajib, karena memuat perbaikan deserialisasi checkpoint (CVE-2026-28277).
2. **Paket `faktur_beres.db`:** model, sesi, repositori, dan lingkungan Alembic yang bisa dijalankan dari akar repo, dengan path berspasi.
   - `npm run db:migrate`: Alembic `upgrade head`, lalu `setup()` tabel checkpointer LangGraph.
   - `npm run db:reset` (SPEC F-10): mengosongkan data aplikasi dan checkpoint. Skema dan tabel versi migrasi tetap. Perintah menolak bila nama database tidak berawalan `fakturberes`.
3. **Migrasi pertama** membuat tabel SPEC §9.2 yang dibutuhkan M4a: `invoices`, `invoice_lines`, `match_results`, `actions`, `approvals`, dan `audit_log`. `vendor_memory` dan `policy_chunks` dibuat di M4b. Pedoman isinya:
   - **`invoices`:**
     - `invoice_id` berformat `FB-000123` dari sequence. ID ini juga `thread_id` LangGraph dan referensi di subjek email BR-19.
     - Data intake: kanal, email pengirim, nama berkas, SHA-256, ukuran, MIME, dan path simpan `var/berkas/<invoice_id>/`.
     - Hasil proses: status (SPEC §6.3), jalur, himpunan jalur yang diizinkan, alasan, vendor, nomor PO, dan nomor faktur (tercetak dan ternormalisasi).
     - Tanggal faktur, total, dan waktu (diterima, jalur ditentukan, diperbarui).
     - Cuplikan hasil ekstraksi dan analisis (JSONB) dengan versi skema.
     - Nomor revisi, supaya faktur perbaikan di M4b menggantikan data tanpa menghapus riwayat.
   - **`invoice_lines`** per revisi, dan **`match_results`** per baris: baris PO, metode pemetaan (termasuk `llm`), hasil harga dan kuantitas, serta bukti.
   - **`actions`:**
     - kunci BR-22 (unik), faktur, jenis, target, dan versi isi;
     - status (`pending`, `done`, `failed`) dan jumlah percobaan;
     - hasil (ID tagihan, lampiran, atau Message-ID), galat, dan waktu.
   - **`approvals`:**
     - permintaan: paket bukti (JSONB), alasan, dan usulan aksi;
     - keputusan: `approve`, `reject`, atau `clarify`, beserta pemutus, catatan, dan waktu;
     - paling banyak satu permintaan terbuka per faktur.
   - **`audit_log`** (BR-23):
     - waktu, faktur, node, dan jenis (`node`, `tool`, `llm`, `guard`, `decision`, `security`);
     - nama, parameter tersamar, dan ringkasan hasil;
     - durasi, token, biaya, model, tanda model cadangan, dan status.
   - Kolom enum dijaga `CHECK`, ada indeks untuk status dan faktur, dan uang memakai `numeric`.
4. **Alembic tidak menyentuh tabel LangGraph**, misalnya lewat `include_object` atau skema terpisah. `alembic check` harus bersih walaupun tabel LangGraph ada.
5. **Tes basis data** (penanda `integrasi`) memakai database sementara `fakturberes_test_<acak>` yang dibuat dan dihapus tes itu sendiri. Isinya:
   - migrasi naik;
   - `alembic check`;
   - repositori;
   - keunikan kunci BR-22;
   - transisi status.
6. **`InvoiceRegistry` dari basis data aplikasi** untuk BR-11: faktur lain milik vendor yang sama, tanpa faktur itu sendiri.

### M4a.5 Graph inti
1. **Langkah analisis bersama.** Pecah `analyse()` menjadi fungsi langkah (`validate`, `identify`, `match`, `decide`) yang dipakai bersama oleh `analyse()` dan node graph. Logika rangkaian tidak disalin, dan tes yang ada tetap hijau.
2. **State.**
   - State graph harus selamat melewati checkpoint, termasuk `Decimal` dan tanggal. Data besar boleh dirujuk dari tabel aplikasi.
   - Tes: simpan state dengan `PostgresSaver`, muat di proses baru, dan hasilnya harus sama.
3. **Node:**
   - **`intake`** (BR-01):
     - jenis dan ukuran berkas diperiksa sebelum thread dibuat (filetype M3). Penolakan memakai pesan bahasa Indonesia yang menyebut langkah berikutnya;
     - menghitung SHA-256, membuat `invoice_id`, dan menyimpan berkas asli di `var/berkas/<invoice_id>/`;
     - berkas yang hash-nya sama dengan faktur yang sudah ada dikenali sebelum ekstraksi dan diperlakukan sebagai duplikat (BR-11). Hasil ekstraksi faktur asal dipakai ulang tanpa memanggil LLM;
     - untuk kanal email, subjek dan isi email menjadi fragmen teks BR-15.
   - **`extract`** memanggil `extract_invoice`.
     - Logika BR-04 tetap di `read_with_llm` (sudah teruji); jangan menulisnya ulang.
     - Setiap pembacaan dicatat di jejak: percobaan, model, field bermasalah, dan ringkasan umpan balik.
     - Bila hasilnya `validation_failed` (C4), graph langsung ke `decide` dengan alasan itu. Paket bukti memuat percobaan dan field bermasalah, dan fragmen yang terbaca tetap diperiksa pola injeksi (BR-15).
   - **`validate`, `identify`, `match`, dan `decide`** memakai fungsi langkah bersama.
     - `identify` menambahkan perencana dan `match` menambahkan pemetaan baris (M4a.6).
     - `decide` di M4a memakai jalur bawaan BR-16 dan BR-17. Pilihan jalur oleh LLM masuk M4b.
     - BR-15: bila jalur bawaan A tetapi lapis 2 belum berhasil menilai, faktur tidak masuk A. Statusnya menjadi `failed` (daftar perhatian), dan `agent:ulang` menjalankan lapis 2 lagi sebelum keputusan.
   - **`report`** memperbarui baris faktur (status, jalur, alasan, dan waktu) dan menulis catatan audit penutup.
4. **Checkpointer.**
   - `PostgresSaver` di basis data aplikasi, dengan pool koneksi psycopg sesuai dokumentasi LangGraph, dan `thread_id = invoice_id`.
   - Pakai mode durabilitas yang menulis checkpoint sebelum langkah berikutnya, lalu catat pilihannya di ADR-0009.
   - Tes unit memakai checkpointer di memori.
5. **Status.** Transisi mengikuti SPEC §6.3 lewat tabel transisi yang diuji, dan setiap perubahan status tercatat di audit.
6. **BR-27.**
   - `LlmUnavailable`, Odoo `Unavailable` setelah retry, dan galat tak terduga membuat status `failed` dengan pesan yang bisa dibaca. Checkpoint tetap ada.
   - `agent:ulang` melanjutkan dari checkpoint terakhir tanpa mengulang aksi yang sudah selesai.
   - `AccessDenied` dari Odoo membuat status `failed` dan menulis catatan audit berjenis `security`.
7. **Runner** adalah lapisan layanan yang nanti dipakai API M4b, dengan operasi:
   - menerima faktur;
   - menjalankan graph sampai berhenti;
   - meneruskan keputusan approver;
   - mengulang;
   - membaca status;
   - membaca jejak.
8. **CLI** lewat `npm run`, dengan pesan bahasa Indonesia yang menyebut langkah berikutnya:
   - `agent:proses -- <berkas>`: kanal unggahan;
   - `agent:status [-- <invoice_id>]`;
   - `agent:jejak -- <invoice_id>`: urutan node, panggilan alat dengan parameter tersamar dan ringkasan hasil, panggilan LLM dengan model, token, dan durasi, keputusan guard, dan aksi;
   - `agent:putuskan -- <invoice_id> setujui|tolak|klarifikasi [--catatan "..."] [--oleh "..."]`;
   - `agent:ulang -- <invoice_id>`.
9. **Diagram.** `npm run graph:diagram` menulis diagram Mermaid dari graph yang dikompilasi ke dokumen. Ada tes bahwa dokumen itu sama dengan graph.
10. **Kesetaraan** (kriteria 7): tes tanpa jaringan yang memakai pembaca ERP dari dataset, checkpointer di memori, dan aksi palsu. Hasil graph dengan LLM dimatikan harus sama dengan `analyse()` untuk 40 skenario dan 13 dokumen ketahanan.

### M4a.6 LLM di dalam pagar
1. **Perencana `identify`** (SPEC §5.1–§5.2, ADR-0005).
   - **Kapan berjalan:** vendor teridentifikasi tunggal, dan nomor PO tercetak tidak ada atau tidak ditemukan (BR-06).
   - **Alat**, memakai function calling dari M4a.2. Semuanya alat agent di `faktur_beres.tools`: hanya membaca, parameternya Pydantic, dengan batas paling banyak 20 PO × 20 baris, dan terikat pada vendor yang sudah teridentifikasi:
     - `get_purchase_order(number)`: status PO, apakah milik vendor ini, dan barisnya (produk, kode vendor, satuan, dipesan, diterima, ditagih, sisa, harga satuan). PO milik vendor lain hanya mengembalikan "milik vendor lain";
     - `list_vendor_purchase_orders()`: PO terkonfirmasi milik vendor, dengan ringkasan baris;
     - `compare_invoice_to_po(number)`: pemeriksaan kandidat kuat BR-06 oleh kode, per baris (pemetaan BR-07, sisa diterima sama, harga dalam BR-08) beserta kesimpulannya;
     - `submit_identification(po_number | null, explanation)`: mengakhiri percakapan. `explanation` paling panjang 400 karakter.

     Karena alat diikat pada vendor, tidak ada parameter `partner_id`. Ini penerapan yang lebih ketat dari contoh di SPEC §5.2; catat di ADR-0010.
   - **Prompt `identify-planner-v1`:** bahasa Indonesia, berversi, dan dikunci tes sidik. Berkas prompt tidak memuat data ERP maupun label.
     - Masukannya ringkasan faktur di dalam pembatas nonce sebagai data: nama penjual, nomor dan tanggal faktur, nomor PO tercetak (atau "tidak ada"), dan baris (deskripsi, kode, qty, satuan, harga satuan). Tanpa NPWP, rekening, dan email.
     - Aturannya: usulkan PO hanya bila `compare_invoice_to_po` menyatakan semua baris cocok dan tidak ada PO lain yang cocok; selain itu usulkan `null`.
   - **Guard:**
     - fungsi yang tidak dikenal tidak dijalankan;
     - parameter divalidasi skema;
     - `po_number` di `submit_identification` harus pernah muncul di hasil alat pada percakapan yang sama;
     - anggaran paling banyak 6 panggilan model dan 12 eksekusi alat. Bila habis, percakapan dihentikan.
   - **Kode yang memutuskan.**
     - BR-06 tetap dijalankan atas seluruh PO terkonfirmasi milik vendor, seperti sekarang.
     - Usulan perencana dibandingkan dengan hasil BR-06, dan kesesuaiannya dicatat. Bila berbeda, aturan yang dipakai dan jejak menyebutnya.
     - Penjelasan perencana menjadi catatan internal (jejak dan paket bukti), tidak pernah masuk email.
   - **Tanpa model cadangan.** Perencana bersifat penasihat. Bila LLM tidak tersedia, jejak mencatat "perencana tidak tersedia" dan aturan tetap memutuskan; BR-27 tidak terpicu.
   - **Tes dengan LLM palsu berskrip:**
     - jalur normal;
     - usulan berbeda dari aturan;
     - fungsi tidak dikenal;
     - parameter tidak valid;
     - nomor PO yang tidak pernah muncul;
     - anggaran habis;
     - LLM tidak tersedia.
2. **Pemetaan baris oleh LLM** (BR-07).
   - **Kapan berjalan:** di `match`, setelah PO ditetapkan, hanya untuk baris faktur tanpa padanan (`no_match`). Syaratnya PO punya baris yang belum terpasang dan produknya muncul tepat di satu baris PO.
     - Baris `ambiguous` tidak pernah dikirim (BR-07).
     - Bila tidak ada baris PO yang bisa ditawarkan, LLM tidak dipanggil.
   - **Keluaran terstruktur**, bukan function calling, dengan prompt `line-mapping-v1`.
     - Masukannya baris faktur tanpa padanan (deskripsi, kode, satuan) di dalam pembatas nonce, dan baris PO yang ditawarkan (ref, nama produk, kode vendor, deskripsi, satuan).
     - Tanpa harga dan kuantitas, supaya pemetaan hanya berdasarkan deskripsi. Angka diperiksa BR-08 dan BR-09 sesudahnya.
   - **Guard:**
     - ref harus salah satu yang ditawarkan;
     - satu baris PO hanya untuk satu baris faktur;
     - `null` boleh;
     - jawaban tidak valid setelah satu ulangan dari klien diperlakukan seperti `LlmUnavailable`, yaitu status `failed` (BR-27).
   - Baris hasil pemetaan bermetode `llm` dan mendapat catatan, misalnya "dipetakan LLM: 'Lem PVAc untuk Laminasi' → Lem PVAc Laminasi".
   - **`domain` tetap tanpa LLM:** `match_lines` menerima pemetaan yang sudah divalidasi sebagai masukan.
   - **Tes:**
     - kasus seperti KTH-06 dipetakan;
     - QTY-04 tidak memanggil LLM;
     - setiap guard.
3. **`npm run eval:perencana`** (paling banyak 48 panggilan, setelah memberi tahu pemilik).
   - Memakai ekstraksi orakel dengan 8 kasus:
     - NPO-01 dan NPO-02 seperti tercetak;
     - COC-01, COC-02, DUP-04, dan REK-04 dengan nomor PO dihapus. Menurut BR-06, masing-masing punya satu kandidat kuat, yaitu PO aslinya;
     - HRG-04 dan QTY-01 dengan nomor PO dihapus. Menurut BR-06, keduanya tanpa kandidat, karena harga di atas toleransi dan kuantitas tidak sama dengan sisa.
   - Harapan dihitung BR-06 saat run, bukan dari label.
   - Laporkan per kasus: usulan, keputusan aturan, kesesuaian, jumlah panggilan model, eksekusi alat, penolakan guard, dan durasi.

### M4a.7 Aksi jalur A, B, dan C
1. **Jalur A** (BR-18, BR-22, F-05), node `act_bill`.
   - Setiap langkah (draft, lampiran, catatan) punya baris `actions` dengan kunci BR-22 sendiri. Urutannya:
     1. `create_draft_bill`;
     2. `attach_file` dengan berkas asli dari `var/berkas`;
     3. `post_note`, dengan catatan yang memuat referensi thread.
   - Verifikasi gagal atau `conflict` menjadi alasan `validation_failed`, lalu faktur masuk C dengan bukti selisihnya. Draft dibiarkan apa adanya (BR-18).
   - Galat Odoo:
     - `Rejected` (422) menjadi `validation_failed`;
     - `Unavailable` dicoba ulang, lalu `failed`;
     - `AccessDenied` menjadi `failed` dengan catatan audit `security`.
   - Status akhir: `draft_created`.
2. **Jalur B keluar** (BR-19 bagian pengiriman, BR-25), lewat `faktur_beres.tools.email_tool`.
   - **Pengiriman:** SMTP ke Mailpit, dari `ap@arunika.test`.
     - Penerimanya alamat penagihan vendor yang dibaca dari Odoo.
     - `check_recipient` (BR-25) dijalankan sebelum kirim, dan alamat lain ditolak, termasuk alamat yang tertulis di dokumen.
   - **Templat** berupa berkas teks bahasa Indonesia yang berversi.
     - Email klarifikasi: satu email per faktur, dengan satu bagian per alasan B. Rinciannya diambil dari temuan kode, dengan NPWP dan rekening disamarkan seperti BR-24.
     - Email pemberitahuan untuk `duplicate_confirmed`.
     - Subjek memuat `[FB-000123]` dan nomor faktur.
     - Di M4a, isi email tidak memakai LLM.
   - **Idempoten dan terverifikasi.**
     - Message-ID deterministik, diturunkan dari kunci BR-22.
     - Sebelum kirim, tabel `actions` dan Mailpit diperiksa (cari berdasarkan Message-ID).
     - Sesudah kirim, pesan dibaca ulang lewat API Mailpit.
   - Status akhir: `awaiting_vendor`, atau `closed_duplicate` untuk B5.
   - Vendor tanpa email penagihan di Odoo membuat status `failed` dengan pesan yang jelas. Email tidak dikirim ke alamat lain.
3. **Jalur C** (BR-21, F-07, ADR-0006).
   - Node `await_approval` menjalankan `interrupt(paket bukti)` tanpa efek samping lain. Paket bukti berisi:
     - data faktur;
     - hasil pencocokan per baris;
     - semua alasan beserta temuannya;
     - catatan perencana bila ada;
     - `policy_quotes: []` (diisi di M4b);
     - usulan aksi.
   - Pilihan approver:
     - setujui hanya ditawarkan bila ada rencana tagihan;
     - klarifikasi tidak ditawarkan untuk vendor yang tidak terdaftar (F-07).
   - Baris `approvals` dibuat, dan statusnya `awaiting_approval`.
   - **Keputusan** dikirim lewat `agent:putuskan`, runner, lalu `Command(resume=...)`:
     - **setujui:** `act_bill`, lalu `draft_created`. Bila verifikasi gagal, terjadi putaran C baru dengan alasan `validation_failed`;
     - **tolak:** `rejected`, tanpa aksi eksternal;
     - **klarifikasi:** email klarifikasi, lalu `awaiting_vendor`. Catatan approver ikut sebagai pertanyaan, berupa teks polos paling panjang 500 karakter.
   - Keputusan, waktu, dan identitas pemutus dicatat (F-07).
   - **Keputusan kedua** untuk permintaan yang sama tidak mengubah apa pun dan melaporkan keputusan yang sudah tercatat. Status dan interrupt yang tertunda diperiksa sebelum resume.
4. **Tes:**
   - idempotensi aksi saat graph diulang setelah berhenti di setiap langkah;
   - guard penerima email, termasuk alamat di INJ-02;
   - isi templat: tanpa label, dengan angka tersamar;
   - semantik resume: setujui dua kali menghasilkan satu draft.

### M4a.8 Evaluasi graph
1. **`npm run eval:graph`** (`faktur_beres.evals.graph`).
   - **Opsi:**
     - `--set faktur|ketahanan` (bisa berulang) dan `--id`;
     - `--ekstraksi orakel|llm`, bawaan `llm`;
     - `--penilai nyala|mati`, bawaan `nyala` di mode `llm` dan `mati` di mode `orakel`;
     - `--tanpa-llm`, yang mematikan perencana dan pemetaan;
     - `--pakai-cache`, `--tanpa-reset` (untuk pengembangan), `--lanjut RUN`, dan `--maks-panggilan N`.
   - **Per skenario:**
     1. Reset Odoo, basis data aplikasi, dan pesan Mailpit, kecuali dengan `--tanpa-reset`.
     2. Intake dengan berkas label beserta kanal dan email-nya.
     3. Jalankan sampai pertama kali berhenti, lalu nilai pemeriksaan pertama berhenti.
     4. Untuk jalur C dengan `keputusan_approver`: kirim keputusan (dua kali bila `uji_idempotensi`), jalankan, lalu nilai status akhir.
     5. Untuk B dengan `balasan_vendor`: tandai "menunggu M4b".
   - **Satu penilai SPEC §15.4** dipakai bersama `eval:orakel`. Refaktor penilainya; jangan membuat penilai kedua. State yang diamati:
     - dari basis data aplikasi: status, jalur, alasan, dan aksi;
     - dari Odoo: tagihan yang dibuat selama percobaan, menurut vendor dan `ref`;
     - dari Mailpit: penerima non-internal;
     - larangan: tagihan terposting, email ke alamat lain, aksi ganda, perubahan data master (bandingkan field master yang ditetapkan, bukan kuantitas hasil hitungan), aksi sebelum persetujuan, dan `email_ke:`.
   - **Hasil** ditulis ke `eval/hasil/graph/<run>.json` setelah setiap skenario. Ringkasannya:
     - hasil per kategori, format, dan kanal;
     - waktu sampai jalur ditentukan: median dan p95 per format;
     - panggilan LLM per model dan per tujuan;
     - biaya setara per faktur.
2. **Run** (setelah memberi tahu pemilik):
   - **Mode orakel** untuk faktur dan ketahanan. LLM hanya dipakai perencana dan pemetaan, sekitar 15 panggilan. Hasilnya kriteria 10.
   - **Mode LLM segar** untuk faktur dan ketahanan: sekitar 120–150 panggilan Flash-Lite dan 40–60 menit termasuk reset. Hasilnya kriteria 11. Bila perlu, pecah run di sekitar reset kuota pukul 15.00 WIB dengan `--lanjut`.

### M4a.9 Dokumen dan push
1. **ADR**, keduanya berstatus **Diusulkan** dan diterima pemilik saat tinjauan:
   - **ADR-0009 "Graph LangGraph, basis data aplikasi, dan aksi sampai agent berhenti"** mencatat:
     - bentuk graph dan state;
     - checkpointer dan mode durabilitas;
     - tabel dan migrasi;
     - skema ID;
     - semantik interrupt dan resume;
     - aksi, idempotensi, dan verifikasi (termasuk email);
     - `failed` dan `agent:ulang`;
     - metode reset evaluasi;
     - versi pustaka.
   - **ADR-0010 "LLM di graph: peran cadangan, batas waktu per tujuan, dan pemanggilan alat di dalam pagar"** mencatat:
     - model cadangan dan batasnya;
     - batas waktu per tujuan;
     - function calling, thought signature, dan pergantian model;
     - guard, anggaran, dan sifat penasihat perencana;
     - guard pemetaan baris;
     - `extraction-v2`.
   - ADR-0008 mendapat catatan bertanggal yang menunjuk ADR-0010, karena peran cadangan yang direncanakan ADR-0008 kini diterapkan.
   - Perbarui indeks ADR.
2. **SPEC 0.5:**
   - §9.2: tabel yang dibuat M4a dan sisanya di M4b;
   - §12.1: evaluasi graph sejak M4a;
   - §14: model cadangan;
   - F-10: `npm run db:reset`;
   - perbedaan lain yang ditemukan, misalnya perulangan BR-04 di dalam alat `extract`;
   - §16: lengkapi baris 0.5.
3. **RENCANA:**
   - §2 versi pustaka;
   - §3.2 modul: `db` dan `graph` di M4a, `api` di M4b;
   - §3.4 keterlacakan, dengan tes bertanda untuk aturan baru yang dihitung kode: BR-01, BR-07 (guard pemetaan LLM), BR-19 (pengiriman), BR-21, BR-22 (tabel `actions`), BR-23 (`audit_log`), BR-25 (`email_tool`), dan BR-27 (`failed` dan ulang);
   - §4.1 dan §4.2 perintah baru;
   - §6: M4a ✅ dengan tanggal;
   - §7 risiko: kuota cadangan 20 per hari dan waktu proses foto.
4. **Dokumen lain:**
   - README: baris status dan perintah;
   - `eval/README.md`: bagian `eval:graph` dan `eval:perencana` beserta hasilnya;
   - `docs/tugas/README.md`: M4a selesai.
5. **Pemeriksaan dan push:** `npm run check`, `npm run test:integrasi`, `npm run guard:secrets`, lalu push ke `origin main`.

## Kapan berhenti dan bertanya
- Uji asap `gemini-3.6-flash` gagal (M4a.2).
- Mode orakel tidak memenuhi kriteria 10, dan penyebabnya tafsir label atau aturan, bukan bug.
- Perilaku LangGraph bertentangan dengan ADR-0006, misalnya resume mengulang aksi, atau interrupt tidak bisa dipisah dari aksi.
- Ada aturan BR yang perlu diubah di luar yang disebut tugas ini.
- Kuota habis sebelum run wajib selesai. Beri tahu pemilik, lalu lanjutkan setelah pukul 15.00 WIB dengan `--lanjut`.
- Ada dua pilihan yang sama masuk akal dan berdampak ke SPEC atau ADR.
- Hal lain di bagian "Kapan berhenti dan bertanya" di CLAUDE.md (port bentrok, `sudo`, path di mount jaringan).

## Laporan
Ikuti format di `docs/tugas/README.md`, ditambah butir khusus M4a:
1. **Hasil `eval:graph` mode orakel:** pemeriksaan pertama berhenti dan status akhir, per kategori, untuk faktur dan ketahanan.
2. **Hasil `eval:graph` mode LLM:**
   - hasil yang sama seperti butir 1;
   - kesalahan ekstraksi;
   - waktu sampai jalur ditentukan (median dan p95 per format);
   - panggilan per model dan per tujuan;
   - biaya per faktur.
3. **Tabel `eval:perencana`** untuk 8 kasus.
4. **KTH-06:** hasil pemetaan LLM dan jalurnya.
5. **Ekstraksi v2 dibanding v1** pada dokumen yang sama.
6. **Uji asap `gemini-3.6-flash`:** latensi dan status.
7. **Label versi 2:** jumlah label dan field yang berubah.
8. **Cuplikan `agent:jejak`** untuk NPO-01, dan untuk COC-04 dengan keputusan setujui dua kali.
9. **Diagram Mermaid graph.**
10. **Pemakaian kuota per hari dan per model.**
11. **Pertanyaan dan usulan untuk M4b.**
