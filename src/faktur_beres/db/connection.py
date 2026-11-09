"""Database URLs and engines. The URL holds a password, so it is never printed or logged."""

from sqlalchemy import Engine, create_engine, make_url

from faktur_beres.config import Settings

DRIVER = "postgresql+psycopg"
REQUIRED_PREFIX = "fakturberes"


class DatabaseNotConfigured(RuntimeError):
    """``APP_DB_URL`` is empty."""


def app_url(settings: Settings) -> str:
    if settings.app_db_url is None:
        raise DatabaseNotConfigured("APP_DB_URL kosong: jalankan npm run setup.")
    url = make_url(settings.app_db_url.get_secret_value())
    return url.set(drivername=DRIVER).render_as_string(hide_password=False)


def libpq_url(url: str) -> str:
    """The same database as a plain ``postgresql://`` URL, for LangGraph and psycopg."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def database_name(url: str) -> str:
    name = make_url(url).database
    if not name:
        raise ValueError("URL basis data tanpa nama database")
    return name


def other_database(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def require_project_database(url: str) -> None:
    """Destructive tools refuse any database that is not this project's."""
    name = database_name(url)
    if not name.startswith(REQUIRED_PREFIX):
        raise ValueError(f"database {name!r} ditolak: nama harus berawalan {REQUIRED_PREFIX}")


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)
