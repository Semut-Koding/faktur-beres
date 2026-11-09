"""The real checkpointer (mark ``integrasi``): state survives a new connection, resume runs once."""

import json
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

from faktur_beres.config import REPO_ROOT, get_settings
from faktur_beres.dataset import load_dataset
from faktur_beres.db.connection import app_url, libpq_url, make_engine, other_database
from faktur_beres.db.migrate import upgrade
from faktur_beres.db.models import Decision
from faktur_beres.domain.engine import AnalysisResult
from faktur_beres.domain.erp import NoOtherInvoices
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.reasons import InvoiceStatus
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.evals.labels import load_labels, truth_fragments, truth_to_invoice
from faktur_beres.extraction.outcome import ExtractionOutcome, ExtractionSource, ExtractionStatus
from faktur_beres.graph.build import build_graph
from faktur_beres.graph.deps import Dependencies, FileStore
from faktur_beres.graph.factory import checkpointer
from faktur_beres.graph.runner import Runner
from graph_fakes import FakeBills, FakeMailer, FakeStore

pytestmark = pytest.mark.integrasi
LABEL = load_labels()["COC-04"]
PDF = (REPO_ROOT / LABEL.berkas.path).read_bytes()


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


def deps(tmp_path: Path, store: FakeStore, bills: FakeBills) -> Dependencies:
    truth = LABEL.kebenaran_dokumen
    outcome = ExtractionOutcome(
        status=ExtractionStatus.OK,
        source=ExtractionSource.LLM,
        invoice=truth_to_invoice(truth),
        fragments=tuple(truth_fragments(truth, document_format=LABEL.format)),
    )
    return Dependencies(
        store=store,
        reader=DatasetErpReader(load_dataset()),
        registry=NoOtherInvoices(),
        policy=load_policy(),
        files=FileStore(tmp_path),
        extractor=lambda _data: outcome,
        bills=bills,
        mailer=FakeMailer(),
    )


def test_state_survives_another_connection_and_resume_acts_once(url: str, tmp_path: Path) -> None:
    store, bills = FakeStore(), FakeBills()
    dependencies = deps(tmp_path, store, bills)
    with checkpointer(url) as saver:
        runner = Runner(store, dependencies.files, build_graph(dependencies, saver))
        invoice_id = runner.accept(PDF, "faktur.pdf")
        assert runner.run(invoice_id).status is InvoiceStatus.AWAITING_APPROVAL
        config = {"configurable": {"thread_id": invoice_id}}
        before = runner.graph.get_state(config).values

    with checkpointer(url) as saver:  # a new pool and a new compiled graph, as after a restart
        runner = Runner(store, dependencies.files, build_graph(dependencies, saver))
        after = runner.graph.get_state(config)
        assert after.values == before
        assert after.next == ("await_approval",)
        analysis = AnalysisResult.model_validate(after.values["analysis"])
        original = AnalysisResult.model_validate(before["analysis"])
        assert analysis == original and analysis.bill_plan is not None
        assert analysis.bill_plan.expected_total == original.bill_plan.expected_total  # Decimal
        stop = runner.decide(invoice_id, Decision.APPROVE, decided_by="uji")
        assert stop.status is InvoiceStatus.DRAFT_CREATED
        again = runner.decide(invoice_id, Decision.APPROVE, decided_by="uji")
        assert again.already_decided
    assert len(bills.created) == 1


def test_a_checkpoint_is_readable_by_a_new_process(url: str, tmp_path: Path) -> None:
    store, bills = FakeStore(), FakeBills()
    dependencies = deps(tmp_path, store, bills)
    with checkpointer(url) as saver:
        runner = Runner(store, dependencies.files, build_graph(dependencies, saver))
        invoice_id = runner.accept(PDF, "faktur.pdf")
        runner.run(invoice_id)
        values = runner.graph.get_state({"configurable": {"thread_id": invoice_id}}).values
    script = (
        "import json, sys\n"
        "from langgraph.checkpoint.postgres import PostgresSaver\n"
        "with PostgresSaver.from_conn_string(sys.argv[1]) as saver:\n"
        "    tuple_ = saver.get_tuple({'configurable': {'thread_id': sys.argv[2]}})\n"
        "print(json.dumps(tuple_.checkpoint['channel_values']['analysis']))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, libpq_url(url), invoice_id],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(done.stdout) == values["analysis"]
