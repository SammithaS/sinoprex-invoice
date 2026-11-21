"""Daily AP Summary: a short digest of the last 30 days at a time you choose."""

from typing import Any

from app.apps.builtin.common import BRAND, inr
from app.apps.context import AppContext
from app.apps.mail import OutgoingEmail
from app.apps.manifest import AppManifest, ConnectionRequirement, Field, Timer


async def _check(ctx: AppContext) -> None:
    check = getattr(ctx.mailer("sender"), "check", None)
    if check is not None:
        await check()


async def digest(ctx: AppContext, payload: dict[str, Any]) -> str:
    from app.api.stats import stats

    async with ctx.sessionmaker() as session:
        s = await stats(session)
    d = s.decisions
    causes = sorted(s.exception_causes.items(), key=lambda kv: -kv[1])[:3]
    rate = "—" if s.straight_through_rate is None else f"{round(s.straight_through_rate * 100)}%"
    lines = [
        "Good morning. Accounts payable, last 30 days:",
        "",
        f"- Cases: {s.runs} ({s.completed} closed)",
        f"- Approved: {d.get('APPROVED', 0)} · Rejected: {d.get('REJECTED', 0)} · "
        f"On hold: {d.get('ON_HOLD', 0)}",
        f"- Straight-through (no human touch): {rate}",
        f"- Waiting for review: {s.waiting_review} · for approval: {s.waiting_approval} "
        f"(value {inr(s.pending_value)})",
        f"- Posted to ERP: {s.erp_posted} ({s.erp_verified} verified, "
        f"{s.erp_mismatches} mismatches)",
        "- Top issues: " + (", ".join(f"{k} ({v})" for k, v in causes) if causes else "none"),
        "",
        f"Dashboard: {ctx.app_url}/dashboard",
        f"Review queue: {ctx.app_url}/review",
    ]
    to = list(ctx.inputs["recipients"])
    subject = f"{BRAND} daily AP summary · {s.waiting_review} waiting for review"
    await ctx.mailer("sender").send(OutgoingEmail(to=to, subject=subject, body="\n".join(lines)))
    return f"Summary sent to {', '.join(to)}"


APP = AppManifest(
    id="daily-summary",
    name="Daily AP Summary",
    tagline="A morning digest of what needs attention",
    description=(
        "Every day at the time you choose (India time), emails a short summary: cases, "
        "decisions, straight-through rate, what is waiting and its value, ERP postings and the "
        "top exception causes, with links to the dashboard and the review queue."
    ),
    icon="calendar-clock",
    category="Reporting",
    inputs=(
        Field("recipients", "Send to", "emails", required=True, help="Comma-separated"),
        Field("send_at", "Send at (India time)", "time", default="09:00"),
        Field("weekdays_only", "Weekdays only", "boolean", default=True),
    ),
    connections=(ConnectionRequirement("sender", "gmail", "Send from"),),
    timers=(
        Timer("digest", "Daily summary", daily_input="send_at", weekdays_input="weekdays_only"),
    ),
    handlers={"digest": digest},
    on_validate=_check,
    run_now="digest",
)
