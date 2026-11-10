"""``npm run setup``: check local tools, create ``.env`` with random secrets, install the hook.

Safe to run again: values already present in ``.env`` are never overwritten, secret values are
never printed, and a pre-commit hook that was not installed by setup is left alone.
"""

import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from faktur_beres.config import REPO_ROOT
from faktur_beres.devtools.envfile import parse_env, read_env, set_env_values, write_text_private
from faktur_beres.textui import print_table

ENV_EXAMPLE = REPO_ROOT / ".env.example"
ENV_FILE = REPO_ROOT / ".env"

#: Variables that setup fills with ``secrets.token_urlsafe`` when they are empty.
GENERATED_SECRETS = ("ODOO_DB_PASSWORD", "ODOO_ADMIN_PASSWORD", "APP_DB_PASSWORD")
APP_DB_URL_TEMPLATE = "postgresql+psycopg://fakturberes:{password}@127.0.0.1:25432/fakturberes"
EMPTY_HINTS = {
    "ODOO_ADMIN_API_KEY": "kosong, diisi `npm run odoo:init`",
    "ODOO_AGENT_API_KEY": "kosong, diisi `npm run odoo:init`",
    "GEMINI_API_KEY": "kosong, diisi pemilik (M3)",
}

VERSION = re.compile(r"(\d+(?:\.\d+)+)")

HOOK_MARKER = "# faktur-beres: dipasang oleh npm run setup"
HOOK_SCRIPT = f"""#!/bin/sh
{HOOK_MARKER}
# Menolak commit yang gagal lint atau format, atau memuat berkas .env dan nilai rahasia.
set -e
cd "$(git rev-parse --show-toplevel)"
uv run ruff check .
uv run ruff format --check .
exec npm run --silent guard:secrets
"""


@dataclass(frozen=True)
class ToolCheck:
    name: str
    version: str
    requirement: str
    ok: bool


def _output(*cmd: str) -> str | None:
    """Run a command and return stdout+stderr, or ``None`` if it is missing or fails."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return f"{result.stdout}\n{result.stderr}"


def _parse_version(text: str | None, prefix: str = "") -> tuple[int, ...] | None:
    if not text:
        return None
    match = re.search(re.escape(prefix) + r"\s*v?" + VERSION.pattern, text) if prefix else None
    match = match or VERSION.search(text)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _fmt(version: tuple[int, ...] | None) -> str:
    return ".".join(map(str, version)) if version else "tidak ada"


def check_tools() -> list[ToolCheck]:
    checks: list[ToolCheck] = []

    def at_least(name: str, version: tuple[int, ...] | None, minimum: tuple[int, ...]) -> None:
        req = "≥ " + ".".join(map(str, minimum))
        checks.append(ToolCheck(name, _fmt(version), req, bool(version and version >= minimum)))

    at_least("git", _parse_version(_output("git", "--version")), (2, 30))
    at_least("podman", _parse_version(_output("podman", "--version")), (6, 0))

    compose = _output("podman", "compose", "version")
    pc_version = _parse_version(compose, "podman-compose version") if compose else None
    if compose and "podman-compose version" in compose:
        checks.append(
            ToolCheck(
                "podman compose",
                f"podman-compose {_fmt(pc_version)}",
                "podman-compose ≥ 1.6",
                bool(pc_version and pc_version >= (1, 6)),
            )
        )
    else:
        dc_version = _parse_version(compose, "Docker Compose version") if compose else None
        checks.append(
            ToolCheck(
                "podman compose",
                f"docker compose {_fmt(dc_version)}" if dc_version else "tidak ada",
                "podman-compose ≥ 1.6 (atau Docker Compose ≥ 2)",
                bool(dc_version and dc_version >= (2, 0)),
            )
        )

    rootless = _output("podman", "info", "--format", "{{.Host.Security.Rootless}}")
    rootless_value = rootless.split()[0] if rootless and rootless.split() else "tidak diketahui"
    checks.append(ToolCheck("podman rootless", rootless_value, "true", rootless_value == "true"))

    uv_version = _parse_version(_output("uv", "--version"))
    checks.append(ToolCheck("uv", _fmt(uv_version), "ada", uv_version is not None))

    node_version = _parse_version(_output("node", "--version"))
    checks.append(
        ToolCheck(
            "Node.js", _fmt(node_version), "24.x", bool(node_version and node_version[0] == 24)
        )
    )
    npm_version = _parse_version(_output("npm", "--version"))
    checks.append(ToolCheck("npm", _fmt(npm_version), "ada", npm_version is not None))

    py = sys.version_info
    checks.append(
        ToolCheck("Python (uv)", f"{py.major}.{py.minor}.{py.micro}", "3.13.x", py[:2] == (3, 13))
    )
    return checks


def ensure_env(example: Path = ENV_EXAMPLE, target: Path = ENV_FILE) -> list[tuple[str, str]]:
    """Create or complete ``target`` from ``example``. Returns ``(variable, status)`` pairs."""
    example_text = example.read_text(encoding="utf-8")
    example_values = parse_env(example_text)
    file_created = not target.exists()
    if file_created:
        write_text_private(target, example_text)
    current = read_env(target)

    updates: dict[str, str] = {}
    status: dict[str, str] = {}
    for key, default in example_values.items():
        if key not in current:
            updates[key] = default
            status[key] = "ditambahkan dari .env.example"

    merged = {**example_values, **current, **updates}
    for key in GENERATED_SECRETS:
        if key in merged and not merged[key]:
            updates[key] = secrets.token_urlsafe(24)
            status[key] = "diisi nilai acak"
    merged.update(updates)
    if "APP_DB_URL" in merged and not merged["APP_DB_URL"] and merged.get("APP_DB_PASSWORD"):
        updates["APP_DB_URL"] = APP_DB_URL_TEMPLATE.format(password=merged["APP_DB_PASSWORD"])
        status["APP_DB_URL"] = "disusun dari APP_DB_PASSWORD"
        merged.update(updates)

    if updates:
        set_env_values(target, updates)
    target.chmod(0o600)

    result = []
    for key in example_values:
        if key in status:
            result.append((key, status[key]))
        elif not merged.get(key):
            result.append((key, EMPTY_HINTS.get(key, "kosong")))
        else:
            result.append((key, "dibuat dari .env.example" if file_created else "sudah ada"))
    return result


def install_pre_commit_hook(repo: Path = REPO_ROOT) -> str:
    """Install the pre-commit hook (ruff, guard:secrets). Returns a short status message."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", "hooks"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "dilewati: bukan repo git"
    hooks_dir = (repo / result.stdout.strip()).resolve()
    hook = hooks_dir / "pre-commit"
    action = "dipasang"
    if hook.exists():
        current = hook.read_text(encoding="utf-8", errors="replace")
        if current == HOOK_SCRIPT and hook.stat().st_mode & 0o111:
            return "sudah terpasang"
        if HOOK_MARKER not in current:
            return (
                f"dilewati: {hook.name} lain sudah ada, tambahkan `npm run guard:secrets` sendiri"
            )
        action = "diperbarui"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK_SCRIPT, encoding="utf-8")
    hook.chmod(0o755)
    return action


def main() -> int:
    print("== Alat lokal")
    checks = check_tools()
    print_table(
        ("Alat", "Versi", "Syarat", "Status"),
        [(c.name, c.version, c.requirement, "OK" if c.ok else "GAGAL") for c in checks],
    )

    print("\n== Berkas .env (nilai tidak ditampilkan)")
    env_status = ensure_env()
    print_table(("Variabel", "Status"), env_status)

    print(f"\n== Hook pre-commit (ruff dan guard:secrets): {install_pre_commit_hook()}")

    failed = [c.name for c in checks if not c.ok]
    if failed:
        print(f"\nsetup selesai dengan masalah alat: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\nsetup selesai.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
