"""Number and date formats used on documents and in e-mails."""

from datetime import date
from typing import Literal

NumberFormat = Literal["rp", "idr"]
DateFormat = Literal["dmy_slash", "long", "dmon"]

MONTHS = (
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
)
MONTHS_SHORT = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")


def thousands(n: int, sep: str) -> str:
    return f"{n:,}".replace(",", sep)


def amount(n: int, fmt: NumberFormat) -> str:
    """Amount without currency, e.g. ``1.234.500`` or ``1,234,500.00``."""
    return thousands(n, ".") if fmt == "rp" else f"{thousands(n, ',')}.00"


def money(n: int, fmt: NumberFormat) -> str:
    """Amount with currency, e.g. ``Rp 1.234.500`` or ``IDR 1,234,500.00``."""
    return f"Rp {amount(n, 'rp')}" if fmt == "rp" else f"IDR {amount(n, 'idr')}"


def quantity(n: int, fmt: NumberFormat) -> str:
    return thousands(n, "." if fmt == "rp" else ",")


def rupiah_plain(n: int) -> str:
    """``Rp24.975.000`` (for running text)."""
    return f"Rp{thousands(n, '.')}"


def long_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def date_text(d: date, fmt: DateFormat) -> str:
    """``06/11/2026``, ``6 November 2026``, or ``06-Nov-2026``."""
    if fmt == "dmy_slash":
        return f"{d:%d/%m/%Y}"
    if fmt == "long":
        return long_date(d)
    return f"{d.day:02d}-{MONTHS_SHORT[d.month - 1]}-{d.year}"
