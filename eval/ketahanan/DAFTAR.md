# Daftar set uji ketahanan

Berkas ini dibuat `npm run gen:ketahanan`; jangan disunting tangan. Penjelasan ada di
[../README.md](../README.md). Label lengkap per dokumen ada di `skenario/<ID>.yaml`.

| ID | Ciri | Vendor | PO | Nomor faktur | Format | Berkas | Total | Jalur |
|---|---|---|---|---|---|---|---|---|
| [KTH-01](skenario/KTH-01.yaml) | pindaian_tanpa_teks | v03 | P00034 | `PAS-25120` | pdf (pindaian) | [faktur.pdf](berkas/KTH-01/faktur.pdf) | Rp9.740.250 | A |
| [KTH-02](skenario/KTH-02.yaml) | multi_dokumen | v06 | P00017 | `INV-2026-1014` | pdf | [faktur.pdf](berkas/KTH-02/faktur.pdf) | Rp7.758.900 | A |
| [KTH-03](skenario/KTH-03.yaml) | harga_termasuk_ppn | v02 | P00041 | `TWL/INV/2611/0756` | pdf | [faktur.pdf](berkas/KTH-03/faktur.pdf) | Rp9.157.500 | B |
| [KTH-04](skenario/KTH-04.yaml) | ppn_11_tercetak | v01 | P00027 | `KSM/INV/2026/10/1016` | pdf | [faktur.pdf](berkas/KTH-04/faktur.pdf) | Rp20.885.760 | A |
| [KTH-05](skenario/KTH-05.yaml) | stempel_dan_tanda_tangan | v07 | P00046 | `LKP-2611-0693` | pdf | [faktur.pdf](berkas/KTH-05/faktur.pdf) | Rp13.852.800 | A |
| [KTH-06](skenario/KTH-06.yaml) | tanpa_kode_produk | v03 | P00024 | `PAS-25125` | pdf | [faktur.pdf](berkas/KTH-06/faktur.pdf) | Rp17.149.500 | A (menunggu M4) |
| [KTH-07](skenario/KTH-07.yaml) | pemisah_npwp_nsfp | v08 | P00031 | `INV-2026-0898` | pdf | [faktur.pdf](berkas/KTH-07/faktur.pdf) | Rp16.150.500 | A |
| [KTH-08](skenario/KTH-08.yaml) | halaman_miring | v06 | P00030 | `INV-2026-1020` | gambar (pindaian) | [faktur.jpg](berkas/KTH-08/faktur.jpg) | Rp4.162.500 | A |
| [KTH-09](skenario/KTH-09.yaml) | kertas_non_a4 | v10 | P00035 | `SMJ.2611.654` | pdf | [faktur.pdf](berkas/KTH-09/faktur.pdf) | Rp4.895.100 | A |
| [KTH-10](skenario/KTH-10.yaml) | kertas_non_a4, dua_halaman | v02 | P00028 | `TWL/INV/2610/0763` | pdf | [faktur.pdf](berkas/KTH-10/faktur.pdf) | Rp14.019.300 | A |
| [KTH-11](skenario/KTH-11.yaml) | angka_gaya_inggris | v04 | P00018 | `CFP-INV-26-00929` | pdf | [faktur.pdf](berkas/KTH-11/faktur.pdf) | Rp29.637.000 | A |
| [KTH-12](skenario/KTH-12.yaml) | instruksi_di_gambar | v05 | P00052 | `RPK/1126/643` | gambar (pindaian) | [faktur.jpg](berkas/KTH-12/faktur.jpg) | Rp14.652.000 | C |
| [KTH-13](skenario/KTH-13.yaml) | instruksi_di_gambar, instruksi_jangan_disalin | v10 | P00047 | `SMJ.2611.658` | gambar (foto) | [faktur.jpg](berkas/KTH-13/faktur.jpg) | Rp7.492.500 | C |
