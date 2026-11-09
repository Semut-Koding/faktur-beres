"""The result of reading one incoming file, whatever path it took (M3.4).

``ExtractionOutcome`` holds the status (``ok`` or ``validation_failed``), the ``InvoiceData``, the
free-text fragments for the injection check (BR-15), every reading attempt with its model, problems
and token use (BR-23), and notes. ``validation_failed`` carries a ``Finding`` of rule BR-04 with
reason ``validation_failed`` (C4) and the fields and attempts as evidence.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.findings import ExtractionEvidence, Finding
from faktur_beres.domain.invoice import InvoiceData, TextFragment
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.extraction.convert import FieldProblem
from faktur_beres.extraction.filetype import FileRejected
from faktur_beres.extraction.ubl import UblError, parse_ubl
from faktur_beres.llm.contracts import LlmCallRecord, ModelRole

RULE = "BR-04"


class ExtractionStatus(StrEnum):
    OK = "ok"
    VALIDATION_FAILED = "validation_failed"


class ExtractionSource(StrEnum):
    UBL = "ubl"  # read by the UBL parser, never re-read
    LLM = "llm"  # read by Gemini, with BR-04 re-reads
    REJECTED = "rejected"  # the file itself was refused (type, size, pages)


class ExtractionAttempt(DomainModel):
    """One reading of the document: one structured request, its retries included."""

    number: int
    role: ModelRole
    model: str | None  # None when the output never matched the schema
    valid_output: bool
    problems: tuple[FieldProblem, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: Decimal | Literal["tidak diketahui"] = Decimal(0)
    calls: tuple[LlmCallRecord, ...] = ()

    def summary(self) -> str:
        if not self.valid_output:
            return f"{self.number}: keluaran tidak sesuai skema"
        found = f"{len(self.problems)} masalah" if self.problems else "lolos validasi"
        return f"{self.number}: {self.model}, {found}"


class ExtractionOutcome(DomainModel):
    status: ExtractionStatus
    source: ExtractionSource
    invoice: InvoiceData | None = None
    fragments: tuple[TextFragment, ...] = ()
    attempts: tuple[ExtractionAttempt, ...] = ()
    used_readings: tuple[int, ...] = ()  # the attempt numbers whose reading was used
    notes: tuple[str, ...] = ()
    finding: Finding | None = None

    @property
    def calls(self) -> tuple[LlmCallRecord, ...]:
        return tuple(call for attempt in self.attempts for call in attempt.calls)


def problem_text(problem: FieldProblem) -> str:
    shown = "kosong" if problem.text is None else repr(problem.text)
    return f"{problem.path}: {problem.kind.value} ({shown})"


def failed(
    source: ExtractionSource,
    message: str,
    *,
    problems: tuple[str, ...],
    fields: tuple[str, ...] = (),
    attempts: tuple[ExtractionAttempt, ...] = (),
    fragments: tuple[TextFragment, ...] = (),
    notes: tuple[str, ...] = (),
) -> ExtractionOutcome:
    """A ``validation_failed`` outcome with its BR-04 finding."""
    finding = Finding(
        rule=RULE,
        reason=ReasonCode.VALIDATION_FAILED,
        message=message,
        evidence=ExtractionEvidence(
            fields=fields,
            problems=problems,
            attempts=tuple(attempt.summary() for attempt in attempts),
        ),
    )
    return ExtractionOutcome(
        status=ExtractionStatus.VALIDATION_FAILED,
        source=source,
        fragments=fragments,
        attempts=attempts,
        notes=notes,
        finding=finding,
    )


def read_ubl_file(data: bytes) -> ExtractionOutcome:
    """UBL is read by code and never re-read: invalid UBL is ``validation_failed`` at once."""
    try:
        reading = parse_ubl(data)
    except UblError as exc:
        return failed(
            ExtractionSource.UBL,
            "E-invoice UBL tidak lolos validasi; UBL tidak dibaca ulang (BR-04).",
            problems=exc.problems,
        )
    return ExtractionOutcome(
        status=ExtractionStatus.OK,
        source=ExtractionSource.UBL,
        invoice=reading.invoice,
        fragments=reading.fragments,
        notes=reading.remarks,
    )


def rejected(exc: FileRejected) -> ExtractionOutcome:
    return failed(
        ExtractionSource.REJECTED,
        f"Berkas ditolak sebelum dibaca: {exc}.",
        problems=(f"berkas: {exc.reason} ({exc})",),
    )
