"""Reviewer Alerts: email whoever has to act when a case waits, with a reminder after N hours."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.apps.builtin.common import case_details, review_email
from app.apps.context import AppContext
from app.apps.mail import OutgoingEmail
from app.apps.manifest import AppManifest, ConnectionRequirement, Field, Timer
from app.db.models import Run

WAITING = ("WAITING_REVIEW", "WAITING_APPROVAL")


def _recipients(ctx: AppContext, kind: str | None) -> list[str]:
    reviewers = list(ctx.inputs.get("reviewer_emails") or [])
    approvers = list(ctx.inputs.get("approver_emails") or [])
    return (approvers or reviewers) if kind == "approval" else reviewers


async def _check(ctx: AppContext) -> None:
    check = getattr(ctx.mailer("sender"), "check", None)
    if check is not None:
        await check()


async def alert(ctx: AppContext, payload: dict[str, Any]) -> str:
    run_id = payload["run_id"]
    details, skip = await case_details(ctx, run_id, "review")
    if details is None:
        return f"SKIP: {skip}"
    to = _recipients(ctx, details.get("kind"))
    subject, body = review_email(details)
    await ctx.mailer("sender").send(OutgoingEmail(to=to, subject=subject, body=body))
    alerted = dict(ctx.runtime.get("alerted", {}))
    alerted[run_id] = ctx.now.isoformat()
    ctx.runtime["alerted"] = alerted
    reminded = set(ctx.runtime.get("reminded", []))
    reminded.discard(run_id)
    ctx.runtime["reminded"] = sorted(reminded)
    return f"Alert sent to {', '.join(to)}: {subject}"


async def remind(ctx: AppContext, payload: dict[str, Any]) -> str:
    hours = float(ctx.inputs.get("remind_after_hours") or 0)
    alerted: dict[str, str] = dict(ctx.runtime.get("alerted", {}))
    if not hours or not alerted:
        return "SKIP: nothing to remind"
    reminded = set(ctx.runtime.get("reminded", []))
    async with ctx.sessionmaker() as session:
        rows = await session.execute(select(Run.id, Run.status).where(Run.id.in_(list(alerted))))
        status = dict(rows.all())
    sent = 0
    for run_id, when in list(alerted.items()):
        if status.get(run_id) not in WAITING:
            alerted.pop(run_id)  # no longer waiting: forget it
            reminded.discard(run_id)
            continue
        if run_id in reminded or ctx.now - datetime.fromisoformat(when) < timedelta(hours=hours):
            continue
        details, _ = await case_details(ctx, run_id, "review")
        if details is None:
            continue
        subject, body = review_email(details, reminder=True)
        await ctx.mailer("sender").send(
            OutgoingEmail(to=_recipients(ctx, details.get("kind")), subject=subject, body=body)
        )
        reminded.add(run_id)
        sent += 1
    ctx.runtime["alerted"], ctx.runtime["reminded"] = alerted, sorted(reminded)
    return f"{sent} reminder(s) sent" if sent else "SKIP: no case waiting long enough"


APP = AppManifest(
    id="reviewer-alerts",
    name="Reviewer Alerts",
    tagline="Email the right person when a case is waiting",
    description=(
        "When a case stops for a review or an approval, emails the reviewer (or approver) the "
        "question, the exceptions and a direct link to the case. If it is still waiting after "
        "the reminder time, sends one reminder."
    ),
    icon="bell-ring",
    category="Notifications",
    inputs=(
        Field(
            "reviewer_emails", "Reviewer email(s)", "emails", required=True, help="Comma-separated"
        ),
        Field(
            "approver_emails",
            "Approver email(s)",
            "emails",
            help="For approval steps. Default: the reviewers",
        ),
        Field(
            "remind_after_hours",
            "Remind after (hours)",
            "number",
            default=24,
            minimum=0,
            maximum=168,
            help="0 turns reminders off",
        ),
    ),
    connections=(ConnectionRequirement("sender", "gmail", "Send from"),),
    events=("case.waiting_review", "case.waiting_approval"),
    timers=(Timer("reminders", "Check for overdue cases", interval_minutes=15),),
    handlers={"case.waiting_review": alert, "case.waiting_approval": alert, "reminders": remind},
    on_validate=_check,
)
