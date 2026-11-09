"""Which files the evaluation tools may send to an LLM (M3 hard constraints).

Only synthetic data reaches Gemini: files under ``eval/berkas/`` and ``eval/ketahanan/berkas/``,
the phrases of an injection set, and files made by tests. Every tool that sends to an LLM refuses a
path with a ``_privat`` or ``privat`` component (CLAUDE.md: real documents live there and are never
read). The extraction tool also refuses a path outside the repository; ``eval:injeksi --berkas`` is
the one exception, for the planner's closed phrase set.
"""

from pathlib import Path

from faktur_beres.config import REPO_ROOT

PRIVATE_PARTS = frozenset({"_privat", "privat"})


class PathRefused(ValueError):
    """The path may not be read by a tool that sends to an LLM."""


def checked_path(path: Path, *, inside_repo: bool) -> Path:
    """The resolved path, or ``PathRefused``."""
    resolved = path.expanduser().resolve()
    parts = {part.casefold() for part in (*path.parts, *resolved.parts)}
    if parts & PRIVATE_PARTS:
        raise PathRefused(f"path dengan komponen _privat atau privat ditolak: {path}")
    if inside_repo and not resolved.is_relative_to(REPO_ROOT):
        raise PathRefused(f"path di luar repo ditolak: {path}")
    return resolved
