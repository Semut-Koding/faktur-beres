"""Line mapping by the LLM (BR-07, ADR-0010): free descriptions to PO lines, checked by code.

Only invoice lines without any counterpart (``no_match``) are sent, and only PO lines that no other
invoice line took and whose product is on exactly one PO line are offered. ``ambiguous`` lines are
never sent. No price and no quantity: the numbers are checked by BR-08 and BR-09 afterwards.

The output is structured, not function calling. Guards: a reference must be one of those offered,
a PO line goes to one invoice line, ``null`` is allowed. An answer the guards refuse is asked once
more, then it counts as ``LlmUnavailable`` (BR-27).
"""

import secrets
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from faktur_beres.domain.erp import PurchaseOrderLineRecord, PurchaseOrderRecord
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.rules.lines import LinesResult
from faktur_beres.llm.contracts import (
    LlmCallRecord,
    LlmRequest,
    LlmUnavailable,
    ModelRole,
    StructuredLlm,
    TextPart,
    ThinkingLevel,
)
from faktur_beres.llm.prompts import Prompt, load_prompt

PROMPT_PACKAGE = "faktur_beres.tools"
PROMPT_VERSION = "line-mapping-v1"
MAX_OUTPUT_TOKENS = 2048
MAX_ANSWERS = 2


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True)

    line: int = Field(description="Nomor baris faktur")
    ref: str | None = Field(max_length=16, description="Ref baris PO, atau null bila tidak ada")


class MappingAnswer(BaseModel):
    """The JSON the mapper returns."""

    model_config = ConfigDict(frozen=True)

    mappings: list[_Answer] = Field(description="Satu elemen per baris faktur")


@dataclass(frozen=True)
class LineMappingOutcome:
    mapping: dict[int, int]  # invoice line number -> PO line id
    called: bool
    calls: tuple[LlmCallRecord, ...] = ()


def eligible(
    invoice: InvoiceData, po: PurchaseOrderRecord, lines: LinesResult
) -> tuple[list[int], list[PurchaseOrderLineRecord]]:
    """The invoice lines to send and the PO lines that may be offered."""
    wanted = [m.line for m in lines.matches if m.status == "no_match"]
    taken = {m.po_line_id for m in lines.matches if m.po_line_id is not None}
    products = Counter(line.product_id for line in po.lines)
    offered = [line for line in po.lines if line.id not in taken and products[line.product_id] == 1]
    if not (wanted and offered):
        return [], []
    return wanted, offered


def _request(
    prompt: Prompt,
    invoice: InvoiceData,
    wanted: Sequence[int],
    offered: Sequence[PurchaseOrderLineRecord],
    nonce: str,
) -> LlmRequest:
    invoice_rows = [
        f"baris {number}: deskripsi: {invoice.lines[number - 1].description} | "
        f"kode: {invoice.lines[number - 1].vendor_code or '-'} | "
        f"satuan: {invoice.lines[number - 1].unit}"
        for number in wanted
    ]
    po_rows = [
        f"ref R{index}: produk: {line.product_name} | kode vendor: "
        f"{', '.join(line.vendor_codes) or '-'} | deskripsi: {line.description} | "
        f"satuan: {line.uom_name}"
        for index, line in enumerate(offered, start=1)
    ]
    text = "\n".join(
        [
            prompt["request"].replace("{nonce}", nonce),
            "",
            f"<<<FAKTUR {nonce}>>>",
            *invoice_rows,
            f"<<<AKHIR FAKTUR {nonce}>>>",
            "",
            f"<<<PO {nonce}>>>",
            *po_rows,
            f"<<<AKHIR PO {nonce}>>>",
        ]
    )
    return LlmRequest(
        purpose="line_mapping",
        role=ModelRole.PRIMARY,
        prompt_version=prompt.version,
        system_instruction=prompt["system"],
        parts=(TextPart(text=text),),
        thinking_level=ThinkingLevel.LOW,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def _validated(
    answer: MappingAnswer, wanted: Sequence[int], offered: Sequence[PurchaseOrderLineRecord]
) -> dict[int, int] | None:
    """The mapping, or None when a guard refuses the answer."""
    refs = {f"R{index}": line.id for index, line in enumerate(offered, start=1)}
    mapping: dict[int, int] = {}
    seen: set[int] = set()
    for item in answer.mappings:
        if item.line not in wanted or item.line in seen:
            return None
        seen.add(item.line)
        if item.ref is None:
            continue
        po_line = refs.get(item.ref)
        if po_line is None or po_line in mapping.values():
            return None
        mapping[item.line] = po_line
    return mapping


def map_lines(
    invoice: InvoiceData,
    po: PurchaseOrderRecord,
    lines: LinesResult,
    *,
    llm: StructuredLlm,
    prompt: Prompt | None = None,
    make_nonce: Callable[[], str] = lambda: secrets.token_hex(8),
) -> LineMappingOutcome:
    """The validated mapping. ``LlmUnavailable`` reaches the caller (BR-27)."""
    wanted, offered = eligible(invoice, po, lines)
    if not wanted:
        return LineMappingOutcome(mapping={}, called=False)
    prompt = prompt or load_prompt(PROMPT_PACKAGE, PROMPT_VERSION)
    calls: list[LlmCallRecord] = []
    for _ in range(MAX_ANSWERS):
        request = _request(prompt, invoice, wanted, offered, make_nonce())
        result = llm.generate(request, MappingAnswer)
        calls += result.calls
        mapping = _validated(result.value, wanted, offered)
        if mapping is not None:
            return LineMappingOutcome(mapping=mapping, called=True, calls=tuple(calls))
    raise LlmUnavailable("line_mapping: jawaban ditolak guard dua kali", calls)
