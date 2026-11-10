"""The state of one invoice's graph: JSON values only.

Domain objects are dumped with ``model_dump(mode="json")``, so ``Decimal`` and dates travel as text
and come back unchanged through their Pydantic models. No application class ever reaches the
checkpointer, which keeps LangGraph's deserialization allow-list untouched (ADR-0009).
"""

from typing import Any, TypedDict


class Decision(TypedDict):
    decision: str  # approve | reject | clarify
    decided_by: str
    note: str | None


class ReplyFile(TypedDict):
    revision: int
    path: str
    sha256: str
    mime: str
    size: int
    name: str


class VendorReply(TypedDict):
    message_id: str
    sender: str
    subject: str
    text: str
    attachment: ReplyFile | None


class GraphState(TypedDict, total=False):
    invoice_id: str
    intake: dict[str, Any]
    reused_from: str | None
    fragments: list[dict[str, Any]]
    extraction: dict[str, Any]
    invoice: dict[str, Any] | None
    arithmetic: dict[str, Any]
    identification: dict[str, Any]
    planner: dict[str, Any] | None
    review: dict[str, Any] | None
    matching: dict[str, Any]
    analysis: dict[str, Any]
    route: str
    route_choice: dict[str, Any]
    previous_reasons: list[str]
    retry_review: bool
    extra_reasons: list[str]
    verification: list[str]  # what differed when the bill action could not be verified
    bill_attempted: bool
    draft_left: int | None  # the agent's draft that failed verification
    approval_request: dict[str, Any]
    decision: Decision | None
    clarification_round: int
    reply: VendorReply
    handled_replies: list[str]
    reply_outcome: str  # po | instruksi | tunggu
    po_from_reply: str | None
    revision: int
    final_status: str
