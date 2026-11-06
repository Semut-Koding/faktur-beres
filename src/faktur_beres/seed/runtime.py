"""Checks on the local runtime that do not go through Odoo: odoo.conf and container env.

Container environment variables are read by **name only**: the Go template given to
``podman inspect`` splits every ``NAME=value`` entry and prints the name, so values never leave
podman and are never seen by this process.
"""

import configparser
import subprocess
from collections.abc import Callable
from pathlib import Path

from faktur_beres.config import REPO_ROOT

PROJECT = "fakturberes"
ODOO_CONF = REPO_ROOT / "infra" / "odoo" / "odoo.conf"
#: Application secrets that must never be passed to a container environment (ADR-0003).
FORBIDDEN_CONTAINER_ENV = (
    "ODOO_ADMIN_API_KEY",
    "ODOO_AGENT_API_KEY",
    "ODOO_ADMIN_PASSWORD",
    "GEMINI_API_KEY",
)
ENV_NAMES_TEMPLATE = '{{range .Config.Env}}{{index (split . "=") 0}}{{"\\n"}}{{end}}'

Runner = Callable[..., subprocess.CompletedProcess]


def odoo_conf_option(name: str, path: Path = ODOO_CONF) -> str | None:
    """Value of ``name`` in the ``[options]`` section of odoo.conf (``None`` if absent)."""
    parser = configparser.ConfigParser()
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser.get("options", name, fallback=None)


def _podman(args: list[str], run: Runner) -> str:
    result = run(["podman", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"podman {args[0]} gagal: {result.stderr.strip()[:200]}")
    return result.stdout


def running_project_containers(run: Runner = subprocess.run) -> list[str]:
    """Names of the running containers of compose project ``fakturberes``."""
    out = _podman(
        ["ps", "--filter", f"label=io.podman.compose.project={PROJECT}", "--format", "{{.Names}}"],
        run,
    )
    return sorted(name for name in out.split() if name.startswith(f"{PROJECT}_"))


def container_env_names(run: Runner = subprocess.run) -> dict[str, list[str]]:
    """Environment variable names (never values) per running ``fakturberes_*`` container."""
    names: dict[str, list[str]] = {}
    for container in running_project_containers(run):
        out = _podman(["inspect", "--format", ENV_NAMES_TEMPLATE, container], run)
        names[container] = sorted({line.strip() for line in out.splitlines() if line.strip()})
    return names


def forbidden_env(names: dict[str, list[str]]) -> dict[str, list[str]]:
    """Containers whose environment carries one of :data:`FORBIDDEN_CONTAINER_ENV`."""
    found = {}
    for container, env in names.items():
        bad = sorted(set(env) & set(FORBIDDEN_CONTAINER_ENV))
        if bad:
            found[container] = bad
    return found
