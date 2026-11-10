"""Reading the AP inbox from Mailpit (F-02). Read only; sending stays in ``email_tool``.

Endpoints of the Mailpit API v1.32.0 (checked against its own ``/api/v1/swagger.json``):
``GET /api/v1/messages`` for the list, ``GET /api/v1/message/{ID}`` for the body and the attachment
list, ``GET /api/v1/message/{ID}/headers`` for ``In-Reply-To`` and ``References``, and
``GET /api/v1/message/{ID}/part/{PartID}`` for one attachment. Mailpit's read flag is not used:
opening a message in its UI changes it, so ``inbox_messages`` is the source of truth.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from faktur_beres.domain.normalize import normalize_email
from faktur_beres.tools.email_tool import MailUnavailable

PAGE = 200
TIMEOUT = 20


@dataclass(frozen=True)
class MailSummary:
    id: str
    message_id: str
    sender: str | None
    recipients: tuple[str, ...]
    subject: str


@dataclass(frozen=True)
class AttachmentInfo:
    part_id: str
    name: str
    mime: str
    size: int


@dataclass(frozen=True)
class Mail:
    summary: MailSummary
    text: str
    received_at: datetime | None
    in_reply_to: tuple[str, ...]
    attachments: tuple[AttachmentInfo, ...]


class Inbox(Protocol):
    def summaries(self) -> Iterator[MailSummary]:
        """Every message, oldest first."""
        ...

    def read(self, summary: MailSummary) -> Mail: ...

    def attachment(self, mail_id: str, part_id: str) -> bytes: ...


def _address(item: Any) -> str | None:
    return normalize_email(item.get("Address") or "") if isinstance(item, dict) else None


def _message_ids(values: list[str] | None) -> tuple[str, ...]:
    return tuple(token for value in values or [] for token in value.split() if "@" in token)


class MailpitInbox:
    def __init__(self, api_url: str) -> None:
        self._api = f"{api_url.rstrip('/')}/api/v1"

    def _get(self, path: str, **params: Any) -> httpx.Response:
        try:
            response = httpx.get(f"{self._api}/{path}", params=params, timeout=TIMEOUT)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MailUnavailable(f"API Mailpit gagal: {type(exc).__name__}") from exc
        return response

    def summaries(self) -> Iterator[MailSummary]:
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            page = self._get("messages", start=start, limit=PAGE).json().get("messages") or []
            rows += page
            start += len(page)
            if len(page) < PAGE:
                break
        for row in reversed(rows):  # Mailpit lists the newest first
            recipients = (_address(item) for item in row.get("To") or [])
            yield MailSummary(
                id=row["ID"],
                message_id=row.get("MessageID") or f"mailpit:{row['ID']}",
                sender=_address(row.get("From")),
                recipients=tuple(address for address in recipients if address),
                subject=row.get("Subject") or "",
            )

    def read(self, summary: MailSummary) -> Mail:
        body = self._get(f"message/{summary.id}").json()
        headers = self._get(f"message/{summary.id}/headers").json()
        when = body.get("Date")
        return Mail(
            summary=summary,
            text=body.get("Text") or "",
            received_at=datetime.fromisoformat(when) if when else None,
            in_reply_to=_message_ids(headers.get("In-Reply-To"))
            + _message_ids(headers.get("References")),
            attachments=tuple(
                AttachmentInfo(
                    part_id=item["PartID"],
                    name=item.get("FileName") or "lampiran",
                    mime=item.get("ContentType") or "application/octet-stream",
                    size=int(item.get("Size") or 0),
                )
                for item in body.get("Attachments") or []
            ),
        )

    def attachment(self, mail_id: str, part_id: str) -> bytes:
        return self._get(f"message/{mail_id}/part/{part_id}").content
