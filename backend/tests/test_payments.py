"""Payments: the payables queue and holds, proposals, release by a person, RazorpayX payouts,
signed webhooks, the in-flight sync and the payment advice email.

RazorpayX is a fake behind an httpx MockTransport; nothing leaves the machine.
"""

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.apps.context import Transports
from app.apps.platform import AppPlatform
from app.config import get_settings
from app.db.models import Approval, ErpDocument, Payment, Vendor
from app.graph.runner import RunService
from app.payments import Payments, choose_mode, narration, next_payment_day
from tests.test_apps import APP_PASSWORD, FakeMailbox, FakeMailer, activate, secrets_key  # noqa: F401
from tests.test_graph import api, db, run_sample, service  # noqa: F401  (fixtures)

KEY_ID, KEY_SECRET, HOOK_SECRET = "rzp_test_abc123", "sekret-key-0001", "hook-secret-0001"
ACCOUNT = "2323230041626905"
ACTOR = {"X-Actor": "Sammitha S Poojary"}


@dataclass
class FakeRazorpayX:
    """Just enough of RazorpayX Payouts: contacts, fund accounts, idempotent payouts."""

    requests: list[httpx.Request] = field(default_factory=list)
    payouts: dict[str, dict[str, Any]] = field(default_factory=dict)  # idempotency key → payout
    payout_error: str | None = None
    drop_next_payout: bool = False
    status_on_get: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Basic "):
            return httpx.Response(401, json={"error": {"description": "auth missing"}})
        path, method = request.url.path.removeprefix("/v1"), request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/transactions":
            return httpx.Response(200, json={"items": []})
        if path == "/contacts" and method == "POST":
            return httpx.Response(200, json={"id": f"cont_{body['reference_id']}"})
        if path == "/fund_accounts" and method == "POST":
            return httpx.Response(200, json={"id": f"fa_{body['contact_id']}"})
        if path == "/payouts" and method == "POST":
            if self.drop_next_payout:
                self.drop_next_payout = False
                raise httpx.ConnectError("connection reset")
            if self.payout_error:
                return httpx.Response(400, json={"error": {"description": self.payout_error}})
            key = request.headers["X-Payout-Idempotency"]
            if key not in self.payouts:
                self.payouts[key] = {
                    "id": f"pout_{len(self.payouts) + 1:04d}",
                    "status": "processing",
                    "utr": None,
                    "reference_id": body["reference_id"],
                    "amount": body["amount"],
                    "mode": body["mode"],
                }
            return httpx.Response(200, json=self.payouts[key])
        if path.startswith("/payouts/") and method == "GET":
            pid = path.rsplit("/", 1)[-1]
            payout = next(p for p in self.payouts.values() if p["id"] == pid)
            if self.status_on_get:
                payout = {**payout, "status": self.status_on_get, "utr": "UTR0000GET01"}
            return httpx.Response(200, json=payout)
        return httpx.Response(404, json={"error": {"description": f"no route {path}"}})

    def posted(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST" and r.url.path.endswith(path)]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2027, 1, 1, 4, 0)  # 09:30 IST, a Friday (a payment day)

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Rig:
    platform: AppPlatform
    payments: Payments
    rzp: FakeRazorpayX
    mailer: FakeMailer
    clock: Clock


@pytest.fixture
def rig(
    db: async_sessionmaker[AsyncSession],
    service: RunService,
    secrets_key: str,
) -> Rig:
    rzp, mailer, clock = FakeRazorpayX(), FakeMailer(), Clock()
    transports = Transports(
        mailer=lambda v: mailer,
        mailbox=lambda v: FakeMailbox(),
        http=httpx.MockTransport(rzp.handler),
    )
    platform = AppPlatform(
        db, get_settings(), transports=transports, run_service=service, clock=clock
    )
    service.on_event = platform.emit
    return Rig(platform, Payments(db, clock=clock), rzp, mailer, clock)


@pytest.fixture
async def pay_api(api: AsyncClient, rig: Rig) -> AsyncIterator[AsyncClient]:
    state = api._transport.app.state  # type: ignore[attr-defined]
    state.apps, state.payments = rig.platform, rig.payments
    yield api
    await rig.platform.drain()


async def razorpayx_app(rig: Rig, **inputs: Any) -> None:
    conn = await rig.platform.create_connection(
        "razorpayx",
        "RazorpayX test",
        {"account_number": ACCOUNT},
        {"key_id": KEY_ID, "key_secret": KEY_SECRET, "webhook_secret": HOOK_SECRET},
        "me",
    )
    await activate(rig.platform, "razorpayx-payouts", inputs, razorpayx=int(conn["id"]))


async def advice_app(rig: Rig) -> None:
    conn = await rig.platform.create_connection(
        "gmail", "AP", {"address": "ap@example.com"}, {"app_password": APP_PASSWORD}, "me"
    )
    await activate(
        rig.platform, "payment-advice", {"demo_inbox": "demo@example.com"}, sender=int(conn["id"])
    )


def signed(payload: dict[str, Any], secret: str = HOOK_SECRET) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Razorpay-Signature": sig, "content-type": "application/json"}


def payout_event(event: str, payout: dict[str, Any], **changes: Any) -> dict[str, Any]:
    entity = {**payout, **changes}
    return {"entity": "event", "event": event, "payload": {"payout": {"entity": entity}}}


async def doc_for(db: async_sessionmaker[AsyncSession], run_id: str) -> ErpDocument:
    async with db() as session:
        doc = await session.scalar(select(ErpDocument).where(ErpDocument.idempotency_key == run_id))
        assert doc is not None
        return doc


# --- small pieces ------------------------------------------------------------------------------


def test_payment_days_modes_and_narration() -> None:
    friday = date(2027, 1, 1)
    assert next_payment_day(friday, ["tue", "fri"]) == date(2027, 1, 5)
    assert next_payment_day(date(2027, 1, 4), ["tue", "fri"]) == date(2027, 1, 5)
    assert next_payment_day(friday, []) == date(2027, 1, 5)  # policy default
    assert choose_mode(Decimal("499999"), "AUTO") == "IMPS"
    assert choose_mode(Decimal("600000"), "AUTO") == "NEFT"
    assert choose_mode(Decimal("600000"), "IMPS") == "NEFT"  # over the IMPS limit
    assert choose_mode(Decimal("100"), "RTGS") == "RTGS"
    assert narration("NW/2026-11/0042") == "Sinoprex NW 2026 11 0042"
    assert len(narration("X" * 50)) == 30


# --- the full flow -------------------------------------------------------------------------------


async def test_propose_release_and_webhook_pay_the_invoice(
    rig: Rig,
    pay_api: AsyncClient,
    service: RunService,
    db: async_sessionmaker[AsyncSession],
) -> None:
    run = await run_sample(service, "northwind-clean")
    assert run.final_decision == "APPROVED"
    doc = await doc_for(db, run.id)

    # Without the app, the queue still shows the invoice as due; proposing needs the app.
    overview = (await pay_api.get("/payments")).json()
    [item] = overview["queue"]
    assert item["state"] == "DUE" and item["doc_number"] == doc.doc_number
    assert overview["app_active"] is False and overview["next_payment_day"] == "2027-01-05"
    assert (await pay_api.post("/payments/propose")).status_code == 409

    await razorpayx_app(rig)
    await advice_app(rig)
    out = await pay_api.post("/payments/propose", headers=ACTOR)
    assert out.status_code == 200 and "Proposed 1 payment" in out.json()["message"]
    assert not rig.rzp.posted("/payouts"), "a proposal sends nothing"
    overview = (await pay_api.get("/payments")).json()
    [batch] = overview["batches"]
    assert batch["status"] == "PROPOSED" and overview["queue"][0]["state"] == "PROPOSED"

    # Release needs a named person (policy §1.3), then the payout is sent once.
    url = f"/payments/batches/{batch['batch_id']}/release"
    assert (await pay_api.post(url)).status_code == 422
    out = await pay_api.post(url, headers=ACTOR)
    assert out.status_code == 200, out.text
    assert "by Sammitha S Poojary" in out.json()["message"]
    [contact] = rig.rzp.posted("/contacts")
    assert json.loads(contact.content)["reference_id"] == "V008"
    [fund] = rig.rzp.posted("/fund_accounts")
    async with db() as session:
        vendor = await session.get(Vendor, "V008")
        assert vendor is not None
    assert json.loads(fund.content)["bank_account"]["account_number"] == vendor.bank_account_number
    [payout_req] = rig.rzp.posted("/payouts")
    body = json.loads(payout_req.content)
    assert body["amount"] == int(doc.amount * 100) and body["account_number"] == ACCOUNT
    assert body["purpose"] == "vendor bill" and body["mode"] == "IMPS"
    assert payout_req.headers["X-Payout-Idempotency"] == f"{doc.doc_number}:1"
    assert (await pay_api.post(url, headers=ACTOR)).status_code == 502  # nothing left

    async with db() as session:
        [p] = list(await session.scalars(select(Payment)))
    assert p.status == "PROCESSING" and p.released_by == "Sammitha S Poojary"
    assert p.payout_id == "pout_0001"

    # RazorpayX reports the result: a forged webhook is refused, a signed one pays.
    payout = next(iter(rig.rzp.payouts.values()))
    event = payout_event("payout.processed", payout, status="processed", utr="UTR123456789")
    body_bytes, headers = signed(event, "wrong-secret")
    res = await pay_api.post("/webhooks/razorpayx", content=body_bytes, headers=headers)
    assert res.status_code == 401
    body_bytes, headers = signed(event)
    hook = await pay_api.post(
        "/webhooks/razorpayx", content=body_bytes, headers={**headers, "X-API-Key": ""}
    )
    assert hook.status_code == 200 and hook.json() == {"updated": 1}
    again = await pay_api.post("/webhooks/razorpayx", content=body_bytes, headers=headers)
    assert again.json() == {"updated": 0}  # redelivery changes nothing
    await rig.platform.drain()

    paid = await doc_for(db, run.id)
    assert paid.status == "PAID" and paid.utr == "UTR123456789" and paid.paid_at is not None
    overview = (await pay_api.get("/payments")).json()
    assert overview["queue"][0]["state"] == "PAID"
    records = (await pay_api.get(f"/runs/{run.id}/records")).json()
    assert records["payments"][0]["utr"] == "UTR123456789"
    ledger = (await pay_api.get("/erp/documents")).json()
    assert ledger[0]["document"]["status"] == "PAID" and ledger[0]["verified"] is True

    [advice] = rig.mailer.sent
    assert advice.to == ["demo@example.com"] and "UTR123456789" in advice.body
    assert "would go to accounts@northwind.example" in advice.body

    stats = (await pay_api.get("/stats")).json()
    assert stats["payments_paid"] == 1 and stats["payments_on_time_rate"] is not None


async def test_holds_keep_invoices_out_until_cleared(
    rig: Rig,
    service: RunService,
    db: async_sessionmaker[AsyncSession],
) -> None:
    run = await run_sample(service, "northwind-clean")
    doc = await doc_for(db, run.id)
    async with db() as session:
        session.add(
            Approval(
                run_id=run.id,
                level=9,
                kind="BANK_CHANGE",
                role="AP Manager",
                reason="bank changed",
                decision="APPROVED",
            )
        )
        await session.commit()
    [item], _ = await rig.payments.queue(["tue", "fri"])
    assert item.state == "HELD" and item.releasable and "§6.2" in item.holds[0]
    batch, rows = await rig.payments.propose(["tue", "fri"])
    assert batch is None and rows == []

    await rig.payments.release_hold(doc.doc_number, "Finance Controller")
    [item], _ = await rig.payments.queue(["tue", "fri"])
    assert item.state == "DUE"

    # A blocked vendor is a hold no one can release from here.
    async with db() as session:
        vendor = await session.get(Vendor, doc.vendor_id)
        assert vendor is not None
        vendor.status = "BLOCKED"
        await session.commit()
    [item], _ = await rig.payments.queue(["tue", "fri"])
    assert item.state == "HELD" and not item.releasable


async def test_failed_payout_returns_with_reason_and_retries_with_a_new_key(
    rig: Rig,
    service: RunService,
) -> None:
    await run_sample(service, "northwind-clean")
    await razorpayx_app(rig)
    rig.rzp.payout_error = "Insufficient balance in the account"
    batch, _ = await rig.payments.propose(["tue", "fri"])
    assert batch
    out = await rig.platform.execute(
        "razorpayx-payouts", "release", {"batch_id": batch, "actor": "AP Manager"}
    )
    assert out["status"] == "OK" and "1 failed" in out["message"]
    [item], _ = await rig.payments.queue(["tue", "fri"])
    assert item.state == "FAILED" and item.payment is not None
    assert "Insufficient balance" in (item.payment.error or "")
    assert (await rig.payments.propose(["tue", "fri"]))[0] is None  # not retried on its own

    await rig.payments.approve_retry(item.payment.id, "AP Manager")
    rig.rzp.payout_error = None
    batch, [retry] = await rig.payments.propose(["tue", "fri"])
    assert batch and retry.attempt == 2 and retry.idempotency_key.endswith(":2")
    await rig.platform.execute(
        "razorpayx-payouts", "release", {"batch_id": batch, "actor": "AP Manager"}
    )
    [item], _ = await rig.payments.queue(["tue", "fri"])
    assert item.state == "IN_FLIGHT" and item.attempts == 2


async def test_sync_resends_unconfirmed_payouts_and_catches_missed_webhooks(
    rig: Rig,
    service: RunService,
    db: async_sessionmaker[AsyncSession],
) -> None:
    run = await run_sample(service, "northwind-clean")
    await razorpayx_app(rig)
    rig.rzp.drop_next_payout = True  # the request dies on the way: did RazorpayX get it?
    batch, _ = await rig.payments.propose(["tue", "fri"])
    assert batch
    await rig.platform.execute(
        "razorpayx-payouts", "release", {"batch_id": batch, "actor": "AP Manager"}
    )
    async with db() as session:
        [p] = list(await session.scalars(select(Payment)))
    assert p.status == "QUEUED" and p.payout_id is None and "will retry" in (p.error or "")

    # The half-hourly sync re-sends with the same idempotency key …
    out = await rig.platform.execute("razorpayx-payouts", "sync", {"timer": "sync"})
    assert out["status"] == "SKIPPED"
    keys = [r.headers["X-Payout-Idempotency"] for r in rig.rzp.posted("/payouts")]
    assert keys == [p.idempotency_key, p.idempotency_key] and len(rig.rzp.payouts) == 1

    # … and asks for the status of payouts in flight, in case a webhook never came.
    rig.rzp.status_on_get = "processed"
    out = await rig.platform.execute("razorpayx-payouts", "sync", {"timer": "sync"})
    assert out["status"] == "OK" and "1 payout(s) processed" in out["message"]
    doc = await doc_for(db, run.id)
    assert doc.status == "PAID" and doc.utr == "UTR0000GET01"


async def test_proposal_timer_runs_only_on_payment_days(
    rig: Rig,
    service: RunService,
) -> None:
    await run_sample(service, "northwind-clean")
    await razorpayx_app(rig, payment_days=["tue"], propose_at="09:00")
    assert await rig.platform.tick() == 2  # Friday: proposal skipped, sync ran
    runs = (await rig.platform.get_app("razorpayx-payouts"))["runs"]
    assert {(r["trigger"], r["status"]) for r in runs} == {
        ("propose", "SKIPPED"),
        ("sync", "SKIPPED"),
    }
    out = await rig.platform.run_now("razorpayx-payouts", "me")  # "Run now" ignores the day
    assert out["status"] == "OK" and "Proposed 1" in out["message"]


async def test_connection_test_checks_the_key_and_the_account(rig: Rig) -> None:
    conn = await rig.platform.create_connection(
        "razorpayx",
        "",
        {"account_number": ACCOUNT},
        {"key_id": "not-a-key", "key_secret": "x", "webhook_secret": "y"},
        "me",
    )
    out = await rig.platform.test_connection(int(conn["id"]), "me")
    assert out["status"] == "ERROR" and "rzp_test_" in out["status_message"]
    await rig.platform.update_connection(
        int(conn["id"]), name=None, fields=None, secrets={"key_id": KEY_ID}, actor="me"
    )
    out = await rig.platform.test_connection(int(conn["id"]), "me")
    assert out["status"] == "OK" and "test mode" in out["status_message"]
    assert "6905" in out["status_message"]
