"""The ``identify`` planner (SPEC §5.1–§5.2, ADR-0005, ADR-0010): an LLM looks for the PO.

It runs when the vendor is identified uniquely and the printed PO number is absent or not found.
The model calls read-only tools bound to that vendor, so no ``partner_id`` is exposed; each answer
is capped at 20 POs × 20 lines. It ends with ``submit_identification``.

Advisory only: BR-06 still decides. The proposal is compared with the rule, the agreement goes in
the trace, and the explanation is an internal note that never reaches an e-mail. ``LlmUnavailable``
leaves a trace and the rule goes on; BR-27 is not triggered. The fallback role is refused: its
daily quota is too small for an advisory step.

Sent to the model: the invoice summary between nonce delimiters, as data (no NPWP, account or
e-mail) and tool results (no NPWP, account or e-mail either).
"""

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from faktur_beres.domain.erp import (
    ErpReader,
    PurchaseOrderLineRecord,
    PurchaseOrderRecord,
    VendorRecord,
)
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.normalize import normalize_po_number
from faktur_beres.domain.policy import PolicyConfig
from faktur_beres.domain.rules.lines import map_line, price_within_tolerance
from faktur_beres.llm.contracts import (
    LlmCallRecord,
    LlmError,
    LlmRequest,
    ModelRole,
    TextPart,
    ThinkingLevel,
    ToolBudget,
    ToolCallingLlm,
    ToolEventStatus,
    ToolReply,
    ToolRunStatus,
    ToolSpec,
)
from faktur_beres.llm.prompts import Prompt, load_prompt

PROMPT_PACKAGE = "faktur_beres.tools"
PROMPT_VERSION = "identify-planner-v1"
MAX_ORDERS = 20
MAX_LINES = 20
MAX_EXPLANATION = 400
BUDGET = ToolBudget(model_calls=6, tool_runs=12)
MAX_OUTPUT_TOKENS = 4096


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoArgs(_Args):
    pass


class PoNumberArgs(_Args):
    number: str = Field(min_length=1, max_length=64, description="Nomor PO")


class SubmitArgs(_Args):
    po_number: str | None = Field(
        max_length=64, description="Nomor PO yang diusulkan, atau null bila tidak ada"
    )
    explanation: str = Field(
        min_length=1, max_length=MAX_EXPLANATION, description="Alasan singkat usulan"
    )


TOOLS = (
    ToolSpec("get_purchase_order", "Baca satu PO menurut nomornya.", PoNumberArgs),
    ToolSpec("list_vendor_purchase_orders", "Daftar PO terkonfirmasi milik vendor ini.", NoArgs),
    ToolSpec(
        "compare_invoice_to_po",
        "Periksa setiap baris faktur terhadap satu PO milik vendor ini.",
        PoNumberArgs,
    ),
    ToolSpec("submit_identification", "Akhiri pencarian dengan usulan PO.", SubmitArgs),
)


def _line_summary(line: PurchaseOrderLineRecord) -> dict[str, Any]:
    return {
        "produk": line.product_name,
        "kode_vendor": list(line.vendor_codes),
        "satuan": line.uom_name,
        "dipesan": str(line.quantity),
        "diterima": str(line.qty_received),
        "ditagih": str(line.qty_invoiced),
        "sisa": str(line.open_quantity),
        "harga_satuan": str(line.price_unit),
    }


def _po_summary(po: PurchaseOrderRecord) -> dict[str, Any]:
    return {
        "nomor": po.number,
        "status": "terkonfirmasi" if po.confirmed else po.state.value,
        "baris": [_line_summary(line) for line in po.lines[:MAX_LINES]],
    }


class PlannerSession:
    """The tools of one conversation, bound to one vendor. Guards the proposal."""

    def __init__(
        self,
        invoice: InvoiceData,
        vendor: VendorRecord,
        reader: ErpReader,
        policy: PolicyConfig,
    ) -> None:
        self._invoice = invoice
        self._vendor = vendor
        self._reader = reader
        self._policy = policy
        self.seen: set[str] = set()
        self.submitted = False
        self.proposal: str | None = None
        self.explanation: str | None = None
        self.refusals: list[str] = []

    def call(self, name: str, args: BaseModel) -> ToolReply:
        if isinstance(args, SubmitArgs):
            return self._submit(args)
        if isinstance(args, PoNumberArgs):
            if name == "get_purchase_order":
                return self._get(args.number)
            return self._compare(args.number)
        return self._list()

    def _owned(self, number: str) -> PurchaseOrderRecord | ToolReply:
        po = self._reader.purchase_order(normalize_po_number(number))
        if po is None:
            return ToolReply({"ditemukan": False})
        if po.vendor_id != self._vendor.id:
            return ToolReply({"ditemukan": True, "kepemilikan": "milik vendor lain"})
        self.seen.add(normalize_po_number(po.number))
        return po

    def _get(self, number: str) -> ToolReply:
        found = self._owned(number)
        if isinstance(found, ToolReply):
            return found
        return ToolReply({"ditemukan": True, **_po_summary(found)})

    def _list(self) -> ToolReply:
        orders = sorted(
            self._reader.confirmed_purchase_orders(self._vendor.id), key=lambda po: po.number
        )
        for po in orders[:MAX_ORDERS]:
            self.seen.add(normalize_po_number(po.number))
        return ToolReply(
            {
                "jumlah": len(orders),
                "pesanan": [_po_summary(po) for po in orders[:MAX_ORDERS]],
            }
        )

    def _compare(self, number: str) -> ToolReply:
        found = self._owned(number)
        if isinstance(found, ToolReply):
            return found
        by_id = {line.id: line for line in found.lines}
        rows: list[dict[str, Any]] = []
        all_ok = found.confirmed
        used: set[int] = set()
        for index, line in enumerate(self._invoice.lines[:MAX_LINES], start=1):
            match = map_line(index, line, found)
            row: dict[str, Any] = {"baris": index, "padanan": match.status}
            ok = False
            if match.status == "matched" and match.po_line_id is not None:
                po_line = by_id[match.po_line_id]
                same_open = line.quantity == po_line.open_quantity
                in_price = price_within_tolerance(line.unit_price, po_line.price_unit, self._policy)
                unique = match.po_line_id not in used
                used.add(match.po_line_id)
                ok = same_open and in_price and unique
                row |= {"sisa_sama": same_open, "harga_dalam_toleransi": in_price}
            all_ok = all_ok and ok
            rows.append(row)
        return ToolReply({"nomor": found.number, "baris": rows, "semua_baris_sesuai": all_ok})

    def _submit(self, args: SubmitArgs) -> ToolReply:
        number = normalize_po_number(args.po_number) if args.po_number else None
        if number is not None and number not in self.seen:
            self.refusals.append("nomor_po_tidak_pernah_muncul")
            return ToolReply({"error": "nomor PO itu tidak pernah muncul di hasil alat"})
        self.submitted = True
        self.proposal = number
        self.explanation = args.explanation
        return ToolReply({"ok": True}, terminal=True)


@dataclass(frozen=True)
class PlannerOutcome:
    status: str  # proposed | budget_exhausted | unavailable
    proposal: str | None = None
    explanation: str | None = None
    model: str | None = None
    model_calls: int = 0
    tool_runs: int = 0
    guard_refusals: tuple[str, ...] = ()
    calls: tuple[LlmCallRecord, ...] = field(default=())


def _invoice_block(invoice: InvoiceData, nonce: str) -> str:
    rows = [
        f"- deskripsi: {line.description} | kode: {line.vendor_code or '-'} | "
        f"qty: {line.quantity} | satuan: {line.unit} | harga satuan: {line.unit_price}"
        for line in invoice.lines[:MAX_LINES]
    ]
    return "\n".join(
        [
            f"<<<DATA {nonce}>>>",
            f"penjual: {invoice.seller.name}",
            f"nomor faktur: {invoice.invoice_number}",
            f"tanggal faktur: {invoice.invoice_date.isoformat()}",
            f"nomor PO tercetak: {invoice.po_number or 'tidak ada'}",
            "baris:",
            *rows,
            f"<<<AKHIR {nonce}>>>",
        ]
    )


def _nonce_for(texts: Sequence[str], make: Callable[[], str]) -> str:
    for _ in range(100):
        nonce = make()
        if all(nonce not in text for text in texts):
            return nonce
    raise RuntimeError("tidak menemukan nonce yang tidak ada di teks")  # pragma: no cover


def run_planner(
    invoice: InvoiceData,
    vendor: VendorRecord,
    reader: ErpReader,
    *,
    llm: ToolCallingLlm,
    policy: PolicyConfig,
    prompt: Prompt | None = None,
    make_nonce: Callable[[], str] = lambda: secrets.token_hex(8),
) -> PlannerOutcome:
    """Run the conversation; never raises for an unavailable or invalid model."""
    prompt = prompt or load_prompt(PROMPT_PACKAGE, PROMPT_VERSION)
    text_fields = [invoice.seller.name, invoice.invoice_number, invoice.po_number or ""]
    text_fields += [line.description for line in invoice.lines]
    nonce = _nonce_for(text_fields, make_nonce)
    request = LlmRequest(
        purpose="identify_planner",
        role=ModelRole.PRIMARY,
        prompt_version=prompt.version,
        system_instruction=prompt["system"],
        parts=(
            TextPart(
                text=prompt["request"].replace("{nonce}", nonce)
                + "\n\n"
                + _invoice_block(invoice, nonce)
            ),
        ),
        thinking_level=ThinkingLevel.LOW,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        allow_fallback=False,
    )
    try:
        result = llm.run_tools(
            request,
            TOOLS,
            lambda: PlannerSession(invoice, vendor, reader, policy),
            BUDGET,
        )
    except LlmError as exc:
        return PlannerOutcome(status="unavailable", calls=tuple(exc.calls))
    session = result.session
    refusals = [
        event.status.value for event in result.events if event.status != ToolEventStatus.RUN
    ] + session.refusals
    status = "proposed" if result.status is ToolRunStatus.FINISHED else "budget_exhausted"
    return PlannerOutcome(
        status=status,
        proposal=session.proposal,
        explanation=session.explanation,
        model=result.model,
        model_calls=result.model_calls,
        tool_runs=result.tool_runs,
        guard_refusals=tuple(refusals),
        calls=result.calls,
    )
