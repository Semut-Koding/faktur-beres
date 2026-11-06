"""``eval/DAFTAR.md``: overview table of the scenario catalogue (generated)."""

from collections.abc import Sequence

INTRO = """# Daftar skenario uji

Berkas ini dibuat `npm run gen:faktur`; jangan disunting tangan. Penjelasan folder ada di
[README.md](README.md). Label lengkap per skenario ada di `skenario/<ID>.yaml`.

"""
COLUMNS = (
    "ID",
    "Kategori",
    "Vendor",
    "Nomor faktur",
    "Format",
    "Kanal",
    "Berkas",
    "Total dokumen",
    "Total tagihan diharapkan",
    "Jalur awal → status akhir",
)


def render_daftar(rows: Sequence[Sequence[str]]) -> str:
    lines = [INTRO, "| " + " | ".join(COLUMNS) + " |\n", "|" + "---|" * len(COLUMNS) + "\n"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |\n")
    return "".join(lines)
