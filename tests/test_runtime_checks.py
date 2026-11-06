"""Tests for the runtime checks in odoo:check (odoo.conf and container env names)."""

import subprocess
from pathlib import Path

from faktur_beres.dataset import load_dataset
from faktur_beres.seed.check import FAIL, INFO, OK, Checker
from faktur_beres.seed.runtime import (
    ENV_NAMES_TEMPLATE,
    FORBIDDEN_CONTAINER_ENV,
    container_env_names,
    forbidden_env,
    odoo_conf_option,
)


class NoOdoo:
    def search_read(self, *args, **kwargs):  # XmlIds() loads fb_seed IDs on construction
        return []


def fake_podman(ps_out: str, inspect_out: dict[str, str]):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "ps":
            return subprocess.CompletedProcess(cmd, 0, stdout=ps_out, stderr="")
        if cmd[1] == "inspect":
            return subprocess.CompletedProcess(cmd, 0, stdout=inspect_out[cmd[-1]], stderr="")
        raise AssertionError(cmd)

    return run, calls


def test_odoo_conf_option(tmp_path: Path) -> None:
    conf = tmp_path / "odoo.conf"
    conf.write_text("; komentar\n[options]\nworkers = 0\nmax_cron_threads = 0\n", encoding="utf-8")
    assert odoo_conf_option("max_cron_threads", conf) == "0"
    assert odoo_conf_option("db_name", conf) is None


def test_repo_odoo_conf_disables_cron() -> None:
    assert odoo_conf_option("max_cron_threads") == "0"


def test_env_names_are_read_by_name_only() -> None:
    run, calls = fake_podman(
        "fakturberes_odoo_1\nfakturberes_app-db_1\nlain_postgres_1\n",
        {
            "fakturberes_odoo_1": "PATH\nPGPASSWORD\nODOO_RC\n",
            "fakturberes_app-db_1": "POSTGRES_USER\nPOSTGRES_PASSWORD\nPATH\n",
        },
    )

    names = container_env_names(run)

    assert names == {
        "fakturberes_app-db_1": ["PATH", "POSTGRES_PASSWORD", "POSTGRES_USER"],
        "fakturberes_odoo_1": ["ODOO_RC", "PATH", "PGPASSWORD"],
    }
    inspects = [c for c in calls if c[1] == "inspect"]
    assert all(c[3] == ENV_NAMES_TEMPLATE for c in inspects)
    assert "split" in ENV_NAMES_TEMPLATE  # the template prints the part before "=" only
    assert "lain_postgres_1" not in {c[-1] for c in inspects}


def test_forbidden_env_detection() -> None:
    names = {"fakturberes_odoo_1": ["PATH", "ODOO_AGENT_API_KEY"], "fakturberes_mailpit_1": ["X"]}
    assert forbidden_env(names) == {"fakturberes_odoo_1": ["ODOO_AGENT_API_KEY"]}
    assert set(FORBIDDEN_CONTAINER_ENV) == {
        "ODOO_ADMIN_API_KEY",
        "ODOO_AGENT_API_KEY",
        "ODOO_ADMIN_PASSWORD",
        "GEMINI_API_KEY",
    }


def runtime_rows(env: dict[str, list[str]], cron: str | None) -> list[tuple[str, str, str]]:
    checker = Checker(
        NoOdoo(), None, load_dataset(), env_names=lambda: env, conf_option=lambda name: cron
    )
    checker.check_runtime()
    return checker.rows


def test_runtime_rows_pass() -> None:
    rows = runtime_rows({"fakturberes_odoo_1": ["PATH", "PGPASSWORD"]}, "0")
    assert [r[2] for r in rows] == [OK, OK, INFO]
    assert rows[2][1] == "PATH, PGPASSWORD"


def test_runtime_rows_fail_on_cron_or_secret() -> None:
    rows = runtime_rows({"fakturberes_odoo_1": ["GEMINI_API_KEY"]}, "2")
    assert [r[2] for r in rows[:2]] == [FAIL, FAIL]
    assert "GEMINI_API_KEY" in rows[1][1]


def test_runtime_rows_fail_without_containers() -> None:
    rows = runtime_rows({}, "0")
    assert [r[2] for r in rows] == [OK, FAIL]
