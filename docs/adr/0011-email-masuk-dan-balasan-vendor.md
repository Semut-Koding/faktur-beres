# ADR-0011: Email masuk dan balasan vendor

- Status: Diusulkan
- Tanggal: 2026-11-10
- Terkait: [SPEC §5.1, §7 (F-02, F-06), BR-11, BR-15, BR-19, BR-20, BR-22](../SPEC.md), [ADR-0006](0006-persetujuan-dan-idempotensi.md), [ADR-0009](0009-graph-basis-data-dan-aksi.md)

## Konteks
M4b menambah faktur lewat email dan balasan vendor yang melanjutkan thread. Isi email adalah data, satu pesan tidak boleh diproses dua kali, dan node interrupt tidak boleh punya efek samping (ADR-0006).

## Keputusan
1. **Pemantau inbox** (`graph/inbox.py`) membaca Mailpit lewat `tools/inbox.py`, modul baca yang terpisah dari `email_tool`, karena `email_tool` hanya untuk pengiriman. Endpoint diperiksa terhadap `/api/v1/swagger.json` Mailpit v1.32.0 dan percobaan langsung. Hanya pesan ke `ap@arunika.test` yang dilihat, dan pesan dari domain internal diabaikan.
2. **`inbox_messages` adalah sumber kebenaran**, bukan status baca Mailpit. Satu Message-ID diproses sekali. Bila proses terputus, lampiran yang sudah menjadi faktur dikenali dari Message-ID dan hash berkas (`invoices.source_message_id`).
3. **Klasifikasi.** Balasan dikenali dari `[FB-nnnnnn]` di subjek, atau `In-Reply-To`/`References` yang menunjuk email keluar di `actions`. Balasan ke thread yang tidak dikenal atau tidak menunggu dicatat dan tidak diproses; lampirannya tidak menjadi faktur baru.
4. **Riwayat berkas.** `invoice_files` menyimpan satu baris per revisi; kolom `file_*` dan `stored_path` di `invoices` selalu menunjuk revisi yang berlaku. Revisi disimpan sebagai `revisi-N.<ekstensi>` tanpa menimpa berkas asli.
5. **Menunggu vendor** memakai node `await_vendor` yang hanya berisi interrupt. `Runner.reply` menyimpan lampiran dan memindahkan status lebih dulu, lalu melanjutkan graph dengan payload berisi referensi. `apply_reply` mencatat balasan; `read_reply` membaca balasan teks. Balasan berlampiran menghapus state yang bergantung pada dokumen lama dan masuk `extract`.
6. **BR-20.** Pengirim dibandingkan dengan alamat penagihan vendor dari analisis terakhir, yang berasal dari Odoo. Pengirim lain dicatat `ditolak`, ditulis ke audit `security`, dan lampirannya tidak diunduh. **Batas keamanannya:** yang diperiksa hanya header `From`. Di produksi perlu verifikasi SPF, DKIM, dan DMARC.
7. **Balasan teks.** Nomor PO disalin LLM (`reply-reading-v1`) lalu divalidasi kode: ada di Odoo, milik vendor, tidak batal. Tepat satu nomor unik mengulang `identify` dengan nomor itu. Nol atau lebih dari satu kembali ke `await_vendor` tanpa email baru. Balasan diperiksa BR-15 lebih dulu; balasan berisi instruksi tidak dibaca LLM dan dinilai ulang lewat aturan.
8. **Nomor putaran** masuk isi kunci BR-22 email klarifikasi, sehingga klarifikasi putaran kedua adalah aksi baru, sedangkan pengulangan dalam putaran yang sama tetap dikenali.

## Konsekuensi
- Setelah `db:reset`, pesan lama di Mailpit terbaca lagi. Mailpit perlu dikosongkan bersama `db:reset` saat menyiapkan demo.
- Pengirim palsu yang memalsukan `From` masih lolos di lingkungan lokal.
- **Keterbatasan yang dicatat, tidak dikerjakan:** bila draft agent yang gagal verifikasi masih ada dan vendor mengirim faktur perbaikan dengan nomor yang sama, hasilnya `conflict` dan faktur masuk C. Mengubah draft milik agent adalah pekerjaan lanjutan.

## Alternatif yang ditolak
- **Status baca Mailpit sebagai penanda:** berubah bila pesan dibuka di UI.
- **Isi berkas di payload resume:** membuat checkpoint besar dan menaruh dokumen di state.
- **Membaca balasan teks dengan pola nomor saja:** gagal pada kalimat seperti "PO lama batal, pakai yang baru"; pola tetap dipakai harness saat `--tanpa-llm`.
