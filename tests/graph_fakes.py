"""An in-memory store, Odoo writes and mailer, so the graph runs without any service."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from faktur_beres.db.models import ActionKind, ActionStatus, AuditKind, Decision, Invoice
from faktur_beres.db.store import (
    ActionState,
    LineOutcome,
    NewFile,
    NewInvoice,
    OpenApproval,
    format_invoice_id,
)
from faktur_beres.domain.erp import ErpReader, NoOtherInvoices
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.normalize import normalize_invoice_number
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import InvoiceStatus, ReasonCode, Route
from faktur_beres.domain.rules.bill_plan import BillPlan
from faktur_beres.domain.status import check_transition
from faktur_beres.graph.build import build_graph
from faktur_beres.graph.deps import Dependencies, Extractor, FileStore
from faktur_beres.graph.runner import Runner
from faktur_beres.llm.contracts import LlmCallRecord
from faktur_beres.tools.email_tool import OutgoingEmail
from faktur_beres.tools.odoo_tools import AttachmentResult, DraftBillResult, NoteResult


@dataclass
class AuditRow:
    kind: AuditKind
    name: str
    node: str | None
    summary: str | None
    status: str


class FakeStore:
    def __init__(self) -> None:
        self.invoices: dict[str, Invoice] = {}
        self.action_rows: dict[str, dict[str, Any]] = {}
        self.approvals: list[OpenApproval] = []
        self.trail: list[AuditRow] = []
        self.llm: list[LlmCallRecord] = []
        self.statuses: list[str] = []
        self.files: dict[tuple[str, int], NewFile] = {}

    def create_invoice(self, new: NewInvoice) -> str:
        invoice_id = format_invoice_id(len(self.invoices) + 1)
        self.invoices[invoice_id] = Invoice(
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
            reasons=[],
            allowed_routes=[],
            revision=1,
        )
        return invoice_id

    def set_stored_path(self, invoice_id: str, path: str) -> None:
        self.invoices[invoice_id].stored_path = path

    def add_revision(self, invoice_id: str, revision: int, file: NewFile) -> None:
        row = self.invoices[invoice_id]
        self.files.setdefault((invoice_id, revision), file)
        row.revision = revision
        row.stored_path = file.path
        row.file_sha256 = file.sha256
        row.file_mime = file.mime
        row.file_size = file.size
        row.file_name = file.name

    def get(self, invoice_id: str) -> Invoice:
        return self.invoices[invoice_id]

    def first_with_hash(self, sha256: str, *, excluding: str) -> Invoice | None:
        same = [
            row
            for key, row in sorted(self.invoices.items())
            if row.file_sha256 == sha256 and key != excluding
        ]
        return same[0] if same else None

    def mark_duplicate_of(self, invoice_id: str, original: str) -> None:
        self.invoices[invoice_id].duplicate_of = original

    def set_status(
        self,
        invoice_id: str,
        new: InvoiceStatus,
        *,
        node: str | None = None,
        message: str | None = None,
    ) -> InvoiceStatus:
        row = self.invoices[invoice_id]
        current = InvoiceStatus(row.status)
        if current is not new:
            check_transition(current, new)
            self.statuses.append(f"{current.value}>{new.value}")
        row.status = new.value
        row.failure_message = message
        return new

    def save_extraction(self, invoice_id: str, invoice: InvoiceData | None, dump: Any) -> None:
        row = self.invoices[invoice_id]
        row.extraction = dump
        if invoice is not None:
            row.invoice_number = invoice.invoice_number
            row.invoice_number_normalized = normalize_invoice_number(invoice.invoice_number)
            row.invoice_date = invoice.invoice_date
            row.total = invoice.total

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
        row = self.invoices[invoice_id]
        row.analysis = dump
        row.reasons = [r.value for r in reasons]
        row.allowed_routes = [r.value for r in allowed]
        row.route = route.value
        row.vendor_id = vendor_id
        row.vendor_name = vendor_name
        row.po_number = po_number

    def update_reasons(self, invoice_id: str, reasons: Sequence[str], *, route: Route) -> None:
        row = self.invoices[invoice_id]
        row.reasons = list(reasons)
        row.route = route.value

    def begin_action(
        self,
        key: str,
        *,
        invoice_id: str,
        kind: ActionKind,
        target: str,
        content_version: str,
    ) -> ActionState:
        row = self.action_rows.setdefault(
            key,
            {
                "invoice_id": invoice_id,
                "kind": kind,
                "status": ActionStatus.PENDING,
                "attempts": 0,
                "result": None,
                "error": None,
            },
        )
        if row["status"] is not ActionStatus.DONE:
            row["attempts"] += 1
            row["status"] = ActionStatus.PENDING
        return ActionState(key, row["status"], row["attempts"], row["result"], row["error"])

    def finish_action(self, key: str, result: dict[str, Any]) -> None:
        self.action_rows[key] |= {"status": ActionStatus.DONE, "result": result}

    def fail_action(self, key: str, error: str) -> None:
        self.action_rows[key] |= {"status": ActionStatus.FAILED, "error": error}

    def open_approval(
        self,
        invoice_id: str,
        *,
        evidence: dict[str, Any],
        reasons: Sequence[str],
        proposed_action: str,
        options: Sequence[str],
    ) -> OpenApproval:
        for approval in self.approvals:
            if approval.invoice_id == invoice_id and approval.decision is None:
                return approval
        approval = OpenApproval(
            len(self.approvals) + 1,
            invoice_id,
            evidence,
            tuple(reasons),
            proposed_action,
            tuple(options),
            None,
            None,
            None,
            None,
        )
        self.approvals.append(approval)
        return approval

    def current_approval(self, invoice_id: str) -> OpenApproval | None:
        mine = [a for a in self.approvals if a.invoice_id == invoice_id]
        if not mine:
            return None
        open_ones = [a for a in mine if a.decision is None]
        return open_ones[-1] if open_ones else mine[-1]

    def decide(
        self, invoice_id: str, *, decision: Decision, decided_by: str, note: str | None
    ) -> OpenApproval | None:
        for index, approval in enumerate(self.approvals):
            if approval.invoice_id == invoice_id and approval.decision is None:
                updated = OpenApproval(
                    **{
                        **approval.__dict__,
                        "decision": decision.value,
                        "decided_by": decided_by,
                        "note": note,
                        "decided_at": datetime.now(UTC),
                    }
                )
                self.approvals[index] = updated
                return updated
        return None

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
        self.trail.append(AuditRow(kind, name, node, summary, status))

    def audit_llm(self, invoice_id: str | None, node: str, calls: Sequence[LlmCallRecord]) -> None:
        self.llm += list(calls)

    def kinds(self, kind: AuditKind) -> list[AuditRow]:
        return [row for row in self.trail if row.kind is kind]


@dataclass
class FakeBills:
    """Natural-key idempotency like ``create_draft_bill``; failures can be scripted."""

    verified: bool = True
    errors: list[Exception] = field(default_factory=list)
    created: list[BillPlan] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def create_draft_bill(self, plan: BillPlan, key: str) -> DraftBillResult:
        if self.errors:
            raise self.errors.pop(0)
        if not self.verified:
            return DraftBillResult(
                outcome="created",
                bill_id=len(self.created) + 1,
                verified=False,
                differences=("total berbeda",),
                idempotency_key=key,
            )
        self.created.append(plan)
        return DraftBillResult(
            outcome="created",
            bill_id=len(self.created),
            verified=True,
            total=Decimal(1),
            idempotency_key=key,
        )

    def attach_file(
        self, bill_id: int, name: str, content: bytes, mimetype: str
    ) -> AttachmentResult:
        self.attachments.append(name)
        return AttachmentResult(attachment_id=len(self.attachments), created=True, checksum="c")

    def post_note(self, bill_id: int, text: str, key: str) -> NoteResult:
        self.notes.append(text)
        return NoteResult(message_id=len(self.notes), created=True, marker="m")


@dataclass
class FakeMailer:
    sent: list[OutgoingEmail] = field(default_factory=list)

    def exists(self, message_id: str) -> bool:
        return any(mail.message_id == message_id for mail in self.sent)

    def send(self, mail: OutgoingEmail) -> None:
        self.sent.append(mail)


@dataclass
class Harness:
    runner: Runner
    store: FakeStore
    bills: FakeBills
    mailer: FakeMailer
    deps: Dependencies


def harness(
    tmp_path: Path,
    reader: ErpReader,
    policy: PolicyConfig,
    extractor: Extractor,
    *,
    bills: FakeBills | None = None,
    **options: Any,
) -> Harness:
    store, mailer = FakeStore(), FakeMailer()
    bills = bills or FakeBills()
    files = FileStore(tmp_path)
    deps = Dependencies(
        store=store,
        reader=reader,
        registry=NoOtherInvoices(),
        policy=policy,
        files=files,
        extractor=extractor,
        bills=bills,
        mailer=mailer,
        retry_interval_seconds=0.001,
        **options,
    )
    graph = build_graph(deps, InMemorySaver())
    return Harness(Runner(store, files, graph), store, bills, mailer, deps)
