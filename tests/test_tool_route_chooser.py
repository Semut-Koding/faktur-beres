"""The route choice against a scripted Gemini (no network)."""

from decimal import Decimal
from typing import Any

import pytest
from google.genai import types

from faktur_beres.db.policy_store import PolicyHit
from faktur_beres.domain.reasons import Route
from faktur_beres.llm.gemini import GeminiClient
from faktur_beres.tools.policy_rag import SearchOutcome
from faktur_beres.tools.route_chooser import BUDGET, RouteContext, RouteOutcome, choose_route
from llm_fakes import FakeClock, Outcome, ScriptedGenerate, quota_per_day, reply

PRIMARY, STRONG, RESERVE = "a", "b", "c"
HIT = PolicyHit("SOP-8.2", "Eskalasi", "Faktur dieskalasi bila masalah tetap ada.", Decimal("0.8"))
CONTEXT = RouteContext(
    vendor_name="Vendor Uji",
    total=Decimal("1000"),
    details=["Baris 1: harga naik.", "NPWP 0990427957220000 salah."],
    rounds=1,
    previous_reasons=["price_variance"],
    allowed=(Route.B, Route.C),
    default=Route.B,
)


def call(name: str, **args: Any) -> Any:
    response = reply(None)
    part = types.Part(function_call=types.FunctionCall(name=name, args=args, id="1"))
    response.candidates[0].content = types.Content(role="model", parts=[part])  # type: ignore[index]
    return response


SEARCH = call("search_policy", query="eskalasi setelah balasan", k=3)


def run(script: dict[str, list[Outcome]]) -> tuple[RouteOutcome, ScriptedGenerate]:
    clock = FakeClock()
    generate = ScriptedGenerate(script, clock=clock)
    llm = GeminiClient(
        generate,
        primary=PRIMARY,
        strong=STRONG,
        fallback=RESERVE,
        rpm={PRIMARY: 600, STRONG: 600, RESERVE: 600},
        clock=clock,
    )
    outcome = choose_route(
        CONTEXT,
        llm=llm,
        search=lambda _q, _k: SearchOutcome((HIT,), ()),
        make_nonce=lambda: "n0nc3",
    )
    return outcome, generate


@pytest.mark.aturan("BR-17")
def test_staying_on_the_default_route_needs_no_quote() -> None:
    outcome, _ = run({PRIMARY: [call("submit_route", route="B", justification="x", quote_refs=[])]})
    assert (outcome.status, outcome.route) == ("chosen", Route.B)


@pytest.mark.aturan("BR-17")
def test_escalation_with_a_searched_quote_is_accepted() -> None:
    outcome, _ = run(
        {
            PRIMARY: [
                SEARCH,
                call(
                    "submit_route", route="C", justification="Tetap sama.", quote_refs=["SOP-8.2"]
                ),
            ]
        }
    )
    assert (outcome.status, outcome.route) == ("chosen", Route.C)
    assert [q.ref for q in outcome.quotes] == ["SOP-8.2"] and outcome.tool_runs == 2


@pytest.mark.aturan("BR-17")
def test_escalation_without_a_quote_is_refused_and_the_default_stands() -> None:
    outcome, _ = run({PRIMARY: [call("submit_route", route="C", justification="x", quote_refs=[])]})
    assert (outcome.status, outcome.route) == ("refused", Route.B)
    assert "kutipan" in (outcome.refusal or "")


@pytest.mark.aturan("BR-17")
def test_a_route_outside_the_allowed_set_is_refused() -> None:
    outcome, _ = run({PRIMARY: [call("submit_route", route="A", justification="x", quote_refs=[])]})
    assert (outcome.status, outcome.route) == ("refused", Route.B)
    assert "di luar himpunan" in (outcome.refusal or "")


@pytest.mark.aturan("BR-21")
def test_a_quote_that_was_never_searched_is_refused() -> None:
    outcome, _ = run(
        {PRIMARY: [call("submit_route", route="C", justification="x", quote_refs=["SOP-1.1"])]}
    )
    assert outcome.status == "refused" and outcome.refusal == "kutipan_tidak_pernah_dicari"


def test_the_budget_stops_the_conversation_on_the_default_route() -> None:
    outcome, generate = run({PRIMARY: [SEARCH] * 6})
    assert (outcome.status, outcome.route) == ("budget_exhausted", Route.B)
    assert generate.calls_to(PRIMARY) <= BUDGET.model_calls


def test_an_unavailable_model_keeps_the_default_and_spares_the_fallback() -> None:
    outcome, generate = run({PRIMARY: [quota_per_day()], STRONG: [quota_per_day()]})
    assert (outcome.status, outcome.route) == ("unavailable", Route.B)
    assert generate.calls_to(RESERVE) == 0


def test_the_input_is_delimited_and_masked() -> None:
    _, generate = run(
        {PRIMARY: [call("submit_route", route="B", justification="x", quote_refs=[])]}
    )
    text = str(generate.sent[0].contents)
    assert "<<<DATA n0nc3>>>" in text and "0990427957220000" not in text
    assert "putaran klarifikasi yang sudah dikirim: 1" in text
