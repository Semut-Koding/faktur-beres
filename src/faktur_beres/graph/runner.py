"""The service layer over the graph; the CLI uses it now, and the API will in M4b.

Operations: accept an invoice, run the graph until it stops, pass on an approver's decision, retry
after a failure, and read status and trace. Failures become status ``failed`` with a readable
message and the checkpoint stays (BR-27).
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from faktur_beres.db.models import AuditKind, Decision
from faktur_beres.db.store import NewInvoice
from faktur_beres.domain.invoice import Channel
from faktur_beres.domain.reasons import InvoiceStatus
from faktur_beres.domain.status import InvalidTransition
from faktur_beres.extraction.filetype import FileKind, FileRejected, identify
from faktur_beres.graph.deps import FileStore, GraphStore
from faktur_beres.graph.nodes import MissingVendorEmail, ReviewPending
from faktur_beres.llm.contracts import LlmError
from faktur_beres.tools.email_tool import MailUnavailable, RecipientRefused
from faktur_beres.tools.errors import AccessDenied, ToolError

EXTENSION = {FileKind.PDF: "pdf", FileKind.PNG: "png", FileKind.JPEG: "jpg", FileKind.XML: "xml"}
MAX_NOTE = 500
DURABILITY = "sync"  # the checkpoint is written before the next step starts (ADR-0009)


class FileRefused(ValueError):
    """The file is not an invoice the agent can take; no thread was created (BR-01)."""


class DecisionRefused(ValueError):
    """The approver's choice cannot be applied."""


@dataclass(frozen=True)
class Stop:
    invoice_id: str
    status: InvoiceStatus
    route: str | None
    reasons: tuple[str, ...]
    message: str | None
    already_decided: bool = False


def failure_message(exc: BaseException) -> str:
    """A message for the AP staff; it never carries prompts, keys or document content."""
    if isinstance(exc, ReviewPending | MissingVendorEmail):
        return str(exc)
    if isinstance(exc, LlmError):
        return "Layanan LLM tidak tersedia. Coba lagi dengan npm run agent:ulang."
    if isinstance(exc, AccessDenied):
        return "Odoo menolak hak akses agent. Periksa hak agent, lalu jalankan npm run agent:ulang."
    if isinstance(exc, ToolError):
        return f"Odoo tidak bisa diproses ({exc.kind}). Coba lagi dengan npm run agent:ulang."
    if isinstance(exc, RecipientRefused):
        return "Alamat tujuan email ditolak (BR-25). Periksa alamat penagihan vendor di Odoo."
    if isinstance(exc, MailUnavailable):
        return "Server surat tidak tersedia. Coba lagi dengan npm run agent:ulang."
    return f"Kesalahan sistem ({type(exc).__name__}). Coba lagi dengan npm run agent:ulang."


class Runner:
    def __init__(self, store: GraphStore, files: FileStore, graph: Any) -> None:
        self.store = store
        self.files = files
        self.graph = graph

    @staticmethod
    def _config(invoice_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": invoice_id}}

    def accept(
        self,
        data: bytes,
        file_name: str,
        *,
        channel: Channel = Channel.UPLOAD,
        sender_email: str | None = None,
        subject: str | None = None,
        body: str | None = None,
        source_message_id: str | None = None,
    ) -> str:
        """BR-01: the type and size are checked before a thread exists."""
        try:
            file = identify(data)
        except FileRejected as exc:
            raise FileRefused(
                f"Berkas ditolak: {exc}. Kirim ulang sebagai PDF, JPG, PNG, atau XML UBL."
            ) from exc
        invoice_id = self.store.create_invoice(
            NewInvoice(
                channel=channel,
                file_name=file_name,
                file_sha256=hashlib.sha256(data).hexdigest(),
                file_size=len(data),
                file_mime=file.mime_type,
                sender_email=sender_email,
                email_subject=subject,
                email_body=body,
                source_message_id=source_message_id,
            )
        )
        self.store.set_stored_path(
            invoice_id, self.files.save(invoice_id, EXTENSION[file.kind], data)
        )
        return invoice_id

    def _advance(self, invoice_id: str, start: Callable[[], Any]) -> Stop:
        try:
            start()
        except GraphInterrupt:  # pragma: no cover - invoke returns on an interrupt
            pass
        except Exception as exc:
            if isinstance(exc, AccessDenied | RecipientRefused):
                self.store.audit(
                    invoice_id,
                    kind=AuditKind.SECURITY,
                    name=type(exc).__name__,
                    summary=failure_message(exc),
                    status="refused",
                )
            self._fail(invoice_id, failure_message(exc))
        return self.status(invoice_id)

    def _fail(self, invoice_id: str, message: str) -> None:
        """To ``failed`` through ``processing``, so the status table of SPEC §6.3 holds.

        A final status stays as it is: the failure is audited and the invoice is not reopened.
        """
        current = InvoiceStatus(self.store.get(invoice_id).status)
        try:
            if current is not InvoiceStatus.PROCESSING:
                self.store.set_status(invoice_id, InvoiceStatus.PROCESSING, node="runner")
            self.store.set_status(invoice_id, InvoiceStatus.FAILED, node="runner", message=message)
        except InvalidTransition:
            self.store.audit(
                invoice_id,
                kind=AuditKind.DECISION,
                name="galat_setelah_status_akhir",
                node="runner",
                summary=f"status {current.value} dipertahankan: {message}",
                status="failed",
            )

    def run(self, invoice_id: str) -> Stop:
        return self._advance(
            invoice_id,
            lambda: self.graph.invoke(
                {"invoice_id": invoice_id},
                self._config(invoice_id),
                durability=DURABILITY,
            ),
        )

    def process(self, data: bytes, file_name: str, **intake: Any) -> Stop:
        return self.run(self.accept(data, file_name, **intake))

    def decide(
        self, invoice_id: str, decision: Decision, *, decided_by: str, note: str | None = None
    ) -> Stop:
        """A second decision on the same request changes nothing and reports the first one."""
        if note is not None and len(note) > MAX_NOTE:
            raise DecisionRefused(f"Catatan melebihi {MAX_NOTE} karakter.")
        request = self.store.current_approval(invoice_id)
        if request is None:
            raise DecisionRefused(f"{invoice_id} tidak punya permintaan persetujuan.")
        status = InvoiceStatus(self.store.get(invoice_id).status)
        if request.decision is not None or status is not InvoiceStatus.AWAITING_APPROVAL:
            stop = self.status(invoice_id)
            return Stop(**{**stop.__dict__, "already_decided": True})
        if decision.value not in request.options:
            raise DecisionRefused(
                f"Pilihan {decision.value} tidak tersedia. Pilihan: {', '.join(request.options)}."
            )
        pending = self.graph.get_state(self._config(invoice_id))
        if not any(task.interrupts for task in pending.tasks):
            raise DecisionRefused(f"{invoice_id} tidak sedang menunggu keputusan.")
        payload = {"decision": decision.value, "decided_by": decided_by, "note": note}
        return self._advance(
            invoice_id,
            lambda: self.graph.invoke(
                Command(resume=payload), self._config(invoice_id), durability=DURABILITY
            ),
        )

    def retry(self, invoice_id: str) -> Stop:
        """Continue a failed invoice from its last checkpoint; finished actions are not repeated."""
        if InvoiceStatus(self.store.get(invoice_id).status) is not InvoiceStatus.FAILED:
            raise DecisionRefused(f"{invoice_id} tidak berstatus failed.")
        self.store.set_status(invoice_id, InvoiceStatus.PROCESSING, node="agent:ulang")
        config = self._config(invoice_id)
        snapshot = self.graph.get_state(config)
        if snapshot.next == ("decide",):
            self.graph.update_state(config, {"retry_review": True}, as_node="match")
        if not snapshot.next and not snapshot.values:
            return self.run(invoice_id)
        return self._advance(
            invoice_id, lambda: self.graph.invoke(None, config, durability=DURABILITY)
        )

    def status(self, invoice_id: str) -> Stop:
        row = self.store.get(invoice_id)
        return Stop(
            invoice_id=invoice_id,
            status=InvoiceStatus(row.status),
            route=row.route,
            reasons=tuple(row.reasons or ()),
            message=row.failure_message,
        )
