"""M4a.5, criterion 7: with the LLM off, the graph gives what ``analyse()`` gives (no services)."""

from pathlib import Path

import pytest

from faktur_beres.dataset import load_dataset
from faktur_beres.domain.engine import AnalysisResult, analyse
from faktur_beres.domain.invoice import Channel, IntakeContext, InvoiceData, TextFragment
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.rules.routing import initial_status
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.labels import (
    email_fragments,
    label_intake,
    load_labels,
    load_robustness_labels,
    truth_fragments,
    truth_to_invoice,
)
from faktur_beres.extraction.outcome import ExtractionOutcome, ExtractionSource, ExtractionStatus
from graph_fakes import harness

POLICY = load_policy()
LABELS = load_labels()
ROBUST = load_robustness_labels()
PDF = b"%PDF-1.7\n"
UBL = b'<?xml version="1.0"?><Invoice/>'


def reader() -> DatasetErpReader:
    return DatasetErpReader(load_dataset())


def extraction(invoice: InvoiceData, fragments: list[TextFragment]) -> ExtractionOutcome:
    return ExtractionOutcome(
        status=ExtractionStatus.OK,
        source=ExtractionSource.UBL,
        invoice=invoice,
        fragments=tuple(fragments),
    )


def assert_same(stop_status: str, state: dict, expected: AnalysisResult) -> None:
    got = AnalysisResult.model_validate(state["analysis"])
    assert got.reasons == expected.reasons
    assert got.default_route == expected.default_route
    assert got.allowed_routes == expected.allowed_routes
    assert got.bill_plan == expected.bill_plan
    assert stop_status == initial_status(expected.default_route, expected.reasons).value


def run(tmp_path: Path, invoice: InvoiceData, fragments: list[TextFragment], intake: IntakeContext):
    h = harness(tmp_path, reader(), POLICY, lambda _data: extraction(invoice, fragments))
    subject = body = None
    email_parts = [f for f in fragments if f.source.value.startswith("email")]
    non_email = [f for f in fragments if not f.source.value.startswith("email")]
    if email_parts:
        subject, body = email_parts[0].text, email_parts[1].text
        h = harness(tmp_path, reader(), POLICY, lambda _data: extraction(invoice, non_email))
    invoice_id = h.runner.accept(
        UBL if intake.file_name and intake.file_name.endswith(".xml") else _pdf(),
        intake.file_name or "faktur.pdf",
        channel=intake.channel,
        sender_email=intake.sender_email,
        subject=subject,
        body=body,
    )
    stop = h.runner.run(invoice_id)
    state = h.runner.graph.get_state(h.runner._config(invoice_id)).values
    return stop, state, h


def _pdf() -> bytes:
    from io import BytesIO

    from reportlab.pdfgen.canvas import Canvas

    buffer = BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(72, 720, "uji")
    canvas.save()
    return buffer.getvalue()


@pytest.mark.parametrize("sid", sorted(LABELS))
def test_scenarios(sid: str, tmp_path: Path) -> None:
    label = LABELS[sid]
    truth = label.kebenaran_dokumen
    invoice = truth_to_invoice(truth)
    fragments = truth_fragments(truth, document_format=label.format) + email_fragments(label)
    intake = label_intake(label)
    expected = analyse(invoice, intake, fragments, reader(), POLICY)
    stop, state, h = run(tmp_path, invoice, fragments, intake)
    assert_same(stop.status.value, state, expected)
    assert len(h.bills.created) == (1 if expected.default_route.value == "A" else 0)


@pytest.mark.parametrize("sid", sorted(ROBUST))
def test_robustness_documents(sid: str, tmp_path: Path) -> None:
    label = ROBUST[sid]
    truth = label.kebenaran_dokumen
    invoice = truth_to_invoice(truth)
    fragments = truth_fragments(truth, document_format=label.format)
    intake = IntakeContext(
        invoice_id="x", channel=Channel.UPLOAD, file_name=label.berkas.nama_kirim
    )
    expected = analyse(invoice, intake, fragments, reader(), POLICY)
    stop, state, _ = run(tmp_path, invoice, fragments, intake)
    assert_same(stop.status.value, state, expected)
