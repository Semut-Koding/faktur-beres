"""``GeminiClient`` without a network: request settings, the error table, fallback, throttle."""

import random
import threading
import time

import httpx
import pytest
from google.genai import errors, types
from pydantic import BaseModel, SecretStr

from faktur_beres.config import Settings
from faktur_beres.llm.contracts import (
    BlobPart,
    CacheUse,
    CallStatus,
    ErrorKind,
    LlmConfigError,
    LlmInvalidOutput,
    LlmRequest,
    LlmUnavailable,
    ModelRole,
    TextPart,
    ThinkingLevel,
    request_key,
)
from faktur_beres.llm.gemini import (
    API_VERSION,
    GeminiClient,
    MissingApiKey,
    classify,
    generation_settings,
    per_day_quota,
    retry_delay_ms,
    rpm_by_model,
    sdk_client,
)
from llm_fakes import (
    RETRY_INFO,
    FakeClock,
    Outcome,
    ScriptedGenerate,
    api_error,
    no_candidates,
    quota_per_day,
    quota_per_minute,
    reply,
)

PRIMARY = "gemini-uji-lite"
STRONG = "gemini-uji-flash"
GOOD = '{"status": "ok", "total": "1.000"}'
SECRET_TEXT = "TEKS-DOKUMEN-RAHASIA-123"
SECRET_BYTES = b"%PDF-1.7 BYTE-DOKUMEN-RAHASIA"


class Answer(BaseModel):
    status: str
    total: str


def make_request(
    role: ModelRole = ModelRole.PRIMARY,
    *,
    blob: bool = False,
    thinking: ThinkingLevel = ThinkingLevel.LOW,
    prompt_version: str = "uji-v1",
) -> LlmRequest:
    parts: list[TextPart | BlobPart] = [TextPart(text=f"Baca teks ini: {SECRET_TEXT}")]
    if blob:
        parts.append(BlobPart(data=SECRET_BYTES, mime_type="application/pdf"))
    return LlmRequest(
        purpose="uji",
        role=role,
        prompt_version=prompt_version,
        system_instruction="Instruksi sistem uji.",
        parts=tuple(parts),
        thinking_level=thinking,
    )


def make_client(
    script: dict[str, list[Outcome]],
    *,
    rpm: dict[str, int] | None = None,
    primary: str = PRIMARY,
    strong: str = STRONG,
) -> tuple[GeminiClient, ScriptedGenerate, FakeClock]:
    clock = FakeClock()
    generate = ScriptedGenerate(script, clock=clock)
    client = GeminiClient(
        generate,
        primary=primary,
        strong=strong,
        rpm=rpm or {PRIMARY: 60, STRONG: 60},
        clock=clock,
        rng=random.Random(7),
    )
    return client, generate, clock


def test_valid_answer_on_the_first_try() -> None:
    client, generate, _ = make_client({PRIMARY: [reply(GOOD)]})
    result = client.generate(make_request(), Answer)
    assert result.value == Answer(status="ok", total="1.000")
    assert result.model == PRIMARY and not result.from_cache
    [call] = result.calls
    assert (call.status, call.attempt, call.fallback, call.cache) == (
        CallStatus.OK,
        1,
        False,
        CacheUse.OFF,
    )
    assert (call.input_tokens, call.output_tokens, call.thinking_tokens, call.total_tokens) == (
        100,
        20,
        30,
        150,
    )
    assert call.finish_reason == "STOP" and call.duration_ms == 2_000
    assert (
        call.request_key
        == request_key(PRIMARY, make_request(), Answer, generation_settings(make_request()))[:16]
    )
    assert generate.calls_to(PRIMARY) == 1


def test_request_settings() -> None:
    client, generate, _ = make_client({PRIMARY: [reply(GOOD), reply(GOOD)]})
    client.generate(make_request(blob=True), Answer)
    client.generate(make_request(thinking=ThinkingLevel.HIGH), Answer)
    with_document, text_only = generate.sent
    config = with_document.config
    assert config.system_instruction == "Instruksi sistem uji."
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == Answer.model_json_schema()
    assert config.max_output_tokens == 8192
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert config.media_resolution == types.MediaResolution.MEDIA_RESOLUTION_HIGH
    assert config.store is False and config.automatic_continuation is False
    assert (config.temperature, config.top_p, config.top_k) == (None, None, None)
    assert text_only.config.media_resolution is None
    assert text_only.config.thinking_config is not None
    assert text_only.config.thinking_config.thinking_level == types.ThinkingLevel.HIGH
    # The document goes inline with its MIME type and no name; the system text is not in contents.
    parts = with_document.contents.parts or []
    assert [p.text for p in parts][0] == f"Baca teks ini: {SECRET_TEXT}"
    blob = parts[1].inline_data
    assert blob is not None
    assert (blob.data, blob.mime_type, blob.display_name) == (
        SECRET_BYTES,
        "application/pdf",
        None,
    )
    assert "Instruksi sistem uji." not in str(with_document.contents)


def test_thoughts_are_not_part_of_the_answer() -> None:
    client, _, _ = make_client({PRIMARY: [reply(GOOD, thought_text="menimbang total")]})
    assert client.generate(make_request(), Answer).value.status == "ok"


@pytest.mark.aturan("BR-27")
def test_quota_per_minute_waits_for_retry_info() -> None:
    client, generate, clock = make_client({PRIMARY: [quota_per_minute("17s"), reply(GOOD)]})
    result = client.generate(make_request(), Answer)
    assert result.model == PRIMARY
    assert [c.error_kind for c in result.calls] == [ErrorKind.QUOTA_PER_MINUTE, None]
    assert result.calls[0].http_status == 429
    [wait] = clock.sleeps_ms
    assert 17_000 <= wait <= 18_000  # RetryInfo plus up to 1 s jitter
    assert result.calls[1].waited_ms == wait and result.calls[1].attempt == 2


@pytest.mark.aturan("BR-27")
def test_quota_per_minute_without_retry_info_backs_off() -> None:
    client, _, clock = make_client({PRIMARY: [quota_per_minute(None), reply(GOOD)]})
    client.generate(make_request(), Answer)
    [wait] = clock.sleeps_ms
    assert 2_000 <= wait <= 3_000


@pytest.mark.aturan("BR-27")
def test_quota_per_minute_three_retries_then_the_fallback_model() -> None:
    client, generate, _ = make_client(
        {PRIMARY: [quota_per_minute("5s") for _ in range(4)], STRONG: [reply(GOOD)]}
    )
    result = client.generate(make_request(), Answer)
    assert result.model == STRONG
    assert generate.calls_to(PRIMARY) == 4  # the first try and three retries
    assert [(c.model, c.attempt, c.fallback) for c in result.calls][-2:] == [
        (PRIMARY, 4, False),
        (STRONG, 1, True),
    ]
    assert client.exhausted == frozenset()


@pytest.mark.aturan("BR-27")
def test_retry_info_longer_than_the_cap_goes_to_the_fallback_at_once() -> None:
    client, generate, clock = make_client(
        {PRIMARY: [quota_per_minute("300s")], STRONG: [reply(GOOD)]}
    )
    assert client.generate(make_request(), Answer).model == STRONG
    assert generate.calls_to(PRIMARY) == 1 and clock.sleeps_ms == []


@pytest.mark.aturan("BR-27")
def test_quota_per_day_marks_the_model_exhausted_for_the_process() -> None:
    client, generate, clock = make_client(
        {PRIMARY: [quota_per_day()], STRONG: [reply(GOOD), reply(GOOD)]}
    )
    first = client.generate(make_request(), Answer)
    assert first.model == STRONG and clock.sleeps_ms == []
    assert [c.error_kind for c in first.calls] == [ErrorKind.QUOTA_PER_DAY, None]
    assert client.exhausted == {PRIMARY}
    second = client.generate(make_request(), Answer)
    assert second.model == STRONG and [c.model for c in second.calls] == [STRONG]
    assert generate.calls_to(PRIMARY) == 1


@pytest.mark.aturan("BR-27")
@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (api_error(500, "INTERNAL"), ErrorKind.SERVER_ERROR),
        (api_error(503, "UNAVAILABLE"), ErrorKind.SERVER_ERROR),
        (api_error(504, "DEADLINE_EXCEEDED"), ErrorKind.SERVER_ERROR),
        (api_error(408, "REQUEST_TIMEOUT"), ErrorKind.TIMEOUT),
        (httpx.ReadTimeout("habis waktu"), ErrorKind.TIMEOUT),
        (httpx.ConnectError("tidak tersambung"), ErrorKind.NETWORK_ERROR),
    ],
    ids=["500", "503", "504", "408", "timeout", "koneksi"],
)
def test_server_errors_and_timeouts_are_retried_with_backoff(
    error: Exception, kind: ErrorKind
) -> None:
    client, _, clock = make_client({PRIMARY: [error, reply(GOOD)]})
    result = client.generate(make_request(), Answer)
    assert result.model == PRIMARY and result.calls[0].error_kind == kind
    [wait] = clock.sleeps_ms
    assert 2_000 <= wait <= 3_000


@pytest.mark.aturan("BR-27")
def test_server_errors_back_off_exponentially_then_fall_back() -> None:
    client, generate, clock = make_client(
        {PRIMARY: [api_error(503, "UNAVAILABLE") for _ in range(4)], STRONG: [reply(GOOD)]}
    )
    assert client.generate(make_request(), Answer).model == STRONG
    assert generate.calls_to(PRIMARY) == 4
    assert len(clock.sleeps_ms) == 3
    for wait, base in zip(clock.sleeps_ms, (2_000, 4_000, 8_000), strict=True):
        assert base <= wait <= base + 1_000  # 2 s, 4 s, 8 s plus up to 1 s jitter


def test_invalid_output_is_retried_once_on_the_same_model() -> None:
    client, generate, _ = make_client({PRIMARY: [reply("bukan json"), reply(GOOD)]})
    result = client.generate(make_request(), Answer)
    assert result.model == PRIMARY and generate.calls_to(STRONG) == 0
    assert [c.error_kind for c in result.calls] == [ErrorKind.INVALID_OUTPUT, None]


@pytest.mark.parametrize(
    "bad",
    [
        reply("bukan json"),
        reply('{"status": "ok"}'),  # schema: total is missing
        reply(GOOD, finish=types.FinishReason.MAX_TOKENS),
        reply(GOOD, finish=types.FinishReason.SAFETY),
        reply(None),
        no_candidates(),
    ],
    ids=["json", "skema", "max-tokens", "safety", "kosong", "diblokir"],
)
def test_invalid_output_twice_goes_back_to_the_caller(
    bad: types.GenerateContentResponse,
) -> None:
    client, generate, _ = make_client({PRIMARY: [bad, bad.model_copy()], STRONG: [reply(GOOD)]})
    with pytest.raises(LlmInvalidOutput) as caught:
        client.generate(make_request(), Answer)
    assert generate.calls_to(STRONG) == 0
    assert [c.error_kind for c in caught.value.calls] == [ErrorKind.INVALID_OUTPUT] * 2


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_refused_requests_are_configuration_errors_without_retry(code: int) -> None:
    client, generate, clock = make_client(
        {PRIMARY: [api_error(code, "INVALID_ARGUMENT")], STRONG: [reply(GOOD)]}
    )
    with pytest.raises(LlmConfigError) as caught:
        client.generate(make_request(), Answer)
    assert generate.calls_to(PRIMARY) == 1 and generate.calls_to(STRONG) == 0
    assert clock.sleeps_ms == []
    [call] = caught.value.calls
    assert (call.error_kind, call.http_status) == (ErrorKind.CONFIG_ERROR, code)


@pytest.mark.aturan("BR-27")
def test_both_models_unavailable() -> None:
    busy: list[Outcome] = [api_error(503, "UNAVAILABLE") for _ in range(8)]
    client, generate, _ = make_client({PRIMARY: [quota_per_day()], STRONG: busy})
    with pytest.raises(LlmUnavailable) as caught:
        client.generate(make_request(), Answer)
    assert [c.model for c in caught.value.calls] == [PRIMARY] + [STRONG] * 4
    assert caught.value.kind == "llm_unavailable"
    # The next request does not try the exhausted model again.
    with pytest.raises(LlmUnavailable) as again:
        client.generate(make_request(), Answer)
    assert [c.model for c in again.value.calls] == [STRONG] * 4
    assert generate.calls_to(PRIMARY) == 1


@pytest.mark.aturan("BR-27")
def test_the_strong_role_falls_back_to_the_primary_model() -> None:
    client, _, _ = make_client({STRONG: [quota_per_day()], PRIMARY: [reply(GOOD)]})
    result = client.generate(make_request(ModelRole.STRONG), Answer)
    assert result.model == PRIMARY
    assert [(c.model, c.fallback) for c in result.calls] == [(STRONG, False), (PRIMARY, True)]


def test_one_model_for_both_roles_is_tried_once_per_request() -> None:
    client, generate, _ = make_client(
        {PRIMARY: [api_error(503, "UNAVAILABLE") for _ in range(4)]}, strong=PRIMARY
    )
    with pytest.raises(LlmUnavailable):
        client.generate(make_request(), Answer)
    assert generate.calls_to(PRIMARY) == 4


def test_other_exceptions_are_not_swallowed() -> None:
    client, _, _ = make_client({PRIMARY: [RuntimeError("bug")]})
    with pytest.raises(RuntimeError, match="bug"):
        client.generate(make_request(), Answer)


def test_throttle_keeps_60_over_rpm_seconds_between_requests_to_one_model() -> None:
    client, _, clock = make_client(
        {PRIMARY: [reply(GOOD), reply(GOOD)], STRONG: [reply(GOOD)]},
        rpm={PRIMARY: 5, STRONG: 5},
    )
    client.generate(make_request(), Answer)
    second = client.generate(make_request(), Answer)
    assert clock.sleeps_ms == [10_000]  # 12 s gap minus the 2 s the first request took
    assert second.calls[0].waited_ms == 10_000
    other = client.generate(make_request(ModelRole.STRONG), Answer)
    assert other.calls[0].waited_ms == 0 and clock.sleeps_ms == [10_000]


def test_requests_are_sequential() -> None:
    active = 0
    most = 0
    lock = threading.Lock()

    def generate(
        *, model: str, contents: types.Content, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        nonlocal active, most
        with lock:
            active += 1
            most = max(most, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return reply(GOOD)

    client = GeminiClient(generate, primary=PRIMARY, strong=STRONG, rpm={PRIMARY: 60_000})
    threads = [
        threading.Thread(target=client.generate, args=(make_request(), Answer)) for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert most == 1


def test_request_repr_hides_document_content() -> None:
    text = repr(make_request(blob=True))
    assert SECRET_TEXT not in text and "RAHASIA" not in text
    assert "Instruksi sistem uji." not in text


def test_request_key_changes_with_what_decides_the_answer() -> None:
    def key(model: str = PRIMARY, request: LlmRequest | None = None) -> str:
        req = request or make_request()
        return request_key(model, req, Answer, generation_settings(req))

    base = key()
    assert len(base) == 64 and base == key()
    assert key(model=STRONG) != base
    assert key(request=make_request(prompt_version="uji-v2")) != base
    assert key(request=make_request(blob=True)) != base
    assert key(request=make_request(thinking=ThinkingLevel.MEDIUM)) != base
    assert SECRET_TEXT not in base


def retry_body(delay: str) -> dict[str, object]:
    return {"error": {"details": [{"@type": RETRY_INFO, "retryDelay": delay}]}}


def test_error_details_are_read_from_the_api_body() -> None:
    assert retry_delay_ms([]) is None
    assert retry_delay_ms(retry_body("17s")) == 17_000
    assert retry_delay_ms(retry_body("0.5s")) == 500
    assert retry_delay_ms(retry_body("1.0001s")) == 1001  # rounded up
    assert retry_delay_ms(retry_body("x")) is None
    assert retry_delay_ms(retry_body("17s")["error"]) == 17_000  # body without the "error" key
    day = quota_per_day()
    minute = quota_per_minute()
    assert isinstance(day, errors.APIError) and isinstance(minute, errors.APIError)
    assert per_day_quota(day.details) and not per_day_quota(minute.details)
    assert classify(ValueError("lain")) is None


def test_sdk_client_needs_a_key_and_disables_sdk_retry() -> None:
    with pytest.raises(MissingApiKey):
        sdk_client(Settings(_env_file=None, gemini_api_key=None))
    client = sdk_client(Settings(_env_file=None, gemini_api_key=SecretStr("kunci-palsu-uji")))
    options = client._api_client._http_options
    assert options.api_version == API_VERSION
    assert options.retry_options is not None and options.retry_options.attempts == 1
    assert options.timeout == 120_000


def test_rpm_per_model() -> None:
    settings = Settings(_env_file=None, gemini_rpm_primary=15, gemini_rpm_strong=5)
    assert rpm_by_model(settings) == {"gemini-3.5-flash-lite": 15, "gemini-3.8-flash": 5}
    same = Settings(
        _env_file=None,
        gemini_model_strong="gemini-3.5-flash-lite",
        gemini_rpm_primary=15,
        gemini_rpm_strong=5,
    )
    assert rpm_by_model(same) == {"gemini-3.5-flash-lite": 5}
