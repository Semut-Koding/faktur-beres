"""The inbox poller (F-02): one pass over the AP inbox, safe to repeat and to resume.

Only messages addressed to the AP mailbox are looked at, and nothing from the internal domain is
processed, so the agent's own e-mail never comes back as work. A message is a reply when its subject
carries ``[FB-nnnnnn]`` or its ``In-Reply-To``/``References`` name one of the agent's e-mails;
otherwise every attachment that is an invoice becomes one invoice. Everything in a message is data
(BR-20), and subject and body reach the injection check (BR-15) with the document.

One Message-ID is handled once. An interrupted pass resumes per attachment: an attachment that
already became an invoice is recognised by the message and the file hash.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from faktur_beres.db.models import InboxKind
from faktur_beres.domain.invoice import Channel
from faktur_beres.domain.reasons import InvoiceStatus
from faktur_beres.extraction.filetype import MAX_FILE_BYTES
from faktur_beres.graph.deps import GraphStore
from faktur_beres.graph.runner import FileRefused, IncomingReply, Runner
from faktur_beres.tools.inbox import AttachmentInfo, Inbox, Mail

THREAD_REFERENCE = re.compile(r"\[(FB-\d{6})\]")
AP_ADDRESS = "ap@arunika.test"
INTERNAL_DOMAIN = "arunika.test"


@dataclass(frozen=True)
class Handled:
    message_id: str
    kind: InboxKind
    invoice_ids: tuple[str, ...] = ()
    reason: str | None = None


class InboxPoller:
    def __init__(
        self,
        store: GraphStore,
        inbox: Inbox,
        runner: Runner,
        *,
        address: str = AP_ADDRESS,
        internal_domain: str = INTERNAL_DOMAIN,
    ) -> None:
        self._store = store
        self._inbox = inbox
        self._runner = runner
        self._address = address
        self._internal = f"@{internal_domain}"

    def poll_once(self) -> list[Handled]:
        summaries = [s for s in self._inbox.summaries() if self._address in s.recipients]
        done = self._store.inbox_done([s.message_id for s in summaries]) if summaries else set()
        handled: list[Handled] = []
        for summary in summaries:
            if summary.message_id in done:
                continue
            handled.append(self._handle(self._inbox.read(summary)))
        return handled

    def _finish(
        self,
        mail: Mail,
        kind: InboxKind,
        invoice_ids: tuple[str, ...] = (),
        reason: str | None = None,
    ) -> Handled:
        message_id = mail.summary.message_id
        invoice_id = invoice_ids[0] if invoice_ids else None
        self._store.inbox_finish(message_id, kind=kind, invoice_id=invoice_id, reason=reason)
        return Handled(message_id, kind, invoice_ids, reason)

    def _handle(self, mail: Mail) -> Handled:
        summary = mail.summary
        row = self._store.inbox_begin(
            summary.message_id,
            received_at=mail.received_at,
            sender=summary.sender,
            recipient=self._address,
            subject=summary.subject,
        )
        if summary.sender is None or summary.sender.endswith(self._internal):
            return self._finish(
                mail, InboxKind.ABAIKAN, reason="pengirim internal atau tanpa alamat"
            )
        thread = self._thread_of(mail)
        if thread is not None:
            return self._reply(mail, thread)
        return self._new_invoices(mail, list(row.attachments or []))

    def _thread_of(self, mail: Mail) -> str | None:
        found = THREAD_REFERENCE.search(mail.summary.subject)
        if found:
            return found.group(1)
        return self._store.invoice_for_outgoing(list(mail.in_reply_to))

    def _reply(self, mail: Mail, thread: str) -> Handled:
        """F-06, BR-20: attachments are fetched only after the reply is known to be acceptable."""
        summary = mail.summary
        refusal = self._runner.reply_refusal(thread, summary.sender, summary.message_id)
        known = () if refusal == "thread tidak dikenal" else (thread,)
        if refusal is not None:
            return self._finish(mail, InboxKind.DITOLAK, known, refusal)
        entries: list[dict[str, Any]] = []
        files: list[tuple[str, bytes]] = []
        for attachment in mail.attachments:
            entry = self._entry(attachment)
            entries.append(entry)
            if attachment.size > MAX_FILE_BYTES:
                entry["status"] = "terlalu besar"
                continue
            data = self._inbox.attachment(summary.id, attachment.part_id)
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            files.append((attachment.name, data))
        self._store.inbox_progress(summary.message_id, entries)
        self._runner.reply(
            thread,
            IncomingReply(
                message_id=summary.message_id,
                sender=summary.sender,
                subject=summary.subject,
                text=mail.text,
                attachments=files,
            ),
        )
        return self._finish(mail, InboxKind.BALASAN, (thread,))

    def _new_invoices(self, mail: Mail, progress: list[dict[str, Any]]) -> Handled:
        message_id = mail.summary.message_id
        by_part = {item["part"]: item for item in progress}
        invoices: list[str] = []
        for attachment in mail.attachments:
            entry = by_part.get(attachment.part_id) or self._entry(attachment)
            if "invoice_id" not in entry and "status" not in entry:
                self._attachment(mail, attachment, entry)
            if entry.get("invoice_id"):
                invoices.append(entry["invoice_id"])
                self._run_if_new(entry["invoice_id"])
            by_part[attachment.part_id] = entry
            self._store.inbox_progress(message_id, list(by_part.values()))
        if invoices:
            return self._finish(mail, InboxKind.FAKTUR, tuple(invoices))
        reason = "tanpa lampiran faktur" if not mail.attachments else "lampiran bukan faktur"
        return self._finish(mail, InboxKind.ABAIKAN, reason=reason)

    @staticmethod
    def _entry(attachment: AttachmentInfo) -> dict[str, Any]:
        return {
            "part": attachment.part_id,
            "name": attachment.name,
            "mime": attachment.mime,
            "size": attachment.size,
        }

    def _attachment(self, mail: Mail, attachment: AttachmentInfo, entry: dict[str, Any]) -> None:
        if attachment.size > MAX_FILE_BYTES:
            entry["status"] = "terlalu besar"  # checked before the download
            return
        data = self._inbox.attachment(mail.summary.id, attachment.part_id)
        digest = hashlib.sha256(data).hexdigest()
        entry["sha256"] = digest
        message_id = mail.summary.message_id
        existing = self._store.invoice_for_message(message_id, digest)
        if existing is not None:
            entry["invoice_id"] = existing
            return
        try:
            entry["invoice_id"] = self._runner.accept(
                data,
                attachment.name,
                channel=Channel.EMAIL,
                sender_email=mail.summary.sender,
                subject=mail.summary.subject,
                body=mail.text,
                source_message_id=message_id,
            )
        except FileRefused:
            entry["status"] = "bukan faktur"

    def _run_if_new(self, invoice_id: str) -> None:
        if InvoiceStatus(self._store.get(invoice_id).status) is InvoiceStatus.RECEIVED:
            self._runner.run(invoice_id)
