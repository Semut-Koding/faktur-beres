"""The deterministic analysis: the rule functions chained in the order of SPEC F-04.

``analyse`` is the default chain for the oracle evaluation and the reference for the LLM-driven
graph of M4, whose nodes (``identify``, ``match``, ``decide``) call the same rule functions one by
one. Steps, always in this order:

1. ``validate``: document arithmetic (BR-03);
2. ``identify``: vendor (BR-05) and, for an identified vendor, PO (BR-06);
3. ``match``: duplicates (BR-11, before the lines because BR-09 leaves out the comparators'
   quantities), lines (BR-07 to BR-09, only for an established PO), tax (BR-10), bank account
   (BR-12), injection patterns (BR-15);
4. ``decide``: threshold (BR-13), new vendor (BR-14), allowed routes and default route (BR-16,
   BR-17), and the draft-bill plan (BR-18) when the PO is established and every line matched.

Vendor-dependent checks (BR-06 to BR-09, BR-11, BR-12) run only for a vendor identified uniquely.
The ERP is read only through ``ErpReader``; the engine itself never writes anything.
"""

from collections.abc import Sequence

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.erp import BankAccountRecord, ErpReader, InvoiceRegistry, NoOtherInvoices
from faktur_beres.domain.findings import Finding, reasons_of
from faktur_beres.domain.invoice import IntakeContext, InvoiceData, TextFragment
from faktur_beres.domain.normalize import account_digits, normalize_po_number
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import ReasonCode, Route
from faktur_beres.domain.rules.arithmetic import check_arithmetic
from faktur_beres.domain.rules.bank import check_bank_account
from faktur_beres.domain.rules.bill_plan import BillPlan, plan_bill
from faktur_beres.domain.rules.duplicates import DuplicateResult, find_duplicates
from faktur_beres.domain.rules.injection import invoice_fragments, merge_fragments, scan_fragments
from faktur_beres.domain.rules.lines import LinesResult, match_lines
from faktur_beres.domain.rules.policy_checks import check_new_vendor, check_threshold
from faktur_beres.domain.rules.purchase_order import (
    PoIdentification,
    PoStatus,
    identify_purchase_order,
)
from faktur_beres.domain.rules.routing import allowed_routes, default_route
from faktur_beres.domain.rules.tax import check_seller_identity, check_tax
from faktur_beres.domain.rules.vendor import (
    VendorCandidates,
    VendorIdentification,
    identify_vendor,
    invoice_emails,
)
from faktur_beres.domain.tax_ids import valid_npwp_digits


class StepResult(DomainModel):
    ran: bool = True
    findings: tuple[Finding, ...] = ()


class AnalysisResult(DomainModel):
    arithmetic: StepResult
    vendor: VendorIdentification
    purchase_order: PoIdentification
    duplicates: DuplicateResult
    lines: LinesResult
    tax: StepResult
    bank_account: StepResult
    injection: StepResult
    policy: StepResult
    reasons: tuple[ReasonCode, ...]
    allowed_routes: tuple[Route, ...]
    default_route: Route
    bill_plan: BillPlan | None
    notes: tuple[str, ...]

    @property
    def findings(self) -> tuple[Finding, ...]:
        """Every finding, in step order."""
        return (
            *self.arithmetic.findings,
            *self.vendor.findings,
            *self.purchase_order.findings,
            *self.duplicates.findings,
            *self.lines.findings,
            *self.tax.findings,
            *self.bank_account.findings,
            *self.injection.findings,
            *self.policy.findings,
        )

    def findings_for(self, reason: ReasonCode) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.reason == reason)


def collect_vendor_candidates(
    invoice: InvoiceData, intake: IntakeContext, reader: ErpReader
) -> VendorCandidates:
    """Ask the ERP for vendors by valid NPWP, by name, and by each e-mail address."""
    npwp = valid_npwp_digits(invoice.seller.npwp)
    by_email = [
        v for email in invoice_emails(invoice, intake) for v in reader.vendors_by_email(email)
    ]
    return VendorCandidates(
        by_npwp=tuple(reader.vendors_by_vat(npwp)) if npwp else (),
        by_name=tuple(reader.vendors_by_name(invoice.seller.name)),
        by_email=tuple(by_email),
    )


def _printed_account_owners(
    invoice: InvoiceData, accounts: Sequence[BankAccountRecord], reader: ErpReader
) -> Sequence[BankAccountRecord]:
    if invoice.bank_account is None:
        return ()
    digits = account_digits(invoice.bank_account.number)
    if not digits or digits in {a.digits for a in accounts}:
        return ()
    return reader.bank_account_owners(digits)


def analyse(
    invoice: InvoiceData,
    intake: IntakeContext,
    fragments: Sequence[TextFragment],
    reader: ErpReader,
    policy: PolicyConfig,
    *,
    registry: InvoiceRegistry | None = None,
    po_number_from_reply: str | None = None,
) -> AnalysisResult:
    """Run every rule on one invoice and compute reasons, routes, and the bill plan.

    ``fragments`` are the free texts around the invoice (hidden text, PDF text layer, e-mail, ...);
    the invoice's own notes and line descriptions are added here. ``po_number_from_reply`` is a PO
    number the vendor gave in a text reply (F-06); BR-06 then looks that number up.
    """
    registry = registry or NoOtherInvoices()

    arithmetic = StepResult(findings=check_arithmetic(invoice, policy))
    company = reader.company()
    vendor_ident = identify_vendor(
        invoice, intake, collect_vendor_candidates(invoice, intake, reader)
    )
    vendor = vendor_ident.vendor if vendor_ident.identified else None

    accounts: Sequence[BankAccountRecord] = ()
    posted: int | None = None
    if vendor is not None:
        duplicates = find_duplicates(
            invoice,
            vendor.id,
            reader.vendor_bills(vendor.id),
            registry.other_invoices(vendor.id, exclude_invoice_id=intake.invoice_id),
            policy,
            exclude_invoice_id=intake.invoice_id,
        )
        number = po_number_from_reply or invoice.po_number
        found = reader.purchase_order(normalize_po_number(number)) if number else None
        po_ident = identify_purchase_order(
            invoice,
            vendor,
            policy,
            number=number,
            found=found,
            confirmed=reader.confirmed_purchase_orders(vendor.id) if found is None else (),
            from_reply=po_number_from_reply is not None,
        )
        po = po_ident.purchase_order
        lines = (
            match_lines(invoice, po, policy, excluded_by_line=duplicates.excluded_quantities())
            if po_ident.established and po is not None
            else LinesResult(ran=False)
        )
        accounts = tuple(reader.bank_accounts(vendor.id))
        bank = StepResult(
            findings=check_bank_account(
                invoice, vendor, accounts, _printed_account_owners(invoice, accounts, reader)
            )
        )
        posted = reader.posted_bill_count(vendor.id)
    else:
        duplicates = DuplicateResult(ran=False)
        po_ident = PoIdentification(status=PoStatus.SKIPPED)
        lines = LinesResult(ran=False)
        bank = StepResult(ran=False)

    tax_findings = list(check_tax(invoice, company, policy))
    if (identity := check_seller_identity(invoice, vendor_ident)) is not None:
        tax_findings.append(identity)
    tax = StepResult(findings=tuple(tax_findings))

    injection = StepResult(
        findings=scan_fragments(merge_fragments(fragments, invoice_fragments(invoice)))
    )
    policy_step = StepResult(
        findings=tuple(
            f
            for f in (check_threshold(invoice, policy), check_new_vendor(vendor_ident, posted))
            if f is not None
        )
    )

    plan = None
    if vendor is not None and po_ident.established and po_ident.purchase_order is not None:
        plan = plan_bill(invoice, vendor, po_ident.purchase_order, lines, accounts)

    steps = (
        arithmetic,
        vendor_ident,
        po_ident,
        duplicates,
        lines,
        tax,
        bank,
        injection,
        policy_step,
    )
    every = tuple(f for step in steps for f in step.findings)
    reasons = reasons_of(every)
    allowed = allowed_routes(reasons)
    return AnalysisResult(
        arithmetic=arithmetic,
        vendor=vendor_ident,
        purchase_order=po_ident,
        duplicates=duplicates,
        lines=lines,
        tax=tax,
        bank_account=bank,
        injection=injection,
        policy=policy_step,
        reasons=reasons,
        allowed_routes=allowed,
        default_route=default_route(allowed),
        bill_plan=plan,
        notes=tuple(f.message for f in every if f.is_note),
    )
