"""M4c.1: the API over the graph with fake store, Odoo and mail (no service, no LLM)."""

import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from faktur_beres.api.app import ApiConfig, create_app
from faktur_beres.api.guard import GuardedRunner, RateLimit
from faktur_beres.api.masking import mask_public
from faktur_beres.api.reads import columns
from faktur_beres.dataset import load_dataset
from faktur_beres.db.store import UnknownInvoice
from faktur_beres.evals.dataset_erp import DatasetErpReader
from graph_fakes import FakeStore, harness
from test_graph_flow import POLICY, documents, good, pdf

AP = {"Authorization": "Bearer kode-ap-uji"}
APPROVER = {"Authorization": "Bearer kode-approver-uji"}


class FakeReads:
    """``Reads`` over the in-memory store of the graph tests."""

    def __init__(self, store: FakeStore) -> None:
        self.store = store

    def invoices(self) -> list[dict[str, Any]]:
        return [columns(row) for row in reversed(list(self.store.invoices.values()))]

    def invoice(self, invoice_id: str) -> dict[str, Any]:
        if invoice_id not in self.store.invoices:
            raise UnknownInvoice(invoice_id)
        return columns(self.store.invoices[invoice_id])

    def matches(self, invoice_id: str, revision: int) -> list[dict[str, Any]]:
        return []

    def actions(self, invoice_id: str) -> list[dict[str, Any]]:
        return [
            {**row, "kind": row["kind"].value, "status": row["status"].value}
            for row in self.store.action_rows.values()
            if row["invoice_id"] == invoice_id
        ]

    def approval(self, invoice_id: str) -> dict[str, Any] | None:
        current = self.store.current_approval(invoice_id)
        return dict(current.__dict__) if current is not None else None

    def trail(self, invoice_id: str) -> list[dict[str, Any]]:
        return [
            {"id": i, "kind": row.kind.value, **row.__dict__ | {"kind": row.kind.value}}
            for i, row in enumerate(self.store.trail, start=1)
        ]

    def last_audit_id(self) -> int:
        return len(self.store.trail)

    def changes_since(self, audit_id: int) -> list[tuple[int, str | None]]:
        return []

    def approval_invoice_ids(self) -> set[str]:
        return {a.invoice_id for a in self.store.approvals}

    def llm_totals(self) -> tuple[int, Decimal]:
        return 0, Decimal(0)


class Api:
    def __init__(self, tmp_path: Path, sid: str, **config: Any) -> None:
        invoice, fragments = documents(sid)
        self.sid = sid
        self.h = harness(
            tmp_path,
            DatasetErpReader(load_dataset()),
            POLICY,
            lambda _data: good(invoice, fragments),
        )
        self.runner = GuardedRunner(self.h.runner)
        self.app = create_app(
            self.runner,
            FakeReads(self.h.store),
            ApiConfig(code_ap="kode-ap-uji", code_approver="kode-approver-uji", **config),
        )

    def processed(self) -> str:
        invoice_id = self.runner.accept(pdf(self.sid), "faktur.pdf")
        self.runner.run(invoice_id)
        return invoice_id

    def settle(self, invoice_id: str) -> None:
        deadline = time.monotonic() + 20
        while self.runner.busy(invoice_id):
            assert time.monotonic() < deadline, "pekerjaan latar tidak selesai"
            time.sleep(0.01)


def upload(client: TestClient, headers: dict[str, str], data: bytes | None = None) -> Any:
    files = {"berkas": ("faktur.pdf", data or pdf("unggah"), "application/pdf")}
    return client.post("/api/faktur", files=files, headers=headers)


def test_list_shows_route_and_status_and_filters(tmp_path: Path) -> None:
    api = Api(tmp_path, "COC-01")
    invoice_id = api.processed()
    with TestClient(api.app) as client:
        rows = client.get("/api/faktur").json()
        assert [(r["id"], r["status"], r["jalur"]) for r in rows] == [
            (invoice_id, "draft_created", "A")
        ]
        assert client.get("/api/faktur", params={"jalur": "C"}).json() == []
        assert len(client.get("/api/faktur", params={"status": "draft_created"}).json()) == 1
        assert client.get("/api/faktur/FB-999999").status_code == 404


@pytest.mark.aturan("BR-24")
def test_detail_and_trail_carry_no_whole_npwp_or_account(tmp_path: Path) -> None:
    api = Api(tmp_path, "REK-01")
    invoice_id = api.processed()
    invoice, _ = documents("REK-01")
    vendor_accounts = [
        vendor.bank_account.number for vendor in load_dataset().vendors if vendor.bank_account
    ]
    secrets = [invoice.seller.npwp, invoice.buyer.npwp, invoice.bank_account.number]
    secrets = [re.sub(r"\D", "", s) for s in [*secrets, *vendor_accounts] if s]
    assert len(secrets) >= 3
    with TestClient(api.app) as client:
        detail = client.get(f"/api/faktur/{invoice_id}")
        trail = client.get(f"/api/faktur/{invoice_id}/jejak")
    body = detail.json()
    assert body["faktur"]["status"] == "awaiting_approval"
    assert body["persetujuan"]["terbuka"] is True
    assert "tolak" in body["persetujuan"]["pilihan"]
    assert Decimal(body["ekstraksi"]["invoice"]["total"]) == invoice.total  # amounts stay whole
    for text in (detail.text, trail.text):
        squeezed = re.sub(r"[\s.\-/]", "", text)
        for secret in secrets:
            assert secret not in squeezed


@pytest.mark.aturan("BR-24")
def test_mask_public_hides_odoo_fields_and_keeps_amounts() -> None:
    masked = mask_public(
        {
            "vendor": {"vat": "0990464570663000", "acc_number": "6510082689"},
            "invoice": {"vat": "1201200", "total": "12121200"},
            "catatan_bebas": "NPWP 09.904.645.7-066.3000 dan rekening 6510082689",
        }
    )
    assert masked["vendor"] == {"vat": "0990********3000", "acc_number": "******2689"}
    assert masked["invoice"] == {"vat": "1201200", "total": "12121200"}
    assert "6510082689" not in masked["catatan_bebas"]
    assert "0663000" not in masked["catatan_bebas"]


def test_upload_needs_the_ap_code(tmp_path: Path) -> None:
    api = Api(tmp_path, "COC-01")
    with TestClient(api.app) as client:
        assert upload(client, {}).status_code == 401
        assert upload(client, {"Authorization": "Bearer salah"}).status_code == 401
        assert upload(client, APPROVER).status_code == 403
        assert api.h.store.invoices == {}
        accepted = upload(client, AP)
        assert accepted.status_code == 201
        invoice_id = accepted.json()["id"]
        api.settle(invoice_id)
        assert client.get(f"/api/faktur/{invoice_id}").json()["faktur"]["status"] == "draft_created"
        file = client.get(f"/api/faktur/{invoice_id}/berkas")
        assert file.headers["content-type"] == "application/pdf"
        assert file.content == pdf("unggah")


def test_upload_refuses_what_is_not_an_invoice_file(tmp_path: Path) -> None:
    api = Api(tmp_path, "COC-01")
    with TestClient(api.app) as client:
        assert upload(client, AP, b"bukan faktur").status_code == 415
    assert api.h.store.invoices == {}


def test_upload_rate_limit_per_client(tmp_path: Path) -> None:
    api = Api(tmp_path, "COC-01", uploads_per_hour=2)
    with TestClient(api.app) as client:
        codes = [upload(client, AP, pdf(f"laju-{i}")).status_code for i in range(3)]
    assert codes == [201, 201, 429]


def test_rate_limit_window_moves() -> None:
    now = [0.0]
    limit = RateLimit(1, window=10, clock=lambda: now[0])
    assert limit.allow("a") and not limit.allow("a") and limit.allow("b")
    now[0] = 10.0
    assert limit.allow("a")


def test_decision_needs_the_approver_code_and_an_offered_option(tmp_path: Path) -> None:
    api = Api(tmp_path, "REK-01")
    invoice_id = api.processed()
    url = f"/api/faktur/{invoice_id}/keputusan"
    with TestClient(api.app) as client:
        assert client.post(url, json={"keputusan": "tolak"}).status_code == 401
        assert client.post(url, json={"keputusan": "tolak"}, headers=AP).status_code == 403
        assert client.post(url, json={"keputusan": "hapus"}, headers=APPROVER).status_code == 422
        assert api.h.store.get(invoice_id).status == "awaiting_approval"
        done = client.post(
            url, json={"keputusan": "tolak", "catatan": "rekening beda"}, headers=APPROVER
        )
        assert done.status_code == 202
        api.settle(invoice_id)
        assert api.h.store.get(invoice_id).status == "rejected"
        assert client.post(url, json={"keputusan": "tolak"}, headers=APPROVER).status_code == 409
        metrics = client.get("/api/metrik").json()
    assert metrics["per_status"] == {"rejected": 1}
    assert metrics["touchless_rate"] == 0


def test_one_job_per_invoice(tmp_path: Path) -> None:
    api = Api(tmp_path, "REK-01")
    invoice_id = api.processed()
    assert api.runner.reserve(invoice_id)
    with TestClient(api.app) as client:
        busy = client.post(
            f"/api/faktur/{invoice_id}/keputusan", json={"keputusan": "tolak"}, headers=APPROVER
        )
        assert busy.status_code == 409
        assert client.post(f"/api/faktur/{invoice_id}/ulang", headers=AP).status_code == 409
    api.runner.release(invoice_id)
    assert api.h.store.get(invoice_id).status == "awaiting_approval"
