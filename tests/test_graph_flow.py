"""M4a.5 and M4a.7: the graph's behaviour, without any service (fake store, Odoo and mail)."""

from collections.abc import Callable
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from reportlab.pdfgen.canvas import Canvas

from faktur_beres.dataset import load_dataset
from faktur_beres.db.models import AuditKind, Decision
from faktur_beres.domain.findings import ExtractionEvidence, Finding
from faktur_beres.domain.invoice import InvoiceData, TextFragment
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.reasons import InvoiceStatus, ReasonCode
from faktur_beres.domain.rules.injection import InjectionReview, ReviewStatus
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.labels import (
    load_labels,
    load_robustness_labels,
    truth_fragments,
    truth_to_invoice,
)
from faktur_beres.extraction.outcome import ExtractionOutcome, ExtractionSource, ExtractionStatus
from faktur_beres.graph.runner import DecisionRefused, FileRefused, Stop
from faktur_beres.llm.contracts import LlmUnavailable
from faktur_beres.tools.errors import AccessDenied, Unavailable
from faktur_beres.tools.injection_review import ReviewOutcome
from faktur_beres.tools.line_mapping import LineMappingOutcome
from faktur_beres.tools.po_planner import PlannerOutcome
from graph_fakes import FakeBills, Harness, harness

POLICY = load_policy()
LABELS = load_labels()
ROBUST = load_robustness_labels()


@cache  # reportlab stamps the current time: the same text must give the same bytes
def pdf(text: str = "uji") -> bytes:
    buffer = BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(72, 720, text)
    canvas.save()
    return buffer.getvalue()


def good(invoice: InvoiceData, fragments: list[TextFragment]) -> ExtractionOutcome:
    return ExtractionOutcome(
        status=ExtractionStatus.OK,
        source=ExtractionSource.UBL,
        invoice=invoice,
        fragments=tuple(fragments),
    )


def documents(sid: str) -> tuple[InvoiceData, list[TextFragment]]:
    label = LABELS.get(sid) or ROBUST[sid]
    truth = label.kebenaran_dokumen
    return truth_to_invoice(truth), truth_fragments(truth, document_format=label.format)


def start(
    tmp_path: Path,
    sid: str,
    *,
    extractor: Callable[[bytes], ExtractionOutcome] | None = None,
    bills: FakeBills | None = None,
    **options: Any,
) -> tuple[Harness, str]:
    invoice, fragments = documents(sid)
    reader = DatasetErpReader(load_dataset())
    h = harness(
        tmp_path,
        reader,
        POLICY,
        extractor or (lambda _data: good(invoice, fragments)),
        bills=bills,
        **options,
    )
    return h, h.runner.accept(pdf(sid), "faktur.pdf")


def state_of(h: Harness, invoice_id: str) -> dict[str, Any]:
    return h.runner.graph.get_state(h.runner._config(invoice_id)).values


# --- intake ---------------------------------------------------------------------------------


@pytest.mark.aturan("BR-01")
def test_a_refused_file_makes_no_thread(tmp_path: Path) -> None:
    h, _ = start(tmp_path, "COC-01")
    before = len(h.store.invoices)
    with pytest.raises(FileRefused, match="Kirim ulang"):
        h.runner.accept(b"bukan faktur", "x.exe")
    assert len(h.store.invoices) == before


@pytest.mark.aturan("BR-01")
def test_the_same_file_reuses_the_reading_without_the_llm(tmp_path: Path) -> None:
    invoice, fragments = documents("COC-01")
    reads: list[int] = []

    def extractor(_data: bytes) -> ExtractionOutcome:
        reads.append(1)
        return good(invoice, fragments)

    h, first = start(tmp_path, "COC-01", extractor=extractor)
    h.runner.run(first)
    second = h.runner.accept(pdf("COC-01"), "ulang.pdf")
    h.runner.run(second)
    assert len(reads) == 1
    assert h.store.get(second).duplicate_of == first
    assert [r.name for r in h.store.kinds(AuditKind.GUARD) if r.name == "berkas_sama"]


@pytest.mark.aturan("BR-01")
def test_stored_files_carry_no_scenario_name(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-01")
    assert h.store.get(invoice_id).stored_path == f"{invoice_id}/asli.pdf"


# --- route A --------------------------------------------------------------------------------


@pytest.mark.aturan("BR-18")
def test_route_a_makes_one_draft_with_attachment_and_note(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-01")
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.DRAFT_CREATED
    assert (len(h.bills.created), len(h.bills.attachments), len(h.bills.notes)) == (1, 1, 1)
    assert f"[{invoice_id}]" in h.bills.notes[0]
    assert h.mailer.sent == []
    assert {r["status"].value for r in h.store.action_rows.values()} == {"done"}


@pytest.mark.aturan("BR-27")
def test_an_unavailable_odoo_is_retried_and_then_fails(tmp_path: Path) -> None:
    errors: list[Exception] = [Unavailable("x") for _ in range(3)]
    h, invoice_id = start(tmp_path, "COC-01", bills=FakeBills(errors=errors))
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.FAILED and "agent:ulang" in (stop.message or "")
    again = h.runner.retry(invoice_id)
    assert again.status is InvoiceStatus.DRAFT_CREATED
    assert len(h.bills.created) == 1


def test_a_short_outage_is_retried_inside_the_node(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-01", bills=FakeBills(errors=[Unavailable("x")]))
    assert h.runner.run(invoice_id).status is InvoiceStatus.DRAFT_CREATED
    assert len(h.bills.created) == 1


@pytest.mark.aturan("BR-22")
def test_finished_actions_are_not_repeated_on_retry(tmp_path: Path) -> None:
    class NoteFails(FakeBills):
        def post_note(self, bill_id: int, text: str, key: str) -> Any:
            if not self.notes and self.failures:
                self.failures.pop()
                raise Unavailable("x")
            return super().post_note(bill_id, text, key)

    bills = NoteFails()
    bills.failures = [1, 1, 1]  # type: ignore[attr-defined]
    h, invoice_id = start(tmp_path, "COC-01", bills=bills)
    assert h.runner.run(invoice_id).status is InvoiceStatus.FAILED
    assert h.runner.retry(invoice_id).status is InvoiceStatus.DRAFT_CREATED
    assert (len(bills.created), len(bills.attachments), len(bills.notes)) == (1, 1, 1)


@pytest.mark.aturan("BR-27")
def test_access_denied_fails_and_is_a_security_event(tmp_path: Path) -> None:
    bills = FakeBills(errors=[AccessDenied("ditolak")])
    h, invoice_id = start(tmp_path, "COC-01", bills=bills)
    assert h.runner.run(invoice_id).status is InvoiceStatus.FAILED
    assert h.store.kinds(AuditKind.SECURITY)


@pytest.mark.aturan("BR-18")
def test_a_failed_verification_goes_to_route_c_without_approval_on_offer(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-01", bills=FakeBills(verified=False))
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.AWAITING_APPROVAL
    assert ReasonCode.VALIDATION_FAILED.value in stop.reasons
    approval = h.store.current_approval(invoice_id)
    assert approval is not None and Decision.APPROVE.value not in approval.options
    assert approval.evidence["verifikasi_tagihan"] == ["total berbeda"]
    final = h.runner.decide(invoice_id, Decision.REJECT, decided_by="uji")
    assert final.status is InvoiceStatus.REJECTED


# --- route B --------------------------------------------------------------------------------


@pytest.mark.aturan("BR-19")
def test_route_b_sends_one_clarification_to_the_vendor_address(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "HRG-04")
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.AWAITING_VENDOR
    [mail] = h.mailer.sent
    vendor = load_dataset().vendor(LABELS["HRG-04"].vendor)
    assert mail.to == vendor.billing_email and mail.sender == "ap@arunika.test"
    assert mail.subject.startswith(f"[{invoice_id}]")
    assert "Harga satuan" in mail.body and "HRG-04" not in mail.body


@pytest.mark.aturan("BR-19")
def test_a_certain_duplicate_gets_a_notice_and_closes(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "DUP-01")
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.CLOSED_DUPLICATE
    [mail] = h.mailer.sent
    assert "Pemberitahuan" in mail.subject and "tidak perlu dibalas" in mail.body


@pytest.mark.aturan("BR-22")
def test_a_retry_does_not_send_a_second_email(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "HRG-04")
    original = h.mailer.send
    failures = [1]

    def flaky(mail: Any) -> None:
        original(mail)
        if failures:
            failures.pop()
            raise Unavailable("hilang setelah terkirim")

    h.mailer.send = flaky  # type: ignore[method-assign]
    # The node is retried; the message is found by its Message-ID and not sent again.
    assert h.runner.run(invoice_id).status is InvoiceStatus.AWAITING_VENDOR
    assert len(h.mailer.sent) == 1


@pytest.mark.aturan("BR-25")
def test_vendor_without_billing_address_fails_without_sending(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "HRG-04")
    original = h.deps.reader.vendors_by_vat
    h.deps.reader.vendors_by_vat = lambda d: [  # type: ignore[method-assign]
        v.model_copy(update={"email": None}) for v in original(d)
    ]
    h.deps.reader.vendors_by_name = h.deps.reader.vendors_by_vat  # type: ignore[method-assign]
    stop = h.runner.run(invoice_id)
    assert h.mailer.sent == []
    if stop.status is InvoiceStatus.FAILED:
        assert "alamat penagihan" in (stop.message or "")


# --- route C --------------------------------------------------------------------------------


@pytest.mark.aturan("BR-21")
def test_route_c_stops_with_the_evidence_pack_and_no_action(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-04")
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.AWAITING_APPROVAL
    assert h.bills.created == [] and h.mailer.sent == []
    approval = h.store.current_approval(invoice_id)
    assert approval is not None and approval.reasons == ("new_vendor",)
    assert approval.options == ("approve", "reject", "clarify")
    assert set(approval.evidence) >= {"faktur", "pencocokan", "alasan", "usulan"}
    assert approval.evidence["policy_quotes"] == []


@pytest.mark.aturan("BR-22")
def test_approving_makes_one_draft_even_when_sent_twice(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-04")
    h.runner.run(invoice_id)
    first = h.runner.decide(invoice_id, Decision.APPROVE, decided_by="manajer")
    second: Stop = h.runner.decide(invoice_id, Decision.REJECT, decided_by="orang lain")
    assert first.status is InvoiceStatus.DRAFT_CREATED
    assert second.status is InvoiceStatus.DRAFT_CREATED and second.already_decided
    assert len(h.bills.created) == 1
    approval = h.store.current_approval(invoice_id)
    assert approval is not None
    assert (approval.decision, approval.decided_by) == ("approve", "manajer")


def test_rejecting_closes_without_any_action(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "DUP-03")
    h.runner.run(invoice_id)
    assert h.runner.decide(invoice_id, Decision.REJECT, decided_by="u").status is (
        InvoiceStatus.REJECTED
    )
    assert h.bills.created == [] and h.mailer.sent == []


@pytest.mark.aturan("BR-19")
def test_clarifying_emails_the_vendor_with_the_approver_note(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-04")
    h.runner.run(invoice_id)
    stop = h.runner.decide(
        invoice_id, Decision.CLARIFY, decided_by="manajer", note="Mohon kirim surat jalan."
    )
    assert stop.status is InvoiceStatus.AWAITING_VENDOR
    [mail] = h.mailer.sent
    assert "Mohon kirim surat jalan." in mail.body
    assert h.bills.created == []


@pytest.mark.aturan("BR-21")
def test_an_unregistered_vendor_cannot_be_asked(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "NPO-05")
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.AWAITING_APPROVAL
    approval = h.store.current_approval(invoice_id)
    assert approval is not None and Decision.CLARIFY.value not in approval.options
    with pytest.raises(DecisionRefused, match="tidak tersedia"):
        h.runner.decide(invoice_id, Decision.CLARIFY, decided_by="u")


def test_a_decision_needs_an_open_request(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-01")
    h.runner.run(invoice_id)
    with pytest.raises(DecisionRefused, match="permintaan"):
        h.runner.decide(invoice_id, Decision.APPROVE, decided_by="u")


def test_a_long_note_is_refused(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-04")
    h.runner.run(invoice_id)
    with pytest.raises(DecisionRefused, match="500"):
        h.runner.decide(invoice_id, Decision.REJECT, decided_by="u", note="x" * 501)


@pytest.mark.aturan("BR-25")
def test_injected_address_in_the_document_is_never_used(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "INJ-02")
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.AWAITING_APPROVAL
    h.runner.decide(invoice_id, Decision.CLARIFY, decided_by="u")
    assert [m.to for m in h.mailer.sent] != ["audit@pemeriksa-eksternal.test"]
    vendor = load_dataset().vendor(LABELS["INJ-02"].vendor)
    assert [m.to for m in h.mailer.sent] == [vendor.billing_email]


# --- BR-04, BR-15, BR-27 --------------------------------------------------------------------


def test_an_unreadable_document_goes_to_route_c_with_its_attempts(tmp_path: Path) -> None:
    finding = Finding(
        rule="BR-04",
        reason=ReasonCode.VALIDATION_FAILED,
        message="tidak terbaca",
        evidence=ExtractionEvidence(fields=("total",), problems=("total: x",), attempts=("1",)),
    )
    outcome = ExtractionOutcome(
        status=ExtractionStatus.VALIDATION_FAILED, source=ExtractionSource.LLM, finding=finding
    )
    h, invoice_id = start(tmp_path, "COC-01", extractor=lambda _d: outcome)
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.AWAITING_APPROVAL
    assert stop.reasons == ("validation_failed",)
    approval = h.store.current_approval(invoice_id)
    assert approval is not None and Decision.APPROVE.value not in approval.options


@pytest.mark.aturan("BR-27")
def test_an_unavailable_llm_fails_and_continues_on_retry(tmp_path: Path) -> None:
    invoice, fragments = documents("COC-01")
    results: list[Any] = [LlmUnavailable("habis"), good(invoice, fragments)]

    def extractor(_data: bytes) -> ExtractionOutcome:
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    h, invoice_id = start(tmp_path, "COC-01", extractor=extractor)
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.FAILED and "LLM" in (stop.message or "")
    assert h.runner.retry(invoice_id).status is InvoiceStatus.DRAFT_CREATED


def _review(status: ReviewStatus) -> ReviewOutcome:
    injection = False if status is ReviewStatus.OK else None
    return ReviewOutcome(InjectionReview(status=status, injection=injection), ())


@pytest.mark.aturan("BR-27")
def test_route_a_waits_for_layer_two_and_retry_runs_it_again(tmp_path: Path) -> None:
    answers = [_review(ReviewStatus.UNAVAILABLE), _review(ReviewStatus.OK)]
    h, invoice_id = start(tmp_path, "COC-01", reviewer=lambda _f: answers.pop(0))
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.FAILED and "lapis 2" in (stop.message or "")
    assert h.bills.created == []
    assert h.runner.retry(invoice_id).status is InvoiceStatus.DRAFT_CREATED
    assert answers == []


@pytest.mark.aturan("BR-27")
@pytest.mark.parametrize(
    "status",
    [InvoiceStatus.RECEIVED, InvoiceStatus.AWAITING_VENDOR, InvoiceStatus.AWAITING_APPROVAL],
)
def test_an_error_outside_processing_still_ends_in_failed(
    tmp_path: Path, status: InvoiceStatus
) -> None:
    h, invoice_id = start(tmp_path, "COC-01")
    h.store.invoices[invoice_id].status = status.value

    def broken() -> None:
        raise RuntimeError("rusak")

    stop = h.runner._advance(invoice_id, broken)
    assert stop.status is InvoiceStatus.FAILED and "agent:ulang" in (stop.message or "")
    assert h.store.statuses == [f"{status.value}>processing", "processing>failed"]


def test_an_error_after_a_final_status_does_not_reopen_it(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-01")
    h.store.invoices[invoice_id].status = InvoiceStatus.DRAFT_CREATED.value

    def broken() -> None:
        raise RuntimeError("rusak")

    assert h.runner._advance(invoice_id, broken).status is InvoiceStatus.DRAFT_CREATED
    assert [r.name for r in h.store.trail][-1] == "galat_setelah_status_akhir"


def test_retry_needs_a_failed_invoice(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-04")
    h.runner.run(invoice_id)
    with pytest.raises(DecisionRefused, match="failed"):
        h.runner.retry(invoice_id)


def test_every_status_change_follows_the_table(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "COC-04")
    h.runner.run(invoice_id)
    h.runner.decide(invoice_id, Decision.APPROVE, decided_by="u")
    assert h.store.statuses == [
        "received>processing",
        "processing>awaiting_approval",
        "awaiting_approval>processing",
        "processing>draft_created",
    ]


# --- LLM inside its guards ------------------------------------------------------------------


def test_the_planner_only_advises_and_a_difference_is_traced(tmp_path: Path) -> None:
    outcome = PlannerOutcome(status="proposed", proposal="P99999", explanation="tebakan")
    h, invoice_id = start(tmp_path, "NPO-01", planner=lambda *_a: outcome)
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.DRAFT_CREATED  # BR-06 still decides
    plan = state_of(h, invoice_id)["planner"]
    assert plan["agrees"] is False and plan["proposal"] == "P99999"
    [row] = [r for r in h.store.trail if r.name == "perencana_identify"]
    assert "berbeda" in (row.summary or "")


def test_an_unavailable_planner_leaves_a_trace_and_the_rule_goes_on(tmp_path: Path) -> None:
    down = PlannerOutcome(status="unavailable")
    h, invoice_id = start(tmp_path, "NPO-01", planner=lambda *_a: down)
    assert h.runner.run(invoice_id).status is InvoiceStatus.DRAFT_CREATED
    [row] = [r for r in h.store.trail if r.name == "perencana_identify"]
    assert "tidak tersedia" in (row.summary or "") and row.status == "unavailable"


def test_the_planner_is_not_called_when_the_printed_po_is_found(tmp_path: Path) -> None:
    def planner(*_a: Any) -> PlannerOutcome:
        raise AssertionError("tidak boleh dipanggil")

    h, invoice_id = start(tmp_path, "COC-01", planner=planner)
    assert h.runner.run(invoice_id).status is InvoiceStatus.DRAFT_CREATED


def _kth06_mapping(invoice: Any, po: Any, lines: Any) -> LineMappingOutcome:
    del invoice
    open_lines = [m.line for m in lines.matches if m.status == "no_match"]
    taken = {m.po_line_id for m in lines.matches}
    free = [pl.id for pl in po.lines if pl.id not in taken]
    return LineMappingOutcome(
        mapping=dict(zip(open_lines, free, strict=False)), called=True, calls=()
    )


@pytest.mark.aturan("BR-07")
def test_an_llm_mapping_lets_lines_without_codes_become_a_draft(tmp_path: Path) -> None:
    h, invoice_id = start(tmp_path, "KTH-06", line_mapper=_kth06_mapping)
    stop = h.runner.run(invoice_id)
    assert stop.status is InvoiceStatus.DRAFT_CREATED and stop.reasons == ()
    matches = state_of(h, invoice_id)["analysis"]["lines"]["matches"]
    assert {m["method"] for m in matches} == {"llm"}


def test_an_unavailable_mapper_is_a_system_failure(tmp_path: Path) -> None:
    def mapper(*_a: Any) -> LineMappingOutcome:
        raise LlmUnavailable("habis")

    h, invoice_id = start(tmp_path, "KTH-06", line_mapper=mapper)
    assert h.runner.run(invoice_id).status is InvoiceStatus.FAILED
