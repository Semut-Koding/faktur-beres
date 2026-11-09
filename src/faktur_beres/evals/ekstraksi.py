"""``npm run eval:ekstraksi``: extraction accuracy per field and the extraction → rules chain.

Groups: ``faktur`` (the 40 invoices), ``balasan`` (the 11 reply attachments) and ``ketahanan`` (the
robustness set). Per document:

1. ``extract_invoice`` reads the file (UBL by code, PDF and images by Gemini with BR-04);
2. fragments: those of the extraction, plus the e-mail of the label for the e-mail channel (or the
   reply text for a reply), exactly what the agent receives;
3. layer 2 of BR-15, unless a strong pattern already decides or ``--tanpa-penilai`` is given;
4. ``analyse()`` with the dataset reader and the same intake context as the oracle;
5. comparison with the truth (field by field), with the oracle (the rules on the truth), and with
   the expectation of the label. A reply attachment is compared with the oracle's analysis of the
   same reply truth.

Field rules: money and quantities as exact ``Decimal``; dates equal; invoice number, PO number,
NPWP, NSFP, account and product code after their own normalisation; names, descriptions,
addresses, banks and units by ``normalize_name``; e-mail by ``normalize_email``; every note of the
truth must occur in the notes and other texts read; lines are paired in order, and a missing or
extra line is wrong. Critical fields are the ones the rules use.

Runs are written after every document to ``eval/hasil/ekstraksi/<run>.json``, so ``--lanjut RUN``
continues a run cut short by quota (``LlmUnavailable``) or by ``--maks-panggilan``. The cache is off
unless ``--pakai-cache``; answers from the cache are marked. ``--banding`` runs the fixed
10-document subset on one model (``--model``), fresh and without the reviewer.
"""

import argparse
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from faktur_beres.config import REPO_ROOT, Settings, get_settings
from faktur_beres.dataset.loader import load_dataset
from faktur_beres.domain.engine import analyse
from faktur_beres.domain.erp import ErpReader
from faktur_beres.domain.invoice import IntakeContext, InvoiceData, TextFragment, TextSource
from faktur_beres.domain.normalize import (
    account_digits,
    normalize_code,
    normalize_email,
    normalize_invoice_number,
    normalize_name,
    normalize_po_number,
    tax_id_digits,
)
from faktur_beres.domain.policy import PolicyConfig, load_policy
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.injection import (
    clean_text,
    document_fragments,
    has_strong_pattern,
    judge_injection,
)
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.ketahanan import robustness_intake
from faktur_beres.evals.labels import (
    email_fragments,
    label_intake,
    label_invoice_id,
    load_labels,
    load_robustness_labels,
    reply_intake,
    truth_fragments,
    truth_to_invoice,
)
from faktur_beres.evals.paths import PathRefused, checked_path
from faktur_beres.extraction.outcome import ExtractionOutcome
from faktur_beres.generator.label_schema import DocumentTruth
from faktur_beres.llm.contracts import (
    CacheUse,
    LlmCallRecord,
    LlmConfigError,
    LlmUnavailable,
    StructuredLlm,
)
from faktur_beres.llm.cost import total_cost
from faktur_beres.llm.gemini import gemini_client
from faktur_beres.tools.extract_invoice import extract_invoice
from faktur_beres.tools.injection_review import review_injection

Group = Literal["faktur", "balasan", "ketahanan"]
GROUPS: tuple[Group, ...] = ("faktur", "balasan", "ketahanan")
RUN_DIR = REPO_ROOT / "eval" / "hasil" / "ekstraksi"
COMPARISON_SUBSET = (
    "DUP-05",
    "HRG-01",
    "DUP-03",
    "HRG-05",
    "COC-01",
    "COC-04",
    "COC-02",
    "DUP-01",
    "HRG-04/balasan",
    "HRG-03/balasan",
)
CRITICAL = frozenset(
    {
        "nomor_faktur",
        "tanggal_faktur",
        "nomor_po",
        "penjual.nama",
        "penjual.npwp",
        "penjual.email",
        "pembeli.npwp",
        "nsfp",
        "rekening.nomor",
        "rekening.bank",
        "baris.kode",
        "baris.deskripsi",
        "baris.qty",
        "baris.satuan",
        "baris.harga_satuan",
        "baris.jumlah",
        "subtotal",
        "dpp_nilai_lain",
        "ppn",
        "total",
    }
)
FREE_TEXT_SOURCES = frozenset({TextSource.NOTE, TextSource.READ_TEXT, TextSource.UBL_NOTE})


# -- the documents of the evaluation -----------------------------------------------------------


@dataclass(frozen=True)
class EvalDocument:
    id: str  # "COC-01", "HRG-03/balasan", "KTH-01"
    group: Group
    path: str  # relative to the repository
    format: str  # PDF teks, PDF pindaian, foto, gambar pindaian, UBL
    truth: DocumentTruth
    intake: IntakeContext
    extra_fragments: tuple[TextFragment, ...]  # e-mail or reply text the agent also receives
    document_format: str  # label format, for truth fragments: pdf, gambar, ubl
    expected: tuple[str, frozenset[str], frozenset[str]] | None  # route, required, allowed
    injection_expected: bool
    waiting: str | None = None


def _format(label_format: str, image_style: str | None, path: str) -> str:
    if path.endswith(".xml") or label_format == "ubl":
        return "UBL"
    if path.endswith(".pdf"):
        return "PDF pindaian" if image_style == "pindaian" else "PDF teks"
    return "foto" if image_style == "foto" else "gambar pindaian"


def documents() -> list[EvalDocument]:
    found: list[EvalDocument] = []
    for sid, label in load_labels().items():
        h = label.harapan
        found.append(
            EvalDocument(
                id=sid,
                group="faktur",
                path=label.berkas.path,
                format=_format(label.format, label.gaya_gambar, label.berkas.path),
                truth=label.kebenaran_dokumen,
                intake=label_intake(label),
                extra_fragments=tuple(email_fragments(label)),
                document_format=label.format,
                expected=(
                    h.jalur_awal,
                    frozenset(h.alasan_wajib),
                    frozenset(h.alasan_boleh_tambahan),
                ),
                injection_expected=sid.startswith("INJ-"),
            )
        )
    for sid, label in load_labels().items():
        reply = label.simulasi.balasan_vendor
        if reply is None or reply.berkas is None or reply.kebenaran_dokumen is None:
            continue
        found.append(
            EvalDocument(
                id=f"{sid}/balasan",
                group="balasan",
                path=reply.berkas.path,
                format=_format(label.format, label.gaya_gambar, reply.berkas.path),
                truth=reply.kebenaran_dokumen,
                intake=reply_intake(reply, invoice_id=label_invoice_id(label)),
                extra_fragments=(
                    TextFragment(source=TextSource.VENDOR_REPLY, text=reply.isi, where="balasan"),
                ),
                document_format=label.format,
                expected=None,
                injection_expected=False,
            )
        )
    for kid, robust in load_robustness_labels().items():
        h2 = robust.harapan
        found.append(
            EvalDocument(
                id=kid,
                group="ketahanan",
                path=robust.berkas.path,
                format=_format(robust.format, robust.gaya_gambar, robust.berkas.path),
                truth=robust.kebenaran_dokumen,
                intake=robustness_intake(robust),
                extra_fragments=(),
                document_format=robust.format,
                expected=(
                    h2.jalur_awal,
                    frozenset(h2.alasan_wajib),
                    frozenset(h2.alasan_boleh_tambahan),
                ),
                injection_expected="prompt_injection" in h2.alasan_wajib,
                waiting=h2.menunggu,
            )
        )
    return found


# -- field comparison --------------------------------------------------------------------------


class FieldCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str  # e.g. "baris[2].jumlah"
    key: str  # e.g. "baris.jumlah", for the metrics
    critical: bool
    ok: bool
    truth: str | None
    read: str | None


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _same(truth: object, read: object, key: Callable[[Any], object]) -> bool:
    if truth is None or read is None:
        return truth is None and read is None
    return key(truth) == key(read)


def _identity(value: Any) -> object:
    return value


def _tax_id(value: str) -> object:
    return tax_id_digits(value) or "".join(value.split())


def compare_fields(truth: InvoiceData, read: InvoiceData | None) -> list[FieldCheck]:
    """Every field of the truth against the reading (all wrong when nothing was read)."""
    checks: list[FieldCheck] = []

    def add(path: str, t: object, r: object, key: Callable[[Any], object]) -> None:
        name = path.split("[")[0] + (path.split("]")[1] if "]" in path else "")
        ok = read is not None and _same(t, r, key)
        checks.append(
            FieldCheck(
                path=path, key=name, critical=name in CRITICAL, ok=ok, truth=_text(t), read=_text(r)
            )
        )

    r = read
    add("nomor_faktur", truth.invoice_number, r and r.invoice_number, normalize_invoice_number)
    add("tanggal_faktur", truth.invoice_date, r and r.invoice_date, _identity)
    add("jatuh_tempo", truth.due_date, r and r.due_date, _identity)
    add("nomor_po", truth.po_number, r and r.po_number, normalize_po_number)
    for prefix, t_party, r_party in (
        ("penjual", truth.seller, r and r.seller),
        ("pembeli", truth.buyer, r and r.buyer),
    ):
        add(f"{prefix}.nama", t_party and t_party.name, r_party and r_party.name, normalize_name)
        add(f"{prefix}.npwp", t_party and t_party.npwp, r_party and r_party.npwp, _tax_id)
        add(
            f"{prefix}.alamat",
            t_party and t_party.address,
            r_party and r_party.address,
            normalize_name,
        )
        add(
            f"{prefix}.email", t_party and t_party.email, r_party and r_party.email, normalize_email
        )
    add("nsfp", truth.nsfp, r and r.nsfp, _tax_id)
    add("tanggal_faktur_pajak", truth.tax_invoice_date, r and r.tax_invoice_date, _identity)
    t_bank, r_bank = truth.bank_account, r and r.bank_account
    add("rekening.bank", t_bank and t_bank.bank, r_bank and r_bank.bank, normalize_name)
    add("rekening.nomor", t_bank and t_bank.number, r_bank and r_bank.number, account_digits)
    add("rekening.atas_nama", t_bank and t_bank.holder, r_bank and r_bank.holder, normalize_name)
    read_lines = r.lines if r is not None else ()
    for i in range(max(len(truth.lines), len(read_lines))):
        t_line = truth.lines[i] if i < len(truth.lines) else None
        r_line = read_lines[i] if i < len(read_lines) else None
        at = f"baris[{i + 1}]"
        for field, attribute, key in (
            ("kode", "vendor_code", normalize_code),
            ("deskripsi", "description", normalize_name),
            ("qty", "quantity", _identity),
            ("satuan", "unit", normalize_name),
            ("harga_satuan", "unit_price", _identity),
            ("jumlah", "amount", _identity),
        ):
            t_value = getattr(t_line, attribute) if t_line else None
            r_value = getattr(r_line, attribute) if r_line else None
            if t_line is None or r_line is None:  # a missing or extra line is wrong
                checks.append(
                    FieldCheck(
                        path=f"{at}.{field}",
                        key=f"baris.{field}",
                        critical=f"baris.{field}" in CRITICAL,
                        ok=False,
                        truth=_text(t_value),
                        read=_text(r_value),
                    )
                )
            else:
                add(f"{at}.{field}", t_value, r_value, key)
    add("subtotal", truth.subtotal, r and r.subtotal, _identity)
    add("dpp_nilai_lain", truth.dpp_nilai_lain, r and r.dpp_nilai_lain, _identity)
    add("ppn", truth.vat, r and r.vat, _identity)
    add("total", truth.total, r and r.total, _identity)
    return checks


def compare_notes(truth_notes: Sequence[str], free_texts: Sequence[str]) -> list[FieldCheck]:
    """Every note of the truth must occur in the notes and other texts read."""
    joined = normalize_name(" ".join(free_texts))
    return [
        FieldCheck(
            path=f"catatan[{i}]",
            key="catatan",
            critical=False,
            ok=normalize_name(note) in joined,
            truth=note,
            read=None,
        )
        for i, note in enumerate(truth_notes, start=1)
    ]


# -- one document ----------------------------------------------------------------------------


class DocResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    group: Group
    format: str
    status: str  # ok, validation_failed
    source: str  # ubl, llm, rejected
    readings: int
    rereads: int
    used_readings: tuple[int, ...]
    fields: tuple[FieldCheck, ...]
    review_status: str | None
    chain_route: str
    chain_reasons: tuple[str, ...]
    oracle_route: str
    oracle_reasons: tuple[str, ...]
    chain_matches_oracle: bool
    chain_matches_expected: bool | None
    injection_expected: bool
    chain_c5: bool
    waiting: str | None
    calls: int
    cache_hits: int
    calls_per_model: dict[str, int]
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cost_usd: str
    llm_ms: int
    wall_ms: int

    @property
    def critical_ok(self) -> bool:
        return all(f.ok for f in self.fields if f.critical)


def _call_stats(calls: Sequence[LlmCallRecord]) -> dict[str, Any]:
    per_model = Counter(c.model for c in calls if c.cache is not CacheUse.HIT)
    return {
        "calls": sum(c.cache is not CacheUse.HIT for c in calls),
        "cache_hits": sum(c.cache is CacheUse.HIT for c in calls),
        "calls_per_model": dict(sorted(per_model.items())),
        "input_tokens": sum(c.input_tokens or 0 for c in calls),
        "output_tokens": sum(c.output_tokens or 0 for c in calls),
        "thinking_tokens": sum(c.thinking_tokens or 0 for c in calls),
        "cost_usd": str(total_cost(calls)),
        "llm_ms": sum(c.duration_ms for c in calls),
    }


def _expected_ok(doc: EvalDocument, route: str, reasons: Iterable[str]) -> bool | None:
    if doc.expected is None or doc.waiting is not None:
        return None
    expected_route, required, allowed = doc.expected
    actual = set(reasons)
    return route == expected_route and required <= actual <= required | allowed


def evaluate_document(
    doc: EvalDocument,
    *,
    llm: StructuredLlm,
    reader: ErpReader,
    policy: PolicyConfig,
    review: bool,
) -> DocResult:
    """Steps 1–5 for one document; ``LlmUnavailable`` and ``LlmConfigError`` reach the caller."""
    started = time.monotonic_ns()
    path = checked_path(REPO_ROOT / doc.path, inside_repo=True)
    outcome: ExtractionOutcome = extract_invoice(path.read_bytes(), llm=llm, policy=policy)
    calls = list(outcome.calls)
    invoice = outcome.invoice
    fragments = (*outcome.fragments, *doc.extra_fragments)
    merged = document_fragments(invoice, fragments)
    review_result = None
    if review and not has_strong_pattern(merged):
        reviewed = review_injection(merged, llm=llm)
        calls += reviewed.calls
        review_result = reviewed.review
    if invoice is not None:
        result = analyse(
            invoice, doc.intake, fragments, reader, policy, injection_review=review_result
        )
        chain_route = result.default_route.value
        chain_reasons = tuple(r.value for r in result.reasons)
    else:
        judged = judge_injection(merged, review_result)
        chain_route = "C"
        chain_reasons = (ReasonCode.VALIDATION_FAILED.value,)
        if any(f.reason is ReasonCode.PROMPT_INJECTION for f in judged.findings):
            chain_reasons += (ReasonCode.PROMPT_INJECTION.value,)
    truth_invoice = truth_to_invoice(doc.truth)
    oracle = analyse(
        truth_invoice,
        doc.intake,
        [*truth_fragments(doc.truth, document_format=doc.document_format), *doc.extra_fragments],
        reader,
        policy,
    )
    oracle_reasons = tuple(r.value for r in oracle.reasons)
    free_texts = [clean_text(f.text) for f in outcome.fragments if f.source in FREE_TEXT_SOURCES]
    if invoice is not None:
        free_texts += list(invoice.notes)
    checks = compare_fields(truth_invoice, invoice) + compare_notes(doc.truth.catatan, free_texts)
    return DocResult(
        id=doc.id,
        group=doc.group,
        format=doc.format,
        status=outcome.status.value,
        source=outcome.source.value,
        readings=len(outcome.attempts),
        rereads=max(len(outcome.attempts) - 1, 0),
        used_readings=outcome.used_readings,
        fields=tuple(checks),
        review_status=review_result.status.value if review_result else None,
        chain_route=chain_route,
        chain_reasons=chain_reasons,
        oracle_route=oracle.default_route.value,
        oracle_reasons=oracle_reasons,
        chain_matches_oracle=(
            chain_route == oracle.default_route.value and set(chain_reasons) == set(oracle_reasons)
        ),
        chain_matches_expected=_expected_ok(doc, chain_route, chain_reasons),
        injection_expected=doc.injection_expected,
        chain_c5=ReasonCode.PROMPT_INJECTION.value in chain_reasons,
        waiting=doc.waiting,
        wall_ms=(time.monotonic_ns() - started) // 1_000_000,
        **_call_stats(calls),
    )


# -- runs --------------------------------------------------------------------------------------


class RunState(BaseModel):
    run: str
    started: datetime
    updated: datetime
    mode: Literal["penuh", "banding"]
    models: dict[str, str]  # role -> model id
    prompt_versions: dict[str, str]
    review: bool
    cache: bool
    groups: tuple[Group, ...]
    ids: tuple[str, ...] = ()
    documents: list[DocResult] = Field(default_factory=list)
    stopped: str | None = None  # why the run stopped early, if it did
    summary: dict[str, Any] = Field(default_factory=dict)


def run_path(run: str) -> Path:
    return RUN_DIR / f"{run}.json"


def save(state: RunState, path: Path) -> None:
    state.updated = datetime.now(UTC)
    state.summary = summarize(state.documents)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def select(
    all_documents: Sequence[EvalDocument], groups: Sequence[Group], ids: Sequence[str]
) -> list[EvalDocument]:
    chosen = [d for d in all_documents if d.group in groups]
    if ids:
        wanted = set(ids)
        chosen = [d for d in all_documents if d.id in wanted]
        missing = wanted - {d.id for d in chosen}
        if missing:
            raise ValueError(f"dokumen tidak dikenal: {', '.join(sorted(missing))}")
    return chosen


def run_documents(
    state: RunState,
    docs: Sequence[EvalDocument],
    *,
    llm: StructuredLlm,
    reader: ErpReader,
    policy: PolicyConfig,
    path: Path,
    max_calls: int | None = None,
    echo: Callable[[str], None] = print,
) -> RunState:
    """Evaluate the documents not yet in ``state``, saving after each one."""
    done = {d.id for d in state.documents}
    calls = 0
    for doc in docs:
        if doc.id in done:
            continue
        if max_calls is not None and calls >= max_calls:
            state.stopped = f"batas {max_calls} panggilan tercapai"
            break
        try:
            result = evaluate_document(
                doc, llm=llm, reader=reader, policy=policy, review=state.review
            )
        except LlmUnavailable as exc:
            state.stopped = f"LLM tidak tersedia pada {doc.id}: {exc}"
            break
        state.documents.append(result)
        calls += result.calls
        save(state, path)
        mark = "ok" if result.critical_ok else "SALAH"
        echo(
            f"{result.id:<16} {result.format:<16} baca {result.readings} "
            f"panggilan {result.calls:<2} "
            f"kritis {mark:<5} rantai={'orakel' if result.chain_matches_oracle else 'BEDA'} "
            f"{result.chain_route} {','.join(result.chain_reasons) or '-'}"
        )
    else:
        state.stopped = None
    save(state, path)
    return state


# -- metrics -----------------------------------------------------------------------------------


def _percent(part: int, whole: int) -> str:
    return f"{Decimal(part * 100) / Decimal(whole):.1f}%" if whole else "-"


def _quantile(values: Sequence[int], q: Decimal) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int((Decimal(len(ordered) - 1) * q).to_integral_value())
    return ordered[index]


def summarize_group(results: Sequence[DocResult]) -> dict[str, Any]:
    fields: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    critical = [0, 0]
    for result in results:
        for check in result.fields:
            fields[check.key][0] += check.ok
            fields[check.key][1] += 1
            if check.critical:
                critical[0] += check.ok
                critical[1] += 1
    judged = [r for r in results if r.chain_matches_expected is not None]
    injection = [r for r in results if r.injection_expected]
    others = [r for r in results if not r.injection_expected]
    llm_ms = [r.llm_ms for r in results if r.calls]
    per_model: Counter[str] = Counter()
    for result in results:
        per_model.update(result.calls_per_model)
    costs = [Decimal(r.cost_usd) for r in results if r.cost_usd != "tidak diketahui"]
    return {
        "dokumen": len(results),
        "validation_failed": sum(r.status == "validation_failed" for r in results),
        "akurasi_field_kritis": _percent(critical[0], critical[1]),
        "field_kritis_benar": f"{critical[0]}/{critical[1]}",
        "dokumen_sempurna": f"{sum(r.critical_ok for r in results)}/{len(results)}",
        "akurasi_per_field": {
            key: f"{ok}/{total} ({_percent(ok, total)})"
            for key, (ok, total) in sorted(fields.items())
        },
        "br04": {
            "pembacaan_per_dokumen": str(
                Decimal(sum(r.readings for r in results)) / Decimal(len(results))
                if results
                else Decimal(0)
            ),
            "dokumen_dibaca_ulang": sum(r.rereads > 0 for r in results),
            "hasil": dict(Counter(f"{r.status}:{r.used_readings}" for r in results if r.rereads)),
        },
        "rantai_sama_orakel": f"{sum(r.chain_matches_oracle for r in results)}/{len(results)}",
        "rantai_sama_harapan": (
            f"{sum(bool(r.chain_matches_expected) for r in judged)}/{len(judged)}"
        ),
        "c5_pada_injeksi": f"{sum(r.chain_c5 for r in injection)}/{len(injection)}",
        "c5_di_dokumen_lain": sum(r.chain_c5 for r in others),
        "token": {
            "masukan": sum(r.input_tokens for r in results),
            "keluaran": sum(r.output_tokens for r in results),
            "thinking": sum(r.thinking_tokens for r in results),
        },
        "biaya_setara_usd": str(sum(costs, Decimal(0))),
        "biaya_tidak_diketahui": sum(r.cost_usd == "tidak diketahui" for r in results),
        "latensi_llm_ms": {
            "median": int(statistics.median(llm_ms)) if llm_ms else None,
            "p95": _quantile(llm_ms, Decimal("0.95")),
        },
        "panggilan_per_model": dict(sorted(per_model.items())),
        "dari_cache": sum(r.cache_hits for r in results),
    }


def summarize(results: Sequence[DocResult]) -> dict[str, Any]:
    by_group = {g: [r for r in results if r.group == g] for g in GROUPS}
    by_format: dict[str, list[DocResult]] = defaultdict(list)
    for result in results:
        by_format[result.format].append(result)
    errors = [
        {"dokumen": r.id, "field": c.path, "kebenaran": c.truth, "hasil_baca": c.read}
        for r in results
        for c in r.fields
        if not c.ok
    ]
    return {
        "per_kelompok": {g: summarize_group(rs) for g, rs in by_group.items() if rs},
        "per_format": {f: summarize_group(rs) for f, rs in sorted(by_format.items())},
        "kesalahan": errors,
    }


def print_summary(state: RunState, echo: Callable[[str], None] = print) -> None:
    summary = summarize(state.documents)
    for title, groups in (("kelompok", summary["per_kelompok"]), ("format", summary["per_format"])):
        echo(f"\nPer {title}:")
        for name, g in groups.items():
            echo(
                f"  {name:<16} dok {g['dokumen']:<3} kritis {g['akurasi_field_kritis']:<7} "
                f"sempurna {g['dokumen_sempurna']:<6} orakel {g['rantai_sama_orakel']:<6} "
                f"harapan {g['rantai_sama_harapan']:<6} C5 inj {g['c5_pada_injeksi']:<4} "
                f"C5 lain {g['c5_di_dokumen_lain']:<2} biaya USD {g['biaya_setara_usd']}"
            )
    echo(f"\nKesalahan field: {len(summary['kesalahan'])}")
    for error in summary["kesalahan"][:40]:
        echo(
            f"  {error['dokumen']:<16} {error['field']:<24} "
            f"{error['kebenaran']!r} → {error['hasil_baca']!r}"
        )
    if state.stopped:
        echo(f"\nRun terhenti: {state.stopped}. Lanjutkan dengan --lanjut {state.run}")


# -- command line ------------------------------------------------------------------------------


def _models(settings: Settings, choice: str | None) -> tuple[dict[str, str], str | None]:
    if choice is None:
        return {"utama": settings.gemini_model_primary, "kuat": settings.gemini_model_strong}, None
    model = {"utama": settings.gemini_model_primary, "kuat": settings.gemini_model_strong}.get(
        choice, choice
    )
    return {"utama": model, "kuat": model}, model


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="npm run eval:ekstraksi", description=__doc__)
    parser.add_argument("--set", action="append", choices=GROUPS, help="kelompok (bisa berulang)")
    parser.add_argument("--id", action="append", default=[], help="dokumen (bisa berulang)")
    parser.add_argument("--pakai-cache", action="store_true", help="pakai cache jawaban LLM")
    parser.add_argument("--lanjut", metavar="RUN", help="lanjutkan run yang terputus")
    parser.add_argument(
        "--maks-panggilan", type=int, metavar="N", help="berhenti setelah N panggilan"
    )
    parser.add_argument("--model", help="utama, kuat, atau ID model; dipakai untuk semua peran")
    parser.add_argument("--banding", action="store_true", help="uji banding 10 dokumen, satu model")
    parser.add_argument("--tanpa-penilai", action="store_true", help="tanpa lapis 2 BR-15")
    args = parser.parse_args(argv)
    settings = get_settings()
    reader, policy = DatasetErpReader(load_dataset()), load_policy()
    all_docs = documents()

    if args.lanjut:
        path = run_path(args.lanjut)
        state = RunState.model_validate_json(path.read_text(encoding="utf-8"))
        only = state.models["utama"] if state.mode == "banding" else None
    else:
        if args.banding and args.pakai_cache:
            print("--banding selalu segar; --pakai-cache tidak dipakai", file=sys.stderr)
            return 2
        models, only = _models(settings, args.model)
        groups = tuple(args.set or GROUPS)
        ids = COMPARISON_SUBSET if args.banding else tuple(args.id)
        now = datetime.now(UTC)
        name = f"{now:%Y%m%d-%H%M%S}" + (f"-banding-{only}" if args.banding else "")
        state = RunState(
            run=name,
            started=now,
            updated=now,
            mode="banding" if args.banding else "penuh",
            models=models,
            prompt_versions={"ekstraksi": "extraction-v1", "penilai": "injection-review-v1"},
            review=not (args.tanpa_penilai or args.banding),
            cache=args.pakai_cache and not args.banding,
            groups=groups,
            ids=ids,
        )
        path = run_path(name)
    try:
        docs = select(all_docs, state.groups, state.ids)
        for doc in docs:
            checked_path(REPO_ROOT / doc.path, inside_repo=True)
        llm = gemini_client(settings, use_cache=state.cache, only_model=only)
    except (PathRefused, ValueError, LlmConfigError) as exc:
        print(f"GALAT: {exc}", file=sys.stderr)
        return 2
    print(
        f"run {state.run}: {len(docs)} dokumen, model {state.models}, "
        f"penilai {'ya' if state.review else 'tidak'}, cache {'ya' if state.cache else 'tidak'}"
    )
    try:
        run_documents(
            state, docs, llm=llm, reader=reader, policy=policy, path=path,
            max_calls=args.maks_panggilan,
        )  # fmt: skip
    except LlmConfigError as exc:
        print(f"GALAT konfigurasi LLM: {exc}", file=sys.stderr)
        return 2
    print_summary(state)
    print(f"\nhasil: {path.relative_to(REPO_ROOT)}")
    return 3 if state.stopped else 0


if __name__ == "__main__":
    sys.exit(main())
