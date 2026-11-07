"""create_draft_bill: natural key, one create call, read-back verification (no network)."""

from datetime import date
from decimal import Decimal

from fake_odoo import FakeOdoo, gateway
from faktur_beres.domain.rules.bill_plan import BillLinePlan, BillPlan
from faktur_beres.tools.odoo_tools import create_draft_bill

PLAN = BillPlan(
    partner_id=7,
    currency_id=12,
    invoice_origin="P00015",
    invoice_payment_term_id=4,
    ref="KSM/INV/2026/10/0458",
    invoice_date=date(2026, 10, 25),
    partner_bank_id=3,
    lines=(
        BillLinePlan(
            product_id=5,
            product_uom_id=12,
            tax_ids=(2,),
            purchase_line_id=19,
            name="[KRT-IVR-310] Ivory Board 310 gsm",
            quantity=Decimal(1200),
            price_unit=Decimal(18750),
            po_number="P00015",
            product_code="KRT-IVR-310",
        ),
    ),
    expected_total=Decimal(24975000),
    expected_untaxed=Decimal(22500000),
)
KEY = "a" * 64


def bill_row(**changes: object) -> dict:
    row: dict = {
        "id": 42,
        "partner_id": [7, "PT A"],
        "ref": PLAN.ref,
        "invoice_date": "2026-10-25",
        "state": "draft",
        "amount_total": 24975000,
        "create_uid": [5, "Agen"],
        "move_type": "in_invoice",
    }
    row.update(changes)
    return row


LINE = {
    "id": 1,
    "move_id": [42, "x"],
    "purchase_line_id": [19, "P00015"],
    "product_id": [5, "x"],
    "quantity": 1200,
    "price_unit": 18750,
}


def fake(existing: list[dict], read_back: dict | None = None, writable: bool = True) -> FakeOdoo:
    return FakeOdoo(
        {
            "account.move/search_read": existing,
            "account.move.line/search_read": [LINE],
            "account.move/create": [42],
            "account.move/read": [read_back or bill_row()],
            "account.move/has_access": writable,
        }
    )


def test_creates_one_bill_and_verifies_it() -> None:
    odoo = fake([])
    result = create_draft_bill(gateway(odoo), PLAN, KEY)
    assert (result.outcome, result.bill_id, result.verified, result.differences) == (
        "created",
        42,
        True,
        (),
    )
    assert result.total == 24975000 and result.idempotency_key == KEY
    creates = [b for p, b in odoo.requests if p == "account.move/create"]
    assert len(creates) == 1 and len(creates[0]["vals_list"][0]["invoice_line_ids"]) == 1


def test_reuses_the_agents_identical_draft() -> None:
    odoo = fake(
        [bill_row(ref="KSM-INV-2026-10-0458")], read_back=bill_row(ref="KSM-INV-2026-10-0458")
    )
    # The stored ref differs only in separators: same natural key, but the content check
    # compares the ref exactly, so this draft is not identical and becomes a conflict.
    assert create_draft_bill(gateway(odoo), PLAN, KEY).outcome == "conflict"
    odoo = fake([bill_row()])
    result = create_draft_bill(gateway(odoo), PLAN, KEY)
    assert (result.outcome, result.bill_id, result.verified) == ("reused", 42, True)
    assert "account.move/create" not in odoo.methods()


def test_posted_bill_with_the_same_key_is_a_conflict() -> None:
    odoo = fake([bill_row(id=3, state="posted", create_uid=[2, "Admin"])])
    result = create_draft_bill(gateway(odoo), PLAN, KEY)
    assert (result.outcome, result.conflicts, result.verified) == ("conflict", (3,), False)
    assert "account.move/create" not in odoo.methods()


def test_draft_of_someone_else_is_a_conflict() -> None:
    odoo = fake([bill_row()], writable=False)
    assert create_draft_bill(gateway(odoo), PLAN, KEY).outcome == "conflict"
    assert "account.move/create" not in odoo.methods()


def test_failed_verification_is_reported_not_repaired() -> None:
    odoo = fake([], read_back=bill_row(amount_total=24975002))
    result = create_draft_bill(gateway(odoo), PLAN, KEY)
    assert (result.outcome, result.verified) == ("created", False)
    assert any("total" in d for d in result.differences)
    assert [p for p in odoo.methods() if p.endswith(("/write", "/unlink"))] == []


def test_total_within_one_rupiah_passes() -> None:
    result = create_draft_bill(
        gateway(fake([], read_back=bill_row(amount_total=24975001))), PLAN, KEY
    )
    assert result.verified
