"""Safety helpers computed by code.

* **BR-22 idempotency**: an action key is the SHA-256 of canonical JSON (sorted keys, no spaces,
  UTF-8, Decimals without trailing zeros) over ``invoice_id``, action, target, and the content
  version, itself the SHA-256 of the canonical content. The same content always gives the same key.
* **BR-24 masking**: NPWP shows the first 4 and last 4 digits, an account number the last 4 digits.
  ``mask_params`` masks a parameter structure (dicts, lists, Odoo domains) before it is logged.
* **BR-25 recipients**: e-mail may only go to a vendor billing address registered in Odoo or to the
  internal domain, compared without case and display name. Look-alike domains such as
  ``arunika.test.contoh.com``, subdomains, and several addresses at once are refused.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.normalize import email_domain, normalize_email

# -- BR-22 ----------------------------------------------------------------------------------------


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, set | frozenset):
        return sorted(_plain(v) for v in value)
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, float):
        raise TypeError("float tidak dipakai untuk kunci idempoten")
    return value


def canonical_json(value: Any) -> str:
    """Stable JSON text of ``value`` (models, mappings, sequences, Decimal, dates)."""
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_version(content: Any) -> str:
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def idempotency_key(invoice_id: str, action: str, target: str, content: Any) -> str:
    """The BR-22 key of one external action."""
    if not ACTION_PATTERN.match(action):
        raise ValueError(f"nama aksi tidak valid: {action!r}")
    payload = {
        "invoice_id": invoice_id,
        "action": action,
        "target": target,
        "version": content_version(content),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# -- BR-24 ----------------------------------------------------------------------------------------

_NPWP_KEYS = ("vat", "npwp")
_ACCOUNT_KEYS = ("acc_number", "account_number", "rekening", "sanitized_acc_number")
_NPWP_IN_TEXT = re.compile(r"(?<!\d)(?:\d[\s.\-]?){14}\d(?:[\s.\-]?\d)?(?!\d)")
_LONG_DIGITS = re.compile(r"(?<!\d)\d{8,}(?!\d)")


def mask_npwp(value: str) -> str:
    """``0990********0000``: the first 4 and the last 4 digits stay."""
    digits = re.sub(r"\D", "", value)
    if len(digits) <= 8:
        return "*" * len(digits)
    return digits[:4] + "*" * (len(digits) - 8) + digits[-4:]


def mask_account(value: str) -> str:
    """``******7927``: only the last 4 digits stay."""
    digits = re.sub(r"\D", "", value)
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


def mask_text(text: str) -> str:
    """Mask NPWP-like numbers (15–16 digits) and other long digit runs (8+) in free text."""
    text = _NPWP_IN_TEXT.sub(lambda m: mask_npwp(m.group(0)), text)
    return _LONG_DIGITS.sub(lambda m: mask_account(m.group(0)), text)


_HASH_KEYS = ("sha256", "checksum")


def _mask_by_key(key: str | None, value: Any) -> Any:
    if not isinstance(value, str):
        return mask_params(value, key)
    name = (key or "").lower()
    if name in _HASH_KEYS:  # a hash identifies content but is no personal data
        return value
    if any(k in name for k in _NPWP_KEYS):
        return mask_npwp(value)
    if any(k in name for k in _ACCOUNT_KEYS):
        return mask_account(value)
    return mask_text(value)


def mask_params(value: Any, key: str | None = None) -> Any:
    """A copy of a parameter structure with NPWP and account numbers masked (BR-24)."""
    if isinstance(value, BaseModel):
        return mask_params(value.model_dump(mode="json"), key)
    if isinstance(value, Mapping):
        return {k: _mask_by_key(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        # An Odoo domain leaf: [field, operator, value]
        if len(value) == 3 and isinstance(value[0], str) and isinstance(value[1], str):
            return [value[0], value[1], _mask_by_key(value[0], value[2])]
        return [mask_params(v, key) for v in value]
    if isinstance(value, str):
        return _mask_by_key(key, value)
    return value


# -- BR-25 ----------------------------------------------------------------------------------------

_ADDRESS = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+$")


class RecipientCheck(DomainModel):
    raw: str
    address: str | None
    allowed: bool
    reason: Literal["vendor", "internal", "invalid", "multiple", "not_allowed"]


def check_recipient(
    raw: str, vendor_emails: Iterable[str | None], internal_domain: str
) -> RecipientCheck:
    """Whether one recipient may receive e-mail from Faktur Beres (BR-25)."""

    def result(
        address: str | None,
        reason: Literal["vendor", "internal", "invalid", "multiple", "not_allowed"],
    ) -> RecipientCheck:
        return RecipientCheck(
            raw=raw, address=address, allowed=reason in ("vendor", "internal"), reason=reason
        )

    if any(sep in raw for sep in (",", ";")) or raw.count("@") > 1:
        return result(None, "multiple")
    address = normalize_email(raw)
    if address is None or not _ADDRESS.match(address):
        return result(None, "invalid")
    vendors = {e for e in (normalize_email(v) for v in vendor_emails) if e}
    if address in vendors:
        return result(address, "vendor")
    if email_domain(address) == internal_domain.lower():
        return result(address, "internal")
    return result(address, "not_allowed")
