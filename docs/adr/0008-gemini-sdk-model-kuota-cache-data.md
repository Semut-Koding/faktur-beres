# ADR-0008: Gemini: SDK, model per peran, kuota, cache, dan data

- Status: Diterima
- Tanggal: 2026-11-09
- Terkait: [SPEC §5.2, §8 (BR-04, BR-15, BR-23, BR-27), §12.2, §14](../SPEC.md), [RENCANA §3.3 dan §7](../RENCANA-TEKNIS.md), [ADR-0001](0001-stack-inti.md), [ADR-0005](0005-llm-memutuskan-kode-menghitung.md), [ADR-0007](0007-data-sintetis-dan-skenario.md), [`data/llm/harga.yaml`](../../data/llm/harga.yaml)

## Konteks
- ADR-0001 memilih Gemini. M3 memakainya untuk dua hal: membaca dokumen (ekstraksi terstruktur dan pembacaan ulang BR-04) dan menilai injeksi lapis kedua (BR-15).
- Key pemilik memakai **tier gratis**. Data yang dikirim dapat dipakai Google untuk meningkatkan produk, dan kuotanya kecil serta berbeda per model. Kuota proyek di AI Studio (dicek pemilik, 9 November 2026):

  | Model | Permintaan per menit | Permintaan per hari |
  |---|---|---|
  | Gemini 3.5 Flash-Lite | 15 | 500 |
  | Gemini 3.8 Flash | 5 | 20 |

  Kuota harian direset pukul **15.00 WIB** (tengah malam waktu Pasifik), bukan 00.00 WIB.
- SDK `google-genai` 3.0.0 (6 November 2026) membawa perubahan besar: versi API bawaan menjadi `v1`, klien HTTP bawaan menjadi `httpx2`, dan `httpx` tidak lagi dipasang otomatis.
- Satu run ekstraksi tiga kelompok (64 dokumen) butuh lebih dari seratus panggilan, dan evaluasi 40 skenario × 3 (M5) lebih banyak lagi.

## Keputusan
1. **SDK.** `google-genai>=2.34,<3` (terpasang 2.34.0). Versi API ditulis eksplisit `v1beta`, retry bawaan SDK dimatikan (`HttpRetryOptions(attempts=1)`) supaya setiap percobaan tercatat sendiri, dan batas waktu 120 detik (`GEMINI_TIMEOUT_SECONDS`). Upgrade ke 3.x ditulis sebagai catatan ADR setelah diuji.
2. **Model per peran.**
   - Model **utama** `gemini-3.5-flash-lite` membaca dokumen pada percobaan pertama dan menjadi penilai injeksi.
   - Peran **kuat** dipakai untuk pembacaan ulang BR-04 dan menjadi cadangan model utama; bila model kuat tidak tersedia saat pembacaan ulang, model utama yang dipakai. Di tier gratis peran ini juga diisi `gemini-3.5-flash-lite` (keputusan pemilik, 9 November 2026). `GEMINI_MODEL_STRONG` tetap menjadi titik konfigurasi untuk model yang lebih kuat bila kuotanya memungkinkan.
   - Model pratinjau, eksperimental, dan alias seperti `-latest` tidak dipakai.
   - **Tanpa model cadangan selama kedua peran sama.** Bila Flash-Lite tidak tersedia, tidak ada model lain yang menjawab, dan yang berlaku `LlmUnavailable` (BR-27). Run evaluasi berhenti dan dilanjutkan dengan `--lanjut`. M4 menambah peran **cadangan** yang terpisah dari peran kuat, dengan batas waktu sendiri dan model yang kuotanya terpisah.
   - **Uji banding** (9 November 2026, 10 dokumen tetap, setiap model sendirian, segar, tanpa penilai):

     | Model | Dokumen | Akurasi field kritis | Dokumen sempurna | Panggilan | Dibaca ulang | Token masuk / keluar | Latensi median / maks | Biaya setara |
     |---|---|---|---|---|---|---|---|---|
     | `gemini-3.5-flash-lite` | 10 | 100% (224/224) | 10/10 | 11 | 1 | 16.097 / 7.207 | 3,8 / 12,0 detik | USD 0,023 |
     | `gemini-3.8-flash` | 0 dari 10 | — | — | 4 percobaan | — | — | lihat di bawah | — |

     Flash 3.8 berhenti di dokumen pertama (COC-01): empat kali timeout 120 detik, lalu `LlmUnavailable`. Diagnosis dengan batas waktu 300 detik pada dokumen yang sama: 503 setelah 7,7 detik, lalu berhasil setelah 194,5 detik (1.457 token masuk, 541 token keluar). Banding Flash tidak dilanjutkan di M3, karena aturannya sudah terpenuhi: akurasi field kritis Flash-Lite (100%) tidak mungkin lebih dari 2 poin persentase di bawah Flash.
   - **`npm run llm:model -- --uji`** (9 November 2026): Flash-Lite OK di setiap percobaan. Flash 3.8 sempat menjawab 503 dan 504 serta timeout, lalu OK pada percobaan berikutnya.
3. **Konfigurasi generasi.**
   - `system_instruction` terpisah dari isi; berkas dikirim inline (`Part.from_bytes`) dengan MIME hasil deteksi magic bytes dan tanpa nama berkas.
   - `response_mime_type="application/json"` dan `response_json_schema` dari model Pydantic. API menerima `maxLength` dan `maxItems` pada daftar teks, tetapi menolak `maxItems` pada daftar objek (HTTP 400 `INVALID_ARGUMENT`, diuji 9 November 2026), jadi batas jumlah baris hanya diperiksa validator.
   - Thinking `low`, bisa diatur per tujuan; `minimal` tidak dipakai.
   - `media_resolution` `high` bila ada dokumen; `max_output_tokens` dibatasi (16.384 untuk ekstraksi, 2.048 untuk penilai); batas waktu per permintaan 120 detik.
   - `temperature`, `top_p`, dan `top_k` tidak diset: dokumentasi Gemini 3 menyarankan nilai bawaan, dan dokumentasi model terbaru menyebut ketiganya usang.
   - `automatic_continuation=False`, supaya satu percobaan adalah satu permintaan HTTP yang terlihat oleh throttle dan audit.
   - `store=False`. Dengan opsi ini, permintaan tidak masuk fitur log proyek (log dan dataset di AI Studio). Ketentuan tier gratis tentang pemakaian data oleh Google tetap berlaku, jadi opsi ini tidak menggantikan aturan data di butir 8.
4. **Galat dan model cadangan.** Keluaran dianggap valid hanya bila `finish_reason` = `STOP` dan teksnya lolos `model_validate_json`.

   | Galat | Penanganan |
   |---|---|
   | 429 kuota per menit | Tunggu sesuai `RetryInfo`, atau backoff eksponensial dengan jitter; maksimal 3 kali, lalu model cadangan. Penantian `RetryInfo` di atas 120 detik langsung ke model cadangan |
   | 429 kuota per hari (`quotaId` memuat `PerDay`) | Model ditandai habis untuk sisa proses, lalu langsung model cadangan |
   | 5xx, 408, timeout, dan galat koneksi | Backoff 2, 4, dan 8 detik ditambah jitter; maksimal 3 kali, lalu model cadangan |
   | Keluaran tidak valid | Diulang sekali di model yang sama, lalu dikembalikan sebagai galat ke pemanggil |
   | 4xx lain (400, 401, 403, 404) | Tanpa retry; galat konfigurasi, dengan status dan pesan API |

   Bila kedua model tidak tersedia, atau satu-satunya model bila kedua peran sama: `LlmUnavailable`, yang di M4 ditangani sesuai BR-27.
5. **Throttle.** Jarak minimum antarpermintaan per model adalah 60/RPM detik, dan panggilan berurutan tanpa paralel. RPM dibaca dari `GEMINI_RPM_PRIMARY` dan `GEMINI_RPM_STRONG` (bawaan 5); bila kedua peran memakai model yang sama, yang dipakai RPM terendah. Pemilik mengisi 12 untuk keduanya, yaitu 80% dari batas Flash-Lite di AI Studio, supaya ada ruang untuk retry.
6. **Cache.** Cache hanya dipakai bila pemanggil memintanya. Kuncinya SHA-256 dari JSON kanonis atas model, tujuan, versi prompt, hash skema, hash instruksi sistem, hash setiap masukan, dan konfigurasi generasi. Hanya jawaban valid yang disimpan; entri yang rusak diabaikan lalu ditulis ulang. Entri berisi teks jawaban dan metadatanya, tidak pernah prompt, dokumen, atau key. Evaluasi berjalan tanpa cache, kecuali `--pakai-cache` untuk pengembangan, dan hasil dari cache ditandai.
7. **Audit dan biaya setara (BR-23).** Satu catatan untuk setiap percobaan atau setiap jawaban dari cache, di logger `faktur_beres.llm` dengan `extra={"llm_call": ...}`, dan juga dikembalikan ke pemanggil. Isinya:
   - waktu, tujuan, peran, model, dan tanda model cadangan;
   - versi prompt dan kunci permintaan (16 digit);
   - percobaan, pemakaian cache, penantian, dan durasi;
   - status, jenis galat, status HTTP, dan `finish_reason`;
   - token masukan, keluaran, thinking, dan total;
   - biaya setara.

   Isi dokumen, prompt, jawaban, dan key tidak pernah dicatat. Biaya setara dihitung dalam USD `Decimal` dari harga tier berbayar di `data/llm/harga.yaml` (sumber dan tanggal tercatat; token thinking dihitung sebagai keluaran). Model tanpa harga dicatat "tidak diketahui", bukan 0. Log DEBUG pustaka HTTP tidak diaktifkan; logger `httpx`, `httpcore`, dan `google_genai` ditahan di WARNING.
8. **Data (tier gratis).** Hanya data sintetis yang dikirim:
   - berkas di `eval/berkas/` dan `eval/ketahanan/berkas/`;
   - frasa set injeksi;
   - berkas buatan tes.

   Alat CLI ekstraksi menolak path di luar repo; pengecualiannya hanya `eval:injeksi --berkas`, untuk set tertutup perencana. Semua alat yang mengirim ke LLM menolak path dengan komponen `_privat` atau `privat`, dan nama berkas tidak pernah dikirim. Prompt tidak memuat label maupun data ERP (diperiksa tes). Prompt disimpan sebagai berkas berversi, dan versi yang sudah ada dikunci tes sidik.
9. **Catatan untuk M4.** Gemini 3 mewajibkan *thought signature* dikembalikan pada percakapan multi-giliran dengan function calling. Graph M4 harus meneruskan bagian jawaban model apa adanya, termasuk `thought_signature`, bukan hanya teksnya.

## Konsekuensi
- Semua panggilan M3 memakai kuota Flash-Lite (500 per hari). Run besar dijadwalkan menurut jendela kuota 15.00 WIB dan bisa dilanjutkan (`eval:ekstraksi --lanjut`).
- Selama kedua peran memakai model yang sama, tidak ada model cadangan. Gangguan Flash-Lite menghentikan run evaluasi, dan di M4 membuat faktur menunggu sesuai BR-27, sampai peran cadangan M4 ada.
- Pembacaan ulang BR-04 memakai model yang sama dengan pembacaan pertama. Syarat dua pembacaan konsisten tetap berlaku, tetapi pembacaan ulang tidak mendapat model yang lebih kuat. Pada run segar 9 November 2026, satu digit NPWP di foto HRG-03 terbaca salah dengan cara yang sama di kedua pembacaan; NPWP itu tidak lolos digit cek, jadi hasilnya `tax_invalid` (jalur B), bukan jalur A yang salah.
- Keluaran LLM tetap bisa berbeda antar-run walaupun skema ketat; BR-04 menerima nilai bermasalah hanya bila dua pembacaan konsisten.
- Upgrade SDK, penggantian model, dan perubahan prompt adalah perubahan yang disengaja dan tercatat: versi prompt baru, catatan ADR, dan run evaluasi ulang.

## Alternatif yang ditolak
- **Flash sebagai model utama:** kuota hariannya (20 permintaan) tidak cukup untuk evaluasi.
- **Flash 3.8 sebagai model kuat di tier gratis:** pada 9 November 2026 ia menjawab 503 dan butuh sekitar 195 detik per dokumen, jauh di atas target kurang dari 2 menit per faktur (SPEC §13), dengan kuota 20 per hari.
- **SDK 3.x:** baru rilis 6 November 2026 dengan perubahan besar; ditunda sampai diuji.
- **`temperature` 0:** tidak disarankan untuk Gemini 3, dan tidak menjamin keluaran yang sama.
- **Files API:** berkas sampai 15 MB cukup dikirim inline, tanpa menyimpan berkas di server.
- **Tier berbayar:** keputusan pemilik, bukan keputusan teknis.
