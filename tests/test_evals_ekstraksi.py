"""``eval:ekstraksi`` without a network: documents, field rules, the chain, runs that resume."""

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from extraction_support import extracted
from faktur_beres.dataset.loader import load_dataset
from faktur_beres.domain.policy import load_policy
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.ekstraksi import (
    COMPARISON_SUBSET,
    RunState,
    compare_fields,
    compare_notes,
    documents,
    evaluate_document,
    run_documents,
    select,
    summarize,
)
from faktur_beres.evals.labels import load_labels, truth_to_invoice
from faktur_beres.llm.contracts import LlmUnavailable
from faktur_beres.tools.injection_review import ReviewAnswer
from llm_fakes import ScriptedLlm

DOCS = {d.id: d for d in documents()}
READER = DatasetErpReader(load_dataset())
POLICY = load_policy()
LABELS = load_labels()
NO = ReviewAnswer(injection=False, quotes=[])


def state(**changes: Any) -> RunState:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    data: dict[str, Any] = {
        "run": "uji",
        "started": now,
        "updated": now,
        "mode": "penuh",
        "models": {"utama": "model-utama", "kuat": "model-kuat"},
        "prompt_versions": {},
        "review": True,
        "cache": False,
        "groups": ("faktur",),
    }
    data.update(changes)
    return RunState.model_validate(data)


def test_the_three_groups() -> None:
    groups = Counter(d.group for d in DOCS.values())
    assert groups == {"faktur": 40, "balasan": 11, "ketahanan": 13}
    formats = Counter(d.format for d in DOCS.values())
    assert formats["UBL"] == 6 and formats["PDF pindaian"] == 1
    assert set(COMPARISON_SUBSET) <= set(DOCS)
    assert {DOCS[i].format for i in COMPARISON_SUBSET} == {
        "foto",
        "gambar pindaian",
        "PDF teks",
    }


def test_select_by_group_or_id() -> None:
    all_docs = list(DOCS.values())
    assert len(select(all_docs, ("balasan",), ())) == 11
    assert [d.id for d in select(all_docs, ("faktur",), ("KTH-01", "COC-01"))] == [
        "COC-01",
        "KTH-01",
    ]
    with pytest.raises(ValueError, match="tidak dikenal"):
        select(all_docs, ("faktur",), ("XYZ-99",))


def test_field_rules() -> None:
    truth = truth_to_invoice(LABELS["COC-02"].kebenaran_dokumen)
    assert all(c.ok for c in compare_fields(truth, truth))
    line = truth.lines[0].model_copy(update={"amount": truth.lines[0].amount + 1})
    seller = truth.seller.model_copy(
        update={"npwp": "0990.7695.1537.6000", "name": truth.seller.name.upper()}
    )
    read = truth.model_copy(
        update={"lines": (line,), "seller": seller, "invoice_number": truth.invoice_number.lower()}
    )
    wrong = {c.path for c in compare_fields(truth, read) if not c.ok}
    assert "baris[1].jumlah" in wrong and "penjual.nama" not in wrong
    assert {p for p in wrong if p.startswith("baris[2]")} == {
        f"baris[2].{f}" for f in ("kode", "deskripsi", "qty", "satuan", "harga_satuan", "jumlah")
    }
    assert all(not c.ok for c in compare_fields(truth, None))
    critical = {c.key for c in compare_fields(truth, truth) if c.critical}
    assert {"total", "baris.qty", "penjual.npwp", "nsfp"} <= critical
    assert "jatuh_tempo" not in critical


def test_buyer_email_is_judged_against_the_label() -> None:
    truth = truth_to_invoice(LABELS["COC-01"].kebenaran_dokumen)
    assert truth.buyer is not None and truth.buyer.email is None  # a PDF does not print it
    invented = truth.model_copy(
        update={"buyer": truth.buyer.model_copy(update={"email": "karangan@contoh.test"})}
    )
    assert {c.path: c.ok for c in compare_fields(truth, invented)}["pembeli.email"] is False
    assert {c.path: c.ok for c in compare_fields(truth, truth)}["pembeli.email"] is True


def test_notes_must_be_found_in_the_free_texts() -> None:
    checks = compare_notes(
        ["Bayar  ke rekening BARU."], ["catatan lain", "bayar ke rekening baru."]
    )
    assert [c.ok for c in checks] == [True]
    assert [c.ok for c in compare_notes(["Catatan hilang"], ["teks lain"])] == [False]


def test_a_pdf_through_the_whole_chain() -> None:
    doc = DOCS["COC-01"]
    llm = ScriptedLlm([extracted(doc.truth), NO])
    result = evaluate_document(doc, llm=llm, reader=READER, policy=POLICY, review=True)
    assert result.critical_ok and result.status == "ok" and result.readings == 1
    assert result.chain_matches_oracle and result.chain_matches_expected
    assert result.review_status == "ok" and result.calls == 2
    assert result.calls_per_model == {"model-utama": 2}
    assert Decimal(result.cost_usd) == Decimal("0.002")


def test_ubl_needs_only_the_reviewer() -> None:
    llm = ScriptedLlm([NO])
    result = evaluate_document(DOCS["PJK-01"], llm=llm, reader=READER, policy=POLICY, review=True)
    assert result.source == "ubl" and result.critical_ok and result.readings == 0
    assert [r.purpose for r in llm.requests] == ["injection_review"]
    assert result.chain_matches_oracle and result.chain_matches_expected


def test_a_strong_pattern_skips_the_reviewer_and_gives_c5() -> None:
    doc = DOCS["INJ-01"]
    llm = ScriptedLlm([extracted(doc.truth)])
    result = evaluate_document(doc, llm=llm, reader=READER, policy=POLICY, review=True)
    assert result.chain_c5 and result.injection_expected and result.review_status is None
    assert result.chain_matches_oracle


def test_without_reviewer() -> None:
    llm = ScriptedLlm([extracted(DOCS["COC-01"].truth)])
    result = evaluate_document(DOCS["COC-01"], llm=llm, reader=READER, policy=POLICY, review=False)
    assert result.review_status is None and result.calls == 1


def test_a_run_stops_at_the_call_limit_and_resumes(tmp_path: Path) -> None:
    docs = [DOCS["COC-01"], DOCS["COC-04"]]
    path = tmp_path / "run.json"
    first = ScriptedLlm([extracted(docs[0].truth), NO])
    run = run_documents(
        state(), docs, llm=first, reader=READER, policy=POLICY, path=path, max_calls=1,
        echo=lambda _: None,
    )  # fmt: skip
    assert [d.id for d in run.documents] == ["COC-01"] and run.stopped
    saved = RunState.model_validate_json(path.read_text(encoding="utf-8"))
    second = ScriptedLlm([extracted(docs[1].truth), NO])
    done = run_documents(
        saved, docs, llm=second, reader=READER, policy=POLICY, path=path, echo=lambda _: None
    )
    assert [d.id for d in done.documents] == ["COC-01", "COC-04"] and done.stopped is None
    summary = summarize(done.documents)
    assert summary["per_kelompok"]["faktur"]["dokumen_sempurna"] == "2/2"


def test_an_unavailable_llm_stops_the_run_and_keeps_what_was_done(tmp_path: Path) -> None:
    docs = [DOCS["COC-01"], DOCS["COC-04"]]
    llm = ScriptedLlm([extracted(docs[0].truth), NO, LlmUnavailable("kuota harian habis")])
    run = run_documents(
        state(), docs, llm=llm, reader=READER, policy=POLICY, path=tmp_path / "run.json",
        echo=lambda _: None,
    )  # fmt: skip
    assert [d.id for d in run.documents] == ["COC-01"]
    assert run.stopped is not None and "COC-04" in run.stopped


def test_summary_lists_every_wrong_field() -> None:
    doc = DOCS["COC-01"]
    reading = extracted(doc.truth, total="Rp 1.000")
    llm = ScriptedLlm([reading, reading, NO])
    result = evaluate_document(doc, llm=llm, reader=READER, policy=POLICY, review=True)
    errors = summarize([result])["kesalahan"]
    assert {
        "dokumen": "COC-01",
        "field": "total",
        "kebenaran": str(doc.truth.total),
        "hasil_baca": "1000",
    } in errors
