"""Payment Advice: email the vendor when a payment reaches the bank (amount, invoice, UTR)."""

from typing import Any

from sqlalchemy import select

from app.apps.builtin.common import BRAND, inr
from app.apps.context import AppContext
from app.apps.mail import OutgoingEmail
from app.apps.manifest import AppManifest, ConfigError, ConnectionRequirement, Field
from app.db.models import Payment, Vendor


async def _check(ctx: AppContext) -> None:
    if not ctx.inputs.get("send_to_vendor") and not ctx.inputs.get("demo_inbox"):
        raise ConfigError("Demo inbox is required while 'Send to the vendor' is off")
    check = getattr(ctx.mailer("sender"), "check", None)
    if check is not None:
        await check()


async def advise(ctx: AppContext, payload: dict[str, Any]) -> str:
    async with ctx.sessionmaker() as session:
        vendor = await session.get(Vendor, payload.get("vendor_id"))
        payment = await session.scalar(select(Payment).where(Payment.id == payload["payment_id"]))
    if vendor is None or payment is None:
        return "SKIP: vendor or payment not found"
    live = bool(ctx.inputs.get("send_to_vendor"))
    to = [vendor.email] if live else [ctx.inputs["demo_inbox"]]
    note = "" if live else f"[Demo: this email would go to {vendor.email}]\n\n"
    paid_on = payment.paid_at.strftime("%d %b %Y") if payment.paid_at else "today"
    body = "\n".join(
        [
            f"{note}Dear {vendor.legal_name},",
            "",
            f"We have paid {inr(payment.amount)} against your invoice "
            f"{payload.get('invoice_number')} dated {payload.get('invoice_date')}.",
            "",
            f"- Paid on: {paid_on}",
            f"- Transfer: {payment.mode} to your account ending {vendor.bank_account_last4}",
            f"- Bank reference (UTR): {payment.utr or 'to follow'}",
            "",
            "Please allow a few hours for the credit to show. Reply to this email if anything "
            "doesn't match your records.",
            "",
            "Accounts Payable",
        ]
    )
    subject = f"{BRAND} · Payment advice: invoice {payload.get('invoice_number')}"
    await ctx.mailer("sender").send(
        OutgoingEmail(to=to, cc=list(ctx.inputs.get("cc") or []), subject=subject, body=body)
    )
    return f"Payment advice sent to {to[0]}" + ("" if live else " (demo inbox)")


APP = AppManifest(
    id="payment-advice",
    name="Payment Advice",
    tagline="Tell vendors when they've been paid",
    description=(
        "When a payout reaches the bank, emails the vendor a payment advice: the invoice it "
        "settles, the amount, the transfer mode, the account it went to (last 4 digits only) "
        "and the bank reference (UTR). While testing, everything goes to a demo inbox instead."
    ),
    icon="receipt",
    category="Payments",
    inputs=(
        Field(
            "send_to_vendor",
            "Send to the vendor",
            "boolean",
            default=False,
            help="Off: send to the demo inbox (the seed vendors' addresses are fictional)",
        ),
        Field("demo_inbox", "Demo inbox", "email"),
        Field("cc", "Cc", "emails", help="Optional, comma-separated"),
    ),
    connections=(ConnectionRequirement("sender", "gmail", "Send from"),),
    events=("payment.processed",),
    handlers={"payment.processed": advise},
    on_validate=_check,
)
