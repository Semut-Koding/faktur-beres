"""``npm run eval:injeksi``: the layers of BR-15 measured on a phrase set (M3.5).

Each phrase is judged as a document with one fragment. Layer 1 runs the patterns. Layer 2, the LLM
reviewer, runs on every phrase unless ``--lapis1`` is given, also on the phrases a strong pattern
caught, so that it is measured on its own. The final decision follows the BR-15 combination table.

Output: a table on the terminal and JSON in ``eval/hasil/``. Per phrase: layer-1 tier and patterns,
layer-2 status, answer and quotes, and the final decision. Metrics: attack recall and benign false
positives for the strong patterns, for layer 1 as a whole, for layer 2 alone, and for the final
decision; plus the number of layer-2 answers that were unavailable, invalid or too long.

``--berkas`` may point outside the repository (the planner's closed set). Nothing of such a set is
written into the repository: its results go next to the set file unless ``--json`` says otherwise,
and they name the set only by its SHA-256. Paths with a ``_privat`` or ``privat`` component are
refused.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from faktur_beres.config import REPO_ROOT
from faktur_beres.domain.invoice import TextSource
from faktur_beres.domain.rules.injection import scan_text
from faktur_beres.evals.paths import PathRefused, checked_path

DEV_SET = REPO_ROOT / "eval" / "injeksi" / "dev.yaml"
RESULT_DIR = REPO_ROOT / "eval" / "hasil"

Label = Literal["wajar", "serangan"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Phrase(_Strict):
    id: str = Field(min_length=1)
    teks: str = Field(min_length=1)
    label: Label
    sumber: TextSource
    catatan: str | None = None


class PhraseSet(_Strict):
    versi: int
    frasa: tuple[Phrase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "PhraseSet":
        ids = [phrase.id for phrase in self.frasa]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"id ganda: {', '.join(duplicates)}")
        return self


class PhraseResult(_Strict):
    id: str
    label: Label
    sumber: TextSource
    layer1_tier: str | None
    layer1_patterns: tuple[str, ...]
    layer2_status: str | None = None
    layer2_answer: bool | None = None
    layer2_quotes: tuple[str, ...] = ()
    from_cache: bool = False
    final: bool
    basis: str


class Rate(_Strict):
    attacks_caught: int
    attacks: int
    benign_flagged: int
    benign: int

    @property
    def recall(self) -> str:
        return f"{self.attacks_caught}/{self.attacks}"

    @property
    def false_positives(self) -> str:
        return f"{self.benign_flagged}/{self.benign}"


class Report(_Strict):
    set_sha256: str
    set_version: int
    mode: Literal["lapis1", "lengkap"]
    phrases: tuple[PhraseResult, ...]
    rates: dict[str, Rate]
    layer2_counts: dict[str, int]


def load_set(path: Path) -> tuple[PhraseSet, str]:
    raw = path.read_bytes()
    return PhraseSet.model_validate(yaml.safe_load(raw)), hashlib.sha256(raw).hexdigest()


def judge_layer1(phrase: Phrase) -> PhraseResult:
    """Layer 1 only; ``--lapis1`` treats every pattern hit as C5 (no reviewer to clear it)."""
    hits = scan_text(phrase.teks)
    names = tuple(pattern.name for pattern, _ in hits)
    tiers = {pattern.tier for pattern, _ in hits}
    tier = "strong" if "strong" in tiers else ("weak" if tiers else None)
    return PhraseResult(
        id=phrase.id,
        label=phrase.label,
        sumber=phrase.sumber,
        layer1_tier=tier,
        layer1_patterns=names,
        final=bool(hits),
        basis="pola_lapis1" if hits else "tanpa_pola",
    )


def rate(results: Sequence[PhraseResult], flagged: str) -> Rate:
    def hit(result: PhraseResult) -> bool:
        if flagged == "pola_kuat":
            return result.layer1_tier == "strong"
        if flagged == "lapis1":
            return bool(result.layer1_patterns)
        if flagged == "lapis2":
            return result.layer2_answer is True
        return result.final

    attacks = [r for r in results if r.label == "serangan"]
    benign = [r for r in results if r.label == "wajar"]
    return Rate(
        attacks_caught=sum(hit(r) for r in attacks),
        attacks=len(attacks),
        benign_flagged=sum(hit(r) for r in benign),
        benign=len(benign),
    )


def build_report(
    phrase_set: PhraseSet, digest: str, results: Sequence[PhraseResult], *, lapis1: bool
) -> Report:
    groups = ["pola_kuat", "lapis1", "akhir"]
    if not lapis1:
        groups.insert(2, "lapis2")
    counts = {
        status: sum(r.layer2_status == status for r in results)
        for status in ("ok", "unavailable", "invalid", "too_long")
    }
    return Report(
        set_sha256=digest,
        set_version=phrase_set.versi,
        mode="lapis1" if lapis1 else "lengkap",
        phrases=tuple(results),
        rates={group: rate(results, group) for group in groups},
        layer2_counts={} if lapis1 else counts,
    )


def print_report(report: Report) -> None:
    for r in report.phrases:
        expected = r.label == "serangan"
        mark = "ok" if r.final == expected else "SALAH"
        layer2 = "-" if r.layer2_status is None else f"{r.layer2_status}/{r.layer2_answer}"
        patterns = ",".join(r.layer1_patterns) or "-"
        print(
            f"{r.id:<8} {r.label:<9} {r.sumber.value:<17} L1 {r.layer1_tier or '-':<13} "
            f"L2 {layer2:<14} akhir {'C5' if r.final else '-':<3} {mark:<5} {patterns}"
        )
    print()
    for group, value in report.rates.items():
        print(
            f"{group:<10} recall serangan {value.recall:<7} "
            f"positif palsu wajar {value.false_positives}"
        )
    if report.layer2_counts:
        print("lapis 2: " + ", ".join(f"{k} {v}" for k, v in report.layer2_counts.items()))


def default_target(set_path: Path, mode: str) -> Path:
    """``eval/hasil/`` for a set in the repository; next to the set file for one outside it."""
    if set_path.is_relative_to(REPO_ROOT):
        return RESULT_DIR / f"injeksi-{set_path.stem}-{mode}.json"
    return set_path.with_name(f"{set_path.stem}-hasil-{mode}.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="npm run eval:injeksi", description=__doc__)
    parser.add_argument("--berkas", type=Path, default=DEV_SET, help="set frasa (YAML)")
    parser.add_argument("--lapis1", action="store_true", help="hanya pola, tanpa LLM")
    parser.add_argument("--pakai-cache", action="store_true", help="pakai cache jawaban LLM")
    parser.add_argument("--json", type=Path, default=None, help="berkas hasil JSON")
    args = parser.parse_args(argv)
    try:
        path = checked_path(args.berkas, inside_repo=False)
    except PathRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    phrase_set, digest = load_set(path)
    if not args.lapis1:
        print("lapis 2 belum tersedia; jalankan dengan --lapis1", file=sys.stderr)
        return 2
    results = [judge_layer1(phrase) for phrase in phrase_set.frasa]
    report = build_report(phrase_set, digest, results, lapis1=args.lapis1)
    print_report(report)
    target = args.json or default_target(path, report.mode)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"hasil: {target.relative_to(REPO_ROOT) if target.is_relative_to(REPO_ROOT) else target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
