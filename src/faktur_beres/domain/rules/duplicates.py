"""BR-11: duplicates.

* Key 1: vendor + invoice number normalised (upper case; spaces, ``/``, ``-``, ``.``, ``_``
  removed), against the vendor's bills in Odoo in state draft or posted (cancelled bills do not
  count) and against other invoices in Faktur Beres. Same key and same total:
  ``duplicate_confirmed``. Same key, different total: ``duplicate_suspected``.
* Key 2: vendor + invoice date + total equal with a different number: ``duplicate_suspected``.

The same number from another vendor is not a duplicate. A corrected invoice that arrives as a reply
in the same thread replaces that thread's invoice and is not compared with the original (the
caller does not pass the thread's own invoice). The bills found here are the comparators whose
quantities BR-09 does not count as invoiced.
"""

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.erp import BillRecord, BillState, RegisteredInvoice
from faktur_beres.domain.findings import DuplicateEvidence, Finding, rupiah
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.normalize import normalize_invoice_number
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import ReasonCode

RULE = "BR-11"
COUNTED_STATES = frozenset({BillState.DRAFT, BillState.POSTED})
_STATE_TEXT = {BillState.DRAFT: "draft", BillState.POSTED: "terposting", BillState.CANCEL: "batal"}


class DuplicateResult(DomainModel):
    ran: bool
    findings: tuple[Finding, ...] = ()
    comparators: tuple[BillRecord, ...] = ()

    def excluded_quantities(self) -> dict[int, Decimal]:
        """Quantity per PO line on the comparator bills (BR-09 does not count it as invoiced)."""
        totals: dict[int, Decimal] = defaultdict(Decimal)
        for bill in self.comparators:
            for line in bill.lines:
                if line.purchase_line_id is not None:
                    totals[line.purchase_line_id] += line.quantity
        return dict(totals)


def _match(
    invoice: InvoiceData,
    number: str | None,
    invoice_date: object,
    total: Decimal,
    policy: PolicyConfig,
) -> tuple[ReasonCode, Literal["number", "date_total"]] | None:
    same_total = abs(invoice.total - total) <= policy.rounding.total
    if number and normalize_invoice_number(number) == normalize_invoice_number(
        invoice.invoice_number
    ):
        return (
            ReasonCode.DUPLICATE_CONFIRMED if same_total else ReasonCode.DUPLICATE_SUSPECTED,
            "number",
        )
    if invoice_date == invoice.invoice_date and same_total:
        return ReasonCode.DUPLICATE_SUSPECTED, "date_total"
    return None


def _message(
    reason: ReasonCode, key: str, what: str, ref: str | None, total: Decimal, invoice: InvoiceData
) -> str:
    if reason == ReasonCode.DUPLICATE_CONFIRMED:
        return f"Sama dengan {what} {ref}: nomor dan total {rupiah(total)} sama."
    if key == "number":
        return (
            f"Nomor sama dengan {what} {ref}, tetapi total berbeda: faktur {rupiah(invoice.total)},"
            f" tercatat {rupiah(total)} (selisih {rupiah(invoice.total - total)})."
        )
    return (
        f"Tanggal {invoice.invoice_date.isoformat()} dan total {rupiah(total)} sama dengan "
        f"{what} {ref}, tetapi nomornya berbeda."
    )


def find_duplicates(
    invoice: InvoiceData,
    vendor_id: int,
    bills: Sequence[BillRecord],
    others: Sequence[RegisteredInvoice],
    policy: PolicyConfig,
) -> DuplicateResult:
    findings: list[Finding] = []
    comparators: list[BillRecord] = []
    for bill in bills:
        if bill.vendor_id != vendor_id or bill.state not in COUNTED_STATES:
            continue
        found = _match(invoice, bill.ref, bill.invoice_date, bill.amount_total, policy)
        if found is None:
            continue
        reason, key = found
        what = f"tagihan {_STATE_TEXT[bill.state]} di Odoo"
        findings.append(
            Finding(
                rule=RULE,
                reason=reason,
                message=_message(reason, key, what, bill.ref, bill.amount_total, invoice),
                evidence=DuplicateEvidence(
                    key=key,
                    source="odoo",
                    record=str(bill.id),
                    ref=bill.ref,
                    state=bill.state.value,
                    invoice_date=bill.invoice_date.isoformat() if bill.invoice_date else None,
                    total=bill.amount_total,
                    invoice_total=invoice.total,
                    difference=invoice.total - bill.amount_total,
                ),
            )
        )
        comparators.append(bill)
    for other in others:
        if other.vendor_id != vendor_id:
            continue
        found = _match(invoice, other.invoice_number, other.invoice_date, other.total, policy)
        if found is None:
            continue
        reason, key = found
        what = "faktur lain di Faktur Beres"
        findings.append(
            Finding(
                rule=RULE,
                reason=reason,
                message=_message(reason, key, what, other.invoice_number, other.total, invoice),
                evidence=DuplicateEvidence(
                    key=key,
                    source="faktur_beres",
                    record=other.invoice_id,
                    ref=other.invoice_number,
                    state=None,
                    invoice_date=other.invoice_date.isoformat(),
                    total=other.total,
                    invoice_total=invoice.total,
                    difference=invoice.total - other.total,
                ),
            )
        )
    return DuplicateResult(ran=True, findings=tuple(findings), comparators=tuple(comparators))
