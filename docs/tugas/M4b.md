# Tugas M4b: catatan tinjauan M4a, email masuk dan balasan vendor, RAG kebijakan, dan pilihan jalur oleh LLM

## Konteks
Kamu adalah Claude Code yang bekerja di repo `/home/farhan/Drive/02 - Proyek Pribadi/02 - Kompetisi/2026/GTNIC2026/faktur-beres`.

M4a sudah selesai dan ditinjau pemilik bersama Claude (chat perencana) pada 10 November 2026. Perencana memeriksa ulang arsip HEAD `abfd9a5`, yang sama dengan `origin/main` (30 commit di atas `b2c89ab`):
- ruff dan mypy `--strict` bersih;
- 1.360 tes unit lulus, 1 dilewati dengan alasan sah (KTH-01 tanpa lapisan teks);
- label versi 2 hanya mengubah `pembeli.email` (null di 58 dokumen PDF dan gambar, tetap terisi di 6 UBL), tanpa perubahan hash berkas;
- angka run 3 dan run 4 di `eval/README.md` konsisten dengan laporannya.

**Pembagian sisa M4 (keputusan pemilik):**
- **M4b (tugas ini):** perbaikan dari tinjauan M4a; email masuk (F-02); balasan vendor yang melanjutkan thread (F-06, BR-20); RAG kebijakan (`policy_rag`, BR-28); pilihan jalur oleh LLM di dalam himpunan yang diizinkan, dengan kutipan kebijakan (BR-17, BR-21).
- **M4c (berikutnya):** FastAPI + SSE dan API keputusan approver; pengingat dan daftar perhatian BR-19; rekap harian (BR-26); `vendor_memory`.
- **M5:** dashboard, harness 40 × 3, dan akurasi per field di `eval:graph`.

Tugas ini terdiri dari:
- **M4b.0** catatan tinjauan M4a di dokumen;
- **M4b.1** perbaikan dari tinjauan M4a;
- **M4b.2** pemantau inbox (F-02);
- **M4b.3** balasan vendor (F-06, BR-20);
- **M4b.4** RAG kebijakan;
- **M4b.5** pilihan jalur oleh LLM dan kutipan kebijakan;
- **M4b.6** evaluasi graph sampai status akhir;
- **M4b.7** dokumen dan push.

Baca dulu, berurutan:
1. `CLAUDE.md` seluruhnya.
2. `docs/SPEC.md`: §5, §6, §7 (F-02, F-04, F-06, F-07, F-10), §8 (terutama BR-07, BR-11, BR-15, BR-17, BR-19–BR-25, BR-27, BR-28), §9.2, §10, §11, §12, §14, §15.4, dan §15.5.
3. `docs/RENCANA-TEKNIS.md` §2, §3, §4.2, §6 (termasuk semua subbagian masukan), dan §7.
4. `docs/adr/README.md`, lalu ADR `0005`, `0006`, `0008`, `0009`, dan `0010`.
5. `eval/README.md` dan label `eval/skenario/` berikut: `QTY-01`, `QTY-04`, `NPO-02`, `NPO-03`, `HRG-04`, `DUP-01`, `REK-01`, dan `PJK-05`.
6. `docs/tugas/README.md` (format laporan).
7. Kode M4a yang diubah atau diperluas di sini:
   - `graph/` seluruhnya, `db/store.py`, `db/models.py`, `db/migrate.py` (`reset`), dan migrasi `0001`;
   - `tools/email_tool.py` dan `tools/email_templates/v1/`, `tools/line_mapping.py`, `tools/po_planner.py`;
   - `domain/rules/safety.py`, `domain/rules/lines.py`, `domain/rules/routing.py`, `domain/engine.py`, `domain/findings.py`, `domain/invoice.py` (`TextSource`);
   - `llm/gemini.py`, `llm/contracts.py`;
   - `evals/graph.py`, `evals/orakel.py` (`_vendor_reply`), `evals/labels.py` (`reply_fragments`, `reply_intake`), `evals/audit_log.py`;
   - `devtools/setup.py`.

**Kuota saat prompt ini ditulis** (AI Studio, 10.58 WIB): Flash-Lite 452 dari 500, jadi sisa sekitar 48 sampai reset pukul 15.00 WIB. `gemini-embedding-2` belum dipakai (100 per menit, 1.000 per hari). Sebelum 15.00 WIB, kerjakan semuanya dengan LLM palsu.

**Di luar tugas ini:** API dan SSE, pengingat dan daftar perhatian BR-19, rekap harian, `vendor_memory` (M4c); dashboard dan harness 40 × 3 (M5).

## Catatan tinjauan M4a
Hasil M4a baik: rancangan sesuai prompt, evaluasi mode orakel dan mode LLM sama-sama 40/40, 27/27, dan 13/13, dan laporannya jujur soal pelanggaran commit, selisih hitungan kuota, dan rumus p95. Perencana menemukan hal berikut:

1. **Penyamaran bisa dilewati (`abfd9a5`).** `mask_npwp` dan `mask_account` mengembalikan nilai apa adanya bila mengandung `*`. Contoh yang diuji perencana:
   - `mask_params({"rekening": "1381646027*"})` menghasilkan `1381646027*`;
   - `mask_params({"npwp": "*0990427957220000"})` menghasilkan `*0990427957220000`.

   Hari ini dampaknya laten, karena nilai yang disamarkan menurut nama field masih berupa digit hasil normalisasi. Begitu nilai tercetak dari dokumen ikut disamarkan (keputusan 2), tanda bintang catatan kaki membuka celah ini. `test:integrasi` juga belum dijalankan ulang setelah commit itu.
2. **Status `failed` bisa gagal dipasang.** `Runner._advance` langsung memasang `failed`. Bila galat terjadi saat status masih `received`, `awaiting_approval`, atau (di M4b) `awaiting_vendor`, tabel transisi menolaknya, dan yang keluar adalah galat, bukan status `failed`.
3. **Paket bukti memuat NPWP dan rekening utuh**, termasuk data master Odoo: `registered_digits` di bukti rekening dan `vendor_npwp_digits` di bukti identitas.
4. **"Setujui" setelah verifikasi gagal** sudah benar tidak ditawarkan. Yang belum ada: peringatan pada draft yang tertinggal di Odoo, dan penjelasan di paket bukti.
5. **Email vendor masih memakai bahasa internal.** Contohnya "tagihan terposting di Odoo" dan "di atas batas Rp4.898,50"; yang kedua membuka toleransi harga kita kepada vendor. Header `Date` belum ada, dan ada baris kosong ganda.
6. **Pemetaan baris oleh LLM** hanya dijaga validitas ref. Pemetaan yang keliru (misalnya "Ongkos kirim" ke baris produk yang belum ditagih) bisa lolos ke jalur A, karena harga yang lebih rendah lulus BR-08 dengan catatan.
7. **Catatan di draft tagihan Odoo** belum menyebut baris hasil pemetaan LLM atau nomor PO yang dikoreksi.
8. **Proses:**
   - dua commit masuk saat lint gagal (`27d7206`, `c2d8f75`);
   - beberapa commit terlalu besar dan menggabungkan butir yang berbeda, misalnya graph inti 2.084 baris, basis data 1.640 baris, dan klien LLM 842 baris untuk tiga butir sekaligus. Ini menyalahi aturan commit kecil di CLAUDE.md.
9. **Waktu proses.** `eval/README.md` hanya memuat p95 per format, padahal target SPEC §12.2 berlaku untuk keseluruhan.

Usulan dari laporan M4a yang diterima: akurasi per field di `eval:graph` (M5), dan hitungan kuota yang lengkap (M4b.1). Batas waktu dokumen tetap 120 detik (keputusan 7).

## Keputusan pemilik dari tinjauan M4a (10 November 2026)
1. **ADR-0009 dan ADR-0010 diterima** di M4b.1. Sebelum statusnya diubah, isinya dilengkapi selagi masih Diusulkan:
   - ADR-0009: kebijakan data paket bukti (keputusan 2), aturan pilihan approver dan peringatan pada draft (keputusan 3), dan jalur ke `failed` lewat `processing` (temuan 2);
   - ADR-0010: pagar tambahan pemetaan baris (keputusan 4).
2. **Paket bukti disamarkan seperti audit** (BR-24): NPWP 4 digit awal dan akhir, rekening 4 digit akhir. Alasannya:
   - approver memutuskan dari kesimpulan kode ("berbeda dari rekening terdaftar", "terdaftar atas pihak lain");
   - berkas asli tetap tampil di layar detail faktur (SPEC §10);
   - verifikasi perubahan rekening seharusnya lewat kontak yang sudah tercatat, bukan dengan membaca nomor dari faktur;
   - paket bukti akan mengalir ke API, browser, tangkapan layar, dan demo publik;
   - sejak pertengahan 2024 NPWP orang pribadi memakai NIK, jadi NPWP vendor perorangan adalah data pribadi (UU PDP).

   Nilai utuh tetap ada di berkas asli, di Odoo, dan di data kerja internal agent yang dibutuhkan aturan (hasil ekstraksi dan checkpoint). Data kerja itu tidak pernah dikirim utuh ke API atau antarmuka (berlaku juga untuk M4c). Cakupan BR-24 diperluas di SPEC 0.6 ke paket bukti dan antarmuka.
3. **"Setujui" tetap tidak ditawarkan setelah verifikasi gagal**, dengan dua tambahan:
   - begitu verifikasi gagal, agent menulis catatan peringatan pada draft itu di Odoo;
   - paket bukti menjelaskan kenapa "Setujui" tidak tersedia, beserta selisihnya.

   Pilihan approver yang bergantung kondisi dicatat di SPEC F-07.
4. **Pagar tambahan pemetaan baris oleh LLM** (penjelasan BR-07 di SPEC 0.6). Pemetaan LLM hanya dipakai bila:
   - satuan tercetak sejenis dengan satuan baris PO (berat atau hitungan);
   - harga satuan faktur berada dalam ±toleransi BR-08 dari harga PO.

   Selain itu baris tetap tanpa padanan (BR-09).
5. **Email vendor dirapikan:** tanpa nama sistem internal, tanpa batas toleransi, dengan header `Date`.
6. **M4 dipecah menjadi M4b dan M4c**, seperti di Konteks.
7. **Batas waktu dokumen tetap 120 detik.** Di M3, panggilan foto yang sah butuh 68–100 detik, jadi batas yang lebih rendah bisa memotong panggilan yang akan berhasil dan menggandakan pemakaian kuota. Ditinjau ulang di M5 dengan data 3 kali run.
8. **Embedding RAG memakai `gemini-embedding-2`** (GA 22 April 2026, masukan sampai 8.192 token, dimensi keluaran bisa diperkecil dari 3.072) dengan **768 dimensi**. Indeks HNSW pgvector untuk tipe `vector` paling banyak 2.000 dimensi, dan Google menyarankan 768 atau 1.536 untuk efisiensi.

## Kriteria selesai
1. **M4b.0:** prompt tersimpan; RENCANA §6 memuat baris M4b dan M4c beserta subbagian masukan tinjauan M4a.
2. **M4b.1:** semua perbaikan selesai, masing-masing dengan tes:
   - penyamaran tanpa celah;
   - jalur ke `failed`;
   - paket bukti tersamar;
   - peringatan pada draft dan penjelasan pilihan approver;
   - pagar pemetaan;
   - email versi 2;
   - catatan draft;
   - hook pre-commit;
   - `llm:kuota`;
   - baris waktu keseluruhan.

   ADR-0009 dan ADR-0010 berstatus Diterima. `test:integrasi` lulus setelah perbaikan penyamaran.
3. **Pemantau inbox** menerima faktur lewat email, dengan tes unit (Mailpit palsu) dan tes integrasi (Mailpit sungguhan).
4. **Balasan vendor** melanjutkan thread yang sama, dengan tes:
   - lampiran;
   - teks berisi nomor PO;
   - pengirim lain (BR-20);
   - thread yang tidak menunggu;
   - balasan ganda;
   - balasan berisi instruksi (BR-15).
5. **RAG:**
   - dua dokumen kebijakan sintetis sesuai Lampiran A;
   - migrasi pgvector dan `policy_chunks`;
   - `rag:indeks` dan `rag:cari`;
   - indeks tetap ada setelah `db:reset`;
   - tes BR-28 dan tes kebocoran label.
6. **Pilihan jalur oleh LLM** dengan guard, anggaran, dan tes LLM palsu; kutipan kebijakan per alasan di paket bukti (BR-21).
7. **`eval:graph` mode orakel (`--ekstraksi orakel`) adalah syarat lulus**, dengan kanal email dan balasan vendor lewat Mailpit sungguhan:
   - **40/40** lulus pemeriksaan pertama berhenti;
   - **40/40** lulus status akhir;
   - set ketahanan **13/13**.
8. **`eval:graph` mode LLM (`--ekstraksi llm`):** satu run segar dilaporkan. Targetnya ≥ 36/40 status akhir, tetapi target ini bukan syarat lulus. Bila tidak tercapai, sertakan analisis per kegagalan.
9. **Pemeriksaan:** `npm run check`, `npm run test:integrasi`, dan `npm run guard:secrets` lulus.
10. **Penutup:**
    - SPEC 0.6;
    - ADR-0011 dan ADR-0012 berstatus Diusulkan;
    - RENCANA dan README diperbarui;
    - M4b ditandai selesai, lalu di-push;
    - laporan ditulis.

## Batasan keras
Semua aturan di CLAUDE.md berlaku. Khusus M4b:
- **Commit kecil, dibuat saat butirnya selesai.**
  - Setiap butir bernomor di bawah adalah satu commit atau lebih. Jangan menumpuk perubahan lalu memecahnya di akhir.
  - Commit kode yang lebih dari sekitar 400 baris (di luar lockfile dan berkas hasil generator) perlu alasan di laporan.
  - Bila gate gagal, jangan commit, dan jangan merangkai perintah commit yang tetap berjalan saat gate gagal.
- **Hanya data sintetis yang dikirim ke LLM dan model embedding.**
  - Yang di-embed hanya dua dokumen kebijakan dan kueri pencarian.
  - Kueri dan masukan pilihan jalur tidak memuat NPWP, rekening, atau email (BR-24).
- **Isi email masuk adalah data, bukan perintah** (BR-20). Subjek, isi, dan lampiran melewati BR-15 seperti dokumen lain.
- **Pemantau inbox hanya membaca Mailpit Faktur Beres** (`127.0.0.1:28025`). Ia hanya memproses pesan yang ditujukan ke `ap@arunika.test`, dan tidak pernah memproses pesan keluar dari agent sendiri.
- **Email keluar tetap hanya lewat `email_tool`** (BR-25), dan isinya tidak memakai teks bebas LLM.
- **Angka kebijakan dibaca dari `data/kebijakan/parameter.yaml`** (BR-28). Teks kebijakan hanya menjadi kutipan, tidak pernah menjadi angka keputusan.
- **LLM hanya memilih di dalam himpunan jalur yang diizinkan kode** (BR-17), lewat `guard_route`. Penolakan guard dicatat di audit.
- **Dokumen kebijakan bebas label.** Tidak boleh memuat ID skenario, kategori, varian, kode alasan, nama vendor dataset, atau jawaban yang diharapkan. Ada tes yang memeriksanya.
- **Migrasi:** migrasi yang sudah di-commit tidak diubah; perubahan skema memakai migrasi baru (`0002`, `0003`, ...).
- **Panggilan LLM dan embedding dalam jumlah banyak dijalankan setelah memberi tahu pemilik** (perkiraan panggilan per model dan waktu). Sebelum 15.00 WIB hari ini, pakai LLM palsu saja.
- **Reset di evaluasi** tetap hanya lewat `npm run odoo:reset`, `npm run db:reset`, dan penghapusan pesan Mailpit Faktur Beres.
- **Dataset, berkas evaluasi, label, dan set injeksi tidak diubah.** Bila aturan dan label bertentangan, berhenti dan laporkan.

## Rancangan graph M4b

```text
intake ─▶ extract ─▶ validate ─▶ identify ─▶ match ─▶ decide ─┬─A─▶ act_bill ─────────────────────────────────▶ report
                                                            ├─B─▶ act_vendor_email ─┬─(B5)──────────────────▶ report
                                                            │                       └─▶ await_vendor (interrupt)
                                                            │                                │ balasan vendor
                                                            │                                ▼
                                                            │                           apply_reply ─┬─lampiran─▶ extract (revisi baru)
                                                            │                                        └─teks─────▶ read_reply ─┬─1 nomor PO────▶ identify
                                                            │                                                                 └─0 atau > 1──▶ await_vendor
                                                            └─C─▶ prepare_approval ─▶ await_approval ─▶ apply_decision ─┬─setujui─────▶ act_bill
                                                                                                                        ├─tolak───────▶ report
                                                                                                                        └─klarifikasi─▶ act_vendor_email
decide: himpunan {B, C} → pilihan LLM di dalam pagar (BR-17); kutipan kebijakan per alasan (BR-21)
```

Nama node boleh disesuaikan, asal node interrupt tetap tanpa efek samping. Pilihannya dicatat di ADR-0011.

## Langkah

### M4b.0 Catatan tinjauan M4a
1. Simpan prompt ini apa adanya ke `docs/tugas/M4b.md`, lalu tambahkan baris M4b berstatus "Berjalan" di `docs/tugas/README.md`.
   - Commit: `M4b.0 prompt tugas M4b`.
2. Di **RENCANA §6**:
   - ubah baris M4b sesuai isi tugas ini, target 10–12 November;
   - tambahkan baris M4c (isi dari Konteks), target 13–14 November;
   - geser M5 menjadi 15–19 November;
   - tambahkan subbagian "Masukan dari tinjauan M4a untuk M4b–M4c (10 November 2026)", berisi ringkasan temuan dan keputusan di atas.

### M4b.1 Perbaikan dari tinjauan M4a
Kerjakan berurutan; satu butir satu commit atau lebih.

1. **Penyamaran tanpa celah** (temuan 1, commit kode pertama).
   - Anggap `*` sebagai digit yang sudah tersamar, bukan alasan untuk melewati penyamaran. Contoh: `0990********0000` disamarkan lagi tetap sama, sedangkan `1381646027*` menjadi `*******027*`.
   - Sifat yang dites: hasil penyamaran tidak pernah menampilkan lebih banyak digit asli daripada aturannya (NPWP 8, rekening 4), dan penyamaran ulang tidak mengubah hasil.
   - Masukan "nakal" untuk tes: kedua contoh di temuan 1, `0990.4279.5722.0000 (*)`, dan nilai yang hanya berisi bintang.
2. **Jalur ke `failed`** (temuan 2).
   - Bila status saat galat bukan `processing`, runner lebih dulu memindahkannya ke `processing` (tabel transisi sudah mengizinkannya dari `received`, `awaiting_vendor`, `awaiting_approval`, dan `failed`), baru ke `failed`. Kedua perubahan tercatat di audit.
   - Ada tes untuk `received`, `awaiting_vendor`, dan `awaiting_approval`.
3. **Paket bukti tersamar** (keputusan 2).
   - Field faktur yang disamarkan: NPWP penjual dan pembeli, serta nomor rekening.
   - Field bukti temuan yang disamarkan:
     - `VendorEvidence.npwp_digits`;
     - `IdentityEvidence.npwp_digits` dan `vendor_npwp_digits`;
     - `BankAccountEvidence.printed_number`, `printed_digits`, dan `registered_digits`;
     - `TaxIdEvidence.printed`, `digits`, dan `expected` bila `field` berisi NPWP.
   - Penyamaran dilakukan menurut nama field. Jangan hanya mengandalkan `mask_text`, karena nomor rekening tercetak dengan pemisah (`137-00-1234567-8`) tidak tertangkap polanya.
   - Kesimpulan kode tetap utuh.
   - Tes memindai JSON paket bukti REK-01, REK-05, dan PJK-05. Urutan digit NPWP dan rekening utuh, baik dari dokumen maupun dari data master, tidak boleh muncul, dibandingkan setelah pemisah dibuang. Field hash dikecualikan.
   - Data pengembangan lama cukup dibersihkan dengan `db:reset`; tidak perlu migrasi.
4. **Peringatan pada draft dan penjelasan pilihan approver** (keputusan 3).
   - Begitu verifikasi tagihan gagal dan ada draft agent, tulis catatan (kunci BR-22 sendiri) pada draft itu, misalnya: "Draft ini tidak lolos verifikasi Faktur Beres (selisih …). Jangan diposting sebelum diperiksa; perbaiki atau hapus secara manual. Referensi [FB-…]."
   - Paket bukti memuat alasan "Setujui" tidak tersedia, beserta selisihnya.
   - Bila approver menolak, pesan status menyebut nomor draft yang perlu dibereskan staf AP.
5. **Pagar pemetaan baris** (keputusan 4).
   - Satuan dikelompokkan menjadi berat (`kg`, `KGM`, `kilogram`) dan hitungan (`pcs`, `unit`, `units`, `roll`, `set`, `lembar`, `paket`, `buah`, `H87`, `C62`). Satuan PO di dataset adalah `kg` dan `Units`. Satuan di luar daftar dianggap tidak sejenis.
   - Pemetaan LLM dipakai hanya bila satuannya sejenis dan harga faktur berada dalam ±toleransi BR-08 dari harga PO. Bila tidak, pemetaan dibuang, baris tetap `no_match`, dan jejak mencatat alasannya.
   - Tes:
     - KTH-06 tetap terpetakan;
     - pemetaan "Ongkos kirim" QTY-04 (paket, Rp450.000) ke baris PO Pisau Die-Cut (Rp1.250.000) ditolak karena harganya.
6. **Email vendor versi 2** (keputusan 5, templat `v2`; `v1` tetap ada dan dikunci tes sidik).
   - Kalimat untuk vendor disusun dari bukti temuan, bukan dari pesan internal:
     - harga: harga di faktur dan harga di pesanan pembelian, tanpa batas atau toleransi;
     - kuantitas: yang ditagih dan yang sudah diterima tetapi belum ditagih;
     - baris di luar PO;
     - pajak: apa yang kurang atau salah hitung;
     - hitungan baris;
     - PO yang tidak jelas: nomor PO kandidat milik vendor itu sendiri, bila ada;
     - duplikat: nomor, tanggal, dan total yang sudah tercatat, tanpa menyebut sistem.
   - Tambahkan header `Date` dan rapikan baris kosong.
   - Tes memastikan email tidak memuat "Odoo", "Faktur Beres", "batas", "toleransi", kode alasan, maupun NPWP atau rekening utuh.
7. **Catatan draft yang lebih lengkap.** Catatan di draft tagihan Odoo menyebut:
   - nomor PO yang dikoreksi atau ditemukan tanpa nomor (catatan BR-06);
   - baris hasil pemetaan LLM;
   - catatan harga lebih rendah.
8. **Hook pre-commit.** Hook yang dipasang `npm run setup` ikut menjalankan `ruff check` dan `ruff format --check`, selain `guard:secrets`. Jalankan `npm run setup`, dan tunjukkan di laporan bahwa hook lama diganti (status `diperbarui`).
9. **`npm run llm:kuota`** menggantikan `python -m faktur_beres.evals.audit_log`. Perintah ini menampilkan permintaan per model (Flash-Lite, cadangan, dan embedding) sejak reset harian terakhir (15.00 WIB).
   - Semua jalur yang memanggil Gemini menulis catatan `llm_call` ke berkas JSONL yang sama: alat evaluasi (sudah), CLI agent dan pemantau inbox, `llm:model -- --uji`, `test:llm`, `rag:indeks`, dan `rag:cari`. Tes unit tetap tidak menulis ke berkas sungguhan.
   - Pencatat JSONL dipindah ke modul yang boleh dipakai kode agent (mis. `faktur_beres.llm`), karena kode agent tidak boleh mengimpor `evals` (tes arsitektur).
   - Catatan yang sudah ada di `eval/hasil/audit-llm.jsonl` tetap ikut terhitung.
   - Tabel `audit_log` tidak dipakai sebagai sumber, karena `db:reset` mengosongkannya.
10. **Waktu proses keseluruhan.** Tambahkan median dan p95 seluruh 53 dokumen run `20261110-022507` ke `eval/README.md`, dihitung dari data mentahnya. Baris "keseluruhan" juga menjadi bagian ringkasan `eval:graph`.
11. **ADR-0009 dan ADR-0010.** Masukkan tambahan dari keputusan 1, lalu ubah statusnya menjadi Diterima dengan tanggal penerimaan, dan perbarui indeks ADR.
12. **SPEC 0.6 dimulai** dengan:
    - §5.1: pemecahan node `act`, merujuk ADR-0009;
    - BR-07: pagar pemetaan;
    - BR-24: paket bukti dan antarmuka;
    - F-07: pilihan approver yang bergantung kondisi.

    Baris §16 dilengkapi di M4b.7.
13. **`npm run test:integrasi`** dijalankan ulang setelah butir 1–4, dan hasilnya masuk laporan.

### M4b.2 Pemantau inbox (F-02)
1. **Migrasi `0002`.**
   - Tabel `inbox_messages`, satu baris per pesan. Isinya:
     - Message-ID (unik), waktu terima, pengirim, penerima, dan subjek;
     - jenis (`faktur`, `balasan`, `abaikan`, `ditolak`);
     - `invoice_id`;
     - ringkasan per lampiran (nama, SHA-256, MIME, ukuran, dan `invoice_id` bila menjadi faktur);
     - status dan alasan.
   - Riwayat berkas per revisi faktur (mis. tabel `invoice_files`: `invoice_id`, revisi, path, SHA-256, MIME, ukuran, dan sumber). `invoices` menunjuk berkas revisi yang berlaku. Bentuk akhirnya boleh lain; catat di ADR-0011.
2. **Pembaca Mailpit** di `faktur_beres.tools` (kode agent). SPEC §5.2 menaruh pembacaan inbox di `email_tool`; modul terpisah boleh, catat di ADR-0011.
   - Periksa endpoint di dokumentasi API Mailpit v1.32.0 sebelum dipakai: daftar pesan, isi dan header pesan, serta unduh lampiran.
   - Pesan yang diproses hanya yang ditujukan ke `ap@arunika.test` dan tidak berasal dari domain internal.
   - Status baca Mailpit tidak dipakai sebagai penanda, karena membuka pesan di UI Mailpit ikut mengubahnya. Sumber kebenarannya `inbox_messages`.
   - Ukuran lampiran diperiksa sebelum diunduh (batas M3, 15 MB).
3. **Klasifikasi pesan:**
   - Pesan dianggap **balasan** (M4b.3) bila subjeknya memuat `[FB-nnnnnn]`, atau bila `In-Reply-To`/`References` cocok dengan Message-ID email keluar di tabel `actions`.
   - Balasan ke thread yang tidak dikenal atau tidak sedang menunggu dicatat beserta alasannya dan tidak diproses (BR-20). Lampirannya tidak otomatis menjadi faktur baru.
   - Bila bukan balasan, setiap lampiran berjenis faktur menjadi satu faktur lewat `Runner.accept` (kanal email, pengirim, subjek, isi), lalu graph dijalankan.
   - Sisanya → **abaikan**, tetapi tetap dicatat.
4. **Idempoten.** Satu Message-ID diproses sekali. Bila proses terputus di tengah, pemanggilan berikutnya melanjutkan per lampiran tanpa membuat faktur ganda.
5. **CLI:**
   - `npm run agent:inbox`: satu kali baca;
   - `npm run agent:inbox -- --terus`: berulang tiap 10 detik sampai dihentikan.

   API M4c memakai fungsi yang sama.
6. **Catatan perilaku.** Setelah `db:reset`, pesan lama di Mailpit akan terbaca lagi. Catat ini di README, dan sarankan Mailpit dikosongkan bersama `db:reset` saat menyiapkan demo (perintah reset demo gabungan dibuat di M6).
7. **Tes:**
   - unit dengan Mailpit palsu: klasifikasi, idempoten, lampiran terlalu besar, pesan keluar diabaikan;
   - integrasi: kirim lewat SMTP, baca, dan faktur terbentuk.

### M4b.3 Balasan vendor (F-06, BR-20)
1. **Menunggu lewat interrupt.**
   - Setelah email klarifikasi (bukan pemberitahuan B5), graph masuk node `await_vendor`, yang hanya berisi interrupt. Status tetap `awaiting_vendor`.
   - B5 tetap langsung ke `report` lalu selesai (`closed_duplicate`).
   - Klarifikasi atas keputusan approver juga masuk `await_vendor`.
   - Kunci BR-22 email klarifikasi memuat nomor putaran. Dengan begitu, klarifikasi putaran kedua adalah aksi baru walaupun temuannya sama, sedangkan pengulangan dalam putaran yang sama tetap dikenali.
2. **Runner `reply(invoice_id, balasan)`** memeriksa beberapa hal sebelum melanjutkan graph:
   - faktur berstatus `awaiting_vendor` dan ada interrupt `await_vendor` yang tertunda;
   - BR-20: pengirim sama dengan alamat penagihan vendor di Odoo setelah dinormalisasi. Bila tidak, catat `ditolak` di `inbox_messages` beserta audit `security`, dan graph tidak dilanjutkan;
   - satu pesan balasan diproses sekali.

   Bila semuanya lolos:
   - runner menyimpan lampiran lebih dulu (seperti `accept`);
   - runner memindahkan status ke `processing`;
   - runner melanjutkan graph dengan payload yang hanya berisi referensi (ID pesan, path, hash), bukan isi berkas.

   Teks balasan dibersihkan dari kutipan email sebelumnya (baris berawalan `>`) sebelum dipakai.
3. **Balasan berlampiran faktur.**
   - Lampiran faktur pertama disimpan sebagai berkas revisi baru di folder berkas faktur itu (mis. `revisi-2.pdf`), tanpa menimpa berkas asli. Lampiran lain dicatat saja.
   - `apply_reply` mencatat balasan, menaikkan nomor revisi, dan mengosongkan state yang bergantung pada dokumen lama.
   - Fragmen BR-15 untuk revisi itu adalah teks bebas dokumen perbaikan ditambah subjek dan isi balasan (`TextSource.VENDOR_REPLY`), seperti `reply_fragments` di evaluasi orakel.
   - Graph lalu lanjut ke `extract` untuk revisi baru.
   - BR-11: faktur perbaikan menggantikan data thread dan tidak dibandingkan dengan aslinya.
4. **Balasan teks.**
   - `read_reply` mengekstrak nomor PO yang disebut vendor dengan keluaran terstruktur (prompt `reply-reading-v1`). Teks balasan dibungkus pembatas nonce sebagai data, dan nomor disalin seperti tertulis.
   - Kode memvalidasi setiap nomor: dinormalisasi, ada di Odoo, milik vendor itu, dan tidak batal.
   - **Tepat satu nomor unik lolos:** `identify` diulang dengan `po_number_from_reply`.
   - **Nol atau lebih dari satu:** balasan dicatat, faktur kembali ke `await_vendor` tanpa email baru, dan jejaknya menjelaskan alasannya. Perilaku ini sama dengan `_vendor_reply` di `evals/orakel.py`. Tindak lanjutnya lewat BR-19 di M4c.
   - Bila LLM tidak tersedia → `failed` (BR-27), dan `agent:ulang` mencoba lagi.
5. **Setelah balasan,** alurnya biasa: `decide`, lalu A, B (putaran klarifikasi kedua), atau C. Catatan draft (M4b.1 butir 7) menyebut revisi faktur atau nomor PO yang berasal dari balasan vendor.
6. **Keterbatasan yang dicatat, tidak dikerjakan.**
   - Kasusnya: draft agent yang tidak lolos verifikasi masih ada, lalu vendor mengirim faktur perbaikan dengan nomor yang sama.
   - Saat ini hasilnya `conflict` dan faktur masuk C.
   - Mengubah draft milik agent (haknya sudah ada) dicatat sebagai pekerjaan lanjutan di ADR-0011.
7. **Tes** (LLM palsu; Mailpit palsu untuk unit, Mailpit sungguhan untuk integrasi):
   - balasan lampiran sampai `draft_created`;
   - balasan teks NPO-02 (satu nomor) dan NPO-03 (nomor batal dan penggantinya);
   - balasan teks tanpa nomor sah → tetap `awaiting_vendor`;
   - pengirim lain;
   - thread yang tidak menunggu;
   - balasan yang sama diproses dua kali;
   - balasan berisi instruksi kepada sistem → C5.

### M4b.4 RAG kebijakan (`policy_rag`, BR-28)
1. **Dua dokumen kebijakan sintetis** di `data/kebijakan/`, ditulis mengikuti Lampiran A dalam bahasa Indonesia, dengan pasal bernomor.
   - Angka kebijakan diberi penanda, misalnya `<!-- param: toleransi_harga_persen -->`. Yang wajib ditandai: nilai rupiah, persen, jumlah hari, jumlah pengingat, faktor DPP, dan kode transaksi.
   - Angka format tidak perlu penanda: panjang NPWP dan NSFP, nomor PMK, dan nomor pasal.
   - Tes BR-28 memeriksa:
     - setiap angka bertanda sama dengan `parameter.yaml`;
     - tidak ada angka rupiah, persen, atau hari tanpa penanda.
2. **Potongan.**
   - Satu potongan per pasal atau subpasal, dengan ref seperti `SOP-8.2`, judul, teks, dan SHA-256.
   - Penanda dibuang dari teks potongan.
   - Hasilnya deterministik.
3. **Migrasi `0003`.**
   - `CREATE EXTENSION IF NOT EXISTS vector`.
   - Tabel `policy_chunks`: ref, dokumen, judul, teks, SHA-256, `embedding vector(768)`, model, dimensi, versi format, dan waktu.
   - Indeks HNSW dengan operator cosine.
   - Pakai pustaka `pgvector` (Python) untuk tipe SQLAlchemy, dan catat versinya.
   - **`db:reset` tidak mengosongkan `policy_chunks` maupun simpanan kutipan per alasan (butir 7).** Isinya turunan dokumen kebijakan, bukan data proses, sedangkan `eval:graph` menjalankan `db:reset` sebelum setiap skenario. Ada tes untuk ini.
4. **Klien embedding** di `faktur_beres.llm`.
   - `gemini-embedding-2` dengan `output_dimensionality` 768. Angka 768 adalah konstanta yang sama dengan kolom `vector(768)`, dan ada tes yang memastikan keduanya sama.
   - Format masukan mengikuti panduan Google untuk model ini (instruksi tugas di dalam teks, mis. `task: search result | query: …` untuk kueri). Periksa dokumentasinya dulu dan catat sumbernya.
   - Konfigurasi `GEMINI_MODEL_EMBEDDING` dan `GEMINI_RPM_EMBEDDING` (bawaan 80), mengikuti pola `GEMINI_MODEL_*` dan `GEMINI_RPM_*` yang sudah ada.
   - Throttle sendiri, retry, audit BR-23 dengan tujuan `embedding`, dan biaya setara dari halaman harga resmi.
   - Cache berdasarkan hash teks, model, dan dimensi.
5. **Perintah:**
   - `npm run rag:indeks` hanya meng-embed potongan yang baru atau berubah;
   - `npm run rag:cari -- "<kueri>"` menampilkan 3 teratas beserta ref dan skor, untuk tinjauan.
6. **Alat `policy_rag`:** `search_policy(query, k ≤ 5)` mengembalikan ref, judul, kutipan (paling panjang 600 karakter), dan skor. Alat ini hanya membaca.
7. **Kutipan per alasan tanpa LLM.**
   - Setiap kode alasan punya kueri tetap di kode.
   - Saat `rag:indeks`, kueri itu di-embed dan hasil pencariannya (ref dan skor) disimpan, sehingga `decide` tidak memanggil API untuk kutipan per alasan.
   - Kutipan hanya dipakai bila skornya di atas ambang. Tetapkan ambangnya dari data, lalu catat.
8. **Tes:**
   - BR-28;
   - potongan deterministik;
   - pencarian dengan embedding palsu;
   - dimensi;
   - indeks bertahan setelah `db:reset`;
   - kebocoran label di dokumen kebijakan.

### M4b.5 Pilihan jalur oleh LLM (BR-17) dan kutipan kebijakan (BR-21)
1. **Kapan berjalan:** hanya bila himpunan jalur yang diizinkan berisi lebih dari satu jalur, yaitu {B, C}. Himpunan tunggal tidak memanggil LLM.
2. **Pemilih jalur** memakai function calling dari M4a.
   - Alat: `search_policy` dan `submit_route(route, justification ≤ 400, quote_refs ≤ 3)`.
   - Prompt `decide-route-v1`.
   - Masukan di dalam pembatas nonce, tanpa NPWP, rekening, dan email:
     - nama vendor dan total faktur;
     - alasan beserta rinciannya dari kode;
     - putaran klarifikasi, yaitu jumlah email klarifikasi yang sudah terkirim untuk faktur ini menurut tabel `actions`;
     - alasan pada putaran sebelumnya, bila ada;
     - jalur yang diizinkan dan jalur bawaan.
3. **Guard:**
   - jalur harus di dalam himpunan;
   - `quote_refs` harus berasal dari hasil `search_policy` pada percakapan yang sama;
   - pindah dari jalur bawaan butuh alasan tertulis dan minimal satu kutipan (`guard_route`, M2);
   - jawaban yang ditolak guard dicatat di audit, lalu jalur bawaan yang dipakai.
4. **Anggaran:** paling banyak 4 panggilan model dan 3 eksekusi alat. Pemilih ini tidak memakai model cadangan. Bila LLM tidak tersedia atau anggaran habis, jalur bawaan dipakai dengan catatan; BR-27 tidak terpicu.
5. **Catatan keputusan dan paket bukti.**
   - Audit `decision` mencatat jalur, siapa yang memutuskan (aturan atau LLM), alasan, dan kutipan.
   - Paket bukti C memuat `policy_quotes`: kutipan per alasan (M4b.4 butir 7) ditambah kutipan pilihan LLM.
   - Email vendor tidak berubah dan tetap tanpa teks LLM.
6. **Tes dengan LLM palsu:**
   - tetap di jalur bawaan;
   - eskalasi dengan kutipan sah;
   - eskalasi tanpa kutipan → ditolak;
   - jalur di luar himpunan;
   - kutipan yang tidak pernah dicari;
   - anggaran habis;
   - LLM tidak tersedia.

### M4b.6 Evaluasi graph sampai status akhir
1. **Kanal masuk.**
   - Skenario `kanal: email` (20 skenario) dikirim lewat SMTP ke Mailpit dari blok `email` label: `dari`, `kepada`, `subjek`, `isi`, `tanggal` (header `Date`), dan `message_id` (header `Message-ID`), dengan lampiran `berkas` bernama `nama_kirim`. Harness lalu menjalankan satu kali baca inbox, yang membuat faktur dan menjalankan graph.
   - Skenario `kanal: unggahan` tetap lewat `Runner.accept`.
   - `--intake langsung` (semua lewat `Runner.accept`, seperti M4a) tetap tersedia untuk pengembangan.
2. **Balasan vendor lewat Mailpit sungguhan.**
   - Untuk skenario dengan `balasan_vendor`, harness mengirim email dari `dari` ke `ap@arunika.test`.
   - Subjeknya `Re: ` + subjek email agent yang dibaca dari Mailpit, dengan `In-Reply-To` berisi Message-ID email itu.
   - Isinya `isi`, dan lampirannya `berkas` bila jenisnya lampiran.
   - Lalu harness menjalankan inbox, dan graph berlanjut.
3. **Mode.**
   - Mode orakel mengganti ekstraksi dokumen dengan kebenaran label, termasuk lampiran balasan yang dikenali dari hash-nya (`simulasi.balasan_vendor.berkas.sha256`).
   - Pembaca balasan teks dan pemilih jalur tetap memakai LLM, seperti perencana dan pemetaan baris di M4a.
   - `--tanpa-llm` juga mematikan pemilih jalur (jalur bawaan). Balasan teks lalu dibaca pembaca deterministik milik harness (pola nomor PO di `evals/orakel.py`), yang hanya membaca teks balasan.
4. **Prasyarat.** Sebelum mulai, `eval:graph` memeriksa bahwa indeks RAG ada dan sesuai (model, dimensi, hash dokumen). Bila tidak, ia berhenti dengan pesan untuk menjalankan `rag:indeks`.
5. **Penilaian.** Semua 40 skenario dinilai sampai status akhir dengan penilai bersama `eval:orakel`. Label "menunggu M4b" tidak ada lagi.
6. **Ringkasan tambahan:**
   - pilihan jalur oleh LLM: jumlah, eskalasi, dan penolakan guard;
   - panggilan embedding;
   - balasan yang diproses;
   - waktu sampai jalur pertama ditentukan, keseluruhan dan per format.
7. **Run** (setelah 15.00 WIB, setelah memberi tahu pemilik):
   1. `rag:indeks`;
   2. mode orakel (syarat lulus, kriteria 7);
   3. mode LLM segar (kriteria 8).

   Sebutkan perkiraan panggilan Flash-Lite dan embedding sebelum setiap run.

### M4b.7 Dokumen dan push
1. **ADR baru, berstatus Diusulkan:**
   - **ADR-0011 "Email masuk dan balasan vendor":**
     - pemantau inbox, klasifikasi, `inbox_messages`, dan riwayat berkas per revisi;
     - BR-20 dan batas keamanannya: pengirim hanya diperiksa dari header `From`, sedangkan di produksi perlu SPF, DKIM, dan DMARC;
     - interrupt `await_vendor`, revisi faktur, pembacaan balasan teks, dan nomor putaran di kunci BR-22;
     - keterbatasan draft lama.
   - **ADR-0012 "RAG kebijakan dan pilihan jalur oleh LLM":**
     - model dan dimensi embedding, serta format masukan;
     - potongan dan indeks pgvector;
     - kutipan per alasan dan ambang skor;
     - guard dan anggaran pemilih jalur.
   - Bagian ADR-0009 dan ADR-0010 (yang sudah Diterima) yang berubah karena M4b ditulis di ADR baru itu. ADR lama hanya diberi catatan bertanggal yang merujuk ke sana.
2. **SPEC 0.6 dilengkapi:**
   - §5.1 (graph M4b) dan §5.2 (`policy_rag` dan alat pemilih jalur);
   - §9.2 (`inbox_messages`, riwayat berkas, dan `policy_chunks` dibuat M4b; `vendor_memory` dipindah ke M4c);
   - §12.1 (evaluasi graph sampai status akhir);
   - §14 (dimensi embedding 768);
   - catatan F-02 dan F-06;
   - §16.
3. **RENCANA:**
   - §2: pustaka `pgvector`;
   - §3.2: `policy_rag`, pembaca inbox, dan klien embedding; `faktur_beres.api` pindah ke M4c;
   - §3.4: tes bertanda untuk aturan yang baru atau berubah (BR-07, BR-11, BR-15, BR-17, BR-20, BR-21, BR-24, dan BR-28), dan BR-20 masuk `CODE_RULES`;
   - §4.2: `setup` (hook baru), `agent:inbox`, `rag:indeks`, `rag:cari`, `llm:kuota`, dan opsi `eval:graph --intake`;
   - §6: M4b ✅ dengan tanggal;
   - §7: variasi pilihan jalur oleh LLM, dan kuota embedding.
4. **Dokumen lain:**
   - `npm run graph:diagram` dijalankan ulang;
   - README (status dan perintah);
   - `eval/README.md` (hasil kedua run);
   - `docs/tugas/README.md` (M4b selesai).
5. **Pemeriksaan dan push:** `npm run check`, `npm run test:integrasi`, `npm run guard:secrets`, lalu push ke `origin main`.

## Kapan berhenti dan bertanya
- Ekstensi `vector` tidak bisa dibuat dengan user aplikasi.
- API Mailpit tidak menyediakan yang dibutuhkan, mis. unduh lampiran atau header `In-Reply-To`.
- Di mode orakel, pemilih jalur mengeskalasi B ke C karena tafsir kebijakan (bukan bug), dan perbaikan umum pada prompt `decide-route-v1` tidak menghilangkannya. Dokumen kebijakan dan prompt tidak boleh disetel untuk skenario tertentu.
- `gemini-embedding-2` tidak menerima 768 dimensi atau format masukan yang dipakai.
- Ada aturan BR yang perlu diubah di luar yang disebut di tugas ini, atau label bertentangan dengan aturan.
- Kuota habis sebelum run wajib selesai. Beri tahu pemilik, lalu lanjutkan dengan `--lanjut` setelah reset.
- Hal lain di bagian "Kapan berhenti dan bertanya" di CLAUDE.md.

## Laporan
Ikuti format di `docs/tugas/README.md`, ditambah butir khusus M4b:
1. **Hasil perbaikan M4b.1**, masing-masing dengan tesnya, termasuk:
   - contoh paket bukti REK-01 yang sudah tersamar;
   - contoh email vendor versi 2 untuk HRG-04 dan DUP-01.
2. **Hasil `eval:graph` mode orakel dan mode LLM:**
   - pertama berhenti dan status akhir per kategori, untuk faktur dan ketahanan;
   - waktu keseluruhan dan per format;
   - panggilan per model dan per tujuan, termasuk embedding;
   - biaya per faktur.
3. **Pilihan jalur oleh LLM:** jumlah keputusan, eskalasi, penolakan guard, dan contoh catatan keputusan beserta kutipannya.
4. **RAG:**
   - jumlah potongan dan dimensi;
   - panggilan saat `rag:indeks`;
   - ambang skor;
   - hasil `rag:cari` untuk tiga kueri: perubahan rekening, ongkos kirim, dan faktur pajak.
5. **Balasan vendor:** hasil per skenario dan bukti tes BR-20.
6. **Cuplikan `agent:jejak`** untuk QTY-01 (dari faktur sampai draft setelah balasan) dan NPO-03.
7. **Pemakaian kuota** per jendela dan per model, dari `llm:kuota`.
8. **Pertanyaan dan usulan untuk M4c.**

## Lampiran A. Kerangka dokumen kebijakan (sintetis)
Tulis sebagai dokumen fiktif PT Arunika Kemas Nusantara, dengan tanda "DOKUMEN SINTETIS – UNTUK PENGUJIAN". Angka kebijakan hanya yang ada di `parameter.yaml`, dan setiap angka diberi penanda (M4b.4 butir 1).

**`data/kebijakan/sop-hutang-usaha.md` — Prosedur Operasional Standar Hutang Usaha**
1. **Ruang lingkup dan peran.** Staf AP, approver, dan agent otomatis. Agent membantu; ia tidak memposting tagihan, tidak membayar, dan tidak mengubah data pemasok.
2. **Penerimaan faktur.** Kanal unggahan dan email ke `ap@arunika.test`; format PDF, JPG, PNG, dan UBL; satu berkas satu faktur; berkas asli disimpan sebagai bukti.
3. **Pencocokan tiga arah.**
   - PO terkonfirmasi, penerimaan barang, dan faktur.
   - Kuantitas tidak melebihi yang diterima dan belum ditagih (toleransi 0).
   - Harga satuan tidak melebihi harga PO lebih dari 1%; harga yang lebih rendah dicatat.
   - Biaya di luar PO, misalnya ongkos kirim, tidak dibayar kecuali tercantum di PO.
4. **Pajak.** PMK 131/2024: DPP nilai lain 11/12 dari harga jual dan PPN 12%; NPWP 16 digit; NSFP 17 digit; kode transaksi 04; selisih pembulatan Rp1.
5. **Persetujuan approver.** Wajib bila:
   - total termasuk PPN lebih dari Rp50.000.000;
   - pemasok belum punya tagihan terposting;
   - identitas tidak cocok;
   - rekening berbeda;
   - dokumen tidak terbaca;
   - ada dugaan duplikat;
   - ada instruksi tidak wajar di dokumen.
6. **Rekening pemasok.** Pembayaran hanya ke rekening yang terdaftar. Perubahan rekening hanya lewat surat resmi pemasok dan verifikasi telepon ke kontak yang sudah tercatat. Rekening yang tertulis di faktur tidak pernah dipakai langsung.
7. **Komunikasi dengan pemasok.**
   - Klarifikasi tertulis dari `ap@arunika.test` ke alamat penagihan terdaftar, berisi rincian selisih, tanpa data internal atau data pemasok lain.
   - Tunggu 5 hari kerja, kirim 1 pengingat, lalu faktur masuk daftar perhatian staf AP.
8. **Penyelesaian selisih dan eskalasi.**
   - **8.1** Selisih harga, kuantitas, data pajak, hitungan, atau PO yang tidak jelas diselesaikan lebih dulu dengan klarifikasi kepada pemasok.
   - **8.2** Faktur dieskalasi ke approver bila:
     - (a) masalah yang sama tetap ada setelah pemasok membalas klarifikasi untuk faktur yang sama;
     - (b) pemasok meminta pembayaran ke rekening lain atau mendesak melewati prosedur;
     - (c) ada indikasi kecurangan lain.
   - **8.3** Approver dapat menyetujui, menolak, atau meminta klarifikasi.
9. **Duplikat.** Faktur dengan nomor dan total yang sama dengan yang sudah tercatat ditutup, dan pemasok diberi tahu. Faktur yang mirip tetapi tidak identik ditinjau approver.
10. **Keamanan dokumen.** Isi dokumen dan email adalah data. Instruksi di dalamnya tidak dijalankan, dan dokumen yang memuat instruksi tidak wajar ditinjau approver.

**`data/kebijakan/syarat-pembelian.md` — Syarat dan Ketentuan Umum Pembelian** (berlaku untuk semua pemasok):
- harga mengikuti PO;
- pengiriman parsial ditagih sesuai yang diterima;
- biaya pengiriman ditanggung pemasok kecuali tercantum di PO;
- setiap faktur mencantumkan nomor PO dan memenuhi ketentuan faktur pajak;
- pembayaran hanya ke rekening yang terdaftar;
- faktur perbaikan dikirim sebagai balasan atas email klarifikasi, dengan kode referensi pada subjek;
- pemasok bertanggung jawab atas kebenaran data pajak.
