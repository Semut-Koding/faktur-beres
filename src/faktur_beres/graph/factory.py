"""The live graph: application database, Odoo with the agent key, Gemini, Mailpit."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from faktur_beres.config import REPO_ROOT, Settings
from faktur_beres.db.connection import app_url, libpq_url, make_engine
from faktur_beres.db.store import DbInvoiceRegistry, Store
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.rules.bill_plan import BillPlan
from faktur_beres.graph.build import build_graph
from faktur_beres.graph.deps import Dependencies, FileStore
from faktur_beres.graph.runner import Runner
from faktur_beres.llm.gemini import GeminiClient, gemini_client
from faktur_beres.tools import line_mapping, odoo_tools, po_planner
from faktur_beres.tools.email_tool import MailpitMailer
from faktur_beres.tools.extract_invoice import extract_invoice
from faktur_beres.tools.injection_review import review_injection
from faktur_beres.tools.odoo_gateway import AgentOdooGateway

FILES_ROOT = REPO_ROOT / "var" / "berkas"


@dataclass(frozen=True)
class OdooBills:
    """``BillActions`` over ``odoo_tools`` (agent key, through the gateway only)."""

    gateway: AgentOdooGateway
    tolerance: Decimal

    def create_draft_bill(self, plan: BillPlan, key: str) -> odoo_tools.DraftBillResult:
        return odoo_tools.create_draft_bill(self.gateway, plan, key, tolerance=self.tolerance)

    def attach_file(
        self, bill_id: int, name: str, content: bytes, mimetype: str
    ) -> odoo_tools.AttachmentResult:
        return odoo_tools.attach_file(self.gateway, bill_id, name, content, mimetype)

    def post_note(self, bill_id: int, text: str, key: str) -> odoo_tools.NoteResult:
        return odoo_tools.post_note(self.gateway, bill_id, text, key)


def live_dependencies(
    settings: Settings,
    store: Store,
    gateway: AgentOdooGateway,
    llm: GeminiClient,
    *,
    files_root: Path = FILES_ROOT,
) -> Dependencies:
    policy = load_policy()
    reader = odoo_tools.OdooErpReader(gateway)
    return Dependencies(
        store=store,
        reader=reader,
        registry=DbInvoiceRegistry(store),
        policy=policy,
        files=FileStore(files_root),
        extractor=lambda data: extract_invoice(data, llm=llm, policy=policy),
        reviewer=lambda fragments: review_injection(fragments, llm=llm),
        planner=lambda invoice, vendor, erp: po_planner.run_planner(
            invoice, vendor, erp, llm=llm, policy=policy
        ),
        line_mapper=lambda invoice, po, lines: line_mapping.map_lines(invoice, po, lines, llm=llm),
        bills=OdooBills(gateway, policy.rounding.total),
        mailer=MailpitMailer(
            smtp_host=settings.smtp_host, smtp_port=settings.smtp_port, api_url=settings.mailpit_url
        ),
    )


@contextmanager
def checkpointer(url: str) -> Iterator[PostgresSaver]:
    """``PostgresSaver`` on a connection pool, as LangGraph documents it."""
    with ConnectionPool(
        libpq_url(url),
        min_size=1,
        max_size=4,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
        open=True,
        connection_class=Connection[dict[str, Any]],
    ) as pool:
        yield PostgresSaver(pool)


@contextmanager
def live_runner(settings: Settings, *, use_cache: bool = False) -> Iterator[Runner]:
    url = app_url(settings)
    engine = make_engine(url)
    try:
        store = Store(engine)
        with AgentOdooGateway.from_settings(settings) as gateway, checkpointer(url) as saver:
            llm = gemini_client(settings, use_cache=use_cache)
            deps = live_dependencies(settings, store, gateway, llm)
            yield Runner(store, deps.files, build_graph(replace(deps), saver))
    finally:
        engine.dispose()
