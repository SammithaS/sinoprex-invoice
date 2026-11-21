"""n8n Connector: the signed-webhook integration with n8n, as an optional app.

Outbound: review-needed and vendor-follow-up notices to the n8n workflows. Inbound: while
active, `POST /intake/email` accepts requests signed with this connection's secret.
"""

from typing import Any

from sqlalchemy import select

from app.apps.context import AppContext
from app.apps.manifest import AppManifest, ConnectionRequirement
from app.db.models import Notification
from app.integrations.notify import REVIEW_NEEDED, VENDOR_FOLLOWUP, Notifier


def _notifier(ctx: AppContext) -> Notifier:
    conn = ctx.connections["n8n"]
    settings = ctx.settings.model_copy(
        update={"n8n_base_url": conn["base_url"], "n8n_webhook_secret": conn["webhook_secret"]}
    )
    return Notifier(ctx.sessionmaker, settings, transport=ctx.transports.http)


async def _send(ctx: AppContext, run_id: str, event: str, extra: dict[str, Any]) -> str:
    await _notifier(ctx)._send(run_id, event, extra)
    async with ctx.sessionmaker() as session:
        row = await session.scalar(
            select(Notification)
            .where(Notification.run_id == run_id, Notification.event == event)
            .order_by(Notification.id.desc())
        )
    if row is None:
        return "SKIP: nothing recorded"
    if row.status == "FAILED":
        raise RuntimeError(row.error or "n8n webhook failed")
    if row.status == "SKIPPED":
        return f"SKIP: {row.error}"
    return f"Sent {event} to n8n ({row.attempts} attempt(s))"


async def review(ctx: AppContext, payload: dict[str, Any]) -> str:
    return await _send(ctx, payload["run_id"], REVIEW_NEEDED, {})


async def follow_up(ctx: AppContext, payload: dict[str, Any]) -> str:
    extra = {"comment": payload.get("comment"), "reviewer": payload.get("reviewer")}
    return await _send(ctx, payload["run_id"], VENDOR_FOLLOWUP, extra)


APP = AppManifest(
    id="n8n-connector",
    name="n8n Connector",
    tagline="Use your own n8n workflows instead of the built-in apps",
    description=(
        "Sends HMAC-signed webhooks to n8n when a case waits for a person or a reviewer "
        "requests information, and accepts signed email-intake requests from n8n. Use it if "
        "you prefer to build the email flows in n8n (see n8n/README.md for the workflows)."
    ),
    icon="workflow",
    category="Integrations",
    connections=(ConnectionRequirement("n8n", "n8n", "n8n instance"),),
    events=("case.waiting_review", "case.waiting_approval", "case.request_info"),
    handlers={
        "case.waiting_review": review,
        "case.waiting_approval": review,
        "case.request_info": follow_up,
    },
)
