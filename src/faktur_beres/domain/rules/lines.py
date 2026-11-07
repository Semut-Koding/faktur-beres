"""BR-07 to BR-09: matching the invoice lines with the lines of the established PO.

* **BR-07 mapping**, in order: the vendor's product code (``product.supplierinfo``), the internal
  code (``default_code``), then the name. The mapped product must be on the PO. A product that is on
  more than one PO line leaves the invoice line without a counterpart; it is never picked.
* **BR-08 price**: invoice unit price ≤ PO price × (1 + tolerance) passes (exactly +1% passes);
  above it ``price_variance``. A lower price passes with a note, a small increase within the
  tolerance too.
* **BR-09 quantity**: invoice quantity ≤ received − invoiced and ≤ ordered; above either,
  ``quantity_variance``. A line without counterpart (e.g. shipping that is not on the PO) is
  ``quantity_variance``. Printed units are not converted. Quantities of the bills found as
  duplicates (BR-11) are not counted as invoiced, so one delivery is not counted twice.

Line matching runs only once the PO is established (BR-06).
"""

import re
from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
from typing import Literal

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.erp import PurchaseOrderLineRecord, PurchaseOrderRecord
from faktur_beres.domain.findings import (
    Finding,
    LineMatchEvidence,
    PriceEvidence,
    QuantityEvidence,
    number,
    percent,
    rupiah,
)
from faktur_beres.domain.invoice import InvoiceData, InvoiceLine
from faktur_beres.domain.normalize import normalize_code, normalize_name
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import ReasonCode

RULE_MAPPING = "BR-07"
RULE_PRICE = "BR-08"
RULE_QUANTITY = "BR-09"
_TRAILING_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")


class LineMatch(DomainModel):
    line: int  # 1-based position on the invoice
    status: Literal["matched", "no_match", "ambiguous"]
    method: Literal["vendor_code", "internal_code", "name"] | None = None
    po_line_id: int | None = None
    product_id: int | None = None
    candidate_line_ids: tuple[int, ...] = ()


class LinesResult(DomainModel):
    ran: bool
    purchase_order: str | None = None
    matches: tuple[LineMatch, ...] = ()
    findings: tuple[Finding, ...] = ()

    @property
    def all_matched(self) -> bool:
        return self.ran and all(m.status == "matched" for m in self.matches)


def _names(text: str) -> set[str]:
    """A text and its form without a trailing bracket, e.g. ``Kraft Liner (KL-200)``."""
    return {normalize_name(text), normalize_name(_TRAILING_BRACKETS.sub("", text))} - {""}


def map_line(index: int, line: InvoiceLine, po: PurchaseOrderRecord) -> LineMatch:
    """BR-07: the PO line of an invoice line, by vendor code, internal code, then name."""
    products: set[int] = set()
    method: Literal["vendor_code", "internal_code", "name"] | None = None
    code = normalize_code(line.vendor_code) if line.vendor_code else ""
    if code:
        products = {
            pl.product_id for pl in po.lines if code in {normalize_code(c) for c in pl.vendor_codes}
        }
        method = "vendor_code"
        if not products:
            products = {
                pl.product_id
                for pl in po.lines
                if pl.product_code and normalize_code(pl.product_code) == code
            }
            method = "internal_code"
    if not products:
        wanted = _names(line.description)
        products = {
            pl.product_id
            for pl in po.lines
            if wanted & (_names(pl.product_name) | _names(pl.description))
        }
        method = "name"
    if not products:
        return LineMatch(line=index, status="no_match")
    candidates = tuple(pl.id for pl in po.lines if pl.product_id in products)
    if len(products) > 1 or len(candidates) > 1:
        return LineMatch(
            line=index, status="ambiguous", method=method, candidate_line_ids=candidates
        )
    (po_line,) = (pl for pl in po.lines if pl.id == candidates[0])
    return LineMatch(
        line=index,
        status="matched",
        method=method,
        po_line_id=po_line.id,
        product_id=po_line.product_id,
    )


def price_limit(po_price: Decimal, policy: PolicyConfig) -> Decimal:
    """The highest unit price that still passes BR-08."""
    return po_price * (1 + policy.price_tolerance_percent / 100)


def price_within_tolerance(price: Decimal, po_price: Decimal, policy: PolicyConfig) -> bool:
    return price <= price_limit(po_price, policy)


def check_price(
    index: int, line: InvoiceLine, po_line: PurchaseOrderLineRecord, policy: PolicyConfig
) -> Finding | None:
    """BR-08 for one matched line: a reason above the tolerance, a note for other differences."""
    price, po_price = line.unit_price, po_line.price_unit
    if price == po_price:
        return None
    difference = price - po_price
    diff_percent = (
        (difference / po_price * 100).quantize(Decimal("0.01")) if po_price else Decimal(0)
    )
    limit = price_limit(po_price, policy)
    label = f"Baris {index} {po_line.product_name}"
    if price > limit:
        return Finding(
            rule=RULE_PRICE,
            reason=ReasonCode.PRICE_VARIANCE,
            message=(
                f"{label}: harga {rupiah(price)}, harga PO {rupiah(po_price)} "
                f"({percent(diff_percent)}), di atas batas {rupiah(limit)}."
            ),
            evidence=PriceEvidence(
                line=index,
                po_line_id=po_line.id,
                product=po_line.product_name,
                invoice_price=price,
                po_price=po_price,
                limit=limit,
                difference=difference,
                difference_percent=diff_percent,
            ),
        )
    if price < po_price:
        text = f"lebih rendah dari harga PO {rupiah(po_price)} ({percent(diff_percent)})"
    else:
        text = (
            f"selisih kecil {percent(diff_percent)} dari harga PO {rupiah(po_price)}, "
            f"dalam toleransi {number(policy.price_tolerance_percent)}%"
        )
    return Finding(rule=RULE_PRICE, message=f"{label}: harga {rupiah(price)} {text}.")


def check_quantity(
    index: int,
    line: InvoiceLine,
    po_line: PurchaseOrderLineRecord,
    policy: PolicyConfig,
    *,
    excluded: Decimal = Decimal(0),
    already_on_invoice: Decimal = Decimal(0),
) -> Finding | None:
    """BR-09 for one matched line, after ``already_on_invoice`` from earlier lines."""
    qty = line.quantity
    tolerance = policy.quantity_tolerance
    available = po_line.qty_received - (po_line.qty_invoiced - excluded) - already_on_invoice
    ordered_left = po_line.quantity - already_on_invoice
    problem: Literal["exceeds_received", "exceeds_ordered"]
    if qty > available + tolerance:
        problem, excess = "exceeds_received", qty - available
        text = (
            f"ditagih {number(qty)}, diterima {number(po_line.qty_received)} dan sudah ditagih "
            f"{number(po_line.qty_invoiced - excluded)}, sisa {number(available)} "
            f"(lebih {number(excess)})"
        )
    elif qty > ordered_left + tolerance:
        problem, excess = "exceeds_ordered", qty - ordered_left
        text = f"ditagih {number(qty)}, kuantitas PO {number(po_line.quantity)}"
    else:
        return None
    return Finding(
        rule=RULE_QUANTITY,
        reason=ReasonCode.QUANTITY_VARIANCE,
        message=f"Baris {index} {po_line.product_name}: {text}.",
        evidence=QuantityEvidence(
            line=index,
            po_line_id=po_line.id,
            product=po_line.product_name,
            invoice_quantity=qty,
            ordered=po_line.quantity,
            received=po_line.qty_received,
            invoiced=po_line.qty_invoiced,
            excluded_duplicate=excluded,
            already_on_invoice=already_on_invoice,
            available=available,
            excess=excess,
            problem=problem,
        ),
    )


def match_lines(
    invoice: InvoiceData,
    po: PurchaseOrderRecord,
    policy: PolicyConfig,
    *,
    excluded_by_line: Mapping[int, Decimal] | None = None,
) -> LinesResult:
    """BR-07 to BR-09 for every invoice line against the established PO."""
    excluded_by_line = excluded_by_line or {}
    by_id = {pl.id: pl for pl in po.lines}
    used: dict[int, Decimal] = defaultdict(Decimal)
    matches: list[LineMatch] = []
    findings: list[Finding] = []
    for index, line in enumerate(invoice.lines, start=1):
        match = map_line(index, line, po)
        matches.append(match)
        if match.status != "matched" or match.po_line_id is None:
            problem: Literal["no_match", "ambiguous"] = (
                "ambiguous" if match.status == "ambiguous" else "no_match"
            )
            why = (
                f"produknya muncul di lebih dari satu baris PO {po.number}; tidak dipilihkan"
                if problem == "ambiguous"
                else f"tidak ada padanan di PO {po.number}"
            )
            findings.append(
                Finding(
                    rule=RULE_QUANTITY,
                    reason=ReasonCode.QUANTITY_VARIANCE,
                    message=f"Baris {index} '{line.description}': {why}.",
                    evidence=LineMatchEvidence(
                        line=index,
                        vendor_code=line.vendor_code,
                        description=line.description,
                        problem=problem,
                        purchase_order=po.number,
                        candidate_line_ids=match.candidate_line_ids,
                    ),
                )
            )
            continue
        po_line = by_id[match.po_line_id]
        if (price := check_price(index, line, po_line, policy)) is not None:
            findings.append(price)
        quantity = check_quantity(
            index,
            line,
            po_line,
            policy,
            excluded=excluded_by_line.get(po_line.id, Decimal(0)),
            already_on_invoice=used[po_line.id],
        )
        if quantity is not None:
            findings.append(quantity)
        used[po_line.id] += line.quantity
    return LinesResult(
        ran=True, purchase_order=po.number, matches=tuple(matches), findings=tuple(findings)
    )
