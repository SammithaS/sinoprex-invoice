"""Helpers shared by the built-in apps: case details and email texts."""

from typing import Any

from app.apps.context import AppContext
from app.db.models import Run
from app.integrations.notify import REVIEW_NEEDED, VENDOR_FOLLOWUP, Notifier

BRAND = "SinoprexInvoice"


async def case_details(
    ctx: AppContext, run_id: str, kind: str, extra: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """The same case payload the n8n notifications use: (payload, why it can't be built)."""
    event = VENDOR_FOLLOWUP if kind == "followup" else REVIEW_NEEDED
    async with ctx.sessionmaker() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return None, "case not found"
        notifier = Notifier(ctx.sessionmaker, ctx.settings)
        return await notifier.payload(session, run, event, extra or {})


def inr(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    whole, frac = f"{abs(n):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    text = ",".join([g for g in [head, *groups] if g] + [tail])
    return "₹" + text + ("" if frac == "00" else f".{frac}")


def review_email(p: dict[str, Any], reminder: bool = False) -> tuple[str, str]:
    """(subject, body) for a reviewer / approver alert."""
    kind = p.get("kind")
    what = (
        "Approval needed"
        if kind == "approval"
        else "ERP recheck needed"
        if kind == "erp"
        else "Review needed"
    )
    who = p.get("vendor") or "Unknown supplier"
    number = p.get("invoice_number") or "(no number)"
    total = f" for {inr(p['total_inr'])}" if p.get("total_inr") else ""
    lines = [f"{what}: {who} invoice {number}{total}.", ""]
    if p.get("question"):
        lines.append(f"Question: {p['question']}")
    for e in p.get("exceptions") or []:
        lines.append(f"- {e.get('category')}: {e.get('summary')} (owner: {e.get('owner')})")
    if p.get("approver_role"):
        name = f" ({p['approver_name']})" if p.get("approver_name") else ""
        lines.append(f"Approver: {p['approver_role']}{name}")
    lines += ["", f"Open the case: {p.get('link')}"]
    lines.append(
        f"Approvals inbox: {p.get('approvals_inbox')}"
        if kind == "approval"
        else f"Review queue: {p.get('review_queue')}"
    )
    if reminder:
        lines.insert(0, "Reminder: this case is still waiting.\n")
    prefix = "Reminder: " if reminder else ""
    return f"{prefix}{BRAND} · {what}: {who} {number}".strip(), "\n".join(lines)
