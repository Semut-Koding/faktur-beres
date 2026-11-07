"""match_engine and tax_check: thin tool wrappers over the M2.4 rules."""

from datetime import date
from decimal import Decimal

import pytest

from factories import COMPANY, POLICY, VENDOR, VENDOR_ACCOUNT, bill, invoice, line, po, po_line
from faktur_beres.domain.erp import BillLineRecord
from faktur_beres.domain.invoice import Seller
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.vendor import VendorIdentification, VendorStatus
from faktur_beres.tools.match_engine import MatchRequest, MatchResult, match_engine
from faktur_beres.tools.tax_check import TaxCheckRequest, TaxCheckResult, tax_check


@pytest.mark.aturan("BR-09")
def test_match_engine_combines_duplicates_lines_and_bank() -> None:
    inv = invoice(
        [line(160, 165000)], invoice_number="RPK/1026/031", invoice_date=date(2026, 10, 23)
    )
    comparator = bill(
        ref="RPK/1026/031",
        invoice_date=date(2026, 10, 23),
        total=27472500,
        lines=[
            BillLineRecord(
                id=1, purchase_line_id=1, quantity=Decimal(150), price_unit=Decimal(165000)
            )
        ],
    )
    request = MatchRequest(
        invoice=inv,
        vendor=VENDOR,
        purchase_order=po([po_line(qty=150, invoiced=150, price=165000)]),
        vendor_bills=(comparator,),
        bank_accounts=(VENDOR_ACCOUNT,),
    )
    result = match_engine(request, POLICY)
    assert result.reasons == (ReasonCode.QUANTITY_VARIANCE, ReasonCode.DUPLICATE_SUSPECTED)
    excess = [f.evidence.excess for f in result.lines.findings if f.evidence]
    assert excess == [10]  # 160 against 150 received, the duplicate's 150 not counted
    assert result.bill_plan is not None
    assert MatchResult.model_validate_json(result.model_dump_json()) == result


def test_match_engine_without_po_matches_no_lines() -> None:
    result = match_engine(MatchRequest(invoice=invoice(), vendor=VENDOR), POLICY)
    assert not result.lines.ran and result.bill_plan is None


@pytest.mark.aturan("BR-10")
def test_tax_check_tool() -> None:
    inv = invoice(nsfp=None, seller=Seller(name=VENDOR.name, npwp="0990992125111000"))
    ident = VendorIdentification(status=VendorStatus.IDENTIFIED, vendor=VENDOR, method="name_email")
    result = tax_check(TaxCheckRequest(invoice=inv, company=COMPANY, vendor=ident), POLICY)
    assert result.reasons == (ReasonCode.TAX_INVALID, ReasonCode.IDENTITY_MISMATCH)
    assert TaxCheckResult.model_validate_json(result.model_dump_json()) == result
    clean = tax_check(TaxCheckRequest(invoice=invoice(), company=COMPANY), POLICY)
    assert clean.findings == () and clean.notes == ()
