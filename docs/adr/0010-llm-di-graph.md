# ADR-0010: LLM di graph: peran cadangan, batas waktu per tujuan, dan pemanggilan alat di dalam pagar

- Status: Diusulkan
- Tanggal: 2026-11-10
- Terkait: [SPEC §5.1–§5.3, §8 (BR-06, BR-07, BR-15, BR-27), §14](../SPEC.md), [ADR-0005](0005-llm-memutuskan-kode-menghitung.md), [ADR-0008](0008-gemini-sdk-model-kuota-cache-data.md)

## Konteks
ADR-0008 mencatat bahwa selama peran utama dan kuat memakai model yang sama, tidak ada model cadangan. M4a menambah pemanggilan alat oleh LLM di `identify` dan pemetaan baris di `match`. Kuota tier gratis kecil: Flash-Lite 15 permintaan per menit dan 500 per hari, `gemini-3.6-flash` 5 per menit dan 20 per hari. Model Gemini 3 mewajibkan *thought signature* dikembalikan pada percakapan multi-giliran dengan function calling.

## Keputusan
1. **Peran cadangan.** `ModelRole.FALLBACK` memakai `gemini-3.6-flash`, paling akhir di setiap rantai: utama → kuat → cadangan, atau kuat → utama → cadangan, dan model yang sama muncul sekali. Di model ini 5xx, 408, timeout, dan galat koneksi hanya dicoba ulang satu kali. Audit menandai jawabannya. Setiap permintaan bisa menolak peran ini (`allow_fallback=False`). `gemini-3.7-flash` tidak dipakai, karena endpoint itu diarahkan ke `gemini-3.8-flash`.
2. **Batas waktu per tujuan, dipasang per permintaan** lewat `http_options` di konfigurasi permintaan, yang digabung SDK dengan opsi klien (diperiksa pada `google-genai` 2.34). Berisi dokumen: `GEMINI_TIMEOUT_SECONDS`, atau `GEMINI_TIMEOUT_FALLBACK_SECONDS` di model cadangan. Tanpa dokumen: `GEMINI_TIMEOUT_TEXT_SECONDS` di semua model.
3. **Function calling multi-giliran.** Deklarasi dibuat dari skema Pydantic, mode `ANY` dengan `allowed_function_names`, tanpa function calling otomatis SDK. Giliran model masuk riwayat persis seperti diterima, sehingga `thought_signature` kembali tanpa diubah dan tidak pernah memakai signature pengganti. Panggilan paralel dijawab bersama pada satu giliran, berurutan. Throttle, retry, audit, dan cache berlaku per giliran; kunci cache mencakup seluruh riwayat dan deklarasi, dan entri menyimpan giliran model (base64), tidak pernah prompt.
4. **Pergantian model di tengah percakapan** mengulang percakapan dari awal dengan sesi alat baru, karena signature tidak dicampur antar-model. Anggaran menghitung semua panggilan.
5. **Perencana `identify` bersifat penasihat.** Alatnya hanya membaca dan terikat pada vendor yang sudah teridentifikasi, jadi tidak ada parameter `partner_id`; ini lebih ketat dari contoh di SPEC §5.2. Batasnya 20 PO × 20 baris, 6 panggilan model, dan 12 eksekusi alat. Fungsi tidak dikenal dan argumen tidak valid tidak dijalankan. Nomor PO yang diusulkan harus pernah muncul di hasil alat pada percakapan yang sama. BR-06 tetap memutuskan; kesesuaian usulan dengan aturan dicatat di jejak. Penjelasannya hanya catatan internal. Perencana tidak memakai peran cadangan, dan bila LLM tidak tersedia aturan tetap berjalan.
6. **Pemetaan baris (BR-07).** Hanya baris `no_match` yang dikirim, dan hanya baris PO yang belum terpakai dan produknya tunggal yang ditawarkan, tanpa harga dan kuantitas. Ref harus salah satu yang ditawarkan, satu baris PO untuk satu baris faktur, `null` boleh. Jawaban yang ditolak guard diminta sekali lagi, lalu diperlakukan seperti `LlmUnavailable` (BR-27). `domain` tetap tanpa LLM: `match_lines` menerima pemetaan yang sudah divalidasi dan memeriksa kembali keberadaan baris PO.
7. **Prompt `extraction-v2`.** Kode barang hanya diisi bila benar-benar tercetak, dan kolom nomor urut bukan kode barang. `extraction-v1` tetap ada dan tetap dikunci tes sidik.

## Konsekuensi
- Kuota cadangan 20 per hari hanya cukup untuk gangguan singkat, bukan untuk run evaluasi.
- Perencana menambah sampai 6 panggilan per faktur tanpa PO, hanya untuk jejak dan paket bukti; keputusannya tetap dari aturan.
- Pemetaan baris yang gagal membuat faktur `failed`, sehingga gangguan LLM bisa menunda faktur yang barisnya tanpa kode.

## Alternatif yang ditolak
- **Perencana yang menetapkan PO:** bertentangan dengan ADR-0005.
- **Function calling otomatis SDK:** throttle, audit, dan anggaran tidak bisa diterapkan per giliran.
- **Harga dan kuantitas di prompt pemetaan:** membuat model memilih berdasarkan angka, padahal angka diperiksa BR-08 dan BR-09.
