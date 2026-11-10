"""The embedding client without a network."""

from pathlib import Path

import pytest
from google.genai import types

from faktur_beres.db.models import PolicyChunkRow
from faktur_beres.llm.cache import LlmCache
from faktur_beres.llm.contracts import CacheUse, LlmConfigError, LlmInvalidOutput, LlmUnavailable
from faktur_beres.llm.embedding import GeminiEmbedder, document_input, query_input
from faktur_beres.llm.embedding_contract import EMBEDDING_DIMENSIONS
from llm_fakes import FakeClock, api_error, quota_per_day, quota_per_minute

VECTOR = [0.5] * EMBEDDING_DIMENSIONS


def reply(values: list[float] = VECTOR) -> types.EmbedContentResponse:
    return types.EmbedContentResponse(embeddings=[types.ContentEmbedding(values=values)])


class Script:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.sent: list[tuple[str, str, types.EmbedContentConfig]] = []

    def __call__(
        self, *, model: str, contents: str, config: types.EmbedContentConfig
    ) -> types.EmbedContentResponse:
        self.sent.append((model, contents, config))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, types.EmbedContentResponse)
        return outcome


def embedder(script: Script, cache: LlmCache | None = None) -> GeminiEmbedder:
    return GeminiEmbedder(script, model="e", rpm=600, clock=FakeClock(), cache=cache)


def test_the_dimension_is_the_size_of_the_column() -> None:
    assert PolicyChunkRow.__table__.c.embedding.type.dim == EMBEDDING_DIMENSIONS == 768


def test_input_formats_follow_the_guide_for_this_model() -> None:
    assert document_input("Judul", "Isi") == "title: Judul | text: Isi"
    assert query_input("rekening") == "task: search result | query: rekening"


def test_request_asks_for_the_dimension_and_no_task_type() -> None:
    script = Script(reply())
    result = embedder(script).embed(["teks"])
    assert result.vectors[0] == tuple(VECTOR)
    config = script.sent[0][2]
    assert config.output_dimensionality == 768 and config.task_type is None
    assert result.calls[0].purpose == "embedding"


def test_a_wrong_dimension_is_refused() -> None:
    with pytest.raises(LlmInvalidOutput):
        embedder(Script(reply([0.1, 0.2]))).embed(["teks"])


def test_server_errors_and_minute_quota_are_retried() -> None:
    script = Script(api_error(503, "UNAVAILABLE"), quota_per_minute("1s"), reply())
    result = embedder(script).embed(["teks"])
    assert len(result.calls) == 3 and result.calls[-1].error_kind is None


def test_the_daily_quota_stops_at_once() -> None:
    script = Script(quota_per_day())
    with pytest.raises(LlmUnavailable, match="harian"):
        embedder(script).embed(["teks"])
    assert len(script.sent) == 1


def test_a_refused_request_is_a_configuration_error() -> None:
    with pytest.raises(LlmConfigError):
        embedder(Script(api_error(400, "INVALID_ARGUMENT"))).embed(["teks"])


def test_the_cache_answers_without_a_request_and_keeps_no_text(tmp_path: Path) -> None:
    cache = LlmCache(tmp_path)
    embedder(Script(reply()), cache).embed(["teks rahasia"])
    stored = "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))
    assert "teks rahasia" not in stored
    script = Script()
    result = embedder(script, cache).embed(["teks rahasia"])
    assert script.sent == [] and result.calls[0].cache is CacheUse.HIT
