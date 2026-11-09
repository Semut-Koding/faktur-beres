"""The extraction schema: text or null everywhere, every key required, limits in code and schema."""

import json

import pytest
from pydantic import ValidationError

from faktur_beres.extraction.schema import (
    MAX_LINES,
    MAX_NOTES,
    MAX_OTHER_TEXTS,
    MAX_TEXT,
    ExtractedInvoice,
)


def empty(**changes: object) -> dict[str, object]:
    fields = dict.fromkeys(ExtractedInvoice.model_fields)
    fields.update(lines=[], notes=[], other_text=[])
    fields.update(changes)
    return fields


LINE = {
    "vendor_code": None,
    "description": "Barang",
    "quantity": "1",
    "unit": "kg",
    "unit_price": "Rp 1.000",
    "amount": "1.000",
}


def test_every_key_is_required_and_values_are_text() -> None:
    schema = ExtractedInvoice.model_json_schema()
    assert set(schema["required"]) == set(ExtractedInvoice.model_fields)
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate({"invoice_number": "A-1"})
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate(empty(total=14918400))  # a number is not text
    assert ExtractedInvoice.model_validate(empty(total="Rp14.918.400")).total == "Rp14.918.400"


def test_limits_gemini_accepts_are_in_the_json_schema() -> None:
    schema = ExtractedInvoice.model_json_schema()
    props = schema["properties"]
    assert "maxItems" not in props["lines"]  # refused by the API on a list of objects
    assert props["notes"]["maxItems"] == MAX_NOTES
    assert props["other_text"]["maxItems"] == MAX_OTHER_TEXTS
    assert f'"maxLength": {MAX_TEXT}' in json.dumps(schema)


@pytest.mark.parametrize(
    "changes",
    [
        {"lines": [LINE] * (MAX_LINES + 1)},
        {"notes": ["catatan"] * (MAX_NOTES + 1)},
        {"other_text": ["teks"] * (MAX_OTHER_TEXTS + 1)},
        {"invoice_number": "x" * (MAX_TEXT + 1)},
        {"other_text": ["x" * (MAX_TEXT + 1)]},
    ],
    ids=["baris", "catatan", "teks-lain", "teks-panjang", "teks-lain-panjang"],
)
def test_limits_are_checked_by_code(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate(empty(**changes))


def test_limits_themselves_are_allowed() -> None:
    value = ExtractedInvoice.model_validate(
        empty(lines=[LINE] * MAX_LINES, notes=["n"] * MAX_NOTES, other_text=["x" * MAX_TEXT])
    )
    assert len(value.lines) == MAX_LINES
