# eval: skenario uji Faktur Beres

Folder ini berisi 40 skenario uji berlabel (SPEC §15.3) untuk evaluasi agent: 8 kategori × 5 varian, masing-masing dijalankan tiga kali (SPEC §12). **Semua dokumen di sini sintetis.** Perusahaan, vendor, NPWP, rekening, alamat, dan email berasal dari [`data/seed/dataset.yaml`](../data/seed/dataset.yaml) atau dibuat generator, dan setiap halaman serta gambar memuat tanda "DOKUMEN SINTETIS – UNTUK PENGUJIAN".

## Struktur

| Path | Isi | Dibuat oleh |
|---|---|---|
| `skenario/<ID>.yaml` | Label satu skenario: dokumen, kanal, kebenaran isi dokumen, simulasi balasan vendor atau keputusan approver, dan harapan state akhir | `npm run gen:faktur` |
| `berkas/<ID>/faktur.*` | Faktur yang dikirim atau diunggah: PDF, gambar (JPEG/PNG), atau UBL 2.1 (XML) | `npm run gen:faktur` |
| `berkas/<ID>/balasan.*` | Lampiran faktur perbaikan dari vendor (11 skenario jalur B) | `npm run gen:faktur` |
| `DAFTAR.md` | Tabel ringkas 40 skenario | `npm run gen:faktur` |
| `hasil/` | Hasil evaluasi (M5), tidak di-commit | harness |
| `README.md` | Berkas ini | ditulis tangan |

## Membuat ulang dan memeriksa

```bash
npm run gen:faktur    # tulis ulang skenario/, berkas/, dan DAFTAR.md
npm run gen:periksa   # buat ulang ke direktori sementara dan bandingkan SHA-256 dengan isi folder ini
```

Generator deterministik: tanpa waktu sekarang atau UUID acak, seed acak diturunkan dari SHA-256 ID skenario, ReportLab memakai `invariant = 1`, dan gambar disimpan tanpa EXIF dengan kompresi tetap. Generator tidak memanggil Odoo maupun LLM. Hasilnya di-commit supaya evaluasi bisa dijalankan tanpa generator; `gen:periksa` memastikan berkas di repo sama persis dengan keluaran generator. Kesamaan byte per byte dijamin untuk versi pustaka di `uv.lock` dan platform yang sama.

## Format label (ringkas)
Format lengkap dan aturan penilaian ada di [SPEC §15.4](../docs/SPEC.md#154-format-label); pemetaan UBL di [SPEC §15.6](../docs/SPEC.md#156-pemetaan-ubl).

- **Identitas:** `id`, `kategori`, `varian`, `format` dan `gaya_gambar`, `kanal`, `vendor`, `po_rujukan` (PO yang dimaksud skenario, bukan yang tercetak).
- **Berkas:** `berkas` dengan `path`, `nama_kirim` (nama saat dikirim atau diunggah), dan `sha256`. Skenario kanal email punya `email` (pengirim, penerima, subjek, isi, tanggal, Message-ID).
- **`kebenaran_dokumen`:** nilai persis seperti tercetak, termasuk yang sengaja salah; dipakai mengukur akurasi ekstraksi (M3).
- **`simulasi`:** balasan vendor (`lampiran` atau `teks`) dan keputusan approver (`setujui` atau `tolak`), beserta `uji_idempotensi`.
- **`harapan`:** jalur awal, alasan wajib dan alasan yang boleh muncul, kondisi saat agent pertama kali berhenti, status akhir, tagihan Odoo yang diharapkan, email keluar, dan larangan.

## Aturan
- **Label tidak pernah diberikan ke agent** (ADR-0007). Harness membaca label untuk mengirim berkas, menyimulasikan balasan atau keputusan, dan menilai state akhir; agent hanya menerima berkas, email, dan balasan.
- ID skenario, kunci kategori, dan kata yang menyebut jawaban tidak muncul di isi berkas, metadata PDF, nama berkas kirim, subjek, maupun isi email. Tes `tests/test_generator_labels.py` memindainya. Path di repo boleh memuat ID skenario karena agent tidak pernah melihatnya.
- Jangan menyunting berkas di `skenario/`, `berkas/`, atau `DAFTAR.md` dengan tangan; ubah katalog atau generator lalu jalankan `npm run gen:faktur`.
