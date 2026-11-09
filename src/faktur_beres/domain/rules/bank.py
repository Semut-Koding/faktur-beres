"""BR-12: the bank account printed on the invoice (SPEC 0.4).

The printed number is normalised to digits and compared with the vendor's accounts in Odoo. A
number that is not registered for the vendor, or that belongs to someone else, is
``bank_account_mismatch`` (C3). When the number is the vendor's but the printed bank name differs,
the names are compared after ``bank_name_key`` (BR-05 normalisation without "bank" and legal forms
such as "PT", "Tbk", "Persero"): the same bank passes; the name of **another bank registered in
Odoo** is C3 (``different_bank``); any other spelling is only a note. An invoice without an account
is no exception: payment always uses the ERP data. Messages show only the last four digits
(BR-24); the evidence keeps the digits for the approver.
"""

from collections.abc import Sequence

from faktur_beres.domain.erp import BankAccountRecord, BankRecord, VendorRecord
from faktur_beres.domain.findings import BankAccountEvidence, Finding
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.normalize import account_digits, bank_name_key
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.safety import mask_account

RULE = "BR-12"


def check_bank_account(
    invoice: InvoiceData,
    vendor: VendorRecord,
    accounts: Sequence[BankAccountRecord],
    owners: Sequence[BankAccountRecord],
    banks: Sequence[BankRecord] = (),
) -> tuple[Finding, ...]:
    """``accounts``: the vendor's accounts; ``owners``: all ERP accounts with the printed digits;
    ``banks``: the banks registered in the ERP."""
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
        notes: list[Finding] = []
        if printed.bank and account.bank_name:
            printed_key = bank_name_key(printed.bank)
            registered_key = bank_name_key(account.bank_name)
            if printed_key != registered_key:
                others = [
                    b
                    for b in banks
                    if printed_key
                    and bank_name_key(b.name) == printed_key
                    and bank_name_key(b.name) != registered_key
                ]
                if others:
                    return (
                        Finding(
                            rule=RULE,
                            reason=ReasonCode.BANK_ACCOUNT_MISMATCH,
                            message=(
                                f"Rekening {shown} tercetak di {printed.bank}, bank lain yang "
                                f"terdaftar di Odoo; rekening ini terdaftar di {account.bank_name}."
                            ),
                            evidence=BankAccountEvidence(
                                printed_number=printed.number,
                                printed_digits=digits,
                                printed_bank=printed.bank,
                                registered_digits=registered,
                                registered_bank=account.bank_name,
                                matched_bank=others[0].name,
                                problem="different_bank",
                            ),
                        ),
                    )
                notes.append(
                    Finding(
                        rule=RULE,
                        message=(
                            f"Nama bank tercetak '{printed.bank}' berbeda dari "
                            f"'{account.bank_name}' yang terdaftar untuk rekening {shown}; "
                            "nomor rekening sama, dicatat."
                        ),
                    )
                )
        if printed.number != account.number:
            notes.append(
                Finding(
                    rule=RULE,
                    message=(
                        f"Rekening tertulis dengan pemisah ({shown}); digitnya sama dengan "
                        "rekening terdaftar."
                    ),
                )
            )
        return tuple(notes)

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
