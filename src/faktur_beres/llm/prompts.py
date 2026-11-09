"""Prompts as versioned text files inside a package (M3.4).

A prompt version is a directory such as ``faktur_beres/extraction/prompts/extraction-v1/``, with
one ``.txt`` file per part (``system.txt``, ``request.txt``, ...). The directory name is the version
that enters the request key and the audit. A change to any part is a new directory (a new version),
never an edit in place: ``tests/test_prompts.py`` keeps a fingerprint of every version.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from types import MappingProxyType


@dataclass(frozen=True)
class Prompt:
    version: str
    parts: Mapping[str, str]

    def __getitem__(self, name: str) -> str:
        return self.parts[name]

    def entries(self, name: str) -> dict[str, str]:
        """A part written as ``key: text`` lines, as a mapping."""
        pairs = (line.split(":", 1) for line in self[name].splitlines() if line.strip())
        return {key.strip(): text.strip() for key, text in pairs}


def load_prompt(package: str, version: str) -> Prompt:
    folder = resources.files(package).joinpath("prompts", version)
    parts = {
        entry.name.removesuffix(".txt"): entry.read_text(encoding="utf-8").strip()
        for entry in folder.iterdir()
        if entry.name.endswith(".txt")
    }
    if not parts:
        raise FileNotFoundError(f"prompt {version} tidak ada di {package}")
    return Prompt(version=version, parts=MappingProxyType(parts))
