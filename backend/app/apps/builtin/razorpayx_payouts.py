"""RazorpayX Payouts: propose payment runs on payment days; pay released batches by transfer."""

from decimal import Decimal
from typing import Any

from app.apps.builtin.common import inr
from app.apps.connections import razorpayx
from app.apps.context import AppContext
from app.apps.manifest import AppManifest, ConnectionRequirement, Field, Timer
from app.payments import WEEKDAYS, Payments, today_ist

APP_ID = "razorpayx-payouts"
DAY_LABELS = (
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
)


def _payments(ctx: AppContext) -> Payments:
    return Payments(ctx.sessionmaker, clock=lambda: ctx.now)


def _days(ctx: AppContext) -> list[str]:
    return list(ctx.inputs.get("payment_days") or ["tue", "fri"])


async def _check(ctx: AppContext) -> None:
    await razorpayx(ctx.connections["razorpayx"], ctx.transports).check()


async def propose(ctx: AppContext, payload: dict[str, Any]) -> str:
    days = _days(ctx)
    weekday = WEEKDAYS[today_ist(ctx.now).weekday()]
    if not payload.get("manual") and weekday not in days:
        return "SKIP: not a payment day"
    batch, rows = await _payments(ctx).propose(days, int(ctx.inputs.get("max_per_run") or 50))
    if batch is None:
        return "SKIP: nothing due before the next payment day"
    total = sum((r.amount for r in rows), Decimal(0))
    return (
        f"Proposed {len(rows)} payment(s), {inr(total)}, in batch {batch}. "
        "Nothing is paid until someone releases it on the Payments page."
    )


async def release(ctx: AppContext, payload: dict[str, Any]) -> str:
    gateway = razorpayx(ctx.connections["razorpayx"], ctx.transports)
    beneficiaries = ctx.runtime.setdefault("beneficiaries", {})
    rows, events = await _payments(ctx).release(
        payload["batch_id"],
        payload["actor"],
        gateway,
        mode=str(ctx.inputs.get("mode") or "AUTO"),
        beneficiaries=beneficiaries,
    )
    for event, data in events:
        ctx.emit(event, data)
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    summary = ", ".join(f"{n} {s.lower()}" for s, n in sorted(by_status.items()))
    return (
        f"Released {payload['batch_id']} ({len(rows)} payout(s): {summary}) by {payload['actor']}"
    )


async def sync(ctx: AppContext, payload: dict[str, Any]) -> str:
    gateway = razorpayx(ctx.connections["razorpayx"], ctx.transports)
    events = await _payments(ctx).sync(gateway, ctx.runtime.setdefault("beneficiaries", {}))
    for event, data in events:
        ctx.emit(event, data)
    if not events:
        return "SKIP: no payout changed"
    done = sum(1 for e, _ in events if e == "payment.processed")
    return f"{done} payout(s) processed, {len(events) - done} failed or reversed"


APP = AppManifest(
    id=APP_ID,
    name="RazorpayX Payouts",
    tagline="Pay approved invoices by bank transfer, on your payment days",
    description=(
        "On each payment day (India time) proposes a payment run: every approved, posted and "
        "verified invoice that falls due before the next payment day. Invoices with a hold "
        "(changed bank details, open exceptions, blocked vendor) are left out with the reason. "
        "A person releases the batch on the Payments page; only then are payouts sent through "
        "RazorpayX, each with an idempotency key. RazorpayX's webhook (and a half-hourly check) "
        "marks the invoice PAID with the bank reference (UTR). Use test keys to try it with no "
        "real money."
    ),
    icon="landmark",
    category="Payments",
    inputs=(
        Field(
            "payment_days",
            "Payment days",
            "multiselect",
            default=["tue", "fri"],
            options=DAY_LABELS,
            help="ap-policy §10.2: Tuesdays and Fridays",
        ),
        Field("propose_at", "Propose the run at (India time)", "time", default="10:00"),
        Field(
            "mode",
            "Transfer mode",
            "select",
            default="AUTO",
            options=(
                ("AUTO", "Automatic (IMPS up to ₹5 lakh, NEFT above)"),
                ("IMPS", "IMPS"),
                ("NEFT", "NEFT"),
                ("RTGS", "RTGS"),
            ),
        ),
        Field("max_per_run", "Most payments per run", "number", default=50, minimum=1, maximum=500),
    ),
    connections=(ConnectionRequirement("razorpayx", "razorpayx", "RazorpayX account"),),
    timers=(
        Timer("propose", "Payment proposal", daily_input="propose_at"),
        Timer("sync", "Check payouts in flight", interval_minutes=30),
    ),
    handlers={"propose": propose, "sync": sync, "release": release},
    on_validate=_check,
    run_now="propose",
)
