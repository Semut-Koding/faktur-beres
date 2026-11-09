"""The application database against a temporary database (mark ``integrasi``)."""

import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from faktur_beres.config import REPO_ROOT, get_settings
from faktur_beres.db.connection import (
    app_url,
    make_engine,
    other_database,
    require_project_database,
)
from faktur_beres.db.migrate import check, reset, upgrade
from faktur_beres.db.models import ActionKind, ActionRow, ActionStatus, AuditKind, Decision
from faktur_beres.db.store import DbInvoiceRegistry, NewInvoice, Store, format_invoice_id
from faktur_beres.domain.invoice import Channel, InvoiceData, InvoiceLine, Seller
from faktur_beres.domain.reasons import InvoiceStatus, ReasonCode, Route
from faktur_beres.domain.status import InvalidTransition

pytestmark = pytest.mark.integrasi

SHA = "a" * 64
NEW = NewInvoice(Channel.UPLOAD, "faktur.pdf", SHA, 10, "application/pdf")


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    main = app_url(get_settings())
    name = f"fakturberes_test_{uuid.uuid4().hex[:10]}"
    admin = make_engine(main).execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    temporary = other_database(main, name)
    try:
        upgrade(temporary)
        yield temporary
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def engine(url: str) -> Iterator[Engine]:
    reset(url)
    engine = make_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def store(engine: Engine) -> Store:
    return Store(engine)


def invoice(number: str = "INV/1", total: str = "111") -> InvoiceData:
    return InvoiceData(
        invoice_number=number,
        invoice_date=date(2026, 11, 1),
        seller=Seller(name="Penjual"),
        lines=(
            InvoiceLine(
                description="Barang",
                quantity=Decimal(1),
                unit="kg",
                unit_price=Decimal(100),
                amount=Decimal(100),
            ),
        ),
        subtotal=Decimal(100),
        vat=Decimal(11),
        total=Decimal(total),
    )


def test_migration_matches_the_models_with_langgraph_tables_present(url: str) -> None:
    check(url)
    engine = make_engine(url)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {"invoices", "actions", "approvals", "audit_log", "alembic_version"} <= names
    assert {"checkpoints", "checkpoint_blobs", "checkpoint_writes"} <= names
    assert "vendor_memory" not in names


def test_invoice_ids_come_from_the_sequence(store: Store) -> None:
    first, second = store.create_invoice(NEW), store.create_invoice(NEW)
    assert (
        (first, second)
        == (format_invoice_id(1), format_invoice_id(2))
        == (
            "FB-000001",
            "FB-000002",
        )
    )


def test_status_changes_follow_the_table_and_are_audited(store: Store) -> None:
    invoice_id = store.create_invoice(NEW)
    store.set_status(invoice_id, InvoiceStatus.PROCESSING, node="intake")
    store.set_status(invoice_id, InvoiceStatus.DRAFT_CREATED, node="act_bill")
    with pytest.raises(InvalidTransition):
        store.set_status(invoice_id, InvoiceStatus.PROCESSING)
    assert store.get(invoice_id).status == "draft_created"
    summaries = [e.summary for e in store.audit_entries(invoice_id) if e.name == "status"]
    assert summaries == ["received -> processing", "processing -> draft_created"]


def test_failed_keeps_the_message_until_it_is_retried(store: Store) -> None:
    invoice_id = store.create_invoice(NEW)
    store.set_status(invoice_id, InvoiceStatus.PROCESSING)
    store.set_status(invoice_id, InvoiceStatus.FAILED, message="LLM tidak tersedia")
    assert store.get(invoice_id).failure_message == "LLM tidak tersedia"
    store.set_status(invoice_id, InvoiceStatus.PROCESSING)
    assert store.get(invoice_id).failure_message is None


def test_an_action_key_is_unique_and_a_done_action_stays_done(store: Store) -> None:
    invoice_id = store.create_invoice(NEW)
    key = "k" * 64
    kw = {"invoice_id": invoice_id, "kind": ActionKind.POST_NOTE, "target": "#1"}
    first = store.begin_action(key, content_version="v", **kw)  # type: ignore[arg-type]
    assert (first.status, first.attempts) == (ActionStatus.PENDING, 1)
    assert store.begin_action(key, content_version="v", **kw).attempts == 2  # type: ignore[arg-type]
    store.finish_action(key, {"message_id": 5})
    again = store.begin_action(key, content_version="v", **kw)  # type: ignore[arg-type]
    assert again.done and again.result == {"message_id": 5} and again.attempts == 2
    assert len(store.actions(invoice_id)) == 1


def test_the_database_refuses_a_second_row_with_the_same_key(store: Store, engine: Engine) -> None:
    invoice_id = store.create_invoice(NEW)
    row = {
        "idempotency_key": "x" * 64,
        "invoice_id": invoice_id,
        "kind": "post_note",
        "target": "t",
        "content_version": "v",
        "status": "pending",
    }
    with engine.begin() as connection:
        connection.execute(ActionRow.__table__.insert().values(row))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(ActionRow.__table__.insert().values(row))


def test_at_most_one_open_approval_and_a_decision_is_recorded_once(store: Store) -> None:
    invoice_id = store.create_invoice(NEW)
    args = {"evidence": {"a": 1}, "reasons": ["new_vendor"], "proposed_action": "setujui"}
    first = store.open_approval(invoice_id, options=["approve"], **args)  # type: ignore[arg-type]
    second = store.open_approval(invoice_id, options=["approve"], **args)  # type: ignore[arg-type]
    assert first.id == second.id
    decided = store.decide(invoice_id, decision=Decision.APPROVE, decided_by="uji", note=None)
    assert decided is not None and decided.decision == "approve"
    assert store.decide(invoice_id, decision=Decision.REJECT, decided_by="lain", note=None) is None
    current = store.current_approval(invoice_id)
    assert current is not None and current.decision == "approve" and current.decided_by == "uji"


def test_analysis_rows_replace_the_same_revision(store: Store) -> None:
    invoice_id = store.create_invoice(NEW)
    for _ in range(2):
        store.save_analysis(
            invoice_id,
            invoice=invoice(),
            dump={"x": 1},
            reasons=[ReasonCode.NEW_VENDOR],
            allowed=[Route.C],
            route=Route.C,
            vendor_id=7,
            vendor_name="V",
            po_number="P1",
            lines=[],
        )
    row = store.get(invoice_id)
    assert (row.route, row.reasons, row.vendor_id) == ("C", ["new_vendor"], 7)


def test_registry_leaves_out_the_invoice_itself_and_other_vendors(store: Store) -> None:
    mine, other = store.create_invoice(NEW), store.create_invoice(replace(NEW, file_name="b.pdf"))
    for invoice_id, vendor in ((mine, 7), (other, 7)):
        store.save_extraction(invoice_id, invoice(f"INV/{invoice_id}"), {"ok": True})
        store.save_analysis(
            invoice_id,
            invoice=None,
            dump={},
            reasons=[],
            allowed=[Route.A],
            route=Route.A,
            vendor_id=vendor,
            vendor_name="V",
            po_number=None,
            lines=[],
        )
    registry = DbInvoiceRegistry(store)
    found = registry.other_invoices(7, exclude_invoice_id=mine)
    assert [f.invoice_id for f in found] == [other]
    assert found[0].total == Decimal("111.00")
    assert registry.other_invoices(8, exclude_invoice_id=None) == []


def test_audit_masks_parameters(store: Store) -> None:
    invoice_id = store.create_invoice(NEW)
    store.audit(
        invoice_id,
        kind=AuditKind.TOOL,
        name="uji",
        params={"npwp": "0990427957220000", "rekening": "5450717927"},
    )
    entry = store.audit_entries(invoice_id)[-1]
    assert entry.params == {"npwp": "0990********0000", "rekening": "******7927"}


def test_reset_empties_data_but_keeps_the_schema(url: str, store: Store) -> None:
    invoice_id = store.create_invoice(NEW)
    assert reset(url)
    assert store.list_invoices() == [] and invoice_id == "FB-000001"
    assert store.create_invoice(NEW) == "FB-000001"
    check(url)


def test_reset_refuses_a_database_of_another_project() -> None:
    with pytest.raises(ValueError, match="berawalan"):
        require_project_database("postgresql+psycopg://u:p@127.0.0.1:25432/dombatek")


def test_db_reset_command_refuses_from_its_guard() -> None:
    code = "from faktur_beres.db.migrate import reset; reset('postgresql+psycopg://u:p@h/lain')"
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert done.returncode != 0 and "berawalan" in done.stderr
