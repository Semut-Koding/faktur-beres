"""``npm run llm:model``: the Gemini models this key can use, and a smoke test of the configured.

Without options it lists the models from ``client.models.list()`` that support ``generateContent``:
name, version, token limits, and whether the name marks a stable model or a preview, experimental
or alias one (those are never configured, M3.2). ``--uji`` sends one small structured request,
without any document, through ``GeminiClient`` to each configured model
(``GEMINI_MODEL_PRIMARY`` and ``GEMINI_MODEL_STRONG``) and prints OK or the error. Error texts
pass through ``redact`` first, so the API key is never printed.
"""

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from google.genai import errors, types
from pydantic import BaseModel

from faktur_beres.config import Settings, get_settings
from faktur_beres.llm.contracts import LlmError, LlmRequest, ModelRole, TextPart
from faktur_beres.llm.gemini import GeminiClient, SdkGenerate, rpm_by_model, sdk_client

GENERATE_ACTION = "generateContent"
UNSTABLE_PARTS = frozenset({"preview", "exp", "experimental", "latest"})
SMOKE_REQUEST = LlmRequest(
    purpose="smoke_test",
    role=ModelRole.PRIMARY,
    prompt_version="smoke-v1",
    system_instruction="Jawab hanya dengan JSON yang sesuai skema.",
    parts=(TextPart(text='Kembalikan {"status": "ok"}.'),),
    max_output_tokens=1024,
)


class SmokeReply(BaseModel):
    status: str


@dataclass(frozen=True)
class ModelRow:
    name: str
    version: str
    input_limit: int | None
    output_limit: int | None
    stable: bool


def is_stable(name: str) -> bool:
    """False for preview, experimental and alias names (``-preview``, ``-exp``, ``-latest``)."""
    parts = name.removeprefix("models/").lower().split("-")
    return not any(part in UNSTABLE_PARTS for part in parts)


def generate_content_models(models: Iterable[types.Model]) -> list[ModelRow]:
    rows = [
        ModelRow(
            name=m.name.removeprefix("models/"),
            version=m.version or "-",
            input_limit=m.input_token_limit,
            output_limit=m.output_token_limit,
            stable=is_stable(m.name),
        )
        for m in models
        if m.name and GENERATE_ACTION in (m.supported_actions or [])
    ]
    return sorted(rows, key=lambda r: r.name)


def format_row(row: ModelRow) -> str:
    mark = "stabil" if row.stable else "pratinjau/alias"
    limits = f"masukan {row.input_limit or '-'}, keluaran {row.output_limit or '-'}"
    return f"{row.name:<40} versi {row.version:<10} {limits:<36} {mark}"


def redact(text: str, secret: str | None) -> str:
    """Replace every occurrence of the secret with ``***``."""
    return text.replace(secret, "***") if secret else text


def configured_models(settings: Settings) -> dict[str, str]:
    return {"primary": settings.gemini_model_primary, "strong": settings.gemini_model_strong}


def describe_error(exc: Exception, secret: str | None) -> str:
    if isinstance(exc, errors.APIError):
        return redact(f"{exc.code} {exc.status}: {exc.message}", secret)
    if isinstance(exc, LlmError):
        attempts = ", ".join(
            f"{c.error_kind or c.status}{f' {c.http_status}' if c.http_status else ''}"
            for c in exc.calls
        )
        return redact(f"{exc.kind}: {exc} [{attempts}]", secret)
    return redact(f"{type(exc).__name__}: {exc}", secret)


def smoke_test(settings: Settings, model: str) -> None:
    """One small structured request through ``GeminiClient`` (retry included, no fallback)."""
    client = GeminiClient(
        SdkGenerate(sdk_client(settings)),
        primary=model,
        strong=model,
        rpm={model: rpm_by_model(settings).get(model, 5)},
    )
    client.generate(SMOKE_REQUEST, SmokeReply)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="npm run llm:model", description="Model Gemini yang tersedia untuk key ini."
    )
    parser.add_argument(
        "--uji",
        action="store_true",
        help="kirim satu permintaan kecil ke setiap model terkonfigurasi",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    secret = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None
    try:
        client = sdk_client(settings)
    except LlmError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not args.uji:
        try:
            rows = generate_content_models(client.models.list())
        except Exception as exc:
            print(f"GALAT daftar model: {describe_error(exc, secret)}", file=sys.stderr)
            return 1
        for row in rows:
            print(format_row(row))
        print(f"{len(rows)} model mendukung {GENERATE_ACTION}.")
        return 0

    failed = 0
    for role, model in configured_models(settings).items():
        if not is_stable(model):
            print(f"{role:<8} {model}: GALAT nama pratinjau/alias tidak dipakai")
            failed += 1
            continue
        try:
            smoke_test(settings, model)
        except Exception as exc:
            print(f"{role:<8} {model}: GALAT {describe_error(exc, secret)}")
            failed += 1
        else:
            print(f"{role:<8} {model}: OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
