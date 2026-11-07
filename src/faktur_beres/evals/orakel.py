"""Oracle evaluation (SPEC §12.1, since M2): the deterministic rules on perfect extraction.

For each of the 40 labels the oracle

1. converts ``kebenaran_dokumen`` into ``InvoiceData`` (a perfect extraction) and the channel and
   e-mail into ``IntakeContext`` and text fragments;
2. runs ``analyse()`` and takes the default route;
3. simulates what follows, the way the agent would act:

   * route A: the draft bill of the plan;
   * route B: one e-mail to the vendor's billing address **from the ERP**; a certain duplicate is
     closed at once; otherwise the vendor's reply is taken, but only from that address (BR-20).
     An attachment is analysed as the corrected invoice of the same thread (BR-11); a text reply
     gives PO numbers, kept when they exist in the ERP, belong to the vendor, and are not
     cancelled. That must leave exactly one, equal to ``po_rujukan`` (the one cross-check with the
     label), and the PO identification is run again with it;
   * route C: the approver's decision; approving executes the plan of the first analysis (twice
     when the label asks for the idempotency test, which must not create a second bill),
     rejecting creates nothing;

4. scores the outcome with the rules of SPEC §15.4 that need no full system: initial route,
   reasons (required ⊆ actual ⊆ required ∪ allowed), final status, new bills before the
   simulation, vendor e-mail before and in total, the expected Odoo bill (vendor, ``ref``
   normalised, total ±Rp1 computed from the policy rate, every line), and the checkable
   prohibitions.

Inputs are the document truth, channel and e-mail, and the simulation; ``harapan``, ``kategori``,
``varian``, ``vendor``, and ``po_rujukan`` are only used for scoring (and the text-reply check).

``--odoo`` reads the ERP from the seeded Odoo with the agent key (read only) instead of the
dataset; both modes must give the same outcomes.
"""

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Literal

from faktur_beres.config import REPO_ROOT
from faktur_beres.dataset import Dataset, get_dataset
from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.engine import AnalysisResult, analyse
from faktur_beres.domain.erp import ErpReader, PoState, VendorRecord
from faktur_beres.domain.normalize import (
    normalize_email,
    normalize_invoice_number,
    normalize_po_number,
    tax_id_digits,
)
from faktur_beres.domain.policy import PolicyConfig, load_policy
from faktur_beres.domain.reasons import InvoiceStatus, ReasonCode, Route
from faktur_beres.domain.rules.bill_plan import BillPlan
from faktur_beres.domain.rules.routing import initial_status
from faktur_beres.domain.rules.safety import idempotency_key
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.labels import (
    email_fragments,
    label_intake,
    load_labels,
    reply_fragments,
    reply_intake,
    truth_fragments,
    truth_to_invoice,
)
from faktur_beres.generator.label_schema import Label
from faktur_beres.tools.errors import ToolError
from faktur_beres.tools.odoo_gateway import AgentOdooGateway
from faktur_beres.tools.odoo_tools import OdooErpReader

RESULT_DIR = REPO_ROOT / "eval" / "hasil"
PO_TOKEN = re.compile(r"\b[A-Z]{1,4}-?\d{3,}\b")
Mode = Literal["luring", "odoo"]


class PlannedLine(DomainModel):
    po_line: str  # "P00027/p03": PO number / dataset product key
    qty: Decimal
    price: Decimal


class PlannedBill(DomainModel):
    vendor: str  # dataset vendor key
    ref: str
    total: Decimal
    lines: tuple[PlannedLine, ...]
    key: str  # BR-22 idempotency key of the create action


class Outcome(DomainModel):
    """What the agent would have done for one scenario (natural keys only, mode independent)."""

    id: str
    initial_route: Route
    reasons: tuple[ReasonCode, ...]
    initial_status: InvoiceStatus
    final_status: InvoiceStatus
    bills_before: int
    emails_before: tuple[tuple[str, int], ...]
    emails_total: tuple[tuple[str, int], ...]
    bills: tuple[PlannedBill, ...]
    steps: tuple[str, ...]


def comparable(outcome: Outcome) -> dict[str, object]:
    """An outcome without what depends on record ids (idempotency keys) or wording (steps)."""
    data = outcome.model_dump(mode="json", exclude={"steps"})
    for bill in data["bills"]:
        bill.pop("key")
    return data


class Check(DomainModel):
    name: str
    ok: bool
    detail: str


class Score(DomainModel):
    id: str
    expected: str
    actual: str
    passed: bool
    checks: tuple[Check, ...]


# -- simulation -------------------------------------------------------------------------------


class Simulator:
    def __init__(self, reader: ErpReader, policy: PolicyConfig, ds: Dataset) -> None:
        self.reader = reader
        self.policy = policy
        self.vendor_keys = {tax_id_digits(v.npwp): v.key for v in ds.vendors}
        self.product_keys = {p.code: p.key for p in ds.products}

    def bill(self, sid: str, plan: BillPlan, analysis: AnalysisResult) -> PlannedBill:
        vendor = analysis.vendor.vendor
        if vendor is None:  # a plan exists only for an identified vendor
            raise ValueError(f"{sid}: rencana tagihan tanpa vendor")
        lines = []
        for line in plan.lines:
            product = self.product_keys.get(line.product_code or "", "?")
            lines.append(
                PlannedLine(
                    po_line=f"{line.po_number}/{product}", qty=line.quantity, price=line.price_unit
                )
            )
        return PlannedBill(
            vendor=self.vendor_keys.get(tax_id_digits(vendor.vat), f"#{vendor.id}"),
            ref=plan.ref,
            total=plan.planned_total(self.policy),
            lines=tuple(lines),
            key=idempotency_key(sid, "create_draft_bill", f"vendor:{vendor.id}", plan),
        )

    def reply_po_numbers(self, text: str, vendor_id: int) -> list[str]:
        """PO numbers named in a reply that exist, belong to the vendor, and are not cancelled."""
        found: list[str] = []
        for token in PO_TOKEN.findall(text.upper()):
            number = normalize_po_number(token)
            po = self.reader.purchase_order(number)
            if (
                po is not None
                and po.vendor_id == vendor_id
                and po.state != PoState.CANCEL
                and po.number not in found
            ):
                found.append(po.number)
        return found

    def run(self, label: Label) -> Outcome:
        sid = label.id
        truth = label.kebenaran_dokumen
        invoice = truth_to_invoice(truth)
        fragments = truth_fragments(truth, document_format=label.format) + email_fragments(label)
        first = analyse(invoice, label_intake(label), fragments, self.reader, self.policy)
        route = first.default_route
        status = initial_status(route, first.reasons)
        steps = [f"analisis: jalur {route.value}, alasan {[r.value for r in first.reasons]}"]
        bills: dict[str, PlannedBill] = {}
        emails: Counter[str] = Counter()
        vendor = first.vendor.vendor

        def execute(plan: BillPlan | None, analysis: AnalysisResult, why: str) -> InvoiceStatus:
            if plan is None:
                steps.append(f"{why}: tidak ada rencana tagihan")
                return InvoiceStatus.FAILED
            planned = self.bill(sid, plan, analysis)
            if planned.key in bills:
                steps.append(f"{why}: kunci idempoten sama, tidak membuat tagihan kedua")
            else:
                bills[planned.key] = planned
                steps.append(f"{why}: draft tagihan {planned.ref} {planned.total}")
            return InvoiceStatus.DRAFT_CREATED

        if route == Route.A:
            status = execute(first.bill_plan, first, "jalur A")
        bills_before = len(bills)

        if route == Route.B:
            address = normalize_email(vendor.email) if vendor else None
            if address is None:
                steps.append("jalur B tanpa alamat penagihan vendor di ERP")
                status = InvoiceStatus.FAILED
            else:
                emails[address] += 1
                steps.append(f"email ke {address}")
        emails_before = tuple(sorted(emails.items()))

        if route == Route.B and status == InvoiceStatus.AWAITING_VENDOR and vendor is not None:
            status = self._vendor_reply(label, vendor, emails, steps, execute)
        elif route == Route.C:
            decision = label.simulasi.keputusan_approver
            if decision is None:
                steps.append("menunggu approver")
            elif decision.keputusan == "tolak":
                status = InvoiceStatus.REJECTED
                steps.append("approver menolak")
            else:
                times = 2 if label.simulasi.uji_idempotensi else 1
                for i in range(times):
                    status = execute(first.bill_plan, first, f"approver menyetujui ({i + 1})")

        return Outcome(
            id=sid,
            initial_route=route,
            reasons=first.reasons,
            initial_status=initial_status(route, first.reasons),
            final_status=status,
            bills_before=bills_before,
            emails_before=emails_before,
            emails_total=tuple(sorted(emails.items())),
            bills=tuple(bills.values()),
            steps=tuple(steps),
        )

    def _vendor_reply(
        self,
        label: Label,
        vendor: VendorRecord,
        emails: Counter[str],
        steps: list[str],
        execute: Callable[[BillPlan | None, AnalysisResult, str], InvoiceStatus],
    ) -> InvoiceStatus:
        reply = label.simulasi.balasan_vendor
        if reply is None:
            steps.append("tidak ada balasan vendor")
            return InvoiceStatus.AWAITING_VENDOR
        sender = normalize_email(reply.dari)
        if sender is None or sender != normalize_email(vendor.email):
            steps.append(f"balasan dari {reply.dari} diabaikan (BR-20)")
            return InvoiceStatus.AWAITING_VENDOR
        vendor_id = vendor.id

        if reply.jenis == "lampiran":
            if reply.kebenaran_dokumen is None:
                steps.append("balasan lampiran tanpa dokumen")
                return InvoiceStatus.FAILED
            corrected = truth_to_invoice(reply.kebenaran_dokumen)
            again = analyse(
                corrected,
                reply_intake(reply),
                reply_fragments(reply, document_format=label.format),
                self.reader,
                self.policy,
            )
            source = "faktur perbaikan"
        else:
            numbers = self.reply_po_numbers(reply.isi, vendor_id)
            steps.append(f"PO dari balasan: {numbers}")
            if len(numbers) != 1 or numbers[0] != label.po_rujukan:
                steps.append("nomor PO dari balasan tidak tunggal atau tidak sesuai")
                return InvoiceStatus.AWAITING_VENDOR
            truth = label.kebenaran_dokumen
            again = analyse(
                truth_to_invoice(truth),
                label_intake(label),
                truth_fragments(truth, document_format=label.format)
                + email_fragments(label)
                + reply_fragments(reply, document_format=label.format),
                self.reader,
                self.policy,
                po_number_from_reply=numbers[0],
            )
            source = "identifikasi PO ulang"
        reasons = [r.value for r in again.reasons]
        steps.append(f"{source}: jalur {again.default_route.value}, alasan {reasons}")
        if again.default_route == Route.A:
            return execute(again.bill_plan, again, source)
        if again.default_route == Route.B:
            # Another round of clarification: one more e-mail, then waiting again.
            if address := normalize_email(vendor.email):
                emails[address] += 1
            return InvoiceStatus.AWAITING_VENDOR
        return InvoiceStatus.AWAITING_APPROVAL


# -- scoring ----------------------------------------------------------------------------------


def score(label: Label, outcome: Outcome, policy: PolicyConfig) -> Score:
    h = label.harapan
    checks: list[Check] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append(Check(name=name, ok=ok, detail=detail))

    check(
        "jalur_awal",
        outcome.initial_route.value == h.jalur_awal,
        f"{outcome.initial_route.value} (harapan {h.jalur_awal})",
    )
    actual = {r.value for r in outcome.reasons}
    required, extra = set(h.alasan_wajib), set(h.alasan_boleh_tambahan)
    check(
        "alasan",
        required <= actual <= required | extra,
        f"{sorted(actual)} (wajib {sorted(required)}, boleh {sorted(extra)})",
    )
    check(
        "status_akhir",
        outcome.final_status.value == h.status_akhir,
        f"{outcome.final_status.value} (harapan {h.status_akhir})",
    )
    check(
        "tagihan_baru",
        outcome.bills_before == h.sebelum_simulasi.tagihan_baru,
        f"{outcome.bills_before} (harapan {h.sebelum_simulasi.tagihan_baru})",
    )
    want_before = tuple(sorted((e.ke, e.jumlah) for e in h.sebelum_simulasi.email_keluar))
    check("email_keluar", outcome.emails_before == want_before, f"{outcome.emails_before}")
    want_total = tuple(sorted((e.ke, e.jumlah) for e in h.email_keluar_total))
    check("email_keluar_total", outcome.emails_total == want_total, f"{outcome.emails_total}")

    expected_bill = h.tagihan_odoo
    if expected_bill is None:
        check(
            "tagihan_odoo", not outcome.bills, f"{len(outcome.bills)} tagihan (harapan tidak ada)"
        )
    else:
        ok = len(outcome.bills) == expected_bill.jumlah == 1
        detail = f"{len(outcome.bills)} tagihan"
        if ok:
            bill = outcome.bills[0]
            want_lines = Counter(
                (ln.po_line, Decimal(ln.qty), Decimal(ln.harga_satuan))
                for ln in expected_bill.baris
            )
            have_lines = Counter((ln.po_line, ln.qty, ln.price) for ln in bill.lines)
            parts = {
                "vendor": bill.vendor == expected_bill.vendor,
                "ref": normalize_invoice_number(bill.ref)
                == normalize_invoice_number(expected_bill.ref),
                "total": abs(bill.total - expected_bill.total) <= policy.rounding.total,
                "baris": have_lines == want_lines,
            }
            ok = all(parts.values())
            detail = f"{bill.vendor} {bill.ref} {bill.total} {sorted(map(str, have_lines))}" + (
                "" if ok else f"; beda: {[k for k, v in parts.items() if not v]}"
            )
        check("tagihan_odoo", ok, detail)

    vendor_addresses = {e.ke for e in h.email_keluar_total}
    sent = {address for address, _ in outcome.emails_total}
    for prohibition in h.larangan:
        if prohibition == "aksi_sebelum_persetujuan":
            check(prohibition, outcome.bills_before == 0 and not outcome.emails_before, "jalur C")
        elif prohibition == "aksi_ganda":
            refs = Counter(normalize_invoice_number(b.ref) for b in outcome.bills)
            check(prohibition, all(n == 1 for n in refs.values()), f"{dict(refs)}")
        elif prohibition == "email_ke_alamat_lain":
            check(prohibition, sent <= vendor_addresses, f"{sorted(sent)}")
        elif prohibition.startswith("email_ke:"):
            target = prohibition.split(":", 1)[1]
            check(prohibition, target not in sent, f"{sorted(sent)}")
        # tagihan_terposting and ubah_data_master: the oracle never posts or writes master data.

    passed = all(c.ok for c in checks)
    return Score(
        id=label.id,
        expected=f"{h.jalur_awal} → {h.status_akhir}",
        actual=f"{outcome.initial_route.value} → {outcome.final_status.value}",
        passed=passed,
        checks=tuple(checks),
    )


# -- running ----------------------------------------------------------------------------------


class Report(DomainModel):
    mode: str
    total: int
    passed: int
    outcomes: tuple[Outcome, ...]
    scores: tuple[Score, ...]


def run_oracle(reader: ErpReader, mode: str, labels: dict[str, Label] | None = None) -> Report:
    labels = labels if labels is not None else load_labels()
    policy = load_policy()
    simulator = Simulator(reader, policy, get_dataset())
    outcomes, scores = [], []
    for label in labels.values():
        outcome = simulator.run(label)
        outcomes.append(outcome)
        scores.append(score(label, outcome, policy))
    return Report(
        mode=mode,
        total=len(scores),
        passed=sum(s.passed for s in scores),
        outcomes=tuple(outcomes),
        scores=tuple(scores),
    )


def offline_reader() -> ErpReader:
    return DatasetErpReader(get_dataset())


def seed_state_problems(gw: AgentOdooGateway, ds: Dataset) -> list[str]:
    """Why Odoo is not in the seed state: the only vendor bills must be the posted history bills."""
    rows = gw.search_read(
        "account.move", [["move_type", "=", "in_invoice"]], ["id", "ref", "state"]
    )
    expected = sorted(bill.vendor_invoice_number for _, bill in ds.history_bills)
    problems = []
    if sorted(str(r["ref"]) for r in rows) != expected:
        problems.append(f"{len(rows)} tagihan vendor, seharusnya {len(expected)} tagihan riwayat")
    not_posted = [r["id"] for r in rows if r["state"] != "posted"]
    if not_posted:
        problems.append(f"tagihan belum terposting: {not_posted}")
    return problems


def compare_reports(offline: Report, online: Report) -> list[str]:
    """Scenarios whose outcome differs between the two modes."""
    off = {o.id: comparable(o) for o in offline.outcomes}
    on = {o.id: comparable(o) for o in online.outcomes}
    return sorted(sid for sid in off.keys() | on.keys() if off.get(sid) != on.get(sid))


def odoo_report(labels: dict[str, Label]) -> tuple[int, Report | None, list[str]]:
    """Run the oracle on Odoo (agent key, read only). Returns (exit code, report, differences)."""
    ds = get_dataset()
    try:
        with AgentOdooGateway.from_settings() as gw:
            problems = seed_state_problems(gw, ds)
            if problems:
                print(
                    "galat: Odoo tidak dalam kondisi seed (" + "; ".join(problems) + "). "
                    "Jalankan npm run odoo:reset lalu ulangi.",
                    file=sys.stderr,
                )
                return 2, None, []
            report = run_oracle(OdooErpReader(gw), "odoo", labels)
    except ToolError as exc:
        print(f"galat: Odoo tidak bisa dibaca dengan key agent: {exc}", file=sys.stderr)
        return 2, None, []
    differences = compare_reports(run_oracle(offline_reader(), "luring", labels), report)
    return 0, report, differences


def print_report(report: Report, labels: dict[str, Label]) -> None:
    rows = []
    for s, o in zip(report.scores, report.outcomes, strict=True):
        reasons = ", ".join(r.value for r in o.reasons) or "-"
        failed = [c.name for c in s.checks if not c.ok]
        rows.append(
            (
                s.id,
                labels[s.id].kategori,
                s.expected,
                s.actual,
                reasons,
                "lulus" if s.passed else "GAGAL: " + ", ".join(failed),
            )
        )
    headers = ("ID", "Kategori", "Harapan", "Hasil", "Alasan", "Status")
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    for r in [headers, tuple("-" * w for w in widths), *rows]:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)).rstrip())
    by_category: dict[str, list[bool]] = {}
    for s in report.scores:
        by_category.setdefault(labels[s.id].kategori, []).append(s.passed)
    print()
    print("Per kategori: " + ", ".join(f"{k} {sum(v)}/{len(v)}" for k, v in by_category.items()))
    print(f"Evaluasi orakel ({report.mode}): {report.passed}/{report.total} skenario lulus")


def write_json(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluasi orakel 40 skenario (SPEC §12.1)")
    parser.add_argument(
        "--odoo",
        action="store_true",
        help="baca ERP dari Odoo hasil seed lewat user agent (baca saja), bukan dari dataset",
    )
    parser.add_argument("--json", type=Path, default=None, help="berkas hasil JSON")
    args = parser.parse_args(argv)

    labels = load_labels()
    mode: Mode = "odoo" if args.odoo else "luring"
    differences: list[str] = []
    if args.odoo:
        code, found, differences = odoo_report(labels)
        if found is None:
            return code
        report = found
    else:
        report = run_oracle(offline_reader(), mode, labels)
    print_report(report, labels)
    if args.odoo:
        print(
            "Sama dengan mode luring: "
            + ("ya" if not differences else "TIDAK, berbeda di " + ", ".join(differences))
        )
    path = args.json or RESULT_DIR / f"orakel-{mode}.json"
    write_json(report, path)
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    print(f"Hasil JSON: {shown}")
    return 0 if report.passed == report.total and not differences else 1


if __name__ == "__main__":
    sys.exit(main())
