"""Alembic from code (no ``alembic.ini``), so the repository path may contain spaces.

``CHECKPOINT_TABLES`` belong to LangGraph: ``include_object`` keeps them out of Alembic.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import text

from faktur_beres.db.connection import libpq_url, make_engine, require_project_database
from faktur_beres.db.models import Base

MIGRATIONS = Path(__file__).parent / "migrations"
#: Derived from the policy documents, not process data; ``eval:graph`` resets before every scenario.
PRESERVED_ON_RESET = frozenset({"policy_chunks", "policy_reason_quotes"})
CHECKPOINT_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS).replace("%", "%%"))
    config.attributes["url"] = url
    return config


def setup_checkpointer(url: str) -> None:
    with PostgresSaver.from_conn_string(libpq_url(url)) as saver:
        saver.setup()


def upgrade(url: str) -> None:
    """Alembic to head, then the checkpointer's own tables."""
    command.upgrade(alembic_config(url), "head")
    setup_checkpointer(url)


def check(url: str) -> None:
    """Raises ``alembic.util.exc.CommandError`` when the models differ from the migrations."""
    command.check(alembic_config(url))


def reset(url: str) -> list[str]:
    """Empty the application data and the checkpoints; the schema and version table stay."""
    require_project_database(url)
    engine = make_engine(url)
    try:
        with engine.begin() as connection:
            present = set(
                connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
                )
            )
            tables = sorted(
                (
                    ({t.name for t in Base.metadata.sorted_tables} - PRESERVED_ON_RESET)
                    | (CHECKPOINT_TABLES - {"checkpoint_migrations"})
                )
                & present
            )
            if tables:
                names = ", ".join(f'"{name}"' for name in tables)
                connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
            connection.execute(text("ALTER SEQUENCE IF EXISTS invoice_number_seq RESTART"))
    finally:
        engine.dispose()
    return tables
