"""Tool ``extract_invoice``: UBL by code, PDF and images by the LLM, refused files fail."""

import pytest

from extraction_support import extracted
from faktur_beres.config import REPO_ROOT
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.evals.labels import load_labels, truth_to_invoice
from faktur_beres.extraction.outcome import ExtractionSource, ExtractionStatus
from faktur_beres.llm.contracts import BlobPart
from faktur_beres.tools.extract_invoice import extract_invoice
from llm_fakes import ScriptedLlm

POLICY = load_policy()
LABELS = load_labels()


def file_of(scenario: str) -> bytes:
    return (REPO_ROOT / LABELS[scenario].berkas.path).read_bytes()


def test_ubl_is_read_by_code_without_the_llm() -> None:
    llm = ScriptedLlm([])
    outcome = extract_invoice(file_of("PJK-01"), llm=llm, policy=POLICY)
    assert (outcome.status, outcome.source) == (ExtractionStatus.OK, ExtractionSource.UBL)
    assert outcome.invoice == truth_to_invoice(LABELS["PJK-01"].kebenaran_dokumen)
    assert llm.requests == [] and outcome.attempts == ()


def test_invalid_ubl_fails_validation_without_a_re_read() -> None:
    data = file_of("PJK-01").replace(
        b"<cbc:DocumentCurrencyCode>IDR", b"<cbc:DocumentCurrencyCode>USD"
    )
    llm = ScriptedLlm([])
    outcome = extract_invoice(data, llm=llm, policy=POLICY)
    assert (outcome.status, outcome.source) == (
        ExtractionStatus.VALIDATION_FAILED,
        ExtractionSource.UBL,
    )
    assert outcome.finding is not None and outcome.finding.reason is ReasonCode.VALIDATION_FAILED
    assert llm.requests == []


@pytest.mark.parametrize(
    ("scenario", "mime"), [("COC-01", "application/pdf"), ("HRG-05", "image/jpeg")]
)
def test_pdf_and_images_are_read_by_the_llm(scenario: str, mime: str) -> None:
    truth = LABELS[scenario].kebenaran_dokumen
    llm = ScriptedLlm([extracted(truth), extracted(truth)])
    outcome = extract_invoice(file_of(scenario), llm=llm, policy=POLICY)
    assert outcome.source is ExtractionSource.LLM and outcome.status is ExtractionStatus.OK
    blob = llm.requests[0].parts[0]
    assert isinstance(blob, BlobPart) and blob.mime_type == mime


@pytest.mark.parametrize(
    "data", [b"GIF89a\x01\x00", b"", b"%PDF-1.7 rusak"], ids=["gif", "kosong", "pdf-rusak"]
)
def test_refused_files_fail_validation(data: bytes) -> None:
    llm = ScriptedLlm([])
    outcome = extract_invoice(data, llm=llm, policy=POLICY)
    assert (outcome.status, outcome.source) == (
        ExtractionStatus.VALIDATION_FAILED,
        ExtractionSource.REJECTED,
    )
    assert outcome.finding is not None and outcome.finding.rule == "BR-04"
    assert llm.requests == []
