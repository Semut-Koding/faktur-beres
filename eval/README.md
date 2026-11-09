# eval: skenario uji Faktur Beres

Folder ini berisi 40 skenario uji berlabel (SPEC §15.3) untuk evaluasi agent: 8 kategori × 5 varian, masing-masing dijalankan tiga kali (SPEC §12). **Semua dokumen di sini sintetis.** Perusahaan, vendor, NPWP, rekening, alamat, dan email berasal dari [`data/seed/dataset.yaml`](../data/seed/dataset.yaml) atau dibuat generator, dan setiap halaman serta gambar memuat tanda "DOKUMEN SINTETIS – UNTUK PENGUJIAN".

## Struktur

| Path | Isi | Dibuat oleh |
|---|---|---|
| `skenario/<ID>.yaml` | Label satu skenario: dokumen, kanal, kebenaran isi dokumen, simulasi balasan vendor atau keputusan approver, dan harapan state akhir | `npm run gen:faktur` |
| `berkas/<ID>/faktur.*` | Faktur yang dikirim atau diunggah: PDF, gambar (JPEG/PNG), atau UBL 2.1 (XML) | `npm run gen:faktur` |
| `berkas/<ID>/balasan.*` | Lampiran faktur perbaikan dari vendor (11 skenario jalur B) | `npm run gen:faktur` |
| `DAFTAR.md` | Tabel ringkas 40 skenario | `npm run gen:faktur` |
| `hasil/` | Hasil evaluasi orakel (M2) dan evaluasi penuh (M5), tidak di-commit | `npm run eval:orakel`, harness |
| `README.md` | Berkas ini | ditulis tangan |

## Membuat ulang dan memeriksa

```bash
npm run gen:faktur    # tulis ulang skenario/, berkas/, dan DAFTAR.md
npm run gen:periksa   # buat ulang ke direktori sementara dan bandingkan isinya dengan folder ini
```

Generator deterministik: tanpa waktu sekarang atau UUID acak, seed acak diturunkan dari SHA-256 ID skenario, ReportLab memakai `invariant = 1`, dan gambar disimpan tanpa EXIF dengan kompresi tetap. Generator tidak memanggil Odoo maupun LLM. Hasilnya di-commit supaya evaluasi bisa dijalankan tanpa generator. `gen:periksa` memastikan berkas di repo sama **isinya** dengan keluaran generator: PDF dibandingkan per halaman (ukuran, teks, dan render 50 DPI), gambar per piksel setelah didekode, dan label setelah di-parse tanpa nilai `sha256` (nilai itu tetap harus sama dengan hash berkas yang di-commit). Berkas lain dibandingkan byte. Byte berkas terkompresi bisa berbeda antar-mesin karena keluaran zlib berbeda walaupun isinya sama; di mesin yang sama dengan versi pustaka di `uv.lock` hasilnya identik byte. Keluaran `gen:periksa` menyebut jumlah berkas yang identik byte, identik isi, dan berbeda; hanya yang berbeda yang membuatnya gagal. `npm run gen:periksa -- --byte` membandingkan byte per byte.

## Evaluasi orakel

```bash
npm run eval:orakel            # ERP dari dataset (luring), tanpa Odoo
npm run odoo:reset             # Odoo kembali ke kondisi seed
npm run eval:orakel -- --odoo  # ERP dari Odoo hasil seed, dibaca dengan key agent (baca saja)
```

Evaluasi orakel ([SPEC §12.1](../docs/SPEC.md#121-metode)) menjalankan aturan deterministik pada `kebenaran_dokumen` setiap label, seolah ekstraksinya sempurna. Untuk setiap skenario orakel:

1. mengubah dokumen menjadi `InvoiceData`, kanal dan email menjadi konteks, dan teks bebas menjadi fragmen untuk pemeriksaan injeksi;
2. menjalankan `analyse()` dan mengambil jalur bawaan;
3. menyimulasikan kelanjutannya seperti agent: draft tagihan (A); email ke alamat penagihan vendor di ERP lalu balasan vendor, yang hanya diterima dari alamat itu (B); atau keputusan approver, termasuk persetujuan kedua pada uji idempotensi (C);
4. menilai hasilnya dengan aturan SPEC §15.4 yang bisa diperiksa tanpa sistem penuh: jalur awal, alasan, status akhir, tagihan baru sebelum simulasi, email ke vendor, tagihan Odoo yang diharapkan (vendor, `ref`, total ±Rp1 dari tarif kebijakan, dan setiap baris), serta larangan yang bisa diperiksa.

`harapan`, `kategori`, `varian`, `vendor`, dan `po_rujukan` hanya dipakai untuk menilai. Satu-satunya pengecualian adalah cek silang balasan teks: nomor PO dari isi balasan harus tunggal dan sama dengan `po_rujukan`.

**Arti hasilnya.** Targetnya 40/40 di kedua mode, dan mode `--odoo` harus memberi hasil yang sama dengan mode luring (hal itu juga diperiksa). Orakel adalah batas atas evaluasi penuh (M5): agent yang sesungguhnya bisa kalah karena ekstraksi atau keputusan LLM, tetapi tidak boleh kalah karena aturan. Bila orakel gagal, yang salah adalah aturan, dataset, atau label, bukan ekstraksi. Tabel 40 baris tampil di terminal, dan rinciannya (alasan, langkah simulasi, tagihan rencana, hasil setiap pemeriksaan) ditulis ke `eval/hasil/orakel-luring.json` atau `eval/hasil/orakel-odoo.json`. Kode keluar bukan nol bila ada skenario yang gagal. `npm run check` ikut menjalankan mode luring (`tests/test_orakel.py`).

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
