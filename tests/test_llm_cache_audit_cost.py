"""Cache, audit (BR-23) and equivalent cost of LLM calls, without a network."""

import json
import logging
import random
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, SecretStr, ValidationError

from faktur_beres.config import REPO_ROOT, Settings
from faktur_beres.llm.cache import CacheEntry, LlmCache
from faktur_beres.llm.contracts import (
    UNKNOWN_COST,
    BlobPart,
    CacheUse,
    ErrorKind,
    LlmCallRecord,
    LlmInvalidOutput,
    LlmRequest,
    ModelRole,
    TextPart,
)
from faktur_beres.llm.cost import PRICES_PATH, PriceList, load_prices, total_cost
from faktur_beres.llm.gemini import HTTP_LOGGERS, GeminiClient, cache_dir, sdk_client
from llm_fakes import FakeClock, Outcome, ScriptedGenerate, quota_per_minute, reply

MODEL = "gemini-3.5-flash-lite"
FALLBACK = "gemini-3.8-flash"
GOOD = '{"status": "ok"}'
DOC_TEXT = "TEKS-DOKUMEN-RAHASIA-777"
DOC_BYTES = b"%PDF-1.7 BYTE-RAHASIA-777"
SYSTEM = "INSTRUKSI-SISTEM-RAHASIA"
API_KEY = "AIza-kunci-palsu-uji"
TODAY = date(2026, 11, 9)


class Answer(BaseModel):
    status: str


def make_request(prompt_version: str = "uji-v1") -> LlmRequest:
    return LlmRequest(
        purpose="uji",
        role=ModelRole.PRIMARY,
        prompt_version=prompt_version,
        system_instruction=SYSTEM,
        parts=(TextPart(text=DOC_TEXT), BlobPart(data=DOC_BYTES, mime_type="application/pdf")),
    )


def make_client(
    script: dict[str, list[Outcome]],
    *,
    cache: LlmCache | None = None,
    primary: str = MODEL,
) -> tuple[GeminiClient, ScriptedGenerate]:
    clock = FakeClock()
    generate = ScriptedGenerate(script, clock=clock)
    client = GeminiClient(
        generate,
        primary=primary,
        strong=FALLBACK,
        rpm={primary: 60, FALLBACK: 60},
        clock=clock,
        rng=random.Random(3),
        cache=cache,
        prices=load_prices(),
    )
    return client, generate


# --- cache -----------------------------------------------------------------------------------


def test_without_a_cache_the_record_says_off() -> None:
    client, _ = make_client({MODEL: [reply(GOOD)]})
    result = client.generate(make_request(), Answer)
    assert result.calls[0].cache is CacheUse.OFF and not result.from_cache


def test_cache_miss_then_hit(tmp_path: Path) -> None:
    client, generate = make_client({MODEL: [reply(GOOD)]}, cache=LlmCache(tmp_path))
    first = client.generate(make_request(), Answer)
    assert first.calls[0].cache is CacheUse.MISS and not first.from_cache
    second = client.generate(make_request(), Answer)  # nothing scripted: a request would fail
    assert second.value == first.value and second.from_cache and second.model == MODEL
    [hit] = second.calls
    assert (hit.cache, hit.attempt, hit.input_tokens, hit.cost_usd) == (CacheUse.HIT, 0, None, None)
    assert generate.calls_to(MODEL) == 1


def test_cache_key_follows_the_prompt_version(tmp_path: Path) -> None:
    client, generate = make_client({MODEL: [reply(GOOD), reply(GOOD)]}, cache=LlmCache(tmp_path))
    client.generate(make_request("uji-v1"), Answer)
    assert not client.generate(make_request("uji-v2"), Answer).from_cache
    assert generate.calls_to(MODEL) == 2


@pytest.mark.parametrize(
    "damage", ["bukan-json", "entri-tidak-lengkap", "skema-lain", "kunci-lain"]
)
def test_a_damaged_entry_is_ignored_and_written_again(tmp_path: Path, damage: str) -> None:
    client, generate = make_client({MODEL: [reply(GOOD), reply(GOOD)]}, cache=LlmCache(tmp_path))
    client.generate(make_request(), Answer)
    [path] = list(tmp_path.rglob("*.json"))
    entry = CacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
    damaged = {
        "bukan-json": "{rusak",
        "entri-tidak-lengkap": '{"version": 1}',
        "skema-lain": entry.model_copy(update={"output": '{"lain": 1}'}).model_dump_json(),
        "kunci-lain": entry.model_copy(update={"key": "0" * 64}).model_dump_json(),
    }[damage]
    path.write_text(damaged, encoding="utf-8")
    result = client.generate(make_request(), Answer)
    assert not result.from_cache and generate.calls_to(MODEL) == 2
    rewritten = CacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
    assert (rewritten.key, rewritten.output) == (entry.key, GOOD)


def test_invalid_answers_are_never_stored(tmp_path: Path) -> None:
    client, _ = make_client(
        {MODEL: [reply("bukan json"), reply("bukan json")]}, cache=LlmCache(tmp_path)
    )
    with pytest.raises(LlmInvalidOutput):
        client.generate(make_request(), Answer)
    assert list(tmp_path.rglob("*")) == []


def test_cache_files_hold_no_document_prompt_or_key(tmp_path: Path) -> None:
    client, _ = make_client({MODEL: [reply(GOOD)]}, cache=LlmCache(tmp_path))
    client.generate(make_request(), Answer)
    [path] = list(tmp_path.rglob("*.json"))
    text = path.read_text(encoding="utf-8")
    for secret in (DOC_TEXT, SYSTEM, API_KEY, DOC_BYTES.decode()):
        assert secret not in text
    assert json.loads(text)["output"] == GOOD


def test_cache_location_and_keys() -> None:
    assert cache_dir(Settings(_env_file=None)) == REPO_ROOT / "var" / "cache" / "llm"
    assert cache_dir(Settings(_env_file=None, llm_cache_dir="/srv/cache")) == Path("/srv/cache")
    with pytest.raises(ValueError, match="SHA-256"):
        LlmCache(REPO_ROOT).path("../../luar")


# --- audit (BR-23) ---------------------------------------------------------------------------


@pytest.mark.aturan("BR-23")
def test_every_attempt_is_logged_and_returned(caplog: pytest.LogCaptureFixture) -> None:
    client, _ = make_client({MODEL: [quota_per_minute("1s"), reply(GOOD)]})
    with caplog.at_level(logging.INFO, logger="faktur_beres.llm"):
        result = client.generate(make_request(), Answer)
    logged = [r.llm_call for r in caplog.records if hasattr(r, "llm_call")]
    assert logged == [call.model_dump(mode="json") for call in result.calls]
    assert {r.name for r in caplog.records} == {"faktur_beres.llm"}
    quota, answer = result.calls
    assert (quota.error_kind, quota.http_status, quota.cost_usd) == (
        ErrorKind.QUOTA_PER_MINUTE,
        429,
        None,
    )
    assert answer.status == "ok" and answer.prompt_version == "uji-v1"
    assert answer.duration_ms == 2_000 and 1_000 <= answer.waited_ms <= 2_000
    assert len(answer.request_key) == 16 and answer.at.tzinfo is not None
    assert {"input_tokens", "output_tokens", "thinking_tokens", "total_tokens"} <= set(logged[1])


@pytest.mark.aturan("BR-23")
def test_audit_holds_no_document_prompt_answer_or_key(caplog: pytest.LogCaptureFixture) -> None:
    client, _ = make_client({MODEL: [reply("bukan json"), reply(GOOD)]})
    with caplog.at_level(logging.DEBUG):
        result = client.generate(make_request(), Answer)
    dumped = "\n".join(
        record.getMessage() + json.dumps(getattr(record, "llm_call", {}))
        for record in caplog.records
    )
    dumped += "".join(call.model_dump_json() for call in result.calls)
    for secret in (DOC_TEXT, SYSTEM, API_KEY, DOC_BYTES.decode(), "bukan json", GOOD):
        assert secret not in dumped


@pytest.mark.aturan("BR-23")
def test_http_libraries_never_log_requests() -> None:
    try:
        for name in HTTP_LOGGERS:
            logging.getLogger(name).setLevel(logging.DEBUG)
        sdk_client(Settings(_env_file=None, gemini_api_key=SecretStr(API_KEY)))
        for name in HTTP_LOGGERS:
            assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
    finally:
        for name in HTTP_LOGGERS:
            logging.getLogger(name).setLevel(logging.NOTSET)


def test_call_records_round_trip_through_json() -> None:
    client, _ = make_client({"model-tanpa-harga": [reply(GOOD)]}, primary="model-tanpa-harga")
    [unknown] = client.generate(make_request(), Answer).calls
    assert unknown.cost_usd == UNKNOWN_COST
    assert LlmCallRecord.model_validate_json(unknown.model_dump_json()) == unknown
    priced, _ = make_client({MODEL: [reply(GOOD)]})
    [known] = priced.generate(make_request(), Answer).calls
    assert LlmCallRecord.model_validate_json(known.model_dump_json()) == known


# --- equivalent cost -------------------------------------------------------------------------


def test_price_file_lists_the_candidate_models() -> None:
    prices = load_prices()
    assert prices.source.startswith("https://") and prices.currency == "USD"
    assert {"gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"} <= set(prices.models)
    lite = prices.period("gemini-3.5-flash-lite", TODAY)
    assert lite is not None
    assert (lite.input_per_million, lite.output_per_million) == (Decimal("0.30"), Decimal("2.50"))


@pytest.mark.aturan("BR-23")
def test_cost_is_decimal_and_counts_thinking_as_output() -> None:
    cost = load_prices().cost(
        "gemini-3.5-flash-lite", TODAY, input_tokens=1000, output_tokens=200, thinking_tokens=300
    )
    assert isinstance(cost, Decimal) and cost == Decimal("0.00155")  # (1000×0.30 + 500×2.50)/1e6


def test_price_follows_the_call_date() -> None:
    prices = load_prices()

    def per_million_input(day: date) -> object:
        return prices.cost(
            "gemini-3.8-flash", day, input_tokens=1_000_000, output_tokens=0, thinking_tokens=None
        )

    assert per_million_input(date(2026, 12, 31)) == Decimal("0.75")
    assert per_million_input(date(2027, 1, 1)) == Decimal("1.50")


def test_unknown_price_is_unknown_not_zero() -> None:
    prices = load_prices()
    unknown = prices.cost("gemini-lain", TODAY, input_tokens=10, output_tokens=1, thinking_tokens=0)
    assert unknown == UNKNOWN_COST
    nothing = prices.cost(
        "gemini-3.5-flash-lite", TODAY, input_tokens=None, output_tokens=None, thinking_tokens=None
    )
    assert nothing is None


def test_price_file_refuses_floats_and_overlapping_periods() -> None:
    raw = yaml.safe_load(PRICES_PATH.read_text(encoding="utf-8"))
    raw["model"]["gemini-3.5-flash-lite"][0]["masukan_per_juta"] = 0.3
    with pytest.raises(ValidationError, match="float"):
        PriceList.model_validate(raw)
    raw = yaml.safe_load(PRICES_PATH.read_text(encoding="utf-8"))
    raw["model"]["gemini-3.8-flash"][1]["mulai"] = date(2026, 12, 1)
    with pytest.raises(ValidationError, match="tumpang"):
        PriceList.model_validate(raw)


def test_records_carry_the_cost_and_the_total_adds_up() -> None:
    client, _ = make_client(
        {
            MODEL: [
                reply("bukan json", prompt=1000, output=0, thoughts=0),
                reply(GOOD, prompt=1000, output=200, thoughts=300),
            ]
        }
    )
    result = client.generate(make_request(), Answer)
    assert [c.cost_usd for c in result.calls] == [Decimal("0.0003"), Decimal("0.00155")]
    assert total_cost(result.calls) == Decimal("0.00185")
    unknown, _ = make_client({"model-tanpa-harga": [reply(GOOD)]}, primary="model-tanpa-harga")
    assert total_cost(unknown.generate(make_request(), Answer).calls) == UNKNOWN_COST
