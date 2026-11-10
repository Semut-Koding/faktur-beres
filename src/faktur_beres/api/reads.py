"""What the API reads from the application database, as plain dictionaries (not yet masked)."""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

from faktur_beres.db.models import (
    ActionRow,
    ApprovalRow,
    AuditEntry,
    AuditKind,
    Invoice,
    MatchResultRow,
)
from faktur_beres.db.store import UnknownInvoice


def columns(row: Any) -> dict[str, Any]:
    """The mapped columns of an ORM row."""
    return {column.key: getattr(row, column.key) for column in row.__table__.columns}


class Reads(Protocol):
    def invoices(self) -> list[dict[str, Any]]: ...

    def invoice(self, invoice_id: str) -> dict[str, Any]: ...

    def matches(self, invoice_id: str, revision: int) -> list[dict[str, Any]]: ...

    def actions(self, invoice_id: str) -> list[dict[str, Any]]: ...

    def approval(self, invoice_id: str) -> dict[str, Any] | None: ...

    def trail(self, invoice_id: str) -> list[dict[str, Any]]: ...

    def last_audit_id(self) -> int: ...

    def changes_since(self, audit_id: int) -> Sequence[tuple[int, str | None]]: ...

    def approval_invoice_ids(self) -> set[str]: ...

    def llm_totals(self) -> tuple[int, Decimal]: ...


class DbReads:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def invoices(self) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(select(Invoice).order_by(Invoice.invoice_id.desc()))
            return [columns(row) for row in rows]

    def invoice(self, invoice_id: str) -> dict[str, Any]:
        with self._sessions() as session:
            row = session.get(Invoice, invoice_id)
        if row is None:
            raise UnknownInvoice(invoice_id)
        return columns(row)

    def matches(self, invoice_id: str, revision: int) -> list[dict[str, Any]]:
        query = (
            select(MatchResultRow)
            .where(MatchResultRow.invoice_id == invoice_id, MatchResultRow.revision == revision)
            .order_by(MatchResultRow.position)
        )
        with self._sessions() as session:
            return [columns(row) for row in session.scalars(query)]

    def actions(self, invoice_id: str) -> list[dict[str, Any]]:
        query = select(ActionRow).where(ActionRow.invoice_id == invoice_id).order_by(ActionRow.id)
        with self._sessions() as session:
            return [columns(row) for row in session.scalars(query)]

    def approval(self, invoice_id: str) -> dict[str, Any] | None:
        """The open request, else the last decided one (as ``Store.current_approval``)."""
        query = (
            select(ApprovalRow)
            .where(ApprovalRow.invoice_id == invoice_id)
            .order_by(ApprovalRow.decision.is_not(None), ApprovalRow.id.desc())
            .limit(1)
        )
        with self._sessions() as session:
            row = session.scalar(query)
        return columns(row) if row is not None else None

    def trail(self, invoice_id: str) -> list[dict[str, Any]]:
        query = (
            select(AuditEntry).where(AuditEntry.invoice_id == invoice_id).order_by(AuditEntry.id)
        )
        with self._sessions() as session:
            return [columns(row) for row in session.scalars(query)]

    def last_audit_id(self) -> int:
        with self._sessions() as session:
            return session.scalar(select(func.max(AuditEntry.id))) or 0

    def changes_since(self, audit_id: int) -> Sequence[tuple[int, str | None]]:
        query = (
            select(AuditEntry.id, AuditEntry.invoice_id)
            .where(AuditEntry.id > audit_id)
            .order_by(AuditEntry.id)
            .limit(500)
        )
        with self._sessions() as session:
            return [(row[0], row[1]) for row in session.execute(query)]

    def approval_invoice_ids(self) -> set[str]:
        with self._sessions() as session:
            return set(session.scalars(select(ApprovalRow.invoice_id).distinct()))

    def llm_totals(self) -> tuple[int, Decimal]:
        query = select(func.count(), func.coalesce(func.sum(AuditEntry.cost_usd), 0)).where(
            AuditEntry.kind == AuditKind.LLM.value
        )
        with self._sessions() as session:
            count, cost = session.execute(query).one()
        return int(count), Decimal(cost or 0)
