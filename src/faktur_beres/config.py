"""Settings loaded from environment variables and the repository's ``.env`` file.

Secrets are typed as ``SecretStr`` so they never appear in ``repr``, logs, or tracebacks.
Call ``get_secret_value()`` only at the point where the value is actually needed.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Runtime configuration for scripts and (later) the agent."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Odoo 19 (External JSON-2 API)
    odoo_url: str = "http://127.0.0.1:28069"
    odoo_db: str = "fakturberes"
    odoo_timeout: float = 30.0
    odoo_db_password: SecretStr | None = None
    odoo_admin_password: SecretStr | None = None
    odoo_admin_api_key: SecretStr | None = None
    odoo_agent_api_key: SecretStr | None = None

    # Application database (PostgreSQL 18 + pgvector)
    app_db_password: SecretStr | None = None
    app_db_url: SecretStr | None = None

    # Mailpit (SMTP sandbox and API)
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 21025
    mailpit_url: str = "http://127.0.0.1:28025"

    # LLM (filled in by the owner from M3 onwards)
    gemini_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
