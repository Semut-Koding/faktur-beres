"""The graph of one invoice, compiled against a checkpointer."""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy

from faktur_beres.db.models import Decision
from faktur_beres.extraction.outcome import ExtractionStatus
from faktur_beres.graph.deps import Dependencies
from faktur_beres.graph.nodes import Nodes
from faktur_beres.graph.state import GraphState
from faktur_beres.tools.email_tool import MailUnavailable
from faktur_beres.tools.errors import Unavailable


def _after_extract(state: GraphState) -> str:
    failed = state["extraction"]["status"] == ExtractionStatus.VALIDATION_FAILED.value
    return "decide" if failed else "validate"


def _after_decide(state: GraphState) -> str:
    return {"A": "act_bill", "B": "act_vendor_email", "C": "prepare_approval"}[state["route"]]


def _after_bill(state: GraphState) -> str:
    return "prepare_approval" if state.get("verification") else "report"


def _after_decision(state: GraphState) -> str:
    chosen = Decision(state["decision"]["decision"])
    if chosen is Decision.APPROVE:
        return "act_bill"
    return "act_vendor_email" if chosen is Decision.CLARIFY else "report"


def build_graph(
    deps: Dependencies, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[GraphState, Any, GraphState, GraphState]:
    nodes = Nodes(deps)
    # Odoo and the mail server may be briefly away (BR-27); actions are idempotent, so a retry is
    # safe.
    retry = RetryPolicy(
        max_attempts=deps.retry_attempts,
        initial_interval=deps.retry_interval_seconds,
        jitter=False,
        retry_on=(Unavailable, MailUnavailable),
    )
    graph = StateGraph(GraphState)
    for name, fn in (
        ("intake", nodes.intake),
        ("extract", nodes.extract),
        ("validate", nodes.validate),
        ("identify", nodes.identify),
        ("match", nodes.match),
        ("decide", nodes.decide),
        ("act_bill", nodes.act_bill),
        ("act_vendor_email", nodes.act_vendor_email),
        ("prepare_approval", nodes.prepare_approval),
        ("apply_decision", nodes.apply_decision),
        ("report", nodes.report),
    ):
        graph.add_node(name, nodes.traced(name, fn), retry_policy=retry)
    graph.add_node("await_approval", nodes.await_approval)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "extract")
    graph.add_conditional_edges("extract", _after_extract, ["validate", "decide"])
    graph.add_edge("validate", "identify")
    graph.add_edge("identify", "match")
    graph.add_edge("match", "decide")
    graph.add_conditional_edges(
        "decide", _after_decide, ["act_bill", "act_vendor_email", "prepare_approval"]
    )
    graph.add_conditional_edges("act_bill", _after_bill, ["prepare_approval", "report"])
    graph.add_edge("act_vendor_email", "report")
    graph.add_edge("prepare_approval", "await_approval")
    graph.add_edge("await_approval", "apply_decision")
    graph.add_conditional_edges(
        "apply_decision", _after_decision, ["act_bill", "act_vendor_email", "report"]
    )
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer)
