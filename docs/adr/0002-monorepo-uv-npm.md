# ADR-0002: Satu repo: uv untuk Python, npm sebagai penjalan perintah dan workspace dashboard

- Status: Diterima
- Tanggal: 2026-11-05
- Terkait: [RENCANA-TEKNIS §4.1–4.2](../RENCANA-TEKNIS.md#41-struktur-repositori)

## Konteks
- Repo berisi backend Python dan dashboard TypeScript.
- Pemilik sudah terbiasa dengan perintah `npm run setup`, `npm run services:up`, dan `npm run services:down` dari DombaTek.
- Pemilik memilih **uv** untuk Python (5 November 2026).
- Path repo di laptop mengandung spasi (`.../02 - Proyek Pribadi/02 - Kompetisi/...`).

## Keputusan
- **Satu repo** untuk semua bagian. Folder dan repo GitHub memakai nama yang sama.
- **Python dikelola uv:** `pyproject.toml` di akar dengan paket `faktur_beres` di `src/`, `uv.lock` di-commit, dan `.python-version` berisi `3.13`. Dependensi ditambah dengan `uv add`.
- **npm sebagai penjalan perintah:** `package.json` akar berisi skrip `setup`, `services:*`, `odoo:*`, `check`, `lint`, `test`, `guard:secrets`, dan seterusnya. Skrip Python dipanggil lewat `uv run python -m ...`. `package-lock.json` di-commit.
- **Dashboard** di `web/` sebagai workspace npm (`"workspaces": ["web"]`), mulai M5.
- **Node.js 24 LTS**, versi yang sama dengan DombaTek.
- Semua skrip shell mengutip path dan tidak bergantung pada path tanpa spasi.

## Konsekuensi
- Satu perintah untuk semua pekerjaan, dari akar repo.
- uv memasang Python 3.13 sendiri, sehingga tidak bergantung pada versi Python sistem.
- Skrip konsol di `.venv/bin` bisa bermasalah bila path mengandung spasi (shebang). Karena itu alat Python dijalankan lewat `python -m` bila perlu, dan perilaku ini diuji di M0.
- Dua lockfile (uv dan npm) harus dijaga bersamaan.

## Alternatif
- **pip + venv** atau **Poetry:** lebih lambat dan tidak mengelola versi Python.
- **Makefile atau justfile** sebagai penjalan perintah: tidak sesuai kebiasaan pemilik dan butuh alat tambahan.
- **Dua repo terpisah** untuk backend dan dashboard: menambah pekerjaan sinkronisasi di proyek tiga minggu.
