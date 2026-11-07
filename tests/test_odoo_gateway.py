"""AgentOdooGateway without a network: refusals happen before any request is sent."""

import logging
from decimal import Decimal

import httpx
import pytest

from fake_odoo import FakeOdoo, gateway
from faktur_beres.config import Settings
from faktur_beres.tools.errors import (
    AccessDenied,
    Conflict,
    ForbiddenOperation,
    NotFound,
    Rejected,
    ToolError,
    Unavailable,
)
from faktur_beres.tools.odoo_gateway import (
    AGENT_CONTEXT,
    NEVER_READ,
    READ_FIELDS,
    AgentOdooGateway,
)


def bill_values(**changes: object) -> dict:
    values: dict = {
        "move_type": "in_invoice",
        "partner_id": 7,
        "ref": "KSM/INV/2026/10/0458",
        "invoice_date": "2026-10-25",
        "invoice_origin": "P00015",
        "invoice_line_ids": [
            [0, 0, {"product_id": 3, "purchase_line_id": 19, "quantity": 1200, "price_unit": 18750}]
        ],
    }
    values.update(changes)
    return values


FORBIDDEN_CALLS = [
    ("account.move", "action_post", {"ids": [1]}),
    ("account.move", "button_cancel", {"ids": [1]}),
    ("account.move", "write", {"ids": [1], "vals": {"ref": "x"}}),
    ("account.move", "unlink", {"ids": [1]}),
    ("purchase.order", "button_confirm", {"ids": [1]}),
    ("purchase.order", "write", {"ids": [1], "vals": {}}),
    ("stock.picking", "button_validate", {"ids": [1]}),
    ("res.partner", "create", {"vals_list": [{"name": "x"}]}),
    ("res.partner.bank", "write", {"ids": [1], "vals": {"acc_number": "1"}}),
    ("ir.attachment", "unlink", {"ids": [1]}),
    ("res.users", "search_read", {"domain": [], "fields": ["login"]}),
    ("account.move", "search_read", {"domain": [], "fields": ["needed_terms"]}),
    ("account.move", "search_read", {"domain": [], "fields": []}),
    ("account.move", "read", {"ids": [1], "fields": ["id", "line_ids"]}),
    ("account.move", "search_read", {"domain": [], "fields": ["id"], "context": {"lang": "en"}}),
    ("account.move", "create", {"vals_list": [bill_values(), bill_values()]}),
]


@pytest.mark.parametrize(("model", "method", "kwargs"), FORBIDDEN_CALLS)
def test_forbidden_operations_send_no_request(model: str, method: str, kwargs: dict) -> None:
    fake = FakeOdoo()
    with pytest.raises(ForbiddenOperation):
        gateway(fake).call(model, method, **kwargs)
    assert fake.requests == []


@pytest.mark.parametrize(
    "values",
    [
        bill_values(move_type="out_invoice"),
        bill_values(move_type=None),
        bill_values(state="posted"),
        bill_values(state="draft"),  # state is never sent; drafts are Odoo's default
        bill_values(narration="x", company_id=1),
        bill_values(partner_id="7"),
        bill_values(invoice_line_ids=[]),
        bill_values(invoice_line_ids=[[1, 5, {"quantity": 1}]]),
        bill_values(invoice_line_ids=[[2, 5, 0]]),
        bill_values(invoice_line_ids=[[0, 0, {"product_id": 3, "quantity": 1}]]),
        bill_values(
            invoice_line_ids=[[0, 0, {"purchase_line_id": 1, "display_type": "line_note"}]]
        ),
        bill_values(invoice_line_ids=[[0, 0, {"purchase_line_id": 1, "account_id": 9}]]),
    ],
)
def test_only_plain_draft_vendor_bills_can_be_created(values: dict) -> None:
    fake = FakeOdoo()
    with pytest.raises(ForbiddenOperation):
        gateway(fake).create_vendor_bill(values)
    assert fake.requests == []


def test_reads_always_send_context_and_fields() -> None:
    fake = FakeOdoo(
        {"res.partner/search_read": [{"id": 7, "name": "PT A"}], "account.move/search_count": 3}
    )
    gw = gateway(fake)
    gw.search_read("res.partner", [["supplier_rank", ">", 0]], ["id", "name"], limit=5)
    gw.read("purchase.order", [3], ["id", "name"])
    assert gw.search_count("account.move", [["state", "=", "posted"]]) == 3
    for path, body in fake.requests:
        assert body["context"] == dict(AGENT_CONTEXT), path
    assert fake.requests[0][1]["fields"] == ["id", "name"]
    assert fake.requests[0][1]["limit"] == 5
    assert fake.requests[1][1]["fields"] == ["id", "name"]
    assert "fields" not in fake.requests[2][1]


def test_numbers_come_back_as_decimal() -> None:
    raw = httpx.Response(200, content=b'[{"id": 1, "price_unit": 185900.5}]')
    fake = FakeOdoo({"purchase.order.line/search_read": raw})
    (row,) = gateway(fake).search_read("purchase.order.line", [], ["id", "price_unit"])
    assert row["price_unit"] == Decimal("185900.5")


def test_create_vendor_bill_sends_one_create() -> None:
    fake = FakeOdoo({"account.move/create": [42]})
    assert gateway(fake).create_vendor_bill(bill_values()) == 42
    ((path, body),) = fake.requests
    assert path == "account.move/create"
    assert len(body["vals_list"]) == 1 and body["vals_list"][0]["move_type"] == "in_invoice"
    assert body["context"] == dict(AGENT_CONTEXT)


def agent_draft(state: str = "draft", writable: bool = True) -> FakeOdoo:
    return FakeOdoo(
        {
            "account.move/read": [{"id": 42, "move_type": "in_invoice", "state": state}],
            "account.move/has_access": writable,
            "ir.attachment/create": [9],
            "account.move/message_post": [11],
        }
    )


def test_attachment_and_note_on_the_agents_draft() -> None:
    fake = agent_draft()
    gw = gateway(fake)
    assert gw.create_attachment(42, "faktur.pdf", b"%PDF", "application/pdf") == 9
    assert gw.post_note(42, "Draft dibuat dari faktur.") == 11
    create = next(b for p, b in fake.requests if p == "ir.attachment/create")
    # The original file is kept as received: Odoo must not shrink and re-encode images.
    assert create["context"] == {**AGENT_CONTEXT, "image_no_postprocess": True}
    assert create["vals_list"][0] == {
        "name": "faktur.pdf",
        "res_model": "account.move",
        "res_id": 42,
        "datas": "JVBERg==",
        "mimetype": "application/pdf",
    }
    post = next(b for p, b in fake.requests if p == "account.move/message_post")
    assert (post["ids"], post["subtype_xmlid"], post["message_type"]) == (
        [42],
        "mail.mt_note",
        "comment",
    )


@pytest.mark.parametrize(("state", "writable"), [("posted", False), ("draft", False)])
def test_no_attachment_or_note_elsewhere(state: str, writable: bool) -> None:
    fake = agent_draft(state, writable)
    gw = gateway(fake)
    with pytest.raises(ForbiddenOperation):
        gw.create_attachment(42, "faktur.pdf", b"%PDF", "application/pdf")
    with pytest.raises(ForbiddenOperation):
        gw.post_note(42, "catatan")
    assert not [p for p in fake.methods() if p.endswith(("/create", "/message_post"))]


def test_empty_note_is_refused_without_request() -> None:
    fake = agent_draft()
    with pytest.raises(ForbiddenOperation):
        gateway(fake).post_note(42, "  ")
    assert fake.requests == []


def test_calls_are_logged_with_masked_parameters(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeOdoo({"res.partner/search_read": []})
    with caplog.at_level(logging.INFO, logger="faktur_beres.tools.odoo"):
        gateway(fake).search_read("res.partner", [["vat", "=", "0990427957220000"]], ["id", "name"])
    (record,) = [r for r in caplog.records if hasattr(r, "odoo_call")]
    entry = record.odoo_call
    assert (entry["model"], entry["method"], entry["status"]) == (
        "res.partner",
        "search_read",
        "ok",
    )
    assert entry["params"]["domain"] == [["vat", "=", "0990********0000"]]
    assert "0990427957220000" not in caplog.text
    assert isinstance(entry["duration_ms"], float)


def error(status: int, name: str) -> httpx.Response:
    return httpx.Response(status, json={"name": name, "message": "galat uji"})


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (error(403, "odoo.exceptions.AccessError"), AccessDenied),
        (error(401, "werkzeug.exceptions.Unauthorized"), AccessDenied),
        (error(404, "odoo.exceptions.MissingError"), NotFound),
        (error(409, "odoo.exceptions.LockError"), Conflict),
        (error(422, "odoo.exceptions.UserError"), Rejected),
        (error(500, "builtins.TypeError"), Unavailable),
    ],
)
def test_odoo_errors_become_tool_errors(response: httpx.Response, expected: type) -> None:
    fake = FakeOdoo({"account.move/create": response})
    with pytest.raises(expected) as exc:
        gateway(fake).create_vendor_bill(bill_values())
    assert exc.value.model == "account.move" and exc.value.status == response.status_code


def test_failed_call_is_logged_as_error(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeOdoo({"account.move/create": error(403, "odoo.exceptions.AccessError")})
    with (
        caplog.at_level(logging.INFO, logger="faktur_beres.tools.odoo"),
        pytest.raises(AccessDenied),
    ):
        gateway(fake).create_vendor_bill(bill_values())
    (record,) = [r for r in caplog.records if hasattr(r, "odoo_call")]
    assert (record.odoo_call["status"], record.odoo_call["error"]) == ("error", "OdooAccessError")


def test_allowlists_never_contain_needed_terms() -> None:
    assert all(not (fields & NEVER_READ) for fields in READ_FIELDS.values())
    assert all("id" in fields for fields in READ_FIELDS.values())


def test_from_settings_needs_the_agent_key() -> None:
    settings = Settings(_env_file=None, odoo_agent_api_key=None, odoo_admin_api_key="admin-key-x")
    with pytest.raises(ToolError):
        AgentOdooGateway.from_settings(settings)
