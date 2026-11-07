"""BR-15, first layer: patterns of instructions to the system inside a document.

Every free text that reached Faktur Beres is scanned: notes, line descriptions, hidden text, other
text fields, the PDF text layer, UBL notes, text read from images, and e-mail subject and body. A
hit is ``prompt_injection`` (C5); the instruction itself is never carried out. The second layer (an
LLM that only answers yes/no with a quote) comes in M4.

The patterns come from a general taxonomy, not from any test document:

1. **Sapaan**: text addressed to an AI, an agent, a bot, or an automated process or system
   ("kepada AI ...", "dear assistant", "system:", "instruksi untuk sistem").
2. **Perintah aksi** on the buyer's side that no supplier asks a person to do: marking an invoice
   paid, posting a bill, sending internal data (vendor lists, bank details) somewhere, changing
   bank-account master data in the ERP.
3. **Abaikan**: telling the reader to ignore instructions, rules, checks, or differences.
4. **Lewati**: asking to skip or go without verification or approval ("tanpa persetujuan",
   "skip the review", "langsung posting", "auto-approve").

What is deliberately *not* a pattern: ordinary payment requests to people ("mohon ditransfer",
"silakan bayar"), statements that the invoice was produced by a system, a price that "sudah
disetujui", "abaikan" about a bill already paid, and a notice that the vendor's account changed.
That last one is a statement to people; BR-12 handles the account itself.

Matching is case-insensitive on NFKC text without zero-width characters. A pattern allows at most
four words between its parts, so that it stays inside one clause.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence

from faktur_beres.domain.base import DomainModel
from faktur_beres.domain.findings import Finding, InjectionEvidence
from faktur_beres.domain.invoice import InvoiceData, TextFragment, TextSource
from faktur_beres.domain.reasons import ReasonCode

RULE = "BR-15"
QUOTE_LIMIT = 240

_GAP = r"(?:\W+\w+){0,4}?\W+"  # up to four words in between
_ADDRESS = (
    r"(?:kepada|untuk|hai|halo|hi|hello|dear|to|attention|perhatian|catatan\s+untuk|"
    r"pesan\s+untuk|note\s+(?:for|to))"
)
_AI = (
    r"(?:sistem\s+(?:ai|a\.i\.|otomatis|cerdas)|ai|a\.i\.|agen(?:\s+ai)?|agent|"
    r"asisten(?:\s+(?:ai|virtual))?|assistant|bot|chatbot|llm|model\s+(?:bahasa|ai)|"
    r"language\s+model|pemroses\s+otomatis|automated\s+(?:system|processor|agent)|automation)"
)


class InjectionPattern(DomainModel):
    name: str
    category: str
    regex: str

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.regex, re.IGNORECASE)


PATTERNS: tuple[InjectionPattern, ...] = (
    InjectionPattern(
        name="sapaan_ke_ai",
        category="sapaan",
        regex=rf"\b{_ADDRESS}\s+(?:the\s+|para\s+|sang\s+)?{_AI}\b",
    ),
    InjectionPattern(
        name="sapaan_ke_sistem",
        category="sapaan",
        regex=(
            rf"\b{_ADDRESS}\s+(?:the\s+)?(?:sistem|system)\s*[:,]"
            r"|\b(?:instruksi|perintah|pesan)\s+(?:untuk\s+|kepada\s+)?(?:sistem|system)\b"
            r"|\bsystem\s+(?:prompt|instruction|message|override)s?\b"
            r"|^\s*(?:system|sistem)\s*:"
        ),
    ),
    InjectionPattern(
        name="tandai_lunas",
        category="perintah_aksi",
        regex=(
            rf"\b(?:tandai|tandakan|mark|ubah\s+status|set\s+status){_GAP}?"
            r"(?:sebagai\s+|as\s+|menjadi\s+|to\s+)?(?:lunas|paid|terbayar|settled)\b"
        ),
    ),
    InjectionPattern(
        name="posting_tagihan",
        category="perintah_aksi",
        regex=(
            rf"\b(?:posting(?:kan)?|post|validasi(?:kan)?|validate){_GAP}?"
            r"(?:tagihan|faktur|invoice|bill)\w*\b"
        ),
    ),
    InjectionPattern(
        name="kirim_data_internal",
        category="perintah_aksi",
        regex=(
            rf"\b(?:kirim(?:kan)?|teruskan|bagikan|ekspor|send|forward|export|share){_GAP}?"
            rf"(?:daftar|data|seluruh|semua|list|all|details?)\b{_GAP}?"
            r"(?:vendor|pemasok|supplier|pelanggan|customer|rekening|bank|akun|account)"
        ),
    ),
    InjectionPattern(
        name="ubah_data_rekening",
        category="perintah_aksi",
        regex=(
            rf"\b(?:ubah|ganti|perbarui|perbaharui|update|change|replace|modify|edit){_GAP}?"
            r"(?:data\s+)?(?:rekening|bank\s+account|account\s+details?|bank\s+details?|"
            rf"payment\s+details?)\b{_GAP}?(?:di|pada|dalam|in|on)\s+(?:the\s+)?"
            r"(?:erp|sistem|system|odoo|database|basis\s+data|data\s+master|master\s+data|records?)\b"
        ),
    ),
    InjectionPattern(
        name="abaikan_aturan",
        category="abaikan",
        regex=(
            rf"\b(?:abaikan|hiraukan|lupakan|acuhkan|ignore|disregard|forget|override){_GAP}?"
            r"(?:instruksi|perintah|aturan|pemeriksaan|pengecekan|verifikasi|validasi|selisih|"
            r"peringatan|kebijakan|batasan|toleransi|instructions?|rules?|checks?|validation|"
            r"verification|warnings?|polic(?:y|ies)|guardrails?|limits?)\b"
        ),
    ),
    InjectionPattern(
        name="tanpa_pemeriksaan",
        category="lewati",
        regex=(
            r"\b(?:tanpa|without|no|tidak\s+perlu|tak\s+perlu|not\s+needed?)\s+"
            r"(?:(?:ada|di|any|the|a|lagi)\s+)?(?:pemeriksaan|pengecekan|verifikasi|validasi|"
            r"persetujuan|approval|review|verification|checking|diperiksa|dicek|disetujui|"
            r"diverifikasi|approved)\b"
        ),
    ),
    InjectionPattern(
        name="lewati_pemeriksaan",
        category="lewati",
        regex=(
            rf"\b(?:lewati|lompati|bypass|skip|circumvent){_GAP}?(?:pemeriksaan|verifikasi|"
            r"validasi|persetujuan|approval|review|verification|checks?|steps?)\b"
        ),
    ),
    InjectionPattern(
        name="langsung_eksekusi",
        category="lewati",
        regex=(
            r"\b(?:langsung|automatically|immediately)\s+(?:di)?(?:setujui|approve[sd]?|"
            r"posting(?:kan)?|post|buat\s+tagihan|lunaskan|tandai)\b"
            r"|\bauto[\s-]?(?:approve|post|pay)\w*\b"
        ),
    ),
)
CATEGORIES = ("sapaan", "perintah_aksi", "abaikan", "lewati")
_COMPILED = tuple((p, p.compiled()) for p in PATTERNS)
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))


def clean_text(text: str) -> str:
    """NFKC, no zero-width characters, single spaces (the text the patterns see)."""
    s = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    return re.sub(r"\s+", " ", s).strip()


def _quote(text: str, start: int, end: int) -> str:
    """The sentence around a match, at most QUOTE_LIMIT characters."""
    left = max(text.rfind(c, 0, start) for c in ".!?\n")
    right_candidates = [i for c in ".!?\n" if (i := text.find(c, end)) != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    sentence = text[left + 1 : right].strip()
    return sentence if len(sentence) <= QUOTE_LIMIT else sentence[: QUOTE_LIMIT - 1] + "…"


def scan_text(text: str) -> list[tuple[InjectionPattern, re.Match[str]]]:
    cleaned = clean_text(text)
    return [(p, m) for p, rx in _COMPILED if (m := rx.search(cleaned))]


def scan_fragments(fragments: Iterable[TextFragment]) -> tuple[Finding, ...]:
    """One ``prompt_injection`` finding per fragment that matches any pattern."""
    findings: list[Finding] = []
    for fragment in fragments:
        hits = scan_text(fragment.text)
        if not hits:
            continue
        cleaned = clean_text(fragment.text)
        first = min(hits, key=lambda h: h[1].start())[1]
        quote = _quote(cleaned, first.start(), first.end())
        names = tuple(p.name for p, _ in hits)
        categories = tuple(c for c in CATEGORIES if any(p.category == c for p, _ in hits))
        where = f" ({fragment.where})" if fragment.where else ""
        findings.append(
            Finding(
                rule=RULE,
                reason=ReasonCode.PROMPT_INJECTION,
                message=f'Instruksi kepada sistem di {fragment.source.value}{where}: "{quote}"',
                evidence=InjectionEvidence(
                    source=fragment.source.value,
                    where=fragment.where,
                    patterns=names,
                    categories=categories,
                    quote=quote,
                ),
            )
        )
    return tuple(findings)


def invoice_fragments(invoice: InvoiceData) -> list[TextFragment]:
    """The free text inside ``InvoiceData`` itself: notes and line descriptions."""
    fragments = [
        TextFragment(source=TextSource.NOTE, text=note, where=f"catatan {i}")
        for i, note in enumerate(invoice.notes, start=1)
    ]
    fragments += [
        TextFragment(source=TextSource.LINE_DESCRIPTION, text=line.description, where=f"baris {i}")
        for i, line in enumerate(invoice.lines, start=1)
    ]
    return fragments


def merge_fragments(*groups: Sequence[TextFragment]) -> tuple[TextFragment, ...]:
    """All fragments once, by text; the first source wins."""
    seen: set[str] = set()
    merged: list[TextFragment] = []
    for group in groups:
        for fragment in group:
            key = clean_text(fragment.text).casefold()
            if key and key not in seen:
                seen.add(key)
                merged.append(fragment)
    return tuple(merged)
