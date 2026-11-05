"""Consistency checks for data/seed/dataset.yaml and the data needs of SPEC §15.3.

Every change to the dataset must pass these tests (ADR-0007). They read only the dataset; the
scenario catalogue itself lives in SPEC §15.3 and eval/skenario/, never in the dataset or Odoo.
"""

import re
from collections import Counter
from datetime import timedelta

import pytest
from stdnum.id import npwp as stdnum_npwp

from faktur_beres.dataset import (
    DATASET_PATH,
    Dataset,
    PurchaseOrder,
    dpp_nilai_lain,
    load_dataset,
    ppn_amount,
    total_with_ppn,
)

LABEL_PATTERN = re.compile(r"\b(COC|HRG|QTY|DUP|NPO|PJK|REK|INJ)-\d{2}\b")
PO_NUMBER = re.compile(r"^P\d{5}$")
PRICE_TOLERANCE_PERCENT = 1  # BR-08 / SPEC §15.5


@pytest.fixture(scope="module")
def ds() -> Dataset:
    return load_dataset()


def all_emails(ds: Dataset) -> list[str]:
    c = ds.company
    emails = [c.email_ap, c.email_finance, c.email_approver]
    emails += [u.login for u in ds.users]
    emails += [v.billing_email for v in ds.vendors]
    emails += [v.billing_email for v in ds.external_vendors]
    return emails


# -- structure --------------------------------------------------------------------------------


def test_dataset_loads_with_strict_models(ds: Dataset) -> None:
    assert ds.version == 1
    assert ds.purchase_tax.effective_rate_percent == 11


def test_keys_are_unique(ds: Dataset) -> None:
    groups = {
        "bank": [b.key for b in ds.banks],
        "vendor": [v.key for v in ds.vendors] + [v.key for v in ds.external_vendors],
        "produk": [p.key for p in ds.products],
        "purchase_order": [po.key for po in ds.purchase_orders],
        "pengguna": [u.key for u in ds.users],
    }
    for name, keys in groups.items():
        dupes = [k for k, n in Counter(keys).items() if n > 1]
        assert not dupes, f"kunci {name} ganda: {dupes}"


def test_po_numbers_unique_and_well_formed(ds: Dataset) -> None:
    numbers = [po.number for po in ds.purchase_orders]
    assert len(numbers) == len(set(numbers))
    bad = [n for n in numbers if not PO_NUMBER.match(n)]
    assert not bad, f"nomor PO tidak berpola P\\d{{5}}: {bad}"


def test_po_numbers_follow_order_date(ds: Dataset) -> None:
    ordered = sorted(ds.purchase_orders, key=lambda po: po.number)
    dates = [po.order_date for po in ordered]
    assert dates == sorted(dates), "nomor PO harus urut tanggal pesan"


def test_po_products_belong_to_po_vendor_and_are_not_repeated(ds: Dataset) -> None:
    for po in ds.purchase_orders:
        assert po.vendor in {v.key for v in ds.vendors}, f"{po.number}: vendor tidak terdaftar"
        products = [line.product for line in po.lines]
        assert len(products) == len(set(products)), f"{po.number}: produk ganda"
        for key in products:
            assert ds.product(key).vendor == po.vendor, f"{po.number}: {key} bukan milik vendor"


def test_products_reference_registered_vendors(ds: Dataset) -> None:
    vendor_keys = {v.key for v in ds.vendors}
    for p in ds.products:
        assert p.vendor in vendor_keys, f"{p.key}: vendor {p.vendor} tidak terdaftar"
    codes = [p.code for p in ds.products]
    assert len(codes) == len(set(codes)), "kode produk ganda"


def test_receipts_match_po_lines(ds: Dataset) -> None:
    for po in ds.purchase_orders:
        lines = {line.product: line for line in po.lines}
        if po.status != "dikonfirmasi":
            assert not po.receipts, f"{po.number}: PO {po.status} tidak boleh punya penerimaan"
        for receipt in po.receipts:
            assert receipt.received_on >= po.order_date, f"{po.number}: diterima sebelum dipesan"
            for key, qty in receipt.qty.items():
                assert key in lines, f"{po.number}: penerimaan {key} tanpa baris PO"
                assert 0 < qty <= lines[key].qty, f"{po.number}: qty terima {key} tidak wajar"
        for line in po.lines:
            assert po.received_qty(line.product) <= line.qty
        has_backorder_flag = any(r.remainder == "backorder" for r in po.receipts)
        assert has_backorder_flag == po.is_partially_received, f"{po.number}: tanda backorder"


def test_history_bills_only_on_fully_received_pos(ds: Dataset) -> None:
    for po, bill in ds.history_bills:
        assert po.is_confirmed and po.is_fully_received, po.number
        last_receipt = max(r.received_on for r in po.receipts)
        assert bill.invoice_date == last_receipt + timedelta(days=1), po.number
    numbers = [(po.vendor, bill.vendor_invoice_number) for po, bill in ds.history_bills]
    assert len(numbers) == len(set(numbers)), "nomor faktur riwayat ganda per vendor"


def test_planned_receipt_dates_only_for_unreceived_confirmed_pos(ds: Dataset) -> None:
    for po in ds.purchase_orders:
        if po.planned_receipt_date is not None:
            assert po.is_confirmed and not po.receipts, po.number
            assert po.planned_receipt_date > po.order_date, po.number


# -- money ------------------------------------------------------------------------------------


def test_line_subtotals_are_multiples_of_100(ds: Dataset) -> None:
    for po in ds.purchase_orders:
        for line in po.lines:
            assert line.subtotal % 100 == 0, f"{po.number}/{line.product}: Rp{line.subtotal}"


def test_document_subtotals_are_multiples_of_300(ds: Dataset) -> None:
    for po in ds.purchase_orders:
        assert po.subtotal % 300 == 0, f"{po.number}: subtotal Rp{po.subtotal}"
        if po.is_partially_received:
            received = po.received_subtotal()
            assert received % 300 == 0, f"{po.number}: subtotal diterima Rp{received}"


def test_ppn_and_dpp_are_whole_rupiah(ds: Dataset) -> None:
    rate = ds.purchase_tax.effective_rate_percent
    for po in ds.purchase_orders:
        ppn = ppn_amount(po.subtotal, rate)
        assert ppn * 100 == po.subtotal * rate
        assert po.total(rate) == po.subtotal + ppn
        # PPN = 12% x DPP nilai lain (PMK 131/2024) gives the same number.
        assert dpp_nilai_lain(po.subtotal) * 12 // 100 == ppn


def test_money_helpers_refuse_rounding() -> None:
    assert ppn_amount(1_000_000) == 110_000
    assert total_with_ppn(1_000_000) == 1_110_000
    assert dpp_nilai_lain(1_200) == 1_100
    with pytest.raises(ValueError):
        ppn_amount(1_050)
    with pytest.raises(ValueError):
        dpp_nilai_lain(1_000)


def test_known_po_totals(ds: Dataset) -> None:
    # Spot checks against the table in SPEC §15.2.
    expected = {"P00001": 66_933_000, "P00020": 62_770_500, "P00027": 26_107_200}
    for number, total in expected.items():
        assert ds.po_by_number(number).total() == total


# -- master data ------------------------------------------------------------------------------


def test_npwp_16_digits_unique_and_valid(ds: Dataset) -> None:
    npwps = [ds.company.npwp] + [v.npwp for v in ds.vendors]
    npwps += [v.npwp for v in ds.external_vendors]
    assert len(npwps) == len(set(npwps)), "NPWP ganda"
    for value in npwps:
        assert re.fullmatch(r"\d{16}", value), f"NPWP bukan 16 digit: {value}"
        assert value.startswith("0990"), f"NPWP uji harus berawalan 0990: {value}"
        assert stdnum_npwp.is_valid(value), f"NPWP tidak lolos validasi format: {value}"


def test_bank_accounts_unique_and_banks_exist(ds: Dataset) -> None:
    bank_keys = {b.key for b in ds.banks}
    accounts = [v.bank_account for v in ds.vendors] + [v.bank_account for v in ds.external_vendors]
    numbers = [a.number for a in accounts]
    assert len(numbers) == len(set(numbers)), "nomor rekening ganda"
    for account in accounts:
        assert account.bank in bank_keys, f"bank {account.bank} tidak ada"
        assert account.number.isdigit()


def test_all_emails_use_test_domain(ds: Dataset) -> None:
    for email in all_emails(ds):
        assert re.fullmatch(r"[^@\s]+@[^@\s]+\.test", email), f"email bukan .test: {email}"


def test_vendor_without_history_and_unregistered_vendor(ds: Dataset) -> None:
    assert not [bill for po in ds.pos_of_vendor("v09") for bill in po.bills]
    assert "x01" not in {v.key for v in ds.vendors}
    assert "x01" in {v.key for v in ds.external_vendors}
    x01 = next(v for v in ds.external_vendors if v.key == "x01")
    assert x01.name not in {v.name for v in ds.vendors}
    assert x01.npwp not in {v.npwp for v in ds.vendors}
    # Every other registered vendor has at least one posted history bill.
    for vendor in ds.vendors:
        has_bill = any(po.bills for po in ds.pos_of_vendor(vendor.key))
        assert has_bill == (vendor.key != "v09"), vendor.key
        assert (vendor.history_note == "baru") == (vendor.key == "v09"), vendor.key


def test_summary_counts(ds: Dataset) -> None:
    """The numbers odoo:check expects (task M0.9)."""
    pos = ds.purchase_orders
    confirmed = [po for po in pos if po.is_confirmed]
    assert (len(ds.banks), len(ds.vendors), len(ds.products)) == (3, 10, 20)
    assert len(pos) == 56
    assert Counter(po.status for po in pos) == {"dikonfirmasi": 53, "batal": 1, "rfq": 2}
    assert sum(len(po.lines) for po in pos) == 65
    assert sum(po.is_fully_received for po in confirmed) == 48
    assert sum(po.is_partially_received for po in confirmed) == 3
    assert sum(not po.receipts for po in confirmed) == 2
    assert len(ds.history_bills) == 14


# -- data needed by the scenario catalogue (SPEC §15.3) ---------------------------------------


def comparable(po: PurchaseOrder) -> tuple:
    return (
        po.vendor,
        po.order_date,
        po.status,
        tuple((line.product, line.qty, line.unit_price) for line in po.lines),
        tuple((r.received_on, tuple(sorted(r.qty.items())), r.remainder) for r in po.receipts),
        po.bills,
    )


def test_twin_pos_are_identical(ds: Dataset) -> None:
    a, b = ds.po_by_number("P00035"), ds.po_by_number("P00036")
    assert comparable(a) == comparable(b)
    assert a.is_confirmed and a.is_fully_received and not a.bills


def test_cancelled_po_and_its_replacement(ds: Dataset) -> None:
    cancelled, replacement = ds.po_by_number("P00013"), ds.po_by_number("P00016")
    assert cancelled.status == "batal"
    assert replacement.is_confirmed and replacement.is_fully_received
    assert cancelled.vendor == replacement.vendor
    lines = [(line.product, line.qty, line.unit_price) for line in cancelled.lines]
    assert lines == [(line.product, line.qty, line.unit_price) for line in replacement.lines]


def test_mistyped_po_number_does_not_exist(ds: Dataset) -> None:
    assert ds.po_by_number("P00330") is None


def strong_candidates(
    ds: Dataset, vendor: str, invoice_lines: list[tuple[str, int, int]]
) -> list[str]:
    """BR-06: confirmed POs of the vendor where every invoice line has a product match with
    qty equal to the received-but-unbilled quantity and a price within tolerance."""
    found = []
    for po in ds.pos_of_vendor(vendor):
        if not po.is_confirmed:
            continue
        ok = True
        for product, qty, price in invoice_lines:
            try:
                line = po.line_for(product)
            except KeyError:
                ok = False
                break
            open_qty = po.received_qty(product) - po.billed_qty(product)
            within = price * 100 <= line.unit_price * (100 + PRICE_TOLERANCE_PERCENT)
            if open_qty != qty or not within:
                ok = False
                break
        if ok:
            found.append(po.number)
    return found


def test_only_one_strong_candidate_for_corrected_po_number(ds: Dataset) -> None:
    # v03 has several open POs; only P00033 matches 240 kg of p07 exactly.
    assert strong_candidates(ds, "v03", [("p07", 240, 32_000)]) == ["P00033"]
    open_v03 = [
        po.number
        for po in ds.pos_of_vendor("v03")
        if po.is_confirmed
        and any(po.received_qty(x.product) > po.billed_qty(x.product) for x in po.lines)
    ]
    assert open_v03 == ["P00024", "P00033", "P00034", "P00045"]


def test_twin_pos_give_two_strong_candidates(ds: Dataset) -> None:
    assert strong_candidates(ds, "v10", [("p20", 45, 98_000)]) == ["P00035", "P00036"]


@pytest.mark.parametrize(
    ("vendor", "number", "po_number"),
    [
        ("v02", "TWL/INV/2610/0153", "P00010"),
        ("v07", "LKP-2610-0088", "P00011"),
        ("v03", "PAS-24519", "P00021"),
        ("v06", "INV-2026-0412", "P00012"),
        ("v05", "RPK/1026/031", "P00014"),
    ],
)
def test_history_bills_needed_by_duplicate_checks_exist(
    ds: Dataset, vendor: str, number: str, po_number: str
) -> None:
    po = ds.po_by_number(po_number)
    assert po.vendor == vendor
    assert [b.vendor_invoice_number for b in po.bills] == [number]
    assert po.bills[0].status == "diposting"


def test_same_invoice_number_format_for_two_vendors(ds: Dataset) -> None:
    # Lets an invoice number collide across vendors (not a duplicate, BR-11).
    assert ds.vendor("v06").invoice_number_format == ds.vendor("v08").invoice_number_format


def test_threshold_only_exceeded_by_the_over_threshold_po(ds: Dataset) -> None:
    threshold = ds.approval_threshold
    assert ds.po_by_number("P00020").total() > threshold
    # Scenario POs (keys starting with "o") other than P00020 must stay at or below the
    # threshold, otherwise an extra approval reason would change their expected route.
    over = [
        po.number
        for po in ds.purchase_orders
        if po.key.startswith("o") and po.number != "P00020" and po.total() > threshold
    ]
    assert over == []


# -- label leakage ----------------------------------------------------------------------------


def test_dataset_contains_no_scenario_ids() -> None:
    text = DATASET_PATH.read_text(encoding="utf-8")
    found = sorted({m.group(0) for m in LABEL_PATTERN.finditer(text)})
    assert found == [], f"ID skenario bocor ke dataset: {found}"


def test_label_pattern_detects_ids() -> None:
    assert LABEL_PATTERN.search("lihat skenario QTY-01 di katalog")
    assert not LABEL_PATTERN.search("QTY-1 atau XQTY-01X")
