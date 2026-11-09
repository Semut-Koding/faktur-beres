"""odoo_tools reads and OdooErpReader, against a fake JSON-2 server (no network)."""

from decimal import Decimal

import httpx
import pytest

from fake_odoo import FakeOdoo, gateway
from faktur_beres.domain.erp import BillState, ErpReader, PoState
from faktur_beres.tools import odoo_tools
from faktur_beres.tools.odoo_tools import OdooErpReader, VendorSearch

VENDORS = [
    {
        "id": 7,
        "name": "PT Kertas Fiktif",
        "vat": "0990427957220000",
        "email": "Tagihan@kertas.test",
    },
    {"id": 8, "name": "PT Kertas Fiktif Lain", "vat": "0990286171486000", "email": False},
]


def test_find_vendors_filters_loose_odoo_searches() -> None:
    fake = FakeOdoo({"res.partner/search_read": VENDORS})
    gw = gateway(fake)
    by_vat = odoo_tools.find_vendors(gw, VendorSearch(npwp="0990.4279.5722.0000"))
    assert [v.id for v in by_vat] == [7]
    domain = fake.requests[0][1]["domain"]
    assert domain == [["supplier_rank", ">", 0], ["vat", "ilike", "57220000"]]
    assert [
        v.id for v in odoo_tools.find_vendors(gw, VendorSearch(name="P.T. Kertas  Fiktif"))
    ] == [7]
    assert [
        v.id for v in odoo_tools.find_vendors(gw, VendorSearch(email="tagihan@KERTAS.test"))
    ] == [7]
    assert odoo_tools.find_vendors(gw, VendorSearch()) == ()
    assert odoo_tools.find_vendors(gw, VendorSearch(name="PT Kertas Fiktif Lain"))[0].email is None


def test_bank_accounts_and_owners() -> None:
    rows = [
        {
            "id": 1,
            "partner_id": [7, "PT A"],
            "acc_number": "651-008-2689",
            "bank_id": [3, "Bank Fiktif"],
            "acc_holder_name": False,
        },
    ]
    fake = FakeOdoo({"res.partner.bank/search_read": rows})
    gw = gateway(fake)
    (acc,) = odoo_tools.vendor_bank_accounts(gw, 7)
    assert (acc.partner_id, acc.digits, acc.bank_name, acc.holder) == (
        7,
        "6510082689",
        "Bank Fiktif",
        None,
    )
    assert odoo_tools.bank_account_owners(gw, "6510082689")[0].id == 1
    assert fake.requests[-1][1]["domain"] == [["sanitized_acc_number", "=", "6510082689"]]
    assert odoo_tools.bank_account_owners(gw, "---") == ()


PO_ROW = {
    "id": 30,
    "name": "P00027",
    "partner_id": [7, "PT A"],
    "state": "purchase",
    "locked": True,
    "currency_id": [12, "IDR"],
    "fiscal_position_id": False,
    "payment_term_id": [4, "30 Hari"],
    "order_line": [91, 92],
}
LINES = (
    b'[{"id": 91, "order_id": [30, "P00027"], "product_id": [5, "[KRT-KFT-200] Kraft"], '
    b'"name": "[KRT-KFT-200] Kraft Liner 200 gsm", "product_qty": 2100.0, "qty_received": 1680.0,'
    b' "qty_invoiced": 0.0, "price_unit": 11200.0, "product_uom_id": [12, "kg"], "tax_ids": [2],'
    b' "display_type": false},'
    b' {"id": 92, "order_id": [30, "P00027"], "product_id": false, "name": "Bagian", '
    b'"product_qty": 0.0, "qty_received": 0.0, "qty_invoiced": 0.0, "price_unit": 0.0, '
    b'"product_uom_id": false, "tax_ids": [], "display_type": "line_section"}]'
)


def po_fake() -> FakeOdoo:
    return FakeOdoo(
        {
            "purchase.order/search_read": [PO_ROW],
            "purchase.order.line/read": httpx.Response(200, content=LINES),
            "product.product/read": [
                {
                    "id": 5,
                    "name": "Kraft Liner 200 gsm",
                    "default_code": "KRT-KFT-200",
                    "product_tmpl_id": [50, "Kraft"],
                }
            ],
            "product.supplierinfo/search_read": [
                {
                    "id": 1,
                    "partner_id": [7, "PT A"],
                    "product_tmpl_id": [50, "Kraft"],
                    "product_id": False,
                    "product_code": "KL-200",
                }
            ],
        }
    )


def test_purchase_order_with_lines_codes_and_decimals() -> None:
    po = odoo_tools.purchase_order_by_number(gateway(po_fake()), " p00027 ")
    assert po is not None
    assert (po.number, po.state, po.locked, po.vendor_id, po.payment_term_id) == (
        "P00027",
        PoState.PURCHASE,
        True,
        7,
        4,
    )
    (line,) = po.lines  # the section line is skipped
    assert (line.product_code, line.vendor_codes, line.uom_name, line.tax_ids) == (
        "KRT-KFT-200",
        ("KL-200",),
        "kg",
        (2,),
    )
    assert (line.quantity, line.qty_received, line.open_quantity) == (2100, 1680, 1680)
    assert isinstance(line.price_unit, Decimal)


def test_purchase_order_number_must_match_exactly() -> None:
    fake = po_fake()
    fake.answers["purchase.order/search_read"] = [{**PO_ROW, "name": "P00027-B"}]
    assert odoo_tools.purchase_order_by_number(gateway(fake), "P00027") is None
    assert odoo_tools.purchase_order_by_number(gateway(FakeOdoo()), "P00027") is None


def test_vendor_bills_with_lines() -> None:
    fake = FakeOdoo(
        {
            "account.move/search_read": [
                {
                    "id": 3,
                    "partner_id": [7, "PT A"],
                    "ref": "KSM/INV/2026/08/0412",
                    "invoice_date": "2026-08-08",
                    "state": "posted",
                    "amount_total": 66933000,
                    "create_uid": [2, "Administrator"],
                }
            ],
            "account.move.line/search_read": [
                {
                    "id": 31,
                    "move_id": [3, "BILL"],
                    "purchase_line_id": [1, "P00001"],
                    "product_id": [5, "x"],
                    "quantity": 3000,
                    "price_unit": 14500,
                }
            ],
        }
    )
    (bill,) = odoo_tools.vendor_bills(gateway(fake), 7)
    assert (bill.state, bill.amount_total, bill.created_by, bill.invoice_date.isoformat()) == (
        BillState.POSTED,
        66933000,
        2,
        "2026-08-08",
    )
    assert (bill.lines[0].purchase_line_id, bill.lines[0].quantity) == (1, 3000)
    domain = fake.requests[0][1]["domain"]
    assert ["state", "in", ["draft", "posted"]] in domain


def test_reader_is_an_erp_reader() -> None:
    fake = FakeOdoo(
        {
            "res.company/search_read": [{"id": 1, "name": "PT Pembeli", "vat": "0990229163926000"}],
            "account.move/search_count": 0,
        }
    )
    reader: ErpReader = OdooErpReader(gateway(fake))
    assert reader.company().vat == "0990229163926000"
    reader.company()
    assert fake.methods().count("res.company/search_read") == 1  # cached
    assert reader.posted_bill_count(7) == 0


def test_unexpected_float_is_refused() -> None:
    with pytest.raises(TypeError):
        odoo_tools._decimal(1.5)


def test_registered_banks() -> None:
    fake = FakeOdoo(
        {
            "res.bank/search_read": [
                {"id": 2, "name": "Bank Mandala Raya"},
                {"id": 3, "name": False},
            ]
        }
    )
    reader = OdooErpReader(gateway(fake))
    assert [(b.id, b.name) for b in reader.banks()] == [(2, "Bank Mandala Raya")]
    reader.banks()
    assert fake.methods().count("res.bank/search_read") == 1  # cached
    assert fake.requests[0][1]["fields"] == ["id", "name"]
