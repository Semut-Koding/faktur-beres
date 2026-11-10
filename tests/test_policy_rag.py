"""The policy index on a temporary database with a fake embedding (mark ``integrasi``)."""

import hashlib
import math
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

import pytest
from sqlalchemy import text

from faktur_beres.config import get_settings
from faktur_beres.db.connection import app_url, make_engine, other_database
from faktur_beres.db.migrate import check, reset, upgrade
from faktur_beres.db.policy_store import PolicyStore
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.llm.embedding import Embedded
from faktur_beres.llm.embedding_contract import EMBEDDING_DIMENSIONS
from faktur_beres.tools.policy_docs import load_chunks
from faktur_beres.tools.policy_rag import (
    MAX_QUOTE,
    REASON_QUERIES,
    index_policy,
    index_problem,
    quotes_for_reasons,
    search_policy,
)

pytestmark = pytest.mark.integrasi


def vector_of(value: str) -> tuple[float, ...]:
    """Words hashed into buckets: texts that share words end up close."""
    buckets = [0.0] * EMBEDDING_DIMENSIONS
    for word in value.casefold().replace(":", " ").replace("|", " ").split():
        buckets[int(hashlib.sha256(word.encode()).hexdigest(), 16) % EMBEDDING_DIMENSIONS] += 1.0
    size = math.sqrt(sum(b * b for b in buckets)) or 1.0
    return tuple(b / size for b in buckets)


@dataclass
class FakeEmbedder:
    model: str = "gemini-embedding-2"
    sent: list[str] = field(default_factory=list)

    def embed(self, texts: Sequence[str]) -> Embedded:
        self.sent += list(texts)
        return Embedded(tuple(vector_of(t) for t in texts), self.model, ())


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    main = app_url(get_settings())
    name = f"fakturberes_test_{uuid.uuid4().hex[:10]}"
    admin = make_engine(main).execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    temporary = other_database(main, name)
    try:
        upgrade(temporary)
        yield temporary
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def store(url: str) -> Iterator[PolicyStore]:
    engine = make_engine(url)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE policy_chunks, policy_reason_quotes"))
    yield PolicyStore(engine)
    engine.dispose()


def test_the_migration_matches_the_models_with_pgvector(url: str) -> None:
    check(url)


def test_indexing_embeds_only_what_is_new(store: PolicyStore) -> None:
    embedder = FakeEmbedder()
    first = index_policy(store, embedder)
    assert first.embedded_chunks == first.chunks == len(load_chunks())
    assert first.embedded_queries == len(REASON_QUERIES)
    assert all(t.startswith(("title: ", "task: search result | query: ")) for t in embedder.sent)
    again = index_policy(store, FakeEmbedder())
    assert (again.embedded_chunks, again.embedded_queries) == (0, 0)
    assert index_problem(store, "gemini-embedding-2") is None


def test_a_changed_chunk_is_embedded_again(store: PolicyStore) -> None:
    index_policy(store, FakeEmbedder())
    chunk = load_chunks()[0]
    store.upsert(
        ref=chunk.ref,
        document=chunk.document,
        title=chunk.title,
        text="lama",
        sha256="lama",
        embedding=vector_of("lama"),
        model="gemini-embedding-2",
        dimensions=EMBEDDING_DIMENSIONS,
        chunk_format=1,
    )
    assert index_problem(store, "gemini-embedding-2") is not None
    report = index_policy(store, FakeEmbedder())
    assert report.embedded_chunks == 1 and report.embedded_queries == len(REASON_QUERIES)


def test_problems_are_named(store: PolicyStore) -> None:
    assert "kosong" in (index_problem(store, "m") or "")
    index_policy(store, FakeEmbedder())
    assert "tidak sesuai" in (index_problem(store, "model-lain") or "")


def test_search_returns_the_closest_article_with_a_short_quote(store: PolicyStore) -> None:
    index_policy(store, FakeEmbedder())
    outcome = search_policy(
        store, FakeEmbedder(), "Perubahan rekening hanya lewat surat resmi pemasok", 3
    )
    assert outcome.hits[0].ref == "SOP-6.2"
    assert all(len(hit.quote) <= MAX_QUOTE for hit in outcome.hits)


def test_query_is_masked_before_it_is_embedded(store: PolicyStore) -> None:
    index_policy(store, FakeEmbedder())
    embedder = FakeEmbedder()
    search_policy(store, embedder, "rekening 5450717927 berbeda", 1)
    assert "5450717927" not in embedder.sent[0]


def test_db_reset_keeps_the_index(url: str, store: PolicyStore) -> None:
    index_policy(store, FakeEmbedder())
    reset(url)
    assert index_problem(store, "gemini-embedding-2") is None


def test_reason_quotes_need_no_embedding_and_respect_the_threshold(store: PolicyStore) -> None:
    index_policy(store, FakeEmbedder())
    quotes = quotes_for_reasons(store, [ReasonCode.BANK_ACCOUNT_MISMATCH.value])
    assert all(q["alasan"] == "bank_account_mismatch" for q in quotes) and len(quotes) <= 2
    assert quotes_for_reasons(store, []) == []
