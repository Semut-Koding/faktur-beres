"""Assemble all generator outputs (labels, files, overview) in memory."""

from faktur_beres.generator.daftar import render_daftar


def assemble() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    files["DAFTAR.md"] = render_daftar([]).encode("utf-8")
    return files
