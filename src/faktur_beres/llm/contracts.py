"""What agent code sends to an LLM and gets back, independent of the provider SDK.

``StructuredLlm`` is the protocol the extraction and the injection reviewer depend on, so their
tests use a fake. ``faktur_beres.llm.gemini.GeminiClient`` is the real implementation.

Document content never shows in a ``repr``: the fields that carry it are excluded, so a traceback or
a log line that formats a request shows the purpose and the MIME types, not the text or the bytes.
``LlmCallRecord`` (BR-23) holds no content at all: no prompt, no document, no output, no key.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from faktur_beres.domain.base import DomainModel

MAX_OUTPUT_TOKENS_CAP = 32768
UNKNOWN_COST: Literal["tidak diketahui"] = "tidak diketahui"
KEY_SHOWN = 16  # hex digits of the request key in the audit


class ModelRole(StrEnum):
    """Model per role (M3.2)."""

    PRIMARY = "primary"  # reads documents on the first try and reviews injection (layer 2)
    STRONG = "strong"  # re-reads (BR-04) and is the fallback of the primary model
    # Last in every chain, with a small daily quota. Only smoke and comparison runs ask for it.
    FALLBACK = "fallback"


class ThinkingLevel(StrEnum):
    # "minimal" is left out: gemini-3.8-flash answers it with an error.
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


DocumentMime = Literal["application/pdf", "image/png", "image/jpeg"]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TextPart(_Input):
    kind: Literal["text"] = "text"
    text: str = Field(min_length=1, repr=False)


class BlobPart(_Input):
    """A document sent inline. It carries no file name: names are never sent to the LLM."""

    kind: Literal["blob"] = "blob"
    data: bytes = Field(min_length=1, repr=False)
    mime_type: DocumentMime


Part = Annotated[TextPart | BlobPart, Field(discriminator="kind")]


class LlmRequest(_Input):
    """One structured request; the response schema is passed separately to ``generate``."""

    purpose: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    role: ModelRole
    prompt_version: str = Field(pattern=r"^[a-z][a-z0-9_-]*-v[0-9]+$", max_length=60)
    system_instruction: str = Field(min_length=1, repr=False)
    parts: tuple[Part, ...] = Field(min_length=1)
    thinking_level: ThinkingLevel = ThinkingLevel.LOW
    max_output_tokens: int = Field(default=8192, ge=256, le=MAX_OUTPUT_TOKENS_CAP)
    allow_fallback: bool = True  # False keeps the small quota of the fallback role out of it

    @property
    def has_media(self) -> bool:
        return any(isinstance(part, BlobPart) for part in self.parts)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def request_key(
    model: str,
    request: LlmRequest,
    schema: type[BaseModel],
    generation: Mapping[str, object],
) -> str:
    """SHA-256 of the canonical JSON of everything that decides the answer (cache key, audit).

    Model, purpose, prompt version, schema hash, system instruction hash, the hash of each input,
    and the generation settings. Content enters only as hashes.
    """
    inputs = [
        {"kind": "text", "sha256": sha256_hex(part.text)}
        if isinstance(part, TextPart)
        else {"kind": "blob", "mime_type": part.mime_type, "sha256": sha256_hex(part.data)}
        for part in request.parts
    ]
    material = {
        "model": model,
        "purpose": request.purpose,
        "prompt_version": request.prompt_version,
        "schema_sha256": sha256_hex(canonical_json(schema.model_json_schema())),
        "system_instruction_sha256": sha256_hex(request.system_instruction),
        "inputs": inputs,
        "generation": dict(generation),
    }
    return sha256_hex(canonical_json(material))


@dataclass(frozen=True)
class ToolSpec:
    """A function the model may call; its parameters are the fields of ``schema``."""

    name: str
    description: str
    schema: type[BaseModel]


@dataclass(frozen=True)
class ToolReply:
    """What a tool returns to the model. ``terminal`` ends the conversation."""

    result: Mapping[str, object]
    terminal: bool = False


class ToolSession(Protocol):
    """Runs validated calls for one conversation; a model switch starts a new session."""

    def call(self, name: str, args: BaseModel) -> ToolReply: ...


class ToolBudget(_Input):
    """Limits over the whole run: every request to a model and every tool execution counts."""

    model_calls: int = Field(ge=1)
    tool_runs: int = Field(ge=0)
    # When only one tool run is left, the model may call nothing but this tool.
    finish_tool: str | None = None


class ToolEventStatus(StrEnum):
    RUN = "run"
    UNKNOWN_FUNCTION = "unknown_function"
    INVALID_ARGS = "invalid_args"


@dataclass(frozen=True)
class ToolEvent:
    """One function call the model made: run, or refused before running."""

    name: str
    status: ToolEventStatus


class ToolRunStatus(StrEnum):
    FINISHED = "finished"  # a tool replied ``terminal``
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class ToolRunResult[S: ToolSession]:
    status: ToolRunStatus
    session: S
    model: str | None
    model_calls: int
    tool_runs: int
    events: tuple[ToolEvent, ...]
    calls: tuple["LlmCallRecord", ...]


def tool_turn_key(
    model: str,
    request: LlmRequest,
    tools: Sequence[ToolSpec],
    history: Sequence[object],
    generation: Mapping[str, object],
) -> str:
    """Cache and audit key of one turn: the declarations and the whole history, as hashes."""
    declarations = [
        {"name": t.name, "description": t.description, "schema": t.schema.model_json_schema()}
        for t in tools
    ]
    material = {
        "model": model,
        "purpose": request.purpose,
        "prompt_version": request.prompt_version,
        "system_instruction_sha256": sha256_hex(request.system_instruction),
        "declarations_sha256": sha256_hex(canonical_json(declarations)),
        "history_sha256": sha256_hex(canonical_json(list(history))),
        "generation": dict(generation),
    }
    return sha256_hex(canonical_json(material))


class CacheUse(StrEnum):
    OFF = "off"  # the caller did not ask for the cache
    MISS = "miss"  # asked for, nothing valid stored: the request went out
    HIT = "hit"  # answered from the cache, no request


class CallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class ErrorKind(StrEnum):
    QUOTA_PER_MINUTE = "quota_per_minute"
    QUOTA_PER_DAY = "quota_per_day"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    CONFIG_ERROR = "config_error"
    INVALID_OUTPUT = "invalid_output"


class LlmCallRecord(DomainModel):
    """One attempt, or one cache hit (BR-23). Logged by the client and returned to the caller."""

    at: datetime
    purpose: str
    role: ModelRole
    model: str
    fallback: bool  # True when the model is not the one the role names
    prompt_version: str
    request_key: str  # the first KEY_SHOWN hex digits
    attempt: int  # 1-based per model within one request; 0 for a cache hit
    cache: CacheUse
    waited_ms: int = 0  # throttle and backoff before this attempt
    duration_ms: int
    status: CallStatus
    error_kind: ErrorKind | None = None
    http_status: int | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    total_tokens: int | None = None
    # USD at the paid-tier price; None when nothing was billed (cache hit, error without usage).
    cost_usd: Decimal | Literal["tidak diketahui"] | None = None


@dataclass(frozen=True)
class LlmResult[T: BaseModel]:
    value: T
    model: str
    calls: tuple[LlmCallRecord, ...]

    @property
    def from_cache(self) -> bool:
        return bool(self.calls) and self.calls[-1].cache == CacheUse.HIT


class LlmError(Exception):
    """Base of the client errors; ``calls`` holds the audit records of every attempt."""

    kind = "llm_error"

    def __init__(self, message: str, calls: Sequence[LlmCallRecord] = ()) -> None:
        super().__init__(message)
        self.calls = tuple(calls)


class LlmUnavailable(LlmError):
    """Neither the model of the role nor its fallback could answer (quota, server, network)."""

    kind = "llm_unavailable"


class LlmInvalidOutput(LlmError):
    """The model answered twice, but not with a complete reply that matches the schema."""

    kind = "invalid_output"


class LlmConfigError(LlmError):
    """The request was refused (400, 401, 403, 404) or the key is missing: no retry helps."""

    kind = "config_error"


class StructuredLlm(Protocol):
    def generate[T: BaseModel](self, request: LlmRequest, schema: type[T]) -> LlmResult[T]:
        """Return a value of ``schema`` or raise an ``LlmError``."""
        ...


class ToolCallingLlm(Protocol):
    def run_tools[S: ToolSession](
        self,
        request: LlmRequest,
        tools: Sequence[ToolSpec],
        new_session: Callable[[], S],
        budget: ToolBudget,
    ) -> ToolRunResult[S]:
        """Run a function-calling conversation until a tool ends it or the budget is spent."""
        ...
