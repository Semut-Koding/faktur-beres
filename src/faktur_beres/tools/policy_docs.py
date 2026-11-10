"""The policy documents as chunks (BR-28): one per article or sub-article, deterministic.

The numbers in a document carry a ``<!-- param: name -->`` marker naming the value of
``parameter.yaml`` they repeat, so a test can hold them equal. Markers are removed from the chunks.
A chunk is only ever quoted: decisions read their numbers from the configuration.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from faktur_beres.config import REPO_ROOT

POLICY_DIR = REPO_ROOT / "data" / "kebijakan"
DOCUMENTS: tuple[tuple[str, str], ...] = (
    ("SOP", "sop-hutang-usaha.md"),
    ("SP", "syarat-pembelian.md"),
)
CHUNK_FORMAT = 1
_MARKER = re.compile(r"\s*<!--\s*param:\s*[\w.]+\s*-->")
_ARTICLE = re.compile(r"^##\s+(\d+)\.\s+(.+)$")
_SUB = re.compile(r"^(\d+\.\d+)\s+(.+)$")


@dataclass(frozen=True)
class PolicyChunk:
    ref: str
    document: str
    title: str
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(f"{self.title}\n{self.text}".encode()).hexdigest()


def chunks_of(prefix: str, document: str, content: str) -> list[PolicyChunk]:
    chunks: list[PolicyChunk] = []
    article: tuple[str, str] | None = None
    plain: list[str] = []

    def close() -> None:
        if article is not None and plain:
            ref = f"{prefix}-{article[0]}"
            chunks.append(PolicyChunk(ref, document, article[1], " ".join(plain)))
        plain.clear()

    for raw in content.splitlines():
        line = _MARKER.sub("", raw).strip()
        if heading := _ARTICLE.match(line):
            close()
            article = (heading.group(1), heading.group(2))
        elif article is not None and (sub := _SUB.match(line)):
            close()
            chunks.append(
                PolicyChunk(f"{prefix}-{sub.group(1)}", document, article[1], sub.group(2))
            )
        elif article is not None and line:
            plain.append(line)
    close()
    return chunks


def load_chunks(directory: Path = POLICY_DIR) -> list[PolicyChunk]:
    found: list[PolicyChunk] = []
    for prefix, name in DOCUMENTS:
        found += chunks_of(prefix, name, (directory / name).read_text(encoding="utf-8"))
    return found


def documents_sha256(chunks: Sequence[PolicyChunk]) -> str:
    joined = "\n".join(f"{c.ref}:{c.sha256}" for c in chunks)
    return hashlib.sha256(joined.encode()).hexdigest()
