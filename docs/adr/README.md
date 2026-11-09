# Catatan Keputusan Arsitektur (ADR)

Setiap keputusan penting dicatat sebagai satu berkas bernomor.

**Aturan perubahan**
- **Keputusan** di ADR yang sudah **Diterima** tidak diubah. Bagian Konteks, Keputusan, Konsekuensi, dan Alternatif tetap seperti saat diterima.
- Keputusan yang berubah ditulis sebagai **ADR baru**, lalu status ADR lama diubah menjadi *Digantikan oleh ADR-xxxx*. Perubahan status ini satu-satunya suntingan pada isi lama.
- **Catatan bertanggal boleh ditambahkan** sebagai bagian tersendiri setelah Keputusan, dengan judul yang memuat tanggal, misalnya `## Catatan implementasi (M0, 6 November 2026)`. Isinya hanya jadwal, cara keputusan diterapkan, atau hasil verifikasi; catatan tidak boleh mengubah atau melemahkan keputusan. Bila sebuah catatan ternyata mengubah keputusan, tulis ADR baru.
- Perbaikan salah ketik dan tautan yang rusak boleh langsung dilakukan.

**Status yang dipakai**
- **Diusulkan**: ditulis untuk ditinjau; belum berlaku sampai pemilik produk menerimanya. Isinya boleh diubah selama masih diusulkan.
- **Diterima**: keputusan berlaku.
- **Sementara**: dipakai sampai pemilik produk memutuskan lain; ditinjau ulang pada waktu yang disebut di berkasnya.
- **Digantikan**: tidak berlaku lagi; lihat ADR penggantinya.

| No | Judul | Status | Tanggal |
|---|---|---|---|
| [0001](0001-stack-inti.md) | Stack inti: Python 3.13, LangGraph, FastAPI, Gemini, PostgreSQL + pgvector, React + Vite | Diterima | 2026-11-05 |
| [0002](0002-monorepo-uv-npm.md) | Satu repo: uv untuk Python, npm sebagai penjalan perintah dan workspace dashboard | Diterima | 2026-11-05 |
| [0003](0003-podman-proyek-terpisah.md) | Podman rootless, proyek compose `fakturberes`, dan port +20000, berdampingan dengan DombaTek | Diterima | 2026-11-05 |
| [0004](0004-odoo-19-json2-api.md) | Odoo 19 Community lewat External JSON-2 API dengan user bot dan dua API key | Diterima | 2026-11-05 |
| [0005](0005-llm-memutuskan-kode-menghitung.md) | LLM merencanakan dan memutuskan di dalam pagar; kode menghitung dan menjalankan aksi | Diterima | 2026-11-05 |
| [0006](0006-persetujuan-dan-idempotensi.md) | Persetujuan lewat interrupt, aksi di node terpisah, dan kunci idempoten | Diterima | 2026-11-05 |
| [0007](0007-data-sintetis-dan-skenario.md) | Data sintetis, dataset tunggal, dan katalog 40 skenario berlabel | Diterima | 2026-11-05 |
| [0008](0008-gemini-sdk-model-kuota-cache-data.md) | Gemini: SDK, model per peran, kuota, cache, dan data | Diterima | 2026-11-09 |

## Templat

```markdown
# ADR-NNNN: Judul singkat

- Status: Diusulkan | Diterima | Sementara | Digantikan oleh ADR-xxxx
- Tanggal: YYYY-MM-DD
- Terkait: dokumen/bagian yang terdampak

## Konteks
Masalah dan batasan yang mendorong keputusan.

## Keputusan
Apa yang dipilih, sejelas mungkin.

## Konsekuensi
Dampak positif, negatif, dan pekerjaan lanjutan.

## Alternatif
Opsi lain dan alasan tidak dipilih.
```
