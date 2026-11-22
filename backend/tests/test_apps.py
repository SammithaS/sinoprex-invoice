"""The apps platform: encrypted connections, app lifecycle, events, timers, built-ins.

Nothing leaves the machine: email goes to fake mailers / mailboxes, HTTP to an httpx
MockTransport. A fixed clock drives the timers.
"""

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.apps.context import Transports
from app.apps.mail import IncomingEmail, OutgoingEmail
from app.apps.manifest import ConfigError, Field
from app.apps.platform import AppPlatform
from app.apps.registry import APPS
from app.apps.secrets import SecretBox, SecretsUnavailableError, hint, scrub
from app.config import get_settings
from app.db.models import AppAudit, AppSecret, Run
from app.graph.runner import RunService
from app.integrations.signing import signed_headers
from tests.test_graph import INVOICES, api, db, run_sample, service  # noqa: F401  (fixtures)

KEY = Fernet.generate_key().decode()
WEBHOOK = "https://hooks.slack.com/services/T000/B000/abcdefghijklmnop"
APP_PASSWORD = "abcd efgh ijkl mnop"
ACTOR = {"X-Actor": "Sammitha S Poojary"}


# --- fakes ---------------------------------------------------------------------------------------


@dataclass
class FakeMailer:
    sent: list[OutgoingEmail] = field(default_factory=list)

    async def send(self, message: OutgoingEmail) -> None:
        self.sent.append(message)

    async def check(self) -> None:
        return None


@dataclass
class FakeMailbox:
    inbox: list[IncomingEmail] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    seen: list[str] = field(default_factory=list)

    async def fetch(self, query: str, limit: int) -> list[IncomingEmail]:
        self.queries.append(query)
        return [m for m in self.inbox if m.uid not in self.seen][:limit]

    async def mark_seen(self, uids: list[str]) -> None:
        self.seen += uids

    async def check(self) -> None:
        return None


@dataclass
class Http:
    """Records requests; answers 200 unless `status` says otherwise."""

    requests: list[httpx.Request] = field(default_factory=list)
    status: int = 200
    body: str = "ok"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, text=self.body)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 20, 4, 0)  # 09:30 IST, a Friday

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Rig:
    platform: AppPlatform
    mailer: FakeMailer
    mailbox: FakeMailbox
    http: Http
    clock: Clock


@pytest.fixture
def secrets_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("SECRETS_KEY", KEY)
    get_settings.cache_clear()
    yield KEY
    get_settings.cache_clear()


@pytest.fixture
def rig(
    db: async_sessionmaker[AsyncSession],
    service: RunService,
    secrets_key: str,
) -> Rig:
    mailer, mailbox, http, clock = FakeMailer(), FakeMailbox(), Http(), Clock()
    transports = Transports(
        mailer=lambda v: mailer, mailbox=lambda v: mailbox, http=httpx.MockTransport(http.handler)
    )
    platform = AppPlatform(
        db, get_settings(), transports=transports, run_service=service, clock=clock
    )
    service.on_event = platform.emit
    return Rig(platform, mailer, mailbox, http, clock)


@pytest.fixture
async def apps_api(api: AsyncClient, rig: Rig) -> AsyncIterator[AsyncClient]:
    api._transport.app.state.apps = rig.platform  # type: ignore[attr-defined]
    yield api
    await rig.platform.drain()


async def gmail(p: AppPlatform) -> int:
    conn = await p.create_connection(
        "gmail", "AP inbox", {"address": "ap@example.com"}, {"app_password": APP_PASSWORD}, "me"
    )
    return int(conn["id"])


async def slack(p: AppPlatform) -> int:
    conn = await p.create_connection(
        "slack", "", {"channel": "#ap"}, {"webhook_url": WEBHOOK}, "me"
    )
    return int(conn["id"])


async def activate(p: AppPlatform, app_id: str, inputs: dict[str, Any], **bind: int) -> None:
    await p.install(app_id, "me")
    await p.configure(app_id, inputs, dict(bind), "me")
    out = await p.activate(app_id, "me")
    assert out["install"]["state"] == "ACTIVE", out["install"]


# --- secrets -------------------------------------------------------------------------------------


def test_secret_box_encrypts_rotates_and_refuses_without_a_key() -> None:
    box = SecretBox([KEY])
    token = box.encrypt("s3cret-value")
    assert "s3cret" not in token and box.decrypt(token) == "s3cret-value"

    new = Fernet.generate_key().decode()
    rotated = SecretBox.from_setting(f"{new},{KEY}")  # newest first, old one kept
    assert rotated.decrypt(token) == "s3cret-value"
    fresh = rotated.rotate(token)
    assert SecretBox([new]).decrypt(fresh) == "s3cret-value"

    with pytest.raises(SecretsUnavailableError):
        SecretBox([new]).decrypt(token)  # old key dropped too early
    with pytest.raises(SecretsUnavailableError):
        SecretBox.from_setting("")
    with pytest.raises(SecretsUnavailableError):
        SecretBox.from_setting("not-a-key")


def test_hint_and_scrub() -> None:
    assert hint("abcdefghijklmnop") == "mnop" and hint("short") == ""
    text = f"POST {WEBHOOK} failed; url={httpx.URL(WEBHOOK)} q=%s" % "ab%2Fcd"
    out = scrub(text, [WEBHOOK, "ab/cd"])
    assert WEBHOOK not in out and "ab%2Fcd" not in out and out.count("••••") == 3


def test_field_coercion() -> None:
    assert Field("n", "N", "number", minimum=1).coerce("5") == 5
    with pytest.raises(ConfigError, match="at least 1"):
        Field("n", "N", "number", minimum=1).coerce(0)
    assert Field("e", "E", "emails").coerce("a@x.com; b@y.in") == ["a@x.com", "b@y.in"]
    with pytest.raises(ConfigError):
        Field("e", "E", "emails").coerce("a@x.com, nope")
    with pytest.raises(ConfigError):
        Field("u", "U", "url").coerce("http://plain.example")
    assert Field("t", "T", "time").coerce("09:30") == "09:30"
    with pytest.raises(ConfigError):
        Field("t", "T", "time").coerce("9.30")
    assert Field("b", "B", "boolean").coerce("on") is True
    assert Field("x", "X", required=False, default="d").coerce("") == "d"
    with pytest.raises(ConfigError, match="required"):
        Field("x", "X", required=True).coerce(None)
    sel = Field("s", "S", "multiselect", options=(("a", "A"), ("b", "B")))
    assert sel.coerce(["a"]) == ["a"]
    with pytest.raises(ConfigError):
        sel.coerce(["c"])


def test_every_builtin_app_has_handlers_for_its_triggers() -> None:
    assert set(APPS) == {
        "gmail-intake",
        "reviewer-alerts",
        "vendor-followup",
        "daily-summary",
        "slack-alerts",
        "razorpayx-payouts",
        "payment-advice",
        "n8n-connector",
    }
    for m in APPS.values():
        pub = m.public()
        assert pub["id"] == m.id and pub["name"]
        for e in m.events:
            assert e in m.handlers
        for t in m.timers:
            assert t.name in m.handlers


# --- connections over the API --------------------------------------------------------------------


async def test_connection_secrets_are_write_only(
    apps_api: AsyncClient,
    rig: Rig,
    db: async_sessionmaker[AsyncSession],
) -> None:
    types = (await apps_api.get("/connection-types")).json()
    assert {t["id"] for t in types} == {"gmail", "slack", "n8n", "razorpayx"}

    res = await apps_api.post(
        "/connections",
        json={"type": "slack", "name": "AP channel", "fields": {"channel": "#ap"}},
        headers=ACTOR,
    )
    assert res.status_code == 422 and "required" in res.json()["detail"]

    res = await apps_api.post(
        "/connections",
        json={
            "type": "slack",
            "name": "AP channel",
            "fields": {"channel": "#ap"},
            "secrets": {"webhook_url": WEBHOOK},
        },
        headers=ACTOR,
    )
    assert res.status_code == 201, res.text
    conn = res.json()
    assert WEBHOOK not in res.text and "abcdefghijklmnop" not in res.text
    assert conn["secrets"] == [
        {
            "name": "webhook_url",
            "label": conn["secrets"][0]["label"],
            "set": True,
            "hint": "mnop",
            "updated_at": conn["secrets"][0]["updated_at"],
            "updated_by": "Sammitha S Poojary",
        }
    ]
    listed = await apps_api.get("/connections")
    assert WEBHOOK not in listed.text and listed.json()[0]["id"] == conn["id"]

    async with db() as session:
        row = (await session.scalars(select(AppSecret))).one()
        assert WEBHOOK not in row.ciphertext
        assert SecretBox([KEY]).decrypt(row.ciphertext) == WEBHOOK

    # Test: posts through the stored webhook (decrypted only in memory).
    tested = (await apps_api.post(f"/connections/{conn['id']}/test")).json()
    assert tested["status"] == "OK" and str(rig.http.requests[0].url) == WEBHOOK

    # A failing test never echoes the secret back.
    rig.http.status, rig.http.body = 404, f"no such hook {WEBHOOK}"
    tested = (await apps_api.post(f"/connections/{conn['id']}/test")).json()
    assert tested["status"] == "ERROR" and WEBHOOK not in tested["status_message"]
    assert "••••" in tested["status_message"]

    # Replace, then remove.
    other = "https://hooks.slack.com/services/T000/B000/zzzzzzzzzzzz9999"
    res = await apps_api.patch(
        f"/connections/{conn['id']}", json={"secrets": {"webhook_url": other}}
    )
    assert res.json()["secrets"][0]["hint"] == "9999" and res.json()["status"] == "UNTESTED"
    res = await apps_api.patch(
        f"/connections/{conn['id']}", json={"secrets": {"webhook_url": None}}
    )
    assert res.json()["secrets"][0]["set"] is False
    res = await apps_api.patch(f"/connections/{conn['id']}", json={"secrets": {"nope": "x"}})
    assert res.status_code == 422

    async with db() as session:
        actions = [a.action for a in await session.scalars(select(AppAudit))]
    assert actions[:1] == ["connection.created"]
    assert {"secret.replaced", "secret.removed", "connection.tested"} <= set(actions)

    assert (await apps_api.delete(f"/connections/{conn['id']}")).status_code == 204
    assert (await apps_api.get("/connections")).json() == []
    assert (await apps_api.delete("/connections/999")).status_code == 404


async def test_without_a_secrets_key_connections_are_refused(
    api: AsyncClient,
    db: async_sessionmaker[AsyncSession],
) -> None:
    api._transport.app.state.apps = AppPlatform(db, get_settings())  # type: ignore[attr-defined]
    res = await api.post(
        "/connections",
        json={"type": "slack", "fields": {"channel": "#ap"}, "secrets": {"webhook_url": WEBHOOK}},
    )
    assert res.status_code == 503 and "SECRETS_KEY" in res.json()["detail"]


# --- lifecycle -----------------------------------------------------------------------------------


async def test_lifecycle_over_the_api(apps_api: AsyncClient, rig: Rig) -> None:
    catalog = (await apps_api.get("/apps")).json()
    assert len(catalog) == 8 and all(a["install"] is None for a in catalog)
    assert (await apps_api.get("/apps/nope")).status_code == 404

    res = await apps_api.post("/apps/slack-alerts/install", headers=ACTOR)
    assert res.json()["install"]["state"] == "DRAFT"
    assert res.json()["install"]["installed_by"] == "Sammitha S Poojary"

    # No channel chosen yet: activating puts the app in ERROR with the reason.
    res = await apps_api.post("/apps/slack-alerts/activate")
    assert res.json()["install"]["state"] == "ERROR"
    assert "Slack channel" in res.json()["install"]["error"]

    bad = await apps_api.put("/apps/slack-alerts/config", json={"inputs": {"events": ["x"]}})
    assert bad.status_code == 422

    gid = await gmail(rig.platform)
    wrong = await apps_api.put("/apps/slack-alerts/config", json={"bindings": {"channel": gid}})
    assert wrong.status_code == 422 and "Slack" in wrong.json()["detail"]

    sid = await slack(rig.platform)
    res = await apps_api.put(
        "/apps/slack-alerts/config",
        json={"inputs": {"events": ["case.failed"]}, "bindings": {"channel": sid}},
    )
    assert res.status_code == 200 and res.json()["install"]["bindings"] == {"channel": sid}
    res = await apps_api.post("/apps/slack-alerts/activate")
    assert res.json()["install"]["state"] == "ACTIVE" and res.json()["install"]["error"] is None

    # The connection is in use: it can't be deleted.
    assert (await apps_api.delete(f"/connections/{sid}")).status_code == 422
    used = (await apps_api.get("/connections")).json()
    assert next(c for c in used if c["id"] == sid)["used_by"] == ["slack-alerts"]

    # Slack Alerts has nothing to run on demand.
    assert (await apps_api.post("/apps/slack-alerts/run")).status_code == 422

    res = await apps_api.post("/apps/slack-alerts/deactivate")
    assert res.json()["install"]["state"] == "INACTIVE"
    assert (await apps_api.delete("/apps/slack-alerts")).status_code == 204
    assert (await apps_api.get("/apps/slack-alerts")).json()["install"] is None
    assert (await apps_api.delete(f"/connections/{sid}")).status_code == 204


async def test_events_reach_active_subscribers_and_runs_are_logged(
    rig: Rig,
    db: async_sessionmaker[AsyncSession],
) -> None:
    p = rig.platform
    await activate(p, "slack-alerts", {"events": ["case.failed"]}, channel=await slack(p))
    async with db() as session:
        session.add(
            Run(
                id="r-1",
                status="FAILED",
                file_name="x.pdf",
                file_path="x.pdf",
                file_hash="h1",
                source="UPLOAD",
                vendor_name="Northwind",
                invoice_number="NW-1",
                error=f"boom at {WEBHOOK}",
            )
        )
        await session.commit()

    p.emit("case.waiting_review", {"run_id": "r-1"})  # switched off in the settings
    p.emit("case.failed", {"run_id": "r-1"})
    p.emit("case.request_info", {"run_id": "r-1"})  # not an event this app listens to
    await p.drain()
    runs = (await p.get_app("slack-alerts"))["runs"]
    assert [(r["trigger"], r["status"]) for r in reversed(runs)] == [
        ("case.waiting_review", "SKIPPED"),
        ("case.failed", "OK"),
    ]
    [req] = rig.http.requests
    text = req.read().decode()
    assert "Case failed" in text and "Northwind" in text and "/runs/r-1" in text

    # A failing handler is logged as ERROR, with the secret scrubbed out of the message.
    rig.http.status, rig.http.body = 500, f"upstream {WEBHOOK}"
    out = await p.execute("slack-alerts", "case.failed", {"run_id": "r-1"})
    assert out["status"] == "ERROR" and WEBHOOK not in out["error"] and "••••" in out["error"]

    # Inactive apps get nothing.
    await p.deactivate("slack-alerts", "me")
    p.emit("case.failed", {"run_id": "r-1"})
    await p.drain()
    assert len((await p.get_app("slack-alerts"))["runs"]) == 3


async def test_editing_a_live_app_revalidates_it(rig: Rig) -> None:
    p = rig.platform
    await activate(p, "vendor-followup", {"demo_inbox": "demo@example.com"}, sender=await gmail(p))
    out = await p.configure("vendor-followup", {"demo_inbox": ""}, {}, "me")
    assert out["install"]["state"] == "ERROR"
    assert "demo inbox" in out["install"]["error"].lower()


# --- built-in apps end to end ------------------------------------------------------------------


async def test_reviewer_alerts_email_and_remind(rig: Rig, service: RunService) -> None:
    p = rig.platform
    await activate(
        p,
        "reviewer-alerts",
        {"reviewer_emails": "rev@example.com", "remind_after_hours": 4},
        sender=await gmail(p),
    )
    run = await run_sample(service, "deccan-scanned-poor")
    assert run.status == "WAITING_REVIEW"
    await p.drain()
    [mail] = rig.mailer.sent
    assert mail.to == ["rev@example.com"] and "Review needed" in mail.subject
    assert f"/runs/{run.id}" in mail.body

    assert await p.tick() == 1  # the reminder timer runs, but it's too early
    assert len(rig.mailer.sent) == 1
    rig.clock.now += timedelta(hours=5)
    await p.tick()
    assert len(rig.mailer.sent) == 2 and "Reminder" in rig.mailer.sent[1].subject
    rig.clock.now += timedelta(hours=5)
    await p.tick()
    assert len(rig.mailer.sent) == 2  # one reminder per wait


async def test_gmail_intake_turns_emails_into_cases(
    rig: Rig,
    service: RunService,
    db: async_sessionmaker[AsyncSession],
) -> None:
    p = rig.platform
    pdf = (INVOICES / "technova-contract-rate.pdf").read_bytes()
    rig.mailbox.inbox = [
        IncomingEmail(
            uid="7",
            message_id="<m-7@example.com>",
            sender="accounts@technova.example",
            subject="Invoice TN-1042",
            attachments=[("TN-1042.pdf", pdf), ("notes.txt", b"hello")],
        )
    ]
    await activate(p, "gmail-intake", {"only_to": "invoices@example.com"}, mailbox=await gmail(p))
    out = await p.run_now("gmail-intake", "me")
    assert out["status"] == "OK" and "1 new case" in out["message"], out
    assert rig.mailbox.queries == ["to:invoices@example.com has:attachment filename:pdf is:unread"]
    assert rig.mailbox.seen == ["7"]
    [reply] = rig.mailer.sent
    assert reply.to == ["accounts@technova.example"] and reply.in_reply_to == "<m-7@example.com>"
    assert "TN-1042.pdf: received" in reply.body and "notes.txt: not accepted" in reply.body
    await service.wait_all()
    async with db() as session:
        [run] = list(await session.scalars(select(Run)))
        assert run.source == "EMAIL" and run.intake_from == "accounts@technova.example"

    # Nothing new: skipped. The timer respects the interval.
    assert (await p.run_now("gmail-intake", "me"))["status"] == "SKIPPED"
    assert await p.tick() == 1 and await p.tick() == 0
    rig.clock.now += timedelta(minutes=1)
    assert await p.tick() == 1


async def test_daily_summary_fires_once_a_day_after_its_time(rig: Rig) -> None:
    p = rig.platform
    await activate(
        p,
        "daily-summary",
        {"recipients": "boss@example.com", "send_at": "10:00", "weekdays_only": True},
        sender=await gmail(p),
    )
    assert await p.tick() == 0  # 09:30 IST
    rig.clock.now += timedelta(minutes=45)  # 10:15 IST
    assert await p.tick() == 1 and await p.tick() == 0
    assert "boss@example.com" in rig.mailer.sent[0].to
    rig.clock.now += timedelta(days=1)  # Saturday
    assert await p.tick() == 0


# --- n8n: migration and inbound secret -----------------------------------------------------------


async def test_env_n8n_settings_move_into_the_connector_app(
    apps_api: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    service: RunService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("N8N_BASE_URL", "https://n8n.example.com")
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "env-secret-1234567890")
    get_settings.cache_clear()
    platform = AppPlatform(
        db,
        get_settings(),
        transports=Transports(http=httpx.MockTransport(lambda r: httpx.Response(200))),
        run_service=service,
    )
    apps_api._transport.app.state.apps = platform  # type: ignore[attr-defined]
    assert await platform.import_env_n8n() is True
    assert await platform.import_env_n8n() is False  # only once
    app = await platform.get_app("n8n-connector")
    assert app["install"]["state"] == "ACTIVE"
    assert await platform.n8n_intake_secret() == "env-secret-1234567890"

    # The intake route now checks the signature with the app's secret.
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    body = b'{"message_id": "m-1", "from": "a@b.c", "attachments": []}'
    headers = {"content-type": "application/json"}
    ok = await apps_api.post(
        "/intake/email",
        content=body,
        headers={**headers, **signed_headers("env-secret-1234567890", body)},
    )
    assert ok.status_code == 202, ok.text
    bad = await apps_api.post(
        "/intake/email", content=body, headers={**headers, **signed_headers("guess", body)}
    )
    assert bad.status_code == 401

    await platform.deactivate("n8n-connector", "me")
    off = await apps_api.post(
        "/intake/email",
        content=body,
        headers={**headers, **signed_headers("env-secret-1234567890", body)},
    )
    assert off.status_code == 503
