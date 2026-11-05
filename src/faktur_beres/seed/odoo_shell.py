"""Run an ORM script inside the running ``odoo`` container through ``odoo shell``.

Used only by developer scripts (``npm run odoo:init``) for what the JSON-2 API cannot do, such as
creating API keys. Parameters and secrets travel as environment variables of the ``podman exec``
process (``-e NAME`` without a value), so they never appear on a command line or in the output.
The script prints one line ``FB_RESULT <json>``; everything else on stdout is ignored.
"""

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from faktur_beres.config import REPO_ROOT

PROJECT = "fakturberes"
SHELL_DIR = REPO_ROOT / "infra" / "odoo" / "shell"
RESULT_MARKER = "FB_RESULT "


class OdooShellError(RuntimeError):
    """The ORM script failed; the message carries the tail of its log output."""


def run_script(
    name: str,
    params: Mapping[str, Any],
    *,
    secrets: Mapping[str, str] | None = None,
    db: str = "fakturberes",
    timeout: float = 600,
) -> dict[str, Any]:
    script: Path = SHELL_DIR / name
    env = dict(os.environ, PODMAN_COMPOSE_WARNING_LOGS="false")
    env["FB_PARAMS"] = json.dumps(params)
    names = ["FB_PARAMS"]
    for key, value in (secrets or {}).items():
        env[key] = value
        names.append(key)

    cmd = ["podman", "compose", "-p", PROJECT, "exec", "-T"]
    for key in names:
        cmd += ["-e", key]
    cmd += ["odoo", "odoo", "shell", "-d", db, "--no-http", "--log-level=warn"]

    proc = subprocess.run(
        cmd,
        input=script.read_bytes(),
        capture_output=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=timeout,
        check=False,
    )
    stdout = proc.stdout.decode("utf-8", "replace")
    lines = [line for line in stdout.splitlines() if line.startswith(RESULT_MARKER)]
    if proc.returncode != 0 or not lines:
        tail = "\n".join(proc.stderr.decode("utf-8", "replace").strip().splitlines()[-20:])
        raise OdooShellError(f"skrip {name} gagal (kode {proc.returncode}):\n{tail}")
    return json.loads(lines[-1][len(RESULT_MARKER) :])
