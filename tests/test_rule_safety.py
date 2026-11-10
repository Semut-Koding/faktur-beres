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
    mask_evidence_pack,
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


NAUGHTY = (
    "1381646027*",
    "*0990427957220000",
    "0990.4279.5722.0000 (*)",
    "****",
    "*",
    "",
    "0990427957220000",
    "137-00-1234567-8",
)


@pytest.mark.aturan("BR-24")
@pytest.mark.parametrize("value", NAUGHTY)
def test_masking_never_shows_more_digits_than_the_rule(value: str) -> None:
    npwp, account = mask_npwp(value), mask_account(value)
    assert sum(c.isdigit() for c in npwp) <= 8
    assert sum(c.isdigit() for c in account) <= 4
    assert mask_npwp(npwp) == npwp and mask_account(account) == account


@pytest.mark.aturan("BR-24")
def test_a_star_counts_as_a_hidden_digit() -> None:
    assert mask_account("1381646027*") == "*******027*"
    assert mask_npwp("0990********0000") == "0990********0000"
    assert mask_params({"rekening": "1381646027*"}) == {"rekening": "*******027*"}
    assert "4279" not in mask_params({"npwp": "*0990427957220000"})["npwp"]


def test_masking_twice_keeps_the_visible_digits() -> None:
    once = mask_params({"npwp": "0990427957220000", "rekening": "5450717927"})
    assert mask_params(once) == {"npwp": "0990********0000", "rekening": "******7927"}


@pytest.mark.aturan("BR-24")
def test_evidence_pack_masks_by_field_and_where_the_number_recurs() -> None:
    pack = {
        "faktur": {
            "seller": {"npwp": "0990.4279.5722.0000"},
            "bank_account": {"number": "137-00-1234567-8"},
            "notes": ["Transfer ke 137-00-1234567-8 ya", "NPWP 0990427957220000"],
            "total": "26107200",
            "nsfp": "04002600007215293",
        },
        "alasan": [
            {
                "message": "Rekening berbeda.",
                "evidence": {
                    "kind": "bank_account",
                    "printed_number": "137-00-1234567-8",
                    "printed_digits": "1370012345678",
                    "registered_digits": ["5450717927"],
                },
            },
            {
                "evidence": {
                    "kind": "tax_id",
                    "field": "npwp_penjual",
                    "printed": "0990427957220000",
                    "digits": "0990427957220000",
                    "expected": None,
                }
            },
            {"evidence": {"kind": "tax_id", "field": "nsfp", "printed": "04002600007215293"}},
        ],
        "berkas": {"sha256": "0" * 64},
    }
    masked = mask_evidence_pack(pack)
    faktur = masked["faktur"]
    assert faktur["seller"]["npwp"] == "0990********0000"
    assert faktur["bank_account"]["number"] == "*********5678"
    assert faktur["total"] == "26107200" and faktur["nsfp"] == "04002600007215293"
    assert "1234567" not in " ".join(faktur["notes"]) and "4279" not in " ".join(faktur["notes"])
    bank = masked["alasan"][0]["evidence"]
    assert bank["registered_digits"] == ["******7927"]
    assert bank["printed_digits"] == "*********5678"
    assert masked["alasan"][1]["evidence"]["digits"] == "0990********0000"
    assert masked["alasan"][2]["evidence"]["printed"] == "04002600007215293"
    assert masked["berkas"]["sha256"] == "0" * 64
    assert mask_evidence_pack(masked) == masked
