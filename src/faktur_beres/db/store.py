"""``Store``: every read and write of the application database the agent does.

Each method is one short transaction. Status changes go through ``domain.status`` and are audited.
Parameters given to ``audit`` must already be masked (BR-24): callers pass them through
``mask_params``, and this module does it once more as a safety net.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from faktur_beres.db.models import (
    INVOICE_ID_SEQUENCE,
    ActionKind,
    ActionRow,
    ActionStatus,
    ApprovalRow,
    AuditEntry,
    AuditKind,
    Decision,
    InboxKind,
    InboxMessage,
    InboxStatus,
    Invoice,
    InvoiceFile,
    InvoiceLineRow,
    MatchResultRow,
)
from faktur_beres.domain.erp import RegisteredInvoice
from faktur_beres.domain.invoice import Channel, InvoiceData
from faktur_beres.domain.normalize import normalize_invoice_number
from faktur_beres.domain.reasons import InvoiceStatus, ReasonCode, Route
from faktur_beres.domain.rules.safety import mask_params
from faktur_beres.domain.status import check_transition
from faktur_beres.llm.contracts import LlmCallRecord

EXTRACTION_SCHEMA = 1
ANALYSIS_SCHEMA = 1


class UnknownInvoice(LookupError):
    def __init__(self, invoice_id: str) -> None:
        super().__init__(f"faktur {invoice_id} tidak ditemukan")
        self.invoice_id = invoice_id


@dataclass(frozen=True)
class NewInvoice:
    channel: Channel
    file_name: str
    file_sha256: str
    file_size: int
    file_mime: str
    sender_email: str | None = None
    email_subject: str | None = None
    email_body: str | None = None
    source_message_id: str | None = None


@dataclass(frozen=True)
class NewFile:
    """The file of one revision of an invoice."""

    path: str
    sha256: str
    mime: str
    size: int
    name: str
    source: str


@dataclass(frozen=True)
class LineOutcome:
    position: int
    status: str
    method: str | None
    po_number: str | None
    po_line_id: int | None
    price_ok: bool | None
    quantity_ok: bool | None
    evidence: Sequence[dict[str, Any]]


@dataclass(frozen=True)
class ActionState:
    key: str
    status: ActionStatus
    attempts: int
    result: dict[str, Any] | None
    error: str | None

    @property
    def done(self) -> bool:
        return self.status is ActionStatus.DONE


@dataclass(frozen=True)
class OpenApproval:
    id: int
    invoice_id: str
    evidence: dict[str, Any]
    reasons: tuple[str, ...]
    proposed_action: str
    options: tuple[str, ...]
    decision: str | None
    decided_by: str | None
    note: str | None
    decided_at: datetime | None


def format_invoice_id(number: int) -> str:
    return f"FB-{number:06d}"


def _approval(row: ApprovalRow) -> OpenApproval:
    return OpenApproval(
        id=row.id,
        invoice_id=row.invoice_id,
        evidence=row.evidence,
        reasons=tuple(row.reasons),
        proposed_action=row.proposed_action,
        options=tuple(row.options),
        decision=row.decision,
        decided_by=row.decided_by,
        note=row.note,
        decided_at=row.decided_at,
    )


class Store:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def _session(self) -> Iterator[Session]:
        with self._sessions.begin() as session:
            yield session

    # --- invoices ---------------------------------------------------------------------------

    def create_invoice(self, new: NewInvoice) -> str:
        """A new thread (BR-01): its ``invoice_id`` also names the LangGraph thread."""
        with self._session() as session:
            number = session.scalar(INVOICE_ID_SEQUENCE.next_value())
            assert number is not None
            invoice_id = format_invoice_id(number)
            session.add(
                Invoice(
                    invoice_id=invoice_id,
                    channel=new.channel.value,
                    sender_email=new.sender_email,
                    email_subject=new.email_subject,
                    email_body=new.email_body,
                    source_message_id=new.source_message_id,
                    file_name=new.file_name,
                    file_sha256=new.file_sha256,
                    file_size=new.file_size,
                    file_mime=new.file_mime,
                    stored_path="",
                    status=InvoiceStatus.RECEIVED.value,
                )
            )
        return invoice_id

    def get(self, invoice_id: str) -> Invoice:
        with self._sessions() as session:
            row = session.get(Invoice, invoice_id)
        if row is None:
            raise UnknownInvoice(invoice_id)
        return row

    def list_invoices(self) -> list[Invoice]:
        with self._sessions() as session:
            return list(session.scalars(select(Invoice).order_by(Invoice.invoice_id)))

    def set_stored_path(self, invoice_id: str, path: str) -> None:
        with self._session() as session:
            row = session.get(Invoice, invoice_id, with_for_update=True)
            if row is None:
                raise UnknownInvoice(invoice_id)
            row.stored_path = path
            session.execute(
                insert(InvoiceFile)
                .values(
                    invoice_id=invoice_id,
                    revision=row.revision or 1,
                    path=path,
                    sha256=row.file_sha256,
                    mime=row.file_mime,
                    size=row.file_size,
                    name=row.file_name,
                    source=row.channel,
                )
                .on_conflict_do_nothing(constraint="uq_invoice_files_revision")
            )

    def add_revision(self, invoice_id: str, revision: int, file: NewFile) -> None:
        """A newer file of the invoice; a repeated call for the same revision changes nothing."""
        with self._session() as session:
            row = session.get(Invoice, invoice_id, with_for_update=True)
            if row is None:
                raise UnknownInvoice(invoice_id)
            session.execute(
                insert(InvoiceFile)
                .values(invoice_id=invoice_id, revision=revision, **file.__dict__)
                .on_conflict_do_nothing(constraint="uq_invoice_files_revision")
            )
            row.revision = revision
            row.stored_path = file.path
            row.file_sha256 = file.sha256
            row.file_mime = file.mime
            row.file_size = file.size
            row.file_name = file.name

    def files_of(self, invoice_id: str) -> list[InvoiceFile]:
        query = (
            select(InvoiceFile)
            .where(InvoiceFile.invoice_id == invoice_id)
            .order_by(InvoiceFile.revision)
        )
        with self._sessions() as session:
            return list(session.scalars(query))

    # --- inbox (F-02) ---------------------------------------------------------------------------

    def inbox_done(self, message_ids: Sequence[str]) -> set[str]:
        query = select(InboxMessage.message_id).where(
            InboxMessage.message_id.in_(list(message_ids)),
            InboxMessage.status == InboxStatus.DONE.value,
        )
        with self._sessions() as session:
            return set(session.scalars(query))

    def inbox_begin(
        self,
        message_id: str,
        *,
        received_at: datetime | None,
        sender: str | None,
        recipient: str | None,
        subject: str | None,
    ) -> InboxMessage:
        """The row of a message, created on first sight."""
        with self._session() as session:
            session.execute(
                insert(InboxMessage)
                .values(
                    message_id=message_id,
                    received_at=received_at,
                    sender=sender,
                    recipient=recipient,
                    subject=subject,
                    status=InboxStatus.PROCESSING.value,
                )
                .on_conflict_do_nothing(constraint="uq_inbox_messages_message_id")
            )
            return session.scalars(
                select(InboxMessage).where(InboxMessage.message_id == message_id)
            ).one()

    def inbox_progress(self, message_id: str, attachments: Sequence[dict[str, Any]]) -> None:
        with self._session() as session:
            session.execute(
                update(InboxMessage)
                .where(InboxMessage.message_id == message_id)
                .values(attachments=list(attachments))
            )

    def inbox_finish(
        self,
        message_id: str,
        *,
        kind: InboxKind,
        invoice_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        with self._session() as session:
            session.execute(
                update(InboxMessage)
                .where(InboxMessage.message_id == message_id)
                .values(
                    kind=kind.value,
                    invoice_id=invoice_id,
                    reason=reason,
                    status=InboxStatus.DONE.value,
                )
            )

    def inbox_rows(self) -> list[InboxMessage]:
        with self._sessions() as session:
            return list(session.scalars(select(InboxMessage).order_by(InboxMessage.id)))

    def invoice_for_message(self, message_id: str, sha256: str) -> str | None:
        """The invoice already made from this attachment of this message, if any."""
        query = select(Invoice.invoice_id).where(
            Invoice.source_message_id == message_id, Invoice.file_sha256 == sha256
        )
        with self._sessions() as session:
            return session.scalar(query)

    def invoice_for_outgoing(self, message_ids: Sequence[str]) -> str | None:
        """The invoice one of whose outgoing e-mails has one of these Message-IDs."""
        if not message_ids:
            return None
        query = select(ActionRow.invoice_id).where(
            ActionRow.kind == ActionKind.SEND_EMAIL.value,
            ActionRow.result["message_id"].astext.in_(list(message_ids)),
        )
        with self._sessions() as session:
            return session.scalar(query)

    def first_with_hash(self, sha256: str, *, excluding: str) -> Invoice | None:
        """The earliest other invoice with the same file (BR-01)."""
        query = (
            select(Invoice)
            .where(Invoice.file_sha256 == sha256, Invoice.invoice_id != excluding)
            .order_by(Invoice.invoice_id)
            .limit(1)
        )
        with self._sessions() as session:
            return session.scalar(query)

    def mark_duplicate_of(self, invoice_id: str, original: str) -> None:
        with self._session() as session:
            session.execute(
                update(Invoice)
                .where(Invoice.invoice_id == invoice_id)
                .values(duplicate_of=original)
            )

    def set_status(
        self,
        invoice_id: str,
        new: InvoiceStatus,
        *,
        node: str | None = None,
        message: str | None = None,
    ) -> InvoiceStatus:
        """Change the status through the table of SPEC §6.3 and audit it; same status is a no-op."""
        with self._session() as session:
            row = session.get(Invoice, invoice_id, with_for_update=True)
            if row is None:
                raise UnknownInvoice(invoice_id)
            current = InvoiceStatus(row.status)
            if current is new and message is None:
                return current
            if current is not new:
                check_transition(current, new)
            row.status = new.value
            row.failure_message = message
            session.add(
                AuditEntry(
                    invoice_id=invoice_id,
                    node=node,
                    kind=AuditKind.DECISION.value,
                    name="status",
                    summary=f"{current.value} -> {new.value}",
                    status="ok",
                )
            )
        return new

    def save_extraction(self, invoice_id: str, invoice: InvoiceData | None, dump: Any) -> None:
        values: dict[str, Any] = {"extraction": dump, "extraction_schema": EXTRACTION_SCHEMA}
        if invoice is not None:
            values |= {
                "invoice_number": invoice.invoice_number,
                "invoice_number_normalized": normalize_invoice_number(invoice.invoice_number),
                "invoice_date": invoice.invoice_date,
                "total": invoice.total,
            }
        with self._session() as session:
            session.execute(update(Invoice).where(Invoice.invoice_id == invoice_id).values(values))

    def save_analysis(
        self,
        invoice_id: str,
        *,
        invoice: InvoiceData | None,
        dump: Any,
        reasons: Sequence[ReasonCode],
        allowed: Sequence[Route],
        route: Route,
        vendor_id: int | None,
        vendor_name: str | None,
        po_number: str | None,
        lines: Sequence[LineOutcome],
    ) -> None:
        with self._session() as session:
            row = session.get(Invoice, invoice_id, with_for_update=True)
            if row is None:
                raise UnknownInvoice(invoice_id)
            row.analysis = dump
            row.analysis_schema = ANALYSIS_SCHEMA
            row.reasons = [reason.value for reason in reasons]
            row.allowed_routes = [r.value for r in allowed]
            row.route = route.value
            row.vendor_id = vendor_id
            row.vendor_name = vendor_name
            row.po_number = po_number
            row.route_decided_at = datetime.now(UTC)
            session.flush()
            revision = row.revision
            for model in (InvoiceLineRow, MatchResultRow):
                session.query(model).filter_by(invoice_id=invoice_id, revision=revision).delete()
            if invoice is not None:
                for position, line in enumerate(invoice.lines, start=1):
                    session.add(
                        InvoiceLineRow(
                            invoice_id=invoice_id,
                            revision=revision,
                            position=position,
                            vendor_code=line.vendor_code,
                            description=line.description,
                            quantity=line.quantity,
                            unit=line.unit,
                            unit_price=line.unit_price,
                            amount=line.amount,
                        )
                    )
            for line_outcome in lines:
                session.add(
                    MatchResultRow(
                        invoice_id=invoice_id,
                        revision=revision,
                        position=line_outcome.position,
                        status=line_outcome.status,
                        method=line_outcome.method,
                        po_number=line_outcome.po_number,
                        po_line_id=line_outcome.po_line_id,
                        price_ok=line_outcome.price_ok,
                        quantity_ok=line_outcome.quantity_ok,
                        evidence=list(line_outcome.evidence),
                    )
                )

    def update_reasons(self, invoice_id: str, reasons: Sequence[str], *, route: Route) -> None:
        """A reason found after the decision, e.g. ``validation_failed`` from the bill action."""
        with self._session() as session:
            session.execute(
                update(Invoice)
                .where(Invoice.invoice_id == invoice_id)
                .values(reasons=list(reasons), route=route.value)
            )

    # --- BR-11 registry -------------------------------------------------------------------------

    def registered(self, vendor_id: int, *, exclude: str | None) -> list[RegisteredInvoice]:
        query = select(Invoice).where(
            Invoice.vendor_id == vendor_id,
            Invoice.invoice_number.is_not(None),
            Invoice.invoice_date.is_not(None),
            Invoice.total.is_not(None),
        )
        if exclude is not None:
            query = query.where(Invoice.invoice_id != exclude)
        with self._sessions() as session:
            rows = list(session.scalars(query.order_by(Invoice.invoice_id)))
        return [
            _registered(r.invoice_id, vendor_id, r.invoice_number, r.invoice_date, r.total)
            for r in rows
        ]

    # --- actions (BR-22) ------------------------------------------------------------------------

    def begin_action(
        self,
        key: str,
        *,
        invoice_id: str,
        kind: ActionKind,
        target: str,
        content_version: str,
    ) -> ActionState:
        """Claim the action: a finished one comes back as it is, otherwise one more attempt."""
        with self._session() as session:
            session.execute(
                insert(ActionRow)
                .values(
                    idempotency_key=key,
                    invoice_id=invoice_id,
                    kind=kind.value,
                    target=target,
                    content_version=content_version,
                    status=ActionStatus.PENDING.value,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            row = session.scalars(
                select(ActionRow).where(ActionRow.idempotency_key == key).with_for_update()
            ).one()
            if row.status != ActionStatus.DONE.value:
                row.attempts += 1
                row.status = ActionStatus.PENDING.value
                row.error = None
            return self._action(row)

    @staticmethod
    def _action(row: ActionRow) -> ActionState:
        return ActionState(
            key=row.idempotency_key,
            status=ActionStatus(row.status),
            attempts=row.attempts,
            result=row.result,
            error=row.error,
        )

    def finish_action(self, key: str, result: dict[str, Any]) -> None:
        self._close_action(key, ActionStatus.DONE, result=result)

    def fail_action(self, key: str, error: str) -> None:
        self._close_action(key, ActionStatus.FAILED, error=error)

    def _close_action(
        self,
        key: str,
        status: ActionStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._session() as session:
            session.execute(
                update(ActionRow)
                .where(ActionRow.idempotency_key == key)
                .values(status=status.value, result=result, error=error)
            )

    def actions(self, invoice_id: str) -> list[ActionRow]:
        query = select(ActionRow).where(ActionRow.invoice_id == invoice_id).order_by(ActionRow.id)
        with self._sessions() as session:
            return list(session.scalars(query))

    # --- approvals (F-07) -----------------------------------------------------------------------

    def open_approval(
        self,
        invoice_id: str,
        *,
        evidence: dict[str, Any],
        reasons: Sequence[str],
        proposed_action: str,
        options: Sequence[str],
    ) -> OpenApproval:
        """The open request of this invoice; a repeated call does not create a second one."""
        with self._session() as session:
            session.execute(
                insert(ApprovalRow)
                .values(
                    invoice_id=invoice_id,
                    evidence=evidence,
                    reasons=list(reasons),
                    proposed_action=proposed_action,
                    options=list(options),
                )
                .on_conflict_do_nothing(
                    index_elements=["invoice_id"], index_where=ApprovalRow.decision.is_(None)
                )
            )
            row = session.scalars(
                select(ApprovalRow).where(
                    ApprovalRow.invoice_id == invoice_id, ApprovalRow.decision.is_(None)
                )
            ).one()
            return _approval(row)

    def current_approval(self, invoice_id: str) -> OpenApproval | None:
        """The open request, else the last decided one."""
        query = (
            select(ApprovalRow)
            .where(ApprovalRow.invoice_id == invoice_id)
            .order_by(ApprovalRow.decision.is_not(None), ApprovalRow.id.desc())
            .limit(1)
        )
        with self._sessions() as session:
            row = session.scalar(query)
        return _approval(row) if row is not None else None

    def decide(
        self,
        invoice_id: str,
        *,
        decision: Decision,
        decided_by: str,
        note: str | None,
    ) -> OpenApproval | None:
        """Record the decision on the open request; None when there is no open request."""
        with self._session() as session:
            row = session.scalars(
                select(ApprovalRow)
                .where(ApprovalRow.invoice_id == invoice_id, ApprovalRow.decision.is_(None))
                .with_for_update()
            ).first()
            if row is None:
                return None
            row.decision = decision.value
            row.decided_by = decided_by
            row.note = note
            row.decided_at = datetime.now(UTC)
            session.add(
                AuditEntry(
                    invoice_id=invoice_id,
                    kind=AuditKind.DECISION.value,
                    name="keputusan_approver",
                    summary=f"{decision.value} oleh {decided_by}",
                    status="ok",
                )
            )
            session.flush()
            return _approval(row)

    # --- audit (BR-23) --------------------------------------------------------------------------

    def audit(
        self,
        invoice_id: str | None,
        *,
        kind: AuditKind,
        name: str,
        node: str | None = None,
        params: Any = None,
        summary: str | None = None,
        duration_ms: int | None = None,
        status: str = "ok",
    ) -> None:
        with self._session() as session:
            session.add(
                AuditEntry(
                    invoice_id=invoice_id,
                    node=node,
                    kind=kind.value,
                    name=name,
                    params=mask_params(params) if params is not None else None,
                    summary=summary,
                    duration_ms=duration_ms,
                    status=status,
                )
            )

    def audit_llm(self, invoice_id: str | None, node: str, calls: Sequence[LlmCallRecord]) -> None:
        with self._session() as session:
            for call in calls:
                cost = call.cost_usd if isinstance(call.cost_usd, Decimal) else None
                session.add(
                    AuditEntry(
                        at=call.at,
                        invoice_id=invoice_id,
                        node=node,
                        kind=AuditKind.LLM.value,
                        name=call.purpose,
                        params={
                            "prompt_version": call.prompt_version,
                            "attempt": call.attempt,
                            "cache": call.cache.value,
                        },
                        summary=call.error_kind.value if call.error_kind else call.finish_reason,
                        duration_ms=call.duration_ms,
                        input_tokens=call.input_tokens,
                        output_tokens=call.output_tokens,
                        thinking_tokens=call.thinking_tokens,
                        cost_usd=cost,
                        model=call.model,
                        fallback=call.fallback,
                        status=call.status.value,
                    )
                )

    def audit_entries(self, invoice_id: str) -> list[AuditEntry]:
        query = (
            select(AuditEntry).where(AuditEntry.invoice_id == invoice_id).order_by(AuditEntry.id)
        )
        with self._sessions() as session:
            return list(session.scalars(query))


def _registered(
    invoice_id: str, vendor_id: int, number: str | None, when: date | None, total: Decimal | None
) -> RegisteredInvoice:
    assert number is not None and when is not None and total is not None
    return RegisteredInvoice(
        invoice_id=invoice_id,
        vendor_id=vendor_id,
        invoice_number=number,
        invoice_date=when,
        total=total,
    )


class DbInvoiceRegistry:
    """BR-11 "faktur lain di Faktur Beres", from the application database."""

    def __init__(self, store: Store) -> None:
        self._store = store

    def other_invoices(
        self, vendor_id: int, *, exclude_invoice_id: str | None
    ) -> Sequence[RegisteredInvoice]:
        return self._store.registered(vendor_id, exclude=exclude_invoice_id)
