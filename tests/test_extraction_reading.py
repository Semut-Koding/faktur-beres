"""BR-04: validation and re-reading of a Gemini extraction, one test per branch (fake LLM)."""

import re
from typing import Any

import pytest

from extraction_support import extracted, extracted_dict, rp
from faktur_beres.config import REPO_ROOT
from faktur_beres.dataset.loader import load_dataset
from faktur_beres.domain.engine import analyse
from faktur_beres.domain.findings import ExtractionEvidence
from faktur_beres.domain.invoice import TextSource
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.arithmetic import check_arithmetic
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.labels import label_intake, load_labels, truth_to_invoice
from faktur_beres.extraction.filetype import identify
from faktur_beres.extraction.outcome import ExtractionOutcome, ExtractionSource, ExtractionStatus
from faktur_beres.extraction.reading import PROMPT_VERSION, read_with_llm
from faktur_beres.extraction.schema import ExtractedInvoice
from faktur_beres.llm.contracts import (
    BlobPart,
    LlmInvalidOutput,
    LlmUnavailable,
    ModelRole,
)
from llm_fakes import ScriptedLlm

POLICY = load_policy()
LABELS = load_labels()
READER = DatasetErpReader(load_dataset())
COC_01 = LABELS["COC-01"].kebenaran_dokumen

pytestmark = pytest.mark.aturan("BR-04")


def run(scenario: str, answers: list[Any]) -> tuple[ExtractionOutcome, ScriptedLlm]:
    data = (REPO_ROOT / LABELS[scenario].berkas.path).read_bytes()
    llm = ScriptedLlm(answers)
    return read_with_llm(llm, data, identify(data), POLICY), llm


def reasons(scenario: str, outcome: ExtractionOutcome) -> tuple[ReasonCode, ...]:
    assert outcome.invoice is not None
    intake = label_intake(LABELS[scenario])
    return analyse(outcome.invoice, intake, outcome.fragments, READER, POLICY).reasons


def misread_total(text: str) -> ExtractedInvoice:
    return extracted(COC_01, total=text)


def test_the_first_reading_that_passes_is_used() -> None:
    outcome, llm = run("COC-01", [extracted(COC_01)])
    assert outcome.status is ExtractionStatus.OK and outcome.source is ExtractionSource.LLM
    assert outcome.invoice == truth_to_invoice(COC_01)
    assert len(outcome.attempts) == 1 and outcome.used_readings == (1,)
    [request] = llm.requests
    assert (request.role, request.purpose, request.prompt_version) == (
        ModelRole.PRIMARY,
        "extraction",
        PROMPT_VERSION,
    )
    blob = request.parts[0]
    assert isinstance(blob, BlobPart) and blob.mime_type == "application/pdf"
    assert "faktur.pdf" not in llm.texts(0)  # file names never reach the LLM


def test_a_misread_is_corrected_and_confirmed() -> None:
    wrong = rp(COC_01.total)[:-1]  # one digit lost: the grouping no longer reads
    outcome, llm = run("COC-01", [misread_total(wrong), extracted(COC_01), extracted(COC_01)])
    assert outcome.status is ExtractionStatus.OK
    assert outcome.invoice == truth_to_invoice(COC_01)
    assert len(outcome.attempts) == 3 and outcome.used_readings == (2, 3)
    assert f'total: angka atau tanggal tidak terbaca; terbaca "{wrong}"' in llm.texts(1)
    assert f'terbaca "{wrong}" lalu "{rp(COC_01.total)}"' in llm.texts(2)


def test_re_reads_use_the_strong_model() -> None:
    wrong = rp(COC_01.total)[:-1]
    outcome, llm = run("COC-01", [misread_total(wrong), extracted(COC_01), extracted(COC_01)])
    assert [r.role for r in llm.requests] == [ModelRole.PRIMARY, ModelRole.STRONG, ModelRole.STRONG]
    assert [r.purpose for r in llm.requests] == [
        "extraction",
        "extraction_reread",
        "extraction_reread",
    ]
    assert [a.model for a in outcome.attempts] == ["model-utama", "model-kuat", "model-kuat"]


def test_inconsistent_arithmetic_read_consistently_becomes_arithmetic_error() -> None:
    truth = LABELS["HRG-05"].kebenaran_dokumen
    outcome, _ = run("HRG-05", [extracted(truth), extracted(truth)])
    assert outcome.status is ExtractionStatus.OK and outcome.used_readings == (1, 2)
    assert outcome.invoice == truth_to_invoice(truth)  # as printed
    assert ReasonCode.ARITHMETIC_ERROR in reasons("HRG-05", outcome)


def test_a_15_digit_npwp_read_consistently_becomes_tax_invalid() -> None:
    seller = extracted_dict(COC_01)["seller"] | {"npwp": "01.234.567.8-901.000"}
    reading = extracted(COC_01, seller=seller)
    outcome, _ = run("COC-01", [reading, reading])
    assert outcome.status is ExtractionStatus.OK and len(outcome.attempts) == 2
    assert outcome.invoice is not None and outcome.invoice.seller.npwp == "01.234.567.8-901.000"
    assert ReasonCode.TAX_INVALID in reasons("COC-01", outcome)


def test_readings_that_keep_changing_fail_validation() -> None:
    answers = [
        misread_total(rp(COC_01.total)[:-1]),
        extracted(COC_01),
        misread_total(rp(COC_01.total + 1000)),
    ]
    outcome, _ = run("COC-01", answers)
    assert outcome.status is ExtractionStatus.VALIDATION_FAILED and outcome.invoice is None
    assert len(outcome.attempts) == 3
    finding = outcome.finding
    assert finding is not None
    assert (finding.rule, finding.reason) == ("BR-04", ReasonCode.VALIDATION_FAILED)
    assert isinstance(finding.evidence, ExtractionEvidence)
    assert finding.evidence.fields == ("total",)
    assert len(finding.evidence.attempts) == 3


def test_an_ambiguous_number_read_the_same_twice_fails_validation() -> None:
    line = {
        "vendor_code": None,
        "description": "Barang uji",
        "quantity": "5",
        "unit": "Unit",
        "unit_price": "1.500",
        "amount": "7.500",
    }
    reading = extracted(
        COC_01, lines=[line], subtotal="7.500", dpp_nilai_lain=None, vat="825", total="8.325"
    )
    outcome, _ = run("COC-01", [reading, reading])
    assert outcome.status is ExtractionStatus.VALIDATION_FAILED and len(outcome.attempts) == 2
    assert outcome.finding is not None and isinstance(outcome.finding.evidence, ExtractionEvidence)
    assert any("ambiguous" in p for p in outcome.finding.evidence.problems)


def test_output_that_never_matches_the_schema_fails_validation() -> None:
    outcome, llm = run("COC-01", [LlmInvalidOutput("tidak valid") for _ in range(3)])
    assert outcome.status is ExtractionStatus.VALIDATION_FAILED
    assert [a.valid_output for a in outcome.attempts] == [False, False, False]
    assert outcome.finding is not None and "skema" in outcome.finding.message
    assert len(llm.requests) == 3


def test_an_unavailable_llm_reaches_the_caller_with_the_audit() -> None:
    first = misread_total(rp(COC_01.total)[:-1])
    with pytest.raises(LlmUnavailable) as caught:
        run("COC-01", [first, LlmUnavailable("kedua model tidak tersedia", [])])
    assert len(caught.value.calls) == 1  # the call of reading 1


def test_feedback_never_holds_a_computed_number() -> None:
    truth = LABELS["HRG-05"].kebenaran_dokumen
    _, llm = run("HRG-05", [extracted(truth), extracted(truth)])
    feedback = llm.texts(1)
    assert "lines[" in feedback  # the fields of the equation are named
    computed = [
        f.evidence.computed  # type: ignore[union-attr]
        for f in check_arithmetic(truth_to_invoice(truth), POLICY)
    ]
    assert computed
    digits = re.sub(r"\D", "", feedback)
    for value in computed:
        whole = str(int(value))
        assert whole not in digits
        assert f"{int(value):,}".replace(",", ".") not in feedback


def test_fragments_hold_the_text_layer_and_every_free_text() -> None:
    truth = LABELS["INJ-01"].kebenaran_dokumen
    outcome, _ = run("INJ-01", [extracted(truth)])
    sources = {f.source for f in outcome.fragments}
    assert {TextSource.PDF_TEXT_LAYER, TextSource.READ_TEXT, TextSource.DOCUMENT_FIELD} <= sources
    read = [f.text for f in outcome.fragments if f.source is TextSource.READ_TEXT]
    assert read == list(truth.teks_tersembunyi)
