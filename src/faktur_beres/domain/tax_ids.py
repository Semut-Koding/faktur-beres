"""Validity of NPWP and NSFP as printed (used by BR-05 and BR-10).

* NPWP: present, 16 digits after removing separators, and valid for ``stdnum.id.npwp`` (check
  digit). The old 15-digit format is invalid in Faktur Beres even though stdnum accepts it.
* NSFP: present and 17 digits after removing separators (2 transaction code, 2 status, 13 serial).
"""

from typing import Literal

from stdnum.id import npwp as stdnum_npwp

from faktur_beres.domain.normalize import tax_id_digits

NPWP_LENGTH = 16
NSFP_LENGTH = 17

NpwpProblem = Literal["missing", "not_digits", "length", "check_digit"]
NsfpProblem = Literal["missing", "not_digits", "length"]


def _blank(text: str | None) -> bool:
    return text is None or not text.strip()


def npwp_problem(text: str | None) -> NpwpProblem | None:
    """Why a printed NPWP is not valid, or ``None`` when it is."""
    if _blank(text):
        return "missing"
    digits = tax_id_digits(text)
    if digits is None:
        return "not_digits"
    if len(digits) != NPWP_LENGTH:
        return "length"
    if not stdnum_npwp.is_valid(digits):
        return "check_digit"
    return None


def valid_npwp_digits(text: str | None) -> str | None:
    """The digits of a valid NPWP, or ``None``."""
    return tax_id_digits(text) if npwp_problem(text) is None else None


def nsfp_problem(text: str | None) -> NsfpProblem | None:
    """Why a printed NSFP is not valid, or ``None`` when it is."""
    if _blank(text):
        return "missing"
    digits = tax_id_digits(text)
    if digits is None:
        return "not_digits"
    if len(digits) != NSFP_LENGTH:
        return "length"
    return None
