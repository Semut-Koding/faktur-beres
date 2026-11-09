"""Normalisation helpers for printed invoice values.

Numbers. Indonesian documents write ``1.234.500`` or ``1.234.500,00`` (``.`` groups thousands, ``,``
is the decimal mark); English-style documents write ``1,234,500.00``. A lone ``4.850`` or ``1,200``
fits both and is **ambiguous**: it raises ``AmbiguousNumberError`` unless the caller passes the
number style of the document (``document_number_style`` finds it from the unambiguous values on the
same document). Nothing is guessed (BR-04: an ambiguous number is a validation failure).

Dates are day first (``06/11/2026``, ``6 November 2026``, ``06-Nov-2026``) or ISO (``2026-11-06``),
with Indonesian and English month names and abbreviations.

Identifiers follow the business rules: NPWP and NSFP without separators (BR-10), invoice numbers as
in BR-11, PO numbers as in BR-06, account numbers as digits (BR-12), names as in BR-05, and e-mail
addresses in lower case without a display name.
"""

import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from email.utils import parseaddr
from enum import StrEnum


class NormalizationError(ValueError):
    """A printed value cannot be read."""


class InvalidNumberError(NormalizationError):
    """The text is not a number in any supported style."""


class AmbiguousNumberError(NormalizationError):
    """The text reads as different numbers in the two styles, or a document mixes styles."""


class InvalidDateError(NormalizationError):
    """The text is not a supported date."""


class NumberStyle(StrEnum):
    ID = "id"  # 1.234.500,00
    EN = "en"  # 1,234,500.00


_CURRENCY = re.compile(r"^(?:rp\.?|idr)\s*", re.IGNORECASE)
_NO_DECIMALS_SUFFIX = re.compile(r"[,.]-$")  # "Rp 10.000,-"
_GROUPED = {
    NumberStyle.ID: re.compile(r"^\d{1,3}(?:\.\d{3})+(?:,\d+)?$|^\d+(?:,\d+)?$"),
    NumberStyle.EN: re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$|^\d+(?:\.\d+)?$"),
}
_SEPARATORS = {NumberStyle.ID: (".", ","), NumberStyle.EN: (",", ".")}


def _clean_number(text: str) -> tuple[str, bool, NumberStyle | None]:
    """Strip currency, spaces, and sign. Returns (digits text, negative, style hint)."""
    s = unicodedata.normalize("NFKC", text).strip()
    s = _CURRENCY.sub("", s)
    s = re.sub(r"\s+", "", s)
    hint = None
    if _NO_DECIMALS_SUFFIX.search(s):
        hint = NumberStyle.ID if s.endswith(",-") else NumberStyle.EN
        s = s[:-2]
    negative = s.startswith("-")
    if negative:
        s = s[1:]
    if not s:
        raise InvalidNumberError(f"bukan angka: {text!r}")
    return s, negative, hint


def _read(s: str, style: NumberStyle) -> Decimal | None:
    if not _GROUPED[style].match(s):
        return None
    thousands, decimal_mark = _SEPARATORS[style]
    plain = s.replace(thousands, "").replace(decimal_mark, ".")
    try:
        return Decimal(plain)
    except InvalidOperation:  # pragma: no cover - the patterns only let digits through
        return None


def number_style(text: str) -> NumberStyle | None:
    """The style a printed number proves on its own, or ``None`` when it fits both or neither."""
    s, _, hint = _clean_number(text)
    if hint is not None:
        return hint
    readings = {style: _read(s, style) for style in NumberStyle}
    valid = [style for style, value in readings.items() if value is not None]
    if len(valid) == 1:
        return valid[0]
    return None


def document_number_style(texts: Iterable[str]) -> NumberStyle | None:
    """The one style proven by the numbers of a document; mixed styles raise."""
    found = {style for text in texts if (style := number_style(text)) is not None}
    if len(found) > 1:
        raise AmbiguousNumberError("dokumen memakai dua gaya angka sekaligus")
    return found.pop() if found else None


def parse_number(text: str, style: NumberStyle | None = None) -> Decimal:
    """Read a printed number (with or without ``Rp``/``IDR``) as an exact ``Decimal``."""
    s, negative, hint = _clean_number(text)
    if style is None:
        style = hint
    if style is not None:
        value = _read(s, style)
        if value is None:
            raise InvalidNumberError(f"{text!r} bukan angka bergaya {style.value}")
    else:
        readings = {v for st in NumberStyle if (v := _read(s, st)) is not None}
        if not readings:
            raise InvalidNumberError(f"bukan angka: {text!r}")
        if len(readings) > 1:
            raise AmbiguousNumberError(
                f"{text!r} bisa dibaca {' atau '.join(sorted(map(str, readings)))}; "
                "gaya angka dokumen diperlukan"
            )
        value = readings.pop()
    return -value if negative else value


def parse_rupiah(text: str, style: NumberStyle | None = None) -> Decimal:
    """Alias of ``parse_number`` for amounts; ``Rp``/``IDR`` prefixes are accepted."""
    return parse_number(text, style)


_MONTHS: dict[str, int] = {}
for _number, _names in enumerate(
    (
        ("jan", "januari", "january"),
        ("feb", "februari", "pebruari", "february"),
        ("mar", "maret", "march"),
        ("apr", "april"),
        ("mei", "may"),
        ("jun", "juni", "june"),
        ("jul", "juli", "july"),
        ("agu", "agt", "ags", "agustus", "aug", "august"),
        ("sep", "sept", "september"),
        ("okt", "oktober", "oct", "october"),
        ("nov", "november", "nopember"),
        ("des", "desember", "dec", "december"),
    ),
    start=1,
):
    for _name in _names:
        _MONTHS[_name] = _number

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_NUMERIC_DMY = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_NAMED_DMY = re.compile(r"^(\d{1,2})[\s-]+([A-Za-z]+)\.?[\s-]+(\d{4})$")


def parse_date(text: str) -> date:
    """Read a printed date: ISO, or day-month-year with numeric or named month."""
    s = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
    try:
        if m := _ISO.match(s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _NUMERIC_DMY.match(s):
            return date(int(m[3]), int(m[2]), int(m[1]))
        if (m := _NAMED_DMY.match(s)) and (month := _MONTHS.get(m[2].lower())):
            return date(int(m[3]), month, int(m[1]))
    except ValueError as exc:
        raise InvalidDateError(f"tanggal tidak valid: {text!r}") from exc
    raise InvalidDateError(f"format tanggal tidak dikenal: {text!r}")


_ID_SEPARATORS = re.compile(r"[\s.\-/_]")


def strip_separators(text: str) -> str:
    """NPWP or NSFP without spaces, ``.``, ``-``, ``/``, and ``_``; other characters stay."""
    return _ID_SEPARATORS.sub("", text)


def tax_id_digits(text: str | None) -> str | None:
    """NPWP/NSFP digits; ``None`` when absent or when more than digits and separators is printed."""
    if text is None:
        return None
    digits = strip_separators(text)
    return digits if digits.isdigit() else None


_INVOICE_NUMBER_DROP = re.compile(r"[\s/\-._]")


def normalize_invoice_number(text: str) -> str:
    """BR-11: upper case; spaces, ``/``, ``-``, ``.``, and ``_`` removed."""
    return _INVOICE_NUMBER_DROP.sub("", text).upper()


def normalize_po_number(text: str) -> str:
    """BR-06: upper case, without spaces."""
    return re.sub(r"\s+", "", text).upper()


def account_digits(text: str) -> str:
    """BR-12: an account number as digits only."""
    return re.sub(r"\D", "", text)


def normalize_name(text: str) -> str:
    """BR-05: lower case, punctuation removed, spaces collapsed."""
    s = unicodedata.normalize("NFKC", text).casefold()
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith(("P", "S")))
    return re.sub(r"\s+", " ", s).strip()


_BANK_NAME_FILLER = frozenset({"bank", "pt", "tbk", "persero", "perseroan", "terbuka"})


def bank_name_key(text: str) -> str:
    """BR-12: a bank name as BR-05 normalises names, without "bank" and legal forms.

    ``PT Bank Mandala Raya Tbk``, ``BANK MANDALA RAYA``, and ``Bank Mandala Raya (Persero)`` all
    become ``mandala raya``.
    """
    return " ".join(w for w in normalize_name(text).split() if w not in _BANK_NAME_FILLER)


def normalize_code(text: str) -> str:
    """Product codes compared without case and punctuation (``KL-200`` = ``kl 200``)."""
    return re.sub(r"[^0-9A-Z]", "", unicodedata.normalize("NFKC", text).upper())


def normalize_email(text: str | None) -> str | None:
    """Lower-case address without display name; ``None`` when there is no usable address."""
    if not text:
        return None
    s = text.strip()
    if s.lower().startswith("mailto:"):
        s = s[len("mailto:") :]
    _, address = parseaddr(s)
    address = address.strip().lower()
    if address.count("@") != 1 or address.startswith("@") or address.endswith("@"):
        return None
    return address


def email_domain(address: str) -> str:
    """The domain of a normalised address."""
    return address.rsplit("@", 1)[1]
