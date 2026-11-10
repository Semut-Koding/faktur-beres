"""Tool ``policy_rag`` (SPEC §5.2, BR-28): quotes from the policy documents, read only.

A hit is text to quote and never a number to decide with: the rules read ``parameter.yaml``.
Every reason code has a fixed query. Those are embedded and searched once, when the index is built,
and only the references and scores are kept, so ``decide`` needs no API call for them.
Queries are masked (BR-24). Only the two policy documents and the queries are ever embedded.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from faktur_beres.db.policy_store import PolicyHit, PolicyStore, ReasonHit
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.safety import mask_text
from faktur_beres.llm.contracts import LlmCallRecord
from faktur_beres.llm.embedding import Embedder, document_input, query_input
from faktur_beres.llm.embedding_contract import EMBEDDING_DIMENSIONS
from faktur_beres.tools.policy_docs import CHUNK_FORMAT, PolicyChunk, load_chunks

MAX_K = 5
MAX_QUOTE = 600
REASON_HITS = 3
MAX_REASON_QUOTES = 2
#: From the scores of the first real index (ADR-0012): below it the article is not on the subject.
REASON_QUOTE_MIN_SCORE = Decimal("0.60")

REASON_QUERIES: dict[ReasonCode, str] = {
    ReasonCode.PRICE_VARIANCE: "harga satuan faktur lebih tinggi dari harga pesanan pembelian",
    ReasonCode.QUANTITY_VARIANCE: "kuantitas faktur melebihi barang diterima atau biaya di luar PO",
    ReasonCode.TAX_INVALID: "NPWP, NSFP, atau PPN pada faktur pajak salah atau tidak lengkap",
    ReasonCode.PO_UNRESOLVED: "pesanan pembelian tidak jelas atau tidak ditemukan untuk faktur",
    ReasonCode.DUPLICATE_CONFIRMED: "faktur dengan nomor dan total sama sudah tercatat",
    ReasonCode.ARITHMETIC_ERROR: "hitungan pada faktur tidak sesuai dan perlu klarifikasi pemasok",
    ReasonCode.OVER_THRESHOLD: "persetujuan approver untuk total faktur di atas ambang",
    ReasonCode.NEW_VENDOR: "pemasok baru yang belum punya tagihan terposting",
    ReasonCode.BANK_ACCOUNT_MISMATCH: "rekening pada faktur berbeda dari rekening terdaftar",
    ReasonCode.VALIDATION_FAILED: "dokumen faktur tidak terbaca atau gagal diverifikasi",
    ReasonCode.PROMPT_INJECTION: "instruksi tidak wajar di dalam dokumen faktur",
    ReasonCode.DUPLICATE_SUSPECTED: "faktur mirip tetapi tidak identik dengan yang sudah ada",
    ReasonCode.IDENTITY_MISMATCH: "identitas pemasok tidak sesuai dengan data terdaftar",
}


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=300, description="Pertanyaan tentang kebijakan")
    k: int = Field(default=3, ge=1, le=MAX_K, description="Jumlah hasil")


@dataclass(frozen=True)
class SearchOutcome:
    hits: tuple[PolicyHit, ...]
    calls: tuple[LlmCallRecord, ...]


def search_policy(store: PolicyStore, embedder: Embedder, query: str, k: int = 3) -> SearchOutcome:
    embedded = embedder.embed([query_input(mask_text(query))])
    hits = store.search(embedded.vectors[0], min(k, MAX_K))
    shortened = tuple(
        PolicyHit(hit.ref, hit.title, hit.quote[:MAX_QUOTE], hit.score) for hit in hits
    )
    return SearchOutcome(shortened, embedded.calls)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class IndexReport:
    chunks: int
    embedded_chunks: int
    embedded_queries: int
    removed: int
    calls: tuple[LlmCallRecord, ...]


def _stale(chunks: Sequence[PolicyChunk], store: PolicyStore, model: str) -> list[PolicyChunk]:
    stored = store.chunks()
    return [
        c
        for c in chunks
        if (row := stored.get(c.ref)) is None
        or (row.sha256, row.model, row.dimensions, row.chunk_format)
        != (c.sha256, model, EMBEDDING_DIMENSIONS, CHUNK_FORMAT)
    ]


def index_policy(store: PolicyStore, embedder: Embedder) -> IndexReport:
    """Embed what is new or changed, then search the fixed queries when anything moved."""
    chunks = load_chunks()
    stale = _stale(chunks, store, embedder.model)
    calls: list[LlmCallRecord] = []
    if stale:
        done = embedder.embed([document_input(c.title, c.text) for c in stale])
        calls += done.calls
        for chunk, vector in zip(stale, done.vectors, strict=True):
            store.upsert(
                ref=chunk.ref,
                document=chunk.document,
                title=chunk.title,
                text=chunk.text,
                sha256=chunk.sha256,
                embedding=vector,
                model=embedder.model,
                dimensions=EMBEDDING_DIMENSIONS,
                chunk_format=CHUNK_FORMAT,
            )
    removed = store.remove_except([c.ref for c in chunks])
    known = store.reason_query_hashes()
    todo = [
        reason
        for reason, query in REASON_QUERIES.items()
        if stale or removed or known.get(reason.value) != _sha(query)
    ]
    if todo:
        done = embedder.embed([query_input(REASON_QUERIES[reason]) for reason in todo])
        calls += done.calls
        for reason, vector in zip(todo, done.vectors, strict=True):
            hits = store.search(vector, REASON_HITS)
            store.replace_reason_hits(
                reason.value,
                _sha(REASON_QUERIES[reason]),
                [ReasonHit(hit.ref, hit.score) for hit in hits],
            )
    return IndexReport(len(chunks), len(stale), len(todo), removed, tuple(calls))


def index_problem(store: PolicyStore, model: str) -> str | None:
    """Why the index cannot be used, or None when it matches documents, model and dimensions."""
    chunks = load_chunks()
    if not store.chunks():
        return "indeks kebijakan kosong"
    if _stale(chunks, store, model) or set(store.chunks()) != {c.ref for c in chunks}:
        return "indeks kebijakan tidak sesuai dengan dokumen, model, atau dimensi"
    if set(store.reason_query_hashes()) != {r.value for r in REASON_QUERIES}:
        return "kutipan per alasan belum dibuat"
    return None


def quotes_for_reasons(store: PolicyStore, reasons: Sequence[str]) -> list[dict[str, str]]:
    """BR-21: quotes per reason from the stored search, without any API call."""
    if not reasons:
        return []
    per_reason: dict[str, int] = {}
    quotes: list[dict[str, str]] = []
    for reason, hit in store.reason_quotes(reasons):
        if hit.score < REASON_QUOTE_MIN_SCORE or per_reason.get(reason, 0) >= MAX_REASON_QUOTES:
            continue
        per_reason[reason] = per_reason.get(reason, 0) + 1
        quotes.append(
            {
                "alasan": reason,
                "ref": hit.ref,
                "judul": hit.title,
                "kutipan": hit.quote[:MAX_QUOTE],
                "skor": str(hit.score),
            }
        )
    return quotes
