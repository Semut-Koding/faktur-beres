"""Integration: Odoo itself refuses what the agent must not do (``npm run test:integrasi``).

The calls go straight through ``OdooClient`` with the agent key, without the gateway, so every
refusal below comes from Odoo (addon faktur_beres_agent and Odoo's own checks). Each case prints
its HTTP status and Odoo error name for the report.
"""

from collections.abc import Callable
from typing import Any

import pytest

from faktur_beres.evals.labels import load_labels
from faktur_beres.odoo import OdooClient, OdooError
from faktur_beres.tools import odoo_tools
from faktur_beres.tools.odoo_gateway import READ_FIELDS, AgentOdooGateway
from faktur_beres.tools.odoo_tools import OdooErpReader
from integrasi_support import keys, original_file

pytestmark = pytest.mark.integrasi
CTX = {"lang": "id_ID", "tz": "Asia/Jakarta"}


@pytest.fixture(scope="module")
def draft(agent_gateway: AgentOdooGateway, odoo_erp: OdooErpReader) -> dict[str, int]:
    """A draft vendor bill of the agent, with one attachment and one note (made through tools)."""
    from integrasi_support import analyse_label

    label = load_labels()["COC-02"]
    analysis = analyse_label(label, odoo_erp)
    assert analysis.bill_plan is not None
    k = keys(label, f"vendor:{analysis.bill_plan.partner_id}")
    bill = odoo_tools.create_draft_bill(agent_gateway, analysis.bill_plan, k["create_draft_bill"])
    assert bill.verified and bill.bill_id is not None
    name, data, mimetype = original_file(label)
    attachment = odoo_tools.attach_file(agent_gateway, bill.bill_id, name, data, mimetype)
    note = odoo_tools.post_note(agent_gateway, bill.bill_id, "Catatan uji hak.", k["post_note"])
    lines = agent_gateway.search_read(
        "account.move.line", [["move_id", "=", bill.bill_id]], ["id"], limit=1
    )
    return {
        "bill": bill.bill_id,
        "line": lines[0]["id"],
        "attachment": attachment.attachment_id,
        "message": note.message_id,
    }


def one(client: OdooClient, model: str, domain: list[Any]) -> int:
    rows = client.search_read(model, domain, ["id"], limit=1, context=CTX)
    assert rows, (model, domain)
    return int(rows[0]["id"])


@pytest.fixture(scope="module")
def seed(agent_client: OdooClient) -> dict[str, int]:
    """Seed records the agent may read but never change."""
    c = agent_client
    vendor = one(c, "res.partner", [["name", "=", "PT Kertas Sagara Mandiri"]])
    po = one(c, "purchase.order", [["name", "=", "P00015"]])
    picking_open = one(c, "stock.picking", [["origin", "=", "P00027"], ["state", "=", "assigned"]])
    picking_done = one(c, "stock.picking", [["origin", "=", "P00015"], ["state", "=", "done"]])
    return {
        "vendor": vendor,
        "bank": one(c, "res.partner.bank", [["partner_id", "=", vendor]]),
        "supplierinfo": one(c, "product.supplierinfo", [["partner_id", "=", vendor]]),
        "po": po,
        "rfq": one(c, "purchase.order", [["name", "=", "P00053"]]),
        "picking_open": picking_open,
        "picking_done": picking_done,
        "move_line": one(c, "stock.move.line", [["picking_id", "=", picking_done]]),
        "posted_bill": one(
            c, "account.move", [["move_type", "=", "in_invoice"], ["state", "=", "posted"]]
        ),
        "product": one(c, "product.product", [["default_code", "=", "KRT-IVR-310"]]),
    }


def refused(case: str, status: int, name: str, call: Callable[[], object]) -> None:
    with pytest.raises(OdooError) as exc:
        call()
    error = exc.value
    print(f"\n  penolakan | {case} | HTTP {error.status} | {error.name}")
    assert (error.status, error.name) == (status, name), case


ACCESS = (403, "odoo.exceptions.AccessError")
USER = (422, "odoo.exceptions.UserError")


def test_posting_and_state_changes_on_the_agents_draft(agent_client, draft) -> None:
    c, bill = agent_client, draft["bill"]
    refused(
        "action_post draft agent",
        *ACCESS,
        lambda: c.call("account.move", "action_post", ids=[bill], context=CTX),
    )
    refused(
        "button_cancel draft agent",
        *ACCESS,
        lambda: c.call("account.move", "button_cancel", ids=[bill], context=CTX),
    )
    refused(
        "write state=cancel draft agent",
        *ACCESS,
        lambda: c.write("account.move", [bill], {"state": "cancel"}, context=CTX),
    )
    refused(
        "button_draft draft agent",
        *USER,
        lambda: c.call("account.move", "button_draft", ids=[bill], context=CTX),
    )
    (row,) = c.read("account.move", [bill], ["state"], context=CTX)
    assert row["state"] == "draft"


def test_nothing_of_the_draft_can_be_deleted(agent_client, draft) -> None:
    c = agent_client
    refused(
        "unlink draft",
        *ACCESS,
        lambda: c.call("account.move", "unlink", ids=[draft["bill"]], context=CTX),
    )
    refused(
        "unlink baris draft",
        *ACCESS,
        lambda: c.call("account.move.line", "unlink", ids=[draft["line"]], context=CTX),
    )
    refused(
        "unlink lampiran",
        *ACCESS,
        lambda: c.call("ir.attachment", "unlink", ids=[draft["attachment"]], context=CTX),
    )
    refused(
        "unlink pesan chatter",
        *ACCESS,
        lambda: c.call("mail.message", "unlink", ids=[draft["message"]], context=CTX),
    )


def test_only_draft_vendor_bills_can_be_created(agent_client, seed) -> None:
    c = agent_client
    refused(
        "create out_invoice",
        *ACCESS,
        lambda: c.create(
            "account.move", {"move_type": "out_invoice", "partner_id": seed["vendor"]}, context=CTX
        ),
    )
    posted = {
        "move_type": "in_invoice",
        "partner_id": seed["vendor"],
        "state": "posted",
        "ref": "UJI-HAK-1",
    }
    refused(
        "create in_invoice state posted",
        *USER,
        lambda: c.create("account.move", posted, context=CTX),
    )
    cancelled = {**posted, "state": "cancel", "ref": "UJI-HAK-2"}
    refused(
        "create in_invoice state cancel",
        *ACCESS,
        lambda: c.create("account.move", cancelled, context=CTX),
    )


def test_posted_history_bill_cannot_be_changed(agent_client, seed) -> None:
    c, bill = agent_client, seed["posted_bill"]
    refused(
        "write tagihan riwayat terposting",
        *ACCESS,
        lambda: c.write("account.move", [bill], {"narration": "x"}, context=CTX),
    )
    refused(
        "button_draft tagihan riwayat",
        *ACCESS,
        lambda: c.call("account.move", "button_draft", ids=[bill], context=CTX),
    )


def test_master_data_purchase_orders_and_receipts_are_read_only(agent_client, seed) -> None:
    c, s = agent_client, seed
    cases = [
        ("write PO", "purchase.order", "write", {"ids": [s["po"]], "vals": {"partner_ref": "x"}}),
        ("button_confirm RFQ", "purchase.order", "button_confirm", {"ids": [s["rfq"]]}),
        ("button_cancel PO", "purchase.order", "button_cancel", {"ids": [s["po"]]}),
        ("write vendor", "res.partner", "write", {"ids": [s["vendor"]], "vals": {"comment": "x"}}),
        (
            "write rekening",
            "res.partner.bank",
            "write",
            {"ids": [s["bank"]], "vals": {"acc_holder_name": "x"}},
        ),
        (
            "write info pemasok",
            "product.supplierinfo",
            "write",
            {"ids": [s["supplierinfo"]], "vals": {"price": 1}},
        ),
        (
            "write penerimaan",
            "stock.picking",
            "write",
            {"ids": [s["picking_done"]], "vals": {"origin": "x"}},
        ),
        (
            "button_validate penerimaan terbuka",
            "stock.picking",
            "button_validate",
            {"ids": [s["picking_open"]]},
        ),
        (
            "write stock.move.line",
            "stock.move.line",
            "write",
            {"ids": [s["move_line"]], "vals": {"quantity": 1}},
        ),
        ("unlink stock.move.line", "stock.move.line", "unlink", {"ids": [s["move_line"]]}),
    ]
    for case, model, method, kwargs in cases:
        refused(case, *ACCESS, lambda m=model, me=method, k=kwargs: c.call(m, me, context=CTX, **k))
    new_line = {"picking_id": s["picking_done"], "product_id": s["product"], "quantity": 1}
    refused(
        "create stock.move.line",
        *ACCESS,
        lambda: c.create("stock.move.line", new_line, context=CTX),
    )


def test_every_allowlisted_field_is_readable_by_the_agent(agent_client) -> None:
    missing = {}
    for model, fields in READ_FIELDS.items():
        available = agent_client.fields_get(model, ["type"], context=CTX)
        gone = sorted(set(fields) - set(available))
        if gone:
            missing[model] = gone
    assert missing == {}
