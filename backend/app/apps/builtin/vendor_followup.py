"""Vendor Follow-up: on "Request info", email the vendor the matching FAQ template."""

from typing import Any

from app.apps.builtin.common import case_details
from app.apps.context import AppContext
from app.apps.mail import OutgoingEmail
from app.apps.manifest import AppManifest, ConfigError, ConnectionRequirement, Field


async def _check(ctx: AppContext) -> None:
    if not ctx.inputs.get("send_to_vendor") and not ctx.inputs.get("demo_inbox"):
        raise ConfigError("Test inbox is required while 'Send to the vendor' is off")
    check = getattr(ctx.mailer("sender"), "check", None)
    if check is not None:
        await check()


async def follow_up(ctx: AppContext, payload: dict[str, Any]) -> str:
    details, skip = await case_details(
        ctx,
        payload["run_id"],
        "followup",
        {"comment": payload.get("comment"), "reviewer": payload.get("reviewer")},
    )
    if details is None:
        return f"SKIP: {skip}"
    live = bool(ctx.inputs.get("send_to_vendor"))
    to = [details["vendor_email"]] if live else [ctx.inputs["demo_inbox"]]
    note = "" if live else f"[Test send: this email would go to {details['vendor_email']}]\n\n"
    ref = f" · template {details['citation']}" if details.get("citation") else ""
    body = (
        f"{note}{details['message']}\n\nAccounts Payable\n"
        f"(Ref: {details.get('invoice_number') or details['run_id']}{ref})"
    )
    await ctx.mailer("sender").send(
        OutgoingEmail(
            to=to, cc=list(ctx.inputs.get("cc") or []), subject=details["subject"], body=body
        )
    )
    return f"Follow-up sent to {to[0]}" + ("" if live else " (test inbox)")


APP = AppManifest(
    id="vendor-followup",
    name="Vendor Follow-up",
    tagline="Ask the vendor for what's missing, in your house style",
    description=(
        "When a reviewer chooses 'Request info', emails the vendor contact on file the "
        "vendor-FAQ template for the problem (price, quantity, GST, bank details, …) with the "
        "reviewer's note. While testing, everything goes to a test inbox instead."
    ),
    icon="send",
    category="Notifications",
    inputs=(
        Field(
            "send_to_vendor",
            "Send to the vendor",
            "boolean",
            default=False,
            help="Off: send to the test inbox (the sample vendors' addresses are fictional)",
        ),
        Field("demo_inbox", "Test inbox", "email"),
        Field("cc", "Cc", "emails", help="Optional, comma-separated"),
    ),
    connections=(ConnectionRequirement("sender", "gmail", "Send from"),),
    events=("case.request_info",),
    handlers={"case.request_info": follow_up},
    on_validate=_check,
)
