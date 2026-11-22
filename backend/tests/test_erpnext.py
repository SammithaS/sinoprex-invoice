"""ERPNext Connector: master-data sync, posting + read-back through ERPNext, idempotency,
read-back mismatches, Payment Entries for payouts, and falling back to the mock ERP.

ERPNext is an in-memory fake (tests/fake_erpnext.py) behind an httpx MockTransport.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.apps.builtin.erpnext_connector import active_erpnext
from app.apps.context import Transports
from app.apps.platform import AppPlatform
from app.config import get_settings
from app.db.models import ErpDocument, ErpPosting
from app.erp import ErpDocumentIn, ErpProvider, RoutingErp
from app.graph.runner import RunService
from app.payments import Payments
from tests.fake_erpnext import ABBR, BASE, COMPANY, FakeErpNext
from tests.test_apps import FakeMailbox, FakeMailer, activate, secrets_key  # noqa: F401
from tests.test_graph import db, make_service, reload, run_sample  # noqa: F401
from tests.test_payments import FakeRazorpayX, razorpayx_app, signed


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2027, 1, 1, 4, 0)

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Rig:
    platform: AppPlatform
    service: RunService
    erp: FakeErpNext
    rzp: FakeRazorpayX
    payments: Payments


@pytest.fixture
def rig(
    db: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    secrets_key: str,
) -> Rig:
    erp, rzp, clock = FakeErpNext(), FakeRazorpayX(), Clock()

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.razorpay.com":
            return rzp.handler(request)
        return erp.handler(request)

    transports = Transports(
        mailer=lambda v: FakeMailer(),
        mailbox=lambda v: FakeMailbox(),
        http=httpx.MockTransport(route),
    )
    holder: dict[str, AppPlatform] = {}

    async def external() -> ErpProvider | None:
        return await active_erpnext(holder["platform"])

    service = make_service(db, tmp_path, erp=RoutingErp(db, external))
    platform = AppPlatform(
        db, get_settings(), transports=transports, run_service=service, clock=clock
    )
    holder["platform"] = platform
    service.on_event = platform.emit
    return Rig(platform, service, erp, rzp, Payments(db, clock=clock))


async def connector(rig: Rig, secret: str = "secret-456") -> None:
    conn = await rig.platform.create_connection(
        "erpnext",
        "ERPNext",
        {"base_url": BASE, "company": COMPANY},
        {"api_key": "key-123", "api_secret": secret},
        "me",
    )
    await activate(rig.platform, "erpnext-connector", {}, erpnext=int(conn["id"]))
    out = await rig.platform.run_now("erpnext-connector", "me")
    assert out["status"] == "OK", out


async def test_master_sync_creates_what_postings_need_once(rig: Rig) -> None:
    await connector(rig)
    erp = rig.erp
    assert len(erp.all("Supplier")) == 10
    assert all(s["tax_id"] and s["supplier_group"] == "Services" for s in erp.all("Supplier"))
    assert f"CC-IT-01 - {ABBR}" in erp.docs["Cost Center"]
    numbers = {a.get("account_number") for a in erp.all("Account")}
    assert {"5110", "5120", "6420", "5999"} <= numbers
    assert f"Input GST - {ABBR}" in erp.docs["Account"]
    assert "SINOPREX-AP" in erp.docs["Item"]
    again = await rig.platform.run_now("erpnext-connector", "me")
    assert again["status"] == "SKIPPED"


async def test_connection_test_needs_a_working_key_and_company(rig: Rig) -> None:
    conn = await rig.platform.create_connection(
        "erpnext",
        "",
        {"base_url": BASE, "company": COMPANY},
        {"api_key": "key-123", "api_secret": "wrong"},
        "me",
    )
    out = await rig.platform.test_connection(int(conn["id"]), "me")
    assert out["status"] == "ERROR" and "bad token" in out["status_message"]
    assert "wrong" not in out["status_message"]
    await rig.platform.update_connection(
        int(conn["id"]), name=None, fields=None, secrets={"api_secret": "secret-456"}, actor="me"
    )
    out = await rig.platform.test_connection(int(conn["id"]), "me")
    assert out["status"] == "OK" and COMPANY in out["status_message"]


async def test_approved_invoice_is_posted_to_erpnext_and_read_back(
    rig: Rig, db: async_sessionmaker[AsyncSession]
) -> None:
    await connector(rig)
    run = await run_sample(rig.service, "northwind-clean")
    assert run.status == "COMPLETED" and run.final_decision == "APPROVED", run.error

    [pi] = rig.erp.all("Purchase Invoice")
    assert pi["name"] == "ACC-PINV-2026-00001" and pi["docstatus"] == 1
    assert pi["bill_no"] == run.invoice_number and pi["company"] == COMPANY
    assert f"Sinoprex case {run.id}" in pi["remarks"]
    assert float(pi["grand_total"]) == float(run.total or 0)
    assert pi["items"][0]["cost_center"].endswith(f" - {ABBR}")
    supplier = rig.erp.docs["Supplier"][pi["supplier"]]
    [attachment] = rig.erp.all("File")
    assert attachment["attached_to_name"] == pi["name"]
    assert attachment["file_url"].endswith(f"/runs/{run.id}/file")

    async with db() as session:
        doc = await session.scalar(select(ErpDocument))
        posting = await session.scalar(select(ErpPosting))
    assert doc is not None and posting is not None
    assert doc.system == "erpnext" and doc.doc_number == pi["name"]
    assert posting.verified is True, posting.mismatches
    assert doc.vendor_id == "V008" and supplier["tax_id"]

    # A retried post finds the same document instead of creating another.
    erp = await active_erpnext(rig.platform)
    assert erp is not None
    request = ErpDocumentIn.model_validate(posting.request)
    again = await erp.post(request)
    assert again.created is False and again.doc_number == pi["name"]
    assert len(rig.erp.all("Purchase Invoice")) == 1


async def test_a_wrong_total_in_erpnext_keeps_the_case_open(rig: Rig) -> None:
    await connector(rig)
    rig.erp.shift_grand_total = -0.01
    run = await run_sample(rig.service, "northwind-clean")
    assert run.status == "WAITING_REVIEW", run.error
    assert run.review_request and run.review_request.get("kind") == "erp"
    assert "posted amount" in json.dumps(run.review_request)


async def test_payout_records_a_payment_entry_in_erpnext(
    rig: Rig, db: async_sessionmaker[AsyncSession]
) -> None:
    await connector(rig)
    await razorpayx_app(rig)  # type: ignore[arg-type]
    await run_sample(rig.service, "northwind-clean")
    batch, _ = await rig.payments.propose(["tue", "fri"])
    assert batch
    await rig.platform.execute(
        "razorpayx-payouts", "release", {"batch_id": batch, "actor": "AP Manager"}
    )
    payout = next(iter(rig.rzp.payouts.values()))
    body, _ = signed(
        {
            "event": "payout.processed",
            "payload": {"payout": {"entity": {**payout, "status": "processed", "utr": "UTR777"}}},
        }
    )
    for event, data in await rig.payments.webhook(body):
        rig.platform.emit(event, data)
    await rig.platform.drain()

    [entry] = rig.erp.all("Payment Entry")
    assert entry["reference_no"] == "UTR777" and entry["docstatus"] == 1
    assert entry["references"][0]["reference_name"] == "ACC-PINV-2026-00001"
    assert rig.erp.docs["Purchase Invoice"]["ACC-PINV-2026-00001"]["status"] == "Paid"
    erp = await active_erpnext(rig.platform)
    assert erp is not None
    read = await erp.read("ACC-PINV-2026-00001")
    assert read is not None and read.status == "PAID"
    runs = (await rig.platform.get_app("erpnext-connector"))["runs"]
    assert runs[0]["trigger"] == "payment.processed" and runs[0]["status"] == "OK"

    # The same payment again (a redelivered event) adds no second entry.
    async with db() as session:
        doc = await session.scalar(select(ErpDocument))
    assert doc is not None and doc.status == "PAID"
    assert doc.paid_at is not None
    assert await erp.pay(doc.doc_number, "UTR777", doc.paid_at.date()) == entry["name"]
    assert len(rig.erp.all("Payment Entry")) == 1


async def test_paused_connector_falls_back_to_the_mock_erp(
    rig: Rig, db: async_sessionmaker[AsyncSession]
) -> None:
    await connector(rig)
    await rig.platform.deactivate("erpnext-connector", "me")
    run = await run_sample(rig.service, "northwind-clean")
    assert run.final_decision == "APPROVED"
    assert rig.erp.all("Purchase Invoice") == []
    async with db() as session:
        doc = await session.scalar(select(ErpDocument))
    assert doc is not None and doc.system == "mock" and doc.doc_number.startswith("AP-2026-")
