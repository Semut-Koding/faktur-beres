"""``npm run eval:perencana`` (M4a.6): the ``identify`` planner on eight fixed cases.

Reads the ERP from the dataset (no Odoo). NPO-01 and NPO-02 stand as printed; the others have their
printed PO number removed. The expected answer is computed by BR-06 at run time, not from a label.
"""

import argparse
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel

from faktur_beres.config import REPO_ROOT, get_settings
from faktur_beres.dataset import load_dataset
from faktur_beres.domain.engine import collect_vendor_candidates
from faktur_beres.domain.normalize import normalize_po_number
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.rules.purchase_order import identify_purchase_order
from faktur_beres.domain.rules.vendor import identify_vendor
from faktur_beres.evals import audit_log
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.labels import label_intake, load_labels, truth_to_invoice
from faktur_beres.llm.gemini import gemini_client
from faktur_beres.tools.po_planner import run_planner

RUN_DIR = REPO_ROOT / "eval" / "hasil" / "perencana"
AS_PRINTED = ("NPO-01", "NPO-02")
PO_REMOVED = ("COC-01", "COC-02", "DUP-04", "REK-04", "HRG-04", "QTY-01")


class Row(BaseModel):
    id: str
    po_removed: bool
    proposal: str | None
    rule: str | None
    agrees: bool
    status: str
    model_calls: int
    tool_runs: int
    guard_refusals: list[str]
    seconds: float


def run_case(sid: str, removed: bool, reader: DatasetErpReader, llm: object) -> Row:
    label = load_labels()[sid]
    policy = load_policy()
    invoice = truth_to_invoice(label.kebenaran_dokumen)
    if removed:
        invoice = invoice.model_copy(update={"po_number": None})
    intake = label_intake(label)
    vendor_ident = identify_vendor(
        invoice, intake, collect_vendor_candidates(invoice, intake, reader)
    )
    vendor = vendor_ident.vendor
    if vendor is None:
        raise ValueError(f"{sid}: vendor tidak teridentifikasi")
    number = invoice.po_number
    found = reader.purchase_order(normalize_po_number(number)) if number else None
    rule = identify_purchase_order(
        invoice,
        vendor,
        policy,
        number=number,
        found=found,
        confirmed=reader.confirmed_purchase_orders(vendor.id) if found is None else (),
    )
    rule_number = normalize_po_number(rule.purchase_order.number) if rule.purchase_order else None
    started = time.monotonic()
    outcome = run_planner(invoice, vendor, reader, llm=llm, policy=policy)  # type: ignore[arg-type]
    return Row(
        id=sid,
        po_removed=removed,
        proposal=outcome.proposal,
        rule=rule_number,
        agrees=outcome.proposal == rule_number,
        status=outcome.status,
        model_calls=outcome.model_calls,
        tool_runs=outcome.tool_runs,
        guard_refusals=list(outcome.guard_refusals),
        seconds=round(time.monotonic() - started, 1),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="npm run eval:perencana", description=__doc__)
    parser.add_argument("--pakai-cache", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()
    run = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    reader = DatasetErpReader(load_dataset())
    llm = gemini_client(settings, use_cache=args.pakai_cache)
    handler = audit_log.attach(run)
    rows = []
    try:
        cases = [(s, False) for s in AS_PRINTED] + [(s, True) for s in PO_REMOVED]
        for sid, removed in cases:
            row = run_case(sid, removed, reader, llm)
            rows.append(row)
            print(row.model_dump_json())
    finally:
        audit_log.detach(handler)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / f"{run}.json"
    path.write_text(
        "[\n" + ",\n".join(r.model_dump_json() for r in rows) + "\n]\n", encoding="utf-8"
    )
    agreed = sum(r.agrees for r in rows)
    print(f"Sesuai aturan BR-06: {agreed}/{len(rows)}. Hasil: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
