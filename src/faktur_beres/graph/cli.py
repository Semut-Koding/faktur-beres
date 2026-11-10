"""``npm run agent:proses``, ``status``, ``jejak``, ``putuskan``, ``ulang``, and ``agent:inbox``.

Only files inside the repository are accepted, and any path with a ``_privat`` or ``privat``
component is refused; the file name is stored but never sent to the LLM.
"""

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from faktur_beres.config import REPO_ROOT, get_settings
from faktur_beres.db.connection import DatabaseNotConfigured, app_url, make_engine
from faktur_beres.db.models import AuditEntry, Decision
from faktur_beres.db.store import Store, UnknownInvoice
from faktur_beres.domain.rules.safety import mask_params
from faktur_beres.graph.factory import live_poller, live_runner
from faktur_beres.graph.inbox import InboxPoller
from faktur_beres.graph.runner import DecisionRefused, FileRefused, Stop
from faktur_beres.llm import usage

POLL_SECONDS = 10
FORBIDDEN_PARTS = frozenset({"_privat", "privat"})
CHOICES = {"setujui": Decision.APPROVE, "tolak": Decision.REJECT, "klarifikasi": Decision.CLARIFY}


class PathRefused(ValueError):
    pass


def checked_file(raw: str) -> Path:
    path = Path(raw).expanduser()
    path = (path if path.is_absolute() else Path.cwd() / path).resolve()
    if FORBIDDEN_PARTS & {part.lower() for part in path.parts}:
        raise PathRefused("Path yang memuat folder privat ditolak dan tidak dibaca.")
    if not path.is_relative_to(REPO_ROOT):
        raise PathRefused("Hanya berkas di dalam repo yang diterima.")
    if not path.is_file():
        raise PathRefused(f"Berkas tidak ditemukan: {path.relative_to(REPO_ROOT)}")
    return path


NEXT_STEP = {
    "awaiting_approval": "Keputusan: npm run agent:putuskan -- {id} setujui|tolak|klarifikasi",
    "failed": "Coba lagi: npm run agent:ulang -- {id}",
    "awaiting_vendor": "Agent menunggu balasan vendor; lihat jejak: npm run agent:jejak -- {id}",
}


def show(stop: Stop) -> None:
    print(f"{stop.invoice_id}: status {stop.status.value}, jalur {stop.route or '-'}")
    if stop.reasons:
        print(f"alasan: {', '.join(stop.reasons)}")
    if stop.message:
        print(f"pesan: {stop.message}")
    if stop.already_decided:
        print("keputusan sudah tercatat sebelumnya; tidak ada yang berubah.")
    hint = NEXT_STEP.get(stop.status.value)
    if hint:
        print(hint.format(id=stop.invoice_id))


def trace_line(entry: AuditEntry) -> str:
    bits = [f"{entry.at:%H:%M:%S}", f"{entry.kind:<8}", entry.node or "-", entry.name]
    if entry.status != "ok":
        bits.append(f"[{entry.status}]")
    if entry.model:
        bits.append(f"{entry.model}{' (cadangan)' if entry.fallback else ''}")
        bits.append(f"token {entry.input_tokens}/{entry.output_tokens}/{entry.thinking_tokens}")
    if entry.params:
        bits.append(json.dumps(mask_params(entry.params), ensure_ascii=False))
    if entry.summary:
        bits.append(f"→ {entry.summary}")
    if entry.duration_ms is not None:
        bits.append(f"({entry.duration_ms} ms)")
    return " ".join(bits)


def read_only_store() -> Store:
    return Store(make_engine(app_url(get_settings())))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="npm run agent:*", description=__doc__)
    commands = parser.add_subparsers(dest="perintah", required=True)
    proses = commands.add_parser("proses")
    proses.add_argument("berkas")
    proses.add_argument("--pakai-cache", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("invoice_id", nargs="?")
    commands.add_parser("jejak").add_argument("invoice_id")
    decide = commands.add_parser("putuskan")
    decide.add_argument("invoice_id")
    decide.add_argument("keputusan", choices=sorted(CHOICES))
    decide.add_argument("--catatan")
    decide.add_argument("--oleh", default="approver")
    commands.add_parser("ulang").add_argument("invoice_id")
    inbox = commands.add_parser("inbox")
    inbox.add_argument("--terus", action="store_true", help="baca tiap 10 detik sampai dihentikan")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (PathRefused, FileRefused, DecisionRefused, UnknownInvoice) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except DatabaseNotConfigured as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _poll(poller: InboxPoller, *, keep_going: bool) -> None:
    while True:
        handled = poller.poll_once()
        for item in handled:
            target = ", ".join(item.invoice_ids) or "-"
            print(f"{item.kind.value:<8} {target} {item.reason or ''}".rstrip())
        if not keep_going:
            if not handled:
                print("Tidak ada pesan baru.")
            return
        try:
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            return


def _run(args: argparse.Namespace) -> int:
    if args.perintah == "status":
        store = read_only_store()
        rows = [store.get(args.invoice_id)] if args.invoice_id else store.list_invoices()
        for row in rows:
            print(
                f"{row.invoice_id} {row.status:<18} jalur {row.route or '-'} "
                f"{row.invoice_number or '-'} {','.join(row.reasons or [])}"
            )
        if not rows:
            print("Belum ada faktur. Proses satu dengan npm run agent:proses -- <berkas>.")
        return 0
    if args.perintah == "jejak":
        store = read_only_store()
        store.get(args.invoice_id)
        for entry in store.audit_entries(args.invoice_id):
            print(trace_line(entry))
        return 0
    settings = get_settings()
    use_cache = args.perintah == "proses" and args.pakai_cache
    handler = usage.attach(f"agent-{args.perintah}")
    try:
        with live_runner(settings, use_cache=use_cache) as runner:
            if args.perintah == "proses":
                path = checked_file(args.berkas)
                show(runner.process(path.read_bytes(), path.name))
            elif args.perintah == "putuskan":
                show(
                    runner.decide(
                        args.invoice_id,
                        CHOICES[args.keputusan],
                        decided_by=args.oleh,
                        note=args.catatan,
                    )
                )
            elif args.perintah == "inbox":
                _poll(live_poller(settings, runner), keep_going=args.terus)
            else:
                show(runner.retry(args.invoice_id))
    finally:
        usage.detach(handler)
    return 0


if __name__ == "__main__":
    sys.exit(main())
