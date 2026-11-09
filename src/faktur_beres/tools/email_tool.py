"""Tool ``email_tool`` (SPEC §5.2, BR-19, BR-25): templated e-mail to the vendor, nothing else.

The only way the agent sends e-mail. The recipient is the vendor's billing address read from Odoo;
``check_recipient`` runs before every send, so an address written in a document or asked for by an
LLM is refused. The body comes from versioned templates and from findings computed by code, with
long digit runs masked (BR-24); no LLM writes it in M4a.

Idempotent and verified (BR-22): the ``Message-ID`` is derived from the action's key, Mailpit is
searched for it before sending, and the message is read back afterwards.
"""

import smtplib
from collections.abc import Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from importlib import resources
from typing import Protocol

import httpx

from faktur_beres.domain.findings import Finding
from faktur_beres.domain.reasons import ReasonCode
from faktur_beres.domain.rules.safety import check_recipient, mask_text

TEMPLATE_VERSION = "v1"
TEMPLATES = "faktur_beres.tools"
MAX_NOTE = 500
MESSAGE_ID_DOMAIN = "fakturberes.arunika.test"


class RecipientRefused(ValueError):
    """BR-25: the address is not one the agent may write to."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"alamat tujuan ditolak (BR-25): {reason}")
        self.reason = reason


class MailUnavailable(RuntimeError):
    """The mail server could not be reached or did not keep the message."""


@dataclass(frozen=True)
class OutgoingEmail:
    sender: str
    to: str
    subject: str
    body: str
    message_id: str


class Mailer(Protocol):
    def exists(self, message_id: str) -> bool: ...

    def send(self, mail: OutgoingEmail) -> None: ...


def _template(name: str) -> str:
    folder = resources.files(TEMPLATES).joinpath("email_templates", TEMPLATE_VERSION)
    return folder.joinpath(f"{name}.txt").read_text(encoding="utf-8").strip()


def message_id_for(key: str) -> str:
    """Deterministic, so a repeated send of the same action is recognised."""
    return f"<{key[:40]}@{MESSAGE_ID_DOMAIN}>"


def _subject(invoice_id: str, invoice_number: str | None, kind: str) -> str:
    number = invoice_number or "tanpa nomor"
    return f"[{invoice_id}] {kind} faktur {number}"


def _bullets(findings: Sequence[Finding]) -> str:
    return "\n".join(f"- {mask_text(f.message)}" for f in findings)


def clarification_body(
    *,
    invoice_number: str | None,
    findings: Sequence[Finding],
    approver_note: str | None,
) -> str:
    """One e-mail per invoice, with one section per reason of route B."""
    sections = []
    for reason in ReasonCode:
        if reason.route.value != "B" or reason is ReasonCode.DUPLICATE_CONFIRMED:
            continue
        mine = [f for f in findings if f.reason is reason]
        if mine:
            sections.append(
                _template(f"bagian_{reason.value}").replace("{rincian}", _bullets(mine))
            )
    note = ""
    if approver_note:
        note = _template("catatan_approver").replace("{catatan}", approver_note[:MAX_NOTE])
    if not sections and not note:
        note = _template("umum")
    body = _template("klarifikasi")
    return (
        body.replace("{nomor}", invoice_number or "tanpa nomor")
        .replace("{bagian}", "\n\n".join(sections))
        .replace("{catatan}", note)
        .replace("\n\n\n", "\n\n")
    )


def clarification_email(
    *,
    invoice_id: str,
    invoice_number: str | None,
    sender: str,
    to: str,
    key: str,
    findings: Sequence[Finding],
    approver_note: str | None,
) -> OutgoingEmail:
    return OutgoingEmail(
        sender=sender,
        to=to,
        subject=_subject(invoice_id, invoice_number, "Klarifikasi"),
        body=clarification_body(
            invoice_number=invoice_number, findings=findings, approver_note=approver_note
        ),
        message_id=message_id_for(key),
    )


def duplicate_notice(
    *,
    invoice_id: str,
    invoice_number: str | None,
    sender: str,
    to: str,
    key: str,
    findings: Sequence[Finding],
) -> OutgoingEmail:
    mine = [f for f in findings if f.reason is ReasonCode.DUPLICATE_CONFIRMED]
    body = (
        _template("duplikat")
        .replace("{nomor}", invoice_number or "tanpa nomor")
        .replace("{rincian}", _bullets(mine))
    )
    return OutgoingEmail(
        sender=sender,
        to=to,
        subject=_subject(invoice_id, invoice_number, "Pemberitahuan duplikat"),
        body=body,
        message_id=message_id_for(key),
    )


def send_checked(
    mailer: Mailer,
    mail: OutgoingEmail,
    *,
    vendor_emails: Sequence[str | None],
    internal_domain: str,
) -> bool:
    """BR-25, then send once and verify. False when the message had already been sent."""
    check = check_recipient(mail.to, vendor_emails, internal_domain)
    if not check.allowed:
        raise RecipientRefused(check.reason)
    if mailer.exists(mail.message_id):
        return False
    mailer.send(mail)
    if not mailer.exists(mail.message_id):
        raise MailUnavailable("email terkirim tetapi tidak terbaca ulang dari server surat")
    return True


class MailpitMailer:
    """SMTP and the search API of the project's Mailpit (``127.0.0.1`` only)."""

    def __init__(self, *, smtp_host: str, smtp_port: int, api_url: str) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._api = api_url.rstrip("/")

    def exists(self, message_id: str) -> bool:
        try:
            response = httpx.get(
                f"{self._api}/api/v1/search",
                params={"query": f'message-id:"{message_id.strip("<>")}"', "limit": 1},
                timeout=10,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MailUnavailable(f"API Mailpit gagal: {type(exc).__name__}") from exc
        return bool(response.json().get("messages_count"))

    def send(self, mail: OutgoingEmail) -> None:
        message = EmailMessage()
        message["From"] = mail.sender
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message["Message-ID"] = mail.message_id
        message.set_content(mail.body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailUnavailable(f"SMTP gagal: {type(exc).__name__}") from exc
