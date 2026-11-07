"""BR-06: PO identification and the strong-candidate search."""

import pytest

from factories import OTHER_VENDOR, POLICY, VENDOR, invoice, line, po, po_line
from faktur_beres.domain.erp import PoState
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.purchase_order import (
    PoStatus,
    identify_purchase_order,
    is_strong_candidate,
)

pytestmark = pytest.mark.aturan("BR-06")


def identify(inv=None, *, number="P00900", found=None, confirmed=(), from_reply=False):
    return identify_purchase_order(
        inv or invoice(),
        VENDOR,
        POLICY,
        number=number,
        found=found,
        confirmed=confirmed,
        from_reply=from_reply,
    )


@pytest.mark.parametrize("locked", [False, True])
def test_printed_confirmed_po_of_the_vendor_is_used(locked: bool) -> None:
    result = identify(found=po(locked=locked))
    assert result.status == PoStatus.PRINTED and result.established
    assert result.findings == ()


def test_number_is_normalised() -> None:
    assert identify(number=" p00900 ", found=po()).status == PoStatus.PRINTED


def test_lookup_for_another_number_is_ignored() -> None:
    result = identify(number="P00901", found=po(number="P00900"))
    assert result.status == PoStatus.NOT_FOUND


def test_po_of_another_vendor_is_identity_mismatch_and_not_established() -> None:
    result = identify(found=po(vendor_id=OTHER_VENDOR.id))
    assert result.status == PoStatus.OTHER_VENDOR and not result.established
    assert [f.reason for f in result.findings] == [ReasonCode.IDENTITY_MISMATCH]


@pytest.mark.parametrize("state", [PoState.CANCEL, PoState.DRAFT, PoState.SENT, PoState.TO_APPROVE])
def test_cancelled_or_unconfirmed_po_is_unresolved_without_moving(state: PoState) -> None:
    other = po(po_id=901, number="P00901")
    result = identify(found=po(state=state), confirmed=[other])
    assert result.status == PoStatus.NOT_CONFIRMED and not result.established
    assert [f.reason for f in result.findings] == [ReasonCode.PO_UNRESOLVED]


def test_mistyped_number_with_one_strong_candidate_is_corrected() -> None:
    inv = invoice([line(240, 32000)], po_number="P09330")
    candidates = [
        po([po_line(qty=300, price=32000)], po_id=1, number="P00024"),  # open 300, not 240
        po([po_line(qty=240, price=32000)], po_id=2, number="P00033"),
        po([po_line(qty=210, price=32000)], po_id=3, number="P00045"),
    ]
    result = identify(inv, number="P09330", confirmed=candidates)
    assert result.status == PoStatus.CORRECTED
    assert result.purchase_order is not None and result.purchase_order.number == "P00033"
    assert result.findings[0].is_note and "dikoreksi" in result.findings[0].message


def test_two_identical_candidates_are_unresolved() -> None:
    inv = invoice([line(45, 98000)], po_number=None)
    twins = [
        po([po_line(qty=45, price=98000)], po_id=1, number="P00035"),
        po([po_line(qty=45, price=98000)], po_id=2, number="P00036"),
    ]
    result = identify(inv, number=None, confirmed=twins)
    assert result.status == PoStatus.AMBIGUOUS
    assert result.candidates == ("P00035", "P00036")
    assert [f.reason for f in result.findings] == [ReasonCode.PO_UNRESOLVED]


def test_one_candidate_for_an_invoice_without_number() -> None:
    inv = invoice([line(45, 98000)], po_number=None)
    result = identify(inv, number=None, confirmed=[po([po_line(qty=45, price=98000)])])
    assert result.status == PoStatus.CANDIDATE and result.established


def test_strong_candidate_is_strict() -> None:
    exact = invoice([line(240, 32000)])
    assert is_strong_candidate(exact, po([po_line(qty=240, price=32000)]), POLICY)
    # quantity must equal the open quantity, not just fit
    assert not is_strong_candidate(exact, po([po_line(qty=300, price=32000)]), POLICY)
    # open quantity 240 after an earlier bill of 60 is equal again
    assert is_strong_candidate(
        exact, po([po_line(qty=300, received=300, invoiced=60, price=32000)]), POLICY
    )
    # price within tolerance (+1%), cancelled POs never count
    assert is_strong_candidate(
        invoice([line(240, 32320)]), po([po_line(qty=240, price=32000)]), POLICY
    )
    assert not is_strong_candidate(
        invoice([line(240, 32321)]), po([po_line(qty=240, price=32000)]), POLICY
    )
    assert not is_strong_candidate(
        exact, po([po_line(qty=240, price=32000)], state=PoState.CANCEL), POLICY
    )


def test_candidates_of_other_vendors_are_ignored() -> None:
    inv = invoice([line(240, 32000)], po_number=None)
    other = po([po_line(qty=240, price=32000)], vendor_id=OTHER_VENDOR.id)
    assert identify(inv, number=None, confirmed=[other]).status == PoStatus.NOT_FOUND


def test_number_from_the_vendor_reply_is_noted() -> None:
    result = identify(number="P00900", found=po(), from_reply=True)
    assert result.status == PoStatus.PRINTED
    assert "balasan vendor" in result.findings[0].message
