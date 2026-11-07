"""Tests for the normalisation helpers against every format the M1 generator prints."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from faktur_beres.domain.normalize import (
    AmbiguousNumberError,
    InvalidDateError,
    InvalidNumberError,
    NumberStyle,
    account_digits,
    document_number_style,
    normalize_code,
    normalize_email,
    normalize_invoice_number,
    normalize_name,
    normalize_po_number,
    number_style,
    parse_date,
    parse_number,
    parse_rupiah,
    tax_id_digits,
)
from faktur_beres.generator.formatting import amount, date_text, money, quantity, rupiah_plain

SAMPLES = (1, 30, 999, 1000, 4850, 4970, 1200, 98000, 185900, 2450000, 24975000, 62770500)
STYLE = {"rp": NumberStyle.ID, "idr": NumberStyle.EN}


@pytest.mark.parametrize("fmt", ["rp", "idr"])
@pytest.mark.parametrize("n", SAMPLES)
def test_generator_numbers_read_back_with_document_style(fmt: str, n: int) -> None:
    printed = [money(n, fmt), amount(n, fmt), quantity(n, fmt)]
    # Every generated document carries totals in the millions, which prove the style.
    style = document_number_style([money(24975000, fmt), *printed])
    assert style == STYLE[fmt]
    for text in printed:
        assert parse_rupiah(text, style) == n


@pytest.mark.parametrize("n", SAMPLES)
def test_plain_rupiah_in_running_text(n: int) -> None:
    assert parse_rupiah(rupiah_plain(n), NumberStyle.ID) == n


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Rp 1.234.500", Decimal(1234500)),
        ("IDR 1,234,500.00", Decimal(1234500)),
        ("Rp24.975.000", Decimal(24975000)),
        ("24975000.00", Decimal(24975000)),
        ("1.234.500,50", Decimal("1234500.50")),
        ("Rp 10.000,-", Decimal(10000)),
        ("12,5", Decimal("12.5")),
        ("-1.500.000", Decimal(-1500000)),
        ("4850", Decimal(4850)),
    ],
)
def test_unambiguous_numbers_need_no_style(text: str, value: Decimal) -> None:
    assert parse_number(text) == value


@pytest.mark.parametrize("text", ["4.850", "1,200", "Rp 4.850", "IDR 1,200", "0.850"])
def test_ambiguous_numbers_raise_instead_of_guessing(text: str) -> None:
    assert number_style(text) is None
    with pytest.raises(AmbiguousNumberError):
        parse_number(text)


def test_style_resolves_ambiguity() -> None:
    assert parse_number("4.850", NumberStyle.ID) == 4850
    assert parse_number("4.850", NumberStyle.EN) == Decimal("4.85")
    assert parse_number("1,200", NumberStyle.EN) == 1200
    assert parse_number("1,200", NumberStyle.ID) == Decimal("1.2")


@pytest.mark.parametrize("text", ["abc", "", "Rp", "1.2.3", "1,234.567,8", "12.34.567"])
def test_invalid_numbers(text: str) -> None:
    with pytest.raises(InvalidNumberError):
        parse_number(text)


def test_text_in_the_wrong_style_is_invalid() -> None:
    with pytest.raises(InvalidNumberError):
        parse_number("1,234,500.00", NumberStyle.ID)


def test_mixed_styles_on_one_document_raise() -> None:
    with pytest.raises(AmbiguousNumberError):
        document_number_style(["Rp 1.234.500", "IDR 1,234,500.00"])
    assert document_number_style(["4.850", "1200"]) is None


@pytest.mark.parametrize("fmt", ["dmy_slash", "long", "dmon"])
def test_generator_dates_read_back(fmt: str) -> None:
    day = date(2026, 1, 6)
    while day.year == 2026:
        assert parse_date(date_text(day, fmt)) == day
        day += timedelta(days=17)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("2026-11-06", date(2026, 11, 6)),
        ("6/11/2026", date(2026, 11, 6)),
        ("06-Oct-2026", date(2026, 10, 6)),
        ("06-Aug-2026", date(2026, 8, 6)),
        ("6 December 2026", date(2026, 12, 6)),
        ("6 Nopember 2026", date(2026, 11, 6)),
        ("11-06-2026", date(2026, 6, 11)),  # day first, never month first
    ],
)
def test_other_date_spellings(text: str, value: date) -> None:
    assert parse_date(text) == value


@pytest.mark.parametrize("text", ["31/02/2026", "2026/11/06", "06/13/2026", "6 Brumaire 2026", "-"])
def test_invalid_dates(text: str) -> None:
    with pytest.raises(InvalidDateError):
        parse_date(text)


def test_tax_ids_lose_their_separators() -> None:
    assert tax_id_digits("09.904.279.5-722.000") == "099042795722000"
    assert tax_id_digits("040.002-26.64847281") == "0400022664847281"
    assert tax_id_digits("04002600007215090") == "04002600007215090"
    assert tax_id_digits("NPWP 0990") is None
    assert tax_id_digits(None) is None


def test_invoice_and_po_numbers() -> None:
    assert normalize_invoice_number("LKP/2610/0088") == normalize_invoice_number("LKP-2610-0088")
    assert normalize_invoice_number("pas-24519-r") == "PAS24519R"
    assert normalize_invoice_number("SMJ.2610.050") == "SMJ2610050"
    assert normalize_invoice_number("INV_2026 0412") == "INV20260412"
    assert normalize_po_number(" p00015 ") == "P00015"
    assert normalize_po_number("P 00015") == "P00015"
    assert normalize_po_number("P-00015") == "P-00015"  # BR-06 removes spaces only


def test_accounts_names_codes_and_emails() -> None:
    assert account_digits("651-008-2689") == "6510082689"
    assert account_digits("5450 7179 27") == "5450717927"
    assert normalize_name("P.T. Kertas  Sagara Mandiri") == normalize_name(
        "PT Kertas Sagara Mandiri"
    )
    assert normalize_name("CV. Tinta Warna Lestari") == "cv tinta warna lestari"
    assert normalize_code("kl 200") == normalize_code("KL-200") == "KL200"
    assert normalize_email("Bagian Penagihan PT A <Tagihan@Contoh.TEST>") == "tagihan@contoh.test"
    assert normalize_email("mailto:AP@arunika.test") == "ap@arunika.test"
    assert normalize_email("bukan alamat") is None
    assert normalize_email(None) is None
