"""``npm run llm:model``: the Gemini models this key can use, and a smoke test of the configured.

Without options it lists the models from ``client.models.list()`` that support ``generateContent``:
name, version, token limits, and whether the name marks a stable model or a preview, experimental
or alias one (those are never configured, M3.2). ``--uji`` sends one small structured request,
without any document, to each configured model (``GEMINI_MODEL_PRIMARY`` and
``GEMINI_MODEL_STRONG``) and prints OK or the error. Error texts pass through ``redact`` first, so
the API key is never printed.
"""

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from faktur_beres.config import Settings, get_settings

API_VERSION = "v1beta"  # written out: SDK 3.x changes the default to v1 (M3.2)
GENERATE_ACTION = "generateContent"
UNSTABLE_PARTS = frozenset({"preview", "exp", "experimental", "latest"})
SMOKE_PROMPT = 'Kembalikan JSON {"status": "ok"}.'
SMOKE_MAX_OUTPUT_TOKENS = 1024


class SmokeReply(BaseModel):
    status: str


class SmokeFailed(Exception):
    """The model answered, but not with a complete reply that matches the schema."""


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


def make_client(settings: Settings) -> genai.Client:
    """A client with an explicit API version, the configured timeout and no SDK retries."""
    if settings.gemini_api_key is None:
        raise SystemExit("GEMINI_API_KEY kosong: isi di .env (jangan ditempel di chat).")
    return genai.Client(
        api_key=settings.gemini_api_key.get_secret_value(),
        http_options=types.HttpOptions(
            api_version=API_VERSION,
            timeout=settings.gemini_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def smoke_test(client: genai.Client, model: str) -> None:
    """One structured request without a document; raises when the reply is not a valid one."""
    response = client.models.generate_content(
        model=model,
        contents=SMOKE_PROMPT,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=SmokeReply.model_json_schema(),
            max_output_tokens=SMOKE_MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
        ),
    )
    candidate = response.candidates[0] if response.candidates else None
    finish = candidate.finish_reason if candidate else None
    if finish != types.FinishReason.STOP:
        raise SmokeFailed(f"finish_reason {finish}")
    try:
        SmokeReply.model_validate_json(response.text or "")
    except ValidationError as exc:
        raise SmokeFailed("keluaran tidak sesuai skema") from exc


def describe_error(exc: Exception, secret: str | None) -> str:
    if isinstance(exc, errors.APIError):
        return redact(f"{exc.code} {exc.status}: {exc.message}", secret)
    return redact(f"{type(exc).__name__}: {exc}", secret)


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
    client = make_client(settings)
    secret = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else None

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
            smoke_test(client, model)
        except Exception as exc:
            print(f"{role:<8} {model}: GALAT {describe_error(exc, secret)}")
            failed += 1
        else:
            print(f"{role:<8} {model}: OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
