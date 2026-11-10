"""Small parts of ``eval:graph`` that need no service."""

import pytest

from faktur_beres.evals.graph import cases, percentile_95, timing_row


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
