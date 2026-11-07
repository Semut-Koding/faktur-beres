"""``tax_check`` (SPEC §5.2): NPWP, NSFP, transaction code, DPP nilai lain, and PPN (BR-10).

A thin wrapper over ``faktur_beres.domain.rules.tax`` with Pydantic input and output. Rates come
from ``PolicyConfig``, never from Odoo's tax record; nothing here calls Odoo or an LLM.
"""

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.erp import CompanyRecord
from faktur_beres.domain.findings import Finding, reasons_of
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.tax import check_seller_identity, check_tax
from faktur_beres.domain.rules.vendor import VendorIdentification


class TaxCheckRequest(DomainModel):
    invoice: InvoiceData
    company: CompanyRecord
    vendor: VendorIdentification | None = None  # for the NPWP identity check of BR-10


class TaxCheckResult(DomainModel):
    findings: tuple[Finding, ...]
    reasons: tuple[ReasonCode, ...]
    notes: tuple[str, ...]


def tax_check(request: TaxCheckRequest, policy: PolicyConfig) -> TaxCheckResult:
    findings = list(check_tax(request.invoice, request.company, policy))
    if request.vendor is not None:
        identity = check_seller_identity(request.invoice, request.vendor)
        if identity is not None:
            findings.append(identity)
    return TaxCheckResult(
        findings=tuple(findings),
        reasons=reasons_of(findings),
        notes=tuple(f.message for f in findings if f.is_note),
    )
