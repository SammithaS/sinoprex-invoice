"""Email for apps: send over Gmail SMTP, read the inbox over Gmail IMAP (App Password).

Both sit behind small protocols so tests use in-memory fakes. The blocking stdlib clients run
in a worker thread. Gmail: SMTP smtp.gmail.com:465 (SSL), IMAP imap.gmail.com:993 (SSL); an App
Password needs 2-Step Verification on the account.
"""

import asyncio
import contextlib
import email
import email.policy
import imaplib
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from typing import Protocol

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
IMAP_HOST, IMAP_PORT = "imap.gmail.com", 993
TIMEOUT = 30


@dataclass
class OutgoingEmail:
    to: list[str]
    subject: str
    body: str
    cc: list[str] = field(default_factory=list)
    in_reply_to: str | None = None  # Message-ID being answered (threads the reply)


@dataclass
class IncomingEmail:
    uid: str
    message_id: str
    sender: str  # plain address
    subject: str
    attachments: list[tuple[str, bytes]]  # (file name, content)


class Mailer(Protocol):
    async def send(self, message: OutgoingEmail) -> None: ...


class Mailbox(Protocol):
    async def fetch(self, query: str, limit: int) -> list[IncomingEmail]: ...
    async def mark_seen(self, uids: list[str]) -> None: ...


class GmailSMTP:
    def __init__(self, address: str, app_password: str, display_name: str = "") -> None:
        self.address = address
        self.app_password = app_password
        self.display_name = display_name

    def _send(self, m: OutgoingEmail) -> None:
        msg = EmailMessage()
        msg["From"] = f"{self.display_name} <{self.address}>" if self.display_name else self.address
        msg["To"] = ", ".join(m.to)
        if m.cc:
            msg["Cc"] = ", ".join(m.cc)
        msg["Subject"] = m.subject
        msg["Message-ID"] = make_msgid(domain=self.address.split("@")[-1])
        if m.in_reply_to:
            msg["In-Reply-To"] = m.in_reply_to
            msg["References"] = m.in_reply_to
        msg.set_content(m.body)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT) as smtp:
            smtp.login(self.address, self.app_password)
            smtp.send_message(msg)

    async def send(self, message: OutgoingEmail) -> None:
        await asyncio.to_thread(self._send, message)

    def _login_only(self) -> None:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT) as smtp:
            smtp.login(self.address, self.app_password)

    async def check(self) -> None:
        await asyncio.to_thread(self._login_only)


class GmailIMAP:
    def __init__(self, address: str, app_password: str) -> None:
        self.address = address
        self.app_password = app_password

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=TIMEOUT)
        conn.login(self.address, self.app_password)
        conn.select("INBOX")
        return conn

    def _fetch(self, query: str, limit: int) -> list[IncomingEmail]:
        conn = self._connect()
        try:
            # X-GM-RAW: Gmail's own search syntax (to:, has:attachment, filename:pdf, is:unread).
            typ, data = conn.uid("SEARCH", "X-GM-RAW", f'"{query}"')
            if typ != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()[:limit]
            out: list[IncomingEmail] = []
            for uid in uids:
                # BODY.PEEK keeps the message unread until it has been processed.
                typ, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
                if typ != "OK" or not isinstance(raw, bytes):
                    continue
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                atts: list[tuple[str, bytes]] = []
                for part in msg.walk():
                    name = part.get_filename()
                    if name and part.get_content_disposition() in ("attachment", "inline"):
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            atts.append((name, payload))
                out.append(
                    IncomingEmail(
                        uid=uid.decode(),
                        message_id=str(msg.get("Message-ID") or f"uid-{uid.decode()}"),
                        sender=parseaddr(str(msg.get("From") or ""))[1],
                        subject=str(msg.get("Subject") or ""),
                        attachments=atts,
                    )
                )
            return out
        finally:
            with contextlib.suppress(Exception):
                conn.logout()

    async def fetch(self, query: str, limit: int) -> list[IncomingEmail]:
        return await asyncio.to_thread(self._fetch, query, limit)

    def _mark_seen(self, uids: list[str]) -> None:
        if not uids:
            return
        conn = self._connect()
        try:
            conn.uid("STORE", ",".join(uids), "+FLAGS", "(\\Seen)")
        finally:
            conn.logout()

    async def mark_seen(self, uids: list[str]) -> None:
        await asyncio.to_thread(self._mark_seen, uids)

    def _login_only(self) -> None:
        self._connect().logout()

    async def check(self) -> None:
        await asyncio.to_thread(self._login_only)
