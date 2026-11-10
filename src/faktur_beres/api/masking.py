"""BR-24 for everything the API returns.

``mask_evidence_pack`` masks by field name and then hides the same numbers wherever they recur, and
leaves amounts alone. Two kinds of field it does not know are added here: Odoo's ``vat`` when it
holds an NPWP (in an invoice the same name is the tax amount), and Odoo's account-number fields.
"""

import re
from collections.abc import Mapping
from typing import Any

from faktur_beres.domain.rules.safety import mask_evidence_pack, mask_params, mask_text

_ACCOUNT_KEYS = frozenset({"acc_number", "sanitized_acc_number", "account_number", "rekening"})
_NPWP_DIGITS = 15


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _collect(node: Any, npwps: list[str], accounts: list[str]) -> None:
    if isinstance(node, Mapping):
        for key, item in node.items():
            if isinstance(item, str):
                if key == "vat" and len(_digits(item)) >= _NPWP_DIGITS:
                    npwps.append(item)
                elif key in _ACCOUNT_KEYS:
                    accounts.append(item)
            else:
                _collect(item, npwps, accounts)
    elif isinstance(node, list | tuple):
        for item in node:
            _collect(item, npwps, accounts)


def mask_public(document: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``document`` with every NPWP and account number masked."""
    npwps: list[str] = []
    accounts: list[str] = []
    _collect(document, npwps, accounts)
    packed = mask_evidence_pack(
        {"document": document, "known": {"npwp": npwps, "printed_digits": accounts}}
    )
    result: dict[str, Any] = packed["document"]
    return result


def mask_trail_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    """One audit row: parameters by key (as the CLI shows them), the summary as free text."""
    masked = dict(entry)
    if masked.get("params") is not None:
        masked["params"] = mask_params(masked["params"])
    if isinstance(masked.get("summary"), str):
        masked["summary"] = mask_text(masked["summary"])
    return masked
