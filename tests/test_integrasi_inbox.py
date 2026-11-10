"""F-02 against the real Mailpit (mark ``integrasi``); its own message is removed after."""

import smtplib
import uuid
from collections.abc import Iterator
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest

from faktur_beres.config import get_settings
from faktur_beres.dataset import load_dataset
from faktur_beres.db.models import InboxKind
from faktur_beres.domain.policy import load_policy
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.graph.inbox import InboxPoller
from faktur_beres.tools.inbox import MailpitInbox
from graph_fakes import harness
from test_graph_flow import documents, good, pdf

pytestmark = pytest.mark.integrasi


@pytest.fixture
def message_id() -> Iterator[str]:
    settings = get_settings()
    value = f"uji-{uuid.uuid4().hex}@vendor.test"
    yield value
    api = f"{settings.mailpit_url.rstrip('/')}/api/v1"
    rows = httpx.get(f"{api}/messages", params={"limit": 200}, timeout=20).json()["messages"] or []
    ids = [row["ID"] for row in rows if row["MessageID"] == value]
    if ids:
        httpx.request("DELETE", f"{api}/messages", json={"IDs": ids}, timeout=20)


def test_an_email_with_an_invoice_becomes_an_invoice(tmp_path: Path, message_id: str) -> None:
    settings = get_settings()
    mail = EmailMessage()
    mail["From"] = "Penagihan <AR@Vendor.Test>"
    mail["To"] = "ap@arunika.test"
    mail["Subject"] = "Tagihan bulan ini"
    mail["Message-ID"] = f"<{message_id}>"
    mail.set_content("Terlampir faktur.")
    mail.add_attachment(pdf("integrasi"), maintype="application", subtype="pdf", filename="f.pdf")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.send_message(mail)

    invoice, fragments = documents("COC-01")
    h = harness(
        tmp_path,
        DatasetErpReader(load_dataset()),
        load_policy(),
        lambda _data: good(invoice, fragments),
    )
    inbox = MailpitInbox(settings.mailpit_url)
    summaries = [s for s in inbox.summaries() if s.message_id == message_id]
    assert len(summaries) == 1

    class Only:
        def summaries(self):
            return iter(summaries)

        def read(self, summary):
            return inbox.read(summary)

        def attachment(self, mail_id, part_id):
            return inbox.attachment(mail_id, part_id)

    poller = InboxPoller(h.store, Only(), h.runner)
    [handled] = poller.poll_once()
    assert handled.kind is InboxKind.FAKTUR
    row = h.store.get(handled.invoice_ids[0])
    assert (row.sender_email, row.email_subject, row.email_body.strip()) == (
        "ar@vendor.test",
        "Tagihan bulan ini",
        "Terlampir faktur.",
    )
    assert poller.poll_once() == []
