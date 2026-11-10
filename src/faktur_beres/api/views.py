"""The JSON the dashboard reads, built from database rows and masked (BR-24) before it leaves."""

from collections import Counter
from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any

from fastapi.encoders import jsonable_encoder

from faktur_beres.api.masking import mask_public, mask_trail_entry
from faktur_beres.db.models import Decision
from faktur_beres.domain.reasons import InvoiceStatus
from faktur_beres.domain.rules.safety import mask_text

CHOICES = {"setujui": Decision.APPROVE, "tolak": Decision.REJECT, "klarifikasi": Decision.CLARIFY}
CHOICE_NAMES = {decision.value: name for name, decision in CHOICES.items()}
FINISHED = frozenset(
    {
        InvoiceStatus.DRAFT_CREATED.value,
        InvoiceStatus.CLOSED_DUPLICATE.value,
        InvoiceStatus.REJECTED.value,
    }
)
TRAIL_FIELDS = (
    "id",
    "at",
    "node",
    "kind",
    "name",
    "params",
    "summary",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "cost_usd",
    "model",
    "fallback",
    "status",
)


def seconds_to_route(invoice: Mapping[str, Any]) -> float | None:
    received, decided = invoice.get("received_at"), invoice.get("route_decided_at")
    if received is None or decided is None:
        return None
    return round(float((decided - received).total_seconds()), 1)


def summary(invoice: Mapping[str, Any]) -> dict[str, Any]:
    message = invoice.get("failure_message")
    result: dict[str, Any] = jsonable_encoder(
        {
            "id": invoice["invoice_id"],
            "vendor": invoice.get("vendor_name"),
            "nomor_faktur": invoice.get("invoice_number"),
            "total": invoice.get("total"),
            "status": invoice["status"],
            "jalur": invoice.get("route"),
            "alasan": list(invoice.get("reasons") or []),
            "diterima": invoice.get("received_at"),
            "detik_sampai_jalur": seconds_to_route(invoice),
            "kanal": invoice.get("channel"),
            "duplikat_dari": invoice.get("duplicate_of"),
            "pesan_gagal": mask_text(message) if message else None,
        }
    )
    return result


def _findings(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    found = []
    for section, body in analysis.items():
        if not isinstance(body, Mapping):
            continue
        for finding in body.get("findings") or []:
            if isinstance(finding, Mapping):
                found.append({"bagian": section, **finding})
    return found


def _lines(
    extraction: Mapping[str, Any], analysis: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    printed = (extraction.get("invoice") or {}).get("lines") or []
    section = analysis.get("lines") or {}
    by_line = {m.get("line"): m for m in section.get("matches") or []}
    by_position = {row.get("position"): row for row in rows}
    order = ((analysis.get("purchase_order") or {}).get("purchase_order") or {}).get("lines") or []
    po_lines = {line.get("id"): line for line in order}
    result = []
    for number, line in enumerate(printed, start=1):
        match = by_line.get(number) or {}
        row = by_position.get(number) or {}
        result.append(
            {
                "baris": number,
                "faktur": line,
                "status": match.get("status") or row.get("status"),
                "metode": match.get("method") or row.get("method"),
                "harga_cocok": row.get("price_ok"),
                "kuantitas_cocok": row.get("quantity_ok"),
                "bukti": row.get("evidence") or [],
                "po": po_lines.get(match.get("po_line_id") or row.get("po_line_id")),
            }
        )
    return {"nomor_po": section.get("purchase_order"), "baris": result}


def _approval(approval: Mapping[str, Any] | None, status: str) -> dict[str, Any] | None:
    if approval is None:
        return None
    open_now = approval.get("decision") is None and status == InvoiceStatus.AWAITING_APPROVAL.value
    decision = approval.get("decision")
    return {
        "bukti": approval.get("evidence"),
        "alasan": list(approval.get("reasons") or []),
        "usulan": approval.get("proposed_action"),
        "pilihan": [CHOICE_NAMES[o] for o in approval.get("options") or [] if o in CHOICE_NAMES],
        "terbuka": open_now,
        "keputusan": CHOICE_NAMES.get(decision) if decision else None,
        "oleh": approval.get("decided_by"),
        "catatan": approval.get("note"),
        "diputuskan": approval.get("decided_at"),
    }


def detail(
    invoice: Mapping[str, Any],
    *,
    matches: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    approval: Mapping[str, Any] | None,
    busy: bool,
) -> dict[str, Any]:
    extraction = invoice.get("extraction") or {}
    analysis = invoice.get("analysis") or {}
    document = {
        "faktur": {
            **summary(invoice),
            "nama_berkas": invoice.get("file_name"),
            "mime": invoice.get("file_mime"),
            "revisi": invoice.get("revision"),
            "nomor_po": invoice.get("po_number"),
            "tanggal_faktur": invoice.get("invoice_date"),
            "pengirim": invoice.get("sender_email"),
            "jalur_diizinkan": list(invoice.get("allowed_routes") or []),
        },
        "ekstraksi": {
            "status": extraction.get("status"),
            "sumber": extraction.get("source"),
            "catatan": extraction.get("notes") or [],
            "invoice": extraction.get("invoice"),
        },
        "pencocokan": _lines(extraction, analysis, matches),
        "temuan": _findings(analysis),
        "catatan": list(analysis.get("notes") or []),
        "persetujuan": _approval(approval, invoice["status"]),
        "aksi": [
            {
                "jenis": action.get("kind"),
                "target": action.get("target"),
                "status": action.get("status"),
                "hasil": action.get("result"),
                "galat": action.get("error"),
                "waktu": action.get("updated_at"),
            }
            for action in actions
        ],
        "sibuk": busy,
    }
    return mask_public(jsonable_encoder(document))


def trail(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [{field: entry.get(field) for field in TRAIL_FIELDS} for entry in entries]
    return [mask_trail_entry(jsonable_encoder(row)) for row in rows]


def metrics(
    invoices: Sequence[Mapping[str, Any]],
    *,
    with_approval: set[str],
    llm_calls: int,
    llm_cost: Any,
    evaluation: Any,
) -> dict[str, Any]:
    finished = [i for i in invoices if i["status"] in FINISHED]
    touchless = [
        i
        for i in finished
        if i["status"] == InvoiceStatus.DRAFT_CREATED.value
        and i.get("route") == "A"
        and i["invoice_id"] not in with_approval
    ]
    seconds = [s for s in (seconds_to_route(i) for i in invoices) if s is not None]
    result: dict[str, Any] = jsonable_encoder(
        {
            "total": len(invoices),
            "per_jalur": dict(Counter(i.get("route") or "belum" for i in invoices)),
            "per_status": dict(Counter(i["status"] for i in invoices)),
            "selesai": len(finished),
            "touchless": len(touchless),
            "touchless_rate": len(touchless) / len(finished) if finished else None,
            "median_detik_jalur": median(seconds) if seconds else None,
            "llm": {"panggilan": llm_calls, "biaya_usd": llm_cost},
            "evaluasi": evaluation,
        }
    )
    return result
