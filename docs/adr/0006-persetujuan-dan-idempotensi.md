# ADR-0006: Persetujuan lewat interrupt, aksi di node terpisah, dan kunci idempoten

- Status: Diterima
- Tanggal: 2026-11-05
- Terkait: [SPEC §6, BR-18 sampai BR-22](../SPEC.md#8-aturan-bisnis), [ADR-0004](0004-odoo-19-json2-api.md), proposal §7

## Konteks
- Jalur C menunggu keputusan manusia, dan jalur B menunggu balasan vendor. Keduanya bisa memakan waktu berhari-hari.
- Saat graph dilanjutkan dari checkpoint, node yang terputus dijalankan ulang dari awal. Aksi eksternal di node yang sama dengan interrupt bisa terulang.
- JSON-2 tidak mendukung transaksi lintas panggilan, sehingga proses bisa gagal di antara "draft dibuat" dan "berkas dilampirkan".
- Evaluasi menghitung kesalahan aksi, dengan target 0: tagihan ganda, email ganda, atau aksi sebelum persetujuan.

## Keputusan
- **Interrupt LangGraph** untuk persetujuan C, dengan paket bukti di payload interrupt. Keputusan approver dikirim lewat API dan melanjutkan thread yang sama.
- Faktur jalur B berhenti dengan status `awaiting_vendor`. Balasan vendor melanjutkan thread yang sama mulai node `extract`.
- **Setiap aksi eksternal berada di node `act`**, terpisah dari node yang berisi interrupt. Node keputusan tidak pernah punya efek samping eksternal.
- **Kunci idempoten** dibentuk dari `invoice_id`, jenis aksi, target, dan versi isi, lalu disimpan di tabel `actions` bersama status dan hasilnya. Sebelum menjalankan aksi:
  1. bila kunci sudah tercatat berhasil, hasilnya dipakai ulang;
  2. untuk draft tagihan, Odoo juga diperiksa dengan kunci alami vendor + `ref`;
  3. langkah lanjutan (lampiran, catatan chatter) masing-masing punya kunci sendiri.
- Setelah aksi, hasilnya diverifikasi dengan membaca ulang (mis. total draft tagihan). Kegagalan verifikasi menjadi alasan C, bukan diabaikan.

## Konsekuensi
- Resume aman diulang: graph yang dilanjutkan dua kali tidak membuat tagihan atau email kedua.
- Harness evaluasi bisa menguji idempotensi dengan menyimulasikan persetujuan pada skenario C (COC-04, COC-05).
- Ada tabel tambahan dan sedikit latensi untuk pemeriksaan sebelum aksi.

## Alternatif
- **Aksi di node yang sama dengan keputusan:** kode lebih pendek, tetapi berisiko aksi ganda saat resume.
- **Konfirmasi lewat email atau chat tanpa interrupt:** state tersebar di luar graph dan sulit diaudit.
- **Mengandalkan pemeriksaan duplikat Odoo saja:** tidak mencakup email dan langkah lanjutan.
