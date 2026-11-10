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
from collections.abc import Callable, Iterable, Mapping
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


def _slots(value: str) -> str:
    """The digits of a value and the ``*`` that stand for digits hidden earlier, in order."""
    return re.sub(r"[^\d*]", "", value)


def mask_npwp(value: str) -> str:
    """``0990********0000``: the first and last 4 characters stay; masking twice changes nothing.

    A ``*`` counts as a digit that is already hidden, so a printed footnote mark cannot open a hole.
    """
    slots = _slots(value)
    if len(slots) <= 8:
        return "*" * len(slots)
    return slots[:4] + "*" * (len(slots) - 8) + slots[-4:]


def mask_account(value: str) -> str:
    """``******7927``: only the last 4 characters stay; masking twice changes nothing."""
    slots = _slots(value)
    if len(slots) <= 4:
        return "*" * len(slots)
    return "*" * (len(slots) - 4) + slots[-4:]


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


_PACK_NPWP = frozenset({"npwp", "npwp_digits", "vendor_npwp_digits"})
_PACK_ACCOUNT = frozenset({"printed_number", "printed_digits", "registered_digits"})
_PACK_TAX_ID = ("printed", "digits", "expected")
_PACK_FREE_TEXT = frozenset(
    {"message", "quote", "description", "notes", "catatan", "problems", "explanation"}
)
_MIN_SECRET_DIGITS = 8


def _is_npwp_evidence(node: Mapping[str, Any]) -> bool:
    return node.get("kind") == "tax_id" and str(node.get("field", "")).startswith("npwp")


def _pack_secrets(value: Any, key: str | None = None) -> tuple[set[str], set[str]]:
    """The digits of every NPWP and account number the pack holds in a named field."""
    npwps: set[str] = set()
    accounts: set[str] = set()

    def add(target: set[str], raw: Any) -> None:
        for item in raw if isinstance(raw, list | tuple) else [raw]:
            digits = re.sub(r"\D", "", item) if isinstance(item, str) else ""
            if len(digits) >= _MIN_SECRET_DIGITS:
                target.add(digits)

    def walk(node: Any, name: str | None) -> None:
        if isinstance(node, Mapping):
            if _is_npwp_evidence(node):
                for field in _PACK_TAX_ID:
                    add(npwps, node.get(field))
            for child, item in node.items():
                if child in _PACK_NPWP:
                    add(npwps, item)
                elif child in _PACK_ACCOUNT or (name == "bank_account" and child == "number"):
                    add(accounts, item)
                else:
                    walk(item, str(child))
        elif isinstance(node, list | tuple):
            for item in node:
                walk(item, name)

    walk(value, key)
    return npwps, accounts


def _digit_pattern(digits: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\d)" + r"[\s.\-/]*".join(digits) + r"(?!\d)")


def _hider(mask: Callable[[str], str]) -> Callable[[re.Match[str]], str]:
    def replace(match: re.Match[str]) -> str:
        return mask(match.group(0))

    return replace


def mask_evidence_pack(pack: Mapping[str, Any]) -> dict[str, Any]:
    """BR-24 for the approval evidence: by field name, then the same numbers wherever they recur.

    Masking only by pattern is not enough, because a printed account such as ``137-00-1234567-8``
    does not look like one. The conclusions of the rules stay whole; hashes are left alone.
    """
    npwps, accounts = _pack_secrets(pack)
    rules = [(_digit_pattern(d), mask_npwp) for d in sorted(npwps, key=len, reverse=True)]
    rules += [(_digit_pattern(d), mask_account) for d in sorted(accounts, key=len, reverse=True)]

    def scrub(text: str) -> str:
        for pattern, mask in rules:
            text = pattern.sub(_hider(mask), text)
        return text

    def walk(node: Any, name: str | None) -> Any:
        if isinstance(node, Mapping):
            tax_id = _is_npwp_evidence(node)
            result: dict[str, Any] = {}
            for child, item in node.items():
                field = str(child)
                if field in _HASH_KEYS:
                    result[field] = item
                elif field in _PACK_NPWP or (tax_id and field in _PACK_TAX_ID):
                    result[field] = _masked(item, mask_npwp)
                elif field in _PACK_ACCOUNT or (name == "bank_account" and field == "number"):
                    result[field] = _masked(item, mask_account)
                else:
                    result[field] = walk(item, field)
            return result
        if isinstance(node, list | tuple):
            return [walk(item, name) for item in node]
        if isinstance(node, str):
            cleaned = scrub(node)
            return mask_text(cleaned) if name in _PACK_FREE_TEXT else cleaned
        return node

    return walk(pack, None)  # type: ignore[no-any-return]


def _masked(value: Any, mask: Any) -> Any:
    if isinstance(value, list | tuple):
        return [mask(item) if isinstance(item, str) else item for item in value]
    return mask(value) if isinstance(value, str) else value


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
