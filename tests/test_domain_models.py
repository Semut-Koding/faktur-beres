"""Tests for the canonical invoice model and the codes of SPEC §6."""

import re
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from faktur_beres.config import REPO_ROOT
from faktur_beres.domain.invoice import (
    Channel,
    IntakeContext,
    InvoiceData,
    InvoiceLine,
    Seller,
    TextFragment,
    TextSource,
)
from faktur_beres.domain.reasons import ROUTE_ORDER, InvoiceStatus, ReasonCode, Route

SPEC = (REPO_ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")


def section(start: str, stop: str) -> str:
    return SPEC.split(start, 1)[1].split(stop, 1)[0]


def test_reason_codes_match_spec_6_2() -> None:
    rows = re.findall(r"^\| ([BC]\d) \| `([a-z_]+)` \|", section("### 6.2", "### 6.3"), re.M)
    assert [(n, c) for n, c in rows] == [(r.number, r.value) for r in ReasonCode]
    assert {r.route for r in ReasonCode if r.number.startswith("B")} == {Route.B}


def test_routes_match_spec_6_1() -> None:
    rows = re.findall(r"^\| ([ABC]) \| `([a-z_]+)` \|", section("### 6.1", "### 6.2"), re.M)
    assert rows == [(r.value, r.code) for r in ROUTE_ORDER]
    assert [r.rank for r in ROUTE_ORDER] == [0, 1, 2]


def test_statuses_match_spec_6_3() -> None:
    rows = re.findall(r"^\| `([a-z_]+)` \|", section("### 6.3", "## 7."), re.M)
    assert rows == [s.value for s in InvoiceStatus]


def invoice(**changes: object) -> InvoiceData:
    data: dict[str, object] = {
        "invoice_number": "KSM/INV/2026/10/0001",
        "invoice_date": date(2026, 10, 25),
        "seller": {"name": "PT Contoh Fiktif"},
        "lines": [
            {
                "description": "Barang uji",
                "quantity": "10",
                "unit": "kg",
                "unit_price": "1500",
                "amount": "15000",
            }
        ],
        "subtotal": "15000",
        "vat": "1650",
        "total": "16650",
    }
    data.update(changes)
    return InvoiceData.model_validate(data)


def test_minimal_invoice_follows_br_02() -> None:
    inv = invoice()
    assert inv.lines[0].quantity == Decimal("10")
    assert inv.po_number is None and inv.nsfp is None and inv.bank_account is None
    assert inv.notes == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"seller": {"npwp": "0990000000000000"}},  # seller name is required
        {"invoice_number": ""},
        {"lines": []},
        {"total": 16650.0},  # float refused
        {
            "lines": [
                {"description": "x", "quantity": 1.5, "unit": "kg", "unit_price": 1, "amount": 1}
            ]
        },
        {"warna": "merah"},  # unknown key
    ],
)
def test_invalid_invoices_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        invoice(**changes)


def test_invoice_round_trips_through_json() -> None:
    inv = invoice(
        po_number="P00015",
        nsfp="04002600007215090",
        notes=["catatan"],
        bank_account={"bank": "Bank Fiktif", "number": "123-456", "holder": "PT Contoh Fiktif"},
        dpp_nilai_lain="13750",
        tax_invoice_date="2026-10-25",
    )
    again = InvoiceData.model_validate_json(inv.model_dump_json())
    assert again == inv
    assert isinstance(again.total, Decimal)


def test_models_are_frozen() -> None:
    line = InvoiceLine(
        description="x", quantity=Decimal(1), unit="kg", unit_price=Decimal(1), amount=Decimal(1)
    )
    with pytest.raises(ValidationError):
        line.quantity = Decimal(2)  # type: ignore[misc]
    assert Seller(name="PT A").name == "PT A"


def test_intake_and_fragments() -> None:
    ctx = IntakeContext(channel=Channel.EMAIL, sender_email="a@b.test", file_sha256="ab" * 32)
    assert IntakeContext.model_validate_json(ctx.model_dump_json()) == ctx
    with pytest.raises(ValidationError):
        IntakeContext(channel=Channel.UPLOAD, file_sha256="xyz")
    frag = TextFragment(source=TextSource.HIDDEN_TEXT, text="halo", where="halaman 1")
    assert TextFragment.model_validate(frag.model_dump(mode="json")) == frag
