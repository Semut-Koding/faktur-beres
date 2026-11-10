"""``npm run eval:graph`` (SPEC §15.4, M4a.8): the live graph against every label.

For each scenario: reset Odoo, the application database and Mailpit; send the labelled file through
the real intake; run until the agent first stops and score that; then, where the label has an
approver's decision, send it (twice for the idempotency test), run on, and score the final state.
Scenarios that wait for a vendor reply are marked "menunggu M4b".

The harness gives the agent only the document and the simulated decisions. ``harapan``, category,
variant and scenario id are read here, for scoring, and go nowhere else.

``--ekstraksi orakel`` feeds the agent the document truth instead of reading the file, also for
attached corrections, recognised by their hash; the planner, the line mapping, the reply reader and
the route choice still use the LLM. ``--ekstraksi llm`` is the whole agent. Scenarios of the e-mail
channel and every vendor reply go through SMTP and the real inbox; ``--intake langsung`` uses
``Runner.accept`` for the invoices instead. ``--tanpa-llm`` also turns off the route choice and
reads text replies with a pattern. Before starting, the policy index must exist and match.
"""

import argparse
import hashlib
import math
import mimetypes
import os
import shutil
import smtplib
import statistics
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from faktur_beres.config import REPO_ROOT, Settings, get_settings
from faktur_beres.dataset import Dataset, load_dataset
from faktur_beres.db.connection import app_url, make_engine
from faktur_beres.db.migrate import reset as reset_database
from faktur_beres.db.models import AuditKind, Decision
from faktur_beres.db.policy_store import PolicyStore
from faktur_beres.db.store import Store
from faktur_beres.domain.engine import AnalysisResult
from faktur_beres.domain.erp import BillRecord
from faktur_beres.domain.invoice import Channel, InvoiceData, TextFragment
from faktur_beres.domain.normalize import normalize_email, tax_id_digits
from faktur_beres.domain.reasons import InvoiceStatus, ReasonCode, Route
from faktur_beres.evals.labels import (
    load_labels,
    load_robustness_labels,
    truth_fragments,
    truth_to_invoice,
)
from faktur_beres.evals.orakel import (
    PO_TOKEN,
    Check,
    Outcome,
    PlannedBill,
    PlannedLine,
    first_stop_checks,
    score,
)
from faktur_beres.extraction.outcome import ExtractionOutcome, ExtractionSource, ExtractionStatus
from faktur_beres.extraction.reading import PROMPT_VERSION as EXTRACTION_PROMPT
from faktur_beres.generator.label_schema import Label, RobustnessLabel, VendorReply
from faktur_beres.graph.build import build_graph
from faktur_beres.graph.deps import Dependencies, FileStore
from faktur_beres.graph.factory import checkpointer, live_dependencies
from faktur_beres.graph.inbox import AP_ADDRESS, InboxPoller
from faktur_beres.graph.runner import Runner, Stop
from faktur_beres.llm import usage
from faktur_beres.llm.cache import LlmCache
from faktur_beres.llm.embedding import gemini_embedder
from faktur_beres.llm.gemini import cache_dir, gemini_client
from faktur_beres.tools import odoo_tools
from faktur_beres.tools.inbox import MailpitInbox
from faktur_beres.tools.odoo_gateway import AgentOdooGateway
from faktur_beres.tools.policy_rag import index_problem
from faktur_beres.tools.reply_reading import ReplyReading

RUN_DIR = REPO_ROOT / "eval" / "hasil" / "graph"
FILES_ROOT = REPO_ROOT / "var" / "evaluasi-berkas"
INTERNAL = "arunika.test"
DECISIONS = {"setujui": Decision.APPROVE, "tolak": Decision.REJECT}
LIMIT = 1000
Group = Literal["faktur", "ketahanan"]
MASTER_MODELS: tuple[tuple[str, list[Any], tuple[str, ...]], ...] = (
    ("res.partner", [["supplier_rank", ">", 0]], ("id", "name", "vat", "email")),
    ("res.partner.bank", [], ("id", "partner_id", "acc_number", "bank_id")),
    ("product.product", [], ("id", "name", "default_code")),
    ("purchase.order", [], ("id", "name", "state", "partner_id")),
    (
        "purchase.order.line",
        [],
        ("id", "order_id", "product_id", "product_qty", "qty_received", "price_unit"),
    ),
)


class Result(BaseModel):
    id: str
    group: Group
    category: str
    format: str
    channel: str
    invoice_id: str
    route: str | None
    reasons: list[str]
    status_first: str
    status_final: str
    message: str | None
    first_stop_passed: bool
    final_passed: bool
    checks: list[dict[str, Any]]
    seconds_to_route: float | None
    calls_per_model: dict[str, int]
    calls_per_purpose: dict[str, int]
    cost_usd: Decimal
    planner: dict[str, Any] | None = None
    mapped_lines: int = 0
    replies: int = 0
    llm_route_choices: int = 0
    escalations: int = 0
    guard_refusals: int = 0
    trace: list[str] = Field(default_factory=list)


class RunState(BaseModel):
    run: str
    started: datetime
    updated: datetime
    extraction: Literal["orakel", "llm"]
    reviewer: bool
    llm: bool
    cache: bool
    intake: Literal["kanal", "langsung"] = "kanal"
    prompt: str
    groups: tuple[Group, ...]
    ids: tuple[str, ...] = ()
    results: list[Result] = Field(default_factory=list)
    stopped: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class Case:
    id: str
    group: Group
    label: Label | RobustnessLabel

    @property
    def category(self) -> str:
        return self.label.kategori if isinstance(self.label, Label) else "ketahanan"

    @property
    def channel(self) -> str:
        return self.label.kanal

    @property
    def format(self) -> str:
        if self.label.format == "gambar" and self.label.gaya_gambar:
            return self.label.gaya_gambar
        return self.label.format


def cases(groups: Sequence[Group], ids: Sequence[str]) -> list[Case]:
    found: list[Case] = []
    if "faktur" in groups:
        found += [Case(k, "faktur", v) for k, v in sorted(load_labels().items())]
    if "ketahanan" in groups:
        found += [Case(k, "ketahanan", v) for k, v in sorted(load_robustness_labels().items())]
    if ids:
        wanted = set(ids)
        unknown = wanted - {c.id for c in found}
        if unknown:
            raise ValueError(f"skenario tidak dikenal: {', '.join(sorted(unknown))}")
        found = [c for c in found if c.id in wanted]
    return found


def oracle_extractor(found: Sequence[Case]) -> Any:
    """The extractor of ``--ekstraksi orakel``: the document truth, by file hash."""
    by_hash: dict[str, tuple[InvoiceData, list[TextFragment]]] = {}

    def add(sha256: str, truth: Any, document_format: str) -> None:
        by_hash[sha256] = (
            truth_to_invoice(truth),
            truth_fragments(truth, document_format=document_format),
        )

    for case in found:
        add(case.label.berkas.sha256, case.label.kebenaran_dokumen, case.label.format)
        reply = case.label.simulasi.balasan_vendor if isinstance(case.label, Label) else None
        if reply is not None and reply.berkas is not None and reply.kebenaran_dokumen is not None:
            add(reply.berkas.sha256, reply.kebenaran_dokumen, case.label.format)

    def extract(data: bytes) -> ExtractionOutcome:
        invoice, fragments = by_hash[hashlib.sha256(data).hexdigest()]
        return ExtractionOutcome(
            status=ExtractionStatus.OK,
            source=ExtractionSource.LLM,
            invoice=invoice,
            fragments=tuple(fragments),
        )

    return extract


class Observer:
    """Reads what happened in Odoo and Mailpit, with the agent's own read rights."""

    def __init__(self, gateway: AgentOdooGateway, settings: Settings) -> None:
        self.gateway = gateway
        self.api = settings.mailpit_url.rstrip("/")

    def bill_states(self) -> dict[int, str]:
        rows = self.gateway.search_read(
            "account.move", [["move_type", "=", "in_invoice"]], ("id", "state"), limit=LIMIT
        )
        return {row["id"]: row["state"] for row in rows}

    def master(self) -> dict[str, Any]:
        return {
            model: sorted(
                self.gateway.search_read(model, domain, fields, limit=LIMIT), key=lambda r: r["id"]
            )
            for model, domain, fields in MASTER_MODELS
        }

    def outgoing(self) -> Counter[str]:
        response = httpx.get(f"{self.api}/api/v1/messages", params={"limit": 200}, timeout=20)
        response.raise_for_status()
        sent: Counter[str] = Counter()
        for message in response.json().get("messages") or []:
            for to in message.get("To") or []:
                address = normalize_email(to.get("Address") or "")
                if address and not address.endswith(f"@{INTERNAL}"):
                    sent[address] += 1
        return sent

    def agent_email_to(self, address: str) -> tuple[str, str]:
        """Subject and Message-ID of the newest e-mail the agent sent to ``address``."""
        response = httpx.get(f"{self.api}/api/v1/messages", params={"limit": 200}, timeout=20)
        response.raise_for_status()
        wanted = normalize_email(address)
        for message in response.json().get("messages") or []:
            if any(
                normalize_email(to.get("Address") or "") == wanted for to in message["To"] or []
            ):
                return message["Subject"], message["MessageID"]
        raise LookupError("email agent ke vendor tidak ditemukan")

    def new_bills(self, before: dict[int, str]) -> tuple[BillRecord, ...]:
        ids = sorted(set(self.bill_states()) - set(before))
        return odoo_tools.bills_by_ids(self.gateway, ids) if ids else ()


def planned(
    bills: Sequence[BillRecord], analysis: AnalysisResult | None, ds: Dataset
) -> tuple[PlannedBill, ...]:
    """Bills as read from Odoo, in the natural keys of the labels."""
    vendors = {tax_id_digits(v.npwp): v.key for v in ds.vendors}
    products = {p.code: p.key for p in ds.products}
    plan = analysis.bill_plan if analysis else None
    by_line = {line.purchase_line_id: line for line in plan.lines} if plan else {}
    vendor = analysis.vendor.vendor if analysis else None
    result = []
    for bill in bills:
        lines = []
        for line in bill.lines:
            info = by_line.get(line.purchase_line_id or -1)
            label = (
                f"{info.po_number}/{products.get(info.product_code or '', '?')}" if info else "?"
            )
            lines.append(PlannedLine(po_line=label, qty=line.quantity, price=line.price_unit))
        key = vendors.get(tax_id_digits(vendor.vat), "?") if vendor and vendor.vat else "?"
        result.append(
            PlannedBill(
                vendor=key,
                ref=bill.ref or "",
                total=bill.amount_total,
                lines=tuple(lines),
                key=str(bill.id),
            )
        )
    return tuple(result)


def _mime(name: str) -> tuple[str, str]:
    guessed = mimetypes.guess_type(name)[0] or "application/octet-stream"
    main, sub = guessed.split("/", 1)
    return main, sub


def _send(
    settings: Settings,
    *,
    sender: str,
    to: str,
    subject: str,
    body: str,
    message_id: str,
    in_reply_to: str | None = None,
    date: str | None = None,
    attachment: tuple[str, bytes] | None = None,
) -> None:
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = to
    mail["Subject"] = subject
    mail["Message-ID"] = message_id
    if date is not None:
        mail["Date"] = format_datetime(datetime.fromisoformat(date))
    if in_reply_to is not None:
        mail["In-Reply-To"] = in_reply_to
        mail["References"] = in_reply_to
    mail.set_content(body)
    if attachment is not None:
        main, sub = _mime(attachment[0])
        mail.add_attachment(attachment[1], maintype=main, subtype=sub, filename=attachment[0])
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.send_message(mail)


def _reset(url: str, settings: Settings) -> None:
    subprocess.run(
        ["npm", "run", "-s", "odoo:reset"], cwd=REPO_ROOT, check=True, capture_output=True
    )
    reset_database(url)
    httpx.delete(
        f"{settings.mailpit_url.rstrip('/')}/api/v1/messages", timeout=20
    ).raise_for_status()
    shutil.rmtree(FILES_ROOT, ignore_errors=True)


def _llm_summary(store: Store, invoice_id: str) -> tuple[Counter[str], Counter[str], Decimal]:
    models: Counter[str] = Counter()
    purposes: Counter[str] = Counter()
    cost = Decimal(0)
    for entry in store.audit_entries(invoice_id):
        if entry.kind != AuditKind.LLM.value:
            continue
        if entry.model and (entry.params or {}).get("cache") != "hit":
            models[entry.model] += 1
            purposes[entry.name] += 1
        cost += entry.cost_usd or Decimal(0)
    return models, purposes, cost


def _prohibitions(
    before: dict[int, str],
    observer: Observer,
    master: dict[str, Any],
    labels: Sequence[str],
) -> list[Check]:
    """The checks the oracle could not make: posted bills and changed master data."""
    checks: list[Check] = []
    states = observer.bill_states()
    if "tagihan_terposting" in labels:
        changed = [
            i for i, state in states.items() if state == "posted" and before.get(i) != "posted"
        ]
        checks.append(Check(name="tagihan_terposting", ok=not changed, detail=f"{changed}"))
    if "ubah_data_master" in labels:
        same = observer.master() == master
        checks.append(Check(name="ubah_data_master", ok=same, detail="sama" if same else "berubah"))
    return checks


@dataclass(frozen=True)
class Live:
    """What a scenario needs from the run."""

    settings: Settings
    store: Store
    saver: Any
    deps_for: Any
    dataset: Dataset


def _first_invoice(
    case: Case, state: RunState, live: Live, runner: Runner, poller: InboxPoller
) -> str:
    label = case.label
    email = label.email if isinstance(label, Label) else None
    data = (REPO_ROOT / label.berkas.path).read_bytes()
    if email is not None and state.intake == "kanal":
        _send(
            live.settings,
            sender=email.dari,
            to=email.kepada,
            subject=email.subjek,
            body=email.isi,
            message_id=email.message_id,
            date=email.tanggal,
            attachment=(label.berkas.nama_kirim, data),
        )
        handled = poller.poll_once()
        return handled[0].invoice_ids[0]
    invoice_id = runner.accept(
        data,
        label.berkas.nama_kirim,
        channel=Channel.EMAIL if email else Channel.UPLOAD,
        sender_email=normalize_email(email.dari) if email else None,
        subject=email.subjek if email else None,
        body=email.isi if email else None,
    )
    runner.run(invoice_id)
    return invoice_id


def run_case(case: Case, state: RunState, live: Live) -> Result:
    with AgentOdooGateway.from_settings(live.settings) as gateway:
        store = live.store
        deps: Dependencies = live.deps_for(gateway)
        runner = Runner(store, deps.files, build_graph(deps, live.saver))
        poller = InboxPoller(store, MailpitInbox(live.settings.mailpit_url), runner)
        observer = Observer(gateway, live.settings)
        before, master = observer.bill_states(), observer.master()
        invoice_id = _first_invoice(case, state, live, runner, poller)
        first = runner.status(invoice_id)
        row = store.get(invoice_id)
        seconds = (
            (row.route_decided_at - row.received_at).total_seconds()
            if row.route_decided_at
            else None
        )
        first_route = row.route
        bills_before = len(observer.new_bills(before))
        emails_before = observer.outgoing()
        final, replies = _simulate(case, runner, poller, first, invoice_id, observer, live)
        config = {"configurable": {"thread_id": invoice_id}}
        snapshot = runner.graph.get_state(config).values
        raw = snapshot.get("analysis")
        analysis = AnalysisResult.model_validate(raw) if raw else None
        emails_total = observer.outgoing()
        bills = planned(observer.new_bills(before), analysis, live.dataset)
        models, purposes, cost = _llm_summary(store, invoice_id)
        mapped = sum(1 for m in (analysis.lines.matches if analysis else ()) if m.method == "llm")
        outcome = _outcome(
            case, first, final, first_route, bills_before, emails_before, emails_total, bills
        )
        checks, first_ok, final_ok = _score(case, outcome, observer, before, master)
        choices, escalations, refusals = _route_counts(store, invoice_id)
        return Result(
            id=case.id,
            group=case.group,
            category=case.category,
            format=case.format,
            channel=case.channel,
            invoice_id=invoice_id,
            route=first_route,
            reasons=list(first.reasons),
            status_first=first.status.value,
            status_final=final.status.value,
            message=final.message,
            first_stop_passed=first_ok,
            final_passed=final_ok,
            checks=[c.model_dump() for c in checks],
            seconds_to_route=seconds,
            calls_per_model=dict(models),
            calls_per_purpose=dict(purposes),
            cost_usd=cost,
            planner=snapshot.get("planner"),
            mapped_lines=mapped,
            replies=replies,
            llm_route_choices=choices,
            escalations=escalations,
            guard_refusals=refusals,
        )


def _route_counts(store: Store, invoice_id: str) -> tuple[int, int, int]:
    """Route choices by the LLM, escalations among them, and choices the guard refused."""
    choices = escalations = refused = 0
    for entry in store.audit_entries(invoice_id):
        if entry.name == "jalur" and "oleh LLM" in (entry.summary or ""):
            choices += 1
            escalations += (entry.summary or "").startswith("jalur C")
        elif entry.name == "pilihan_jalur_ditolak":
            refused += 1
    return choices, escalations, refused


def _send_reply(case: Case, reply: VendorReply, observer: Observer, live: Live) -> None:
    """The vendor's answer to the agent's e-mail, through SMTP like any other message."""
    subject, message_id = observer.agent_email_to(reply.dari)
    attachment = None
    if reply.jenis == "lampiran" and reply.berkas is not None:
        attachment = (reply.berkas.nama_kirim, (REPO_ROOT / reply.berkas.path).read_bytes())
    digest = hashlib.sha256(f"{case.label.berkas.sha256}{message_id}".encode()).hexdigest()[:24]
    _send(
        live.settings,
        sender=reply.dari,
        to=AP_ADDRESS,
        subject=f"Re: {subject}",
        body=reply.isi,
        message_id=f"<balasan-{digest}@{reply.dari.rsplit('@', 1)[-1]}>",
        in_reply_to=f"<{message_id}>",
        attachment=attachment,
    )


def _simulate(
    case: Case,
    runner: Runner,
    poller: InboxPoller,
    first: Stop,
    invoice_id: str,
    observer: Observer,
    live: Live,
) -> tuple[Stop, int]:
    label = case.label
    if isinstance(label, RobustnessLabel):
        return first, 0
    decision = label.simulasi.keputusan_approver
    if first.status is InvoiceStatus.AWAITING_APPROVAL and decision is not None:
        chosen = DECISIONS[decision.keputusan]
        final = runner.decide(
            invoice_id, chosen, decided_by="simulasi", note=decision.catatan or None
        )
        if label.simulasi.uji_idempotensi:
            final = runner.decide(
                invoice_id, chosen, decided_by="simulasi", note=decision.catatan or None
            )
        return final, 0
    reply = label.simulasi.balasan_vendor
    if reply is not None and first.status is InvoiceStatus.AWAITING_VENDOR:
        _send_reply(case, reply, observer, live)
        poller.poll_once()
        return runner.status(invoice_id), 1
    return first, 0


def _outcome(
    case: Case,
    first: Stop,
    final: Stop,
    route: str | None,
    bills_before: int,
    emails_before: Counter[str],
    emails_total: Counter[str],
    bills: tuple[PlannedBill, ...],
) -> Outcome:
    return Outcome(
        id=case.id,
        initial_route=Route(route) if route else Route.C,
        reasons=tuple(ReasonCode(r) for r in first.reasons),
        initial_status=first.status,
        final_status=final.status,
        bills_before=bills_before,
        emails_before=tuple(sorted(emails_before.items())),
        emails_total=tuple(sorted(emails_total.items())),
        bills=bills,
        steps=(),
    )


def _score(
    case: Case,
    outcome: Outcome,
    observer: Observer,
    before: dict[int, str],
    master: dict[str, Any],
) -> tuple[list[Check], bool, bool]:
    policy = load_policy_cached()
    label = case.label
    if isinstance(label, RobustnessLabel):
        h = label.harapan
        actual = {r.value for r in outcome.reasons}
        required, extra = set(h.alasan_wajib), set(h.alasan_boleh_tambahan)
        checks = [
            Check(
                name="jalur_awal",
                ok=outcome.initial_route.value == h.jalur_awal,
                detail=f"{outcome.initial_route.value} (harapan {h.jalur_awal})",
            ),
            Check(
                name="alasan",
                ok=required <= actual <= required | extra,
                detail=f"{sorted(actual)}",
            ),
        ]
        ok = all(c.ok for c in checks)
        return checks, ok, ok
    stop_checks = first_stop_checks(label, outcome)
    extra_checks = _prohibitions(before, observer, master, label.harapan.larangan)
    scored = score(label, outcome, policy)
    checks = [*scored.checks, *extra_checks]
    return checks, all(c.ok for c in stop_checks), all(c.ok for c in checks)


_POLICY: Any = None


def load_policy_cached() -> Any:
    global _POLICY
    if _POLICY is None:
        from faktur_beres.domain.policy import load_policy

        _POLICY = load_policy()
    return _POLICY


def percentile_95(values: Sequence[float]) -> float:
    """The nearest-rank 95th percentile: with few values it is the largest one."""
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def _pattern_reply_reader(text: str) -> ReplyReading:
    """``--tanpa-llm``: the numbers in the reply text, with the pattern of the oracle."""
    return ReplyReading(tuple(PO_TOKEN.findall(text.upper())))


def timing_row(values: Sequence[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "median_detik": round(statistics.median(values), 1),
        "p95_detik": round(percentile_95(values), 1),
        "maks_detik": round(max(values), 1),
    }


def summarize(results: Sequence[Result]) -> dict[str, Any]:
    def group(key: str) -> dict[str, dict[str, int]]:
        table: dict[str, dict[str, int]] = {}
        for result in results:
            name = getattr(result, key)
            row = table.setdefault(name, {"n": 0, "pertama": 0, "akhir": 0})
            row["n"] += 1
            row["pertama"] += result.first_stop_passed
            row["akhir"] += result.final_passed
        return table

    per_format: dict[str, list[float]] = {}
    for result in results:
        if result.seconds_to_route is not None:
            per_format.setdefault(result.format, []).append(result.seconds_to_route)
    timing = {name: timing_row(values) for name, values in sorted(per_format.items())}
    everything = [value for values in per_format.values() for value in values]
    if everything:
        timing["keseluruhan"] = timing_row(everything)
    models: Counter[str] = Counter()
    purposes: Counter[str] = Counter()
    for result in results:
        models.update(result.calls_per_model)
        purposes.update(result.calls_per_purpose)
    cost = sum((r.cost_usd for r in results), Decimal(0))
    return {
        "jumlah": len(results),
        "pertama_lulus": sum(r.first_stop_passed for r in results),
        "akhir_lulus": sum(r.final_passed for r in results),
        "gagal": [r.id for r in results if not r.first_stop_passed or not r.final_passed],
        "balasan_diproses": sum(r.replies for r in results),
        "pilihan_jalur_llm": sum(r.llm_route_choices for r in results),
        "eskalasi_llm": sum(r.escalations for r in results),
        "pilihan_jalur_ditolak": sum(r.guard_refusals for r in results),
        "panggilan_embedding": sum(r.calls_per_purpose.get("embedding", 0) for r in results),
        "per_kelompok": group("group"),
        "per_kategori": group("category"),
        "per_format": group("format"),
        "per_kanal": group("channel"),
        "waktu_sampai_jalur": timing,
        "panggilan_per_model": dict(sorted(models.items())),
        "panggilan_per_tujuan": dict(sorted(purposes.items())),
        "biaya_usd": str(cost),
        "biaya_per_faktur_usd": str(cost / len(results)) if results else "0",
    }


def save(state: RunState) -> None:
    state.updated = datetime.now(UTC)
    state.summary = summarize(state.results)
    path = RUN_DIR / f"{state.run}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def print_result(result: Result) -> None:
    final = "lulus" if result.final_passed else "GAGAL"
    first = "lulus" if result.first_stop_passed else "GAGAL"
    print(
        f"{result.id:<8} jalur {result.route or '-'} {result.status_first:<18} "
        f"pertama {first:<6} akhir {final}"
    )
    for check in result.checks:
        if not check["ok"]:
            print(f"    {check['name']}: {check['detail']}")
    if result.message:
        print(f"    pesan: {result.message}")


def print_summary(state: RunState) -> None:
    s = state.summary
    print(
        f"\nPertama berhenti: {s['pertama_lulus']}/{s['jumlah']}. "
        f"Status akhir: {s['akhir_lulus']}/{s['jumlah']}. Balasan: {s['balasan_diproses']}. "
        f"Pilihan jalur LLM: {s['pilihan_jalur_llm']} (eskalasi {s['eskalasi_llm']}, "
        f"ditolak guard {s['pilihan_jalur_ditolak']}). Embedding: {s['panggilan_embedding']}."
    )
    for name in ("per_kelompok", "per_kategori", "per_format", "per_kanal"):
        cells = [
            f"{key} {row['pertama']}/{row['n']} (akhir {row['akhir']}/{row['n']})"
            for key, row in s[name].items()
        ]
        print(f"{name.replace('per_', 'Per ')}: " + ", ".join(cells))
    print(f"Waktu sampai jalur: {s['waktu_sampai_jalur']}")
    print(f"Panggilan per model: {s['panggilan_per_model']}")
    print(f"Panggilan per tujuan: {s['panggilan_per_tujuan']}")
    print(f"Biaya setara: USD {s['biaya_usd']} (per faktur USD {s['biaya_per_faktur_usd']})")
    if s["gagal"]:
        print(f"Gagal: {', '.join(s['gagal'])}")
    print(f"Hasil: eval/hasil/graph/{state.run}.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="npm run eval:graph", description=__doc__)
    parser.add_argument("--set", action="append", choices=("faktur", "ketahanan"))
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--ekstraksi", choices=("orakel", "llm"), default="llm")
    parser.add_argument("--penilai", choices=("nyala", "mati"))
    parser.add_argument("--tanpa-llm", action="store_true", help="tanpa perencana dan pemetaan")
    parser.add_argument("--pakai-cache", action="store_true")
    parser.add_argument("--intake", choices=("kanal", "langsung"), default="kanal")
    parser.add_argument("--tanpa-reset", action="store_true", help="untuk pengembangan")
    parser.add_argument(
        "--tanpa-rag", action="store_true", help="untuk pengembangan: tanpa indeks kebijakan"
    )
    parser.add_argument("--lanjut", metavar="RUN")
    parser.add_argument("--maks-panggilan", type=int, metavar="N")
    args = parser.parse_args(argv)
    settings = get_settings()
    url = app_url(settings)
    if args.lanjut:
        path = RUN_DIR / f"{args.lanjut}.json"
        state = RunState.model_validate_json(path.read_text(encoding="utf-8"))
    else:
        now = datetime.now(UTC)
        reviewer = (args.penilai or ("nyala" if args.ekstraksi == "llm" else "mati")) == "nyala"
        state = RunState(
            run=now.strftime("%Y%m%d-%H%M%S"),
            started=now,
            updated=now,
            extraction=args.ekstraksi,
            reviewer=reviewer,
            llm=not args.tanpa_llm,
            cache=args.pakai_cache,
            intake=args.intake,
            prompt=EXTRACTION_PROMPT,
            groups=tuple(args.set or ("faktur", "ketahanan")),
            ids=tuple(args.id),
        )
    try:
        todo = cases(state.groups, state.ids)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    done = {r.id for r in state.results}
    ds = load_dataset()
    uses_llm = state.llm or state.extraction == "llm" or state.reviewer
    llm = gemini_client(settings, use_cache=state.cache)
    handler = usage.attach(state.run) if uses_llm else None
    engine = make_engine(url)
    store = Store(engine)
    oracle = oracle_extractor(todo)

    policy_store = PolicyStore(engine)
    embedder = gemini_embedder(
        settings, cache=LlmCache(cache_dir(settings)) if state.cache else None
    )
    if not args.tanpa_rag:
        problem = index_problem(policy_store, settings.gemini_model_embedding)
        if problem is not None:
            engine.dispose()
            usage.detach(handler)
            print(f"{problem}. Jalankan npm run rag:indeks dulu.", file=sys.stderr)
            return 2

    def deps_for(gateway: AgentOdooGateway) -> Dependencies:
        built = live_dependencies(
            settings, store, gateway, llm, policy_store=policy_store, embedder=embedder
        )
        changes: dict[str, Any] = {"files": FileStore(FILES_ROOT)}
        if state.extraction == "orakel":
            changes["extractor"] = oracle
        if not state.reviewer:
            changes["reviewer"] = None
        if not state.llm:
            changes |= {
                "planner": None,
                "line_mapper": None,
                "route_chooser": None,
                "reply_reader": _pattern_reply_reader,
            }
        if args.tanpa_rag:
            changes |= {"route_chooser": None, "policy_quotes": None}
        return replace(built, **changes)

    calls = sum(sum(r.calls_per_model.values()) for r in state.results)
    print(
        f"run {state.run}: {len(todo)} skenario, ekstraksi {state.extraction}, "
        f"penilai {'nyala' if state.reviewer else 'mati'}, LLM {'nyala' if state.llm else 'mati'}"
    )
    code = 0
    try:
        with checkpointer(url) as saver:
            for case in todo:
                if case.id in done:
                    continue
                if args.maks_panggilan is not None and calls >= args.maks_panggilan:
                    state.stopped = f"batas {args.maks_panggilan} panggilan tercapai"
                    break
                if llm.exhausted:
                    state.stopped = f"kuota harian habis: {sorted(llm.exhausted)}"
                    break
                if not args.tanpa_reset:
                    _reset(url, settings)
                result = run_case(case, state, Live(settings, store, saver, deps_for, ds))
                calls += sum(result.calls_per_model.values())
                state.results.append(result)
                save(state)
                print_result(result)
    finally:
        usage.detach(handler)
        engine.dispose()
    save(state)
    print_summary(state)
    if state.stopped:
        print(f"Berhenti: {state.stopped}. Lanjutkan dengan --lanjut {state.run}.")
        code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
