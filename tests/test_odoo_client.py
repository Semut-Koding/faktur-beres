"""Tests for the Odoo JSON-2 client, using httpx.MockTransport (no Odoo needed)."""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest
from pydantic import SecretStr

from faktur_beres.odoo import (
    OdooAccessError,
    OdooAuthError,
    OdooClient,
    OdooConnectionError,
    OdooError,
    OdooNotFoundError,
    OdooServerError,
    OdooValidationError,
)

KEY = "0123456789abcdef0123456789abcdef01234567"
BASE_URL = "http://odoo.test:28069"


class Recorder:
    """Mock transport handler that records requests and replays canned responses."""

    def __init__(self, *responses: httpx.Response | Exception | Callable[[httpx.Request], object]):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        if callable(item) and not isinstance(item, httpx.Response):
            return httpx.Response(200, json=item(request))
        return item


def make_client(recorder: Recorder, **kwargs) -> tuple[OdooClient, list[float]]:
    sleeps: list[float] = []
    client = OdooClient(
        BASE_URL,
        "fakturberes",
        kwargs.pop("api_key", KEY),
        transport=httpx.MockTransport(recorder),
        sleep=sleeps.append,
        **kwargs,
    )
    return client, sleeps


def odoo_error(status: int, name: str, message: str = "ditolak") -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "name": name,
            "message": message,
            "arguments": [message],
            "context": {},
            "debug": "Traceback (most recent call last): ...",
        },
    )


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


# -- request format ---------------------------------------------------------------------------


def test_headers_and_url() -> None:
    rec = Recorder(httpx.Response(200, json=[{"id": 1, "name": "PT Uji"}]))
    client, _ = make_client(rec)

    result = client.search_read("res.partner", [["is_company", "=", True]], ["name"], limit=1)

    assert result == [{"id": 1, "name": "PT Uji"}]
    (request,) = rec.requests
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/json/2/res.partner/search_read"
    assert request.headers["Authorization"] == f"bearer {KEY}"
    assert request.headers["X-Odoo-Database"] == "fakturberes"
    assert request.headers["Content-Type"].startswith("application/json")
    assert request.headers["User-Agent"] == "faktur-beres/0.1"


def test_custom_user_agent_and_secret_str_key() -> None:
    rec = Recorder(httpx.Response(200, json=3))
    client, _ = make_client(rec, api_key=SecretStr(KEY), user_agent="faktur-beres-agent/0.1")

    assert client.search_count("purchase.order") == 3
    assert rec.requests[0].headers["User-Agent"] == "faktur-beres-agent/0.1"
    assert rec.requests[0].headers["Authorization"] == f"bearer {KEY}"


def test_all_arguments_are_sent_by_name() -> None:
    rec = Recorder(httpx.Response(200, json=True))
    client, _ = make_client(rec)

    client.call(
        "res.partner",
        "write",
        ids=[7, 8],
        context={"lang": "id_ID"},
        vals={"name": "PT Uji", "date": date(2026, 11, 6)},
    )

    assert body(rec.requests[0]) == {
        "ids": [7, 8],
        "context": {"lang": "id_ID"},
        "vals": {"name": "PT Uji", "date": "2026-11-06"},
    }


def test_helpers_build_expected_bodies() -> None:
    rec = Recorder(lambda request: [])
    client, _ = make_client(rec)

    client.search_read("purchase.order", [["name", "=", "P00001"]], ["name"], order="id")
    client.read("purchase.order", [1, 2], ["state"])
    client.search_count("account.move", [["state", "=", "posted"]])
    client.write("res.partner", [5], {"email": "a@b.test"})
    client.fields_get("res.partner", ["type", "string"])

    bodies = [(r.url.path, body(r)) for r in rec.requests]
    assert bodies == [
        (
            "/json/2/purchase.order/search_read",
            {"domain": [["name", "=", "P00001"]], "fields": ["name"], "order": "id"},
        ),
        ("/json/2/purchase.order/read", {"ids": [1, 2], "fields": ["state"]}),
        ("/json/2/account.move/search_count", {"domain": [["state", "=", "posted"]]}),
        ("/json/2/res.partner/write", {"ids": [5], "vals": {"email": "a@b.test"}}),
        ("/json/2/res.partner/fields_get", {"attributes": ["type", "string"]}),
    ]


def test_create_sends_vals_list_and_returns_ids() -> None:
    rec = Recorder(httpx.Response(200, json=[41, 42]))
    client, _ = make_client(rec)

    ids = client.create("res.bank", [{"name": "Bank A"}, {"name": "Bank B"}])

    assert ids == [41, 42]
    assert body(rec.requests[0]) == {"vals_list": [{"name": "Bank A"}, {"name": "Bank B"}]}


def test_datetimes_are_sent_as_naive_utc() -> None:
    rec = Recorder(httpx.Response(200, json=True))
    client, _ = make_client(rec)
    wib = timezone(timedelta(hours=7))

    client.write(
        "purchase.order",
        [1],
        {
            "date_order": datetime(2026, 8, 4, 10, 0, tzinfo=wib),
            "date_approve": datetime(2026, 8, 4, 3, 0, tzinfo=UTC),
        },
    )

    assert body(rec.requests[0])["vals"] == {
        "date_order": "2026-08-04 03:00:00",
        "date_approve": "2026-08-04 03:00:00",
    }


# -- error mapping ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "name", "expected"),
    [
        (401, "werkzeug.exceptions.Unauthorized", OdooAuthError),
        (403, "odoo.exceptions.AccessError", OdooAccessError),
        (403, "odoo.exceptions.AccessDenied", OdooAccessError),
        (404, "werkzeug.exceptions.NotFound", OdooNotFoundError),
        (404, "odoo.exceptions.MissingError", OdooNotFoundError),
        (422, "odoo.exceptions.ValidationError", OdooValidationError),
        (422, "odoo.exceptions.UserError", OdooValidationError),
        (422, "werkzeug.exceptions.UnprocessableEntity", OdooValidationError),
        (500, "odoo.exceptions.RedirectWarning", OdooValidationError),
        (500, "builtins.ValueError", OdooServerError),
        (409, "odoo.exceptions.LockError", OdooError),
    ],
)
def test_error_mapping(status: int, name: str, expected: type[OdooError]) -> None:
    rec = Recorder(odoo_error(status, name, "pesan dari odoo"))
    client, _ = make_client(rec)

    with pytest.raises(expected) as info:
        client.write("res.partner", [1], {"name": "x"})

    err = info.value
    assert type(err) is expected
    assert err.status == status
    assert err.name == name
    assert err.message == "pesan dari odoo"
    assert err.model == "res.partner"
    assert err.method == "write"
    assert "pesan dari odoo" in str(err)


def test_non_json_error_body() -> None:
    rec = Recorder(httpx.Response(502, text="<html>Bad Gateway</html>"))
    client, _ = make_client(rec, max_attempts=1)

    with pytest.raises(OdooServerError) as info:
        client.search_read("res.partner")
    assert info.value.status == 502


# -- retry policy -----------------------------------------------------------------------------


def test_read_method_is_retried_on_5xx_with_backoff() -> None:
    rec = Recorder(
        httpx.Response(503, text="unavailable"),
        httpx.Response(502, text="bad gateway"),
        httpx.Response(200, json=[{"id": 1}]),
    )
    client, sleeps = make_client(rec, max_attempts=3, backoff=0.5)

    assert client.search_read("res.partner") == [{"id": 1}]
    assert len(rec.requests) == 3
    assert sleeps == [0.5, 1.0]


def test_read_method_is_retried_on_connection_error() -> None:
    rec = Recorder(httpx.ConnectError("refused"), httpx.Response(200, json=5))
    client, sleeps = make_client(rec)

    assert client.search_count("res.partner") == 5
    assert len(rec.requests) == 2
    assert len(sleeps) == 1


def test_read_method_gives_up_after_max_attempts() -> None:
    rec = Recorder(httpx.ConnectError("refused"))
    client, _ = make_client(rec, max_attempts=3)

    with pytest.raises(OdooConnectionError):
        client.read("res.partner", [1])
    assert len(rec.requests) == 3


def test_read_method_is_not_retried_on_4xx() -> None:
    rec = Recorder(odoo_error(401, "werkzeug.exceptions.Unauthorized", "Invalid apikey"))
    client, sleeps = make_client(rec)

    with pytest.raises(OdooAuthError):
        client.search_read("res.partner")
    assert len(rec.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "failure", [httpx.Response(503, text="unavailable"), httpx.ConnectError("refused")]
)
def test_create_is_never_retried(failure: httpx.Response | Exception) -> None:
    rec = Recorder(failure, httpx.Response(200, json=[1]))
    client, sleeps = make_client(rec, max_attempts=5)

    with pytest.raises((OdooServerError, OdooConnectionError)):
        client.create("res.partner", {"name": "PT Uji"})
    assert len(rec.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("model", "method", "kwargs"),
    [
        ("res.partner", "write", {"ids": [1], "vals": {"name": "x"}}),
        ("purchase.order", "button_confirm", {"ids": [1]}),
        ("account.move", "action_post", {"ids": [1]}),
    ],
)
def test_write_and_action_methods_are_never_retried(model: str, method: str, kwargs) -> None:
    rec = Recorder(httpx.Response(500, json={"name": "builtins.Exception", "message": "x"}))
    client, _ = make_client(rec, max_attempts=5)

    with pytest.raises(OdooServerError):
        client.call(model, method, **kwargs)
    assert len(rec.requests) == 1


# -- secrecy ----------------------------------------------------------------------------------


def test_api_key_not_in_repr() -> None:
    client, _ = make_client(Recorder(httpx.Response(200, json=True)))
    assert KEY not in repr(client)
    assert "***" in repr(client)
    assert KEY not in repr(client._http.auth)


def test_api_key_not_in_error_even_if_server_echoes_it() -> None:
    rec = Recorder(
        odoo_error(422, "odoo.exceptions.UserError", f"kunci {KEY} tidak dikenal"),
    )
    client, _ = make_client(rec)

    with pytest.raises(OdooValidationError) as info:
        client.write("res.partner", [1], {"name": "x"})

    err = info.value
    for text in (str(err), repr(err), err.message, err.debug or ""):
        assert KEY not in text
    assert "***" in err.message


def test_api_key_not_in_connection_error() -> None:
    rec = Recorder(httpx.ConnectError("refused"))
    client, _ = make_client(rec, max_attempts=1)

    with pytest.raises(OdooConnectionError) as info:
        client.search_read("res.partner")
    assert KEY not in str(info.value)
    assert KEY not in repr(info.value)


def test_empty_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        OdooClient(BASE_URL, "fakturberes", "")


def test_decimals_are_sent_without_float() -> None:
    from decimal import Decimal

    recorder = Recorder(httpx.Response(200, json=[7]))
    client, _ = make_client(recorder)
    client.create(
        "account.move.line", {"quantity": Decimal("840.000"), "price_unit": Decimal("12.5")}
    )
    body = json.loads(recorder.requests[0].content)
    assert body["vals_list"] == [{"quantity": 840, "price_unit": "12.5"}]


def test_parse_float_reads_exact_decimals() -> None:
    from decimal import Decimal

    raw = b'[{"id": 1, "price_unit": 185900.0, "qty": 0.1}]'
    client, _ = make_client(Recorder(httpx.Response(200, content=raw)), parse_float=Decimal)
    rows = client.search_read("purchase.order.line", [], ["price_unit", "qty"])
    assert rows == [{"id": 1, "price_unit": Decimal("185900.0"), "qty": Decimal("0.1")}]
    assert isinstance(rows[0]["qty"], Decimal)
    plain, _ = make_client(Recorder(httpx.Response(200, content=raw)))
    assert isinstance(plain.search_read("x", [], ["qty"])[0]["qty"], float)
