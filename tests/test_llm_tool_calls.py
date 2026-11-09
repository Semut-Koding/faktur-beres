"""M4a.2: the fallback role, per-request timeouts and multi-turn function calling (no network)."""

from collections.abc import Sequence
from pathlib import Path

import pytest
from google.genai import types
from pydantic import BaseModel

from faktur_beres.llm.cache import LlmCache
from faktur_beres.llm.contracts import (
    BlobPart,
    CacheUse,
    LlmInvalidOutput,
    LlmRequest,
    LlmUnavailable,
    ModelRole,
    TextPart,
    ToolBudget,
    ToolEventStatus,
    ToolReply,
    ToolRunStatus,
    ToolSpec,
)
from faktur_beres.llm.gemini import GeminiClient, Timeouts
from llm_fakes import FakeClock, Outcome, ScriptedGenerate, api_error, quota_per_day, reply

PRIMARY, STRONG, RESERVE = "model-a", "model-b", "model-c"
SIGNATURE = b"\x00\xfftanda-tangan"


class Answer(BaseModel):
    status: str


class FindArgs(BaseModel):
    number: str


class SubmitArgs(BaseModel):
    number: str | None


TOOLS = (
    ToolSpec("find", "Cari", FindArgs),
    ToolSpec("submit", "Kirim", SubmitArgs),
)
BUDGET = ToolBudget(model_calls=6, tool_runs=12)


class Session:
    def __init__(self) -> None:
        self.runs: list[tuple[str, BaseModel]] = []
        self.submitted: str | None = None

    def call(self, name: str, args: BaseModel) -> ToolReply:
        self.runs.append((name, args))
        if name == "submit":
            assert isinstance(args, SubmitArgs)
            self.submitted = args.number
            return ToolReply({"ok": True}, terminal=True)
        return ToolReply({"found": True})


def client(
    script: dict[str, list[Outcome]],
    *,
    cache: LlmCache | None = None,
    strong: str = STRONG,
) -> tuple[GeminiClient, ScriptedGenerate, FakeClock]:
    clock = FakeClock()
    generate = ScriptedGenerate(script, clock=clock)
    llm = GeminiClient(
        generate,
        primary=PRIMARY,
        strong=strong,
        fallback=RESERVE,
        rpm={PRIMARY: 600, strong: 600, RESERVE: 600},
        clock=clock,
        cache=cache,
        timeouts=Timeouts(document_ms=120_000, fallback_document_ms=180_000, text_ms=60_000),
    )
    return llm, generate, clock


def request(*, role: ModelRole = ModelRole.PRIMARY, allow_fallback: bool = True) -> LlmRequest:
    return LlmRequest(
        purpose="uji",
        role=role,
        prompt_version="uji-v1",
        system_instruction="Sistem",
        parts=(TextPart(text="Pertanyaan"),),
        allow_fallback=allow_fallback,
    )


def with_document(role: ModelRole = ModelRole.PRIMARY) -> LlmRequest:
    return request(role=role).model_copy(
        update={"parts": (BlobPart(data=b"%PDF-", mime_type="application/pdf"),)}
    )


def turn(*calls: tuple[str, dict[str, object]], signature: bytes | None = None):
    parts = [
        types.Part(
            function_call=types.FunctionCall(name=name, args=args, id=f"id-{i}"),
            thought_signature=signature if i == 0 else None,
        )
        for i, (name, args) in enumerate(calls)
    ]
    response = reply(None)
    response.candidates[0].content = types.Content(role="model", parts=parts)  # type: ignore[index]
    return response


OK = '{"status": "ok"}'
DOWN = api_error(503, "UNAVAILABLE")


def history_of(generate: ScriptedGenerate, index: int) -> Sequence[types.Content]:
    contents = generate.sent[index].contents
    assert isinstance(contents, list)
    return contents


# --- fallback role --------------------------------------------------------------------------


def test_chains_put_the_fallback_model_last_and_each_model_once() -> None:
    llm, _, _ = client({})
    assert llm.chain(ModelRole.PRIMARY) == (PRIMARY, STRONG, RESERVE)
    assert llm.chain(ModelRole.STRONG) == (STRONG, PRIMARY, RESERVE)
    assert llm.chain(ModelRole.FALLBACK) == (RESERVE,)
    assert llm.chain(ModelRole.PRIMARY, allow_fallback=False) == (PRIMARY, STRONG)
    same, _, _ = client({}, strong=PRIMARY)
    assert same.chain(ModelRole.STRONG) == (PRIMARY, RESERVE)


def test_the_fallback_model_answers_last_and_is_marked() -> None:
    llm, generate, _ = client(
        {PRIMARY: [quota_per_day()], STRONG: [quota_per_day()], RESERVE: [reply(OK)]}
    )
    result = llm.generate(request(), Answer)
    assert result.model == RESERVE and result.calls[-1].fallback is True
    assert generate.calls_to(RESERVE) == 1


@pytest.mark.parametrize(
    "error",
    [DOWN, api_error(408, "REQUEST_TIMEOUT")],
)
def test_the_fallback_model_gets_one_retry_for_server_errors(error: Exception) -> None:
    llm, generate, _ = client(
        {
            PRIMARY: [quota_per_day()],
            STRONG: [quota_per_day()],
            RESERVE: [error, error, reply(OK)],
        }
    )
    with pytest.raises(LlmUnavailable):
        llm.generate(request(), Answer)
    assert generate.calls_to(RESERVE) == 2


def test_a_call_can_refuse_the_fallback_model() -> None:
    llm, generate, _ = client({PRIMARY: [quota_per_day()], STRONG: [quota_per_day()]})
    with pytest.raises(LlmUnavailable):
        llm.generate(request(allow_fallback=False), Answer)
    assert generate.calls_to(RESERVE) == 0


# --- timeouts -------------------------------------------------------------------------------


def _timeout(generate: ScriptedGenerate, index: int = -1) -> int | None:
    options = generate.sent[index].config.http_options
    assert options is not None
    return options.timeout


def test_a_document_on_the_primary_model_waits_the_document_timeout() -> None:
    llm, generate, _ = client({PRIMARY: [reply(OK)]})
    llm.generate(with_document(), Answer)
    assert _timeout(generate) == 120_000


def test_a_document_on_the_fallback_model_waits_longer() -> None:
    llm, generate, _ = client(
        {PRIMARY: [quota_per_day()], STRONG: [quota_per_day()], RESERVE: [reply(OK)]}
    )
    llm.generate(with_document(), Answer)
    assert _timeout(generate) == 180_000


def test_a_request_without_a_document_waits_the_text_timeout_on_every_model() -> None:
    llm, generate, _ = client(
        {PRIMARY: [quota_per_day()], STRONG: [quota_per_day()], RESERVE: [reply(OK)]}
    )
    llm.generate(request(), Answer)
    assert [_timeout(generate, i) for i in range(3)] == [60_000, 60_000, 60_000]


# --- function calling -----------------------------------------------------------------------


def test_the_conversation_ends_with_the_terminal_tool() -> None:
    llm, generate, _ = client(
        {PRIMARY: [turn(("find", {"number": "P1"})), turn(("submit", {"number": "P1"}))]}
    )
    result = llm.run_tools(request(), TOOLS, Session, BUDGET)
    assert result.status is ToolRunStatus.FINISHED
    assert result.session.submitted == "P1"
    assert (result.model_calls, result.tool_runs) == (2, 2)
    assert result.model == PRIMARY
    config = generate.sent[0].config
    assert config.automatic_function_calling is not None
    assert config.automatic_function_calling.disable is True
    calling = config.tool_config.function_calling_config  # type: ignore[union-attr]
    assert calling is not None and calling.mode == types.FunctionCallingConfigMode.ANY
    assert calling.allowed_function_names == ["find", "submit"]


def test_the_model_turn_goes_back_unchanged_with_its_signature() -> None:
    first = turn(("find", {"number": "P1"}), signature=SIGNATURE)
    llm, generate, _ = client({PRIMARY: [first, turn(("submit", {"number": None}))]})
    llm.run_tools(request(), TOOLS, Session, BUDGET)
    history = history_of(generate, 1)
    assert len(history) == 3
    assert history[1] == first.candidates[0].content  # type: ignore[index]
    assert history[1].parts[0].thought_signature == SIGNATURE  # type: ignore[index]


def test_parallel_calls_are_answered_together_in_order() -> None:
    llm, generate, _ = client(
        {
            PRIMARY: [
                turn(("find", {"number": "P1"}), ("find", {"number": "P2"})),
                turn(("submit", {"number": None})),
            ]
        }
    )
    llm.run_tools(request(), TOOLS, Session, BUDGET)
    answer = history_of(generate, 1)[2]
    responses = [p.function_response for p in answer.parts or []]
    assert [r.id for r in responses if r] == ["id-0", "id-1"]
    assert answer.role == "user"


def test_an_unknown_function_is_not_run_and_counts_in_the_budget() -> None:
    llm, generate, _ = client(
        {PRIMARY: [turn(("hapus", {"number": "P1"})), turn(("submit", {"number": None}))]}
    )
    result = llm.run_tools(request(), TOOLS, Session, BUDGET)
    assert result.events[0].status is ToolEventStatus.UNKNOWN_FUNCTION
    assert [name for name, _ in result.session.runs] == ["submit"]
    assert result.model_calls == 2
    error = history_of(generate, 1)[2].parts[0].function_response  # type: ignore[index]
    assert error is not None and "error" in (error.response or {})


def test_invalid_arguments_are_not_run_and_their_values_do_not_echo() -> None:
    llm, generate, _ = client(
        {PRIMARY: [turn(("find", {"number": ["rahasia"]})), turn(("submit", {"number": None}))]}
    )
    result = llm.run_tools(request(), TOOLS, Session, BUDGET)
    assert result.events[0].status is ToolEventStatus.INVALID_ARGS
    assert [name for name, _ in result.session.runs] == ["submit"]
    error = history_of(generate, 1)[2].parts[0].function_response  # type: ignore[index]
    assert error is not None and "rahasia" not in str(error.response)


def test_a_model_switch_restarts_the_conversation_with_a_new_session() -> None:
    signed = turn(("find", {"number": "P1"}), signature=SIGNATURE)
    llm, generate, _ = client(
        {
            PRIMARY: [signed, quota_per_day()],
            STRONG: [turn(("submit", {"number": "P9"}))],
        }
    )
    result = llm.run_tools(request(), TOOLS, Session, BUDGET)
    assert result.model == STRONG and result.session.submitted == "P9"
    assert [name for name, _ in result.session.runs] == ["submit"]
    assert result.model_calls == 3
    restart = history_of(generate, 2)
    assert len(restart) == 1 and "thought_signature" not in restart[0].model_dump_json(
        exclude_none=True
    )


def test_the_model_call_budget_stops_the_conversation() -> None:
    script: dict[str, list[Outcome]] = {PRIMARY: [turn(("find", {"number": "P"}))] * 5}
    llm, generate, _ = client(script)
    result = llm.run_tools(request(), TOOLS, Session, ToolBudget(model_calls=2, tool_runs=12))
    assert result.status is ToolRunStatus.BUDGET_EXHAUSTED
    assert generate.calls_to(PRIMARY) == 2


def test_the_tool_budget_stops_the_conversation() -> None:
    llm, _, _ = client({PRIMARY: [turn(("find", {"number": "A"}), ("find", {"number": "B"}))]})
    result = llm.run_tools(request(), TOOLS, Session, ToolBudget(model_calls=6, tool_runs=1))
    assert result.status is ToolRunStatus.BUDGET_EXHAUSTED
    assert result.tool_runs == 1


def test_a_turn_without_a_function_call_is_invalid_output() -> None:
    llm, _, _ = client({PRIMARY: [reply("teks"), reply("teks")]})
    with pytest.raises(LlmInvalidOutput):
        llm.run_tools(request(), TOOLS, Session, BUDGET)


def test_no_available_model_raises_unavailable() -> None:
    llm, _, _ = client(
        {PRIMARY: [quota_per_day()], STRONG: [quota_per_day()], RESERVE: [quota_per_day()]}
    )
    with pytest.raises(LlmUnavailable):
        llm.run_tools(request(), TOOLS, Session, BUDGET)


def test_the_cache_replays_turns_with_their_signature(tmp_path: Path) -> None:
    script: dict[str, list[Outcome]] = {
        PRIMARY: [
            turn(("find", {"number": "P1"}), signature=SIGNATURE),
            turn(("submit", {"number": "P1"})),
        ]
    }
    cache = LlmCache(tmp_path)
    first, _, _ = client(script, cache=cache)
    first.run_tools(request(), TOOLS, Session, BUDGET)
    stored = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))
    assert "Pertanyaan" not in stored and "Sistem" not in stored

    second, generate, _ = client({}, cache=cache)
    result = second.run_tools(request(), TOOLS, Session, BUDGET)
    assert result.session.submitted == "P1"
    assert generate.sent == []
    assert {c.cache for c in result.calls} == {CacheUse.HIT}


def test_the_cache_key_depends_on_the_declarations(tmp_path: Path) -> None:
    cache = LlmCache(tmp_path)
    script: dict[str, list[Outcome]] = {PRIMARY: [turn(("submit", {"number": None}))]}
    first, _, _ = client(script, cache=cache)
    first.run_tools(request(), TOOLS, Session, BUDGET)
    other = (ToolSpec("find", "Deskripsi lain", FindArgs), TOOLS[1])
    second, generate, _ = client({PRIMARY: [turn(("submit", {"number": None}))]}, cache=cache)
    second.run_tools(request(), other, Session, BUDGET)
    assert generate.calls_to(PRIMARY) == 1
