"""The route choice by the LLM (BR-17, BR-21, ADR-0012), only when the code allows two routes.

The model may search the policy and then submits a route. ``guard_route`` decides whether it stands:
inside the allowed set, and leaving the default needs a written reason and at least one article that
the search of this same conversation returned. A refused answer is recorded and the default route is
used. So is an unavailable model or a spent budget: this step is optional, BR-27 is not triggered,
and the small quota of the fallback role is left alone.

Sent to the model between nonce delimiters: vendor name, total, reasons with the code's details
(masked), clarification rounds so far, the previous round's reasons, allowed and default routes. No
NPWP, account number or e-mail.
"""

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from faktur_beres.db.policy_store import PolicyHit
from faktur_beres.domain.reasons import Route
from faktur_beres.domain.rules.routing import RouteChoice, RouteRejected, guard_route
from faktur_beres.domain.rules.safety import mask_text
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
from faktur_beres.tools.policy_rag import MAX_K, SearchArgs, SearchOutcome

PROMPT_PACKAGE = "faktur_beres.tools"
PROMPT_VERSION = "decide-route-v1"
BUDGET = ToolBudget(model_calls=4, tool_runs=3)
MAX_JUSTIFICATION = 400
MAX_QUOTES = 3
MAX_OUTPUT_TOKENS = 2048
Search = Callable[[str, int], SearchOutcome]


class SubmitRouteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["A", "B", "C"] = Field(description="Jalur yang dipilih")
    justification: str = Field(max_length=MAX_JUSTIFICATION, description="Alasan singkat")
    quote_refs: list[str] = Field(max_length=MAX_QUOTES, description="Ref ketentuan yang dikutip")


TOOLS = (
    ToolSpec("search_policy", "Cari ketentuan kebijakan yang relevan.", SearchArgs),
    ToolSpec("submit_route", "Akhiri dengan jalur yang dipilih.", SubmitRouteArgs),
)


@dataclass(frozen=True)
class RouteContext:
    vendor_name: str | None
    total: Decimal | None
    details: Sequence[str]  # one line per reason, from the code
    rounds: int
    previous_reasons: Sequence[str]
    allowed: Sequence[Route]
    default: Route


@dataclass(frozen=True)
class RouteOutcome:
    status: str  # chosen | refused | budget_exhausted | unavailable
    route: Route
    justification: str | None = None
    quotes: tuple[PolicyHit, ...] = ()
    refusal: str | None = None
    model: str | None = None
    model_calls: int = 0
    tool_runs: int = 0
    calls: tuple[LlmCallRecord, ...] = ()


class RouteSession:
    """The tools of one conversation; remembers which articles the search returned."""

    def __init__(self, search: Search, allowed: Sequence[Route]) -> None:
        self._search = search
        self._allowed = tuple(allowed)
        self.found: dict[str, PolicyHit] = {}
        self.calls: list[LlmCallRecord] = []
        self.choice: RouteChoice | None = None
        self.refusal: str | None = None
        self.quotes: tuple[PolicyHit, ...] = ()

    def call(self, name: str, args: BaseModel) -> ToolReply:
        if isinstance(args, SearchArgs):
            return self._run_search(args)
        assert isinstance(args, SubmitRouteArgs), name
        return self._submit(args)

    def _run_search(self, args: SearchArgs) -> ToolReply:
        outcome = self._search(args.query, min(args.k, MAX_K))
        self.calls += outcome.calls
        for hit in outcome.hits:
            self.found[hit.ref] = hit
        return ToolReply(
            {
                "hasil": [
                    {"ref": h.ref, "judul": h.title, "kutipan": h.quote, "skor": str(h.score)}
                    for h in outcome.hits
                ]
            }
        )

    def _submit(self, args: SubmitRouteArgs) -> ToolReply:
        unknown = [ref for ref in args.quote_refs if ref not in self.found]
        if unknown:
            self.refusal = "kutipan_tidak_pernah_dicari"
            return ToolReply({"ok": False}, terminal=True)
        choice = RouteChoice(
            route=Route(args.route),
            justification=args.justification,
            policy_quotes=tuple(args.quote_refs),
        )
        try:
            guard_route(self._allowed, choice)
        except RouteRejected as exc:
            self.refusal = exc.why
            return ToolReply({"ok": False}, terminal=True)
        self.choice = choice
        self.quotes = tuple(self.found[ref] for ref in args.quote_refs)
        return ToolReply({"ok": True}, terminal=True)


def _block(context: RouteContext, nonce: str) -> str:
    return "\n".join(
        [
            f"<<<DATA {nonce}>>>",
            f"vendor: {context.vendor_name or 'tidak diketahui'}",
            f"total faktur: {context.total if context.total is not None else 'tidak terbaca'}",
            "alasan dari kode:",
            *[f"- {mask_text(line)}" for line in context.details],
            f"putaran klarifikasi yang sudah dikirim: {context.rounds}",
            f"alasan pada putaran sebelumnya: {', '.join(context.previous_reasons) or 'tidak ada'}",
            f"jalur yang boleh dipilih: {', '.join(r.value for r in context.allowed)}",
            f"jalur bawaan: {context.default.value}",
            f"<<<AKHIR {nonce}>>>",
        ]
    )


def choose_route(
    context: RouteContext,
    *,
    llm: ToolCallingLlm,
    search: Search,
    prompt: Prompt | None = None,
    make_nonce: Callable[[], str] = lambda: secrets.token_hex(8),
) -> RouteOutcome:
    """Never raises for an unavailable model: the default route stands."""
    prompt = prompt or load_prompt(PROMPT_PACKAGE, PROMPT_VERSION)
    nonce = make_nonce()
    request = LlmRequest(
        purpose="route_choice",
        role=ModelRole.PRIMARY,
        prompt_version=prompt.version,
        system_instruction=prompt["system"],
        parts=(
            TextPart(
                text=f"{prompt['request'].replace('{nonce}', nonce)}\n\n{_block(context, nonce)}"
            ),
        ),
        thinking_level=ThinkingLevel.LOW,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        allow_fallback=False,
    )
    sessions: list[RouteSession] = []

    def new_session() -> RouteSession:
        sessions.append(RouteSession(search, context.allowed))
        return sessions[-1]

    try:
        result = llm.run_tools(request, TOOLS, new_session, BUDGET)
    except LlmError as exc:
        embedded = [call for session in sessions for call in session.calls]
        return RouteOutcome(
            status="unavailable", route=context.default, calls=(*exc.calls, *embedded)
        )
    session = result.session
    refusals = [e.status.value for e in result.events if e.status is not ToolEventStatus.RUN]
    calls = (*result.calls, *(call for s in sessions for call in s.calls))
    common = {
        "model": result.model,
        "model_calls": result.model_calls,
        "tool_runs": result.tool_runs,
        "calls": calls,
    }
    if result.status is not ToolRunStatus.FINISHED:
        return RouteOutcome(
            status="budget_exhausted",
            route=context.default,
            refusal=", ".join(refusals) or None,
            **common,  # type: ignore[arg-type]
        )
    if session.choice is None:
        return RouteOutcome(
            status="refused",
            route=context.default,
            refusal=session.refusal,
            **common,  # type: ignore[arg-type]
        )
    return RouteOutcome(
        status="chosen",
        route=session.choice.route,
        justification=session.choice.justification,
        quotes=session.quotes,
        **common,  # type: ignore[arg-type]
    )
