# ADR-0001: Stack inti Python 3.13, LangGraph, FastAPI, Gemini, PostgreSQL + pgvector, React + Vite

- Status: Diterima
- Tanggal: 2026-11-05
- Terkait: [RENCANA-TEKNIS §2–3](../RENCANA-TEKNIS.md#2-stack), [SPEC §5](../SPEC.md#5-agent-alur-kerja-alat-dan-memori), proposal §5.5

## Konteks
- Faktur Beres harus menunjukkan agent yang otonom, bernalar, memakai alat, dan direkayasa dengan baik, karena empat kriteria itu bernilai 70% ([lomba](../lomba/gtnic-2026.md#kriteria-penilaian)).
- Alur per faktur bisa berhenti menunggu vendor atau approver lalu dilanjutkan tanpa mengulang aksi.
- Faktur datang sebagai PDF, foto, dan UBL. LLM harus bisa membaca gambar dan menghasilkan keluaran terstruktur.
- Waktu pengerjaan tiga minggu, satu pengembang dibantu AI, dengan anggaran LLM sekecil mungkin.
- Proposal yang sudah disetujui menyebut LangGraph, FastAPI, Gemini Flash/Flash-Lite, PostgreSQL + pgvector, React + Vite, Odoo 19, Mailpit, dan parser UBL.

## Keputusan
- **Python 3.13** untuk semua kode backend: agent, alat, seed, generator, dan evaluasi.
- **LangGraph** untuk graph per faktur, dengan **checkpointer PostgreSQL** dan **interrupt** untuk persetujuan manusia.
- **Gemini** keluarga Flash (utama) dan Flash-Lite (cadangan dan tugas ringan) lewat SDK **`google-genai`**, dengan keluaran terstruktur berbasis skema. Nama model persis dikunci di konfigurasi saat M3.
- **Pydantic v2** untuk skema ekstraksi, parameter alat, dan konfigurasi.
- **FastAPI** dengan REST dan **SSE** untuk dashboard.
- **PostgreSQL 18 + pgvector** untuk basis data aplikasi: checkpoint, tabel faktur, aksi, persetujuan, audit, memori vendor, dan potongan kebijakan untuk RAG. Akses lewat **SQLAlchemy 2 + psycopg 3**, migrasi dengan **Alembic**.
- **React + Vite + TypeScript** untuk dashboard.
- **Mailpit** sebagai SMTP sandbox dan inbox AP.
- Uang disimpan sebagai rupiah bulat (integer) atau `Decimal`, tidak pernah `float`.
- **pytest** dan **Ruff** sejak M0; **mypy** mulai M2.

## Konsekuensi
- Satu bahasa backend memudahkan pembagian kode antara agent, generator, dan evaluasi; misalnya model dataset dan aturan pajak dipakai bersama.
- LangGraph memberi state, interrupt, dan resume tanpa kode orkestrasi sendiri. Versi pustaka dikunci di `uv.lock` karena API LangGraph berkembang cepat.
- Tier gratis Gemini punya batas kuota dan dapat memakai data untuk peningkatan produk. Karena itu hanya data sintetis yang dikirim, ada model cadangan, dan ada cache untuk demo.
- Dashboard terpisah dari backend; deploy publiknya diputuskan di M6.

## Alternatif
- **TypeScript penuh** (seperti DombaTek): satu bahasa dengan dashboard, tetapi ekosistem agent, PDF, dan gambar di Python lebih matang.
- **LangChain agent biasa atau loop buatan sendiri:** lebih sederhana di awal, tetapi interrupt, checkpoint, dan resume harus ditulis sendiri.
- **OpenAI atau Claude API:** kualitas tinggi, tetapi berbayar sejak awal.
- **Platform low-code (n8n, Flowise):** cepat, tetapi idempotensi, tes otomatis, dan guardrail di kode lebih sulit dikendalikan.
