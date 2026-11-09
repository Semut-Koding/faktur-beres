"""The ``identify`` planner against a scripted Gemini (no network)."""

from decimal import Decimal
from typing import Any

from google.genai import types

from faktur_beres.dataset import load_dataset
from faktur_beres.domain.engine import collect_vendor_candidates
from faktur_beres.domain.invoice import InvoiceData
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.rules.vendor import identify_vendor
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.labels import label_intake, load_labels, truth_to_invoice
from faktur_beres.llm.contracts import ModelRole
from faktur_beres.llm.gemini import GeminiClient
from faktur_beres.tools.po_planner import BUDGET, PlannerSession, run_planner
from llm_fakes import FakeClock, Outcome, ScriptedGenerate, quota_per_day, reply

PRIMARY, STRONG, RESERVE = "a", "b", "c"
LABELS = load_labels()
POLICY = load_policy()


def client(script: dict[str, list[Outcome]]) -> tuple[GeminiClient, ScriptedGenerate]:
    clock = FakeClock()
    generate = ScriptedGenerate(script, clock=clock)
    return (
        GeminiClient(
            generate,
            primary=PRIMARY,
            strong=STRONG,
            fallback=RESERVE,
            rpm={PRIMARY: 600, STRONG: 600, RESERVE: 600},
            clock=clock,
        ),
        generate,
    )


def call(name: str, **args: Any) -> Any:
    response = reply(None)
    part = types.Part(function_call=types.FunctionCall(name=name, args=args, id="1"))
    response.candidates[0].content = types.Content(role="model", parts=[part])  # type: ignore[index]
    return response


def setup(sid: str = "NPO-01") -> tuple[InvoiceData, Any, DatasetErpReader, str]:
    reader = DatasetErpReader(load_dataset())
    label = LABELS[sid]
    invoice = truth_to_invoice(label.kebenaran_dokumen)
    intake = label_intake(label)
    ident = identify_vendor(invoice, intake, collect_vendor_candidates(invoice, intake, reader))
    assert ident.vendor is not None
    return invoice, ident.vendor, reader, label.po_rujukan


def sent_text(generate: ScriptedGenerate) -> str:
    return str([s.contents for s in generate.sent]) + str(
        generate.sent[0].config.system_instruction
    )


def test_normal_conversation_proposes_the_po() -> None:
    invoice, vendor, reader, po = setup()
    llm, generate = client(
        {
            PRIMARY: [
                call("list_vendor_purchase_orders"),
                call("compare_invoice_to_po", number=po),
                call("submit_identification", po_number=po, explanation="Semua baris cocok."),
            ]
        }
    )
    outcome = run_planner(invoice, vendor, reader, llm=llm, policy=POLICY)
    assert (outcome.status, outcome.proposal, outcome.explanation) == (
        "proposed",
        po,
        "Semua baris cocok.",
    )
    assert (outcome.model_calls, outcome.tool_runs) == (3, 3)
    assert outcome.guard_refusals == ()
    assert generate.calls_to(PRIMARY) == 3


def test_erp_identifiers_never_reach_the_model() -> None:
    invoice, vendor, reader, po = setup()
    llm, generate = client(
        {
            PRIMARY: [
                call("get_purchase_order", number=po),
                call("submit_identification", po_number=None, explanation="x"),
            ]
        }
    )
    run_planner(invoice, vendor, reader, llm=llm, policy=POLICY)
    text = sent_text(generate)
    assert (vendor.vat or "#") not in text and (vendor.email or "#") not in text
    if invoice.seller.npwp:
        assert invoice.seller.npwp not in text
    if invoice.bank_account:
        assert invoice.bank_account.number not in text


def test_a_po_of_another_vendor_only_says_so() -> None:
    invoice, vendor, reader, _ = setup()
    others = [p for p in load_dataset().purchase_orders if p.vendor != LABELS["NPO-01"].vendor]
    session = PlannerSession(invoice, vendor, reader, POLICY)
    reply_ = session._get(others[0].number)
    assert reply_.result == {"ditemukan": True, "kepemilikan": "milik vendor lain"}
    assert session.seen == set()


def test_a_proposal_that_never_appeared_is_refused() -> None:
    invoice, vendor, reader, po = setup()
    llm, _ = client(
        {
            PRIMARY: [
                call("submit_identification", po_number="P99999", explanation="karangan"),
                call("get_purchase_order", number=po),
                call("submit_identification", po_number=po, explanation="ok"),
            ]
        }
    )
    outcome = run_planner(invoice, vendor, reader, llm=llm, policy=POLICY)
    assert outcome.proposal == po
    assert outcome.guard_refusals == ("nomor_po_tidak_pernah_muncul",)


def test_unknown_function_and_bad_arguments_are_refused() -> None:
    invoice, vendor, reader, _ = setup()
    llm, _ = client(
        {
            PRIMARY: [
                call("hapus_tagihan", number="1"),
                call("get_purchase_order", nomor="salah"),
                call("submit_identification", po_number=None, explanation="tidak ada"),
            ]
        }
    )
    outcome = run_planner(invoice, vendor, reader, llm=llm, policy=POLICY)
    assert outcome.status == "proposed" and outcome.tool_runs == 1
    assert outcome.guard_refusals == ("unknown_function", "invalid_args")


def test_budget_stops_the_conversation() -> None:
    invoice, vendor, reader, _ = setup()
    llm, generate = client({PRIMARY: [call("list_vendor_purchase_orders")] * 10})
    outcome = run_planner(invoice, vendor, reader, llm=llm, policy=POLICY)
    assert outcome.status == "budget_exhausted" and outcome.proposal is None
    assert generate.calls_to(PRIMARY) == BUDGET.model_calls


def test_unavailable_model_never_uses_the_fallback_role_and_does_not_raise() -> None:
    invoice, vendor, reader, _ = setup()
    llm, generate = client({PRIMARY: [quota_per_day()], STRONG: [quota_per_day()]})
    outcome = run_planner(invoice, vendor, reader, llm=llm, policy=POLICY)
    assert outcome.status == "unavailable"
    assert generate.calls_to(RESERVE) == 0


def test_request_role_and_nonce() -> None:
    invoice, vendor, reader, _ = setup()
    llm, generate = client(
        {PRIMARY: [call("submit_identification", po_number=None, explanation="x")]}
    )
    run_planner(invoice, vendor, reader, llm=llm, policy=POLICY, make_nonce=lambda: "n0nc3")
    text = sent_text(generate)
    assert "<<<DATA n0nc3>>>" in text and "<<<AKHIR n0nc3>>>" in text
    assert llm.chain(ModelRole.PRIMARY, allow_fallback=False) == (PRIMARY, STRONG)


def test_compare_needs_equal_open_quantity() -> None:
    invoice, vendor, reader, po = setup()
    session = PlannerSession(invoice, vendor, reader, POLICY)
    assert session._compare(po).result["semua_baris_sesuai"] is True
    line = invoice.lines[0].model_copy(update={"quantity": invoice.lines[0].quantity + Decimal(1)})
    changed = invoice.model_copy(update={"lines": (line, *invoice.lines[1:])})
    other = PlannerSession(changed, vendor, reader, POLICY)
    assert other._compare(po).result["semua_baris_sesuai"] is False
