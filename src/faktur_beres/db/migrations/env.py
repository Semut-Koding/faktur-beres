"""Alembic environment: the URL comes from the caller, LangGraph's tables are left alone."""

from typing import Literal

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.schema import SchemaItem

from faktur_beres.db.migrate import CHECKPOINT_TABLES
from faktur_beres.db.models import Base


def include_object(
    item: SchemaItem,
    name: str | None,
    type_: Literal[
        "schema",
        "table",
        "column",
        "index",
        "unique_constraint",
        "foreign_key_constraint",
        "check_constraint",
    ],
    reflected: bool,
    compare_to: SchemaItem | None,
) -> bool:
    del item, reflected, compare_to
    return not (type_ == "table" and name in CHECKPOINT_TABLES)


def run() -> None:
    engine = create_engine(context.config.attributes["url"])
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                include_object=include_object,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run()
