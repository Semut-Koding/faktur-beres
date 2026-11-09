"""``npm run test:llm``: one structured call to each configured Gemini model, over the network.

Marked ``llm``, so ``npm test`` leaves it out. Without ``GEMINI_API_KEY`` the tests are skipped
with a clear message. Two small calls in total, with no document.
"""

import pytest

from faktur_beres.config import Settings, get_settings
from faktur_beres.llm.contracts import CallStatus
from faktur_beres.llm.models import smoke_test

pytestmark = pytest.mark.llm


def settings_with_key() -> Settings:
    settings = get_settings()
    if settings.gemini_api_key is None:
        pytest.skip("GEMINI_API_KEY kosong: isi di .env untuk menjalankan npm run test:llm")
    return settings


@pytest.mark.parametrize("role", ["primary", "strong"], ids=["utama", "kuat"])
def test_structured_call_to_each_configured_model(role: str) -> None:
    settings = settings_with_key()
    model = settings.gemini_model_primary if role == "primary" else settings.gemini_model_strong
    result = smoke_test(settings, model)
    assert result.value.status == "ok" and result.model == model
    last = result.calls[-1]
    assert (last.status, last.finish_reason, last.model) == (CallStatus.OK, "STOP", model)
    assert last.input_tokens and last.total_tokens
    assert last.cost_usd is not None
