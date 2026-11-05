# ADR-0005: LLM merencanakan dan memutuskan di dalam pagar; kode menghitung dan menjalankan aksi

- Status: Diterima
- Tanggal: 2026-11-05
- Terkait: [SPEC §5.3, §6, BR-16, BR-17](../SPEC.md#53-pembagian-kerja-llm-dan-kode), proposal §5 dan §7

## Konteks
- Risiko terbesar agent keuangan adalah angka yang salah dan aksi yang tidak bisa ditarik kembali.
- Juri menilai otonomi dan penalaran. Agent yang hanya menjalankan aturan tetap tidak menunjukkan penalaran, sedangkan agent yang menyerahkan semuanya ke LLM tidak bisa dipercaya untuk uang.
- LLM kuat membaca dokumen tidak terstruktur dan merencanakan pencarian, tetapi bisa salah berhitung dan bisa dimanipulasi isi dokumen.

## Keputusan
- **Kode menghitung semuanya:** subtotal, DPP nilai lain, PPN, selisih, toleransi, duplikat, rekening, dan ambang. LLM tidak pernah diminta menghitung angka yang dipakai untuk keputusan.
- **Kode menetapkan pemicu jalur wajib dan prioritasnya** (C > B > A). Jalur A hanya mungkin bila semua pemeriksaan kode lulus.
- **LLM merencanakan dan memilih di dalam pagar:**
  - membaca PDF dan foto ke skema;
  - merencanakan pencarian vendor dan PO, lalu memilih alat dan parameternya;
  - memetakan deskripsi baris ke produk, yang hasilnya divalidasi kode;
  - memilih di antara jalur yang diizinkan kode untuk kasus ambigu;
  - menulis penjelasan, rincian email, dan paket bukti dari hasil yang sudah divalidasi.
- **Guard di kode** menolak keputusan LLM di luar himpunan yang diizinkan, parameter alat yang tidak valid, dan alamat email yang tidak diizinkan. Penolakan dicatat di audit.
- Isi dokumen, balasan vendor, dan hasil RAG selalu diperlakukan sebagai **data**, tidak pernah sebagai instruksi.

## Konsekuensi
- Angka di draft tagihan dan email bisa diaudit sampai ke kode yang menghitungnya.
- Penalaran agent tetap terlihat di jejak (rencana, pilihan alat, dan alasan jalur), sehingga kriteria otonomi dan penalaran terpenuhi tanpa menyerahkan kendali angka.
- Setiap aturan yang dihitung kode wajib punya tes unit (M2).
- Menambah jenis keputusan baru berarti menambah aturan di kode lebih dulu, baru kemudian prompt.

## Alternatif
- **LLM penuh** (LLM menghitung dan memutuskan sendiri): demo lebih cepat, tetapi tidak bisa dipercaya dan sulit diuji.
- **Aturan penuh tanpa LLM:** deterministik, tetapi tidak bisa membaca dokumen tidak terstruktur dan tidak menunjukkan kemampuan agent.
