"""Paying posted invoices: the payables queue, payment proposals, RazorpayX payouts, webhooks.

Flow (policy §10.2, §1.3, §6.2):

1. The queue lists every posted invoice with its due date (invoice date + the vendor's payment
   terms) and whether it can be paid: a hold explains why not (vendor blocked, no bank account
   on file, ERP write not verified, open exception or approval, bank details changed).
2. On a payment day the payment run *proposes* a batch: everything due before the next payment
   day, so nothing is paid late. Automation never releases money (§1.3): a named person
   releases the batch, and only then are payouts sent.
3. Each payout goes to RazorpayX with an idempotency key, so a retried request never pays twice.
   RazorpayX reports the result by webhook (HMAC-signed); a periodic sync asks for the status
   of anything still in flight, in case a webhook was missed.
4. A processed payout marks the ERP document PAID with the bank reference (UTR); a failure
   goes back to the queue with the reason, to be retried after someone looks at it.
"""

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Approval, CaseException, ErpDocument, ErpPosting, Payment, Run, Vendor

IST = ZoneInfo("Asia/Kolkata")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
IMPS_LIMIT = Decimal("500000")  # IMPS takes up to ₹5 lakh per transfer; above that, NEFT
OPEN = ("QUEUED", "PROCESSING")  # released and not finished
ACTIVE = ("PROPOSED", *OPEN, "PAID")  # an invoice with one of these is not proposed again

# RazorpayX payout status → ours.
_STATUS = {
    "processed": "PAID",
    "failed": "FAILED",
    "rejected": "FAILED",
    "cancelled": "FAILED",
    "reversed": "REVERSED",
}


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def today_ist(now: datetime) -> date:
    return now.replace(tzinfo=UTC).astimezone(IST).date()


def next_payment_day(after: date, days: list[str]) -> date:
    """The first payment day strictly after `after` (Tuesdays and Fridays by default)."""
    wanted = {WEEKDAYS.index(d) for d in days if d in WEEKDAYS} or {1, 4}
    d = after + timedelta(days=1)
    while d.weekday() not in wanted:
        d += timedelta(days=1)
    return d


def choose_mode(amount: Decimal, preferred: str) -> str:
    if preferred in ("IMPS", "NEFT", "RTGS"):
        return "NEFT" if preferred == "IMPS" and amount > IMPS_LIMIT else preferred
    return "IMPS" if amount <= IMPS_LIMIT else "NEFT"


# --- the payables queue ------------------------------------------------------------------------


@dataclass
class QueueItem:
    doc_number: str
    system: str
    run_id: str | None
    vendor_id: str
    vendor_name: str | None
    invoice_number: str
    invoice_date: date
    amount: Decimal
    due_date: date
    # DUE (in the next run) | SCHEDULED (later) | HELD | PROPOSED | IN_FLIGHT | PAID | FAILED
    state: str
    holds: list[str] = field(default_factory=list)
    releasable: bool = False  # only the bank-change hold can be released by a person
    payment: Payment | None = None  # latest attempt
    attempts: int = 0
    utr: str | None = None
    paid_at: datetime | None = None


BANK_HOLD = (
    "bank details on the invoice differ from the vendor master (ap-policy §6.2): confirm with "
    "the vendor on the contact details on file and update the master, then release"
)


async def build_queue(session: AsyncSession, today: date, next_run: date) -> list[QueueItem]:
    docs = list(await session.scalars(select(ErpDocument).order_by(ErpDocument.id)))
    if not docs:
        return []
    run_ids = [d.idempotency_key for d in docs]
    vendors = {v.id: v for v in await session.scalars(select(Vendor))}
    runs = {r.id: r for r in await session.scalars(select(Run).where(Run.id.in_(run_ids)))}
    verified = {
        p.run_id: p.verified
        for p in await session.scalars(select(ErpPosting).where(ErpPosting.run_id.in_(run_ids)))
    }
    open_exc: dict[str, list[str]] = {}
    for e in await session.scalars(
        select(CaseException).where(
            CaseException.run_id.in_(run_ids), CaseException.status == "OPEN"
        )
    ):
        open_exc.setdefault(e.run_id, []).append(e.category)
    pending: dict[str, int] = {}
    bank_change: set[str] = set()
    for a in await session.scalars(select(Approval).where(Approval.run_id.in_(run_ids))):
        if a.decision == "PENDING":
            pending[a.run_id] = pending.get(a.run_id, 0) + 1
        if a.kind == "BANK_CHANGE":
            bank_change.add(a.run_id)
    payments: dict[str, list[Payment]] = {}
    for p in await session.scalars(select(Payment).order_by(Payment.attempt)):
        payments.setdefault(p.doc_number, []).append(p)

    out: list[QueueItem] = []
    for d in docs:
        vendor = vendors.get(d.vendor_id)
        terms = vendor.payment_terms_days if vendor else 30
        attempts = [p for p in payments.get(d.doc_number, []) if p.status != "CANCELLED"]
        latest = attempts[-1] if attempts else None
        item = QueueItem(
            doc_number=d.doc_number,
            system=d.system,
            run_id=d.idempotency_key if d.idempotency_key in runs else None,
            vendor_id=d.vendor_id,
            vendor_name=vendor.name if vendor else None,
            invoice_number=d.invoice_number,
            invoice_date=d.invoice_date,
            amount=d.amount,
            due_date=d.invoice_date + timedelta(days=terms),
            state="SCHEDULED",
            payment=latest,
            attempts=len(attempts),
            utr=d.utr,
            paid_at=d.paid_at,
        )
        out.append(item)
        if d.status == "PAID" or (latest and latest.status == "PAID"):
            item.state = "PAID"
            continue
        if latest and latest.status in OPEN:
            item.state = "IN_FLIGHT"
            continue
        if latest and latest.status == "PROPOSED":
            item.state = "PROPOSED"
            continue

        run = runs.get(d.idempotency_key)
        if vendor is None or vendor.status != "ACTIVE":
            item.holds.append("vendor is blocked or not in the vendor master")
        elif not (vendor.bank_account_number and vendor.bank_ifsc):
            item.holds.append("no bank account on file for the vendor")
        if run is not None and run.final_decision != "APPROVED":
            item.holds.append(f"case decision is {run.final_decision or 'not final'}")
        if run is not None and verified.get(run.id) is not True:
            item.holds.append("the ERP posting is not verified by read-back yet")
        for category in open_exc.get(d.idempotency_key, []):
            item.holds.append(f"open exception: {category}")
        if pending.get(d.idempotency_key):
            item.holds.append("approvals still pending")
        if d.idempotency_key in bank_change and not d.hold_released_by:
            item.holds.append(BANK_HOLD)
            item.releasable = len(item.holds) == 1

        if latest and latest.status in ("FAILED", "REVERSED") and not latest.retry_approved_by:
            item.state = "FAILED"
        elif item.holds:
            item.state = "HELD"
        elif item.due_date < next_run:
            item.state = "DUE"
    return out


# --- RazorpayX ---------------------------------------------------------------------------------


class PayoutError(RuntimeError):
    """RazorpayX refused a request (bad credentials, validation, insufficient balance …)."""


class PayoutGateway(Protocol):
    async def check(self) -> str: ...
    async def create_contact(self, vendor: Vendor) -> str: ...
    async def create_fund_account(self, contact_id: str, vendor: Vendor) -> str: ...
    async def create_payout(
        self,
        *,
        fund_account_id: str,
        amount: Decimal,
        mode: str,
        reference_id: str,
        narration: str,
        notes: dict[str, str],
        idempotency_key: str,
    ) -> dict[str, Any]: ...
    async def get_payout(self, payout_id: str) -> dict[str, Any]: ...


class RazorpayX:
    """The few RazorpayX Payouts calls a vendor payment needs (test or live keys alike)."""

    BASE = "https://api.razorpay.com/v1"

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        account_number: str,
        client: Callable[[], httpx.AsyncClient],
    ) -> None:
        self.auth = (key_id, key_secret)
        self.account_number = account_number
        self.client = client

    async def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self.client() as c:
            res = await c.request(
                method,
                f"{self.BASE}{path}",
                json=body,
                params=params,
                headers=headers,
                auth=self.auth,
            )
        try:
            data: dict[str, Any] = res.json()
        except ValueError:
            data = {}
        if res.status_code >= 400:
            err = data.get("error") or {}
            reason = err.get("description") or res.text[:200] or f"HTTP {res.status_code}"
            raise PayoutError(f"RazorpayX: {reason}")
        return data

    async def check(self) -> str:
        await self._call(
            "GET", "/transactions", params={"account_number": self.account_number, "count": 1}
        )
        mode = "test" if self.auth[0].startswith("rzp_test_") else "live"
        return f"Connected to RazorpayX ({mode} mode), account ••••{self.account_number[-4:]}"

    async def create_contact(self, vendor: Vendor) -> str:
        data = await self._call(
            "POST",
            "/contacts",
            {
                "name": vendor.legal_name,
                "email": vendor.email,
                "type": "vendor",
                "reference_id": vendor.id,
                "notes": {"gstin": vendor.gstin},
            },
        )
        return str(data["id"])

    async def create_fund_account(self, contact_id: str, vendor: Vendor) -> str:
        data = await self._call(
            "POST",
            "/fund_accounts",
            {
                "contact_id": contact_id,
                "account_type": "bank_account",
                "bank_account": {
                    "name": vendor.legal_name,
                    "ifsc": vendor.bank_ifsc,
                    "account_number": vendor.bank_account_number,
                },
            },
        )
        return str(data["id"])

    async def create_payout(
        self,
        *,
        fund_account_id: str,
        amount: Decimal,
        mode: str,
        reference_id: str,
        narration: str,
        notes: dict[str, str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._call(
            "POST",
            "/payouts",
            {
                "account_number": self.account_number,
                "fund_account_id": fund_account_id,
                "amount": int((amount * 100).to_integral_value()),  # paise
                "currency": "INR",
                "mode": mode,
                "purpose": "vendor bill",
                "queue_if_low_balance": True,
                "reference_id": reference_id[:40],
                "narration": narration,
                "notes": notes,
            },
            headers={"X-Payout-Idempotency": idempotency_key},
        )

    async def get_payout(self, payout_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/payouts/{payout_id}")


def narration(invoice_number: str) -> str:
    """RazorpayX narrations: up to 30 letters, digits and spaces."""
    clean = re.sub(r"[^A-Za-z0-9 ]", " ", f"Sinoprex {invoice_number}")
    return re.sub(r"\s+", " ", clean).strip()[:30]


def _account_key(vendor: Vendor) -> str:
    """Changes when the vendor's bank details change, so a new fund account is created."""
    raw = f"{vendor.bank_ifsc}:{vendor.bank_account_number}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# --- proposals, release, status updates --------------------------------------------------------

Event = tuple[str, dict[str, Any]]


@dataclass
class Payments:
    sessionmaker: async_sessionmaker[AsyncSession]
    clock: Callable[[], datetime] = now_utc

    async def queue(self, days: list[str]) -> tuple[list[QueueItem], date]:
        today = today_ist(self.clock())
        next_run = next_payment_day(today, days)
        async with self.sessionmaker() as session:
            return await build_queue(session, today, next_run), next_run

    async def propose(self, days: list[str], limit: int = 100) -> tuple[str | None, list[Payment]]:
        """Put everything due before the next payment day into a new batch (nothing is sent)."""
        today = today_ist(self.clock())
        next_run = next_payment_day(today, days)
        batch = f"PR-{today:%Y%m%d}-{uuid.uuid4().hex[:6]}"
        async with self.sessionmaker() as session:
            due = [i for i in await build_queue(session, today, next_run) if i.state == "DUE"]
            due.sort(key=lambda i: (i.due_date, i.doc_number))
            rows: list[Payment] = []
            for item in due[:limit]:
                attempt = item.attempts + 1
                rows.append(
                    Payment(
                        doc_number=item.doc_number,
                        run_id=item.run_id,
                        batch_id=batch,
                        attempt=attempt,
                        vendor_id=item.vendor_id,
                        amount=item.amount,
                        due_date=item.due_date,
                        mode="AUTO",
                        status="PROPOSED",
                        idempotency_key=f"{item.doc_number}:{attempt}",
                        updated_at=self.clock(),
                    )
                )
            session.add_all(rows)
            await session.commit()
        return (batch if rows else None), rows

    async def cancel(self, payment_id: int) -> Payment:
        async with self.sessionmaker() as session:
            p = await session.get(Payment, payment_id)
            if p is None:
                raise LookupError(f"payment {payment_id} not found")
            if p.status != "PROPOSED":
                raise ValueError(f"only a proposed payment can be removed (this one is {p.status})")
            p.status, p.updated_at = "CANCELLED", self.clock()
            # Free the attempt number and key for the next proposal.
            p.idempotency_key = f"{p.idempotency_key}:cancelled:{p.id}"
            p.attempt = -p.id
            await session.commit()
            return p

    async def approve_retry(self, payment_id: int, actor: str) -> Payment:
        async with self.sessionmaker() as session:
            p = await session.get(Payment, payment_id)
            if p is None:
                raise LookupError(f"payment {payment_id} not found")
            if p.status not in ("FAILED", "REVERSED"):
                raise ValueError("only a failed or reversed payment can be retried")
            p.retry_approved_by, p.updated_at = actor, self.clock()
            await session.commit()
            return p

    async def release_hold(self, doc_number: str, actor: str) -> None:
        async with self.sessionmaker() as session:
            doc = await session.scalar(
                select(ErpDocument).where(ErpDocument.doc_number == doc_number)
            )
            if doc is None:
                raise LookupError(f"document {doc_number} not found")
            doc.hold_released_by, doc.hold_released_at = actor, self.clock()
            await session.commit()

    async def release(
        self,
        batch_id: str,
        actor: str,
        gateway: PayoutGateway,
        *,
        mode: str = "AUTO",
        beneficiaries: dict[str, Any] | None = None,
    ) -> tuple[list[Payment], list[Event]]:
        """A person releases a proposed batch: send each payout (idempotently)."""
        beneficiaries = beneficiaries if beneficiaries is not None else {}
        async with self.sessionmaker() as session:
            rows = list(
                await session.scalars(
                    select(Payment)
                    .where(Payment.batch_id == batch_id, Payment.status == "PROPOSED")
                    .order_by(Payment.id)
                )
            )
            if not rows:
                raise ValueError(f"nothing to release in {batch_id}")
            for p in rows:
                p.status, p.released_by, p.released_at = "QUEUED", actor, self.clock()
                p.mode = choose_mode(p.amount, mode)
            await session.commit()
        events: list[Event] = []
        for p in rows:
            events += await self._send(p.id, gateway, beneficiaries)
        async with self.sessionmaker() as session:
            ids = [p.id for p in rows]
            return list(await session.scalars(select(Payment).where(Payment.id.in_(ids)))), events

    async def _send(
        self, payment_id: int, gateway: PayoutGateway, beneficiaries: dict[str, Any]
    ) -> list[Event]:
        async with self.sessionmaker() as session:
            p = await session.get(Payment, payment_id)
            assert p is not None
            vendor = await session.get(Vendor, p.vendor_id)
            doc = await session.scalar(
                select(ErpDocument).where(ErpDocument.doc_number == p.doc_number)
            )
            if vendor is None or doc is None or not vendor.bank_account_number:
                events = self._fail(p, "vendor or bank account missing")
                await session.commit()
                return events
            try:
                cached = beneficiaries.get(vendor.id) or {}
                if cached.get("account") != _account_key(vendor):
                    contact = await gateway.create_contact(vendor)
                    fund = await gateway.create_fund_account(contact, vendor)
                    cached = {"contact_id": contact, "fund_account_id": fund}
                    cached["account"] = _account_key(vendor)
                    beneficiaries[vendor.id] = cached
                p.contact_id, p.fund_account_id = cached["contact_id"], cached["fund_account_id"]
                payout = await gateway.create_payout(
                    fund_account_id=cached["fund_account_id"],
                    amount=p.amount,
                    mode=p.mode,
                    reference_id=p.idempotency_key,
                    narration=narration(doc.invoice_number),
                    notes={
                        "doc_number": p.doc_number,
                        "invoice_number": doc.invoice_number,
                        "case": p.run_id or "",
                    },
                    idempotency_key=p.idempotency_key,
                )
            except PayoutError as exc:
                events = self._fail(p, str(exc))
                await session.commit()
                return events
            except httpx.HTTPError as exc:
                # Unknown whether RazorpayX got it: stay QUEUED; the sync re-sends with the same
                # idempotency key, which returns the same payout instead of a second one.
                p.error = f"not confirmed yet ({type(exc).__name__}); will retry"
                p.updated_at = self.clock()
                await session.commit()
                return []
            p.payout_id = str(payout.get("id"))
            events = await self._apply(session, p, payout)
            await session.commit()
            return events

    def _fail(self, p: Payment, reason: str) -> list[Event]:
        p.status, p.error, p.updated_at = "FAILED", reason[:500], self.clock()
        return [("payment.failed", _event_payload(p, None))]

    async def _apply(
        self, session: AsyncSession, p: Payment, payout: dict[str, Any]
    ) -> list[Event]:
        """Bring our row in line with a RazorpayX payout; the events of the change, if any."""
        provider_status = str(payout.get("status") or "")
        new = _STATUS.get(provider_status, "PROCESSING")
        before = p.status
        p.provider_status = provider_status or p.provider_status
        p.utr = payout.get("utr") or p.utr
        p.updated_at = self.clock()
        if new == before:
            return []
        p.status = new
        doc = await session.scalar(
            select(ErpDocument).where(ErpDocument.doc_number == p.doc_number)
        )
        if new == "PAID":
            p.paid_at, p.error = self.clock(), None
            if doc is not None:
                doc.status, doc.paid_at, doc.utr = "PAID", p.paid_at, p.utr
                doc.updated_at = self.clock()
            return [("payment.processed", _event_payload(p, doc))]
        if new in ("FAILED", "REVERSED"):
            details = payout.get("status_details") or {}
            p.error = (
                details.get("description")
                or payout.get("failure_reason")
                or f"payout {provider_status}"
            )
            if new == "REVERSED" and doc is not None and doc.status == "PAID":
                doc.status, doc.paid_at, doc.utr = "POSTED", None, None
            return [("payment.failed", _event_payload(p, doc))]
        return []

    async def sync(self, gateway: PayoutGateway, beneficiaries: dict[str, Any]) -> list[Event]:
        """Ask RazorpayX about payouts still in flight (and re-send unconfirmed ones)."""
        async with self.sessionmaker() as session:
            rows = list(await session.scalars(select(Payment).where(Payment.status.in_(OPEN))))
        events: list[Event] = []
        for row in rows:
            if row.payout_id is None:
                events += await self._send(row.id, gateway, beneficiaries)
                continue
            try:
                payout = await gateway.get_payout(row.payout_id)
            except (PayoutError, httpx.HTTPError):
                continue
            async with self.sessionmaker() as session:
                p = await session.get(Payment, row.id)
                assert p is not None
                events += await self._apply(session, p, payout)
                await session.commit()
        return events

    async def webhook(self, body: bytes) -> list[Event]:
        """A verified RazorpayX webhook: update the payout it is about."""
        data = json.loads(body)
        event = str(data.get("event") or "")
        if not event.startswith("payout."):
            return []
        entity = ((data.get("payload") or {}).get("payout") or {}).get("entity") or {}
        payout_id = entity.get("id")
        async with self.sessionmaker() as session:
            p = await session.scalar(select(Payment).where(Payment.payout_id == payout_id))
            if p is None and entity.get("reference_id"):
                p = await session.scalar(
                    select(Payment).where(Payment.idempotency_key == entity["reference_id"])
                )
                if p is not None:
                    p.payout_id = payout_id
            if p is None:
                return []
            events = await self._apply(session, p, entity)
            await session.commit()
            return events


def verify_webhook(secret: str, body: bytes, signature: str | None) -> bool:
    """RazorpayX signs the raw body: hex HMAC-SHA256 with the webhook secret."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def _event_payload(p: Payment, doc: ErpDocument | None) -> dict[str, Any]:
    return {
        "run_id": p.run_id,
        "payment_id": p.id,
        "doc_number": p.doc_number,
        "vendor_id": p.vendor_id,
        "invoice_number": doc.invoice_number if doc else None,
        "invoice_date": str(doc.invoice_date) if doc else None,
        "amount": str(p.amount),
        "mode": p.mode,
        "status": p.status,
        "utr": p.utr,
        "error": p.error,
        "system": doc.system if doc else None,
    }


# --- payables figures for the dashboard ---------------------------------------------------------


@dataclass
class Payables:
    outstanding: Decimal
    overdue: Decimal
    ageing: dict[str, Decimal]  # bucket → unpaid amount
    paid: int
    paid_on_time: int
    on_time_rate: float | None


def payables(items: list[QueueItem], today: date) -> Payables:
    buckets = {"Not due": Decimal(0), "1–15 days": Decimal(0), "16–30 days": Decimal(0)}
    buckets["Over 30 days"] = Decimal(0)
    outstanding = overdue = Decimal(0)
    paid = on_time = 0
    for i in items:
        if i.state == "PAID":
            paid += 1
            when = i.paid_at or (i.payment.paid_at if i.payment else None)
            if when is not None and today_ist(when) <= i.due_date:
                on_time += 1
            continue
        outstanding += i.amount
        late = (today - i.due_date).days
        if late <= 0:
            buckets["Not due"] += i.amount
            continue
        overdue += i.amount
        key = "1–15 days" if late <= 15 else "16–30 days" if late <= 30 else "Over 30 days"
        buckets[key] += i.amount
    return Payables(
        outstanding=outstanding,
        overdue=overdue,
        ageing=buckets,
        paid=paid,
        paid_on_time=on_time,
        on_time_rate=(on_time / paid) if paid else None,
    )
