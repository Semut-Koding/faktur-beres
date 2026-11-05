# ADR-0007: Data sintetis, dataset tunggal, dan katalog 40 skenario berlabel

- Status: Diterima
- Tanggal: 2026-11-05
- Terkait: [SPEC §9.4, §12, §15](../SPEC.md#15-lampiran), [`data/seed/dataset.yaml`](../../data/seed/dataset.yaml), proposal §6.2 dan §7

## Konteks
- Lomba mewajibkan karya aman dan menyerahkan privasi data kepada peserta. UU PDP berlaku untuk data pribadi.
- Tier gratis Gemini dapat memakai data untuk peningkatan produk.
- Proposal menjanjikan evaluasi pada 40 skenario sintetis berlabel, masing-masing dijalankan tiga kali dan dinilai dari state akhir.
- Generator faktur (M1) bergantung pada data Odoo hasil seed (M0), sehingga keduanya butuh satu sumber yang sama.
- Bila label jawaban ikut terbaca agent, hasil evaluasi tidak bermakna.

## Keputusan
- **Semua data fiktif:** perusahaan PT Arunika Kemas Nusantara, 10 vendor terdaftar, 1 vendor di luar ERP, 3 bank fiktif, email berdomain `.test`, dan NPWP berawalan `0990` dengan digit cek valid (agar lolos validasi format). Tidak ada orang bernama di data.
- **[`data/seed/dataset.yaml`](../../data/seed/dataset.yaml)** menjadi sumber tunggal data master dan transaksi untuk seed, generator, dan evaluasi. Nomor PO ditetapkan di dataset. Setiap subtotal kelipatan Rp300 agar DPP nilai lain dan PPN bulat.
- **Katalog 40 skenario** (8 kategori × 5) di SPEC §15.3, dengan label per skenario di `eval/skenario/` (format SPEC §15.4).
- **Label tidak boleh bocor:** ID skenario, kategori, dan jawaban tidak muncul di Odoo (termasuk external ID, referensi, dan catatan), di berkas faktur, atau di dokumen kebijakan untuk RAG. Ada tes yang memeriksanya.
- Setiap berkas faktur memuat tanda kecil "DOKUMEN SINTETIS – UNTUK PENGUJIAN", tanpa logo, QR, atau tampilan resmi DJP maupun bank sungguhan.
- Evaluasi mereset Odoo ke snapshot seed sebelum setiap percobaan.

## Konsekuensi
- Demo dan evaluasi bisa diulang siapa saja dari repo, tanpa data rahasia.
- Hasil evaluasi tidak bisa diklaim sebagai hasil produksi; proposal menyatakannya apa adanya.
- Mengubah dataset bisa mengubah label. Karena itu perubahan dataset wajib lolos tes konsistensi dataset dan kebutuhan skenario (SPEC §15.3).
- Akurasi pada faktur sungguhan (pindaian buram, tulisan tangan) belum terukur.

## Alternatif
- **Faktur sungguhan yang disamarkan:** lebih realistis, tetapi berisiko privasi dan tidak boleh dikirim ke tier gratis LLM.
- **Data acak tanpa katalog berlabel:** cepat dibuat, tetapi tidak bisa mengukur presisi dan recall dengan jujur.
- **Data dari modul demo Odoo:** tidak mencerminkan pajak Indonesia dan tidak berisi exception yang dibutuhkan.
