"""The audit file of the evaluation tools and the count per quota window."""

import logging
from datetime import UTC, datetime
from pathlib import Path

from faktur_beres.evals.audit_log import attach, last_reset, requests_since
from faktur_beres.llm.gemini import LOGGER


def record(model: str, at: str, cache: str = "off") -> dict[str, object]:
    return {"model": model, "at": at, "cache": cache, "status": "error", "purpose": "uji"}


def test_records_are_appended_and_counted_per_window(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    handler = attach("run-uji", path)
    try:
        LOGGER.info("x", extra={"llm_call": record("flash", "2026-11-09T07:59:00+00:00")})
        LOGGER.info("x", extra={"llm_call": record("flash", "2026-11-09T08:14:00+00:00")})
        LOGGER.info("x", extra={"llm_call": record("lite", "2026-11-09T08:15:00+00:00")})
        LOGGER.info("x", extra={"llm_call": record("lite", "2026-11-09T08:16:00+00:00", "hit")})
        LOGGER.info("tanpa catatan")
    finally:
        LOGGER.removeHandler(handler)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4 and '"run": "run-uji"' in lines[0]
    since = last_reset(datetime(2026, 11, 9, 9, 0, tzinfo=UTC))
    assert since == datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
    assert requests_since(since, path) == {"flash": 1, "lite": 1}


def test_the_window_starts_at_15_wib() -> None:
    before = datetime(2026, 11, 9, 7, 30, tzinfo=UTC)  # 14.30 WIB
    assert last_reset(before) == datetime(2026, 11, 8, 8, 0, tzinfo=UTC)
    assert logging.getLogger("faktur_beres.llm") is LOGGER
