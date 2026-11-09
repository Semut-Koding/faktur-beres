"""Tests for writing generator outputs and comparing them by content (gen:periksa, M3.1)."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from faktur_beres.generator.build import check_outputs, compare_trees, write_outputs

FILES = {
    "skenario/X-01.yaml": b"id: X-01\n",
    "berkas/X-01/faktur.txt": b"contoh",
    "DAFTAR.md": b"# Daftar\n",
}


def pdf(compress: int) -> bytes:
    out = BytesIO()
    canvas = Canvas(out, invariant=1, pageCompression=compress)
    canvas.drawString(72, 720, "DOKUMEN SINTETIS - UNTUK PENGUJIAN")
    canvas.rect(72, 600, 200, 80, fill=1)
    canvas.showPage()
    canvas.save()
    return out.getvalue()


def png(level: int, color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    out = BytesIO()
    Image.new("RGB", (40, 30), color).save(out, format="PNG", compress_level=level)
    return out.getvalue()


def test_write_replaces_only_generated_outputs(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("tulisan tangan", encoding="utf-8")
    (tmp_path / "berkas" / "LAMA").mkdir(parents=True)
    (tmp_path / "berkas" / "LAMA" / "sisa.pdf").write_bytes(b"lama")

    write_outputs(FILES, tmp_path)

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "tulisan tangan"
    assert not (tmp_path / "berkas" / "LAMA").exists()
    assert (tmp_path / "berkas" / "X-01" / "faktur.txt").read_bytes() == b"contoh"


def test_compare_reports_missing_extra_and_changed(tmp_path: Path) -> None:
    expected, actual = tmp_path / "a", tmp_path / "b"
    write_outputs(FILES, expected)
    write_outputs({**FILES, "berkas/X-01/faktur.txt": b"beda", "skenario/X-02.yaml": b"x"}, actual)
    (actual / "DAFTAR.md").unlink()

    result = compare_trees(expected, actual, repo_root=None)
    assert result.problems == (
        "tidak ada di repo: DAFTAR.md",
        "tidak dihasilkan generator: skenario/X-02.yaml",
        "isi berbeda: berkas/X-01/faktur.txt",
    )
    assert result.same_bytes == ("skenario/X-01.yaml",) and not result.ok


def test_recompressed_files_are_identical_in_content(tmp_path: Path) -> None:
    assert pdf(0) != pdf(1) and png(1) != png(9)
    new = {"berkas/A/faktur.pdf": pdf(1), "berkas/A/foto.png": png(9)}
    old = {"berkas/A/faktur.pdf": pdf(0), "berkas/A/foto.png": png(1)}
    write_outputs(new, tmp_path / "a")
    write_outputs(old, tmp_path / "b")
    result = compare_trees(tmp_path / "a", tmp_path / "b", repo_root=None)
    assert result.ok and result.same_content == ("berkas/A/faktur.pdf", "berkas/A/foto.png")
    strict = compare_trees(tmp_path / "a", tmp_path / "b", by_bytes=True, repo_root=None)
    assert not strict.ok and len(strict.problems) == 2


def test_other_pixels_or_text_are_a_difference(tmp_path: Path) -> None:
    write_outputs({"berkas/A/foto.png": png(9)}, tmp_path / "a")
    write_outputs({"berkas/A/foto.png": png(9, (200, 30, 31))}, tmp_path / "b")
    assert compare_trees(tmp_path / "a", tmp_path / "b", repo_root=None).problems == (
        "isi berbeda: berkas/A/foto.png",
    )


def label(sha: str) -> bytes:
    return f"id: X-01\nberkas:\n  path: eval/berkas/X-01/faktur.txt\n  sha256: '{sha}'\n".encode()


def test_labels_compare_without_hashes_but_hashes_must_match_files(tmp_path: Path) -> None:
    import hashlib

    good = hashlib.sha256(b"contoh").hexdigest()
    repo = tmp_path / "repo"
    files = {**FILES, "skenario/X-01.yaml": label(good)}
    write_outputs(files, repo / "eval")
    write_outputs({**files, "skenario/X-01.yaml": label("0" * 64)}, tmp_path / "baru")

    result = compare_trees(tmp_path / "baru", repo / "eval", repo_root=repo)
    assert result.ok and result.same_content == ("skenario/X-01.yaml",)

    write_outputs({**files, "skenario/X-01.yaml": label("1" * 64)}, repo / "eval")
    broken = compare_trees(tmp_path / "baru", repo / "eval", repo_root=repo)
    assert broken.problems == (
        "sha256 di label skenario/X-01.yaml bukan hash berkas eval/berkas/X-01/faktur.txt",
    )


def test_check_outputs_passes_for_identical_tree(tmp_path: Path) -> None:
    write_outputs(FILES, tmp_path)
    assert check_outputs(lambda: dict(FILES), tmp_path, repo_root=None).ok
    result = check_outputs(lambda: {**FILES, "DAFTAR.md": b"lain"}, tmp_path, repo_root=None)
    assert result.problems == ("isi berbeda: DAFTAR.md",)


@pytest.mark.parametrize("name", ["a.pdf", "a.png", "a.jpg", "a.yaml", "a.md"])
def test_content_signature_kinds(name: str) -> None:
    from faktur_beres.generator.content import content_signature

    data = {"pdf": pdf(1), "png": png(9), "jpg": None, "yaml": b"a: 1\n", "md": b"# x\n"}
    ext = name.rsplit(".", 1)[1]
    if ext == "jpg":
        out = BytesIO()
        Image.new("RGB", (8, 8), (1, 2, 3)).save(out, format="JPEG", quality=82)
        content = out.getvalue()
    else:
        content = data[ext]
    kind = content_signature(name, content)[0]
    assert (
        kind == {"pdf": "pdf", "png": "image", "jpg": "image", "yaml": "label", "md": "bytes"}[ext]
    )
