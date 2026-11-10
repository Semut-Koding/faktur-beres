"""Tables of the application database for M4a (SPEC §9.2).

``vendor_memory`` and ``policy_chunks`` come with M4b. The tables of the LangGraph checkpointer are
created by LangGraph itself and are not managed here (ADR-0009).

Enum columns are text with a ``CHECK``, so a new value is a small migration. Money, quantities and
costs are ``numeric``, read as ``Decimal``.
"""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Sequence,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from faktur_beres.domain.invoice import Channel
from faktur_beres.domain.reasons import InvoiceStatus, Route
from faktur_beres.llm.embedding_contract import EMBEDDING_DIMENSIONS

MONEY = Numeric(18, 2)
QUANTITY = Numeric(18, 4)
PRICE = Numeric(18, 4)
COST = Numeric(18, 8)
INVOICE_ID_SEQUENCE = Sequence("invoice_number_seq")


class ActionKind(StrEnum):
    CREATE_DRAFT_BILL = "create_draft_bill"
    ATTACH_FILE = "attach_file"
    POST_NOTE = "post_note"
    SEND_EMAIL = "send_email"


class ActionStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CLARIFY = "clarify"


class AuditKind(StrEnum):
    NODE = "node"
    TOOL = "tool"
    LLM = "llm"
    GUARD = "guard"
    DECISION = "decision"
    SECURITY = "security"


class InboxKind(StrEnum):
    FAKTUR = "faktur"
    BALASAN = "balasan"
    ABAIKAN = "abaikan"
    DITOLAK = "ditolak"


class InboxStatus(StrEnum):
    PROCESSING = "diproses"
    DONE = "selesai"


class MatchMethod(StrEnum):
    VENDOR_CODE = "vendor_code"
    INTERNAL_CODE = "internal_code"
    NAME = "name"
    LLM = "llm"


def one_of(column: str, values: Iterable[StrEnum]) -> str:
    """The body of a ``CHECK`` that keeps ``column`` inside an enum (NULL passes)."""
    quoted = ", ".join(f"'{value.value}'" for value in values)
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint(one_of("status", InvoiceStatus), name="status"),
        CheckConstraint(one_of("route", Route), name="route"),
        CheckConstraint(one_of("channel", Channel), name="channel"),
        Index("ix_invoices_status", "status"),
        Index("ix_invoices_file_sha256", "file_sha256"),
        Index("ix_invoices_vendor_number", "vendor_id", "invoice_number_normalized"),
    )

    invoice_id: Mapped[str] = mapped_column(Text, primary_key=True)
    # intake
    channel: Mapped[str] = mapped_column(Text)
    sender_email: Mapped[str | None] = mapped_column(Text)
    email_subject: Mapped[str | None] = mapped_column(Text)
    email_body: Mapped[str | None] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[str] = mapped_column(Text)
    file_size: Mapped[int] = mapped_column(BigInteger)
    file_mime: Mapped[str] = mapped_column(Text)
    stored_path: Mapped[str] = mapped_column(Text)
    duplicate_of: Mapped[str | None] = mapped_column(ForeignKey("invoices.invoice_id"))
    source_message_id: Mapped[str | None] = mapped_column(Text)
    # outcome
    status: Mapped[str] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(Text)
    allowed_routes: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    reasons: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    failure_message: Mapped[str | None] = mapped_column(Text)
    vendor_id: Mapped[int | None] = mapped_column(Integer)
    vendor_name: Mapped[str | None] = mapped_column(Text)
    po_number: Mapped[str | None] = mapped_column(Text)
    invoice_number: Mapped[str | None] = mapped_column(Text)
    invoice_number_normalized: Mapped[str | None] = mapped_column(Text)
    invoice_date: Mapped[date | None] = mapped_column(Date)
    total: Mapped[Decimal | None] = mapped_column(MONEY)
    # snapshots (JSON of the extraction and of the analysis, with their schema version)
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_schema: Mapped[int | None] = mapped_column(Integer)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    analysis_schema: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    # time
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    route_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InvoiceFile(Base):
    """Every file an invoice has had, one per revision; ``invoices`` holds the current one."""

    __tablename__ = "invoice_files"
    __table_args__ = (UniqueConstraint("invoice_id", "revision", name="uq_invoice_files_revision"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.invoice_id"))
    revision: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InboxMessage(Base):
    """One message of the AP inbox: the source of truth for what has been handled (F-02)."""

    __tablename__ = "inbox_messages"
    __table_args__ = (
        CheckConstraint(one_of("kind", InboxKind), name="kind"),
        CheckConstraint(one_of("status", InboxStatus), name="status"),
        UniqueConstraint("message_id", name="uq_inbox_messages_message_id"),
        Index("ix_inbox_messages_invoice_id", "invoice_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sender: Mapped[str | None] = mapped_column(Text)
    recipient: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(Text)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.invoice_id"))
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InvoiceLineRow(Base):
    __tablename__ = "invoice_lines"
    __table_args__ = (
        UniqueConstraint("invoice_id", "revision", "position", name="uq_invoice_lines_position"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.invoice_id"))
    revision: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    vendor_code: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    unit: Mapped[str] = mapped_column(Text)
    unit_price: Mapped[Decimal] = mapped_column(PRICE)
    amount: Mapped[Decimal] = mapped_column(MONEY)


class MatchResultRow(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        CheckConstraint(one_of("method", MatchMethod), name="method"),
        UniqueConstraint("invoice_id", "revision", "position", name="uq_match_results_position"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.invoice_id"))
    revision: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text)
    method: Mapped[str | None] = mapped_column(Text)
    po_number: Mapped[str | None] = mapped_column(Text)
    po_line_id: Mapped[int | None] = mapped_column(Integer)
    price_ok: Mapped[bool | None] = mapped_column(Boolean)
    quantity_ok: Mapped[bool | None] = mapped_column(Boolean)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )


class ActionRow(Base):
    __tablename__ = "actions"
    __table_args__ = (
        CheckConstraint(one_of("kind", ActionKind), name="kind"),
        CheckConstraint(one_of("status", ActionStatus), name="status"),
        UniqueConstraint("idempotency_key", name="uq_actions_idempotency_key"),
        Index("ix_actions_invoice_id", "invoice_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(Text)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.invoice_id"))
    kind: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    content_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApprovalRow(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(one_of("decision", Decision), name="decision"),
        Index("ix_approvals_invoice_id", "invoice_id"),
        # At most one open request per invoice.
        Index(
            "uq_approvals_open",
            "invoice_id",
            unique=True,
            postgresql_where=text("decision IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.invoice_id"))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    reasons: Mapped[list[str]] = mapped_column(JSONB)
    proposed_action: Mapped[str] = mapped_column(Text)
    options: Mapped[list[str]] = mapped_column(JSONB)
    decision: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyChunkRow(Base):
    """One article of a policy document with its embedding (derived data: ``db:reset`` keeps it)."""

    __tablename__ = "policy_chunks"
    __table_args__ = (
        UniqueConstraint("ref", name="uq_policy_chunks_ref"),
        Index(
            "ix_policy_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ref: Mapped[str] = mapped_column(Text)
    document: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    model: Mapped[str] = mapped_column(Text)
    dimensions: Mapped[int] = mapped_column(Integer)
    chunk_format: Mapped[int] = mapped_column(Integer)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyReasonQuote(Base):
    """The best articles for one reason code, found at indexing time, so ``decide`` needs no API."""

    __tablename__ = "policy_reason_quotes"
    __table_args__ = (UniqueConstraint("reason", "ref", name="uq_policy_reason_quotes_reason"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    reason: Mapped[str] = mapped_column(Text)
    ref: Mapped[str] = mapped_column(Text)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    query_sha256: Mapped[str] = mapped_column(Text)


class AuditEntry(Base):
    """BR-23: parameters are masked (BR-24) before they get here."""

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(one_of("kind", AuditKind), name="kind"),
        Index("ix_audit_log_invoice_id", "invoice_id", "id"),
        Index("ix_audit_log_kind", "kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.invoice_id"))
    node: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    params: Mapped[Any | None] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    thinking_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(COST)
    model: Mapped[str | None] = mapped_column(Text)
    fallback: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text)
