"""``match_engine`` (SPEC §5.2): three-way match, duplicates, and bank account, as one tool.

A thin wrapper over the M2.4 rules with Pydantic input and output, so that the ``match`` node of
M4 can call it as a tool. It reads nothing itself: the caller passes the ERP records it fetched
with ``odoo_tools``. No LLM and no Odoo call happen here.
"""

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.erp import (
    BankAccountRecord,
    BillRecord,
    PurchaseOrderRecord,
    RegisteredInvoice,
    VendorRecord,
)
from faktur_beres.domain.findings import Finding, reasons_of
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.bank import check_bank_account
from faktur_beres.domain.rules.bill_plan import BillPlan, plan_bill
from faktur_beres.domain.rules.duplicates import DuplicateResult, find_duplicates
from faktur_beres.domain.rules.lines import LinesResult, match_lines


class MatchRequest(DomainModel):
    invoice: InvoiceData
    vendor: VendorRecord
    purchase_order: PurchaseOrderRecord | None = None  # the PO established by BR-06, if any
    vendor_bills: tuple[BillRecord, ...] = ()
    other_invoices: tuple[RegisteredInvoice, ...] = ()
    bank_accounts: tuple[BankAccountRecord, ...] = ()
    account_owners: tuple[BankAccountRecord, ...] = ()


class MatchResult(DomainModel):
    duplicates: DuplicateResult
    lines: LinesResult
    bank_account: tuple[Finding, ...]
    reasons: tuple[ReasonCode, ...]
    bill_plan: BillPlan | None


def match_engine(request: MatchRequest, policy: PolicyConfig) -> MatchResult:
    """BR-11, then BR-07 to BR-09 (without the duplicates' quantities), BR-12, and BR-18."""
    inv, vendor = request.invoice, request.vendor
    duplicates = find_duplicates(
        inv, vendor.id, request.vendor_bills, request.other_invoices, policy
    )
    po = request.purchase_order
    lines = (
        match_lines(inv, po, policy, excluded_by_line=duplicates.excluded_quantities())
        if po is not None
        else LinesResult(ran=False)
    )
    bank = check_bank_account(inv, vendor, request.bank_accounts, request.account_owners)
    plan = plan_bill(inv, vendor, po, lines, request.bank_accounts) if po is not None else None
    return MatchResult(
        duplicates=duplicates,
        lines=lines,
        bank_account=bank,
        reasons=reasons_of((*duplicates.findings, *lines.findings, *bank)),
        bill_plan=plan,
    )
