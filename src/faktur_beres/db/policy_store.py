"""The stored policy chunks and the quotes found per reason code."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from faktur_beres.db.models import PolicyChunkRow, PolicyReasonQuote


@dataclass(frozen=True)
class StoredChunk:
    ref: str
    sha256: str
    model: str
    dimensions: int
    chunk_format: int


@dataclass(frozen=True)
class PolicyHit:
    ref: str
    title: str
    quote: str
    score: Decimal


@dataclass(frozen=True)
class ReasonHit:
    ref: str
    score: Decimal


class PolicyStore:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)

    def chunks(self) -> dict[str, StoredChunk]:
        with self._sessions() as session:
            rows = session.scalars(select(PolicyChunkRow)).all()
        return {
            r.ref: StoredChunk(r.ref, r.sha256, r.model, r.dimensions, r.chunk_format) for r in rows
        }

    def upsert(
        self,
        *,
        ref: str,
        document: str,
        title: str,
        text: str,
        sha256: str,
        embedding: Sequence[float],
        model: str,
        dimensions: int,
        chunk_format: int,
    ) -> None:
        values = {
            "ref": ref,
            "document": document,
            "title": title,
            "text": text,
            "sha256": sha256,
            "embedding": list(embedding),
            "model": model,
            "dimensions": dimensions,
            "chunk_format": chunk_format,
        }
        statement = insert(PolicyChunkRow).values(values)
        with self._sessions.begin() as session:
            session.execute(
                statement.on_conflict_do_update(constraint="uq_policy_chunks_ref", set_=values)
            )

    def remove_except(self, refs: Sequence[str]) -> int:
        with self._sessions.begin() as session:
            result = session.execute(
                delete(PolicyChunkRow).where(PolicyChunkRow.ref.not_in(list(refs)))
            )
            return int(result.rowcount)  # type: ignore[attr-defined]

    def search(self, vector: Sequence[float], k: int) -> list[PolicyHit]:
        distance = PolicyChunkRow.embedding.cosine_distance(list(vector))
        query = select(PolicyChunkRow, distance.label("distance")).order_by(distance).limit(k)
        with self._sessions() as session:
            rows = session.execute(query).all()
        return [
            PolicyHit(row.ref, row.title, row.text, Decimal(str(round(1 - dist, 6))))
            for row, dist in rows
        ]

    def replace_reason_hits(
        self, reason: str, query_sha256: str, hits: Sequence[ReasonHit]
    ) -> None:
        with self._sessions.begin() as session:
            session.execute(delete(PolicyReasonQuote).where(PolicyReasonQuote.reason == reason))
            session.add_all(
                PolicyReasonQuote(
                    reason=reason, ref=hit.ref, score=hit.score, query_sha256=query_sha256
                )
                for hit in hits
            )

    def reason_query_hashes(self) -> dict[str, str]:
        with self._sessions() as session:
            rows = session.scalars(select(PolicyReasonQuote)).all()
        return {row.reason: row.query_sha256 for row in rows}

    def reason_quotes(self, reasons: Sequence[str]) -> list[tuple[str, PolicyHit]]:
        """Stored quotes of these reasons, with the chunk's text, best first per reason."""
        query = (
            select(PolicyReasonQuote, PolicyChunkRow)
            .join(PolicyChunkRow, PolicyChunkRow.ref == PolicyReasonQuote.ref)
            .where(PolicyReasonQuote.reason.in_(list(reasons)))
            .order_by(PolicyReasonQuote.reason, PolicyReasonQuote.score.desc())
        )
        with self._sessions() as session:
            rows = session.execute(query).all()
        return [
            (quote.reason, PolicyHit(chunk.ref, chunk.title, chunk.text, quote.score))
            for quote, chunk in rows
        ]
