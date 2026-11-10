"""The FastAPI application (SPEC §10).

Reads are public and masked (BR-24). Actions need an access code per role in
``Authorization: Bearer <kode>``. Graph work runs in background threads, one operation per invoice
at a time (``GuardedRunner``); the dashboard follows it over ``/api/stream`` or by polling.
"""

import asyncio
import hmac
import json
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from faktur_beres.api import views
from faktur_beres.api.guard import GuardedRunner, RateLimit
from faktur_beres.api.reads import Reads
from faktur_beres.db.store import UnknownInvoice
from faktur_beres.domain.reasons import InvoiceStatus
from faktur_beres.extraction.filetype import MAX_FILE_BYTES
from faktur_beres.graph.inbox import InboxPoller
from faktur_beres.graph.runner import MAX_NOTE, DecisionRefused, FileRefused

LOGGER = logging.getLogger("faktur_beres.api")
FORM_OVERHEAD = 64 * 1024
STREAM_SECONDS = 1.5
MAX_STREAMS = 100
ROLE_AP = "ap"
ROLE_APPROVER = "approver"
PAGE_POLICY = (
    "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "frame-src 'self'; object-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'self'"
)
# An uploaded XML is shown as a document of its own: no script, no requests, no same-origin access.
FILE_POLICY = "sandbox; default-src 'none'; style-src 'unsafe-inline'"


@dataclass(frozen=True)
class ApiConfig:
    code_ap: str | None = None
    code_approver: str | None = None
    uploads_per_hour: int = 10
    inbox_interval: float | None = None
    mailpit_public_url: str | None = None
    odoo_public_url: str | None = None
    static_dir: Path | None = None
    evaluation_summary: Path | None = None
    workers: int = 2


class DecisionBody(BaseModel):
    keputusan: str
    catatan: str | None = Field(default=None, max_length=MAX_NOTE)


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def _same(given: str, expected: str | None) -> bool:
    return expected is not None and hmac.compare_digest(given.encode(), expected.encode())


def _safe_name(raw: str | None) -> str:
    name = PurePosixPath((raw or "").replace("\\", "/")).name.strip()
    cleaned = "".join(ch for ch in name if ch.isprintable())[:120]
    return cleaned or "unggahan"


def create_app(
    runner: GuardedRunner,
    reads: Reads,
    config: ApiConfig,
    poller: InboxPoller | None = None,
) -> FastAPI:
    executor = ThreadPoolExecutor(max_workers=config.workers, thread_name_prefix="graph")
    stop = threading.Event()
    uploads = RateLimit(config.uploads_per_hour)
    streams = {"open": 0}

    def poll_forever(interval: float) -> None:
        assert poller is not None
        while not stop.wait(interval):
            try:
                poller.poll_once()
            except Exception as exc:  # the next pass tries again; a pass is safe to repeat
                LOGGER.warning("pemantau inbox gagal: %s", type(exc).__name__)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        thread = None
        if poller is not None and config.inbox_interval:
            thread = threading.Thread(
                target=poll_forever, args=(config.inbox_interval,), name="inbox", daemon=True
            )
            thread.start()
        try:
            yield
        finally:
            stop.set()
            executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="Faktur Beres", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.endswith("/berkas"):
            response.headers.setdefault("Content-Security-Policy", PAGE_POLICY)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    def require(role: str) -> Callable[[str | None], str]:
        codes = {ROLE_AP: config.code_ap, ROLE_APPROVER: config.code_approver}

        def check(authorization: str | None = Header(default=None)) -> str:
            given = _bearer(authorization)
            if given is None:
                raise HTTPException(401, "Kode akses diperlukan.")
            if _same(given, codes[role]):
                return role
            if any(_same(given, code) for code in codes.values()):
                raise HTTPException(403, "Kode akses ini bukan untuk peran yang diminta.")
            raise HTTPException(401, "Kode akses tidak dikenal.")

        return check

    def known(invoice_id: str) -> dict[str, Any]:
        try:
            return reads.invoice(invoice_id)
        except UnknownInvoice as exc:
            raise HTTPException(404, f"{invoice_id} tidak ditemukan.") from exc

    def in_background(invoice_id: str, work: Callable[[], object]) -> None:
        """Run ``work`` for a reserved invoice; the outcome is read from the database."""

        def job() -> None:
            try:
                work()
            except (DecisionRefused, UnknownInvoice) as exc:
                LOGGER.info("%s: %s", invoice_id, exc)
            except Exception as exc:
                LOGGER.error("%s: galat tak terduga %s", invoice_id, type(exc).__name__)
            finally:
                runner.release(invoice_id)

        executor.submit(job)

    def reserve(invoice_id: str) -> None:
        if not runner.reserve(invoice_id):
            raise HTTPException(409, f"{invoice_id} sedang diproses. Coba lagi sebentar.")

    # --- public reads (masked) ------------------------------------------------------------------

    @app.get("/api/konfigurasi")
    def configuration() -> dict[str, Any]:
        return {
            "mailpit_url": config.mailpit_public_url,
            "odoo_url": config.odoo_public_url,
            "batas_berkas_byte": MAX_FILE_BYTES,
        }

    @app.get("/api/faktur")
    def list_invoices(jalur: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = reads.invoices()
        if jalur:
            rows = [r for r in rows if (r.get("route") or "belum") == jalur]
        if status:
            rows = [r for r in rows if r["status"] == status]
        return [views.summary(row) for row in rows]

    @app.get("/api/faktur/{invoice_id}")
    def invoice_detail(invoice_id: str) -> dict[str, Any]:
        invoice = known(invoice_id)
        return views.detail(
            invoice,
            matches=reads.matches(invoice_id, invoice.get("revision") or 1),
            actions=reads.actions(invoice_id),
            approval=reads.approval(invoice_id),
            busy=runner.busy(invoice_id),
        )

    @app.get("/api/faktur/{invoice_id}/jejak")
    def invoice_trail(invoice_id: str) -> list[dict[str, Any]]:
        known(invoice_id)
        return views.trail(reads.trail(invoice_id))

    @app.get("/api/faktur/{invoice_id}/berkas")
    def invoice_file(invoice_id: str) -> Response:
        invoice = known(invoice_id)
        try:
            data = runner.files.read(invoice["stored_path"])
        except OSError as exc:
            raise HTTPException(404, "Berkas tidak tersedia.") from exc
        return Response(
            data,
            media_type=invoice["file_mime"],
            headers={
                "Content-Disposition": "inline",
                "Content-Security-Policy": FILE_POLICY
                if "xml" in invoice["file_mime"]
                else "default-src 'none'; style-src 'unsafe-inline'; object-src 'self'",
            },
        )

    @app.get("/api/metrik")
    def metrics() -> dict[str, Any]:
        evaluation = None
        if config.evaluation_summary is not None and config.evaluation_summary.is_file():
            evaluation = json.loads(config.evaluation_summary.read_text(encoding="utf-8"))
        calls, cost = reads.llm_totals()
        return views.metrics(
            reads.invoices(),
            with_approval=reads.approval_invoice_ids(),
            llm_calls=calls,
            llm_cost=cost,
            evaluation=evaluation,
        )

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        if streams["open"] >= MAX_STREAMS:
            raise HTTPException(503, "Terlalu banyak sambungan langsung; pakai polling.")

        async def events() -> AsyncIterator[str]:
            streams["open"] += 1
            try:
                last = await run_in_threadpool(reads.last_audit_id)
                yield "retry: 3000\n\n"
                quiet = 0
                while not stop.is_set() and not await request.is_disconnected():
                    changes = await run_in_threadpool(reads.changes_since, last)
                    if changes:
                        last = changes[-1][0]
                        ids = sorted({invoice for _, invoice in changes if invoice})
                        yield f"event: ubah\ndata: {json.dumps({'faktur': ids})}\n\n"
                        quiet = 0
                    else:
                        quiet += 1
                        if quiet % 10 == 0:
                            yield ": hidup\n\n"
                    await asyncio.sleep(STREAM_SECONDS)
            finally:
                streams["open"] -= 1

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- actions (access code) ------------------------------------------------------------------

    @app.post("/api/faktur", status_code=201)
    async def upload(request: Request, _role: str = Depends(require(ROLE_AP))) -> dict[str, Any]:
        client = request.client.host if request.client else "?"
        declared = request.headers.get("content-length", "")
        if not declared.isdigit():
            raise HTTPException(411, "Ukuran unggahan harus dinyatakan (Content-Length).")
        if int(declared) > MAX_FILE_BYTES + FORM_OVERHEAD:
            raise HTTPException(413, "Berkas melebihi 15 MB.")
        if not uploads.allow(client):
            raise HTTPException(
                429, f"Batas {config.uploads_per_hour} unggahan per jam tercapai. Coba lagi nanti."
            )
        form = await request.form(max_files=1, max_fields=1)
        file = form.get("berkas")
        if not isinstance(file, UploadFile):
            raise HTTPException(422, "Sertakan satu berkas di field 'berkas'.")
        data = await file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, "Berkas melebihi 15 MB.")
        try:
            invoice_id = await run_in_threadpool(runner.accept, data, _safe_name(file.filename))
        except FileRefused as exc:
            raise HTTPException(415, str(exc)) from exc
        if runner.reserve(invoice_id):
            in_background(invoice_id, lambda: runner.run(invoice_id))
        return {"id": invoice_id, "status": InvoiceStatus.RECEIVED.value}

    @app.post("/api/faktur/{invoice_id}/keputusan", status_code=202)
    def decide(
        invoice_id: str, body: DecisionBody, _role: str = Depends(require(ROLE_APPROVER))
    ) -> dict[str, Any]:
        invoice = known(invoice_id)
        decision = views.CHOICES.get(body.keputusan)
        if decision is None:
            raise HTTPException(422, "Keputusan harus setujui, tolak, atau klarifikasi.")
        approval = reads.approval(invoice_id)
        if (
            approval is None
            or approval.get("decision") is not None
            or invoice["status"] != InvoiceStatus.AWAITING_APPROVAL.value
        ):
            raise HTTPException(409, f"{invoice_id} tidak sedang menunggu keputusan.")
        if decision.value not in (approval.get("options") or []):
            raise HTTPException(422, f"Pilihan {body.keputusan} tidak tersedia untuk faktur ini.")
        reserve(invoice_id)
        note = body.catatan.strip() if body.catatan and body.catatan.strip() else None
        in_background(
            invoice_id,
            lambda: runner.decide(invoice_id, decision, decided_by="approver (dasbor)", note=note),
        )
        return {"id": invoice_id, "diterima": body.keputusan}

    @app.post("/api/faktur/{invoice_id}/ulang", status_code=202)
    def retry(invoice_id: str, _role: str = Depends(require(ROLE_AP))) -> dict[str, Any]:
        invoice = known(invoice_id)
        if invoice["status"] != InvoiceStatus.FAILED.value:
            raise HTTPException(409, f"{invoice_id} tidak berstatus failed.")
        reserve(invoice_id)
        in_background(invoice_id, lambda: runner.retry(invoice_id))
        return {"id": invoice_id, "diterima": "ulang"}

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown(rest: str) -> Response:
        raise HTTPException(404, "Tidak ada di API.")

    if config.static_dir is not None and config.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=config.static_dir, html=True), name="dasbor")
    return app
