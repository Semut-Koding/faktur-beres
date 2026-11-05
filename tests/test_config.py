"""Tests for settings loading."""

from pathlib import Path

import pytest

from faktur_beres.config import REPO_ROOT, Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_ADMIN_API_KEY", "ODOO_AGENT_API_KEY", "SMTP_PORT"):
        monkeypatch.delenv(name, raising=False)


def test_repo_root_points_at_repository() -> None:
    assert (REPO_ROOT / "pyproject.toml").is_file()


def test_defaults_without_env_file(tmp_path: Path) -> None:
    settings = Settings(_env_file=tmp_path / "tidak-ada.env")
    assert settings.odoo_url == "http://127.0.0.1:28069"
    assert settings.odoo_db == "fakturberes"
    assert settings.smtp_port == 21025
    assert settings.odoo_agent_api_key is None


def test_values_from_env_file_and_empty_means_unset(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "ODOO_URL=http://127.0.0.1:28069\n"
        "ODOO_AGENT_API_KEY=rahasia-agent-123456\n"
        "ODOO_ADMIN_API_KEY=\n"
        "SMTP_PORT=21025\n"
        "VARIABEL_LAIN=diabaikan\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env)
    assert settings.odoo_agent_api_key is not None
    assert settings.odoo_agent_api_key.get_secret_value() == "rahasia-agent-123456"
    assert settings.odoo_admin_api_key is None


def test_secrets_are_hidden_in_repr(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("ODOO_AGENT_API_KEY=rahasia-agent-123456\n", encoding="utf-8")
    settings = Settings(_env_file=env)
    assert "rahasia-agent-123456" not in repr(settings)
    assert "rahasia-agent-123456" not in str(settings.model_dump())
