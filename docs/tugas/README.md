# Tugas per milestone

Cara kerja proyek ini:

1. **Claude (chat perencana)** menyusun prompt tugas untuk satu milestone, berdasarkan SPEC, RENCANA-TEKNIS, dan ADR.
2. **Pemilik (Farhan)** menempelkan prompt itu ke **Claude Code** di folder repo.
3. **Claude Code** menyimpan prompt ke folder ini (`Mx.md`), mengerjakannya dengan commit kecil, lalu menulis laporan dengan format di bawah.
4. Pemilik meneruskan laporan ke chat perencana. Hasilnya ditinjau bersama, lalu prompt berikutnya disusun.

Prompt yang sudah disimpan tidak diubah. Koreksi atau tambahan ditulis sebagai prompt baru, misalnya `M0-lanjutan.md`.

| Berkas | Isi | Status |
|---|---|---|
| [`M0.md`](M0.md) | Fondasi repo, stack Podman, inisialisasi dan seed Odoo | Selesai |
| [`M1.md`](M1.md) | Catatan tinjauan M0 dan generator faktur uji 40 skenario | Selesai |
| [`M2.md`](M2.md) | Catatan tinjauan M1 dan lapisan alat: grup Odoo agent, aturan deterministik, `odoo_tools`, evaluasi orakel | Selesai |
| [`M3.md`](M3.md) | Catatan tinjauan M2, ekstraksi Gemini, parser UBL, injeksi dua lapis, set uji ketahanan | Berjalan |

## Format laporan
Laporan ditulis di akhir tugas dalam bahasa Indonesia, dengan bagian berikut secara berurutan:

1. **Ringkasan**: 2–4 kalimat tentang hasil, termasuk bagian yang belum selesai.
2. **Pemeriksaan awal**: tabel versi alat, port, kontainer lain yang sedang berjalan, dan ruang disk.
3. **Commit**: tabel `Hash | Butir` untuk setiap commit, berurutan.
4. **Bukti verifikasi**: keluaran ringkas dari perintah pemeriksaan (mis. `odoo:check`, `npm run check`), tanpa rahasia.
5. **Perbedaan dari dokumen**: hal yang ternyata berbeda dari SPEC, RENCANA-TEKNIS, atau ADR, beserta dokumen yang sudah diperbarui.
6. **Keputusan yang diambil sendiri**: pilihan kecil yang diputuskan selama tugas beserta alasannya.
7. **Pertanyaan untuk pemilik**: hal yang butuh keputusan pemilik.
8. **Usulan langkah berikutnya**.

Rahasia (API key, sandi) tidak pernah ditulis di laporan; ganti dengan `***`.
