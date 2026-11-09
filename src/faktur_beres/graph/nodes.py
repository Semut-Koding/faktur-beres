"""The nodes of the graph (SPEC §5.1, M4a).

The steps ``validate``, ``identify``, ``match`` and ``decide`` call the same functions as
``analyse()`` (``domain.engine``), so the chain of rules exists once. Around them the nodes add what
only the graph has: the LLM inside its guards, the trace, and the actions.

``await_approval`` holds the interrupt and does nothing else: LangGraph runs a node again from its
start on resume, so whatever has an effect lives in ``prepare_approval`` before it and in
``apply_decision`` after it (ADR-0006).
"""

import time
from collections.abc import Callable, Sequence
from typing import Any

from langgraph.types import interrupt
from pydantic import TypeAdapter

from faktur_beres.db.models import ActionKind, AuditKind, Decision
from faktur_beres.db.store import LineOutcome
from faktur_beres.domain.engine import (
    AnalysisResult,
    Identification,
    Matching,
    StepResult,
    apply_line_mapping,
    decide_step,
    identify_step,
    judge_step,
    match_step,
    unreadable_analysis,
    validate_step,
)
from faktur_beres.domain.findings import Finding, PriceEvidence, QuantityEvidence
from faktur_beres.domain.invoice import (
    Channel,
    IntakeContext,
    InvoiceData,
    TextFragment,
    TextSource,
)
from faktur_beres.domain.normalize import normalize_po_number
from faktur_beres.domain.reasons import InvoiceStatus, ReasonCode, Route
from faktur_beres.domain.rules.injection import InjectionReview, document_fragments
from faktur_beres.domain.rules.purchase_order import PoStatus
from faktur_beres.domain.rules.routing import initial_status
from faktur_beres.domain.rules.safety import content_version, idempotency_key
from faktur_beres.domain.rules.vendor import VendorStatus
from faktur_beres.extraction.outcome import ExtractionOutcome, ExtractionStatus
from faktur_beres.graph.deps import Dependencies
from faktur_beres.graph.state import GraphState
from faktur_beres.tools.email_tool import (
    OutgoingEmail,
    clarification_email,
    duplicate_notice,
    send_checked,
)
from faktur_beres.tools.errors import AccessDenied, Conflict, ForbiddenOperation, Rejected
from faktur_beres.tools.errors import VerificationFailed as ToolVerificationFailed
from faktur_beres.tools.odoo_tools import bill_note_text

PLANNER_STATUSES = frozenset(
    {PoStatus.CANDIDATE, PoStatus.CORRECTED, PoStatus.NOT_FOUND, PoStatus.AMBIGUOUS}
)
_FRAGMENTS = TypeAdapter(list[TextFragment])
Update = dict[str, Any]


class ReviewPending(RuntimeError):
    """BR-15: route A has to wait until layer 2 has reviewed the document."""


class MissingVendorEmail(RuntimeError):
    """Route B without a billing address in Odoo: no other address may be used (BR-25)."""


def _invoice(state: GraphState) -> InvoiceData | None:
    raw = state.get("invoice")
    return InvoiceData.model_validate(raw) if raw else None


def _fragments(state: GraphState) -> list[TextFragment]:
    return _FRAGMENTS.validate_python(state.get("fragments", []))


def _analysis(state: GraphState) -> AnalysisResult:
    return AnalysisResult.model_validate(state["analysis"])


def _dump_fragments(fragments: Sequence[TextFragment]) -> list[dict[str, Any]]:
    return [f.model_dump(mode="json") for f in fragments]


def _extraction_dump(outcome: ExtractionOutcome) -> dict[str, Any]:
    """Without the call records: those go to the audit, not into the state."""
    attempts = tuple(a.model_copy(update={"calls": ()}) for a in outcome.attempts)
    return outcome.model_copy(update={"attempts": attempts}).model_dump(mode="json")


def line_outcomes(analysis: AnalysisResult) -> list[LineOutcome]:
    outcomes = []
    for match in analysis.lines.matches:
        found = [
            f
            for f in analysis.lines.findings
            if isinstance(f.evidence, PriceEvidence | QuantityEvidence)
            and f.evidence.line == match.line
        ]
        matched = match.status == "matched"
        outcomes.append(
            LineOutcome(
                position=match.line,
                status=match.status,
                method=match.method,
                po_number=analysis.lines.purchase_order,
                po_line_id=match.po_line_id,
                price_ok=(
                    not any(isinstance(f.evidence, PriceEvidence) for f in found)
                    if matched
                    else None
                ),
                quantity_ok=(
                    not any(isinstance(f.evidence, QuantityEvidence) for f in found)
                    if matched
                    else None
                ),
                evidence=[f.model_dump(mode="json") for f in found],
            )
        )
    return outcomes


class Nodes:
    def __init__(self, deps: Dependencies) -> None:
        self.deps = deps

    def traced(self, name: str, fn: Callable[[GraphState], Update]) -> Callable[..., Update]:
        """One audit row per node run (BR-23)."""

        def run(state: GraphState) -> Update:
            started = time.monotonic()
            invoice_id = state["invoice_id"]
            status = "ok"
            try:
                return fn(state)
            except Exception as exc:
                status = type(exc).__name__
                raise
            finally:
                self.deps.store.audit(
                    invoice_id,
                    kind=AuditKind.NODE,
                    name=name,
                    node=name,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    status=status,
                )

        return run

    # --- from the file to the route -------------------------------------------------------------

    def intake(self, state: GraphState) -> Update:
        store, invoice_id = self.deps.store, state["invoice_id"]
        row = store.get(invoice_id)
        store.set_status(invoice_id, InvoiceStatus.PROCESSING, node="intake")
        same = store.first_with_hash(row.file_sha256, excluding=invoice_id)
        reused = None
        if same is not None:
            store.mark_duplicate_of(invoice_id, same.invoice_id)
            reused = same.invoice_id if same.extraction else None
            store.audit(
                invoice_id,
                kind=AuditKind.GUARD,
                name="berkas_sama",
                node="intake",
                summary=f"hash sama dengan {same.invoice_id}",
            )
        channel = Channel(row.channel)
        fragments: list[TextFragment] = []
        if channel is Channel.EMAIL:
            if row.email_subject:
                fragments.append(
                    TextFragment(
                        source=TextSource.EMAIL_SUBJECT, text=row.email_subject, where="subjek"
                    )
                )
            if row.email_body:
                fragments.append(
                    TextFragment(
                        source=TextSource.EMAIL_BODY, text=row.email_body, where="isi email"
                    )
                )
        intake = IntakeContext(
            invoice_id=invoice_id,
            channel=channel,
            sender_email=row.sender_email,
            file_name=row.file_name,
            file_sha256=row.file_sha256,
        )
        return {
            "intake": intake.model_dump(mode="json"),
            "reused_from": reused,
            "fragments": _dump_fragments(fragments),
        }

    def extract(self, state: GraphState) -> Update:
        store, invoice_id = self.deps.store, state["invoice_id"]
        reused = state.get("reused_from")
        if reused:
            source = store.get(reused).extraction
            assert source is not None
            outcome = ExtractionOutcome.model_validate(source)
            store.audit(
                invoice_id,
                kind=AuditKind.GUARD,
                name="ekstraksi_dipakai_ulang",
                node="extract",
                summary=f"dari {reused}, tanpa LLM",
            )
        else:
            row = store.get(invoice_id)
            outcome = self.deps.extractor(self.deps.files.read(row.stored_path))
            store.audit_llm(invoice_id, "extract", outcome.calls)
        for attempt in outcome.attempts:
            store.audit(
                invoice_id,
                kind=AuditKind.TOOL,
                name="pembacaan",
                node="extract",
                summary=attempt.summary(),
                params={
                    "percobaan": attempt.number,
                    "field_bermasalah": [p.path for p in attempt.problems],
                },
            )
        dump = _extraction_dump(outcome)
        store.save_extraction(invoice_id, outcome.invoice, dump)
        return {
            "extraction": dump,
            "invoice": outcome.invoice.model_dump(mode="json") if outcome.invoice else None,
            "fragments": [*state.get("fragments", []), *_dump_fragments(outcome.fragments)],
        }

    def validate(self, state: GraphState) -> Update:
        invoice = _invoice(state)
        assert invoice is not None
        return {"arithmetic": validate_step(invoice, self.deps.policy).model_dump(mode="json")}

    def identify(self, state: GraphState) -> Update:
        invoice = _invoice(state)
        assert invoice is not None
        intake = IntakeContext.model_validate(state["intake"])
        ident = identify_step(invoice, intake, self.deps.reader, self.deps.policy)
        return {
            "identification": ident.model_dump(mode="json"),
            "planner": self._plan(state, invoice, ident),
        }

    def _plan(
        self, state: GraphState, invoice: InvoiceData, ident: Identification
    ) -> dict[str, Any] | None:
        vendor = ident.vendor.vendor
        if (
            self.deps.planner is None
            or vendor is None
            or ident.vendor.status is not VendorStatus.IDENTIFIED
            or ident.purchase_order.status not in PLANNER_STATUSES
        ):
            return None
        invoice_id, store = state["invoice_id"], self.deps.store
        outcome = self.deps.planner(invoice, vendor, self.deps.reader)
        store.audit_llm(invoice_id, "identify", outcome.calls)
        po = ident.purchase_order.purchase_order
        rule = normalize_po_number(po.number) if po is not None else None
        agrees = outcome.proposal == rule
        if outcome.status == "unavailable":
            summary = "perencana tidak tersedia; aturan BR-06 tetap memutuskan"
        else:
            summary = (
                f"usulan {outcome.proposal or 'tidak ada'}, aturan {rule or 'tidak ada'}: "
                + ("sama" if agrees else "berbeda, aturan yang dipakai")
            )
        store.audit(
            invoice_id,
            kind=AuditKind.GUARD,
            name="perencana_identify",
            node="identify",
            summary=summary,
            params={
                "status": outcome.status,
                "panggilan_model": outcome.model_calls,
                "eksekusi_alat": outcome.tool_runs,
                "penolakan_guard": list(outcome.guard_refusals),
            },
            status="ok" if outcome.status != "unavailable" else "unavailable",
        )
        return {
            "status": outcome.status,
            "proposal": outcome.proposal,
            "rule": rule,
            "agrees": agrees,
            "explanation": outcome.explanation,
            "model_calls": outcome.model_calls,
            "tool_runs": outcome.tool_runs,
            "guard_refusals": list(outcome.guard_refusals),
        }

    def _review(
        self, state: GraphState, invoice: InvoiceData | None, node: str
    ) -> InjectionReview | None:
        """BR-15 layer 2 over every free text of the document, when a reviewer is configured."""
        if self.deps.reviewer is None:
            return None
        outcome = self.deps.reviewer(document_fragments(invoice, _fragments(state)))
        self.deps.store.audit_llm(state["invoice_id"], node, outcome.calls)
        return outcome.review

    def match(self, state: GraphState) -> Update:
        invoice = _invoice(state)
        assert invoice is not None
        deps, invoice_id = self.deps, state["invoice_id"]
        intake = IntakeContext.model_validate(state["intake"])
        ident = Identification.model_validate(state["identification"])
        review = self._review(state, invoice, "match")
        matching = match_step(
            invoice,
            intake,
            _fragments(state),
            ident,
            deps.reader,
            deps.policy,
            registry=deps.registry,
            injection_review=review,
        )
        po = ident.purchase_order.purchase_order
        if deps.line_mapper is not None and po is not None and matching.lines.ran:
            outcome = deps.line_mapper(invoice, po, matching.lines)
            deps.store.audit_llm(invoice_id, "match", outcome.calls)
            if outcome.called:
                deps.store.audit(
                    invoice_id,
                    kind=AuditKind.GUARD,
                    name="pemetaan_baris",
                    node="match",
                    summary=f"{len(outcome.mapping)} baris dipetakan",
                )
            matching = apply_line_mapping(invoice, ident, matching, deps.policy, outcome.mapping)
        return {
            "matching": matching.model_dump(mode="json"),
            "review": review.model_dump(mode="json") if review is not None else None,
        }

    def decide(self, state: GraphState) -> Update:
        deps, invoice_id = self.deps, state["invoice_id"]
        invoice = _invoice(state)
        extraction = ExtractionOutcome.model_validate(state["extraction"])
        if extraction.status is ExtractionStatus.VALIDATION_FAILED:
            assert extraction.finding is not None
            analysis = unreadable_analysis(extraction.finding, _fragments(state), None)
        else:
            assert invoice is not None
            analysis = self._analysis_of(state, invoice)
        route = analysis.default_route
        if analysis.injection_awaiting_review and route is Route.A:
            raise ReviewPending(
                "Penilaian injeksi lapis 2 belum berhasil, jadi draft tagihan belum dibuat. "
                "Coba lagi dengan npm run agent:ulang."
            )
        vendor = analysis.vendor.vendor
        po = analysis.purchase_order.purchase_order
        deps.store.save_analysis(
            invoice_id,
            invoice=invoice,
            dump=analysis.model_dump(mode="json"),
            reasons=analysis.reasons,
            allowed=analysis.allowed_routes,
            route=route,
            vendor_id=vendor.id if vendor else None,
            vendor_name=vendor.name if vendor else None,
            po_number=po.number if po else None,
            lines=line_outcomes(analysis),
        )
        deps.store.audit(
            invoice_id,
            kind=AuditKind.DECISION,
            name="jalur",
            node="decide",
            summary=(
                f"jalur {route.value}; alasan {[r.value for r in analysis.reasons]}; "
                f"diizinkan {[r.value for r in analysis.allowed_routes]}"
            ),
        )
        return {
            "analysis": analysis.model_dump(mode="json"),
            "route": route.value,
            "retry_review": False,
        }

    def _analysis_of(self, state: GraphState, invoice: InvoiceData) -> AnalysisResult:
        deps = self.deps
        matching = Matching.model_validate(state["matching"])
        if matching.injection_awaiting_review and state.get("retry_review"):
            review = self._review(state, invoice, "decide")
            injection, awaiting = judge_step(invoice, _fragments(state), review)
            matching = matching.model_copy(
                update={"injection": injection, "injection_awaiting_review": awaiting}
            )
        return decide_step(
            invoice,
            StepResult.model_validate(state["arithmetic"]),
            Identification.model_validate(state["identification"]),
            matching,
            deps.reader,
            deps.policy,
        )

    # --- route A --------------------------------------------------------------------------------

    def _claim(
        self, state: GraphState, kind: ActionKind, target: str, content: Any
    ) -> tuple[str, dict[str, Any] | None]:
        """The BR-22 key of an action and, when it already finished, its result."""
        invoice_id = state["invoice_id"]
        key = idempotency_key(invoice_id, kind.value, target, content)
        action = self.deps.store.begin_action(
            key,
            invoice_id=invoice_id,
            kind=kind,
            target=target,
            content_version=content_version(content),
        )
        return key, action.result if action.done else None

    def _security(self, state: GraphState, node: str, exc: Exception) -> None:
        self.deps.store.audit(
            state["invoice_id"],
            kind=AuditKind.SECURITY,
            name=type(exc).__name__,
            node=node,
            summary=str(exc)[:300],
            status="refused",
        )

    def act_bill(self, state: GraphState) -> Update:
        """BR-18: draft, attachment, note; each with its own key, each verified."""
        deps, invoice_id = self.deps, state["invoice_id"]
        analysis = _analysis(state)
        plan = analysis.bill_plan
        if plan is None:
            raise RuntimeError("jalur tagihan tanpa rencana tagihan")
        try:
            key, done = self._claim(state, ActionKind.CREATE_DRAFT_BILL, plan.ref, plan)
            if done is None:
                result = deps.bills.create_draft_bill(plan, key)
                deps.store.audit(
                    invoice_id,
                    kind=AuditKind.TOOL,
                    name="create_draft_bill",
                    node="act_bill",
                    params={"ref": plan.ref, "partner_id": plan.partner_id},
                    summary=f"{result.outcome}, terverifikasi {result.verified}",
                )
                if not result.verified or result.bill_id is None:
                    deps.store.fail_action(key, "; ".join(result.differences) or result.outcome)
                    return self._verification_failed(state, list(result.differences))
                done = {"bill_id": result.bill_id, "outcome": result.outcome}
                deps.store.finish_action(key, done)
            bill_id = int(done["bill_id"])
            row = deps.store.get(invoice_id)
            content = deps.files.read(row.stored_path)
            target = f"bill:{bill_id}"
            key, attached = self._claim(
                state,
                ActionKind.ATTACH_FILE,
                target,
                {"name": row.file_name, "sha256": row.file_sha256},
            )
            if attached is None:
                attachment = deps.bills.attach_file(bill_id, row.file_name, content, row.file_mime)
                deps.store.finish_action(key, {"attachment_id": attachment.attachment_id})
                deps.store.audit(
                    invoice_id,
                    kind=AuditKind.TOOL,
                    name="attach_file",
                    node="act_bill",
                    params={"bill_id": bill_id, "sha256": row.file_sha256},
                    summary=f"lampiran #{attachment.attachment_id}",
                )
            invoice = _invoice(state)
            assert invoice is not None
            text = (
                bill_note_text(
                    invoice.invoice_number, invoice.nsfp, invoice.tax_invoice_date, row.file_name
                )
                + f" Referensi: [{invoice_id}]."
            )
            key, noted = self._claim(state, ActionKind.POST_NOTE, target, {"text": text})
            if noted is None:
                note = deps.bills.post_note(bill_id, text, key)
                deps.store.finish_action(key, {"message_id": note.message_id})
                deps.store.audit(
                    invoice_id,
                    kind=AuditKind.TOOL,
                    name="post_note",
                    node="act_bill",
                    params={"bill_id": bill_id},
                    summary=f"catatan #{note.message_id}",
                )
        except (Rejected, Conflict, ToolVerificationFailed) as exc:
            return self._verification_failed(state, [f"{type(exc).__name__}: {exc}"])
        except (AccessDenied, ForbiddenOperation) as exc:
            self._security(state, "act_bill", exc)
            raise
        deps.store.set_status(invoice_id, InvoiceStatus.DRAFT_CREATED, node="act_bill")
        return {"final_status": InvoiceStatus.DRAFT_CREATED.value, "verification": []}

    def _verification_failed(self, state: GraphState, differences: list[str]) -> Update:
        """BR-18: the draft stays as it is and the invoice goes to route C."""
        self.deps.store.audit(
            state["invoice_id"],
            kind=AuditKind.GUARD,
            name="verifikasi_tagihan_gagal",
            node="act_bill",
            summary="; ".join(differences)[:500],
            status="failed",
        )
        return {
            "verification": differences,
            "bill_attempted": True,
            "extra_reasons": [ReasonCode.VALIDATION_FAILED.value],
        }

    # --- route B --------------------------------------------------------------------------------

    def act_vendor_email(self, state: GraphState) -> Update:
        """BR-19 and BR-25: one e-mail to the billing address in Odoo, once."""
        deps, invoice_id = self.deps, state["invoice_id"]
        analysis = _analysis(state)
        vendor = analysis.vendor.vendor
        if vendor is None or not vendor.email:
            raise MissingVendorEmail(
                "Vendor tidak punya alamat penagihan di Odoo, jadi email tidak dikirim. "
                "Lengkapi alamatnya di Odoo lalu jalankan npm run agent:ulang."
            )
        invoice = _invoice(state)
        decision = state.get("decision")
        approver_note = decision.get("note") if decision else None
        number = invoice.invoice_number if invoice else None
        findings = list(analysis.findings)
        clarifying = decision is not None and decision["decision"] == Decision.CLARIFY.value
        notice = not clarifying and ReasonCode.DUPLICATE_CONFIRMED in analysis.reasons
        content = {
            "jenis": "pemberitahuan" if notice else "klarifikasi",
            "alasan": [r.value for r in analysis.reasons],
            "pesan": [f.message for f in findings if f.reason is not None],
            "catatan": approver_note,
        }
        key, done = self._claim(state, ActionKind.SEND_EMAIL, vendor.email, content)
        if done is None:
            mail = self._email(
                invoice_id, number, vendor.email, key, findings, notice, approver_note
            )
            sent = send_checked(
                deps.mailer,
                mail,
                vendor_emails=[vendor.email],
                internal_domain=deps.internal_domain,
            )
            deps.store.finish_action(key, {"message_id": mail.message_id, "dikirim": sent})
            deps.store.audit(
                invoice_id,
                kind=AuditKind.TOOL,
                name="email_tool",
                node="act_vendor_email",
                params={"subject": mail.subject, "message_id": mail.message_id},
                summary="terkirim" if sent else "sudah ada, tidak dikirim lagi",
            )
        final = (
            InvoiceStatus.AWAITING_VENDOR
            if clarifying
            else initial_status(Route.B, analysis.reasons)
        )
        deps.store.set_status(invoice_id, final, node="act_vendor_email")
        return {"final_status": final.value}

    def _email(
        self,
        invoice_id: str,
        number: str | None,
        to: str,
        key: str,
        findings: Sequence[Finding],
        notice: bool,
        approver_note: str | None,
    ) -> OutgoingEmail:
        sender = self.deps.sender
        if notice:
            return duplicate_notice(
                invoice_id=invoice_id,
                invoice_number=number,
                sender=sender,
                to=to,
                key=key,
                findings=findings,
            )
        return clarification_email(
            invoice_id=invoice_id,
            invoice_number=number,
            sender=sender,
            to=to,
            key=key,
            findings=findings,
            approver_note=approver_note,
        )

    # --- route C --------------------------------------------------------------------------------

    def prepare_approval(self, state: GraphState) -> Update:
        """BR-21: the evidence pack, the ``approvals`` row and the status, before the interrupt."""
        deps, invoice_id = self.deps, state["invoice_id"]
        analysis = _analysis(state)
        reasons = [r.value for r in analysis.reasons]
        for extra in state.get("extra_reasons", []):
            if extra not in reasons:
                reasons.append(extra)
        options = [Decision.REJECT.value]
        if analysis.bill_plan is not None and not state.get("bill_attempted"):
            options.insert(0, Decision.APPROVE.value)
        if analysis.vendor.status is VendorStatus.IDENTIFIED:
            options.append(Decision.CLARIFY.value)
        proposed = (
            "Setujui untuk membuat draft tagihan."
            if Decision.APPROVE.value in options
            else "Tinjau bukti; draft tagihan tidak bisa dibuat otomatis."
        )
        pack: dict[str, Any] = {
            "invoice_id": invoice_id,
            "faktur": state.get("invoice"),
            "ekstraksi": _extraction_summary(state["extraction"]),
            "pencocokan": state["analysis"]["lines"],
            "alasan": [
                f.model_dump(mode="json") for f in analysis.findings if f.reason is not None
            ],
            "catatan": list(analysis.notes),
            "perencana": state.get("planner"),
            "verifikasi_tagihan": state.get("verification", []),
            "policy_quotes": [],
            "usulan": proposed,
            "pilihan": options,
        }
        deps.store.update_reasons(invoice_id, reasons, route=Route.C)
        deps.store.open_approval(
            invoice_id,
            evidence=pack,
            reasons=reasons,
            proposed_action=proposed,
            options=options,
        )
        deps.store.set_status(invoice_id, InvoiceStatus.AWAITING_APPROVAL, node="prepare_approval")
        return {"approval_request": pack}

    def await_approval(self, state: GraphState) -> Update:
        """Only the interrupt: no effect here, because this node runs again on resume."""
        return {"decision": interrupt(state["approval_request"])}

    def apply_decision(self, state: GraphState) -> Update:
        deps, invoice_id = self.deps, state["invoice_id"]
        decision = state["decision"]
        chosen = Decision(decision["decision"])
        deps.store.decide(
            invoice_id, decision=chosen, decided_by=decision["decided_by"], note=decision["note"]
        )
        deps.store.set_status(invoice_id, InvoiceStatus.PROCESSING, node="apply_decision")
        if chosen is Decision.REJECT:
            deps.store.set_status(invoice_id, InvoiceStatus.REJECTED, node="apply_decision")
            return {"final_status": InvoiceStatus.REJECTED.value}
        return {}

    # --- the end --------------------------------------------------------------------------------

    def report(self, state: GraphState) -> Update:
        self.deps.store.audit(
            state["invoice_id"],
            kind=AuditKind.NODE,
            name="selesai",
            node="report",
            summary=f"status {state.get('final_status')}",
        )
        return {}


def _extraction_summary(dump: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": dump.get("status"),
        "sumber": dump.get("source"),
        "percobaan": [
            {
                "nomor": a.get("number"),
                "model": a.get("model"),
                "field_bermasalah": [p.get("path") for p in a.get("problems", [])],
            }
            for a in dump.get("attempts", [])
        ],
    }
