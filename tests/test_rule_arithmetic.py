"""BR-03: arithmetic of the document."""

import pytest

from factories import POLICY, invoice, line
from faktur_beres.domain.findings import DocumentSumEvidence, LineAmountEvidence
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.arithmetic import check_arithmetic

pytestmark = pytest.mark.aturan("BR-03")


def test_consistent_document_passes() -> None:
    inv = invoice([line(40, 135000), line(30, 98000, code="KD-02", description="Barang Dua")])
    assert check_arithmetic(inv, POLICY) == ()


def test_wrong_line_amount_is_arithmetic_error() -> None:
    # Printed line amount 3.240.000 while 30 x 98.000 = 2.940.000; subtotal adds up the
    # printed amounts, so only the line itself is wrong.
    inv = invoice([line(40, 135000), line(30, 98000, amount=3240000)])
    (finding,) = check_arithmetic(inv, POLICY)
    assert finding.reason == ReasonCode.ARITHMETIC_ERROR
    ev = finding.evidence
    assert isinstance(ev, LineAmountEvidence)
    assert (ev.line, ev.computed, ev.printed, ev.difference) == (2, 2940000, 3240000, 300000)
    assert "Baris 2" in finding.message


@pytest.mark.parametrize(("off", "triggers"), [(1, False), (-1, False), (2, True)])
def test_line_tolerance_is_one_rupiah(off: int, triggers: bool) -> None:
    inv = invoice([line(3, 1250000, amount=3750000 + off)])
    assert bool(check_arithmetic(inv, POLICY)) is triggers


def test_subtotal_tolerance_is_one_rupiah_per_line() -> None:
    lines = [line(1, 1000), line(1, 2000, code="KD-02", description="Dua")]
    assert check_arithmetic(invoice(lines, subtotal=3002, vat=330, total=3332), POLICY) == ()
    (finding,) = check_arithmetic(invoice(lines, subtotal=3003, vat=330, total=3333), POLICY)
    assert isinstance(finding.evidence, DocumentSumEvidence)
    assert finding.evidence.subject == "subtotal"


@pytest.mark.parametrize(("off", "triggers"), [(1, False), (2, True), (-2, True)])
def test_total_must_equal_subtotal_plus_vat(off: int, triggers: bool) -> None:
    inv = invoice()
    changed = invoice(total=inv.total + off)
    findings = check_arithmetic(changed, POLICY)
    assert bool(findings) is triggers
    if triggers:
        assert isinstance(findings[0].evidence, DocumentSumEvidence)
        assert findings[0].evidence.subject == "total"


def test_tax_rule_is_not_checked_here() -> None:
    # PPN 12% x harga jual instead of 12% x 11/12 is a BR-10 matter; the sum still adds up.
    inv = invoice([line(50, 165000)], vat=990000, total=9240000, dpp_nilai_lain=8250000)
    assert check_arithmetic(inv, POLICY) == ()
