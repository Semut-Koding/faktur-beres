"""``npm run guard:secrets``: refuse commits that would publish a secret.

Fails when:

* a ``.env`` or ``.env.*`` file (other than ``.env.example``) is tracked or staged;
* the value of a secret variable from the local ``.env`` (8 characters or longer) appears in a
  tracked or staged file;
* a well-known secret pattern appears (Google API key, private key header, ...);
* a ``.zip`` archive, or a path with a ``_privat`` or ``privat`` component, is tracked or staged.
  Real documents never enter the repository (CLAUDE.md, "Dokumen nyata dan berkas pribadi"), so
  such files are reported by name only and their content is never read.

Everything in the git index is scanned, i.e. tracked files plus staged changes. Messages name the
file and line but never print the secret itself.
"""

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from faktur_beres.devtools.envfile import read_env

MIN_SECRET_LENGTH = 8
MAX_BLOB_BYTES = 10 * 1024 * 1024

SECRET_NAME = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)", re.IGNORECASE)
URL_WITH_CREDENTIALS = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")

PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("kunci API Google (AIza...)", re.compile(rb"AIza[0-9A-Za-z_-]{35}")),
    (
        "header private key",
        re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY"),
    ),
    ("token GitHub", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("access key AWS", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int | None
    what: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{self.what} di {where}"


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, input=stdin, capture_output=True, check=False)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git {args[0]} gagal: {message}")
    return result.stdout


def is_forbidden_env_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (name == ".env" or name.startswith(".env.")) and name != ".env.example"


PRIVATE_COMPONENTS = frozenset({"_privat", "privat"})


def private_document_reason(path: str) -> str | None:
    """Why ``path`` may hold a real or private document, or ``None`` when it is fine."""
    parts = path.lower().split("/")
    if PRIVATE_COMPONENTS & set(parts):
        return "path _privat/privat ter-track atau di-stage"
    if parts[-1].endswith(".zip"):
        return "arsip .zip ter-track atau di-stage"
    return None


def secret_values(env: dict[str, str]) -> dict[str, str]:
    """Pick the variables whose values must never be committed (value -> variable name)."""
    found: dict[str, str] = {}
    for name, value in env.items():
        if len(value) < MIN_SECRET_LENGTH:
            continue
        if SECRET_NAME.search(name) or URL_WITH_CREDENTIALS.match(value):
            found.setdefault(value, name)
    return found


def index_blobs(repo: Path) -> list[tuple[str, str]]:
    """Return ``(blob sha, path)`` for every file in the index (tracked + staged)."""
    entries = []
    for record in _git(repo, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        meta, raw_path = record.split(b"\t", 1)
        mode, sha, _stage = meta.split(b" ")
        if mode == b"160000":  # submodule
            continue
        entries.append((sha.decode(), os.fsdecode(raw_path)))
    return entries


def read_blobs(repo: Path, shas: list[str]) -> dict[str, bytes]:
    """Read many blobs with one ``git cat-file --batch`` call."""
    if not shas:
        return {}
    unique = list(dict.fromkeys(shas))
    out = _git(repo, "cat-file", "--batch", stdin=("\n".join(unique) + "\n").encode())
    blobs: dict[str, bytes] = {}
    pos = 0
    for sha in unique:
        header_end = out.index(b"\n", pos)
        header = out[pos:header_end].split(b" ")
        pos = header_end + 1
        if len(header) < 3 or header[1] == b"missing":
            continue
        size = int(header[2])
        blobs[sha] = out[pos : pos + size]
        pos += size + 1
    return blobs


def _line_of(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def scan(repo: Path, env_file: Path | None = None) -> list[Finding]:
    """Return all findings for the repository at ``repo``."""
    env_file = env_file if env_file is not None else repo / ".env"
    secrets = {v.encode(): name for v, name in secret_values(read_env(env_file)).items()}
    entries = index_blobs(repo)
    findings: list[Finding] = []

    private: set[str] = set()
    for _sha, path in entries:
        if is_forbidden_env_file(path):
            findings.append(Finding(path, None, "berkas rahasia ter-track atau di-stage"))
        if (reason := private_document_reason(path)) is not None:
            findings.append(Finding(path, None, reason))
            private.add(path)

    # Possible real documents are reported by name only; their content is never read.
    entries = [(sha, path) for sha, path in entries if path not in private]
    blobs = read_blobs(repo, [sha for sha, _ in entries])
    for sha, path in entries:
        data = blobs.get(sha)
        if data is None or len(data) > MAX_BLOB_BYTES:
            continue
        for value, name in secrets.items():
            start = 0
            while (offset := data.find(value, start)) != -1:
                findings.append(Finding(path, _line_of(data, offset), f"nilai {name} dari .env"))
                start = offset + len(value)
        for label, pattern in PATTERNS:
            for match in pattern.finditer(data):
                findings.append(Finding(path, _line_of(data, match.start()), label))
    return findings


def repo_root(start: Path) -> Path:
    return Path(_git(start, "rev-parse", "--show-toplevel").decode().strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="folder repo git")
    args = parser.parse_args(argv)

    try:
        root = repo_root(args.repo)
        findings = scan(root)
    except RuntimeError as exc:
        print(f"guard:secrets: {exc}", file=sys.stderr)
        return 2

    if findings:
        print(f"guard:secrets GAGAL: {len(findings)} temuan", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        print(
            "Keluarkan rahasia dari berkas itu (atau `git rm --cached <berkas>` untuk .env, "
            "arsip .zip, dan berkas _privat/privat tanpa membukanya), lalu ulangi.",
            file=sys.stderr,
        )
        return 1
    print(f"guard:secrets lulus ({len(index_blobs(root))} berkas di index diperiksa)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
