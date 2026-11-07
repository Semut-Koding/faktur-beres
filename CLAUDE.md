# CLAUDE.md

Aturan kerja untuk agen koding (Claude Code) di repo **Faktur Beres**. Baca seluruhnya sebelum bekerja.

## Konteks singkat
- Faktur Beres adalah AI agent yang menuntaskan *exception* faktur pemasok di proses *accounts payable*: ekstraksi, pencocokan tiga arah dengan Odoo, lalu salah satu dari tiga jalur (A draft tagihan, B klarifikasi vendor, C persetujuan approver).
- Karya lomba **GTNIC 2026, kategori AI Agent**. Batas pengumpulan **30 November 2026** (target kirim 29 November). Ketentuan dan bobot penilaian ada di [docs/lomba/gtnic-2026.md](docs/lomba/gtnic-2026.md): 70% nilai ada di otonomi agent, penalaran, penggunaan alat, dan arsitektur. Pakai itu saat memilih pendekatan teknis.
- Pemilik repo: Farhan Ginting. Tugas datang sebagai prompt per milestone ([docs/tugas/](docs/tugas/README.md)), dan setiap tugas diakhiri laporan dengan format di sana.
- Dokumen acuan, dibaca sesuai kebutuhan tugas: [SPEC](docs/SPEC.md) (apa yang dibangun), [RENCANA-TEKNIS](docs/RENCANA-TEKNIS.md) (bagaimana), [ADR](docs/adr/README.md) (keputusan yang mengikat), [lokal-podman](docs/deploy/lokal-podman.md) (stack lokal).

## Bahasa
- Dokumen, teks antarmuka, pesan commit, dan laporan ditulis dalam **bahasa Indonesia**.
- Nama berkas kode, identifier, nilai enum, dan komentar kode dalam **bahasa Inggris**. Istilah domain Indonesia yang tidak punya padanan tepat boleh dipakai apa adanya (`npwp`, `nsfp`, `dpp_nilai_lain`).

## Mesin pengembang dipakai bersama DombaTek
Laptop yang sama menjalankan proyek DombaTek, sering **bersamaan**. Stack DombaTek (proyek compose `dombatek`) berisi data pengembangan yang tidak boleh rusak.
- Proyek compose Faktur Beres hanya **`fakturberes`**. Percobaan yang butuh stack terpisah memakai `fakturberes-<nama>` (mis. `podman compose -p fakturberes-verify ...`) dan dimatikan setelah selesai.
- Nyalakan dan matikan stack hanya lewat `npm run services:up` dan `npm run services:down`. Data Odoo dikembalikan ke kondisi seed hanya lewat `npm run odoo:reset`.
- Jangan menyentuh kontainer, pod, volume, jaringan, image, atau secret yang namanya tidak berawalan `fakturberes`. Khususnya, jangan menghentikan, menghapus, atau membuat ulang apa pun milik `dombatek` atau `dombatek-*`.
- Dilarang: `podman system reset`, `podman system prune`, `podman volume prune`, `podman container prune`, `podman image prune`, `podman volume rm`, `podman compose down -v`, `podman rm -f` untuk kontainer yang bukan milik `fakturberes`, dan opsi `--root` atau `--runroot`.
- Jangan mengubah konfigurasi Podman global (`~/.config/containers/`), sysctl, unit systemd atau Quadlet, firewall, atau paket sistem. Bila sesuatu butuh `sudo`, berhenti dan minta pemilik menjalankannya.
- Port Faktur Beres = port standar layanan **+20000**, diikat ke `127.0.0.1` (tabel di [RENCANA-TEKNIS §4.3](docs/RENCANA-TEKNIS.md#43-kontainer-lokal-dan-port)). Port DombaTek (3000, 3900–3903, 5432, 8081) tidak boleh dipakai. Bila port yang dibutuhkan sudah terpakai, berhenti dan laporkan; jangan mematikan proses lain.
- Pekerjaan berat (mis. evaluasi 40 skenario × 3) dijalankan setelah memberi tahu pemilik, karena DombaTek mungkin sedang berjalan.

## Path dengan spasi
Path repo berada di bawah `.../02 - Proyek Pribadi/02 - Kompetisi/...` dan mengandung spasi.
- Selalu kutip path di shell (`"$PWD"`, `"$(git rev-parse --show-toplevel)"`). Jangan membangun perintah dengan path tanpa kutip.
- Jalankan alat Python lewat `uv run python -m <modul>` (mis. `uv run python -m pytest`) bila skrip konsol di `.venv/bin` bermasalah karena spasi di shebang.

## Data dan rahasia
- **Semua data sintetis.** Jangan pernah memasukkan faktur, nama perusahaan, NPWP, rekening, email, atau data orang yang nyata, termasuk sebagai contoh di tes atau dokumen. Sumber data tunggal: [data/seed/dataset.yaml](data/seed/dataset.yaml).
- Tier gratis Gemini dapat memakai data untuk peningkatan produk; karena itu hanya data sintetis yang boleh dikirim ke LLM.
- **Label evaluasi tidak boleh bocor ke agent.** ID skenario, kategori, dan jawaban yang diharapkan tidak boleh muncul di Odoo, di berkas faktur, di dokumen kebijakan untuk RAG, atau di data apa pun yang dibaca agent saat berjalan. Label hanya ada di `eval/skenario/`.
- `.env` tidak pernah di-commit. Jangan menampilkan isi rahasia (API key, sandi) di laporan, log, atau keluaran perintah; tulis `***`. Jalankan `npm run guard:secrets` sebelum commit dan push.

## Dokumen nyata dan berkas pribadi
- Dokumen nyata (faktur, faktur pajak, atau dokumen usaha milik siapa pun) tidak pernah masuk repo dan tidak pernah diproses agen. Bila pemilik menyimpan dokumen seperti itu, letaknya di folder `_privat` di luar repo.
- Jangan membuka, membaca, menyalin, mengonversi, atau mengirim isi folder `_privat` atau `privat` (di mana pun letaknya) ke LLM, walaupun hanya untuk melihat contoh.
- Dokumen nyata tidak dipakai sebagai data uji, contoh di tes, atau bahan dokumen. Kebutuhan realisme dipenuhi dokumen sintetis (set uji ketahanan, RENCANA-TEKNIS §6).
- Bila menemukan arsip `.zip` atau berkas yang tampak sebagai dokumen nyata di dalam repo (nama perusahaan, NPWP, atau rekening yang tidak ada di dataset), jangan membukanya. Berhenti dan laporkan nama berkasnya saja.
- `.gitignore` memuat `/*.zip`, `/_privat/`, dan `/privat/`, dan `guard:secrets` menolak berkas `.zip` serta path `_privat/` atau `privat/` yang di-stage. Pengaman ini tidak menggantikan aturan di atas.

## Odoo
- Integrasi hanya lewat **External JSON-2 API** (`POST /json/2/<model>/<method>`, `Authorization: bearer <API key>`, header `X-Odoo-Database`). XML-RPC dan JSON-RPC tidak dipakai ([ADR-0004](docs/adr/0004-odoo-19-json2-api.md)).
- Ada dua API key: **admin** untuk skrip pengembang (init, seed, check) dan **agent** untuk kode agent. Kode agent tidak boleh memakai key admin.
- Nama field dan metode di Odoo 19 bisa berbeda dari versi lama. Periksa dengan `fields_get` (atau halaman `/doc` di database) sebelum memakainya; jangan berasumsi dari ingatan.
- Database Odoo `fakturberes` boleh dibangun ulang kapan saja karena seluruh isinya berasal dari seed. Volume tidak dihapus; pakai `npm run odoo:reset`.

## Basis data aplikasi (berlaku sejak tabel aplikasi pertama dibuat)
- Skema dikelola dengan migrasi berversi (Alembic). Migrasi yang sudah di-commit tidak diubah; perbaikan ditulis sebagai migrasi baru.
- Jangan mengedit tabel versi migrasi atau objek skema di database pengembangan secara manual. Percobaan migrasi memakai database sementara, atau tanya pemilik dulu.

## Git
- **Commit kecil.** Satu commit berisi satu butir yang bisa dijelaskan dalam satu kalimat. Butir yang menyentuh hal tidak berkaitan dipecah.
- Format pesan: `<ID butir> <ringkasan singkat huruf kecil>`, misalnya `M0.3 compose: Odoo 19, PostgreSQL, pgvector, Mailpit`. ID butir mengikuti prompt tugas.
- Stage path yang spesifik; jangan `git add -A` atau `git add .` tanpa memeriksa `git status`.
- Sebelum commit kode: `npm run check` lulus. Sebelum setiap commit: `npm run guard:secrets` lulus. Keduanya berlaku sejak perintahnya tersedia (M0).
- Jangan `--amend` commit yang sudah di-push, jangan `push --force`, dan jangan menulis ulang riwayat.
- Push hanya ke `origin main`. Visibilitas repo GitHub (privat sampai minggu 3) diubah pemilik, bukan agen.

## Orisinalitas (ketentuan lomba)
- Karya harus orisinal. Jangan menyalin kode atau arsitektur agent dari repositori publik secara utuh.
- Pustaka dipakai lewat package manager. Cuplikan dari dokumentasi resmi boleh dipakai setelah disesuaikan; sumber yang penting dicatat di komentar atau ADR.
- Keluaran LLM yang dikirim ke luar (email vendor, rekap) memakai templat; LLM hanya mengisi rincian yang sudah divalidasi kode. Keluaran tidak boleh memuat unsur SARA, kekerasan, atau konten yang melanggar hukum.

## Dokumen
- ADR mengikuti aturan di [docs/adr/README.md](docs/adr/README.md): keputusan yang sudah **Diterima** tidak diubah. Catatan bertanggal boleh ditambahkan; keputusan yang berubah ditulis sebagai ADR baru.
- Saat milestone selesai, perbarui status di [RENCANA-TEKNIS §6](docs/RENCANA-TEKNIS.md#6-milestone) dan baris status di README.
- Perubahan isi SPEC menaikkan versinya dan menambah baris di Riwayat versi.
- Bila kenyataan berbeda dari dokumen (versi image, nama field, perintah), perbarui dokumennya di commit terpisah dan sebutkan di laporan.

## Kapan berhenti dan bertanya
- Ada keputusan yang tidak bisa dibatalkan, atau ada dua pilihan yang sama masuk akal dan berdampak ke SPEC/ADR.
- Port bentrok, akses `sudo` dibutuhkan, atau path repo berada di mount jaringan/FUSE.
- Tugas meminta sesuatu yang bertentangan dengan aturan di berkas ini.
