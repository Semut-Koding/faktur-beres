"""Reading a vendor's text reply (F-06): only the PO numbers it names, copied as written.

The text sits between nonce delimiters as data, with long digit runs masked (BR-24). Code validates
every number afterwards (it exists in the ERP, belongs to the vendor, is not cancelled); the model
decides nothing. ``LlmError`` reaches the caller, and the invoice becomes ``failed`` (BR-27).
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from faktur_beres.domain.rules.safety import mask_text
from faktur_beres.llm.contracts import (
    LlmCallRecord,
    LlmRequest,
    ModelRole,
    StructuredLlm,
    TextPart,
    ThinkingLevel,
)
from faktur_beres.llm.prompts import Prompt, load_prompt

PROMPT_PACKAGE = "faktur_beres.tools"
PROMPT_VERSION = "reply-reading-v1"
MAX_TEXT = 6000
MAX_NUMBERS = 10
MAX_OUTPUT_TOKENS = 1024


class ReplyAnswer(BaseModel):
    """The JSON the reader returns."""

    model_config = ConfigDict(frozen=True)

    po_numbers: list[str] = Field(
        max_length=MAX_NUMBERS, description="Nomor PO yang disebut, persis seperti tertulis"
    )


@dataclass(frozen=True)
class ReplyReading:
    numbers: tuple[str, ...]
    calls: tuple[LlmCallRecord, ...] = ()


def read_reply_numbers(
    text: str,
    *,
    llm: StructuredLlm,
    prompt: Prompt | None = None,
    make_nonce: Callable[[], str] = lambda: secrets.token_hex(8),
) -> ReplyReading:
    prompt = prompt or load_prompt(PROMPT_PACKAGE, PROMPT_VERSION)
    body = mask_text(text[:MAX_TEXT])
    nonce = make_nonce()
    while nonce in body:  # pragma: no cover
        nonce = make_nonce()
    request = LlmRequest(
        purpose="reply_reading",
        role=ModelRole.PRIMARY,
        prompt_version=prompt.version,
        system_instruction=prompt["system"],
        parts=(
            TextPart(
                text=(
                    f"{prompt['request'].replace('{nonce}', nonce)}\n\n"
                    f"<<<BALASAN {nonce}>>>\n{body}\n<<<AKHIR {nonce}>>>"
                )
            ),
        ),
        thinking_level=ThinkingLevel.LOW,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    result = llm.generate(request, ReplyAnswer)
    return ReplyReading(numbers=tuple(result.value.po_numbers), calls=result.calls)
