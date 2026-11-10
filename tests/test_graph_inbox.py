"""F-02: classification, idempotence and limits of the inbox poller (fake Mailpit, fake LLM)."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from faktur_beres.dataset import load_dataset
from faktur_beres.db.models import InboxKind
from faktur_beres.domain.policy import load_policy
from faktur_beres.domain.reasons import InvoiceStatus
from faktur_beres.evals.dataset_erp import DatasetErpReader
from faktur_beres.extraction.filetype import MAX_FILE_BYTES
from faktur_beres.graph.inbox import InboxPoller
from faktur_beres.tools.inbox import MailpitInbox
from graph_fakes import Harness, harness
from inbox_fakes import FakeInbox
from test_graph_flow import documents, good, pdf

VENDOR = "ar@vendor.test"


def poller(tmp_path: Path, sid: str = "COC-01") -> tuple[Harness, FakeInbox, InboxPoller]:
    invoice, fragments = documents(sid)
    h = harness(
        tmp_path,
        DatasetErpReader(load_dataset()),
        load_policy(),
        lambda _data: good(invoice, fragments),
    )
    inbox = FakeInbox()
    return h, inbox, InboxPoller(h.store, inbox, h.runner)


@pytest.mark.aturan("BR-01")
def test_each_invoice_attachment_becomes_one_invoice(tmp_path: Path) -> None:
    h, inbox, poll = poller(tmp_path)
    inbox.add("m1", sender=VENDOR, subject="Tagihan", attachments=(("a.pdf", pdf("satu")),))
    [handled] = poll.poll_once()
    assert handled.kind is InboxKind.FAKTUR and len(handled.invoice_ids) == 1
    row = h.store.get(handled.invoice_ids[0])
    assert (row.channel, row.sender_email, row.email_subject) == ("email", VENDOR, "Tagihan")
    assert row.status == InvoiceStatus.DRAFT_CREATED.value


def test_a_message_is_handled_once(tmp_path: Path) -> None:
    h, inbox, poll = poller(tmp_path)
    inbox.add("m1", sender=VENDOR, attachments=(("a.pdf", pdf("dua")),))
    poll.poll_once()
    assert poll.poll_once() == []
    assert len(h.store.invoices) == 1 and len(h.bills.created) == 1


def test_an_interrupted_pass_resumes_without_a_second_invoice(tmp_path: Path) -> None:
    h, inbox, poll = poller(tmp_path)
    inbox.add("m1", sender=VENDOR, attachments=(("a.pdf", pdf("tiga")),))
    original = h.runner.run

    def crash(invoice_id: str) -> Any:
        raise RuntimeError("putus")

    h.runner.run = crash  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        poll.poll_once()
    h.runner.run = original  # type: ignore[method-assign]
    [handled] = poll.poll_once()
    assert len(h.store.invoices) == 1 and handled.kind is InboxKind.FAKTUR
    assert h.store.get(handled.invoice_ids[0]).status == InvoiceStatus.DRAFT_CREATED.value


def test_a_crash_right_after_the_invoice_exists_finds_it_again(tmp_path: Path) -> None:
    h, inbox, poll = poller(tmp_path)
    inbox.add("m1", sender=VENDOR, attachments=(("a.pdf", pdf("empat")),))
    original = h.store.inbox_progress
    calls: list[int] = []

    def crash(message_id: str, attachments: Any) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("putus")
        original(message_id, attachments)

    h.store.inbox_progress = crash  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        poll.poll_once()
    poll.poll_once()
    assert len(h.store.invoices) == 1


def test_a_too_large_attachment_is_never_downloaded(tmp_path: Path) -> None:
    h, inbox, poll = poller(tmp_path)
    inbox.add(
        "m1",
        sender=VENDOR,
        attachments=(("besar.pdf", b"x"),),
        sizes={"besar.pdf": MAX_FILE_BYTES + 1},
    )
    [handled] = poll.poll_once()
    assert handled.kind is InboxKind.ABAIKAN and inbox.downloads == []
    assert h.store.inbox["m1"].attachments[0]["status"] == "terlalu besar"


def test_a_message_without_an_invoice_is_recorded_and_ignored(tmp_path: Path) -> None:
    h, inbox, poll = poller(tmp_path)
    inbox.add("m1", sender=VENDOR, text="Halo", attachments=())
    inbox.add("m2", sender=VENDOR, attachments=(("catatan.txt", b"bukan faktur"),))
    handled = poll.poll_once()
    assert [item.kind for item in handled] == [InboxKind.ABAIKAN, InboxKind.ABAIKAN]
    assert h.store.invoices == {}


def test_outgoing_mail_is_never_processed(tmp_path: Path) -> None:
    h, inbox, poll = poller(tmp_path)
    inbox.add("keluar", sender="ap@arunika.test", to=(VENDOR,), attachments=(("a.pdf", pdf()),))
    assert poll.poll_once() == []
    assert h.store.inbox == {}


def test_a_message_from_the_internal_domain_is_ignored(tmp_path: Path) -> None:
    h, inbox, poll = poller(tmp_path)
    inbox.add("m1", sender="finance@arunika.test", attachments=(("a.pdf", pdf("lima")),))
    [handled] = poll.poll_once()
    assert handled.kind is InboxKind.ABAIKAN and h.store.invoices == {}


def test_a_subject_with_a_reference_never_makes_a_new_invoice(tmp_path: Path) -> None:
    h, inbox, poll = poller(tmp_path)
    inbox.add("m1", sender=VENDOR, subject="Re: [FB-000999] x", attachments=(("a.pdf", pdf()),))
    [handled] = poll.poll_once()
    assert handled.kind is InboxKind.DITOLAK and h.store.invoices == {}


def test_mailpit_lists_oldest_first_and_reads_reply_headers() -> None:
    base = {"From": {"Address": "A@Vendor.Test"}, "To": [{"Address": "ap@arunika.test"}]}

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages"):
            rows = [
                {**base, "ID": "b", "MessageID": "new@x", "Subject": "baru"},
                {**base, "ID": "a", "MessageID": "old@x", "Subject": "lama"},
            ]
            return httpx.Response(200, json={"messages": rows})
        if path.endswith("/headers"):
            return httpx.Response(
                200, json={"In-Reply-To": ["<x@y>"], "References": ["<p@q> <x@y>"]}
            )
        if "/part/" in path:
            return httpx.Response(200, content=b"%PDF")
        return httpx.Response(
            200,
            json={
                "Text": "isi",
                "Date": "2026-11-10T10:00:00+07:00",
                "Attachments": [
                    {"PartID": "2", "FileName": "a.pdf", "ContentType": "a/b", "Size": 4}
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx, "get", client.get)
        inbox = MailpitInbox("http://mailpit.test/")
        summaries = list(inbox.summaries())
        assert [s.message_id for s in summaries] == ["old@x", "new@x"]
        assert summaries[0].sender == "a@vendor.test"
        mail = inbox.read(summaries[0])
        assert mail.in_reply_to == ("<x@y>", "<p@q>", "<x@y>")
        assert mail.attachments[0].part_id == "2"
        assert inbox.attachment("a", "2") == b"%PDF"
