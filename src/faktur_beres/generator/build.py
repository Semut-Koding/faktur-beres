"""Build every generator output and write it to ``eval/`` or compare it with ``eval/``.

Outputs (all deterministic, see ``docs/tugas/M1.md``):

* ``eval/skenario/<ID>.yaml``: one label per scenario;
* ``eval/berkas/<ID>/...``: invoice files and vendor-reply attachments;
* ``eval/DAFTAR.md``: overview table.

``eval/README.md`` is written by hand and never touched here.
"""

import hashlib
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from faktur_beres.config import REPO_ROOT
from faktur_beres.generator.assemble import assemble

EVAL_DIR = REPO_ROOT / "eval"
GENERATED_DIRS = ("skenario", "berkas")
GENERATED_FILES = ("DAFTAR.md",)

Builder = Callable[[], dict[str, bytes]]


def build_outputs() -> dict[str, bytes]:
    """Render all outputs in memory as ``{path relative to eval/: content}``."""
    return assemble()


def _generated_paths(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for name in GENERATED_DIRS:
        base = root / name
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    found[path.relative_to(root).as_posix()] = path
    for name in GENERATED_FILES:
        path = root / name
        if path.is_file():
            found[name] = path
    return found


def write_outputs(files: dict[str, bytes], root: Path = EVAL_DIR) -> None:
    """Replace the generated outputs under ``root`` with ``files``."""
    for name in GENERATED_DIRS:
        target = root / name
        if target.exists():
            shutil.rmtree(target)
    for name in GENERATED_FILES:
        (root / name).unlink(missing_ok=True)
    for rel, content in sorted(files.items()):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_trees(expected_root: Path, actual_root: Path) -> list[str]:
    """Differences between two output trees, by SHA-256 of every generated file."""
    expected = _generated_paths(expected_root)
    actual = _generated_paths(actual_root)
    problems = []
    for rel in sorted(expected.keys() - actual.keys()):
        problems.append(f"tidak ada di repo: {rel}")
    for rel in sorted(actual.keys() - expected.keys()):
        problems.append(f"tidak dihasilkan generator: {rel}")
    for rel in sorted(expected.keys() & actual.keys()):
        if sha256_of(expected[rel]) != sha256_of(actual[rel]):
            problems.append(f"isi berbeda: {rel}")
    return problems


def check_outputs(builder: Builder = build_outputs, root: Path = EVAL_DIR) -> list[str]:
    """Rebuild into a temporary directory and compare with ``root``."""
    with tempfile.TemporaryDirectory(prefix="faktur-beres-gen-") as tmp:
        tmp_root = Path(tmp)
        write_outputs(builder(), tmp_root)
        return compare_trees(tmp_root, root)
