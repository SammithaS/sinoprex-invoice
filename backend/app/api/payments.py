"""Payments: the payables queue, proposed batches, release (by a named person), RazorpayX webhook.

The webhook route is not behind the API key: RazorpayX proves itself with the signature
(hex HMAC-SHA256 of the raw body with the webhook secret of the RazorpayX connection).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.apps import ActorDep, PlatformDep
from app.apps.builtin.razorpayx_payouts import APP_ID
from app.apps.platform import AppPlatform
from app.db.models import AppInstall, Payment
from app.db.session import get_sessionmaker
from app.payments import Payments, QueueItem, today_ist, verify_webhook

router = APIRouter(tags=["payments"])
webhook_router = APIRouter(tags=["payments"])


def get_payments(request: Request) -> Payments:
    override = getattr(request.app.state, "payments", None)
    if isinstance(override, Payments):
        return override
    service = getattr(request.app.state, "run_service", None)
    return Payments(service.sessionmaker if service is not None else get_sessionmaker())


PaymentsDep = Annotated[Payments, Depends(get_payments)]


class PaymentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    doc_number: str
    run_id: str | None
    batch_id: str
    attempt: int
    vendor_id: str
    amount: Decimal
    due_date: date
    mode: str
    status: str
    payout_id: str | None
    provider_status: str | None
    utr: str | None
    error: str | None
    released_by: str | None
    released_at: datetime | None
    retry_approved_by: str | None
    paid_at: datetime | None
    created_at: datetime


class QueueItemOut(BaseModel):
    doc_number: str
    system: str
    run_id: str | None
    vendor_id: str
    vendor_name: str | None
    invoice_number: str
    invoice_date: date
    amount: Decimal
    due_date: date
    state: str
    holds: list[str]
    releasable: bool
    attempts: int
    utr: str | None
    paid_at: datetime | None
    payment: PaymentOut | None


class BatchOut(BaseModel):
    batch_id: str
    status: str  # PROPOSED | RELEASED
    count: int
    total: Decimal
    released_by: str | None
    released_at: datetime | None
    payments: list[PaymentOut]


class PaymentsOut(BaseModel):
    app_active: bool
    payment_days: list[str]
    next_payment_day: date
    today: date
    queue: list[QueueItemOut]
    batches: list[BatchOut]


def _item(i: QueueItem) -> QueueItemOut:
    return QueueItemOut(
        doc_number=i.doc_number,
        system=i.system,
        run_id=i.run_id,
        vendor_id=i.vendor_id,
        vendor_name=i.vendor_name,
        invoice_number=i.invoice_number,
        invoice_date=i.invoice_date,
        amount=i.amount,
        due_date=i.due_date,
        state=i.state,
        holds=i.holds,
        releasable=i.releasable,
        attempts=i.attempts,
        utr=i.utr,
        paid_at=i.paid_at,
        payment=PaymentOut.model_validate(i.payment) if i.payment else None,
    )


async def _days(request: Request) -> tuple[bool, list[str]]:
    platform: AppPlatform | None = getattr(request.app.state, "apps", None)
    days = ["tue", "fri"]
    if platform is None:
        return False, days
    active = await platform.is_active(APP_ID)
    async with platform.sessionmaker() as session:
        inst = await session.get(AppInstall, APP_ID)
        if inst is not None and (inst.inputs or {}).get("payment_days"):
            days = list(inst.inputs["payment_days"])
    return active, days


@router.get("/payments", response_model=PaymentsOut)
async def payments_overview(request: Request, payments: PaymentsDep) -> PaymentsOut:
    active, days = await _days(request)
    queue, next_run = await payments.queue(days)
    async with payments.sessionmaker() as session:
        rows = list(
            await session.scalars(
                select(Payment).where(Payment.status != "CANCELLED").order_by(Payment.id.desc())
            )
        )
    batches: dict[str, list[Payment]] = {}
    for p in rows:
        batches.setdefault(p.batch_id, []).append(p)
    out_batches = []
    for batch_id, ps in list(batches.items())[:20]:
        released = [p for p in ps if p.released_by]
        out_batches.append(
            BatchOut(
                batch_id=batch_id,
                status="RELEASED" if released else "PROPOSED",
                count=len(ps),
                total=sum((p.amount for p in ps), Decimal(0)),
                released_by=released[0].released_by if released else None,
                released_at=released[0].released_at if released else None,
                payments=[PaymentOut.model_validate(p) for p in ps],
            )
        )
    order = {"DUE": 0, "PROPOSED": 1, "FAILED": 2, "HELD": 3, "IN_FLIGHT": 4, "SCHEDULED": 5}
    queue.sort(key=lambda i: (order.get(i.state, 9), i.due_date))
    return PaymentsOut(
        app_active=active,
        payment_days=days,
        next_payment_day=next_run,
        today=today_ist(payments.clock()),
        queue=[_item(i) for i in queue],
        batches=out_batches,
    )


def _actor(actor: str | None) -> str:
    if not actor or not actor.strip():
        raise HTTPException(status_code=422, detail="a named person must do this (X-Actor)")
    return actor.strip()


class ProposalOut(BaseModel):
    status: str
    message: str | None
    error: str | None


def _run(result: dict[str, Any]) -> ProposalOut:
    if result["status"] == "ERROR":
        raise HTTPException(status_code=502, detail=result["error"])
    return ProposalOut(status=result["status"], message=result["message"], error=result["error"])


@router.post("/payments/propose", response_model=ProposalOut)
async def propose(platform: PlatformDep, actor: ActorDep = None) -> ProposalOut:
    """Propose a payment run now (the same as the app's "Run now")."""
    if not await platform.is_active(APP_ID):
        raise HTTPException(status_code=409, detail="activate the RazorpayX Payouts app first")
    return _run(await platform.run_now(APP_ID, actor))


@router.post("/payments/batches/{batch_id}/release", response_model=ProposalOut)
async def release_batch(
    platform: PlatformDep, batch_id: str, actor: ActorDep = None
) -> ProposalOut:
    """A person releases a proposed batch; the payouts are sent now."""
    who = _actor(actor)
    if not await platform.is_active(APP_ID):
        raise HTTPException(status_code=409, detail="activate the RazorpayX Payouts app first")
    return _run(await platform.execute(APP_ID, "release", {"batch_id": batch_id, "actor": who}))


class CheckOut(BaseModel):
    status: str  # OK (paid or failed now) | SKIPPED (still in progress)
    message: str | None
    payment: PaymentOut


@router.post("/payments/{payment_id}/check", response_model=CheckOut)
async def check_payment(
    platform: PlatformDep, payments: PaymentsDep, payment_id: int, actor: ActorDep = None
) -> CheckOut:
    """'Confirm received': fetch this payout's latest status from RazorpayX now. Only
    RazorpayX's answer marks it paid; the person's word alone never does."""
    if not await platform.is_active(APP_ID):
        raise HTTPException(status_code=409, detail="activate the RazorpayX Payouts app first")
    result = await platform.execute(APP_ID, "check", {"payment_ids": [payment_id]})
    if result["status"] == "ERROR":
        raise HTTPException(status_code=502, detail=result["error"])
    async with payments.sessionmaker() as session:
        p = await session.get(Payment, payment_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"payment {payment_id} not found")
    return CheckOut(
        status=result["status"], message=result["message"], payment=PaymentOut.model_validate(p)
    )


@router.post("/payments/{payment_id}/cancel", response_model=PaymentOut)
async def cancel_payment(payments: PaymentsDep, payment_id: int) -> Payment:
    """Take one invoice out of a proposed batch (it is proposed again in the next run)."""
    try:
        return await payments.cancel(payment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/payments/{payment_id}/retry", response_model=PaymentOut)
async def retry_payment(payments: PaymentsDep, payment_id: int, actor: ActorDep = None) -> Payment:
    """After a failure is looked into: include the invoice in the next proposal again."""
    try:
        return await payments.approve_retry(payment_id, _actor(actor))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/payments/holds/{doc_number}/release", status_code=204)
async def release_hold(payments: PaymentsDep, doc_number: str, actor: ActorDep = None) -> None:
    """Release a bank-change hold after the vendor master was checked and updated."""
    try:
        await payments.release_hold(doc_number, _actor(actor))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- RazorpayX webhook (signed, no API key) ----------------------------------------------------


class WebhookOut(BaseModel):
    updated: int


@webhook_router.post("/webhooks/razorpayx", response_model=WebhookOut)
async def razorpayx_webhook(
    request: Request,
    payments: PaymentsDep,
    signature: Annotated[str | None, Header(alias="X-Razorpay-Signature")] = None,
) -> WebhookOut:
    platform: AppPlatform | None = getattr(request.app.state, "apps", None)
    values = await platform.active_connection(APP_ID, "razorpayx") if platform else None
    secret = str((values or {}).get("webhook_secret") or "")
    if not secret:
        raise HTTPException(status_code=503, detail="RazorpayX Payouts is not active")
    body = await request.body()
    if len(body) > 1_000_000:
        raise HTTPException(status_code=413, detail="request too large")
    if not verify_webhook(secret, body, signature):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        events = await payments.webhook(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="body is not JSON") from exc
    assert platform is not None
    for event, data in events:
        platform.emit(event, data)
    return WebhookOut(updated=len(events))
