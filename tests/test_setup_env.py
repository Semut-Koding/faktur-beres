"""Tests for .env handling in ``npm run setup``."""

import stat
from pathlib import Path

from faktur_beres.devtools.envfile import parse_env, read_env, set_env_values
from faktur_beres.devtools.setup import ENV_EXAMPLE, GENERATED_SECRETS, ensure_env

EXAMPLE = """\
# komentar
ODOO_URL=http://127.0.0.1:28069
ODOO_DB_PASSWORD=
ODOO_ADMIN_PASSWORD=
ODOO_ADMIN_API_KEY=
APP_DB_PASSWORD=
APP_DB_URL=
"""


def write_example(tmp_path: Path) -> Path:
    example = tmp_path / ".env.example"
    example.write_text(EXAMPLE, encoding="utf-8")
    return example


def test_creates_env_with_random_secrets(tmp_path: Path) -> None:
    example, target = write_example(tmp_path), tmp_path / ".env"

    status = dict(ensure_env(example, target))

    values = read_env(target)
    assert values["ODOO_URL"] == "http://127.0.0.1:28069"
    for key in ("ODOO_DB_PASSWORD", "ODOO_ADMIN_PASSWORD", "APP_DB_PASSWORD"):
        assert len(values[key]) >= 24
        assert status[key] == "diisi nilai acak"
    assert values["ODOO_DB_PASSWORD"] != values["ODOO_ADMIN_PASSWORD"]
    assert values["APP_DB_URL"] == (
        f"postgresql+psycopg://fakturberes:{values['APP_DB_PASSWORD']}@127.0.0.1:25432/fakturberes"
    )
    assert values["ODOO_ADMIN_API_KEY"] == ""
    assert "# komentar" in target.read_text(encoding="utf-8")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_second_run_changes_nothing(tmp_path: Path) -> None:
    example, target = write_example(tmp_path), tmp_path / ".env"
    ensure_env(example, target)
    before = target.read_text(encoding="utf-8")

    status = dict(ensure_env(example, target))

    assert target.read_text(encoding="utf-8") == before
    assert status["ODOO_DB_PASSWORD"] == "sudah ada"
    assert status["ODOO_ADMIN_API_KEY"].startswith("kosong")


def test_existing_values_are_never_overwritten(tmp_path: Path) -> None:
    example, target = write_example(tmp_path), tmp_path / ".env"
    target.write_text("ODOO_DB_PASSWORD=milik-pemilik-123\nAPP_DB_URL=postgresql://x\n")

    ensure_env(example, target)

    values = read_env(target)
    assert values["ODOO_DB_PASSWORD"] == "milik-pemilik-123"
    assert values["APP_DB_URL"] == "postgresql://x"
    assert values["ODOO_URL"] == "http://127.0.0.1:28069"  # missing key appended
    assert values["ODOO_ADMIN_PASSWORD"]  # empty secret filled


def test_repo_env_example_lists_m0_variables() -> None:
    values = parse_env(ENV_EXAMPLE.read_text(encoding="utf-8"))
    required = {
        "ODOO_URL",
        "ODOO_DB",
        "ODOO_DB_PASSWORD",
        "ODOO_ADMIN_PASSWORD",
        "ODOO_ADMIN_API_KEY",
        "ODOO_AGENT_API_KEY",
        "APP_DB_PASSWORD",
        "APP_DB_URL",
        "SMTP_HOST",
        "SMTP_PORT",
        "MAILPIT_URL",
        "GEMINI_API_KEY",
    }
    assert required <= values.keys()
    assert values["ODOO_URL"] == "http://127.0.0.1:28069"
    assert values["SMTP_PORT"] == "21025"
    assert values["MAILPIT_URL"] == "http://127.0.0.1:28025"
    for key in (*GENERATED_SECRETS, "ODOO_ADMIN_API_KEY", "ODOO_AGENT_API_KEY", "GEMINI_API_KEY"):
        assert values[key] == "", f"{key} di .env.example harus kosong"


def test_set_env_values_replaces_in_place_and_appends(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("# atas\nA=1\n\n# tengah\nB=2\n", encoding="utf-8")

    set_env_values(target, {"B": "baru", "C": "3"})

    assert target.read_text(encoding="utf-8") == "# atas\nA=1\n\n# tengah\nB=baru\nC=3\n"


def test_parse_env_handles_quotes_and_comments() -> None:
    text = "# X=1\nA='satu dua'\nexport B=\"tiga\"\nC = empat \n"
    assert parse_env(text) == {"A": "satu dua", "B": "tiga", "C": "empat"}
