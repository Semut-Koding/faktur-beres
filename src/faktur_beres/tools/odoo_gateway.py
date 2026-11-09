"""``AgentOdooGateway``: the only way agent code talks to Odoo (ADR-0004, SPEC §5.2).

It wraps the M0 JSON-2 client with the **agent** key and allows a short list of operations:

* ``search_read``, ``read``, ``search_count``, ``fields_get``, and ``has_access`` on the read models
  of the agent's rights matrix (RENCANA-TEKNIS §4.4), with an explicit, non-empty list of fields
  from a per-model allowlist (``needed_terms`` and other fields reserved to accounting groups are
  never read);
* ``create`` of one ``account.move`` of type ``in_invoice`` in state draft, with all its product
  lines as create commands;
* ``create`` of an ``ir.attachment`` and ``message_post`` of an internal note, both only on a draft
  vendor bill the agent itself created (checked through Odoo's own record rule: the agent may write
  only its own drafts, so ``has_access("write")`` answers the question).

Anything else raises ``ForbiddenOperation`` before a request is sent. The generic ``call()`` entry
(for tool calling in M4) only reads: ``search_read`` (``limit`` 50 by default, at most 200),
``read`` (at most 200 ids), and ``search_count``; ``order`` may only name allowed fields. Bills,
attachments, and notes are written only through ``odoo_tools`` (BR-18 plan, BR-22 key).

Every call carries the context ``{"lang": "id_ID", "tz": "Asia/Jakarta"}``; callers cannot override
it. Creating an attachment adds ``image_no_postprocess``, because Odoo otherwise shrinks images
above 1920 px and re-encodes them (``ir.attachment._postprocess_contents``), and the original
invoice file must be kept as received. Odoo errors become typed tool errors; only reads are retried
(by the client). Each call is logged as one structured record (model, method, masked parameters per
BR-24, duration, status) on the ``faktur_beres.tools.odoo`` logger; file contents are logged as
size and SHA-256 only, and so are texts longer than 1.000 characters. Storing the record in
``audit_log`` is M4.

Odoo still enforces the rights on its side (addon ``faktur_beres_agent``); the gateway is the second
fence, not the only one.
"""

import base64
import binascii
import hashlib
import logging
import re
import time
from collections.abc import Mapping, Sequence
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import httpx

from faktur_beres.config import Settings
from faktur_beres.domain.rules.safety import mask_params
from faktur_beres.odoo import OdooClient, OdooError
from faktur_beres.tools.errors import ForbiddenOperation, ToolError, from_odoo

log = logging.getLogger("faktur_beres.tools.odoo")

AGENT_CONTEXT: Mapping[str, str] = MappingProxyType({"lang": "id_ID", "tz": "Asia/Jakarta"})
USER_AGENT = "faktur-beres-agent/0.1"

#: Fields the agent may read, per model (the read models of the rights matrix).
READ_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "res.company": frozenset({"id", "name", "vat", "currency_id"}),
        "res.partner": frozenset(
            {"id", "name", "vat", "email", "supplier_rank", "is_company", "bank_ids"}
        ),
        "res.partner.bank": frozenset(
            {
                "id",
                "partner_id",
                "acc_number",
                "sanitized_acc_number",
                "bank_id",
                "bank_name",
                "acc_holder_name",
            }
        ),
        "product.product": frozenset({"id", "name", "default_code", "product_tmpl_id", "uom_id"}),
        "product.template": frozenset({"id", "name", "default_code", "uom_id"}),
        "product.supplierinfo": frozenset(
            {"id", "partner_id", "product_id", "product_tmpl_id", "product_code", "product_name"}
        ),
        "uom.uom": frozenset({"id", "name"}),
        "purchase.order": frozenset(
            {
                "id",
                "name",
                "partner_id",
                "state",
                "locked",
                "currency_id",
                "fiscal_position_id",
                "payment_term_id",
                "order_line",
                "receipt_status",
            }
        ),
        "purchase.order.line": frozenset(
            {
                "id",
                "order_id",
                "product_id",
                "name",
                "product_qty",
                "qty_received",
                "qty_invoiced",
                "price_unit",
                "product_uom_id",
                "tax_ids",
                "display_type",
            }
        ),
        "stock.picking": frozenset({"id", "name", "origin", "state", "date_done", "backorder_id"}),
        "stock.move": frozenset({"id", "picking_id", "product_id", "quantity", "state"}),
        "stock.move.line": frozenset({"id", "move_id", "picking_id", "product_id", "quantity"}),
        "account.tax": frozenset({"id", "name", "amount", "amount_type", "type_tax_use"}),
        "account.journal": frozenset({"id", "name", "code", "type"}),
        "account.payment.term": frozenset({"id", "name"}),
        "res.currency": frozenset({"id", "name"}),
        "account.move": frozenset(
            {
                "id",
                "name",
                "move_type",
                "state",
                "partner_id",
                "ref",
                "invoice_date",
                "amount_untaxed",
                "amount_tax",
                "amount_total",
                "currency_id",
                "invoice_origin",
                "invoice_payment_term_id",
                "partner_bank_id",
                "fiscal_position_id",
                "create_uid",
                "invoice_line_ids",
            }
        ),
        "account.move.line": frozenset(
            {
                "id",
                "move_id",
                "display_type",
                "product_id",
                "purchase_line_id",
                "quantity",
                "price_unit",
                "price_subtotal",
                "tax_ids",
                "product_uom_id",
                "name",
            }
        ),
        "ir.attachment": frozenset({"id", "name", "res_model", "res_id", "checksum", "mimetype"}),
        "mail.message": frozenset({"id", "model", "res_id", "body", "subtype_id", "message_type"}),
    }
)
#: Never read, even if someone adds them to an allowlist (JSON-2 cannot serialise needed_terms).
NEVER_READ: frozenset[str] = frozenset({"needed_terms"})
READ_METHODS: frozenset[str] = frozenset(
    {"search_read", "read", "search_count", "fields_get", "has_access"}
)
BILL_FIELDS: frozenset[str] = frozenset(
    {
        "move_type",
        "partner_id",
        "currency_id",
        "fiscal_position_id",
        "invoice_origin",
        "invoice_payment_term_id",
        "ref",
        "invoice_date",
        "partner_bank_id",
        "invoice_line_ids",
    }
)
BILL_LINE_FIELDS: frozenset[str] = frozenset(
    {
        "display_type",
        "product_id",
        "product_uom_id",
        "tax_ids",
        "purchase_line_id",
        "name",
        "quantity",
        "price_unit",
    }
)
NOTE_SUBTYPE = "mail.mt_note"
#: Limits of the generic read-only ``call()`` entry.
CALL_LIMIT_DEFAULT = 50
CALL_LIMIT_MAX = 200
CALL_READ_MAX_IDS = 200
CALL_PARAMETERS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "search_read": frozenset({"domain", "fields", "limit", "order"}),
        "read": frozenset({"ids", "fields"}),
        "search_count": frozenset({"domain"}),
    }
)
#: Text values longer than this are logged as their length and SHA-256 only.
LOG_TEXT_LIMIT = 1000
_ORDER_TERM = re.compile(r"^\s*([a-z_][a-z0-9_]*)(?:\s+(asc|desc))?\s*$", re.IGNORECASE)


def _forbid(message: str, model: str, method: str) -> ForbiddenOperation:
    return ForbiddenOperation(message, model=model, method=method)


def _file_summary(datas: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(datas, validate=True)
    except (binascii.Error, ValueError):
        raw = datas.encode("utf-8")
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def summarize_params(value: Any, key: str | None = None) -> Any:
    """Parameters for the log: file contents and long texts become size and SHA-256 only."""
    if isinstance(value, Mapping):
        return {k: summarize_params(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [summarize_params(v, key) for v in value]
    if isinstance(value, str):
        if key == "datas":
            return _file_summary(value)
        if len(value) > LOG_TEXT_LIMIT:
            return {"chars": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()}
    return value


def log_params(params: Mapping[str, Any]) -> Any:
    """What the log keeps of the parameters: summarised (no file content), then masked (BR-24)."""
    return mask_params(summarize_params(params))


class AgentOdooGateway:
    """The agent's door to Odoo. Construct it with a client that holds the agent key."""

    def __init__(self, client: OdooClient) -> None:
        self._client = client

    @classmethod
    def connect(
        cls,
        url: str,
        db: str,
        agent_key: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> "AgentOdooGateway":
        client = OdooClient(
            url,
            db,
            agent_key,
            timeout=timeout,
            user_agent=USER_AGENT,
            transport=transport,
            parse_float=Decimal,
        )
        return cls(client)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "AgentOdooGateway":
        """The gateway with ``ODOO_AGENT_API_KEY`` from ``.env``; the admin key is never used."""
        settings = settings or Settings()
        if settings.odoo_agent_api_key is None:
            raise ToolError("ODOO_AGENT_API_KEY kosong; jalankan npm run odoo:init")
        return cls.connect(
            settings.odoo_url,
            settings.odoo_db,
            settings.odoo_agent_api_key.get_secret_value(),
            timeout=settings.odoo_timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AgentOdooGateway":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- checks before any request ----------------------------------------------------------------

    @staticmethod
    def _check_read(model: str, method: str, fields: Sequence[str] | None) -> None:
        if model not in READ_FIELDS:
            raise _forbid(f"model {model} tidak boleh dibaca agent", model, method)
        if method not in READ_METHODS:
            raise _forbid(f"metode {method} tidak diizinkan", model, method)
        if fields is None:
            return
        if not fields:
            raise _forbid("daftar field wajib diisi", model, method)
        bad = sorted({*fields} - READ_FIELDS[model] | ({*fields} & NEVER_READ))
        if bad:
            raise _forbid(f"field tidak diizinkan untuk {model}: {', '.join(bad)}", model, method)

    @staticmethod
    def _check_order(model: str, method: str, order: str) -> None:
        """``order`` may only be ``field [asc|desc]`` terms over the model's readable fields."""
        for term in order.split(","):
            match = _ORDER_TERM.match(term)
            if match is None or match.group(1) not in READ_FIELDS[model]:
                raise _forbid(f"urutan tidak diizinkan: {order!r}", model, method)

    @staticmethod
    def _check_bill_values(values: Mapping[str, Any]) -> None:
        model, method = "account.move", "create"
        unknown = sorted(set(values) - BILL_FIELDS)
        if unknown:
            raise _forbid(f"field tagihan tidak diizinkan: {', '.join(unknown)}", model, method)
        if values.get("move_type") != "in_invoice":
            raise _forbid("hanya tagihan vendor (in_invoice) yang boleh dibuat", model, method)
        if not isinstance(values.get("partner_id"), int):
            raise _forbid("partner_id wajib berupa id vendor", model, method)
        lines = values.get("invoice_line_ids")
        if not isinstance(lines, list) or not lines:
            raise _forbid("baris tagihan wajib ada", model, method)
        for command in lines:
            if (
                not isinstance(command, list | tuple)
                or len(command) != 3
                or command[0] != 0
                or not isinstance(command[2], Mapping)
            ):
                raise _forbid("baris hanya boleh perintah buat [0, 0, {...}]", model, method)
            extra = sorted(set(command[2]) - BILL_LINE_FIELDS)
            if extra:
                raise _forbid(f"field baris tidak diizinkan: {', '.join(extra)}", model, method)
            if command[2].get("display_type", "product") != "product":
                raise _forbid("baris tagihan harus baris produk", model, method)
            if not isinstance(command[2].get("purchase_line_id"), int):
                raise _forbid("setiap baris wajib tertaut ke baris PO", model, method)

    # -- the one place that sends requests ----------------------------------------------------

    def _send(self, model: str, method: str, *, keep_file: bool = False, **kwargs: Any) -> Any:
        started = time.monotonic()
        status, error = "ok", None
        context: dict[str, str | bool] = dict(AGENT_CONTEXT)
        if keep_file:
            context["image_no_postprocess"] = True
        try:
            return self._client.call(model, method, context=context, **kwargs)
        except OdooError as exc:
            status, error = "error", type(exc).__name__
            raise from_odoo(exc) from exc
        finally:
            entry = {
                "model": model,
                "method": method,
                "params": log_params(kwargs),
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
                "status": status,
                "error": error,
            }
            log.info("odoo %s/%s %s", model, method, status, extra={"odoo_call": entry})

    # -- reads -----------------------------------------------------------------------------------

    def search_read(
        self,
        model: str,
        domain: Sequence[Any],
        fields: Sequence[str],
        *,
        limit: int | None = None,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        self._check_read(model, "search_read", fields)
        kwargs: dict[str, Any] = {"domain": list(domain), "fields": list(fields)}
        if limit is not None:
            kwargs["limit"] = limit
        if order is not None:
            self._check_order(model, "search_read", order)
            kwargs["order"] = order
        result: list[dict[str, Any]] = self._send(model, "search_read", **kwargs)
        return result

    def read(self, model: str, ids: Sequence[int], fields: Sequence[str]) -> list[dict[str, Any]]:
        self._check_read(model, "read", fields)
        if not ids:
            return []
        result: list[dict[str, Any]] = self._send(model, "read", ids=list(ids), fields=list(fields))
        return result

    def search_count(self, model: str, domain: Sequence[Any]) -> int:
        self._check_read(model, "search_count", None)
        return int(self._send(model, "search_count", domain=list(domain)))

    def fields_get(self, model: str, attributes: Sequence[str]) -> dict[str, dict[str, Any]]:
        self._check_read(model, "fields_get", None)
        if not attributes:
            raise _forbid("atribut fields_get wajib diisi", model, "fields_get")
        result: dict[str, dict[str, Any]] = self._send(
            model, "fields_get", attributes=list(attributes)
        )
        return result

    def can_write(self, model: str, record_id: int) -> bool:
        """Whether Odoo's rules let the agent write this record (no change is made)."""
        self._check_read(model, "has_access", None)
        return bool(self._send(model, "has_access", ids=[record_id], operation="write"))

    # -- the three writes ------------------------------------------------------------------------

    def is_agent_draft(self, bill_id: int) -> bool:
        """A draft vendor bill the agent created (Odoo lets the agent write only those)."""
        rows = self.read("account.move", [bill_id], ["move_type", "state"])
        if not rows or rows[0]["move_type"] != "in_invoice" or rows[0]["state"] != "draft":
            return False
        return self.can_write("account.move", bill_id)

    def create_vendor_bill(self, values: Mapping[str, Any]) -> int:
        """Create one draft vendor bill with all its lines in a single ``create`` call."""
        self._check_bill_values(values)
        result = self._send("account.move", "create", vals_list=[dict(values)])
        return int(result[0] if isinstance(result, list) else result)

    def create_attachment(self, bill_id: int, name: str, content: bytes, mimetype: str) -> int:
        """Attach a file to a draft vendor bill of the agent."""
        if not name or not mimetype:
            raise _forbid("nama dan mimetype lampiran wajib diisi", "ir.attachment", "create")
        if not self.is_agent_draft(bill_id):
            raise _forbid(
                f"lampiran hanya pada draft tagihan agent (#{bill_id})", "ir.attachment", "create"
            )
        values = {
            "name": name,
            "res_model": "account.move",
            "res_id": bill_id,
            "datas": base64.b64encode(content).decode("ascii"),
            "mimetype": mimetype,
        }
        result = self._send("ir.attachment", "create", keep_file=True, vals_list=[values])
        return int(result[0] if isinstance(result, list) else result)

    def post_note(self, bill_id: int, body: str) -> int:
        """Post an internal note (``mail.mt_note``) on a draft vendor bill of the agent."""
        if not body.strip():
            raise _forbid("isi catatan kosong", "account.move", "message_post")
        if not self.is_agent_draft(bill_id):
            raise _forbid(
                f"catatan hanya pada draft tagihan agent (#{bill_id})",
                "account.move",
                "message_post",
            )
        result = self._send(
            "account.move",
            "message_post",
            ids=[bill_id],
            body=body,
            message_type="comment",
            subtype_xmlid=NOTE_SUBTYPE,
        )
        return int(result[0] if isinstance(result, list) else result)

    # -- the generic entry for tool calling (M4) --------------------------------------------------

    def call(self, model: str, method: str, **kwargs: Any) -> Any:
        """The read-only entry for tool calling: ``search_read``, ``read``, ``search_count``.

        Everything else, including ``create``, ``write``, ``unlink``, and action methods, is
        refused before a request is sent. ``search_read`` returns at most ``CALL_LIMIT_MAX`` rows
        (``CALL_LIMIT_DEFAULT`` when no limit is given) and ``read`` takes at most
        ``CALL_READ_MAX_IDS`` ids.
        """
        if method not in CALL_PARAMETERS:
            raise _forbid(
                f"operasi {model}/{method} tidak diizinkan: pintu call() hanya membaca",
                model,
                method,
            )
        extra = sorted(set(kwargs) - CALL_PARAMETERS[method])
        if extra:
            raise _forbid(f"parameter tidak diizinkan: {', '.join(extra)}", model, method)
        if method == "search_read":
            limit = kwargs.get("limit", CALL_LIMIT_DEFAULT)
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= CALL_LIMIT_MAX
            ):
                raise _forbid(
                    f"limit harus 1 sampai {CALL_LIMIT_MAX}, bukan {limit!r}", model, method
                )
            return self.search_read(
                model,
                kwargs.get("domain", []),
                kwargs.get("fields", ()),
                limit=limit,
                order=kwargs.get("order"),
            )
        if method == "read":
            ids = kwargs.get("ids", ())
            if not isinstance(ids, list | tuple) or not all(
                isinstance(i, int) and not isinstance(i, bool) for i in ids
            ):
                raise _forbid("ids harus daftar bilangan bulat", model, method)
            if len(ids) > CALL_READ_MAX_IDS:
                raise _forbid(
                    f"read paling banyak {CALL_READ_MAX_IDS} id, bukan {len(ids)}", model, method
                )
            return self.read(model, ids, kwargs.get("fields", ()))
        return self.search_count(model, kwargs.get("domain", []))
