"""Minimal ``.env`` reader and writer that keeps comments and order intact.

Only ``KEY=value`` lines are understood (optionally quoted values, no interpolation). Writes are
atomic and leave the file readable by its owner only, because ``.env`` holds secrets.
"""

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if match:
            values[match.group(1)] = _unquote(match.group(2))
    return values


def read_env(path: Path) -> dict[str, str]:
    """Return the variables in ``path`` (empty dict if the file does not exist)."""
    if not path.exists():
        return {}
    return parse_env(path.read_text(encoding="utf-8"))


def write_text_private(path: Path, text: str) -> None:
    """Atomically replace ``path`` with ``text``, mode 0600."""
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def set_env_values(path: Path, updates: Mapping[str, str]) -> None:
    """Set ``updates`` in ``path``: replace existing lines in place, append missing keys."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    out: list[str] = []
    for line in lines:
        match = _LINE.match(line) if not line.lstrip().startswith("#") else None
        if match and match.group(1) in pending:
            key = match.group(1)
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    for key, value in pending.items():
        out.append(f"{key}={value}")
    write_text_private(path, "\n".join(out) + "\n")
