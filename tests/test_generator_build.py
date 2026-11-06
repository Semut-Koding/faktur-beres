"""Tests for writing and comparing generator outputs (no rendering involved)."""

from pathlib import Path

from faktur_beres.generator.build import check_outputs, compare_trees, write_outputs

FILES = {
    "skenario/X-01.yaml": b"id: X-01\n",
    "berkas/X-01/faktur.pdf": b"%PDF-1.4 contoh",
    "DAFTAR.md": b"# Daftar\n",
}


def test_write_replaces_only_generated_outputs(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("tulisan tangan", encoding="utf-8")
    (tmp_path / "berkas" / "LAMA").mkdir(parents=True)
    (tmp_path / "berkas" / "LAMA" / "sisa.pdf").write_bytes(b"lama")

    write_outputs(FILES, tmp_path)

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "tulisan tangan"
    assert not (tmp_path / "berkas" / "LAMA").exists()
    assert (tmp_path / "berkas" / "X-01" / "faktur.pdf").read_bytes() == FILES[
        "berkas/X-01/faktur.pdf"
    ]


def test_compare_reports_missing_extra_and_changed(tmp_path: Path) -> None:
    expected, actual = tmp_path / "a", tmp_path / "b"
    write_outputs(FILES, expected)
    write_outputs({**FILES, "berkas/X-01/faktur.pdf": b"beda", "skenario/X-02.yaml": b"x"}, actual)
    (actual / "DAFTAR.md").unlink()

    assert compare_trees(expected, actual) == [
        "tidak ada di repo: DAFTAR.md",
        "tidak dihasilkan generator: skenario/X-02.yaml",
        "isi berbeda: berkas/X-01/faktur.pdf",
    ]


def test_check_outputs_passes_for_identical_tree(tmp_path: Path) -> None:
    write_outputs(FILES, tmp_path)
    assert check_outputs(lambda: dict(FILES), tmp_path) == []
    assert check_outputs(lambda: {**FILES, "DAFTAR.md": b"lain"}, tmp_path) == [
        "isi berbeda: DAFTAR.md"
    ]
