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

from collections.abc import Mapping, Sequence

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
from faktur_beres.domain.rules.injection import (
    InjectionReview,
    document_fragments,
    judge_injection,
)
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
    VendorStatus,
    identify_vendor,
    invoice_emails,
)
from faktur_beres.domain.tax_ids import valid_npwp_digits


class StepResult(DomainModel):
    ran: bool = True
    findings: tuple[Finding, ...] = ()


class AnalysisResult(DomainModel):
    extraction: StepResult = StepResult(ran=False)  # filled only when the document was unreadable
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
    # BR-15: no injection pattern, and layer 2 has not answered yet (unavailable or invalid).
    # Route A must wait until layer 2 succeeds (M4); routes B and C are not affected.
    injection_awaiting_review: bool = False
    default_route: Route
    bill_plan: BillPlan | None
    notes: tuple[str, ...]

    @property
    def findings(self) -> tuple[Finding, ...]:
        """Every finding, in step order."""
        return (
            *self.extraction.findings,
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


class Identification(DomainModel):
    """The ``identify`` step: vendor (BR-05) and, for an identified vendor, PO (BR-06)."""

    vendor: VendorIdentification
    purchase_order: PoIdentification


class Matching(DomainModel):
    """The ``match`` step: everything checked against the identified vendor and PO."""

    duplicates: DuplicateResult
    lines: LinesResult
    tax: StepResult
    bank_account: StepResult
    injection: StepResult
    injection_awaiting_review: bool = False
    accounts: tuple[BankAccountRecord, ...] = ()


def validate_step(invoice: InvoiceData, policy: PolicyConfig) -> StepResult:
    return StepResult(findings=check_arithmetic(invoice, policy))


def identify_step(
    invoice: InvoiceData,
    intake: IntakeContext,
    reader: ErpReader,
    policy: PolicyConfig,
    *,
    po_number_from_reply: str | None = None,
) -> Identification:
    vendor_ident = identify_vendor(
        invoice, intake, collect_vendor_candidates(invoice, intake, reader)
    )
    vendor = vendor_ident.vendor if vendor_ident.identified else None
    if vendor is None:
        return Identification(
            vendor=vendor_ident, purchase_order=PoIdentification(status=PoStatus.SKIPPED)
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
    return Identification(vendor=vendor_ident, purchase_order=po_ident)


def judge_step(
    invoice: InvoiceData | None,
    fragments: Sequence[TextFragment],
    injection_review: InjectionReview | None,
) -> tuple[StepResult, bool]:
    """BR-15: the injection step and whether route A must wait for layer 2."""
    judgement = judge_injection(document_fragments(invoice, fragments), injection_review)
    return StepResult(findings=judgement.findings), judgement.awaiting_review


def match_step(
    invoice: InvoiceData,
    intake: IntakeContext,
    fragments: Sequence[TextFragment],
    identification: Identification,
    reader: ErpReader,
    policy: PolicyConfig,
    *,
    registry: InvoiceRegistry | None = None,
    injection_review: InjectionReview | None = None,
    llm_mapping: Mapping[int, int] | None = None,
) -> Matching:
    registry = registry or NoOtherInvoices()
    vendor_ident, po_ident = identification.vendor, identification.purchase_order
    vendor = vendor_ident.vendor if vendor_ident.identified else None
    company = reader.company()
    accounts: Sequence[BankAccountRecord] = ()
    if vendor is not None:
        duplicates = find_duplicates(
            invoice,
            vendor.id,
            reader.vendor_bills(vendor.id),
            registry.other_invoices(vendor.id, exclude_invoice_id=intake.invoice_id),
            policy,
            exclude_invoice_id=intake.invoice_id,
        )
        po = po_ident.purchase_order
        lines = (
            match_lines(
                invoice,
                po,
                policy,
                excluded_by_line=duplicates.excluded_quantities(),
                llm_mapping=llm_mapping,
            )
            if po_ident.established and po is not None
            else LinesResult(ran=False)
        )
        accounts = tuple(reader.bank_accounts(vendor.id))
        printed = invoice.bank_account
        bank = StepResult(
            findings=check_bank_account(
                invoice,
                vendor,
                accounts,
                _printed_account_owners(invoice, accounts, reader),
                tuple(reader.banks()) if printed is not None and printed.bank else (),
            )
        )
    else:
        duplicates = DuplicateResult(ran=False)
        lines = LinesResult(ran=False)
        bank = StepResult(ran=False)

    tax_findings = list(check_tax(invoice, company, policy))
    if (identity := check_seller_identity(invoice, vendor_ident)) is not None:
        tax_findings.append(identity)
    injection, awaiting = judge_step(invoice, fragments, injection_review)
    return Matching(
        duplicates=duplicates,
        lines=lines,
        tax=StepResult(findings=tuple(tax_findings)),
        bank_account=bank,
        injection=injection,
        injection_awaiting_review=awaiting,
        accounts=tuple(accounts),
    )


def apply_line_mapping(
    invoice: InvoiceData,
    identification: Identification,
    matching: Matching,
    policy: PolicyConfig,
    mapping: Mapping[int, int],
) -> Matching:
    """Match the lines again with a mapping the caller validated (BR-07)."""
    po = identification.purchase_order.purchase_order
    if po is None or not mapping:
        return matching
    lines = match_lines(
        invoice,
        po,
        policy,
        excluded_by_line=matching.duplicates.excluded_quantities(),
        llm_mapping=mapping,
    )
    return matching.model_copy(update={"lines": lines})


def _result(
    *,
    extraction: StepResult,
    arithmetic: StepResult,
    identification: Identification,
    matching: Matching,
    policy_step: StepResult,
    plan: BillPlan | None,
) -> AnalysisResult:
    steps = (
        extraction,
        arithmetic,
        identification.vendor,
        identification.purchase_order,
        matching.duplicates,
        matching.lines,
        matching.tax,
        matching.bank_account,
        matching.injection,
        policy_step,
    )
    every = tuple(f for step in steps for f in step.findings)
    reasons = reasons_of(every)
    allowed = allowed_routes(reasons)
    return AnalysisResult(
        extraction=extraction,
        arithmetic=arithmetic,
        vendor=identification.vendor,
        purchase_order=identification.purchase_order,
        duplicates=matching.duplicates,
        lines=matching.lines,
        tax=matching.tax,
        bank_account=matching.bank_account,
        injection=matching.injection,
        policy=policy_step,
        reasons=reasons,
        allowed_routes=allowed,
        injection_awaiting_review=matching.injection_awaiting_review,
        default_route=default_route(allowed),
        bill_plan=plan,
        notes=tuple(f.message for f in every if f.is_note),
    )


def decide_step(
    invoice: InvoiceData,
    arithmetic: StepResult,
    identification: Identification,
    matching: Matching,
    reader: ErpReader,
    policy: PolicyConfig,
) -> AnalysisResult:
    """Threshold, new vendor, allowed and default routes, and the bill plan."""
    vendor_ident, po_ident = identification.vendor, identification.purchase_order
    vendor = vendor_ident.vendor if vendor_ident.identified else None
    posted = reader.posted_bill_count(vendor.id) if vendor is not None else None
    policy_step = StepResult(
        findings=tuple(
            f
            for f in (check_threshold(invoice, policy), check_new_vendor(vendor_ident, posted))
            if f is not None
        )
    )
    plan = None
    if vendor is not None and po_ident.established and po_ident.purchase_order is not None:
        plan = plan_bill(
            invoice, vendor, po_ident.purchase_order, matching.lines, matching.accounts
        )
    return _result(
        extraction=StepResult(ran=False),
        arithmetic=arithmetic,
        identification=identification,
        matching=matching,
        policy_step=policy_step,
        plan=plan,
    )


def unreadable_analysis(
    finding: Finding,
    fragments: Sequence[TextFragment],
    injection_review: InjectionReview | None,
) -> AnalysisResult:
    """The analysis of a document that could not be read (BR-04): only its free text is checked."""
    injection, awaiting = judge_step(None, fragments, injection_review)
    return _result(
        extraction=StepResult(findings=(finding,)),
        arithmetic=StepResult(ran=False),
        identification=Identification(
            vendor=VendorIdentification(status=VendorStatus.UNREAD),
            purchase_order=PoIdentification(status=PoStatus.SKIPPED),
        ),
        matching=Matching(
            duplicates=DuplicateResult(ran=False),
            lines=LinesResult(ran=False),
            tax=StepResult(ran=False),
            bank_account=StepResult(ran=False),
            injection=injection,
            injection_awaiting_review=awaiting,
        ),
        policy_step=StepResult(ran=False),
        plan=None,
    )


def analyse(
    invoice: InvoiceData,
    intake: IntakeContext,
    fragments: Sequence[TextFragment],
    reader: ErpReader,
    policy: PolicyConfig,
    *,
    registry: InvoiceRegistry | None = None,
    po_number_from_reply: str | None = None,
    injection_review: InjectionReview | None = None,
) -> AnalysisResult:
    """Run every rule on one invoice and compute reasons, routes, and the bill plan.

    ``fragments`` are the free texts around the invoice (hidden text, PDF text layer, e-mail, ...);
    the invoice's own notes and line descriptions are added here. ``po_number_from_reply`` is a PO
    number the vendor gave in a text reply (F-06); BR-06 then looks that number up.
    ``injection_review`` is the layer-2 answer of BR-15 for exactly ``document_fragments(invoice,
    fragments)``; ``None`` means layer 2 was not run on purpose (oracle, rule tests), and weak
    patterns then count as injection. The steps are the ones the graph nodes call one by one.
    """
    arithmetic = validate_step(invoice, policy)
    identification = identify_step(
        invoice, intake, reader, policy, po_number_from_reply=po_number_from_reply
    )
    matching = match_step(
        invoice,
        intake,
        fragments,
        identification,
        reader,
        policy,
        registry=registry,
        injection_review=injection_review,
    )
    return decide_step(invoice, arithmetic, identification, matching, reader, policy)
