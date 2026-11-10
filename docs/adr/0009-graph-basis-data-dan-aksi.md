# ADR-0009: Graph LangGraph, basis data aplikasi, dan aksi sampai agent berhenti

- Status: Diterima
- Tanggal: 2026-11-10 (diterima pemilik 2026-11-10)
- Terkait: [SPEC §5–§7, §9.2, §15.4](../SPEC.md), [RENCANA §3.2](../RENCANA-TEKNIS.md), [ADR-0001](0001-stack-inti.md), [ADR-0005](0005-llm-memutuskan-kode-menghitung.md), [ADR-0006](0006-persetujuan-dan-idempotensi.md), [diagram graph](../diagram-graph.md)

## Konteks
M4a membangun semua langkah sampai agent pertama kali berhenti: jalur A membuat draft tagihan, jalur B mengirim email, dan jalur C berhenti di interrupt, ditambah keputusan approver lewat CLI. ADR-0006 mengikat dua hal: interrupt tidak punya efek samping, dan aksi ada di node terpisah dengan kunci idempoten. Status harus mengikuti SPEC §6.3, dan kegagalan sistem tidak boleh hilang diam-diam (BR-27).

## Keputusan
1. **Bentuk graph.** Node: `intake`, `extract`, `validate`, `identify`, `match`, `decide`, `act_bill`, `act_vendor_email`, `prepare_approval`, `await_approval`, `apply_decision`, `report`. Perulangan BR-04 tetap di dalam alat `extract`, tidak menjadi tepi graph. Node `act` di SPEC §5.1 dipecah menjadi dua node aksi, dan jalur C menjadi tiga node, supaya bagian yang berefek terpisah dari interrupt.
2. **State hanya berisi nilai JSON.** Objek domain disimpan dengan `model_dump(mode="json")`, sehingga `Decimal` dan tanggal berjalan sebagai teks lalu kembali lewat model Pydantic. Tidak ada kelas aplikasi yang sampai ke checkpointer, jadi pengaman deserialisasi LangGraph tidak perlu dilonggarkan atau diberi daftar tipe.
3. **Rangkaian aturan hanya satu.** `analyse()` dipecah menjadi `validate_step`, `identify_step`, `match_step`, dan `decide_step`, yang dipakai bersama oleh `analyse()` dan node graph. Graph dengan LLM dimatikan diuji setara dengan `analyse()` untuk 40 skenario dan 13 dokumen ketahanan.
4. **Checkpointer.** `PostgresSaver` di basis data aplikasi dengan pool koneksi psycopg, `thread_id = invoice_id`, dan mode durabilitas `sync`: checkpoint ditulis sebelum langkah berikutnya mulai. Tabelnya dibuat `setup()` LangGraph lewat `db:migrate` dan tidak dikelola Alembic (`include_object`).
5. **Tabel.** Migrasi pertama membuat `invoices`, `invoice_lines`, `match_results`, `actions`, `approvals`, dan `audit_log`. `vendor_memory` dan `policy_chunks` dibuat di M4b. Enum dijaga `CHECK`, uang memakai `numeric`, dan paling banyak satu permintaan persetujuan terbuka per faktur (indeks unik parsial).
6. **ID.** `invoice_id` berformat `FB-000123` dari sequence dan menjadi `thread_id` serta referensi subjek email BR-19.
7. **Interrupt dan resume.** `prepare_approval` menulis paket bukti, baris `approvals`, dan status. `await_approval` hanya berisi `interrupt`, karena LangGraph menjalankan node dari awal saat resume. `apply_decision` mencatat keputusan. Keputusan kedua pada permintaan yang sama tidak mengubah apa pun dan melaporkan keputusan yang sudah tercatat.
8. **Putaran C setelah verifikasi gagal.** Bila tagihan tidak lolos verifikasi, faktur masuk C dengan `validation_failed`, draft dibiarkan apa adanya (BR-18), dan pilihan "setujui" tidak ditawarkan lagi di putaran itu, karena tagihan yang sama sudah gagal dibuat dengan benar. Approver bisa menolak atau meminta klarifikasi. Agent menulis catatan peringatan pada draft itu di Odoo dengan kunci BR-22 sendiri, paket bukti menjelaskan kenapa "Setujui" tidak tersedia beserta selisihnya, dan pesan status penolakan menyebut nomor draft yang perlu dibereskan staf AP.
9. **Aksi.** Setiap langkah (draft, lampiran, catatan, email) punya baris `actions` dengan kunci BR-22 sendiri. Aksi yang sudah `done` tidak diulang. Email memakai `Message-ID` deterministik dari kunci itu, dicari di Mailpit sebelum kirim, dan dibaca ulang sesudah kirim.
10. **Kegagalan.** `LlmError`, `Unavailable` setelah retry node, dan galat tak terduga membuat status `failed` dengan pesan yang bisa dibaca, dan checkpoint tetap ada. Bila status saat galat bukan `processing`, runner lebih dulu memindahkannya ke `processing`, lalu ke `failed`, supaya tabel SPEC §6.3 tetap berlaku; status akhir tidak dibuka lagi, dan galatnya dicatat di audit. `agent:ulang` melanjutkan dari checkpoint terakhir. `AccessDenied` dan penolakan BR-25 juga ditulis ke audit berjenis `security`. Node yang menyentuh Odoo atau surat dicoba ulang lewat `RetryPolicy`; aman karena aksinya idempoten.
11. **Lapis 2 injeksi.** Bila jalur bawaan A dan lapis 2 belum berhasil, faktur menjadi `failed` dengan pesan yang menjelaskannya. `agent:ulang` menjalankan lapis 2 lagi di `decide`.
12. **Reset evaluasi** hanya lewat `npm run odoo:reset`, `npm run db:reset`, dan penghapusan pesan lewat API Mailpit.
13. **Kebijakan data paket bukti.** Paket bukti disamarkan seperti audit (BR-24): NPWP 4 digit awal dan akhir, rekening 4 digit akhir, menurut nama field dan di setiap tempat nomor yang sama muncul lagi. Kesimpulan kode tetap utuh. Nilai utuh tetap ada di berkas asli, di Odoo, dan di data kerja internal agent (hasil ekstraksi dan checkpoint), yang tidak pernah dikirim utuh ke API atau antarmuka.
14. **Versi pustaka** (uv.lock): LangGraph 1.2.20, langgraph-checkpoint 4.3.1, langgraph-checkpoint-postgres 3.2.0, SQLAlchemy 2.1.8, psycopg 3.3.8, psycopg-pool 3.3.3, Alembic 1.20.1. LangGraph 1.0.10 ke atas memuat perbaikan CVE-2026-28277.

## Konsekuensi
- Node tambahan di jalur C membuat diagram lebih panjang daripada SPEC §5.1, tetapi pemisahan efek samping bisa diuji langsung.
- State berisi dump JSON sehingga setiap node memvalidasi ulang objeknya; biayanya kecil dibanding panggilan ERP dan LLM.
- Tes unit graph memakai toko data palsu di memori. Perilaku tabel sungguhan diuji terpisah dengan database sementara.
- `langsmith` ikut terpasang sebagai dependensi LangGraph. Pelacakannya hanya aktif bila variabel lingkungannya diisi, dan repo ini tidak mengisinya.

## Alternatif yang ditolak
- **Objek Pydantic di state:** butuh daftar tipe di serializer checkpoint dan melonggarkan pengaman deserialisasi.
- **Interrupt dan pencatatan dalam satu node:** pencatatan akan berulang setiap resume, bertentangan dengan ADR-0006.
- **Perulangan BR-04 sebagai tepi graph:** logikanya sudah teruji di `read_with_llm` pada M3.
