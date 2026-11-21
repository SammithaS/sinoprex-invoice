"""What an app's code gets at run time (DevRev: the function input)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.apps.mail import GmailIMAP, GmailSMTP, Mailbox, Mailer
from app.config import Settings

if TYPE_CHECKING:
    from app.graph.runner import RunService

ConnectionValues = dict[str, Any]


@dataclass
class Transports:
    """Network clients, injectable so tests never send real email or HTTP."""

    mailer: Callable[[ConnectionValues], Mailer] = lambda v: GmailSMTP(
        v["address"], v["app_password"], v.get("display_name") or ""
    )
    mailbox: Callable[[ConnectionValues], Mailbox] = lambda v: GmailIMAP(
        v["address"], v["app_password"]
    )
    http: httpx.AsyncBaseTransport | None = None

    def client(self, timeout: float = 15) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.http)


@dataclass
class AppContext:
    app_id: str
    inputs: dict[str, Any]
    connections: dict[str, ConnectionValues]  # requirement name → fields + decrypted secrets
    sessionmaker: async_sessionmaker[AsyncSession]
    settings: Settings
    transports: Transports
    run_service: "RunService | None"
    runtime: dict[str, Any]  # persisted between runs (timers, reminders sent, …)
    now: datetime
    secret_values: list[str] = field(default_factory=list)  # for scrubbing logs
    notes: list[str] = field(default_factory=list)

    @property
    def app_url(self) -> str:
        return (self.settings.app_url or self.settings.frontend_origin).rstrip("/")

    def mailer(self, connection: str) -> Mailer:
        return self.transports.mailer(self.connections[connection])

    def mailbox(self, connection: str) -> Mailbox:
        return self.transports.mailbox(self.connections[connection])

    def note(self, text: str) -> None:
        self.notes.append(text)
