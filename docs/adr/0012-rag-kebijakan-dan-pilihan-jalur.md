# ADR-0012: RAG kebijakan dan pilihan jalur oleh LLM

- Status: Diusulkan
- Tanggal: 2026-11-10
- Terkait: [SPEC §5.2, BR-17, BR-21, BR-28, §14](../SPEC.md), [ADR-0005](0005-llm-memutuskan-kode-menghitung.md), [ADR-0010](0010-llm-di-graph.md)

## Konteks
Decide di M4a selalu memakai jalur bawaan. M4b menambahkan pilihan LLM di dalam himpunan yang diizinkan kode dan kutipan kebijakan di paket bukti. Angka kebijakan tetap dibaca dari konfigurasi (BR-28).

## Keputusan
1. **Embedding** memakai `gemini-embedding-2` dengan 768 dimensi. Indeks HNSW pgvector dibatasi 2.000 dimensi untuk tipe `vector`, dan Google menyarankan 768 atau 1.536. Konstanta yang sama dipakai panggilan model dan kolom, dan ada tes yang mengikatnya.
2. **Format masukan** mengikuti panduan Google untuk model ini (`aistudio.google.com/docs/embeddings`, dibaca 10 November 2026): `title: ... | text: ...` untuk dokumen dan `task: search result | query: ...` untuk kueri. `task_type` tidak didukung model ini. Model menormalkan dimensi yang dipangkas.
3. **Potongan** satu per pasal atau subpasal, dengan ref seperti `SOP-8.2`. Penanda angka dibuang dari teks, dan hasilnya deterministik.
4. **Indeks** ada di `policy_chunks` dengan HNSW cosine. Tabel ini dan `policy_reason_quotes` adalah turunan dokumen, sehingga `db:reset` tidak mengosongkannya. `eval:graph` memeriksa kesesuaian indeks sebelum mulai.
5. **Kutipan per alasan tanpa LLM.** Setiap kode alasan punya kueri tetap yang di-embed saat `rag:indeks`. Ref dan skornya disimpan, sehingga `decide` tidak memanggil API untuk ini. Hanya artikel terbaik yang dikutip, dengan ambang skor 0,70. Dasarnya indeks pertama (10 November 2026, 37 potongan): artikel terbaik setiap alasan relevan dan skornya 0,722–0,824, sedangkan artikel kedua mencapai 0,80 tetapi sering tidak relevan, dan hasil yang tidak relevan mulai sekitar 0,69.
6. **Pilihan jalur** hanya berjalan bila himpunan yang diizinkan berisi lebih dari satu jalur. Alatnya `search_policy` dan `submit_route`. Prompt `decide-route-v1` meminta model tetap di jalur bawaan kecuali ketentuan secara tegas mewajibkan lainnya.
7. **Guard.** Jalur harus di dalam himpunan, ref harus berasal dari hasil pencarian pada percakapan yang sama, dan pindah dari jalur bawaan butuh alasan tertulis serta minimal satu kutipan (`guard_route`). Jawaban yang ditolak dicatat di audit, lalu jalur bawaan dipakai.
8. **Anggaran** 4 panggilan model dan 3 eksekusi alat, tanpa model cadangan. Bila LLM tidak tersedia atau anggaran habis, jalur bawaan dipakai dengan catatan; BR-27 tidak terpicu.
9. **Email vendor tetap tanpa teks LLM.** Pertimbangan dan kutipan hanya masuk audit dan paket bukti.

## Konsekuensi
- Pilihan jalur bisa berbeda antar-run; kode tetap membatasi himpunannya.
- Embedding menambah satu kuota lagi yang perlu dipantau (`llm:kuota`).

## Catatan atas ADR yang sudah diterima
ADR-0009 dan ADR-0010 tetap berlaku. Yang berubah di M4b ditulis di sini dan di ADR-0011: node `await_vendor`, `apply_reply`, dan `read_reply`; kutipan kebijakan yang kini terisi; dan pemilih jalur sebagai percakapan alat kedua.

## Alternatif yang ditolak
- **Kutipan per alasan lewat LLM saat `decide`:** menambah panggilan untuk tiap faktur tanpa menambah mutu.
- **Dimensi 3.072:** melebihi batas indeks HNSW tipe `vector`.
- **Angka kebijakan dari teks RAG:** dilarang BR-28.
