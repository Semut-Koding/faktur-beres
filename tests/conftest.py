"""Shared fixtures. The Odoo fixtures are used only by integration tests (``-m integrasi``)."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from faktur_beres.config import Settings
from faktur_beres.llm import usage
from faktur_beres.odoo import OdooClient
from faktur_beres.tools.odoo_gateway import AgentOdooGateway
from faktur_beres.tools.odoo_tools import OdooErpReader


@pytest.fixture(autouse=True)
def llm_usage_file(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Tests write no LLM usage to the real file; ``test:llm`` really calls Gemini, so it does."""
    if request.node.get_closest_marker("llm") is None:
        monkeypatch.setattr(usage, "AUDIT_PATH", tmp_path / "audit-llm.jsonl")
        yield
        return
    handler = usage.attach("test-llm")
    try:
        yield
    finally:
        usage.detach(handler)


@pytest.fixture(scope="session")
def odoo_settings() -> Settings:
    settings = Settings()
    if settings.odoo_agent_api_key is None:
        pytest.fail("ODOO_AGENT_API_KEY kosong; jalankan npm run odoo:init")
    return settings


@pytest.fixture(scope="session")
def agent_client(odoo_settings: Settings) -> Iterator[OdooClient]:
    """The plain JSON-2 client with the agent key, without the gateway's fence."""
    assert odoo_settings.odoo_agent_api_key is not None
    with OdooClient(
        odoo_settings.odoo_url,
        odoo_settings.odoo_db,
        odoo_settings.odoo_agent_api_key,
        user_agent="faktur-beres-test-integrasi/0.1",
    ) as client:
        yield client


@pytest.fixture(scope="session")
def agent_gateway(odoo_settings: Settings) -> Iterator[AgentOdooGateway]:
    with AgentOdooGateway.from_settings(odoo_settings) as gateway:
        yield gateway


@pytest.fixture(scope="session")
def odoo_erp(agent_gateway: AgentOdooGateway) -> OdooErpReader:
    return OdooErpReader(agent_gateway)
