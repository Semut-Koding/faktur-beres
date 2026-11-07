"""BR-10: tax, checked against the printed figures (SPEC 0.3, PMK 131/2024).

* Seller NPWP: present, 16 digits, valid check digit (``stdnum.id.npwp``); 15 digits is invalid.
* Buyer NPWP: present and equal to the company's NPWP in Odoo.
* NSFP: present and 17 digits after removing separators. A transaction code other than the
  expected one (``04``) is a note, not an exception.
* DPP nilai lain = 11/12 × printed subtotal and PPN = 12% × 11/12 × printed subtotal, each ±Rp1;
  without a printed DPP nilai lain only PPN is checked. The base is the printed subtotal, not the
  recomputed lines, so a wrong addition is only an ``arithmetic_error`` (BR-03).

Every violation is ``tax_invalid``. A valid seller NPWP that differs from the NPWP of the vendor
identified by name or e-mail is ``identity_mismatch`` (``check_seller_identity``); an invalid NPWP
only gives ``tax_invalid``. Rates come from ``PolicyConfig``, never from the tax record in Odoo.
"""

from fractions import Fraction
from typing import Literal

from faktur_beres.domain.erp import CompanyRecord
from faktur_beres.domain.findings import (
    Finding,
    IdentityEvidence,
    TaxAmountEvidence,
    TaxIdEvidence,
    TransactionCodeEvidence,
    number,
    rupiah,
    to_decimal,
)
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.normalize import tax_id_digits
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.vendor import VendorIdentification
from faktur_beres.domain.tax_ids import (
    NPWP_LENGTH,
    NSFP_LENGTH,
    npwp_problem,
    nsfp_problem,
    valid_npwp_digits,
)

RULE = "BR-10"

_NPWP_TEXT = {
    "missing": "tidak ada",
    "not_digits": "memuat karakter selain angka dan pemisah",
    "length": f"bukan {NPWP_LENGTH} digit",
    "check_digit": "tidak lolos validasi digit cek",
}


def _tax_id(
    field: Literal["npwp_penjual", "npwp_pembeli", "nsfp"],
    printed: str | None,
    problem: Literal["missing", "not_digits", "length", "check_digit", "mismatch"],
    message: str,
    expected: str | None = None,
) -> Finding:
    return Finding(
        rule=RULE,
        reason=ReasonCode.TAX_INVALID,
        message=message,
        evidence=TaxIdEvidence(
            field=field,
            printed=printed,
            digits=tax_id_digits(printed),
            problem=problem,
            expected=expected,
        ),
    )


def _amount(
    field: Literal["dpp_nilai_lain", "ppn"],
    invoice: InvoiceData,
    printed: Fraction,
    expected: Fraction,
    policy: PolicyConfig,
    formula: str,
) -> Finding | None:
    difference = printed - expected
    if abs(difference) <= Fraction(policy.rounding.tax):
        return None
    label = "DPP nilai lain" if field == "dpp_nilai_lain" else "PPN"
    return Finding(
        rule=RULE,
        reason=ReasonCode.TAX_INVALID,
        message=(
            f"{label} tercetak {rupiah(to_decimal(printed))}, seharusnya {formula} = "
            f"{rupiah(to_decimal(expected))} (selisih {rupiah(to_decimal(difference))})."
        ),
        evidence=TaxAmountEvidence(
            field=field,
            subtotal=invoice.subtotal,
            printed=to_decimal(printed),
            expected=to_decimal(expected),
            difference=to_decimal(difference),
            tolerance=policy.rounding.tax,
        ),
    )


def check_tax(
    invoice: InvoiceData, company: CompanyRecord, policy: PolicyConfig
) -> tuple[Finding, ...]:
    """All BR-10 findings for the printed tax data (``tax_check``)."""
    findings: list[Finding] = []

    seller = invoice.seller.npwp
    if (problem := npwp_problem(seller)) is not None:
        findings.append(
            _tax_id("npwp_penjual", seller, problem, f"NPWP penjual {_NPWP_TEXT[problem]}.")
        )

    buyer = invoice.buyer.npwp if invoice.buyer else None
    buyer_digits = tax_id_digits(buyer)
    company_digits = tax_id_digits(company.vat)
    if buyer is None or not buyer.strip():
        findings.append(_tax_id("npwp_pembeli", buyer, "missing", "NPWP pembeli tidak ada."))
    elif buyer_digits is None:
        findings.append(
            _tax_id(
                "npwp_pembeli", buyer, "not_digits", f"NPWP pembeli {_NPWP_TEXT['not_digits']}."
            )
        )
    elif buyer_digits != company_digits:
        findings.append(
            _tax_id(
                "npwp_pembeli",
                buyer,
                "mismatch",
                "NPWP pembeli berbeda dari NPWP perusahaan di Odoo.",
                expected=company_digits,
            )
        )

    nsfp = invoice.nsfp
    if (nsfp_issue := nsfp_problem(nsfp)) is not None:
        digits = tax_id_digits(nsfp)
        text = {
            "missing": "NSFP tidak ada.",
            "not_digits": "NSFP memuat karakter selain angka dan pemisah.",
            "length": f"NSFP {len(digits or '')} digit; wajib {NSFP_LENGTH} digit.",
        }[nsfp_issue]
        findings.append(_tax_id("nsfp", nsfp, nsfp_issue, text))
    else:
        code = (tax_id_digits(nsfp) or "")[:2]
        expected_code = policy.tax.expected_transaction_code
        if code != expected_code:
            findings.append(
                Finding(
                    rule=RULE,
                    message=f"Kode transaksi NSFP {code}, bukan {expected_code}; dicatat.",
                    evidence=TransactionCodeEvidence(code=code, expected=expected_code),
                )
            )

    subtotal = Fraction(invoice.subtotal)
    factor = policy.tax.dpp_factor
    expected_dpp = subtotal * factor.fraction
    rate = number(policy.tax.vat_rate_percent)
    if invoice.dpp_nilai_lain is not None:
        dpp = _amount(
            "dpp_nilai_lain",
            invoice,
            Fraction(invoice.dpp_nilai_lain),
            expected_dpp,
            policy,
            f"{factor} × subtotal {rupiah(invoice.subtotal)}",
        )
        if dpp is not None:
            findings.append(dpp)
    vat = _amount(
        "ppn",
        invoice,
        Fraction(invoice.vat),
        expected_dpp * policy.tax.vat_rate,
        policy,
        f"{rate}% × {factor} × subtotal {rupiah(invoice.subtotal)}",
    )
    if vat is not None:
        findings.append(vat)
    return tuple(findings)


def check_seller_identity(
    invoice: InvoiceData, identification: VendorIdentification
) -> Finding | None:
    """A valid seller NPWP that differs from the vendor identified by name or e-mail."""
    vendor = identification.vendor
    if not identification.identified or identification.method != "name_email" or vendor is None:
        return None
    digits = valid_npwp_digits(invoice.seller.npwp)
    if digits is None:
        return None
    vendor_digits = tax_id_digits(vendor.vat)
    if digits == vendor_digits:
        return None
    return Finding(
        rule=RULE,
        reason=ReasonCode.IDENTITY_MISMATCH,
        message=f"NPWP penjual valid tetapi berbeda dari NPWP {vendor.name} di Odoo.",
        evidence=IdentityEvidence(
            npwp_digits=digits, vendor_id=vendor.id, vendor_npwp_digits=vendor_digits
        ),
    )
