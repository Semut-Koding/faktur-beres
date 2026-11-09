"""Fakes for LLM tests without a network: a clock, a scripted ``generate_content``, SDK replies.

``ScriptedGenerate`` stands in for the SDK call inside ``GeminiClient``, so the real request
building, error classification, retry, fallback, throttle and audit code all run. Every call moves
the fake clock forward by ``latency_ms``, like a real request would.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from google.genai import errors, types
from pydantic import BaseModel

from faktur_beres.llm.contracts import (
    CacheUse,
    CallStatus,
    ErrorKind,
    LlmCallRecord,
    LlmRequest,
    LlmResult,
    ModelRole,
    TextPart,
)

NS_PER_MS = 1_000_000
START = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)
RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"
QUOTA_METRIC = "generativelanguage.googleapis.com/generate_content_free_tier_requests"


class FakeClock:
    def __init__(self) -> None:
        self.ns = 0
        self.sleeps_ms: list[int] = []

    def monotonic_ns(self) -> int:
        return self.ns

    def sleep_ns(self, ns: int) -> None:
        self.sleeps_ms.append(ns // NS_PER_MS)
        self.ns += max(ns, 0)

    def now(self) -> datetime:
        return START + timedelta(microseconds=self.ns // 1000)


Outcome = types.GenerateContentResponse | BaseException


@dataclass
class Sent:
    model: str
    contents: types.Content
    config: types.GenerateContentConfig


@dataclass
class ScriptedGenerate:
    """Answers each request with the next outcome scripted for its model."""

    script: dict[str, list[Outcome]]
    clock: FakeClock | None = None
    latency_ms: int = 2_000
    sent: list[Sent] = field(default_factory=list)

    def __call__(
        self, *, model: str, contents: types.Content, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        self.sent.append(Sent(model, contents, config))
        if self.clock is not None:
            self.clock.ns += self.latency_ms * NS_PER_MS
        queue = self.script.get(model)
        if not queue:
            raise AssertionError(f"tidak ada jawaban terjadwal untuk {model}")
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def calls_to(self, model: str) -> int:
        return sum(1 for s in self.sent if s.model == model)


def reply(
    text: str | None,
    *,
    finish: types.FinishReason | None = types.FinishReason.STOP,
    prompt: int = 100,
    output: int = 20,
    thoughts: int = 30,
    thought_text: str | None = None,
) -> types.GenerateContentResponse:
    parts = []
    if thought_text is not None:
        parts.append(types.Part(text=thought_text, thought=True))
    if text is not None:
        parts.append(types.Part(text=text))
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=finish)
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt,
            candidates_token_count=output,
            thoughts_token_count=thoughts,
            total_token_count=prompt + output + thoughts,
        ),
    )


def no_candidates() -> types.GenerateContentResponse:
    """A blocked prompt: no candidate at all."""
    return types.GenerateContentResponse(
        candidates=[],
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.OTHER
        ),
    )


def api_error(code: int, status: str, details: Iterable[dict[str, object]] = ()) -> Exception:
    body = {
        "error": {"code": code, "status": status, "message": "pesan uji", "details": list(details)}
    }
    if code >= 500:
        return errors.ServerError(code, body)
    return errors.ClientError(code, body)


def quota_failure(quota_id: str) -> dict[str, object]:
    return {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [
            {
                "quotaMetric": QUOTA_METRIC,
                "quotaId": quota_id,
            }
        ],
    }


def quota_per_minute(retry_delay: str | None = "17s") -> Exception:
    details = [quota_failure("GenerateRequestsPerMinutePerProjectPerModel-FreeTier")]
    if retry_delay is not None:
        details.append(
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
        )
    return api_error(429, "RESOURCE_EXHAUSTED", details)


def quota_per_day() -> Exception:
    return api_error(
        429,
        "RESOURCE_EXHAUSTED",
        [
            quota_failure("GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
            {"@type": RETRY_INFO, "retryDelay": "40s"},
        ],
    )


# --- a fake StructuredLlm, for the code that uses the client ---------------------------------


def call_record(request: LlmRequest, model: str, *, ok: bool = True) -> LlmCallRecord:
    return LlmCallRecord(
        at=START,
        purpose=request.purpose,
        role=request.role,
        model=model,
        fallback=False,
        prompt_version=request.prompt_version,
        request_key="0" * 16,
        attempt=1,
        cache=CacheUse.OFF,
        duration_ms=1_000,
        status=CallStatus.OK if ok else CallStatus.ERROR,
        error_kind=None if ok else ErrorKind.INVALID_OUTPUT,
        finish_reason="STOP",
        input_tokens=1_000,
        output_tokens=200,
        thinking_tokens=50,
        total_tokens=1_250,
        cost_usd=Decimal("0.001"),
    )


class ScriptedLlm:
    """``StructuredLlm`` fake: each request gets the next scripted answer, a value or an error.

    An answer that is a function of the request is called with it, so a test can answer by role.
    """

    def __init__(
        self,
        answers: Iterable[BaseModel | BaseException | Callable[[LlmRequest], BaseModel]],
        *,
        models: dict[ModelRole, str] | None = None,
    ) -> None:
        self.answers = list(answers)
        self.models = models or {ModelRole.PRIMARY: "model-utama", ModelRole.STRONG: "model-kuat"}
        self.requests: list[LlmRequest] = []

    def generate[T: BaseModel](self, request: LlmRequest, schema: type[T]) -> LlmResult[T]:
        self.requests.append(request)
        if not self.answers:
            raise AssertionError("tidak ada jawaban terjadwal")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer) and not isinstance(answer, BaseModel):
            answer = answer(request)
        model = self.models[request.role]
        value = schema.model_validate(answer.model_dump())
        return LlmResult(value=value, model=model, calls=(call_record(request, model),))

    def texts(self, index: int) -> str:
        """All text parts of request ``index`` (the system instruction included)."""
        request = self.requests[index]
        parts = [p.text for p in request.parts if isinstance(p, TextPart)]
        return "\n".join([request.system_instruction, *parts])
