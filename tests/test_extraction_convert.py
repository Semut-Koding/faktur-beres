"""Conversion of the printed text to ``InvoiceData``, with problems per field path (BR-02)."""

from decimal import Decimal

import pytest

from extraction_support import extracted, rp, with_line
from faktur_beres.domain.policy import load_policy
from faktur_beres.evals.labels import load_labels, truth_to_invoice
from faktur_beres.extraction.convert import ProblemKind, convert, json_path
from faktur_beres.generator.label_schema import DocumentTruth

POLICY = load_policy()
LABELS = load_labels()
COC_01 = LABELS["COC-01"].kebenaran_dokumen
PJK_01 = LABELS["PJK-01"].kebenaran_dokumen


def all_truths() -> list[tuple[str, DocumentTruth]]:
    found = [(sid, label.kebenaran_dokumen) for sid, label in LABELS.items()]
    for sid, label in LABELS.items():
        reply = label.simulasi.balasan_vendor
        if reply and reply.kebenaran_dokumen:
            found.append((f"{sid}-balasan", reply.kebenaran_dokumen))
    return found


def kinds(conversion_problems: object) -> list[tuple[str, ProblemKind]]:
    return [(p.path, p.kind) for p in conversion_problems]  # type: ignore[attr-defined]


@pytest.mark.parametrize(("name", "truth"), all_truths(), ids=[t[0] for t in all_truths()])
def test_a_perfect_reading_gives_the_truth(name: str, truth: DocumentTruth) -> None:
    result = convert(extracted(truth), POLICY)
    assert result.invoice == truth_to_invoice(truth)
    assert result.blocking == ()


@pytest.mark.aturan("BR-02")
def test_required_fields_are_checked() -> None:
    result = convert(
        extracted(COC_01, invoice_number=None, invoice_date=" ", seller=None, total=None),
        POLICY,
    )
    assert result.invoice is None
    assert kinds(result.problems) == [
        ("nomor_faktur", ProblemKind.MISSING_REQUIRED),
        ("tanggal_faktur", ProblemKind.MISSING_REQUIRED),
        ("penjual.nama", ProblemKind.MISSING_REQUIRED),
        ("total", ProblemKind.MISSING_REQUIRED),
    ]


@pytest.mark.aturan("BR-02")
def test_required_line_fields_and_at_least_one_line() -> None:
    result = convert(with_line(COC_01, 1, unit=None, amount=None, description=""), POLICY)
    assert kinds(result.problems) == [
        ("baris[1].deskripsi", ProblemKind.MISSING_REQUIRED),
        ("baris[1].satuan", ProblemKind.MISSING_REQUIRED),
        ("baris[1].jumlah", ProblemKind.MISSING_REQUIRED),
    ]
    assert kinds(convert(extracted(COC_01, lines=[]), POLICY).problems) == [
        ("baris", ProblemKind.MISSING_REQUIRED)
    ]


@pytest.mark.aturan("BR-02")
def test_optional_fields_may_be_empty() -> None:
    result = convert(
        extracted(COC_01, po_number=None, nsfp=None, bank_account=None, buyer=None, due_date=None),
        POLICY,
    )
    assert result.problems == () and result.invoice is not None
    assert result.invoice.buyer is None and result.invoice.bank_account is None


def test_unreadable_numbers_and_dates() -> None:
    result = convert(extracted(COC_01, total="Rp 1O.000.000", invoice_date="31/02/2026"), POLICY)
    assert result.invoice is None
    assert kinds(result.problems) == [
        ("tanggal_faktur", ProblemKind.UNREADABLE),
        ("total", ProblemKind.UNREADABLE),
    ]
    assert result.problems[1].text == "Rp 1O.000.000"


def test_a_number_style_nothing_proves_is_ambiguous() -> None:
    line = {
        "vendor_code": None,
        "description": "Barang uji",
        "quantity": "5",
        "unit": "Unit",
        "unit_price": "1.500",
        "amount": "7.500",
    }
    reading = extracted(
        COC_01, lines=[line], subtotal="7.500", dpp_nilai_lain=None, vat="825", total="8.325"
    )
    result = convert(reading, POLICY)
    assert result.invoice is None
    assert {p.kind for p in result.problems} == {ProblemKind.AMBIGUOUS}
    assert ("baris[1].harga_satuan", ProblemKind.AMBIGUOUS) in kinds(result.problems)
    assert result.values["total"] == "8.325"  # the text, since no number could be read


def test_two_number_styles_in_one_document_are_ambiguous() -> None:
    result = convert(extracted(COC_01, total="Rp 26,700,000.00"), POLICY)
    assert result.invoice is None
    assert {p.kind for p in result.problems} == {ProblemKind.AMBIGUOUS}


def test_arithmetic_problems_name_every_field_of_the_equation() -> None:
    hrg_05 = LABELS["HRG-05"].kebenaran_dokumen
    result = convert(extracted(hrg_05), POLICY)
    assert result.invoice == truth_to_invoice(hrg_05)  # kept as printed for BR-03
    arithmetic = [p for p in result.problems if p.kind is ProblemKind.ARITHMETIC]
    paths = [p.path for p in arithmetic]
    wrong = next(
        i for i, line in enumerate(hrg_05.baris, 1) if line.qty * line.harga_satuan != line.jumlah
    )
    assert paths[:3] == [
        f"baris[{wrong}].qty",
        f"baris[{wrong}].harga_satuan",
        f"baris[{wrong}].jumlah",
    ]
    assert all(p.text is not None for p in arithmetic)


def test_total_mismatch_names_subtotal_vat_and_total() -> None:
    result = convert(extracted(COC_01, total=rp(COC_01.total + 1000)), POLICY)
    assert kinds(result.problems) == [
        ("subtotal", ProblemKind.ARITHMETIC),
        ("ppn", ProblemKind.ARITHMETIC),
        ("total", ProblemKind.ARITHMETIC),
    ]
    assert result.invoice is not None and result.blocking == ()


def test_npwp_and_nsfp_formats_are_problems_but_values_stay_as_printed() -> None:
    seller = {
        "name": COC_01.penjual.nama,
        "npwp": "01.234.567.8-901.000",  # 15 digits, the old format
        "address": COC_01.penjual.alamat,
        "email": COC_01.penjual.email,
    }
    result = convert(extracted(COC_01, seller=seller, nsfp="040.026-00.0721514"), POLICY)
    assert kinds(result.problems) == [
        ("penjual.npwp", ProblemKind.NPWP_FORMAT),
        ("nsfp", ProblemKind.NSFP_FORMAT),
    ]
    assert result.invoice is not None
    assert result.invoice.seller.npwp == "01.234.567.8-901.000"
    assert result.values["penjual.npwp"] == "012345678901000"


def test_normalised_values_compare_readings() -> None:
    a = convert(extracted(PJK_01), POLICY)
    b = convert(extracted(PJK_01, total="Rp 14.918.400,00", invoice_date="4 November 2026"), POLICY)
    assert a.values["total"] == b.values["total"] == "14918400"
    assert a.values["tanggal_faktur"] == b.values["tanggal_faktur"] == "2026-11-04"
    assert a.texts["total"] != b.texts["total"]
    assert b.invoice is not None and b.invoice.total == Decimal("14918400.00")


def test_json_paths() -> None:
    assert json_path("baris[2].jumlah") == "lines[1].amount"
    assert json_path("penjual.npwp") == "seller.npwp"
    assert json_path("rekening.atas_nama") == "bank_account.holder"
    assert json_path("ppn") == "vat"
