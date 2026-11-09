"""Gemini extraction with validation and re-reading (BR-04, SPEC 0.4) (M3.4).

Attempt 1 uses the primary model; re-reads use the strong model (the client falls back to the
primary model when the strong one is unavailable). At most three readings per document. The
client's own retries (network, quota, invalid output) are not readings, but every call is kept in
the attempt's audit records (BR-23).

* The first reading that passes validation is used.
* Otherwise the problem fields of the first reading are the focus. A re-read is asked with feedback
  that names each field, the kind of problem and the text read, never a computed number.
* Two readings are consistent when every focus field has the same normalised value in both.
* Reading 2 equal to reading 1: consistent, decided now. Reading 2 different, even when it passes:
  reading 3 confirms. Reading 3 equal to one earlier reading: that pair is used. Otherwise:
  ``validation_failed``.
* A consistent pair is decided on its earlier reading. Passes: used. Fails only on arithmetic or
  the NPWP/NSFP format: used as printed, and the rules give B6 or B3. Unreadable, ambiguous or a
  required field empty: ``validation_failed``.

A reading whose output never matched the schema (``LlmInvalidOutput``) is an attempt without a
reading. ``LlmUnavailable`` and ``LlmConfigError`` reach the caller, with the audit records of every
call so far; they never become ``validation_failed`` (BR-27 applies in M4).

Comparison mode (M3.7) runs every attempt on one model: the caller gives a client whose primary and
strong models are the same.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from faktur_beres.domain.invoice import TextFragment, TextSource
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.rules.injection import merge_fragments
from faktur_beres.extraction.convert import Conversion, FieldProblem, convert, json_path
from faktur_beres.extraction.filetype import FileKind, IncomingFile
from faktur_beres.extraction.outcome import (
    ExtractionAttempt,
    ExtractionOutcome,
    ExtractionSource,
    ExtractionStatus,
    failed,
    problem_text,
)
from faktur_beres.extraction.pdf_text import text_layer
from faktur_beres.extraction.schema import ExtractedInvoice
from faktur_beres.llm.contracts import (
    BlobPart,
    DocumentMime,
    LlmCallRecord,
    LlmConfigError,
    LlmInvalidOutput,
    LlmRequest,
    LlmUnavailable,
    ModelRole,
    StructuredLlm,
    TextPart,
    ThinkingLevel,
)
from faktur_beres.llm.cost import total_cost
from faktur_beres.llm.prompts import Prompt, load_prompt

PROMPT_PACKAGE = "faktur_beres.extraction"
PROMPT_VERSION = "extraction-v1"
MAX_READINGS = 3
MAX_OUTPUT_TOKENS = 16384
DOCUMENT_MIME: dict[FileKind, DocumentMime] = {
    FileKind.PDF: "application/pdf",
    FileKind.PNG: "image/png",
    FileKind.JPEG: "image/jpeg",
}
_DOCUMENT_FIELDS = (
    "penjual.nama",
    "penjual.alamat",
    "pembeli.nama",
    "pembeli.alamat",
    "rekening.bank",
    "rekening.atas_nama",
)


@dataclass(frozen=True)
class _Reading:
    attempt: int
    extracted: ExtractedInvoice
    conversion: Conversion


def _consistent(a: _Reading, b: _Reading, focus: Sequence[FieldProblem]) -> bool:
    paths = {problem.path for problem in focus}
    return all(a.conversion.values.get(p) == b.conversion.values.get(p) for p in paths)


def _feedback(prompt: Prompt, focus: Sequence[FieldProblem], readings: Sequence[_Reading]) -> str:
    """The re-read request: field, kind of problem and every text read; never a computed value."""
    kinds = prompt.entries("problems")
    lines: list[str] = []
    seen: set[str] = set()
    for problem in focus:
        if problem.path in seen:
            continue
        seen.add(problem.path)
        texts = [r.conversion.texts.get(problem.path) for r in readings]
        read = " lalu ".join(f'"{text}"' if text is not None else "kosong" for text in texts)
        lines.append(f"- {json_path(problem.path)}: {kinds[problem.kind.value]}; terbaca {read}")
    return prompt["reread"].replace("{fields}", "\n".join(lines))


def _request(
    prompt: Prompt, data: bytes, file: IncomingFile, attempt: int, feedback: str | None
) -> LlmRequest:
    parts: list[BlobPart | TextPart] = [
        BlobPart(data=data, mime_type=DOCUMENT_MIME[file.kind]),
        TextPart(text=prompt["request"]),
    ]
    if feedback:
        parts.append(TextPart(text=feedback))
    first = attempt == 1
    return LlmRequest(
        purpose="extraction" if first else "extraction_reread",
        role=ModelRole.PRIMARY if first else ModelRole.STRONG,
        prompt_version=prompt.version,
        system_instruction=prompt["system"],
        parts=tuple(parts),
        thinking_level=ThinkingLevel.LOW,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def _attempt(
    number: int,
    role: ModelRole,
    model: str | None,
    calls: Sequence[LlmCallRecord],
    problems: Sequence[FieldProblem] = (),
    *,
    valid: bool,
) -> ExtractionAttempt:
    return ExtractionAttempt(
        number=number,
        role=role,
        model=model,
        valid_output=valid,
        problems=tuple(problems),
        input_tokens=sum(c.input_tokens or 0 for c in calls),
        output_tokens=sum(c.output_tokens or 0 for c in calls),
        thinking_tokens=sum(c.thinking_tokens or 0 for c in calls),
        cost_usd=total_cost(calls) if calls else Decimal(0),
        calls=tuple(calls),
    )


def _fragments(
    readings: Sequence[_Reading], layer: Sequence[TextFragment]
) -> tuple[TextFragment, ...]:
    """Every free text of every reading (BR-15 checks them all), plus the PDF text layer."""
    groups: list[list[TextFragment]] = [list(layer)]
    for reading in readings:
        x = reading.extracted
        texts = reading.conversion.texts
        group = [
            TextFragment(source=TextSource.NOTE, text=note, where=f"catatan {i}")
            for i, note in enumerate(x.notes, start=1)
            if note.strip()
        ]
        group += [
            TextFragment(source=TextSource.READ_TEXT, text=text, where=f"teks lain {i}")
            for i, text in enumerate(x.other_text, start=1)
            if text.strip()
        ]
        group += [
            TextFragment(
                source=TextSource.LINE_DESCRIPTION, text=line.description, where=f"baris {i}"
            )
            for i, line in enumerate(x.lines, start=1)
            if line.description and line.description.strip()
        ]
        group += [
            TextFragment(source=TextSource.DOCUMENT_FIELD, text=text, where=path)
            for path in _DOCUMENT_FIELDS
            if (text := texts.get(path))
        ]
        groups.append(group)
    return merge_fragments(*groups)


def read_with_llm(
    llm: StructuredLlm,
    data: bytes,
    file: IncomingFile,
    policy: PolicyConfig,
    *,
    prompt: Prompt | None = None,
) -> ExtractionOutcome:
    """Read a PDF or an image with the LLM, validate it, and re-read it as BR-04 says."""
    if file.kind not in DOCUMENT_MIME:
        raise ValueError(f"{file.kind} tidak dibaca LLM")
    prompt = prompt or load_prompt(PROMPT_PACKAGE, PROMPT_VERSION)
    layer = text_layer(file, data).fragments()
    attempts: list[ExtractionAttempt] = []
    readings: list[_Reading] = []
    focus: tuple[FieldProblem, ...] = ()

    def done(base: _Reading, pair: tuple[int, ...], note: str) -> ExtractionOutcome:
        fragments = _fragments([base, *[r for r in readings if r is not base]], layer)
        conversion = base.conversion
        if conversion.invoice is None or conversion.blocking:
            blocking = conversion.blocking or conversion.problems
            return failed(
                ExtractionSource.LLM,
                f"{note}, tetapi nilainya tidak terbaca, ambigu, atau field wajib kosong.",
                fields=tuple(dict.fromkeys(p.path for p in blocking)),
                problems=tuple(problem_text(p) for p in blocking),
                attempts=tuple(attempts),
                fragments=fragments,
                notes=(note,),
            )
        notes = [note]
        if conversion.problems:
            notes.append(
                "Dipakai seperti tercetak; aturan menilai aritmetika (B6) dan format "
                "NPWP/NSFP (B3)."
            )
        return ExtractionOutcome(
            status=ExtractionStatus.OK,
            source=ExtractionSource.LLM,
            invoice=conversion.invoice,
            fragments=fragments,
            attempts=tuple(attempts),
            used_readings=pair,
            notes=tuple(notes),
        )

    for number in range(1, MAX_READINGS + 1):
        feedback = _feedback(prompt, focus, readings) if readings and focus else None
        request = _request(prompt, data, file, number, feedback)
        try:
            result = llm.generate(request, ExtractedInvoice)
        except LlmInvalidOutput as exc:
            attempts.append(_attempt(number, request.role, None, exc.calls, valid=False))
            continue
        except (LlmUnavailable, LlmConfigError) as exc:
            previous = [call for attempt in attempts for call in attempt.calls]
            raise type(exc)(str(exc), [*previous, *exc.calls]) from exc
        conversion = convert(result.value, policy)
        attempts.append(
            _attempt(
                number, request.role, result.model, result.calls, conversion.problems, valid=True
            )
        )
        reading = _Reading(attempt=number, extracted=result.value, conversion=conversion)
        readings.append(reading)
        if len(readings) == 1:
            if not conversion.problems:
                return done(reading, (number,), f"Pembacaan {number} lolos validasi")
            focus = conversion.problems
            continue
        for earlier in reversed(readings[:-1]):
            if _consistent(earlier, reading, focus):
                pair = (earlier.attempt, number)
                return done(earlier, pair, f"Pembacaan {pair[0]} dan {pair[1]} konsisten")

    used = [r.attempt for r in readings]
    if not readings:
        message = f"Tidak ada keluaran yang sesuai skema dalam {MAX_READINGS} percobaan."
        problems: tuple[str, ...] = ("skema: keluaran tidak sesuai skema",)
    else:
        message = f"Pembacaan {', '.join(map(str, used))} tidak konsisten pada field bermasalah."
        problems = tuple(problem_text(p) for p in focus)
    return failed(
        ExtractionSource.LLM,
        message,
        fields=tuple(dict.fromkeys(p.path for p in focus)),
        problems=problems,
        attempts=tuple(attempts),
        fragments=_fragments(readings, layer),
    )
