"""BR-12: the bank account printed on the invoice.

The printed number is normalised to digits and compared with the vendor's accounts in Odoo. A
number that is not registered for the vendor, that belongs to someone else, or that is registered
but printed with another bank is ``bank_account_mismatch`` (C3). An invoice without an account is
no exception: payment always uses the ERP data. Messages show only the last four digits (BR-24);
the evidence keeps the digits for the approver.
"""

from collections.abc import Sequence

from faktur_beres.domain.erp import BankAccountRecord, VendorRecord
from faktur_beres.domain.findings import BankAccountEvidence, Finding
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.normalize import account_digits, normalize_name
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.safety import mask_account

RULE = "BR-12"


def check_bank_account(
    invoice: InvoiceData,
    vendor: VendorRecord,
    accounts: Sequence[BankAccountRecord],
    owners: Sequence[BankAccountRecord],
) -> tuple[Finding, ...]:
    """``accounts``: the vendor's accounts; ``owners``: all ERP accounts with the printed digits."""
    printed = invoice.bank_account
    if printed is None:
        return (
            Finding(
                rule=RULE,
                message="Faktur tanpa rekening; pembayaran memakai rekening vendor di Odoo.",
            ),
        )
    digits = account_digits(printed.number)
    own = [a for a in accounts if a.partner_id == vendor.id]
    registered = tuple(sorted({a.digits for a in own}))
    shown = mask_account(printed.number)

    same = [a for a in own if digits and a.digits == digits]
    if same:
        account = same[0]
        if (
            printed.bank
            and account.bank_name
            and normalize_name(printed.bank) != normalize_name(account.bank_name)
        ):
            return (
                Finding(
                    rule=RULE,
                    reason=ReasonCode.BANK_ACCOUNT_MISMATCH,
                    message=(
                        f"Rekening {shown} tercetak di {printed.bank}, sedangkan di Odoo "
                        f"terdaftar di {account.bank_name}."
                    ),
                    evidence=BankAccountEvidence(
                        printed_number=printed.number,
                        printed_digits=digits,
                        printed_bank=printed.bank,
                        registered_digits=registered,
                        problem="different_bank",
                    ),
                ),
            )
        if printed.number != account.number:
            return (
                Finding(
                    rule=RULE,
                    message=(
                        f"Rekening tertulis dengan pemisah ({shown}); digitnya sama dengan "
                        "rekening terdaftar."
                    ),
                ),
            )
        return ()

    other = tuple(
        sorted({o.partner_id for o in owners if o.digits == digits and o.partner_id != vendor.id})
    )
    where = "terdaftar atas pihak lain di Odoo" if other else "tidak terdaftar untuk vendor ini"
    return (
        Finding(
            rule=RULE,
            reason=ReasonCode.BANK_ACCOUNT_MISMATCH,
            message=f"Rekening {shown} di faktur berbeda dari rekening {vendor.name}; {where}.",
            evidence=BankAccountEvidence(
                printed_number=printed.number,
                printed_digits=digits,
                printed_bank=printed.bank,
                registered_digits=registered,
                other_owner_ids=other,
                problem="other_owner" if other else "not_registered",
            ),
        ),
    )
