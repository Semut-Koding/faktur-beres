# GTNIC 2026: ketentuan lomba dan artinya bagi Faktur Beres

| | |
|---|---|
| Lomba | Garda Tech National Innovation Competition (GTNIC) 2026 |
| Kategori | AI Agent. Subtema "Building Intelligent AI Agents for Enterprise Productivity", fokus Enterprise & Industry |
| Tim | Daftar H-2 Jam: Naura Zalfa Salsabila (ketua), Farhan Ginting (anggota) |
| Pembimbing | Nurfitria Khoirunnisa, S.Tr.Kom., M.Kom. (Politeknik Negeri Subang) |
| Sumber | Guidebook resmi GTNIC 2026 dan informasi panitia (dirangkum 5 November 2026) |

Dokumen ini merangkum aturan yang memengaruhi pekerjaan teknis. Bila ada perbedaan, guidebook dan pengumuman panitia yang berlaku.

## Jadwal

| Tahap | Guidebook | Berlaku sekarang |
|---|---|---|
| Masa pengerjaan | 19 Oktober – 10 November 2026 | Diperpanjang |
| Batas pengumpulan | 10 November 2026, 23.59 WIB | **30 November 2026** (diperpanjang panitia). Jam 23.59 WIB diasumsikan sama |
| Pengumuman finalis (Top 5) | 12 November 2026 | Belum diumumkan ulang |
| Grand Final (live pitching via Zoom) | 14 November 2026 | Belum diumumkan ulang |

Target internal: semua bahan dikirim **29 November 2026** (H-1).

## Ketentuan karya yang mengikat kode
1. **Orisinal.** Karya 100% buatan sendiri dan belum pernah dipublikasikan atau memenangkan lomba lain. Dilarang menyalin arsitektur atau kode AI secara utuh dari repositori publik tanpa modifikasi signifikan.
2. **Keluaran aman.** Sistem tidak boleh menghasilkan keluaran bermuatan SARA, pornografi, kekerasan, provokatif, atau melanggar hukum. Privasi data dan hak cipta menjadi tanggung jawab peserta.
3. **Sesuai subtema.** Membantu perusahaan mengotomatiskan pekerjaan, menganalisis data, dan meningkatkan efisiensi operasional. Faktur Beres termasuk jenis *AI Finance Assistant* dan *AI Document Processing Agent*.
4. **Bukan sekadar ChatGPT wrapper.** Wajib punya fitur lanjut seperti penalaran, integrasi API atau *tool calling*, atau RAG. Faktur Beres memakai ketiganya.
5. **Teknologi bebas.** Boleh memakai LLM pihak ketiga (Gemini, dll.), framework, low-code, dan API eksternal sesuai lisensinya. Arsitekturnya wajib dijelaskan di proposal.
6. **Deploy publik.** Wajib ada UI yang di-deploy publik. Tautan demo live dan repo GitHub harus **publik** saat pengumpulan.
7. **Satu tim, satu karya.**

## Bahan yang dikumpulkan
- **Tautan antarmuka live** AI agent.
- **Tautan repo GitHub publik.**
- **Proposal PDF** (UX case study): maksimal 15 halaman, A4, Times New Roman 12 pt, spasi 1,5, margin kiri 4 cm dan sisi lain 3 cm, bahasa Indonesia atau Inggris yang baik. Wajib memuat: judul karya dan nama tim; latar belakang masalah operasional; tujuan, manfaat, dan solusi otomatisasi; target perusahaan; arsitektur sistem AI (alur logika/RAG, tech stack, integrasi API); fitur utama dan metrik estimasi efisiensi; batasan sistem dan mitigasi risiko (halusinasi, kebocoran data); tangkapan layar antarmuka dan interaksi agent; rencana pengembangan lanjutan. Logo GTNIC 2026 wajib ada di sampul. Nama berkas: `GTNIC2026_AIAgent_NamaTim.pdf` (rencana: `GTNIC2026_AIAgent_DaftarH-2Jam.pdf`).
- **Video demo** 3–5 menit (YouTube atau Google Drive, akses publik): perkenalan tim, masalah dan solusi (boleh berupa pitching), **demo live** agent menyelesaikan tugas, dan penjelasan singkat arsitektur.
- **Surat pernyataan keaslian karya.**
- Semua dikirim lewat Google Form resmi dengan mencantumkan nama tim.

Proposal dikerjakan Naura, dan pembaruan draf dijadwalkan di minggu 3 (20–26 November). Repo ini menyediakan bahannya: angka evaluasi, tangkapan layar, dan diagram arsitektur.

## Kriteria penilaian

| Kriteria | Bobot | Yang dinilai juri | Artinya bagi keputusan teknis |
|---|---|---|---|
| Kapabilitas dan otonomi agent | 25% | Beroperasi mandiri, adaptif terhadap perubahan kondisi, menyelesaikan tujuan akhir tanpa intervensi manual | Alur ujung ke ujung sampai draft tagihan; melanjutkan sendiri setelah balasan vendor; touchless rate diukur |
| Penalaran, perencanaan, dan pengambilan keputusan | 15% | Mengurai tugas kompleks, perencanaan logis, menjaga konteks, menangani ambiguitas, pulih otomatis dari kegagalan | Rencana langkah yang terlihat di jejak; aturan "tidak menebak"; perbaikan ekstraksi maksimal 2 kali; state di checkpointer |
| Penggunaan alat dan eksekusi tindakan | 15% | Memilih layanan yang relevan, parameter valid, membaca hasil alat, berpindah antaralat untuk tindakan nyata | *Tool calling* dengan skema; parameter divalidasi; aksi nyata di Odoo dan email; verifikasi setelah aksi |
| Arsitektur teknis dan rekayasa sistem | 15% | Modularitas, skalabilitas, desain basis data, manajemen memori, keamanan, penanganan kesalahan, efisiensi alur (dinilai lewat proposal) | Modul terpisah per alat; basis data dengan migrasi; memori jangka pendek dan panjang; guardrail OWASP; retry dan idempotensi |
| Inovasi dan kreativitas | 10% | Orisinalitas, kebaruan alur kerja, optimalisasi sistem cerdas di sektor sasaran | Pajak Indonesia (PMK 131/2024, Coretax), UBL EN 16931, deteksi injeksi lewat faktur |
| Dampak masalah dan nilai bisnis | 10% | Urgensi, target pengguna spesifik, metrik dampak terukur, potensi komersial | Metrik evaluasi yang jujur dan dapat diulang; biaya LLM per faktur |
| Pengalaman dan kemudahan pengguna | 5% | Alur interaksi jelas, umpan balik sistem, pesan kesalahan terbaca, keluaran mudah dipahami | Dashboard berbahasa Indonesia, status langsung, paket bukti yang ringkas |
| Demonstrasi, presentasi, dan dokumentasi | 5% | Kelengkapan dokumentasi repo, kejelasan masalah dan solusi, demo yang menampilkan eksekusi langsung | README, docs, dan ADR yang rapi; demo live yang bisa diulang dari data seed |

**70% nilai** (empat baris pertama) ada pada perilaku dan rekayasa agent. Saat memilih pendekatan teknis, utamakan yang:
1. membuat perilaku agent **terlihat** (rencana, panggilan alat, dan hasilnya tercatat dan bisa ditampilkan);
2. **terukur** lewat evaluasi otomatis;
3. **aman** karena dijaga guardrail di kode, bukan hanya di prompt;
4. bisa **didemokan langsung** dari kondisi seed yang sama.

Hindari pekerjaan infrastruktur yang tidak terlihat dan tidak menambah nilai pada kriteria di atas.

## Daftar periksa pengumpulan
- [ ] Dashboard publik berjalan dan bisa dicoba juri
- [ ] Repo GitHub diubah menjadi publik oleh pemilik, README berisi cara mencoba demo
- [ ] Hasil evaluasi terbaru tercantum di README dan proposal
- [ ] Tangkapan layar 3–9 (proposal §8) diambil dari prototipe
- [ ] Proposal PDF ≤ 15 halaman sesuai format, logo GTNIC di sampul
- [ ] Video demo 3–5 menit, akses publik
- [ ] Surat pernyataan keaslian
- [ ] Google Form diisi dengan nama tim
