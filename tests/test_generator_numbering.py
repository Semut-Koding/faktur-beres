"""Tests for invoice numbering, NSFP, substitute identifiers, terbilang, and model checks."""

from datetime import date

import pytest
from stdnum.id import npwp as stdnum_npwp

from faktur_beres.dataset import load_dataset
from faktur_beres.generator.model import DocLine, InvoiceDoc, Party
from faktur_beres.generator.numbering import (
    SEQ_RULES,
    NumberingError,
    NumberRequest,
    assign_invoice_numbers,
    assign_nsfp,
    dataset_accounts,
    dataset_npwps,
    format_number,
    group_digits,
    history_seq,
    normalize_number,
    nsfp,
    old_nsfp,
    parse_seq,
    substitute_account,
    substitute_npwp,
)
from faktur_beres.generator.terbilang import terbilang, terbilang_rupiah


@pytest.fixture(scope="module")
def ds():
    return load_dataset()


def formats(ds) -> dict[str, str]:
    result = {v.key: v.invoice_number_format for v in ds.vendors}
    result |= {v.key: v.invoice_number_format for v in ds.external_vendors}
    return result


# -- invoice numbers ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "seq", "expected"),
    [
        ("KSM/INV/{yyyy}/{mm}/{seq:04d}", 457, "KSM/INV/2026/11/0457"),
        ("TWL/INV/{yy}{mm}/{seq:04d}", 165, "TWL/INV/2611/0165"),
        ("PAS-{seq:05d}", 24551, "PAS-24551"),
        ("CFP-INV-{yy}-{seq:05d}", 333, "CFP-INV-26-00333"),
        ("RPK/{mm}{yy}/{seq:03d}", 35, "RPK/1126/035"),
        ("INV-{yyyy}-{seq:04d}", 432, "INV-2026-0432"),
        ("LKP-{yy}{mm}-{seq:04d}", 99, "LKP-2611-0099"),
        ("GLC-INV-{seq:04d}", 15, "GLC-INV-0015"),
        ("SMJ.{yy}{mm}.{seq:03d}", 49, "SMJ.2611.049"),
        ("MPB/{yy}{mm}/{seq:03d}", 7, "MPB/2611/007"),
    ],
)
def test_format_and_parse_number(fmt: str, seq: int, expected: str) -> None:
    assert format_number(fmt, date(2026, 11, 4), seq) == expected
    assert parse_seq(fmt, expected) == seq
    assert parse_seq(fmt, expected + "-R") is None


def test_history_numbers_are_parsed(ds) -> None:
    assert history_seq(ds) == {
        "v01": 412,
        "v02": 153,
        "v03": 24519,
        "v04": 318,
        "v05": 31,
        "v06": 412,
        "v07": 88,
        "v08": 291,
        "v10": 45,
    }


def test_numbering_bases_continue_history(ds) -> None:
    history = history_seq(ds)
    for vendor, rule in SEQ_RULES.items():
        assert rule.start >= history.get(vendor, 0), vendor
        assert 1 <= rule.step_min <= rule.step_max, vendor
    assert set(SEQ_RULES) == {v.key for v in ds.vendors} | {v.key for v in ds.external_vendors}


def test_assign_numbers_grow_with_date_and_honour_fixed(ds) -> None:
    requests = [
        NumberRequest("A-02", "v08", date(2026, 11, 1)),
        NumberRequest("A-01", "v08", date(2026, 10, 30)),
        NumberRequest("A-03", "v08", date(2026, 11, 3), fixed="INV-2026-0412"),
        NumberRequest("A-04", "v08", date(2026, 11, 5)),
        NumberRequest("A-05", "v08", date(2026, 10, 16), fixed="INV-2026-0291", in_sequence=False),
    ]
    numbers = assign_invoice_numbers(requests, formats(ds), history_seq(ds))
    fmt = ds.vendor("v08").invoice_number_format
    seqs = [parse_seq(fmt, numbers[k]) for k in ("A-01", "A-02", "A-03", "A-04")]
    assert seqs == sorted(seqs) and len(set(seqs)) == 4
    assert seqs[2] == 412 and seqs[0] > history_seq(ds)["v08"]
    assert numbers["A-05"] == "INV-2026-0291"
    assert assign_invoice_numbers(requests, formats(ds), history_seq(ds)) == numbers


def test_fixed_number_out_of_order_is_rejected(ds) -> None:
    requests = [
        NumberRequest("A-01", "v08", date(2026, 10, 30)),
        NumberRequest("A-02", "v08", date(2026, 11, 3), fixed="INV-2026-0300"),
    ]
    with pytest.raises(NumberingError):
        assign_invoice_numbers(requests, formats(ds), history_seq(ds))


def test_normalize_number_follows_br11() -> None:
    assert normalize_number("LKP/2610/0088") == normalize_number("LKP-2610-0088") == "LKP26100088"
    assert normalize_number("smj.2611.049") == "SMJ2611049"
    assert normalize_number("PAS-24519-R") != normalize_number("PAS-24519")


# -- NSFP ---------------------------------------------------------------------------------------


def test_nsfp_layout() -> None:
    value = nsfp(7_215_031)
    assert value == "04002600007215031"
    assert len(value) == 17 and value.isdigit()
    assert value[:2] == "04" and value[2:4] == "00" and value[4:6] == "26"
    with pytest.raises(NumberingError):
        nsfp(10**11)


def test_old_nsfp_has_16_digits() -> None:
    value = old_nsfp(7_215_031)
    assert value == "040.002-26.07215031"
    digits = "".join(c for c in value if c.isdigit())
    assert len(digits) == 16


def test_assign_nsfp_is_unique_and_increasing() -> None:
    keys = [f"K-{i:02d}" for i in range(60)]
    values = assign_nsfp(keys)
    serials = [int(values[k][6:]) for k in keys]
    assert serials == sorted(serials) and len(set(serials)) == 60
    assert assign_nsfp(keys) == values
    with pytest.raises(NumberingError):
        assign_nsfp(["X", "X"])


# -- substitutes --------------------------------------------------------------------------------


def test_substitute_npwp_is_valid_and_new(ds) -> None:
    taken = dataset_npwps(ds)
    value = substitute_npwp("S-01", taken)
    assert len(value) == 16 and value.startswith("0990")
    assert stdnum_npwp.is_valid(value)
    assert value not in taken
    assert substitute_npwp("S-01", taken) == value
    assert substitute_npwp("S-01", taken | {value}) != value


def test_substitute_account_is_new(ds) -> None:
    taken = dataset_accounts(ds)
    values = {substitute_account(f"S-{i}", taken) for i in range(20)}
    assert not values & taken
    assert all(len(v) == 10 and v.isdigit() and v[0] != "0" for v in values)


def test_group_digits() -> None:
    assert group_digits("6510082689", (3, 3, 4), "-") == "651-008-2689"
    with pytest.raises(NumberingError):
        group_digits("6510082689", (3, 3), "-")


# -- terbilang ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "words"),
    [
        (0, "nol"),
        (1, "satu"),
        (10, "sepuluh"),
        (11, "sebelas"),
        (12, "dua belas"),
        (19, "sembilan belas"),
        (20, "dua puluh"),
        (21, "dua puluh satu"),
        (100, "seratus"),
        (101, "seratus satu"),
        (110, "seratus sepuluh"),
        (115, "seratus lima belas"),
        (999, "sembilan ratus sembilan puluh sembilan"),
        (1000, "seribu"),
        (1001, "seribu satu"),
        (1100, "seribu seratus"),
        (2000, "dua ribu"),
        (11000, "sebelas ribu"),
        (100000, "seratus ribu"),
        (1_000_000, "satu juta"),
        (26_107_200, "dua puluh enam juta seratus tujuh ribu dua ratus"),
        (62_770_500, "enam puluh dua juta tujuh ratus tujuh puluh ribu lima ratus"),
        (1_000_000_000, "satu miliar"),
        (2_001_000_100, "dua miliar satu juta seratus"),
    ],
)
def test_terbilang(n: int, words: str) -> None:
    assert terbilang(n) == words


def test_terbilang_rupiah() -> None:
    expected = "dua puluh enam juta seratus tujuh ribu dua ratus rupiah"
    assert terbilang_rupiah(26_107_200) == expected
    with pytest.raises(ValueError):
        terbilang(-1)


# -- model checks ---------------------------------------------------------------------------------


def make_doc(lines, subtotal, dpp, ppn, total) -> InvoiceDoc:
    party = Party("PT Contoh", "0990000000000000", "Jl. Uji 1", "Kota Uji", "Jawa Barat", "40000")
    return InvoiceDoc(
        number="X/1",
        issue_date=date(2026, 11, 4),
        due_date=date(2026, 12, 4),
        po_ref="P00001",
        seller=party,
        buyer=party,
        lines=tuple(lines),
        subtotal=subtotal,
        dpp=dpp,
        ppn=ppn,
        total=total,
        nsfp=None,
        tax_invoice_date=None,
        bank=None,
    )


def line(qty: int, price: int, amount: int | None = None) -> DocLine:
    return DocLine("K", "Barang", qty, "kg", price, qty * price if amount is None else amount)


def test_model_arithmetic_and_tax_checks() -> None:
    good = make_doc([line(1200, 18750)], 22_500_000, 20_625_000, 2_475_000, 24_975_000)
    assert good.arithmetic_consistent() and good.tax_rule_consistent()

    wrong_line = make_doc(
        [line(40, 135_000), line(30, 98_000, 3_240_000)], 8_640_000, 7_920_000, 950_400, 9_590_400
    )
    assert not wrong_line.arithmetic_consistent()
    assert wrong_line.tax_rule_consistent()

    wrong_tax = make_doc([line(50, 165_000)], 8_250_000, 8_250_000, 990_000, 9_240_000)
    assert wrong_tax.arithmetic_consistent()
    assert not wrong_tax.tax_rule_consistent()
