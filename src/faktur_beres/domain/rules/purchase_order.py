"""BR-06: PO identification (SPEC 0.3).

The printed PO number is normalised (upper case, no spaces) and looked up exactly:

* found, the vendor's, and confirmed (state ``purchase``, locked or not): used;
* another vendor's: ``identity_mismatch``;
* cancelled or not yet confirmed (RFQ): ``po_unresolved``, and the invoice is never moved to
  another PO;
* absent or not found: look for a **strong candidate**, a confirmed PO of the vendor on which every
  invoice line has a product counterpart (BR-07) with quantity **equal to** the received but not
  invoiced quantity and a price within the tolerance (BR-08). Exactly one: used, with the note
  "nomor PO dikoreksi". None or several: ``po_unresolved``. This is deliberately stricter than
  BR-09, because the PO is chosen without a number to go by.

Lines are matched (BR-07 to BR-09) only once the PO is established; other checks still run.
"""

from collections.abc import Sequence
from enum import StrEnum

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.erp import PurchaseOrderRecord, VendorRecord
from faktur_beres.domain.findings import Finding, PurchaseOrderEvidence
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.normalize import normalize_po_number
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.lines import map_line, price_within_tolerance

RULE = "BR-06"


class PoStatus(StrEnum):
    PRINTED = "printed"
    CORRECTED = "corrected"
    CANDIDATE = "candidate"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    OTHER_VENDOR = "other_vendor"
    NOT_CONFIRMED = "not_confirmed"
    SKIPPED = "skipped"


ESTABLISHED = frozenset({PoStatus.PRINTED, PoStatus.CORRECTED, PoStatus.CANDIDATE})


class PoIdentification(DomainModel):
    status: PoStatus
    number: str | None = None  # the number looked up, normalised
    purchase_order: PurchaseOrderRecord | None = None  # set only when the PO is established
    candidates: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()

    @property
    def established(self) -> bool:
        return self.status in ESTABLISHED and self.purchase_order is not None


def is_strong_candidate(
    invoice: InvoiceData, po: PurchaseOrderRecord, policy: PolicyConfig
) -> bool:
    """Every invoice line matches its own PO line exactly on open quantity, price in tolerance."""
    if not po.confirmed:
        return False
    by_id = {pl.id: pl for pl in po.lines}
    seen: set[int] = set()
    for index, line in enumerate(invoice.lines, start=1):
        match = map_line(index, line, po)
        if match.status != "matched" or match.po_line_id is None or match.po_line_id in seen:
            return False
        seen.add(match.po_line_id)
        po_line = by_id[match.po_line_id]
        if line.quantity != po_line.open_quantity:
            return False
        if not price_within_tolerance(line.unit_price, po_line.price_unit, policy):
            return False
    return True


def identify_purchase_order(
    invoice: InvoiceData,
    vendor: VendorRecord,
    policy: PolicyConfig,
    *,
    number: str | None,
    found: PurchaseOrderRecord | None,
    confirmed: Sequence[PurchaseOrderRecord],
    from_reply: bool = False,
) -> PoIdentification:
    """BR-06 for an identified vendor.

    ``number`` is the printed PO number (or the one the vendor's reply gave), ``found`` the PO
    the ERP has under that number, and ``confirmed`` the vendor's confirmed POs for the candidate
    search.
    """
    wanted = normalize_po_number(number) if number else None
    if found is not None and (wanted is None or normalize_po_number(found.number) != wanted):
        found = None  # never trust a lookup that does not match the number exactly
    evidence = PurchaseOrderEvidence(
        printed=number,
        found=found.number if found else None,
        state=found.state.value if found else None,
        owner_vendor_id=found.vendor_id if found else None,
    )
    source = "dari balasan vendor" if from_reply else "tercetak"

    if found is not None:
        if found.vendor_id != vendor.id:
            return PoIdentification(
                status=PoStatus.OTHER_VENDOR,
                number=wanted,
                findings=(
                    Finding(
                        rule=RULE,
                        reason=ReasonCode.IDENTITY_MISMATCH,
                        message=f"PO {found.number} ({source}) milik vendor lain.",
                        evidence=evidence,
                    ),
                ),
            )
        if not found.confirmed:
            state = "batal" if found.state.value == "cancel" else "belum dikonfirmasi (RFQ)"
            return PoIdentification(
                status=PoStatus.NOT_CONFIRMED,
                number=wanted,
                findings=(
                    Finding(
                        rule=RULE,
                        reason=ReasonCode.PO_UNRESOLVED,
                        message=f"PO {found.number} ({source}) berstatus {state}.",
                        evidence=evidence,
                    ),
                ),
            )
        notes = (
            (Finding(rule=RULE, message=f"Nomor PO {found.number} dari balasan vendor."),)
            if from_reply
            else ()
        )
        return PoIdentification(
            status=PoStatus.PRINTED, number=wanted, purchase_order=found, findings=notes
        )

    strong = sorted(
        (
            po
            for po in confirmed
            if po.vendor_id == vendor.id and is_strong_candidate(invoice, po, policy)
        ),
        key=lambda po: po.number,
    )
    names = tuple(po.number for po in strong)
    evidence = evidence.model_copy(update={"candidates": names})
    if len(strong) == 1:
        chosen = strong[0]
        if wanted:
            message = f"Nomor PO dikoreksi: tercetak {number}, dipakai {chosen.number}."
            status = PoStatus.CORRECTED
        else:
            message = f"Faktur tanpa nomor PO; satu kandidat kuat: {chosen.number}."
            status = PoStatus.CANDIDATE
        return PoIdentification(
            status=status,
            number=wanted,
            purchase_order=chosen,
            candidates=names,
            findings=(Finding(rule=RULE, message=message, evidence=evidence),),
        )
    if strong:
        message = f"Lebih dari satu kandidat PO: {', '.join(names)}."
        status = PoStatus.AMBIGUOUS
    else:
        message = (
            f"PO {number} tidak ditemukan dan tidak ada kandidat kuat."
            if wanted
            else "Faktur tanpa nomor PO dan tidak ada kandidat kuat."
        )
        status = PoStatus.NOT_FOUND
    return PoIdentification(
        status=status,
        number=wanted,
        candidates=names,
        findings=(
            Finding(rule=RULE, reason=ReasonCode.PO_UNRESOLVED, message=message, evidence=evidence),
        ),
    )
