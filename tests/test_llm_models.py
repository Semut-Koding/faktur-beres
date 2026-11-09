"""``npm run llm:model`` without the network: model filtering, stable names, key redaction."""

from google.genai import errors, types

from faktur_beres.config import Settings
from faktur_beres.llm.models import (
    configured_models,
    describe_error,
    generate_content_models,
    is_stable,
    redact,
)


def test_stable_names() -> None:
    assert is_stable("models/gemini-3.5-flash-lite")
    assert is_stable("gemini-3.8-flash")
    assert not is_stable("gemini-3.8-flash-preview-09-2026")
    assert not is_stable("gemini-exp-1206")
    assert not is_stable("gemini-flash-latest")


def test_only_generate_content_models_are_listed() -> None:
    models = [
        types.Model(
            name="models/gemini-3.8-flash",
            version="3.8",
            input_token_limit=1048576,
            output_token_limit=65536,
            supported_actions=["generateContent", "countTokens"],
        ),
        types.Model(name="models/text-embedding-9", supported_actions=["embedContent"]),
        types.Model(name="models/gemini-3.5-flash-lite", supported_actions=["generateContent"]),
    ]
    rows = generate_content_models(models)
    assert [r.name for r in rows] == ["gemini-3.5-flash-lite", "gemini-3.8-flash"]
    assert rows[1].input_limit == 1048576
    assert rows[0].version == "-"


def test_redact_hides_the_key() -> None:
    assert redact("key AIza-test-123 ditolak", "AIza-test-123") == "key *** ditolak"
    assert redact("tanpa key", None) == "tanpa key"


def test_error_text_never_shows_the_key() -> None:
    secret = "AIza-palsu-tes"
    exc = errors.ClientError(
        403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": f"key {secret}"}}
    )
    text = describe_error(exc, secret)
    assert secret not in text and "403 PERMISSION_DENIED" in text
    assert secret not in describe_error(RuntimeError(f"gagal {secret}"), secret)


def test_configured_models_default_to_the_stable_candidates() -> None:
    settings = Settings(_env_file=None)
    assert configured_models(settings) == {
        "primary": "gemini-3.5-flash-lite",
        "strong": "gemini-3.8-flash",
    }
    assert settings.gemini_rpm_primary == settings.gemini_rpm_strong == 5
    assert settings.gemini_timeout_seconds == 120
    assert settings.llm_cache_dir == "var/cache/llm"
