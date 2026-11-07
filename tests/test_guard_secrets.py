"""Tests for ``npm run guard:secrets`` using throwaway git repositories (path with spaces)."""

import subprocess
from pathlib import Path

import pytest

from faktur_beres.devtools import guard_secrets
from faktur_beres.devtools.guard_secrets import scan
from faktur_beres.devtools.setup import HOOK_MARKER, HOOK_SCRIPT, install_pre_commit_hook

SECRET = "sandi-rahasia-uji-123"
# Built at runtime so this file never contains the patterns it tests for.
GOOGLE_KEY = "AI" + "za" + "Sy" + "B" * 33
PRIVATE_KEY_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo dengan spasi"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Uji")
    git(root, "config", "user.email", "uji@contoh.test")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "core.hooksPath", ".git/hooks")
    (root / ".env").write_text(
        f"ODOO_ADMIN_PASSWORD={SECRET}\n"
        "ODOO_URL=http://127.0.0.1:28069\n"
        "SHORT_TOKEN=abc\n"
        "APP_DB_URL=postgresql+psycopg://fakturberes:url-rahasia-99@127.0.0.1:25432/fb\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("halo\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-q", "-m", "awal")
    return root


def stage(repo: Path, relpath: str, text: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "-f", relpath)


def test_clean_repo_passes(repo: Path) -> None:
    assert scan(repo) == []


@pytest.mark.parametrize("name", [".env", ".env.local", "web/.env", "infra/.env.production"])
def test_env_files_must_not_be_staged(repo: Path, name: str) -> None:
    stage(repo, name, "X=1\n")
    findings = scan(repo)
    assert [f.path for f in findings if "berkas rahasia" in f.what] == [name]


def test_env_example_is_allowed(repo: Path) -> None:
    stage(repo, ".env.example", "ODOO_ADMIN_PASSWORD=\n")
    assert scan(repo) == []


@pytest.mark.parametrize(
    "name",
    [
        "arsip.zip",
        "docs/ekspor 05.ZIP",
        "_privat/faktur.pdf",
        "data/privat/catatan.txt",
        "eval/Privat/a.png",
        "privat",
    ],
)
def test_private_documents_must_not_be_staged(repo: Path, name: str) -> None:
    stage(repo, name, f"isi tidak dibaca {SECRET}\n")
    findings = scan(repo)
    # Reported by name only: the secret inside is not scanned, so there is exactly one finding.
    assert [f.path for f in findings] == [name]
    assert findings[0].line is None
    assert "zip" in findings[0].what or "privat" in findings[0].what


@pytest.mark.parametrize(
    "name", ["docs/privatisasi.md", "data/privat.md", "src/zipper.py", "catatan_privat.txt"]
)
def test_similar_names_are_allowed(repo: Path, name: str) -> None:
    stage(repo, name, "halo\n")
    assert scan(repo) == []


def test_secret_value_in_staged_file(repo: Path) -> None:
    stage(repo, "docs/catatan.md", f"baris satu\nsandi: {SECRET}\n")

    findings = scan(repo)

    assert len(findings) == 1
    finding = findings[0]
    assert (finding.path, finding.line) == ("docs/catatan.md", 2)
    assert "ODOO_ADMIN_PASSWORD" in finding.what
    assert SECRET not in str(finding)


def test_secret_value_in_tracked_file(repo: Path) -> None:
    stage(repo, "src/app.py", f'URL = "x"\nPW = "{SECRET}"\n')
    git(repo, "commit", "-q", "--no-verify", "-m", "bocor")

    findings = scan(repo)
    assert [(f.path, f.line) for f in findings] == [("src/app.py", 2)]


def test_url_with_credentials_is_secret(repo: Path) -> None:
    stage(
        repo,
        "alembic.ini",
        "sqlalchemy.url = postgresql+psycopg://fakturberes:url-rahasia-99@127.0.0.1:25432/fb\n",
    )
    assert [f.what for f in scan(repo)] == ["nilai APP_DB_URL dari .env"]


def test_short_and_non_secret_values_are_ignored(repo: Path) -> None:
    stage(repo, "catatan.txt", "abc\nhttp://127.0.0.1:28069\n")
    assert scan(repo) == []


def test_unstaged_changes_are_not_scanned(repo: Path) -> None:
    (repo / "README.md").write_text(f"halo {SECRET}\n", encoding="utf-8")
    assert scan(repo) == []


@pytest.mark.parametrize(
    ("text", "label"),
    [(f"key = '{GOOGLE_KEY}'\n", "Google"), (f"{PRIVATE_KEY_HEADER}\nMIIE\n", "private key")],
)
def test_common_patterns(repo: Path, text: str, label: str) -> None:
    stage(repo, "config.txt", text)
    findings = scan(repo)
    assert len(findings) == 1
    assert label in findings[0].what
    assert findings[0].line == 1


def test_works_without_local_env(repo: Path) -> None:
    (repo / ".env").unlink()
    stage(repo, "catatan.md", f"{SECRET}\n")
    assert scan(repo) == []


def test_cli_exit_codes_and_no_secret_in_output(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert guard_secrets.main(["--repo", str(repo)]) == 0
    stage(repo, "bocor.md", f"{SECRET}\n")

    assert guard_secrets.main(["--repo", str(repo)]) == 1

    out = capsys.readouterr()
    assert "bocor.md:1" in out.err
    assert SECRET not in out.out + out.err


def test_pre_commit_hook_install_is_idempotent(repo: Path) -> None:
    assert install_pre_commit_hook(repo) == "dipasang"
    hook = repo / ".git" / "hooks" / "pre-commit"
    assert hook.read_text(encoding="utf-8") == HOOK_SCRIPT
    assert hook.stat().st_mode & 0o111
    assert install_pre_commit_hook(repo) == "sudah terpasang"


def test_foreign_pre_commit_hook_is_left_alone(repo: Path) -> None:
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho hook lain\n", encoding="utf-8")
    assert install_pre_commit_hook(repo).startswith("dilewati")
    assert HOOK_MARKER not in hook.read_text(encoding="utf-8")
