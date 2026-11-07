"""BR-13 approval threshold and BR-14 new vendor."""

from decimal import Decimal

import pytest

from factories import POLICY, VENDOR, invoice
from faktur_beres.domain.findings import NewVendorEvidence, ThresholdEvidence
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.policy_checks import check_new_vendor, check_threshold
from faktur_beres.domain.rules.vendor import VendorIdentification, VendorStatus


def with_total(total: int):
    return invoice().model_copy(update={"total": Decimal(total)})


@pytest.mark.aturan("BR-13")
@pytest.mark.parametrize(
    ("total", "triggers"),
    [(49_999_999, False), (50_000_000, False), (50_000_001, True), (62_770_500, True)],
)
def test_threshold_is_strictly_more_than(total: int, triggers: bool) -> None:
    finding = check_threshold(with_total(total), POLICY)
    assert (finding is not None) is triggers
    if finding is not None:
        assert finding.reason == ReasonCode.OVER_THRESHOLD
        assert isinstance(finding.evidence, ThresholdEvidence)
        assert finding.evidence.threshold == 50_000_000


@pytest.mark.aturan("BR-14")
def test_vendor_without_posted_bills_is_new() -> None:
    ident = VendorIdentification(status=VendorStatus.IDENTIFIED, vendor=VENDOR, method="npwp")
    finding = check_new_vendor(ident, 0)
    assert finding is not None and finding.reason == ReasonCode.NEW_VENDOR
    assert isinstance(finding.evidence, NewVendorEvidence) and finding.evidence.registered
    assert check_new_vendor(ident, 3) is None


@pytest.mark.aturan("BR-14")
def test_unregistered_vendor_is_new() -> None:
    finding = check_new_vendor(VendorIdentification(status=VendorStatus.NOT_REGISTERED), None)
    assert finding is not None and finding.reason == ReasonCode.NEW_VENDOR
    assert not finding.evidence.registered


@pytest.mark.aturan("BR-14")
def test_ambiguous_vendor_is_not_judged_here() -> None:
    assert check_new_vendor(VendorIdentification(status=VendorStatus.AMBIGUOUS), None) is None
