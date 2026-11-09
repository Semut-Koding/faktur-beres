"""Tool ``injection_review`` (SPEC §5.2, BR-15 layer 2): an LLM reviewer that only says yes or no.

Input: every free-text fragment of one document, merged as ``analyse()`` merges them
(``document_fragments``), with long digit runs masked (``mask_text``). Nothing else: no ERP data, no
label, no other extracted value. Each fragment is wrapped in delimiters that carry a random nonce,
new for every call; code makes sure the nonce does not occur in the text.

Limits: 40,000 characters, split into at most four calls: every call is filled up to 10,000
characters, cutting a fragment at the call boundary when needed. Above that the status is
``too_long`` and no call is made.

Output of the model: ``{"injection": bool, "quotes": [{"fragment": int, "text": str}]}``, at most
five quotes; a yes needs at least one. Code keeps a quote only when, after ``clean_text``, it has at
least 15 characters and occurs in the fragment it names (ignoring case). A yes without such a quote
is asked once more; a second failure is ``invalid``. The primary model answers; the strong model is
its fallback. ``LlmUnavailable`` becomes ``unavailable``; ``LlmConfigError`` reaches the caller.
"""

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from faktur_beres.domain.invoice import TextFragment
from faktur_beres.domain.rules.injection import (
    InjectionReview,
    ReviewQuote,
    ReviewStatus,
    clean_text,
)
from faktur_beres.domain.rules.safety import mask_text
from faktur_beres.llm.contracts import (
    LlmCallRecord,
    LlmInvalidOutput,
    LlmRequest,
    LlmUnavailable,
    ModelRole,
    StructuredLlm,
    TextPart,
    ThinkingLevel,
)
from faktur_beres.llm.prompts import Prompt, load_prompt

PROMPT_PACKAGE = "faktur_beres.tools"
PROMPT_VERSION = "injection-review-v1"
MAX_CHARS = 40_000
MAX_CALLS = 4
CHARS_PER_CALL = MAX_CHARS // MAX_CALLS
MIN_QUOTE = 15
MAX_QUOTES = 5
MAX_OUTPUT_TOKENS = 2048


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True)

    fragment: int = Field(description="Nomor fragmen tempat kutipan diambil")
    text: str = Field(max_length=1000, description="Potongan teks yang disalin persis")


class ReviewAnswer(BaseModel):
    """The JSON the reviewer returns."""

    model_config = ConfigDict(frozen=True)

    injection: bool = Field(description="true bila ada teks yang memberi perintah kepada mesin")
    quotes: list[_Answer] = Field(max_length=MAX_QUOTES, description="Kutipan, kosong bila false")


@dataclass(frozen=True)
class _Piece:
    number: int  # the fragment number the model sees (1-based, over the whole document)
    fragment: TextFragment
    text: str  # masked


@dataclass(frozen=True)
class ReviewOutcome:
    review: InjectionReview
    calls: tuple[LlmCallRecord, ...]


def _masked(fragments: Sequence[TextFragment]) -> list[tuple[int, TextFragment, str]]:
    return [
        (number, fragment, mask_text(fragment.text))
        for number, fragment in enumerate(fragments, start=1)
    ]


def _batches(masked: Sequence[tuple[int, TextFragment, str]]) -> list[list[_Piece]]:
    """Fill every call up to ``CHARS_PER_CALL``; a fragment may be cut at a call boundary.

    Filling each call keeps text of at most ``MAX_CHARS`` within ``MAX_CALLS`` calls whatever the
    fragment sizes. A cut fragment keeps its number in both calls, and a quote is checked against
    the part of the fragment its call carried.
    """
    batches: list[list[_Piece]] = [[]]
    room = CHARS_PER_CALL
    for number, fragment, text in masked:
        start = 0
        while start < len(text):
            if room == 0:
                batches.append([])
                room = CHARS_PER_CALL
            part = text[start : start + room]
            batches[-1].append(_Piece(number, fragment, part))
            start += len(part)
            room -= len(part)
    return [batch for batch in batches if batch]


def _fresh_nonce(texts: Sequence[str], make: Callable[[], str]) -> str:
    for _ in range(100):
        nonce = make()
        if all(nonce not in text for text in texts):
            return nonce
    raise RuntimeError("tidak menemukan nonce yang tidak ada di teks")  # pragma: no cover


def _request(prompt: Prompt, batch: Sequence[_Piece], nonce: str) -> LlmRequest:
    blocks = [prompt["request"].replace("{nonce}", nonce)]
    for piece in batch:
        where = f" di={piece.fragment.where}" if piece.fragment.where else ""
        blocks.append(
            f"<<<FRAGMEN {nonce} {piece.number} sumber={piece.fragment.source.value}{where}>>>\n"
            f"{piece.text}\n<<<AKHIR {nonce} {piece.number}>>>"
        )
    return LlmRequest(
        purpose="injection_review",
        role=ModelRole.PRIMARY,
        prompt_version=prompt.version,
        system_instruction=prompt["system"],
        parts=(TextPart(text="\n\n".join(blocks)),),
        thinking_level=ThinkingLevel.LOW,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def verified_quotes(answer: ReviewAnswer, batch: Sequence[_Piece]) -> list[ReviewQuote]:
    """The quotes that occur in the fragment they name, with at least 15 characters."""
    by_number: dict[int, list[_Piece]] = {}
    for piece in batch:
        by_number.setdefault(piece.number, []).append(piece)
    found: list[ReviewQuote] = []
    for quote in answer.quotes:
        cleaned = clean_text(quote.text).casefold()
        if len(cleaned) < MIN_QUOTE:
            continue
        for piece in by_number.get(quote.fragment, []):
            if cleaned in clean_text(piece.text).casefold():
                found.append(
                    ReviewQuote(
                        source=piece.fragment.source,
                        where=piece.fragment.where,
                        text=clean_text(quote.text),
                    )
                )
                break
    return found


def review_injection(
    fragments: Sequence[TextFragment],
    *,
    llm: StructuredLlm,
    prompt: Prompt | None = None,
    make_nonce: Callable[[], str] = lambda: secrets.token_hex(8),
) -> ReviewOutcome:
    """Ask the reviewer about every fragment; never raises for an unavailable or bad answer."""
    masked = _masked(fragments)
    if sum(len(text) for _, _, text in masked) > MAX_CHARS:
        return ReviewOutcome(InjectionReview(status=ReviewStatus.TOO_LONG), ())
    batches = _batches(masked)
    if not batches:
        return ReviewOutcome(InjectionReview(status=ReviewStatus.OK, injection=False), ())
    prompt = prompt or load_prompt(PROMPT_PACKAGE, PROMPT_VERSION)
    calls: list[LlmCallRecord] = []
    quotes: list[ReviewQuote] = []
    statuses: list[ReviewStatus] = []
    for batch in batches:
        status = ReviewStatus.INVALID
        for _ in range(2):  # a yes without a verified quote is asked once more
            nonce = _fresh_nonce([piece.text for piece in batch], make_nonce)
            try:
                result = llm.generate(_request(prompt, batch, nonce), ReviewAnswer)
            except LlmUnavailable as exc:
                calls += exc.calls
                status = ReviewStatus.UNAVAILABLE
                break
            except LlmInvalidOutput as exc:
                calls += exc.calls
                status = ReviewStatus.INVALID
                break
            calls += result.calls
            if not result.value.injection:
                status = ReviewStatus.OK
                break
            found = verified_quotes(result.value, batch)
            if found:
                quotes += found
                status = ReviewStatus.OK
                break
        statuses.append(status)
    if quotes:
        review = InjectionReview(status=ReviewStatus.OK, injection=True, quotes=tuple(quotes))
    elif ReviewStatus.UNAVAILABLE in statuses:
        review = InjectionReview(status=ReviewStatus.UNAVAILABLE)
    elif ReviewStatus.INVALID in statuses:
        review = InjectionReview(status=ReviewStatus.INVALID)
    else:
        review = InjectionReview(status=ReviewStatus.OK, injection=False)
    return ReviewOutcome(review=review, calls=tuple(calls))
