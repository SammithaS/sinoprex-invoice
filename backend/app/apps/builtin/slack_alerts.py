"""Slack Alerts: post case events to a Slack channel (incoming webhook)."""

from typing import Any

from app.apps.builtin.common import case_details, inr
from app.apps.context import AppContext
from app.apps.manifest import AppManifest, ConnectionRequirement, Field
from app.db.models import Run

EVENT_LABELS = {
    "case.waiting_review": "Waiting for review",
    "case.waiting_approval": "Waiting for approval",
    "case.closed": "Case closed",
    "case.failed": "Case failed",
}


async def post(ctx: AppContext, payload: dict[str, Any]) -> str:
    event = str(payload.get("event"))
    if event not in (ctx.inputs.get("events") or []):
        return f"SKIP: {event} is switched off"
    run_id = payload["run_id"]
    async with ctx.sessionmaker() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return "SKIP: case not found"
        who, number, total = run.vendor_name, run.invoice_number, run.total
        decision = run.final_decision or run.decision
        error = run.error
    text = (
        f"*{EVENT_LABELS[event]}* · {who or 'Unknown supplier'} {number or ''}"
        f"{f' · {inr(total)}' if total else ''}"
    )
    if event in ("case.waiting_review", "case.waiting_approval"):
        details, _ = await case_details(ctx, run_id, "review")
        if details and details.get("question"):
            text += f"\n> {details['question']}"
    elif event == "case.closed" and decision:
        text += f" · {decision}"
    elif event == "case.failed" and error:
        text += f"\n> {error[:200]}"
    text += f"\n<{ctx.app_url}/runs/{run_id}|Open the case>"
    async with ctx.transports.client() as client:
        res = await client.post(ctx.connections["channel"]["webhook_url"], json={"text": text})
    if res.status_code >= 300:
        raise RuntimeError(f"Slack answered HTTP {res.status_code}: {res.text[:120]}")
    return f"Posted: {EVENT_LABELS[event]}"


def _handler(event: str):  # type: ignore[no-untyped-def]
    async def handler(ctx: AppContext, payload: dict[str, Any]) -> str:
        return await post(ctx, {**payload, "event": event})

    return handler


APP = AppManifest(
    id="slack-alerts",
    name="Slack Alerts",
    tagline="Case updates in your team's channel",
    description=(
        "Posts to a Slack channel when cases wait for review or approval, close, or fail, "
        "with the question and a link to the case. Choose which events to post."
    ),
    icon="message-square-share",
    category="Notifications",
    inputs=(
        Field(
            "events",
            "Post these events",
            "multiselect",
            default=["case.waiting_review", "case.waiting_approval", "case.failed"],
            options=tuple(EVENT_LABELS.items()),
        ),
    ),
    connections=(ConnectionRequirement("channel", "slack", "Slack channel"),),
    events=tuple(EVENT_LABELS),
    handlers={e: _handler(e) for e in EVENT_LABELS},
)
