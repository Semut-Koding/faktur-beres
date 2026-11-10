"""A Mailpit that lives in memory."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from faktur_beres.tools.inbox import AttachmentInfo, Mail, MailSummary


@dataclass
class FakeInbox:
    mails: list[Mail] = field(default_factory=list)
    files: dict[tuple[str, str], bytes] = field(default_factory=dict)
    downloads: list[str] = field(default_factory=list)

    def add(
        self,
        message_id: str,
        *,
        sender: str,
        subject: str = "Faktur",
        text: str = "Terlampir.",
        to: tuple[str, ...] = ("ap@arunika.test",),
        attachments: tuple[tuple[str, bytes], ...] = (),
        in_reply_to: tuple[str, ...] = (),
        sizes: dict[str, int] | None = None,
    ) -> None:
        mail_id = f"id-{len(self.mails) + 1}"
        infos = []
        for index, (name, data) in enumerate(attachments, start=1):
            part = str(index)
            self.files[(mail_id, part)] = data
            size = (sizes or {}).get(name, len(data))
            infos.append(AttachmentInfo(part, name, "application/pdf", size))
        self.mails.append(
            Mail(
                MailSummary(mail_id, message_id, sender, to, subject),
                text,
                datetime(2026, 11, 10, tzinfo=UTC),
                in_reply_to,
                tuple(infos),
            )
        )

    def summaries(self) -> Iterator[MailSummary]:
        return iter([mail.summary for mail in self.mails])

    def read(self, summary: MailSummary) -> Mail:
        return next(mail for mail in self.mails if mail.summary.id == summary.id)

    def attachment(self, mail_id: str, part_id: str) -> bytes:
        self.downloads.append(f"{mail_id}/{part_id}")
        return self.files[(mail_id, part_id)]
