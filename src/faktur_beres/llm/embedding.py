"""Embeddings for the policy search (M4b.4), with ``gemini-embedding-2`` and 768 dimensions.

Input format from Google's guide for this model (aistudio.google.com/docs/embeddings, read on 10
November 2026): ``task_type`` is not supported, so the task goes in the text, as
``title: ... | text: ...`` for a document and ``task: search result | query: ...`` for a query. The
model normalises truncated dimensions itself. One request per text. Throttle, retry and the audit
follow ``GeminiClient``; the audit purpose is ``embedding``. The cache stores the vector only.
"""

import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from google.genai import errors, types
from pydantic import BaseModel

from faktur_beres.config import Settings
from faktur_beres.llm.cache import CacheEntry, LlmCache
from faktur_beres.llm.contracts import (
    KEY_SHOWN,
    CacheUse,
    CallStatus,
    ErrorKind,
    LlmCallRecord,
    LlmConfigError,
    LlmInvalidOutput,
    LlmUnavailable,
    ModelRole,
    sha256_hex,
)
from faktur_beres.llm.cost import Cost, PriceList, load_prices
from faktur_beres.llm.embedding_contract import EMBEDDING_DIMENSIONS
from faktur_beres.llm.gemini import (
    LOGGER,
    MAX_RETRIES,
    MAX_WAIT_MS,
    Failure,
    classify,
    sdk_client,
)
from faktur_beres.llm.pacing import (
    NS_PER_MS,
    Clock,
    SystemClock,
    Throttle,
    backoff_ms,
    jitter_ms,
)

PURPOSE = "embedding"
VERSION = "embedding-v1"
TIMEOUT_MS = 30_000


def document_input(title: str, text: str) -> str:
    return f"title: {title} | text: {text}"


def query_input(text: str) -> str:
    return f"task: search result | query: {text}"


@dataclass(frozen=True)
class Embedded:
    vectors: tuple[tuple[float, ...], ...]
    model: str
    calls: tuple[LlmCallRecord, ...]


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> Embedded: ...


class _Stored(BaseModel):
    values: list[float]


class EmbedFn(Protocol):
    def __call__(
        self, *, model: str, contents: str, config: types.EmbedContentConfig
    ) -> types.EmbedContentResponse: ...


class SdkEmbed:
    def __init__(self, client: object) -> None:
        self._client = client

    def __call__(
        self, *, model: str, contents: str, config: types.EmbedContentConfig
    ) -> types.EmbedContentResponse:
        return self._client.models.embed_content(  # type: ignore[attr-defined,no-any-return]
            model=model, contents=contents, config=config
        )


class GeminiEmbedder:
    def __init__(
        self,
        embed: EmbedFn,
        *,
        model: str,
        rpm: int,
        clock: Clock | None = None,
        rng: random.Random | None = None,
        cache: LlmCache | None = None,
        prices: PriceList | None = None,
        logger: logging.Logger = LOGGER,
    ) -> None:
        self.model = model
        self._embed = embed
        self._clock = clock or SystemClock()
        self._rng = rng or random.Random()
        self._throttle = Throttle({model: rpm}, self._clock)
        self._cache = cache
        self._prices = prices
        self._logger = logger

    def embed(self, texts: Sequence[str]) -> Embedded:
        calls: list[LlmCallRecord] = []
        vectors = tuple(self._one(text, calls) for text in texts)
        return Embedded(vectors=vectors, model=self.model, calls=tuple(calls))

    def _key(self, text: str) -> str:
        return sha256_hex(f"{self.model}|{EMBEDDING_DIMENSIONS}|{sha256_hex(text)}")

    def _note(self, calls: list[LlmCallRecord], record: LlmCallRecord) -> None:
        calls.append(record)
        self._logger.info(
            "llm embedding %s percobaan %d: %s",
            record.model,
            record.attempt,
            record.error_kind or record.status,
            extra={"llm_call": record.model_dump(mode="json")},
        )

    def _record(
        self,
        key: str,
        *,
        attempt: int,
        at: datetime,
        waited_ns: int,
        duration_ns: int,
        cache: CacheUse,
        failure: Failure | None = None,
        tokens: int | None = None,
    ) -> LlmCallRecord:
        cost: Cost = None
        if self._prices is not None and tokens is not None:
            cost = self._prices.cost(
                self.model, at.date(), input_tokens=tokens, output_tokens=0, thinking_tokens=0
            )
        return LlmCallRecord(
            at=at,
            purpose=PURPOSE,
            role=ModelRole.PRIMARY,
            model=self.model,
            fallback=False,
            prompt_version=VERSION,
            request_key=key[:KEY_SHOWN],
            attempt=attempt,
            cache=cache,
            waited_ms=waited_ns // NS_PER_MS,
            duration_ms=duration_ns // NS_PER_MS,
            status=CallStatus.ERROR if failure else CallStatus.OK,
            error_kind=failure.kind if failure else None,
            http_status=failure.http_status if failure else None,
            input_tokens=tokens,
            total_tokens=tokens,
            cost_usd=cost,
        )

    def _one(self, text: str, calls: list[LlmCallRecord]) -> tuple[float, ...]:
        key = self._key(text)
        if self._cache is not None:
            cached = self._cache.load(key, _Stored)
            if cached is not None and len(cached.value.values) == EMBEDDING_DIMENSIONS:
                self._note(
                    calls,
                    self._record(
                        key,
                        attempt=0,
                        at=self._clock.now(),
                        waited_ns=0,
                        duration_ns=0,
                        cache=CacheUse.HIT,
                    ),
                )
                return tuple(cached.value.values)
        use = CacheUse.OFF if self._cache is None else CacheUse.MISS
        config = types.EmbedContentConfig(
            output_dimensionality=EMBEDDING_DIMENSIONS,
            http_options=types.HttpOptions(timeout=TIMEOUT_MS),
        )
        attempt = retries = 0
        waited_ns = 0
        while True:
            attempt += 1
            waited_ns += self._throttle.wait(self.model)
            at = self._clock.now()
            started = self._clock.monotonic_ns()
            try:
                response = self._embed(model=self.model, contents=text, config=config)
            except Exception as exc:
                failure = classify(exc)
                if failure is None:
                    raise
                self._note(
                    calls,
                    self._record(
                        key,
                        attempt=attempt,
                        at=at,
                        waited_ns=waited_ns,
                        duration_ns=self._clock.monotonic_ns() - started,
                        cache=use,
                        failure=failure,
                    ),
                )
                self._raise_if_final(failure, exc, calls)
                if retries == MAX_RETRIES:
                    raise LlmUnavailable(f"{PURPOSE}: {self.model} tidak tersedia", calls) from exc
                retries += 1
                delay = self._delay_ms(failure, retries)
                if delay is None:
                    raise LlmUnavailable(f"{PURPOSE}: {self.model} tidak tersedia", calls) from exc
                self._clock.sleep_ns(delay * NS_PER_MS)
                waited_ns = delay * NS_PER_MS
                continue
            return self._accepted(response, key, attempt, at, waited_ns, started, use, calls)

    def _raise_if_final(self, failure: Failure, exc: Exception, calls: list[LlmCallRecord]) -> None:
        if failure.kind is ErrorKind.CONFIG_ERROR:
            detail = exc.message if isinstance(exc, errors.APIError) else ""
            raise LlmConfigError(
                f"{self.model}: permintaan ditolak (HTTP {failure.http_status}) {detail}", calls
            ) from exc
        if failure.kind is ErrorKind.QUOTA_PER_DAY:
            raise LlmUnavailable(f"{PURPOSE}: kuota harian {self.model} habis", calls) from exc

    def _delay_ms(self, failure: Failure, retry: int) -> int | None:
        if failure.retry_after_ms is None:
            return backoff_ms(retry, self._rng)
        if failure.retry_after_ms > MAX_WAIT_MS:
            return None
        return failure.retry_after_ms + jitter_ms(self._rng)

    def _accepted(
        self,
        response: types.EmbedContentResponse,
        key: str,
        attempt: int,
        at: datetime,
        waited_ns: int,
        started: int,
        use: CacheUse,
        calls: list[LlmCallRecord],
    ) -> tuple[float, ...]:
        embedding = response.embeddings[0] if response.embeddings else None
        values = list(embedding.values or []) if embedding else []
        statistics = embedding.statistics if embedding else None
        tokens = int(statistics.token_count) if statistics and statistics.token_count else None
        self._note(
            calls,
            self._record(
                key,
                attempt=attempt,
                at=at,
                waited_ns=waited_ns,
                duration_ns=self._clock.monotonic_ns() - started,
                cache=use,
                tokens=tokens,
            ),
        )
        if len(values) != EMBEDDING_DIMENSIONS:
            raise LlmInvalidOutput(
                f"{self.model}: embedding {len(values)} dimensi, seharusnya {EMBEDDING_DIMENSIONS}",
                calls,
            )
        if self._cache is not None:
            self._cache.store(
                CacheEntry(
                    key=key,
                    model=self.model,
                    purpose=PURPOSE,
                    prompt_version=VERSION,
                    created=at,
                    output=_Stored(values=values).model_dump_json(),
                )
            )
        return tuple(values)


def gemini_embedder(settings: Settings, *, cache: LlmCache | None = None) -> GeminiEmbedder:
    return GeminiEmbedder(
        SdkEmbed(sdk_client(settings)),
        model=settings.gemini_model_embedding,
        rpm=settings.gemini_rpm_embedding,
        cache=cache,
        prices=load_prices(),
    )
