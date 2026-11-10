"""Small parts of ``eval:graph`` that need no service."""

from decimal import Decimal

import pytest

from faktur_beres.config import REPO_ROOT
from faktur_beres.evals.graph import (
    Result,
    _pattern_reply_reader,
    cases,
    oracle_extractor,
    percentile_95,
    summarize,
    timing_row,
)


def test_the_95th_percentile_of_few_values_is_the_largest() -> None:
    assert percentile_95([1.0, 2.0, 3.0, 4.0, 135.0]) == 135.0


def test_the_95th_percentile_uses_the_nearest_rank() -> None:
    assert percentile_95([float(n) for n in range(1, 21)]) == 19.0


def test_cases_cover_both_sets_and_refuse_an_unknown_id() -> None:
    assert len(cases(("faktur", "ketahanan"), ())) == 53
    assert [c.id for c in cases(("faktur",), ("COC-04",))] == ["COC-04"]
    with pytest.raises(ValueError, match="tidak dikenal"):
        cases(("faktur",), ("ZZZ-99",))


def test_timing_row_reports_median_p95_and_maximum() -> None:
    assert timing_row([1.0, 2.0, 3.0, 4.0, 135.0]) == {
        "n": 5,
        "median_detik": 3.0,
        "p95_detik": 135.0,
        "maks_detik": 135.0,
    }


def test_the_oracle_recognises_a_corrected_attachment_by_its_hash() -> None:
    found = cases(("faktur",), ("QTY-01",))
    label = found[0].label
    reply = label.simulasi.balasan_vendor  # type: ignore[union-attr]
    extract = oracle_extractor(found)
    data = (REPO_ROOT / reply.berkas.path).read_bytes()
    outcome = extract(data)
    assert outcome.invoice is not None
    assert outcome.invoice.invoice_number == reply.kebenaran_dokumen.nomor_faktur


def test_the_pattern_reader_only_reads_the_text() -> None:
    assert _pattern_reply_reader("mohon pakai p00033 ya").numbers == ("P00033",)


def test_summary_counts_replies_and_route_choices() -> None:
    row = Result(
        id="X",
        group="faktur",
        category="c",
        format="pdf",
        channel="email",
        invoice_id="FB-000001",
        route="B",
        reasons=[],
        status_first="awaiting_vendor",
        status_final="draft_created",
        message=None,
        first_stop_passed=True,
        final_passed=False,
        checks=[],
        seconds_to_route=2.0,
        calls_per_model={"m": 2},
        calls_per_purpose={"embedding": 1, "route_choice": 1},
        cost_usd=Decimal(1),
        replies=1,
        llm_route_choices=1,
        escalations=1,
        guard_refusals=0,
    )
    summary = summarize([row])
    assert (summary["balasan_diproses"], summary["eskalasi_llm"]) == (1, 1)
    assert summary["panggilan_embedding"] == 1 and summary["gagal"] == ["X"]
    assert summary["waktu_sampai_jalur"]["keseluruhan"]["n"] == 1
