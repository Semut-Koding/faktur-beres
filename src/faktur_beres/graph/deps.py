"""What the graph needs from outside; tests replace any of it."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from faktur_beres.db.models import ActionKind, AuditKind, Decision, Invoice
from faktur_beres.db.store import ActionState, LineOutcome, NewFile, NewInvoice, OpenApproval
from faktur_beres.domain.erp import (
    ErpReader,
    InvoiceRegistry,
    PurchaseOrderRecord,
    VendorRecord,
)
from faktur_beres.domain.invoice import InvoiceData, TextFragment
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import InvoiceStatus, ReasonCode, Route
from faktur_beres.domain.rules.bill_plan import BillPlan
from faktur_beres.domain.rules.lines import LinesResult
from faktur_beres.extraction.outcome import ExtractionOutcome
from faktur_beres.llm.contracts import LlmCallRecord
from faktur_beres.tools.email_tool import Mailer
from faktur_beres.tools.injection_review import ReviewOutcome
from faktur_beres.tools.line_mapping import LineMappingOutcome
from faktur_beres.tools.odoo_tools import AttachmentResult, DraftBillResult, NoteResult
from faktur_beres.tools.po_planner import PlannerOutcome

Extractor = Callable[[bytes], ExtractionOutcome]
Reviewer = Callable[[Sequence[TextFragment]], ReviewOutcome]
Planner = Callable[[InvoiceData, VendorRecord, ErpReader], PlannerOutcome]
LineMapper = Callable[[InvoiceData, PurchaseOrderRecord, LinesResult], LineMappingOutcome]


class GraphStore(Protocol):
    def create_invoice(self, new: NewInvoice) -> str: ...

    def set_stored_path(self, invoice_id: str, path: str) -> None: ...

    def add_revision(self, invoice_id: str, revision: int, file: NewFile) -> None: ...

    def get(self, invoice_id: str) -> Invoice: ...

    def first_with_hash(self, sha256: str, *, excluding: str) -> Invoice | None: ...

    def mark_duplicate_of(self, invoice_id: str, original: str) -> None: ...

    def set_status(
        self,
        invoice_id: str,
        new: InvoiceStatus,
        *,
        node: str | None = None,
        message: str | None = None,
    ) -> InvoiceStatus: ...

    def save_extraction(self, invoice_id: str, invoice: InvoiceData | None, dump: Any) -> None: ...

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
    ) -> None: ...

    def update_reasons(self, invoice_id: str, reasons: Sequence[str], *, route: Route) -> None: ...

    def begin_action(
        self,
        key: str,
        *,
        invoice_id: str,
        kind: ActionKind,
        target: str,
        content_version: str,
    ) -> ActionState: ...

    def finish_action(self, key: str, result: dict[str, Any]) -> None: ...

    def fail_action(self, key: str, error: str) -> None: ...

    def open_approval(
        self,
        invoice_id: str,
        *,
        evidence: dict[str, Any],
        reasons: Sequence[str],
        proposed_action: str,
        options: Sequence[str],
    ) -> OpenApproval: ...

    def current_approval(self, invoice_id: str) -> OpenApproval | None: ...

    def decide(
        self, invoice_id: str, *, decision: Decision, decided_by: str, note: str | None
    ) -> OpenApproval | None: ...

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
    ) -> None: ...

    def audit_llm(
        self, invoice_id: str | None, node: str, calls: Sequence[LlmCallRecord]
    ) -> None: ...


class BillActions(Protocol):
    """The three Odoo writes the agent may do (agent key, ``odoo_tools``)."""

    def create_draft_bill(self, plan: BillPlan, key: str) -> DraftBillResult: ...

    def attach_file(
        self, bill_id: int, name: str, content: bytes, mimetype: str
    ) -> AttachmentResult: ...

    def post_note(self, bill_id: int, text: str, key: str) -> NoteResult: ...


class FileStore:
    """The original files, under ``<root>/<invoice_id>/``. Names carry nothing about the file."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, invoice_id: str, extension: str, data: bytes) -> str:
        folder = self.root / invoice_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"asli.{extension}"
        path.write_bytes(data)
        return path.relative_to(self.root).as_posix()

    def read(self, stored_path: str) -> bytes:
        return (self.root / stored_path).read_bytes()


@dataclass(frozen=True)
class Dependencies:
    store: GraphStore
    reader: ErpReader
    registry: InvoiceRegistry
    policy: PolicyConfig
    files: FileStore
    extractor: Extractor
    bills: BillActions
    mailer: Mailer
    reviewer: Reviewer | None = None
    planner: Planner | None = None
    line_mapper: LineMapper | None = None
    internal_domain: str = "arunika.test"
    sender: str = "ap@arunika.test"
    retry_attempts: int = 3
    retry_interval_seconds: float = 2.0
