"""``npm run demo:balas -- <FB-id> [berkas] [--teks "..."]``: a mock vendor reply for the demo.

A presenter's tool, not part of the agent: it sends an e-mail through the Mailpit SMTP sandbox from
the vendor's registered billing address, as a reply to the agent's clarification e-mail, with the
sender function of the ``eval:graph`` harness. The inbox poller then picks it up like any reply.
Only files inside the repository are accepted, and private folders are refused.
"""

import argparse
import secrets
import sys
from collections.abc import Sequence

from faktur_beres.config import get_settings
from faktur_beres.db.connection import DatabaseNotConfigured, app_url, make_engine
from faktur_beres.db.models import ActionKind, ActionStatus
from faktur_beres.db.store import Store, UnknownInvoice
from faktur_beres.evals.graph import _send
from faktur_beres.graph.cli import PathRefused, checked_file
from faktur_beres.graph.inbox import AP_ADDRESS

DEFAULT_TEXT = "Yth. Tim AP,\n\nTerlampir faktur revisi sesuai klarifikasi.\n\nSalam."


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="npm run demo:balas --", description=__doc__)
    parser.add_argument("invoice_id")
    parser.add_argument("berkas", nargs="?", help="lampiran balasan (mis. faktur revisi)")
    parser.add_argument("--teks", help="isi balasan; wajib bila tanpa lampiran")
    args = parser.parse_args(argv)
    if args.berkas is None and not args.teks:
        parser.error("beri lampiran atau --teks")
    try:
        settings = get_settings()
        store = Store(make_engine(app_url(settings)))
        invoice = store.get(args.invoice_id)
        attachment = None
        if args.berkas is not None:
            path = checked_file(args.berkas)
            attachment = (path.name, path.read_bytes())
    except (UnknownInvoice, PathRefused, DatabaseNotConfigured) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    vendor = ((invoice.analysis or {}).get("vendor") or {}).get("vendor") or {}
    sent = [
        action.result["message_id"]
        for action in store.actions(args.invoice_id)
        if action.kind == ActionKind.SEND_EMAIL.value
        and action.status == ActionStatus.DONE.value
        and action.result
        and action.result.get("message_id")
    ]
    if not vendor.get("email") or not sent:
        print(
            f"{args.invoice_id} belum punya email klarifikasi ke vendor terdaftar; "
            "balasan hanya untuk faktur jalur B yang menunggu vendor.",
            file=sys.stderr,
        )
        return 2
    sender = vendor["email"]
    number = invoice.invoice_number or ""
    message_id = f"<demo-balasan-{secrets.token_hex(8)}@{sender.rsplit('@', 1)[-1]}>"
    _send(
        settings,
        sender=sender,
        to=AP_ADDRESS,
        subject=f"Re: [{args.invoice_id}] Klarifikasi faktur {number}".strip(),
        body=args.teks or DEFAULT_TEXT,
        message_id=message_id,
        in_reply_to=sent[-1],
        attachment=attachment,
    )
    print(f"Balasan tiruan untuk {args.invoice_id} terkirim ke Mailpit dari alamat vendor.")
    print(
        "Pemantau inbox memprosesnya (API dengan INBOX_INTERVAL_DETIK, atau npm run agent:inbox)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
