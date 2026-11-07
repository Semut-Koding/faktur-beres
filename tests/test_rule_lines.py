"""BR-07 (mapping), BR-08 (price), and BR-09 (quantity) against the established PO."""

from decimal import Decimal

import pytest

from factories import POLICY, invoice, line, po, po_line
from faktur_beres.domain.findings import LineMatchEvidence, PriceEvidence, QuantityEvidence
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.lines import (
    check_price,
    check_quantity,
    map_line,
    match_lines,
    price_limit,
)

SECOND = po_line(2, 502, code="INT-02", vendor_codes=("KD-02",), name="Barang Uji Dua")


# -- BR-07 ----------------------------------------------------------------------------------------


@pytest.mark.aturan("BR-07")
@pytest.mark.parametrize(
    ("code", "description", "method"),
    [
        ("KD-01", "apa saja", "vendor_code"),
        ("kd 01", "apa saja", "vendor_code"),
        ("INT-01", "apa saja", "internal_code"),
        (None, "Barang Uji Satu", "name"),
        (None, "barang uji satu (KD-01)", "name"),
        ("ZZ-99", "[INT-01] Barang Uji Satu", "name"),
    ],
)
def test_mapping_order_vendor_code_internal_code_name(
    code: str | None, description: str, method: str
) -> None:
    match = map_line(1, line(code=code, description=description), po([po_line(), SECOND]))
    assert (match.status, match.method, match.po_line_id) == ("matched", method, 1)


@pytest.mark.aturan("BR-07")
def test_vendor_code_wins_over_a_name_that_points_elsewhere() -> None:
    match = map_line(1, line(code="KD-02", description="Barang Uji Satu"), po([po_line(), SECOND]))
    assert match.po_line_id == 2


@pytest.mark.aturan("BR-07")
def test_unknown_line_has_no_match() -> None:
    match = map_line(1, line(1, 450000, code="KRM-01", description="Ongkos kirim"), po())
    assert match.status == "no_match"


@pytest.mark.aturan("BR-07")
def test_product_on_two_po_lines_is_never_picked() -> None:
    twice = po([po_line(1), po_line(3, qty=50)])
    match = map_line(1, line(), twice)
    assert (match.status, match.candidate_line_ids) == ("ambiguous", (1, 3))
    result = match_lines(invoice([line()]), twice, POLICY)
    (finding,) = result.findings
    assert finding.reason == ReasonCode.QUANTITY_VARIANCE
    assert isinstance(finding.evidence, LineMatchEvidence)
    assert finding.evidence.problem == "ambiguous"
    assert not result.all_matched


# -- BR-08 ----------------------------------------------------------------------------------------


@pytest.mark.aturan("BR-08")
def test_price_exactly_one_percent_above_passes_with_note() -> None:
    pl = po_line(price=185000)
    assert price_limit(Decimal(185000), POLICY) == Decimal("186850")
    finding = check_price(1, line(price=186850), pl, POLICY)
    assert finding is not None and finding.is_note
    assert "dalam toleransi" in finding.message


@pytest.mark.aturan("BR-08")
def test_price_one_rupiah_above_the_limit_is_price_variance() -> None:
    finding = check_price(1, line(price=186851), po_line(price=185000), POLICY)
    assert finding is not None and finding.reason == ReasonCode.PRICE_VARIANCE


@pytest.mark.aturan("BR-08")
def test_price_variance_evidence() -> None:
    finding = check_price(2, line(price=60840), po_line(price=58500, name="Perekat"), POLICY)
    assert finding is not None
    ev = finding.evidence
    assert isinstance(ev, PriceEvidence)
    assert (ev.invoice_price, ev.po_price, ev.difference) == (60840, 58500, 2340)
    assert ev.difference_percent == Decimal("4.00")
    assert "+4,00%" in finding.message


@pytest.mark.aturan("BR-08")
def test_lower_price_passes_with_note_and_equal_price_says_nothing() -> None:
    note = check_price(1, line(price=76000), po_line(price=78000), POLICY)
    assert note is not None and note.is_note and "lebih rendah" in note.message
    assert check_price(1, line(price=78000), po_line(price=78000), POLICY) is None


# -- BR-09 ----------------------------------------------------------------------------------------


@pytest.mark.aturan("BR-09")
def test_quantity_equal_to_open_quantity_passes() -> None:
    assert check_quantity(1, line(840), po_line(qty=1050, received=840), POLICY) is None


@pytest.mark.aturan("BR-09")
def test_quantity_above_received_not_invoiced_is_variance() -> None:
    finding = check_quantity(1, line(2100), po_line(qty=2100, received=1680), POLICY)
    assert finding is not None and finding.reason == ReasonCode.QUANTITY_VARIANCE
    ev = finding.evidence
    assert isinstance(ev, QuantityEvidence)
    assert (ev.available, ev.excess, ev.problem) == (1680, 420, "exceeds_received")


@pytest.mark.aturan("BR-09")
def test_one_unit_more_than_open_is_variance() -> None:
    assert check_quantity(1, line(841), po_line(qty=1050, received=840), POLICY) is not None


@pytest.mark.aturan("BR-09")
def test_already_invoiced_quantity_counts() -> None:
    pl = po_line(qty=150, received=150, invoiced=150)
    assert check_quantity(1, line(150), pl, POLICY) is not None
    # A duplicate's comparator bill (BR-11) is not counted as invoiced.
    assert check_quantity(1, line(150), pl, POLICY, excluded=Decimal(150)) is None
    finding = check_quantity(1, line(160), pl, POLICY, excluded=Decimal(150))
    assert finding is not None
    assert isinstance(finding.evidence, QuantityEvidence)
    assert finding.evidence.excess == 10


@pytest.mark.aturan("BR-09")
def test_quantity_above_ordered_is_variance() -> None:
    finding = check_quantity(1, line(110), po_line(qty=100, received=120), POLICY)
    assert finding is not None
    assert isinstance(finding.evidence, QuantityEvidence)
    assert finding.evidence.problem == "exceeds_ordered"


@pytest.mark.aturan("BR-09")
def test_lines_on_the_same_po_line_add_up() -> None:
    result = match_lines(invoice([line(60), line(50)]), po([po_line(qty=100)]), POLICY)
    assert [f.evidence.line for f in result.findings if f.evidence] == [2]


@pytest.mark.aturan("BR-09")
def test_line_outside_the_po_is_quantity_variance() -> None:
    inv = invoice([line(3, 1250000), line(1, 450000, code="KRM-01", description="Ongkos kirim")])
    result = match_lines(inv, po([po_line(qty=3, price=1250000)]), POLICY)
    (finding,) = result.findings
    assert finding.reason == ReasonCode.QUANTITY_VARIANCE
    assert isinstance(finding.evidence, LineMatchEvidence)
    assert finding.evidence.problem == "no_match"
    assert [m.status for m in result.matches] == ["matched", "no_match"]


@pytest.mark.aturan("BR-09")
def test_printed_units_are_not_converted() -> None:
    result = match_lines(invoice([line(840, unit="KGM")]), po([po_line(qty=840)]), POLICY)
    assert result.findings == () and result.all_matched
