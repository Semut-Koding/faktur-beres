"""BR-03: the arithmetic of the document itself, recomputed by code.

* quantity × unit price = line amount, ±Rp1 per line;
* sum of the line amounts = subtotal, ±Rp1 per line;
* subtotal + PPN = total, ±Rp1.

DPP nilai lain and PPN against the tax rule are checked by BR-10, not here. In M2 a mismatch is an
``arithmetic_error`` (B6) at once; M3 adds the re-extraction of BR-04 before that is concluded.
"""

from decimal import Decimal

from faktur_beres.domain.findings import (
    DocumentSumEvidence,
    Finding,
    LineAmountEvidence,
    number,
    rupiah,
)
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import ReasonCode

RULE = "BR-03"


def check_arithmetic(invoice: InvoiceData, policy: PolicyConfig) -> tuple[Finding, ...]:
    """All arithmetic inconsistencies of the printed document (empty when it adds up)."""
    findings: list[Finding] = []
    per_line = policy.rounding.per_line
    for i, line in enumerate(invoice.lines, start=1):
        computed = line.quantity * line.unit_price
        difference = line.amount - computed
        if abs(difference) > per_line:
            findings.append(
                Finding(
                    rule=RULE,
                    reason=ReasonCode.ARITHMETIC_ERROR,
                    message=(
                        f"Baris {i}: {number(line.quantity)} × {rupiah(line.unit_price)} = "
                        f"{rupiah(computed)}, tercetak {rupiah(line.amount)} "
                        f"(selisih {rupiah(difference)})."
                    ),
                    evidence=LineAmountEvidence(
                        line=i,
                        quantity=line.quantity,
                        unit_price=line.unit_price,
                        printed=line.amount,
                        computed=computed,
                        difference=difference,
                        tolerance=per_line,
                    ),
                )
            )

    lines_sum = sum((line.amount for line in invoice.lines), Decimal(0))
    tolerance = per_line * len(invoice.lines)
    difference = invoice.subtotal - lines_sum
    if abs(difference) > tolerance:
        findings.append(
            Finding(
                rule=RULE,
                reason=ReasonCode.ARITHMETIC_ERROR,
                message=(
                    f"Jumlah baris {rupiah(lines_sum)}, subtotal tercetak "
                    f"{rupiah(invoice.subtotal)} (selisih {rupiah(difference)})."
                ),
                evidence=DocumentSumEvidence(
                    subject="subtotal",
                    printed=invoice.subtotal,
                    computed=lines_sum,
                    difference=difference,
                    tolerance=tolerance,
                ),
            )
        )

    computed_total = invoice.subtotal + invoice.vat
    difference = invoice.total - computed_total
    if abs(difference) > policy.rounding.total:
        findings.append(
            Finding(
                rule=RULE,
                reason=ReasonCode.ARITHMETIC_ERROR,
                message=(
                    f"Subtotal + PPN = {rupiah(computed_total)}, total tercetak "
                    f"{rupiah(invoice.total)} (selisih {rupiah(difference)})."
                ),
                evidence=DocumentSumEvidence(
                    subject="total",
                    printed=invoice.total,
                    computed=computed_total,
                    difference=difference,
                    tolerance=policy.rounding.total,
                ),
            )
        )
    return tuple(findings)
