"""BR-13 approval threshold and BR-14 new vendor.

* BR-13: a total including PPN **above** the threshold (Rp50.000.000 by default) is
  ``over_threshold`` (C1); a total of exactly the threshold is not.
* BR-14: a vendor without posted vendor bills in Odoo, or a vendor that is not registered at all,
  is ``new_vendor`` (C2). This is inferred from Odoo data, never from the dataset.
"""

from faktur_beres.domain.findings import Finding, NewVendorEvidence, ThresholdEvidence, rupiah
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.vendor import VendorIdentification, VendorStatus

RULE_THRESHOLD = "BR-13"
RULE_NEW_VENDOR = "BR-14"


def check_threshold(invoice: InvoiceData, policy: PolicyConfig) -> Finding | None:
    threshold = policy.approval_threshold
    if invoice.total <= threshold:
        return None
    return Finding(
        rule=RULE_THRESHOLD,
        reason=ReasonCode.OVER_THRESHOLD,
        message=(f"Total {rupiah(invoice.total)} di atas ambang persetujuan {rupiah(threshold)}."),
        evidence=ThresholdEvidence(total=invoice.total, threshold=threshold),
    )


def check_new_vendor(
    identification: VendorIdentification, posted_bills: int | None
) -> Finding | None:
    """``posted_bills`` is the vendor's number of posted bills (``None`` when not looked up)."""
    if identification.status == VendorStatus.NOT_REGISTERED:
        return Finding(
            rule=RULE_NEW_VENDOR,
            reason=ReasonCode.NEW_VENDOR,
            message="Vendor tidak terdaftar di Odoo.",
            evidence=NewVendorEvidence(registered=False, posted_bills=None),
        )
    vendor = identification.vendor
    if identification.identified and vendor is not None and posted_bills == 0:
        return Finding(
            rule=RULE_NEW_VENDOR,
            reason=ReasonCode.NEW_VENDOR,
            message=f"{vendor.name} belum punya tagihan vendor terposting di Odoo.",
            evidence=NewVendorEvidence(registered=True, posted_bills=0),
        )
    return None
