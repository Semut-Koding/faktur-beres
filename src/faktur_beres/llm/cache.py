"""LLM answers on disk, used only when the caller asks for it (``--pakai-cache``, demo).

The key is ``request_key``: model, purpose, prompt version, schema hash, system instruction hash,
input hashes and generation settings. Only valid answers are stored. An unreadable or invalid entry
is ignored, and the next valid answer overwrites it. An entry holds the answer text and its
metadata, never the prompt, a document, or the API key. Evaluation runs keep the cache off unless
``--pakai-cache`` is given (RENCANA §3.3), and their reports mark the answers that came from it.
"""

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from faktur_beres.domain.base import DomainModel

_KEY = re.compile(r"[0-9a-f]{64}")


class CacheEntry(DomainModel):
    version: Literal[1] = 1
    key: str
    model: str
    purpose: str
    prompt_version: str
    created: datetime
    output: str  # the JSON text the model returned


@dataclass(frozen=True)
class CachedAnswer[T: BaseModel]:
    value: T
    entry: CacheEntry


class LlmCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ValueError("kunci cache harus SHA-256 heksadesimal")
        return self.directory / key[:2] / f"{key}.json"

    def load[T: BaseModel](self, key: str, schema: type[T]) -> CachedAnswer[T] | None:
        """The stored answer, or None when there is none or it is unreadable or invalid."""
        try:
            raw = self.path(key).read_bytes()
        except OSError:
            return None
        try:
            entry = CacheEntry.model_validate_json(raw)
            if entry.key != key:
                return None
            return CachedAnswer(value=schema.model_validate_json(entry.output), entry=entry)
        except ValidationError:
            return None

    def store(self, entry: CacheEntry) -> None:
        """Write atomically: a reader sees the old entry or the new one, never half of one."""
        path = self.path(entry.key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, path)
