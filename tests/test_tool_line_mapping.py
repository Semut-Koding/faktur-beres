"""BR-07 mapping by the LLM: which lines are sent, and the guards on the answer (fake LLM)."""

from decimal import Decimal

import pytest

from faktur_beres.dataset import load_dataset
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.rules.lines import LineMatch, LinesResult, match_lines
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.labels import load_labels, load_robustness_labels, truth_to_invoice
from faktur_beres.llm.contracts import LlmUnavailable, TextPart
from faktur_beres.tools.line_mapping import MappingAnswer, eligible, map_lines
from llm_fakes import ScriptedLlm

POLICY = load_policy()
LABELS = load_labels()


def kth06():
    label = load_robustness_labels()["KTH-06"]
    invoice = truth_to_invoice(label.kebenaran_dokumen)
    po = DatasetErpReader(load_dataset()).purchase_order(label.po_rujukan)
    assert po is not None
    return invoice, po, match_lines(invoice, po, POLICY)


def answer(*pairs: tuple[int, str | None]) -> MappingAnswer:
    return MappingAnswer.model_validate({"mappings": [{"line": n, "ref": r} for n, r in pairs]})


def all_refs(invoice) -> list[tuple[int, str | None]]:
    return [(n, f"R{n}") for n in range(1, len(invoice.lines) + 1)]


@pytest.mark.aturan("BR-07")
def test_lines_without_codes_are_mapped() -> None:
    invoice, po, lines = kth06()
    assert all(m.status == "no_match" for m in lines.matches)
    llm = ScriptedLlm([answer(*all_refs(invoice))])
    outcome = map_lines(invoice, po, lines, llm=llm)
    assert outcome.called and set(outcome.mapping) == {1, 2}
    mapped = match_lines(invoice, po, POLICY, llm_mapping=outcome.mapping)
    assert mapped.all_matched and {m.method for m in mapped.matches} == {"llm"}
    assert any("dipetakan LLM" in f.message for f in mapped.findings)


@pytest.mark.aturan("BR-07")
def test_no_price_or_quantity_is_sent() -> None:
    invoice, po, lines = kth06()
    llm = ScriptedLlm([answer(*all_refs(invoice))])
    map_lines(invoice, po, lines, llm=llm)
    [request] = llm.requests
    text = "\n".join(p.text for p in request.parts if isinstance(p, TextPart))
    for line in invoice.lines:
        assert str(line.unit_price) not in text and f"qty: {line.quantity}" not in text
    assert "harga" not in text.casefold()


def test_a_scenario_without_unmapped_lines_never_calls_the_llm() -> None:
    label = LABELS["QTY-04"]
    invoice = truth_to_invoice(label.kebenaran_dokumen)
    po = DatasetErpReader(load_dataset()).purchase_order(label.po_rujukan)
    assert po is not None
    lines = match_lines(invoice, po, POLICY)
    llm = ScriptedLlm([])
    if all(m.status != "no_match" for m in lines.matches):
        assert map_lines(invoice, po, lines, llm=llm).called is False
    else:
        wanted, offered = eligible(invoice, po, lines)
        assert (wanted, offered) == ([], [])
        assert map_lines(invoice, po, lines, llm=llm).called is False
    assert llm.requests == []


@pytest.mark.aturan("BR-07")
def test_ambiguous_lines_are_never_sent() -> None:
    invoice, po, lines = kth06()
    ambiguous = LinesResult(
        ran=True,
        purchase_order=po.number,
        matches=tuple(LineMatch(line=m.line, status="ambiguous") for m in lines.matches),
    )
    assert eligible(invoice, po, ambiguous) == ([], [])


def test_null_is_allowed() -> None:
    invoice, po, lines = kth06()
    llm = ScriptedLlm([answer((1, None), (2, None))])
    assert map_lines(invoice, po, lines, llm=llm).mapping == {}


@pytest.mark.aturan("BR-07")
@pytest.mark.parametrize(
    "bad",
    [
        answer((1, "R99")),  # never offered
        answer((1, "R1"), (2, "R1")),  # one PO line for two invoice lines
        answer((5, "R1")),  # a line that was not asked
        answer((1, "R1"), (1, "R2")),  # the same line twice
    ],
)
def test_a_refused_answer_is_asked_once_more_then_unavailable(bad: MappingAnswer) -> None:
    invoice, po, lines = kth06()
    llm = ScriptedLlm([bad, bad])
    with pytest.raises(LlmUnavailable):
        map_lines(invoice, po, lines, llm=llm)
    assert len(llm.requests) == 2


def test_a_second_good_answer_is_used() -> None:
    invoice, po, lines = kth06()
    llm = ScriptedLlm([answer((1, "R99")), answer(*all_refs(invoice))])
    assert len(map_lines(invoice, po, lines, llm=llm).mapping) == 2


@pytest.mark.aturan("BR-07")
def test_domain_refuses_a_mapping_to_a_line_outside_the_po() -> None:
    invoice, po, _ = kth06()
    mapped = match_lines(invoice, po, POLICY, llm_mapping={1: 999_999})
    assert mapped.matches[0].status == "no_match"


def test_price_is_still_checked_after_mapping() -> None:
    invoice, po, _ = kth06()
    first = invoice.lines[0].model_copy(
        update={"unit_price": invoice.lines[0].unit_price * Decimal(2)}
    )
    changed = invoice.model_copy(update={"lines": (first, *invoice.lines[1:])})
    ids = {i: pl.id for i, pl in enumerate(po.lines, start=1)}
    mapped = match_lines(changed, po, POLICY, llm_mapping=ids)
    assert any(f.reason is not None and f.reason.value == "price_variance" for f in mapped.findings)
