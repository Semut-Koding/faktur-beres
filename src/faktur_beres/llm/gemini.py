"""``GeminiClient``: structured output from Gemini, with retry, fallback model and throttle (M3.2).

Request (``generation_config``):

* the system instruction apart from the content; documents inline with their detected MIME type,
  never with a file name;
* JSON output constrained by the JSON schema of the Pydantic model, then validated by that model;
* thinking level per purpose (``low`` by default), high media resolution when a document is sent;
* capped ``max_output_tokens``; ``temperature``, ``top_p`` and ``top_k`` stay at the model default;
* ``store=False``: the request is left out of the project's request logs (ADR-0008);
* ``automatic_continuation=False``: one HTTP request per attempt, so throttle and audit see each.

Errors. The SDK's own retry is off and every attempt is one audit record (BR-23):

* 429 per minute: wait ``RetryInfo`` (else backoff), at most 3 retries, then the fallback model;
* 429 per day (a ``quotaId`` contains ``PerDay``): the model is exhausted for this process and the
  fallback model answers at once;
* 5xx, 408, timeout, connection error: backoff, at most 3 retries, then the fallback model;
* invalid output (finish reason not STOP, bad JSON, wrong schema): once more on the same model,
  then ``LlmInvalidOutput`` to the caller;
* other 4xx (400, 401, 403, 404): no retry, ``LlmConfigError``.

Fallback chain: primary → strong → fallback for the primary role, strong → primary → fallback for
the strong role; a model appears once. ``allow_fallback=False`` leaves the fallback role out. On the
fallback model, whose daily quota is small, 5xx, 408, timeout and connection errors get one retry.
When no model answers: ``LlmUnavailable`` (BR-27). Requests are sequential: one lock per client.

Timeout per request (``http_options`` of the config, which the SDK merges over the client's): a
request with a document waits ``GEMINI_TIMEOUT_SECONDS``, or ``GEMINI_TIMEOUT_FALLBACK_SECONDS`` on
the fallback model; a request without one waits ``GEMINI_TIMEOUT_TEXT_SECONDS`` on every model.

Function calling (``run_tools``): declarations from Pydantic, mode ``ANY``, and no automatic
function calling. Each model turn joins the history exactly as received, so its
``thought_signature`` values go back unchanged; parallel calls are answered together in one turn,
in order. An unknown function or invalid arguments are not run: the model gets an error result and
the turn counts in the budget.
Throttle, retry, audit and cache work per turn. The cache key covers the history and declarations;
an entry stores the model turn (parts and signatures, base64), never the prompt.

Audit (BR-23): every attempt and every cache hit is one ``LlmCallRecord``, logged on the
``faktur_beres.llm`` logger with ``extra={"llm_call": ...}`` and returned to the caller, in the
result or in the error. It carries the equivalent cost at the paid-tier price
(``faktur_beres.llm.cost``). The cache (``faktur_beres.llm.cache``) is used only when the client is
given one, and only valid answers are stored.
"""

import logging
import random
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from faktur_beres.config import REPO_ROOT, Settings
from faktur_beres.llm.cache import CacheEntry, LlmCache
from faktur_beres.llm.contracts import (
    KEY_SHOWN,
    UNKNOWN_COST,
    BlobPart,
    CacheUse,
    CallStatus,
    ErrorKind,
    LlmCallRecord,
    LlmConfigError,
    LlmInvalidOutput,
    LlmRequest,
    LlmResult,
    LlmUnavailable,
    ModelRole,
    ThinkingLevel,
    ToolBudget,
    ToolEvent,
    ToolEventStatus,
    ToolReply,
    ToolRunResult,
    ToolRunStatus,
    ToolSession,
    ToolSpec,
    request_key,
    tool_turn_key,
)
from faktur_beres.llm.cost import Cost, PriceList, load_prices
from faktur_beres.llm.pacing import (
    DEFAULT_RPM,
    NS_PER_MS,
    Clock,
    SystemClock,
    Throttle,
    backoff_ms,
    jitter_ms,
)

API_VERSION = "v1beta"  # written out: SDK 3.x changes the default to v1 (M3.2)
JSON_MIME = "application/json"
MAX_RETRIES = 3  # per model: quota per minute, server errors, timeouts
MAX_RETRIES_RESERVE = 1  # the fallback model: server errors, timeouts, connection errors
MAX_INVALID_RETRIES = 1
MAX_WAIT_MS = 120_000  # a longer RetryInfo wait goes to the fallback model instead
HTTP_LOGGERS = ("httpx", "httpcore", "google_genai")
MESSAGE_SHOWN = 300
LOGGER = logging.getLogger("faktur_beres.llm")
FALLBACK_LIMITED = frozenset({ErrorKind.SERVER_ERROR, ErrorKind.TIMEOUT, ErrorKind.NETWORK_ERROR})

THINKING = {
    ThinkingLevel.LOW: types.ThinkingLevel.LOW,
    ThinkingLevel.MEDIUM: types.ThinkingLevel.MEDIUM,
    ThinkingLevel.HIGH: types.ThinkingLevel.HIGH,
}


Contents = types.Content | list[types.Content]


class GenerateFn(Protocol):
    """``generate_content`` of the SDK (``SdkGenerate``), or a fake in tests."""

    def __call__(
        self, *, model: str, contents: Contents, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse: ...


class MissingApiKey(LlmConfigError):
    """``GEMINI_API_KEY`` is empty."""


def quiet_http_loggers() -> None:
    """Request lines and headers of the HTTP libraries stay out of the logs (no DEBUG, no INFO)."""
    for name in HTTP_LOGGERS:
        logger = logging.getLogger(name)
        if logger.getEffectiveLevel() < logging.WARNING:
            logger.setLevel(logging.WARNING)


def sdk_client(settings: Settings) -> genai.Client:
    """The SDK client: explicit API version, the configured timeout, SDK retry off."""
    if settings.gemini_api_key is None:
        raise MissingApiKey("GEMINI_API_KEY kosong: isi di .env (jangan ditempel di chat).")
    quiet_http_loggers()
    return genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=types.HttpOptions(
            api_version=API_VERSION,
            timeout=settings.gemini_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


class SdkGenerate:
    """Holds the SDK client, whose HTTP connections close when it is garbage-collected."""

    def __init__(self, client: genai.Client) -> None:
        self._client = client

    def __call__(
        self, *, model: str, contents: Contents, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        return self._client.models.generate_content(model=model, contents=contents, config=config)


def rpm_by_model(settings: Settings) -> dict[str, int]:
    rpm = {settings.gemini_model_primary: settings.gemini_rpm_primary}
    strong = settings.gemini_model_strong
    rpm[strong] = min(rpm.get(strong, settings.gemini_rpm_strong), settings.gemini_rpm_strong)
    fallback = settings.gemini_model_fallback
    rpm[fallback] = min(
        rpm.get(fallback, settings.gemini_rpm_fallback), settings.gemini_rpm_fallback
    )
    return rpm


def cache_dir(settings: Settings) -> Path:
    path = Path(settings.llm_cache_dir)
    return path if path.is_absolute() else REPO_ROOT / path


def gemini_client(
    settings: Settings, *, use_cache: bool = False, only_model: str | None = None
) -> "GeminiClient":
    """The agent's client; the cache only when the caller asks for it (``--pakai-cache``).

    ``only_model`` puts one model in both roles (comparison runs, M3.7): every request goes to it
    and there is no fallback. When that model is the configured fallback model, its limits apply.
    """
    primary = only_model or settings.gemini_model_primary
    strong = only_model or settings.gemini_model_strong
    fallback = settings.gemini_model_fallback
    reserve = None if only_model is not None and only_model != fallback else fallback
    known = rpm_by_model(settings)
    models = {primary, strong} | ({reserve} if reserve else set())
    return GeminiClient(
        SdkGenerate(sdk_client(settings)),
        primary=primary,
        strong=strong,
        fallback=reserve,
        rpm={model: known.get(model, DEFAULT_RPM) for model in models},
        cache=LlmCache(cache_dir(settings)) if use_cache else None,
        prices=load_prices(),
        timeouts=timeouts_from(settings),
    )


@dataclass(frozen=True)
class Timeouts:
    """Per-request timeouts, by what is sent (M4a.2)."""

    document_ms: int = 120_000
    fallback_document_ms: int = 180_000
    text_ms: int = 60_000


def timeouts_from(settings: Settings) -> Timeouts:
    return Timeouts(
        document_ms=settings.gemini_timeout_seconds * 1000,
        fallback_document_ms=settings.gemini_timeout_fallback_seconds * 1000,
        text_ms=settings.gemini_timeout_text_seconds * 1000,
    )


def generation_settings(request: LlmRequest) -> dict[str, object]:
    """The generation settings sent with the request; they are part of the request key."""
    return {
        "api_version": API_VERSION,
        "response_mime_type": JSON_MIME,
        "thinking_level": request.thinking_level.value,
        "max_output_tokens": request.max_output_tokens,
        "media_resolution": "high" if request.has_media else None,
        "store": False,
    }


def generation_config(
    request: LlmRequest, schema: type[BaseModel], timeout_ms: int | None = None
) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        http_options=types.HttpOptions(timeout=timeout_ms) if timeout_ms is not None else None,
        system_instruction=request.system_instruction,
        response_mime_type=JSON_MIME,
        response_json_schema=schema.model_json_schema(),
        max_output_tokens=request.max_output_tokens,
        thinking_config=types.ThinkingConfig(thinking_level=THINKING[request.thinking_level]),
        media_resolution=(
            types.MediaResolution.MEDIA_RESOLUTION_HIGH if request.has_media else None
        ),
        store=False,
        automatic_continuation=False,
    )


def tool_settings(request: LlmRequest, allowed: Sequence[str]) -> dict[str, object]:
    """The generation settings of a function-calling turn; they are part of its key."""
    return {
        "allowed_function_names": list(allowed),
        "api_version": API_VERSION,
        "thinking_level": request.thinking_level.value,
        "max_output_tokens": request.max_output_tokens,
        "function_calling_mode": "ANY",
        "store": False,
    }


def function_declaration(tool: ToolSpec) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description,
        parameters_json_schema=tool.schema.model_json_schema(),
    )


def tool_config(
    request: LlmRequest, tools: Sequence[ToolSpec], allowed: Sequence[str], timeout_ms: int
) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        http_options=types.HttpOptions(timeout=timeout_ms),
        system_instruction=request.system_instruction,
        max_output_tokens=request.max_output_tokens,
        thinking_config=types.ThinkingConfig(thinking_level=THINKING[request.thinking_level]),
        tools=[types.Tool(function_declarations=[function_declaration(t) for t in tools])],
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY,
                allowed_function_names=list(allowed),
            )
        ),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        store=False,
        automatic_continuation=False,
    )


def request_content(request: LlmRequest) -> types.Content:
    parts = [
        types.Part.from_bytes(data=part.data, mime_type=part.mime_type)
        if isinstance(part, BlobPart)
        else types.Part.from_text(text=part.text)
        for part in request.parts
    ]
    return types.Content(role="user", parts=parts)


@dataclass(frozen=True)
class Failure:
    """The row of the error table an exception falls in."""

    kind: ErrorKind
    http_status: int | None = None
    retry_after_ms: int | None = None


def _error_items(details: object) -> list[dict[str, object]]:
    body = details.get("error", details) if isinstance(details, dict) else None
    items = body.get("details") if isinstance(body, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _of_type(item: dict[str, object], name: str) -> bool:
    return str(item.get("@type", "")).endswith(name)


def per_day_quota(details: object) -> bool:
    """True when a ``QuotaFailure`` violation names a per-day quota."""
    for item in _error_items(details):
        violations = item.get("violations") if _of_type(item, "QuotaFailure") else None
        for violation in violations if isinstance(violations, list) else []:
            if isinstance(violation, dict) and "PerDay" in str(violation.get("quotaId", "")):
                return True
    return False


def retry_delay_ms(details: object) -> int | None:
    """The ``RetryInfo.retryDelay`` (e.g. ``"17s"``, ``"0.5s"``) in milliseconds, rounded up."""
    for item in _error_items(details):
        raw = item.get("retryDelay") if _of_type(item, "RetryInfo") else None
        if isinstance(raw, str) and raw.endswith("s"):
            try:
                seconds = Decimal(raw.removesuffix("s"))
            except InvalidOperation:
                return None
            if seconds.is_finite() and seconds >= 0:
                return int((seconds * 1000).to_integral_value(rounding=ROUND_CEILING))
    return None


def classify(exc: BaseException) -> Failure | None:
    """The error-table row; None for an exception that is neither an API nor a network error."""
    if isinstance(exc, errors.APIError):
        code = exc.code if isinstance(exc.code, int) else 0
        if code == 429:
            if per_day_quota(exc.details):
                return Failure(ErrorKind.QUOTA_PER_DAY, code)
            return Failure(ErrorKind.QUOTA_PER_MINUTE, code, retry_delay_ms(exc.details))
        if code == 408:
            return Failure(ErrorKind.TIMEOUT, code)
        if code >= 500 or code == 0:
            return Failure(ErrorKind.SERVER_ERROR, code or None)
        return Failure(ErrorKind.CONFIG_ERROR, code)
    if isinstance(exc, errors.UnknownApiResponseError):
        return Failure(ErrorKind.SERVER_ERROR)
    if isinstance(exc, httpx.TimeoutException):
        return Failure(ErrorKind.TIMEOUT)
    if isinstance(exc, httpx.TransportError):
        return Failure(ErrorKind.NETWORK_ERROR)
    return None


def api_message(exc: BaseException) -> str:
    """The status and message the API gave, shortened; they hold neither the key nor content."""
    if not isinstance(exc, errors.APIError):
        return ""
    message = " ".join(str(exc.message or "").split())
    if len(message) > MESSAGE_SHOWN:
        message = message[:MESSAGE_SHOWN] + "…"
    return f" {exc.status or ''}: {message}".rstrip(": ")


@dataclass(frozen=True)
class Reading:
    """What the client keeps of a response: the answer text and the metadata for the audit."""

    text: str | None
    finish_reason: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    total_tokens: int | None = None


def read_response(response: types.GenerateContentResponse) -> Reading:
    candidate = response.candidates[0] if response.candidates else None
    finish = candidate.finish_reason if candidate is not None else None
    text = None
    if candidate is not None and candidate.content is not None and candidate.content.parts:
        pieces = [p.text for p in candidate.content.parts if p.text is not None and not p.thought]
        text = "".join(pieces) if pieces else None
    usage = response.usage_metadata
    return Reading(
        text=text,
        finish_reason=str(getattr(finish, "value", finish)) if finish is not None else None,
        input_tokens=usage.prompt_token_count if usage else None,
        output_tokens=usage.candidates_token_count if usage else None,
        thinking_tokens=usage.thoughts_token_count if usage else None,
        total_tokens=usage.total_token_count if usage else None,
    )


def accept_model_turn(
    response: types.GenerateContentResponse, reading: Reading
) -> types.Content | None:
    """The model turn as received, or None unless it ended normally with a function call."""
    if reading.finish_reason != types.FinishReason.STOP.value or not response.candidates:
        return None
    content = response.candidates[0].content
    if content is None or not any(p.function_call for p in content.parts or []):
        return None
    return content


def _load_model_turn(cache: LlmCache, key: str) -> types.Content | None:
    cached = cache.load(key, types.Content)
    return cached.value if cached is not None else None


def parse[T: BaseModel](reading: Reading, schema: type[T]) -> T | None:
    """The validated value, or None for an invalid output (finish reason not STOP, bad JSON)."""
    if reading.finish_reason != types.FinishReason.STOP.value or not reading.text:
        return None
    try:
        return schema.model_validate_json(reading.text)
    except ValidationError:
        return None


@dataclass(frozen=True)
class _Exchange[V]:
    """What differs between a structured request and a turn of a tool conversation."""

    config: types.GenerateContentConfig
    contents: Contents
    accept: Callable[[types.GenerateContentResponse, Reading], V | None]
    load: Callable[[LlmCache, str], V | None]
    dump: Callable[[V, Reading], str | None]


class _BudgetSpent(Exception):
    """Raised inside a conversation when its budget has no call left."""


class _ToolUsage:
    """The budget of one ``run_tools`` run, over every model it tries."""

    def __init__(self, budget: ToolBudget) -> None:
        self.budget = budget
        self.model_calls = 0
        self.tool_runs = 0
        self.events: list[ToolEvent] = []

    def take_model_call(self) -> None:
        if self.model_calls >= self.budget.model_calls:
            raise _BudgetSpent
        self.model_calls += 1

    def allowed_now(self, names: Sequence[str]) -> list[str]:
        """Every tool, or only the finishing one when a single tool run is left."""
        finish = self.budget.finish_tool
        if finish is not None and self.tool_runs >= self.budget.tool_runs - 1:
            return [finish]
        return list(names)

    def take_tool_run(self) -> None:
        if self.tool_runs >= self.budget.tool_runs:
            raise _BudgetSpent
        self.tool_runs += 1

    def result[S: ToolSession](
        self, status: ToolRunStatus, session: S, model: str | None, calls: list[LlmCallRecord]
    ) -> ToolRunResult[S]:
        return ToolRunResult(
            status=status,
            session=session,
            model=model,
            model_calls=self.model_calls,
            tool_runs=self.tool_runs,
            events=tuple(self.events),
            calls=tuple(calls),
        )


@dataclass(frozen=True)
class _Target:
    """One model asked within one request: what every audit record of it repeats."""

    request: LlmRequest
    model: str
    fallback: bool
    key: str

    def record(
        self,
        *,
        attempt: int,
        at: datetime,
        waited_ns: int,
        duration_ns: int,
        cache: CacheUse,
        failure: Failure | None = None,
        reading: Reading | None = None,
        cost: Cost = None,
        ok: bool,
    ) -> LlmCallRecord:
        if ok:
            error_kind = None
        elif failure is not None:
            error_kind = failure.kind
        else:
            error_kind = ErrorKind.INVALID_OUTPUT
        return LlmCallRecord(
            at=at,
            purpose=self.request.purpose,
            role=self.request.role,
            model=self.model,
            fallback=self.fallback,
            prompt_version=self.request.prompt_version,
            request_key=self.key[:KEY_SHOWN],
            attempt=attempt,
            cache=cache,
            waited_ms=waited_ns // NS_PER_MS,
            duration_ms=duration_ns // NS_PER_MS,
            status=CallStatus.OK if ok else CallStatus.ERROR,
            error_kind=error_kind,
            http_status=failure.http_status if failure is not None else None,
            finish_reason=reading.finish_reason if reading is not None else None,
            input_tokens=reading.input_tokens if reading is not None else None,
            output_tokens=reading.output_tokens if reading is not None else None,
            thinking_tokens=reading.thinking_tokens if reading is not None else None,
            total_tokens=reading.total_tokens if reading is not None else None,
            cost_usd=cost,
        )


class GeminiClient:
    """``StructuredLlm`` over the Gemini API; see the module docstring for the error table."""

    def __init__(
        self,
        generate: GenerateFn,
        *,
        primary: str,
        strong: str,
        fallback: str | None = None,
        rpm: Mapping[str, int],
        timeouts: Timeouts | None = None,
        clock: Clock | None = None,
        rng: random.Random | None = None,
        cache: LlmCache | None = None,
        prices: PriceList | None = None,
        logger: logging.Logger = LOGGER,
    ) -> None:
        self._generate = generate
        self._cache = cache
        self._prices = prices
        self._logger = logger
        self._models = {ModelRole.PRIMARY: primary, ModelRole.STRONG: strong}
        self._reserve = fallback
        if fallback is not None:
            self._models[ModelRole.FALLBACK] = fallback
        self._timeouts = timeouts or Timeouts()
        self._clock = clock or SystemClock()
        self._rng = rng or random.Random()
        self._throttle = Throttle(rpm, self._clock)
        self._exhausted: set[str] = set()
        self._lock = threading.Lock()

    @property
    def exhausted(self) -> frozenset[str]:
        """Models whose daily quota ran out in this process."""
        return frozenset(self._exhausted)

    def chain(self, role: ModelRole, *, allow_fallback: bool = True) -> tuple[str, ...]:
        """The models that may answer, in order; each appears once."""
        if role is ModelRole.FALLBACK:
            return (self._reserve,) if self._reserve else ()
        other = ModelRole.STRONG if role is ModelRole.PRIMARY else ModelRole.PRIMARY
        models = [self._models[role], self._models[other]]
        if allow_fallback and self._reserve is not None:
            models.append(self._reserve)
        return tuple(dict.fromkeys(models))

    def generate[T: BaseModel](self, request: LlmRequest, schema: type[T]) -> LlmResult[T]:
        with self._lock:
            calls: list[LlmCallRecord] = []
            chain = self.chain(request.role, allow_fallback=request.allow_fallback)
            for model in chain:
                if model in self._exhausted:
                    continue
                value = self._on_model(model, request, schema, calls)
                if value is not None:
                    return LlmResult(value=value, model=model, calls=tuple(calls))
            raise LlmUnavailable(
                f"{request.purpose}: tidak ada model yang tersedia ({', '.join(chain)})", calls
            )

    def _note(self, calls: list[LlmCallRecord], record: LlmCallRecord) -> None:
        """Keep the record for the caller and log it (BR-23); it holds no content and no key."""
        calls.append(record)
        self._logger.info(
            "llm %s %s percobaan %d: %s",
            record.purpose,
            record.model,
            record.attempt,
            record.error_kind or ("cache" if record.cache is CacheUse.HIT else record.status),
            extra={"llm_call": record.model_dump(mode="json")},
        )

    def _cost(self, model: str, at: datetime, reading: Reading) -> Cost:
        if self._prices is None:
            reported = (reading.input_tokens, reading.output_tokens, reading.thinking_tokens)
            return UNKNOWN_COST if any(t is not None for t in reported) else None
        return self._prices.cost(
            model,
            at.date(),
            input_tokens=reading.input_tokens,
            output_tokens=reading.output_tokens,
            thinking_tokens=reading.thinking_tokens,
        )

    def _on_model[T: BaseModel](
        self, model: str, request: LlmRequest, schema: type[T], calls: list[LlmCallRecord]
    ) -> T | None:
        """The value, or None when this model is unavailable and the next one should answer."""

        def load(cache: LlmCache, key: str) -> T | None:
            cached = cache.load(key, schema)
            return cached.value if cached is not None else None

        exchange = _Exchange[T](
            config=generation_config(request, schema, self._timeout_ms(model, request)),
            contents=request_content(request),
            accept=lambda _response, reading: parse(reading, schema),
            load=load,
            dump=lambda _value, reading: reading.text,
        )
        key = request_key(model, request, schema, generation_settings(request))
        return self._ask(self._target(request, model, key), exchange, calls)

    def _target(self, request: LlmRequest, model: str, key: str) -> _Target:
        by_role = self._models.get(request.role)
        return _Target(
            request=request,
            model=model,
            fallback=model != by_role or model == self._reserve,
            key=key,
        )

    def _timeout_ms(self, model: str, request: LlmRequest) -> int:
        if not request.has_media:
            return self._timeouts.text_ms
        return (
            self._timeouts.fallback_document_ms
            if model == self._reserve
            else (self._timeouts.document_ms)
        )

    def _retry_limit(self, model: str, failure: Failure) -> int:
        if model == self._reserve and failure.kind in FALLBACK_LIMITED:
            return MAX_RETRIES_RESERVE
        return MAX_RETRIES

    def _ask[V](
        self,
        target: _Target,
        exchange: _Exchange[V],
        calls: list[LlmCallRecord],
        before_request: Callable[[], None] | None = None,
    ) -> V | None:
        """One question to one model, with its retries; None when the next model should answer."""
        model = target.model
        if self._cache is not None:
            cached = exchange.load(self._cache, target.key)
            if cached is not None:
                if before_request is not None:
                    before_request()
                hit = target.record(
                    attempt=0,
                    at=self._clock.now(),
                    waited_ns=0,
                    duration_ns=0,
                    cache=CacheUse.HIT,
                    ok=True,
                )
                self._note(calls, hit)
                return cached
        cache_use = CacheUse.OFF if self._cache is None else CacheUse.MISS
        attempt = retries = invalid = 0
        waited_ns = 0
        while True:
            attempt += 1
            if before_request is not None:
                before_request()
            waited_ns += self._throttle.wait(model)
            at = self._clock.now()
            started = self._clock.monotonic_ns()
            try:
                response = self._generate(
                    model=model, contents=exchange.contents, config=exchange.config
                )
            except Exception as exc:
                failure = classify(exc)
                if failure is None:
                    raise
                self._note(
                    calls,
                    target.record(
                        attempt=attempt,
                        at=at,
                        waited_ns=waited_ns,
                        duration_ns=self._clock.monotonic_ns() - started,
                        cache=cache_use,
                        failure=failure,
                        ok=False,
                    ),
                )
                if failure.kind is ErrorKind.CONFIG_ERROR:
                    raise LlmConfigError(
                        f"{model}: permintaan ditolak (HTTP {failure.http_status})"
                        f"{api_message(exc)}",
                        calls,
                    ) from exc
                if failure.kind is ErrorKind.QUOTA_PER_DAY:
                    self._exhausted.add(model)
                    return None
                if retries >= self._retry_limit(model, failure):
                    return None
                retries += 1
                delay_ms = self._delay_ms(failure, retries)
                if delay_ms is None:
                    return None
                self._clock.sleep_ns(delay_ms * NS_PER_MS)
                waited_ns = delay_ms * NS_PER_MS
                continue
            reading = read_response(response)
            value = exchange.accept(response, reading)
            self._note(
                calls,
                target.record(
                    attempt=attempt,
                    at=at,
                    waited_ns=waited_ns,
                    duration_ns=self._clock.monotonic_ns() - started,
                    cache=cache_use,
                    reading=reading,
                    cost=self._cost(model, at, reading),
                    ok=value is not None,
                ),
            )
            waited_ns = 0
            if value is not None:
                output = exchange.dump(value, reading)
                if self._cache is not None and output is not None:
                    self._cache.store(
                        CacheEntry(
                            key=target.key,
                            model=model,
                            purpose=target.request.purpose,
                            prompt_version=target.request.prompt_version,
                            created=at,
                            output=output,
                        )
                    )
                return value
            if invalid == MAX_INVALID_RETRIES:
                raise LlmInvalidOutput(
                    f"{model}: keluaran tidak valid {invalid + 1} kali"
                    f" (finish_reason {reading.finish_reason})",
                    calls,
                )
            invalid += 1

    # --- function calling ---------------------------------------------------------------------

    def run_tools[S: ToolSession](
        self,
        request: LlmRequest,
        tools: Sequence[ToolSpec],
        new_session: Callable[[], S],
        budget: ToolBudget,
    ) -> ToolRunResult[S]:
        """A function-calling conversation; see the module docstring.

        A model that becomes unavailable mid-way is replaced by the next one in the chain, and the
        conversation restarts there with a new session: thought signatures are not mixed between
        models. The budget covers the whole run.
        """
        usage = _ToolUsage(budget)
        calls: list[LlmCallRecord] = []
        chain = self.chain(request.role, allow_fallback=request.allow_fallback)
        session: S | None = None
        for model in chain:
            if model in self._exhausted:
                continue
            session = new_session()
            try:
                finished = self._conversation(model, request, tools, session, usage, calls)
            except _BudgetSpent:
                return usage.result(ToolRunStatus.BUDGET_EXHAUSTED, session, model, calls)
            if finished:
                return usage.result(ToolRunStatus.FINISHED, session, model, calls)
        raise LlmUnavailable(
            f"{request.purpose}: tidak ada model yang tersedia ({', '.join(chain)})", calls
        )

    def _conversation(
        self,
        model: str,
        request: LlmRequest,
        tools: Sequence[ToolSpec],
        session: ToolSession,
        usage: _ToolUsage,
        calls: list[LlmCallRecord],
    ) -> bool:
        """True when a tool ended it, False when this model became unavailable."""
        specs = {tool.name: tool for tool in tools}
        names = [tool.name for tool in tools]
        timeout = self._timeout_ms(model, request)
        history: list[types.Content] = [request_content(request)]
        while True:
            allowed = usage.allowed_now(names)
            config = tool_config(request, tools, allowed, timeout)
            settings = tool_settings(request, allowed)
            dumped = [turn.model_dump(mode="json", exclude_none=True) for turn in history]
            key = tool_turn_key(model, request, tools, dumped, settings)
            exchange = _Exchange[types.Content](
                config=config,
                contents=list(history),
                accept=accept_model_turn,
                load=_load_model_turn,
                dump=lambda value, _reading: value.model_dump_json(exclude_none=True),
            )
            with self._lock:
                turn = self._ask(
                    self._target(request, model, key), exchange, calls, usage.take_model_call
                )
            if turn is None:
                return False
            history.append(turn)  # exactly as received: every part, every thought signature
            answers: list[types.Part] = []
            for part in turn.parts or []:
                if part.function_call is None:
                    continue
                reply = self._run_call(part.function_call, specs, session, usage)
                if reply.terminal:
                    return True
                answers.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=part.function_call.id,
                            name=part.function_call.name,
                            response=dict(reply.result),
                        )
                    )
                )
            history.append(types.Content(role="user", parts=answers))

    @staticmethod
    def _run_call(
        call: types.FunctionCall,
        specs: Mapping[str, ToolSpec],
        session: ToolSession,
        usage: _ToolUsage,
    ) -> ToolReply:
        """Run one call, or answer with an error when it is unknown or its arguments are invalid."""
        name = call.name or ""
        spec = specs.get(name)
        if spec is None:
            usage.events.append(ToolEvent(name[:80], ToolEventStatus.UNKNOWN_FUNCTION))
            return ToolReply({"error": f"fungsi tidak dikenal; yang tersedia: {sorted(specs)}"})
        try:
            args = spec.schema.model_validate(call.args or {})
        except ValidationError as exc:
            usage.events.append(ToolEvent(name, ToolEventStatus.INVALID_ARGS))
            problems = [
                {"field": ".".join(str(p) for p in e["loc"]), "masalah": e["type"]}
                for e in exc.errors(include_input=False, include_url=False, include_context=False)
            ]
            return ToolReply({"error": "argumen tidak valid", "rincian": problems})
        usage.take_tool_run()
        usage.events.append(ToolEvent(name, ToolEventStatus.RUN))
        return session.call(name, args)

    def _delay_ms(self, failure: Failure, retry: int) -> int | None:
        """Wait before the retry; None when ``RetryInfo`` asks for longer than ``MAX_WAIT_MS``."""
        if failure.retry_after_ms is None:
            return backoff_ms(retry, self._rng)
        if failure.retry_after_ms > MAX_WAIT_MS:
            return None
        return failure.retry_after_ms + jitter_ms(self._rng)
