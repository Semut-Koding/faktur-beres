"""BR-22 idempotency keys, BR-24 masking, BR-25 recipient check."""

from datetime import date
from decimal import Decimal

import pytest

from faktur_beres.domain.rules.safety import (
    canonical_json,
    check_recipient,
    content_version,
    idempotency_key,
    mask_account,
    mask_npwp,
    mask_params,
    mask_text,
)

# -- BR-22 ----------------------------------------------------------------------------------------


@pytest.mark.aturan("BR-22")
def test_canonical_json_is_stable() -> None:
    a = {"b": Decimal("24975000.00"), "a": [1, date(2026, 10, 25)], "c": {"y": 1, "x": 2}}
    b = {"c": {"x": 2, "y": 1}, "a": (1, date(2026, 10, 25)), "b": Decimal(24975000)}
    assert canonical_json(a) == canonical_json(b)
    assert canonical_json(a) == '{"a":[1,"2026-10-25"],"b":"24975000","c":{"x":2,"y":1}}'


@pytest.mark.aturan("BR-22")
def test_idempotency_key_depends_on_every_part() -> None:
    content = {"ref": "KSM/INV/2026/10/0458", "total": Decimal(24975000)}
    key = idempotency_key("FB-000001", "create_draft_bill", "vendor:10", content)
    assert len(key) == 64
    assert key == idempotency_key("FB-000001", "create_draft_bill", "vendor:10", dict(content))
    others = {
        idempotency_key("FB-000002", "create_draft_bill", "vendor:10", content),
        idempotency_key("FB-000001", "attach_file", "vendor:10", content),
        idempotency_key("FB-000001", "create_draft_bill", "vendor:11", content),
        idempotency_key("FB-000001", "create_draft_bill", "vendor:10", {**content, "total": 1}),
    }
    assert key not in others and len(others) == 4
    assert content_version(content) != content_version({**content, "ref": "lain"})


@pytest.mark.aturan("BR-22")
def test_bad_inputs_are_refused() -> None:
    with pytest.raises(ValueError):
        idempotency_key("FB-1", "Buat Draft", "x", {})
    with pytest.raises(TypeError):
        canonical_json({"total": 1.5})


# -- BR-24 ----------------------------------------------------------------------------------------


@pytest.mark.aturan("BR-24")
def test_masks() -> None:
    assert mask_npwp("0990427957220000") == "0990********0000"
    assert mask_npwp("09.904.279.5-722.000") == "0990*******2000"
    assert mask_account("5450717927") == "******7927"
    assert mask_account("651-008-2689") == "******2689"
    assert mask_account("123") == "***"


@pytest.mark.aturan("BR-24")
def test_mask_text_and_params() -> None:
    assert mask_text("NPWP 0990427957220000, rek. 5450717927, PO P00015") == (
        "NPWP 0990********0000, rek. ******7927, PO P00015"
    )
    params = {
        "domain": [["vat", "=", "0990427957220000"], ["name", "ilike", "Sagara"]],
        "vals": {"acc_number": "5450717927", "ref": "KSM/INV/2026/10/0458"},
        "fields": ["id", "vat"],
    }
    masked = mask_params(params)
    assert masked["domain"][0] == ["vat", "=", "0990********0000"]
    assert masked["domain"][1] == ["name", "ilike", "Sagara"]
    assert masked["vals"] == {"acc_number": "******7927", "ref": "KSM/INV/2026/10/0458"}
    assert masked["fields"] == ["id", "vat"]
    assert params["vals"]["acc_number"] == "5450717927"  # the input is not changed


# -- BR-25 ----------------------------------------------------------------------------------------

VENDORS = ["tagihan@kertas-sagara.test", None]


@pytest.mark.aturan("BR-25")
@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("tagihan@kertas-sagara.test", "vendor"),
        ("TAGIHAN@Kertas-Sagara.TEST", "vendor"),
        ("Bagian Penagihan <tagihan@kertas-sagara.test>", "vendor"),
        ("ap@arunika.test", "internal"),
        ("Finance <FINANCE@ARUNIKA.TEST>", "internal"),
    ],
)
def test_allowed_recipients(raw: str, reason: str) -> None:
    check = check_recipient(raw, VENDORS, "arunika.test")
    assert check.allowed and check.reason == reason


@pytest.mark.aturan("BR-25")
@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("audit@pemeriksa-eksternal.test", "not_allowed"),
        ("ap@arunika.test.contoh.com", "not_allowed"),
        ("ap@sub.arunika.test", "not_allowed"),
        ("ap@arunika-test.com", "not_allowed"),
        ("tagihan@kertas-sagara.test.evil.test", "not_allowed"),
        # A display name that is itself an address counts as two addresses.
        ("ap@arunika.test <audit@pemeriksa-eksternal.test>", "multiple"),
        ("ap@arunika.test, audit@pemeriksa-eksternal.test", "multiple"),
        ("ap@arunika.test;x@y.test", "multiple"),
        ("bukan alamat", "invalid"),
        ("ap@arunika.test.", "invalid"),
    ],
)
def test_refused_recipients(raw: str, reason: str) -> None:
    check = check_recipient(raw, VENDORS, "arunika.test")
    assert not check.allowed and check.reason == reason
