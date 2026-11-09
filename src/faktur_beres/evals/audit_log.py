"""The LLM audit of the evaluation tools, kept on disk, and the quota count per window (M3.7).

``GeminiClient`` logs one ``llm_call`` record per attempt on the ``faktur_beres.llm`` logger
(BR-23). The evaluation tools append those records as JSON lines to ``eval/hasil/audit-llm.jsonl``
(local, not committed), so failed attempts are kept too and the use of each model can be counted.
The handler is attached only while a tool really calls the LLM, and removed afterwards, so tests
that run a tool's ``main`` never write to the real file.
The daily quota of the Gemini API resets at midnight Pacific time, 15.00 WIB: ``python -m
faktur_beres.evals.audit_log`` prints the requests per model since the last reset.
"""

import json
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from faktur_beres.config import REPO_ROOT
from faktur_beres.llm.gemini import LOGGER

AUDIT_PATH = REPO_ROOT / "eval" / "hasil" / "audit-llm.jsonl"
RESET_UTC = time(8, 0)  # midnight Pacific (UTC−8) = 15.00 WIB


class _JsonLines(logging.Handler):
    def __init__(self, path: Path, run: str) -> None:
        super().__init__(level=logging.INFO)
        self.path = path
        self.run = run

    def emit(self, record: logging.LogRecord) -> None:
        call = getattr(record, "llm_call", None)
        if not isinstance(call, dict):
            return
        line = json.dumps({"run": self.run, **call}, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def attach(run: str, path: Path = AUDIT_PATH) -> logging.Handler:
    """Append every ``llm_call`` record of this process to ``path``."""
    handler = _JsonLines(path, run)
    LOGGER.addHandler(handler)
    if LOGGER.getEffectiveLevel() > logging.INFO:
        LOGGER.setLevel(logging.INFO)
    return handler


def detach(handler: logging.Handler | None) -> None:
    if handler is not None:
        LOGGER.removeHandler(handler)
        handler.close()


def last_reset(now: datetime) -> datetime:
    today = datetime.combine(now.astimezone(UTC).date(), RESET_UTC, tzinfo=UTC)
    return today if now >= today else today - timedelta(days=1)


def requests_since(since: datetime, path: Path = AUDIT_PATH) -> Counter[str]:
    """Requests per model since ``since`` (cache hits are not requests)."""
    counts: Counter[str] = Counter()
    if not path.is_file():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        call = json.loads(line)
        if call.get("cache") == "hit":
            continue
        if datetime.fromisoformat(call["at"]) >= since:
            counts[call["model"]] += 1
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    now = datetime.now(UTC)
    since = last_reset(now)
    counts = requests_since(since)
    print(f"permintaan sejak reset {since:%Y-%m-%d %H:%M} UTC (15.00 WIB):")
    for model, count in sorted(counts.items()):
        print(f"  {model}: {count}")
    if not counts:
        print("  belum ada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
