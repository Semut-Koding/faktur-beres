"""Invoice numbers, tax-invoice serials (NSFP), and substitute identifiers. All deterministic.

* Invoice numbers follow ``format_nomor_faktur`` of the vendor in the dataset. The running number
  continues the vendor's history: it starts above the highest number of the vendor's history
  bills and grows in the order of the invoice dates, with small gaps seeded from the scenario ID.
* NSFP has 17 digits: transaction code ``04`` (DPP nilai lain), status ``00``, year ``26``, and an
  11-digit running number.
* Substitute NPWP and bank-account numbers are made up deterministically and never equal a value
  from the dataset. A substitute NPWP still passes ``stdnum.id.npwp``.

The numbering bases below are generator configuration, deliberately not part of the dataset.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from stdnum import luhn
from stdnum.id import npwp as stdnum_npwp

from faktur_beres.dataset import Dataset
from faktur_beres.generator.determinism import rng


class NumberingError(ValueError):
    """The requested numbers cannot be made consistent."""


@dataclass(frozen=True)
class SeqRule:
    start: int  # the first generated number is start + step
    step_min: int
    step_max: int


#: Running-number base per vendor. ``start`` is above the vendor's highest history number.
#: v06 and v08 share the format ``INV-{yyyy}-{seq:04d}``: v08 stays below 0430 (and passes the
#: fixed 0412 of its own sequence in date order), v06 stays above it.
SEQ_RULES: dict[str, SeqRule] = {
    "v01": SeqRule(452, 3, 9),  # history KSM/INV/2026/08/0412
    "v02": SeqRule(161, 3, 9),  # history up to TWL/INV/2610/0153
    "v03": SeqRule(24547, 4, 12),  # history up to PAS-24519
    "v04": SeqRule(331, 3, 9),  # history CFP-INV-26-00318
    "v05": SeqRule(33, 1, 4),  # history up to RPK/1026/031
    "v06": SeqRule(430, 2, 6),  # history up to INV-2026-0412
    "v07": SeqRule(96, 2, 6),  # history up to LKP-2610-0088
    "v08": SeqRule(396, 3, 5),  # history INV-2026-0291
    "v09": SeqRule(14, 1, 3),  # no history bill
    "v10": SeqRule(47, 1, 4),  # history SMJ.2609.045
    "x01": SeqRule(6, 1, 3),  # vendor outside Odoo
}

NSFP_PREFIX = "04" + "00" + "26"
NSFP_BASE = 7_215_000
OLD_NSFP_PREFIX = "040.002-26."

_FIELD = re.compile(r"\{(\w+)(?::0?\d+d)?\}")


# -- invoice numbers ----------------------------------------------------------------------------


def format_number(fmt: str, issued: date, seq: int) -> str:
    """Fill a ``format_nomor_faktur`` pattern, e.g. ``KSM/INV/{yyyy}/{mm}/{seq:04d}``."""
    return fmt.format(
        yyyy=f"{issued.year:04d}",
        yy=f"{issued.year % 100:02d}",
        mm=f"{issued.month:02d}",
        seq=seq,
    )


def number_pattern(fmt: str) -> re.Pattern[str]:
    parts: list[str] = []
    pos = 0
    for match in _FIELD.finditer(fmt):
        parts.append(re.escape(fmt[pos : match.start()]))
        name = match.group(1)
        if name == "seq":
            parts.append(r"(?P<seq>\d+)")
        elif name == "yyyy":
            parts.append(r"\d{4}")
        elif name in ("yy", "mm"):
            parts.append(r"\d{2}")
        else:
            raise NumberingError(f"bagian format tidak dikenal: {{{name}}}")
        pos = match.end()
    parts.append(re.escape(fmt[pos:]))
    return re.compile("^" + "".join(parts) + "$")


def parse_seq(fmt: str, number: str) -> int | None:
    """Running number inside ``number``, or ``None`` if it does not follow ``fmt``."""
    match = number_pattern(fmt).match(number)
    return int(match.group("seq")) if match else None


def history_seq(ds: Dataset) -> dict[str, int]:
    """Highest running number per vendor among the dataset's history bills."""
    highest: dict[str, int] = {}
    for po, bill in ds.history_bills:
        fmt = ds.vendor(po.vendor).invoice_number_format
        seq = parse_seq(fmt, bill.vendor_invoice_number)
        if seq is None:
            raise NumberingError(f"{bill.vendor_invoice_number} tidak mengikuti {fmt}")
        highest[po.vendor] = max(highest.get(po.vendor, 0), seq)
    return highest


@dataclass(frozen=True)
class NumberRequest:
    key: str  # scenario ID
    vendor: str
    issued: date
    fixed: str | None = None  # a number the scenario prescribes
    in_sequence: bool = True  # a fixed number that belongs to the vendor's running sequence


def assign_invoice_numbers(
    requests: Iterable[NumberRequest],
    formats: dict[str, str],
    history: dict[str, int],
    rules: dict[str, SeqRule] = SEQ_RULES,
) -> dict[str, str]:
    """Number every request; per vendor the numbers grow with the invoice date."""
    by_vendor: dict[str, list[NumberRequest]] = defaultdict(list)
    for request in requests:
        by_vendor[request.vendor].append(request)

    numbers: dict[str, str] = {}
    for vendor in sorted(by_vendor):
        rule, fmt = rules[vendor], formats[vendor]
        if rule.start < history.get(vendor, 0):
            raise NumberingError(f"{vendor}: basis {rule.start} di bawah riwayat")
        previous = rule.start
        for request in sorted(by_vendor[vendor], key=lambda r: (r.issued, r.key)):
            if request.fixed is not None:
                numbers[request.key] = request.fixed
                if request.in_sequence:
                    seq = parse_seq(fmt, request.fixed)
                    if seq is None or seq <= previous:
                        raise NumberingError(
                            f"{request.key}: nomor tetap {request.fixed} tidak urut"
                        )
                    previous = seq
                continue
            previous += rng(request.key, "nomor-faktur").randint(rule.step_min, rule.step_max)
            numbers[request.key] = format_number(fmt, request.issued, previous)
    return numbers


def normalize_number(number: str) -> str:
    """Invoice-number key of BR-11: upper case without spaces, ``/``, ``-``, ``.``, ``_``."""
    return re.sub(r"[\s/\-._]", "", number.upper())


# -- NSFP -----------------------------------------------------------------------------------------


def nsfp(serial: int) -> str:
    """17-digit NSFP with status code 00 for 2026."""
    if not 0 <= serial < 10**11:
        raise NumberingError("nomor urut NSFP harus 11 digit")
    return f"{NSFP_PREFIX}{serial:011d}"


def old_nsfp(serial: int) -> str:
    """Pre-2025 16-digit layout, e.g. ``040.002-26.00012345`` (no longer valid)."""
    return f"{OLD_NSFP_PREFIX}{serial % 10**8:08d}"


def assign_nsfp(keys_in_order: Iterable[str], base: int = NSFP_BASE) -> dict[str, str]:
    """Running NSFP serials with small seeded gaps, in the given order."""
    serial = base
    result: dict[str, str] = {}
    for key in keys_in_order:
        if key in result:
            raise NumberingError(f"NSFP ganda untuk {key}")
        serial += rng(key, "nsfp").randint(1, 40)
        result[key] = nsfp(serial)
    return result


# -- substitute identifiers -------------------------------------------------------------------


def dataset_npwps(ds: Dataset) -> set[str]:
    values = {ds.company.npwp}
    values |= {v.npwp for v in ds.vendors}
    values |= {v.npwp for v in ds.external_vendors}
    return values


def dataset_accounts(ds: Dataset) -> set[str]:
    values = {v.bank_account.number for v in ds.vendors}
    values |= {v.bank_account.number for v in ds.external_vendors}
    return values


def substitute_npwp(key: str, taken: set[str]) -> str:
    """A valid 16-digit test NPWP (prefix 0990, Luhn check digit) not in ``taken``."""
    r = rng(key, "npwp-pengganti")
    while True:
        first9 = "0990" + "".join(str(r.randint(0, 9)) for _ in range(5))
        candidate = first9 + luhn.calc_check_digit(first9) + f"{r.randint(1, 999):03d}000"
        if candidate not in taken and stdnum_npwp.is_valid(candidate):
            return candidate


def substitute_account(key: str, taken: set[str], length: int = 10) -> str:
    """A bank-account number of ``length`` digits not in ``taken``."""
    r = rng(key, "rekening-pengganti")
    while True:
        candidate = str(r.randint(1, 9)) + "".join(str(r.randint(0, 9)) for _ in range(length - 1))
        if candidate not in taken:
            return candidate


def group_digits(number: str, sizes: tuple[int, ...], separator: str) -> str:
    """Print an account number in groups, e.g. ``651-008-2689``."""
    if sum(sizes) != len(number):
        raise NumberingError("ukuran kelompok tidak sama dengan panjang nomor")
    parts, pos = [], 0
    for size in sizes:
        parts.append(number[pos : pos + size])
        pos += size
    return separator.join(parts)
