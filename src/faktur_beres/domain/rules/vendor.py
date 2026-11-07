"""BR-05: vendor identification, in order (SPEC 0.3).

1. A valid seller NPWP (BR-10) is matched exactly, as digits, with the vendors' NPWP in Odoo.
2. Without an NPWP match, candidates come from the seller name (equal after BR-05 normalisation)
   and from the e-mail: the sender of the e-mail channel or the seller e-mail printed on the
   document, equal to a vendor's billing e-mail.

Zero candidates: the vendor is not registered (``new_vendor`` is reported by BR-14). More than one:
``identity_mismatch``. An NPWP that points to vendor A while the name or e-mail points to vendor B:
``identity_mismatch``. Only a vendor identified uniquely lets the vendor-dependent checks run
(BR-06 to BR-09, BR-11, BR-12).

The reader supplies candidates; this function filters them again with the same normalisation, so
a loose ERP search can never widen the match.
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.erp import VendorRecord
from faktur_beres.domain.findings import Finding, VendorEvidence
from faktur_beres.domain.invoice import Channel, IntakeContext, InvoiceData
from faktur_beres.domain.normalize import normalize_email, normalize_name, tax_id_digits
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.tax_ids import valid_npwp_digits

RULE = "BR-05"


class VendorCandidates(DomainModel):
    """What the ERP returned for the NPWP, the name, and the e-mail addresses of an invoice."""

    by_npwp: tuple[VendorRecord, ...] = ()
    by_name: tuple[VendorRecord, ...] = ()
    by_email: tuple[VendorRecord, ...] = ()


class VendorStatus(StrEnum):
    IDENTIFIED = "identified"
    NOT_REGISTERED = "not_registered"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"


class VendorIdentification(DomainModel):
    status: VendorStatus
    vendor: VendorRecord | None = None
    method: Literal["npwp", "name_email"] | None = None
    findings: tuple[Finding, ...] = ()

    @property
    def identified(self) -> bool:
        return self.status == VendorStatus.IDENTIFIED


def invoice_emails(invoice: InvoiceData, intake: IntakeContext) -> tuple[str, ...]:
    """The sender (e-mail channel only) and the printed seller e-mail, normalised."""
    raw = [intake.sender_email if intake.channel == Channel.EMAIL else None, invoice.seller.email]
    found: list[str] = []
    for value in raw:
        address = normalize_email(value)
        if address and address not in found:
            found.append(address)
    return tuple(found)


def _unique(vendors: Iterable[VendorRecord]) -> tuple[VendorRecord, ...]:
    seen: dict[int, VendorRecord] = {}
    for vendor in vendors:
        seen.setdefault(vendor.id, vendor)
    return tuple(seen.values())


def identify_vendor(
    invoice: InvoiceData, intake: IntakeContext, candidates: VendorCandidates
) -> VendorIdentification:
    npwp = valid_npwp_digits(invoice.seller.npwp)
    name = normalize_name(invoice.seller.name)
    emails = invoice_emails(invoice, intake)

    by_npwp = _unique(
        v for v in candidates.by_npwp if npwp is not None and tax_id_digits(v.vat) == npwp
    )
    by_name = _unique(v for v in candidates.by_name if normalize_name(v.name) == name)
    by_email = _unique(v for v in candidates.by_email if normalize_email(v.email) in emails)
    evidence = VendorEvidence(
        npwp_digits=tax_id_digits(invoice.seller.npwp),
        npwp_valid=npwp is not None,
        by_npwp=tuple(v.id for v in by_npwp),
        by_name=tuple(v.id for v in by_name),
        by_email=tuple(v.id for v in by_email),
        emails=emails,
    )

    def mismatch(status: VendorStatus, message: str) -> VendorIdentification:
        return VendorIdentification(
            status=status,
            findings=(
                Finding(
                    rule=RULE,
                    reason=ReasonCode.IDENTITY_MISMATCH,
                    message=message,
                    evidence=evidence,
                ),
            ),
        )

    if by_npwp:
        if len(by_npwp) > 1:
            return mismatch(
                VendorStatus.AMBIGUOUS, "NPWP penjual terdaftar untuk lebih dari satu vendor."
            )
        vendor = by_npwp[0]
        others = _unique(v for v in (*by_name, *by_email) if v.id != vendor.id)
        if others:
            names = ", ".join(v.name for v in others)
            return mismatch(
                VendorStatus.CONFLICT,
                f"NPWP penjual milik {vendor.name}, tetapi nama atau email menunjuk {names}.",
            )
        return VendorIdentification(status=VendorStatus.IDENTIFIED, vendor=vendor, method="npwp")

    pool = _unique((*by_name, *by_email))
    if not pool:
        return VendorIdentification(
            status=VendorStatus.NOT_REGISTERED,
            findings=(
                Finding(
                    rule=RULE,
                    message=(
                        f"Penjual {invoice.seller.name} tidak terdaftar sebagai vendor di Odoo; "
                        "vendor dan rekeningnya belum terverifikasi."
                    ),
                    evidence=evidence,
                ),
            ),
        )
    if len(pool) > 1:
        names = ", ".join(v.name for v in pool)
        return mismatch(
            VendorStatus.AMBIGUOUS,
            f"Nama dan email penjual menunjuk lebih dari satu vendor: {names}.",
        )
    return VendorIdentification(status=VendorStatus.IDENTIFIED, vendor=pool[0], method="name_email")
