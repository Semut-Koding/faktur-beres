"""Build every generator output and write it to ``eval/`` or compare it with ``eval/``.

``gen:periksa`` compares content, not bytes (``generator.content``): zlib output can differ between
machines while pages, text, and pixels are the same. ``--byte`` keeps the strict byte comparison.

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
from dataclasses import dataclass
from pathlib import Path

import yaml

from faktur_beres.config import REPO_ROOT
from faktur_beres.generator.assemble import assemble
from faktur_beres.generator.content import content_signature, file_refs

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


@dataclass(frozen=True)
class TreeComparison:
    """Result of ``gen:periksa``: files equal byte for byte, equal in content, and problems."""

    same_bytes: tuple[str, ...]
    same_content: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def label_hash_problems(actual_root: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    """Every ``sha256`` in a label under ``actual_root`` must be the hash of the file it names."""
    problems = []
    for rel, path in _generated_paths(actual_root).items():
        if not rel.endswith((".yaml", ".yml")):
            continue
        label = yaml.safe_load(path.read_text(encoding="utf-8"))
        for ref_path, sha in file_refs(label):
            target = repo_root / ref_path
            if not target.is_file():
                problems.append(f"label {rel} menunjuk berkas yang tidak ada: {ref_path}")
            elif sha256_of(target) != sha:
                problems.append(f"sha256 di label {rel} bukan hash berkas {ref_path}")
    return problems


def compare_trees(
    expected_root: Path,
    actual_root: Path,
    *,
    by_bytes: bool = False,
    repo_root: Path | None = REPO_ROOT,
) -> TreeComparison:
    """Compare a fresh generator tree with the committed one (content, or bytes with ``by_bytes``).

    With ``repo_root`` the label hashes under ``actual_root`` are also checked against the files.
    """
    expected = _generated_paths(expected_root)
    actual = _generated_paths(actual_root)
    problems = [f"tidak ada di repo: {rel}" for rel in sorted(expected.keys() - actual.keys())]
    problems += [
        f"tidak dihasilkan generator: {rel}" for rel in sorted(actual.keys() - expected.keys())
    ]
    same_bytes, same_content = [], []
    for rel in sorted(expected.keys() & actual.keys()):
        new, old = expected[rel].read_bytes(), actual[rel].read_bytes()
        if new == old:
            same_bytes.append(rel)
        elif not by_bytes and content_signature(rel, new) == content_signature(rel, old):
            same_content.append(rel)
        else:
            problems.append(f"isi berbeda: {rel}")
    if repo_root is not None:
        problems += label_hash_problems(actual_root, repo_root)
    return TreeComparison(tuple(same_bytes), tuple(same_content), tuple(problems))


def check_outputs(
    builder: Builder = build_outputs,
    root: Path = EVAL_DIR,
    *,
    by_bytes: bool = False,
    repo_root: Path | None = REPO_ROOT,
) -> TreeComparison:
    """Rebuild into a temporary directory and compare with ``root``."""
    with tempfile.TemporaryDirectory(prefix="faktur-beres-gen-") as tmp:
        tmp_root = Path(tmp)
        write_outputs(builder(), tmp_root)
        return compare_trees(tmp_root, root, by_bytes=by_bytes, repo_root=repo_root)
