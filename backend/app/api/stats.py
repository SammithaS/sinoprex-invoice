"""Dashboard KPIs (`GET /stats`).

Computed in Python from runs, exceptions, approvals, ERP postings and corrections — fine at
demo scale (hundreds of runs).
"""

import statistics
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.schemas import RunOut
from app.db.models import Approval, CaseException, Correction, ErpPosting, Run
from app.deps import SessionDep
from app.payments import build_queue, now_utc, payables, today_ist

router = APIRouter(tags=["stats"])


class DayCount(BaseModel):
    day: date
    total: int
    approved: int
    needs_review: int
    on_hold: int
    rejected: int


class FunnelStage(BaseModel):
    stage: str
    count: int


class OwnerLoad(BaseModel):
    owner: str
    open: int
    resolved: int
    median_resolution_ms: int | None


class Stats(BaseModel):
    runs: int
    completed: int
    waiting_review: int
    waiting_approval: int
    running: int
    failed: int
    decisions: dict[str, int]  # final decision where known, else the rules' decision
    straight_through: int  # approved with no human touch
    straight_through_rate: float | None  # of completed runs
    median_cycle_ms: int | None  # completed runs, receipt → close (includes waiting time)
    median_touchless_ms: int | None
    pending_value: Decimal
    oldest_waiting_at: datetime | None
    reviewed: int
    exception_causes: dict[str, int]
    funnel: list[FunnelStage]
    volume: list[DayCount]
    recent: list[RunOut]
    erp_posted: int
    erp_verified: int
    erp_mismatches: int
    exceptions_by_owner: list[OwnerLoad]
    corrections_by_vendor: dict[str, int]
    approvals_decided: int
    payables_outstanding: Decimal
    payables_overdue: Decimal
    payables_ageing: dict[str, Decimal]
    payments_paid: int
    payments_on_time_rate: float | None


def _decision(run: Run) -> str | None:
    return run.final_decision or run.decision


def _median(values: list[int]) -> int | None:
    return int(statistics.median(values)) if values else None


@router.get("/stats", response_model=Stats)
async def stats(session: SessionDep, days: int = 30) -> Stats:
    runs = list(
        await session.scalars(
            select(Run).options(selectinload(Run.reviews)).order_by(Run.created_at.desc())
        )
    )
    completed = [r for r in runs if r.status == "COMPLETED"]
    waiting = [r for r in runs if r.status in ("WAITING_REVIEW", "WAITING_APPROVAL")]
    approvals = list(await session.scalars(select(Approval)))
    postings = list(await session.scalars(select(ErpPosting)))
    exceptions = list(await session.scalars(select(CaseException)))
    corrections = list(await session.scalars(select(Correction)))
    pay_today = today_ist(now_utc())
    owed = payables(await build_queue(session, pay_today, pay_today), pay_today)
    with_approvals = {a.run_id for a in approvals}
    verified = {p.run_id for p in postings if p.verified}
    # Touchless: approved with no reviewer, no approver, and the ERP write confirmed.
    touchless = [
        r
        for r in completed
        if r.decision == "APPROVED"
        and not r.reviews
        and r.id not in with_approvals
        and r.id in verified
    ]

    causes: Counter[str] = Counter()
    for r in runs:
        for exc in ((r.state or {}).get("assessment") or {}).get("exceptions") or []:
            causes[exc["category"]] += 1

    def reached(node: str) -> int:
        return sum(1 for r in runs if node in _done_nodes(r))

    funnel = [
        FunnelStage(stage="Received", count=len(runs)),
        FunnelStage(stage="Extracted", count=reached("extract")),
        FunnelStage(stage="Decided by rules", count=reached("rules")),
        FunnelStage(stage="Closed", count=len(completed)),
        FunnelStage(stage="Closed with no human touch", count=len(touchless)),
    ]

    today = datetime.now(UTC).date()
    start = today - timedelta(days=max(1, min(days, 365)) - 1)
    by_day: dict[date, Counter[str]] = {
        start + timedelta(days=i): Counter() for i in range((today - start).days + 1)
    }
    for r in runs:
        d = r.created_at.date()
        if d in by_day:
            by_day[d][_decision(r) or "PENDING"] += 1
    volume = [
        DayCount(
            day=d,
            total=sum(c.values()),
            approved=c["APPROVED"],
            needs_review=c["NEEDS_REVIEW"],
            on_hold=c["ON_HOLD"],
            rejected=c["REJECTED"],
        )
        for d, c in sorted(by_day.items())
    ]

    return Stats(
        runs=len(runs),
        completed=len(completed),
        waiting_review=sum(1 for r in runs if r.status == "WAITING_REVIEW"),
        waiting_approval=sum(1 for r in runs if r.status == "WAITING_APPROVAL"),
        running=sum(1 for r in runs if r.status in ("QUEUED", "RUNNING")),
        failed=sum(1 for r in runs if r.status == "FAILED"),
        decisions=dict(Counter(dec for r in runs if (dec := _decision(r)))),
        straight_through=len(touchless),
        straight_through_rate=len(touchless) / len(completed) if completed else None,
        median_cycle_ms=_median([r.duration_ms for r in completed if r.duration_ms is not None]),
        median_touchless_ms=_median(
            [r.duration_ms for r in touchless if r.duration_ms is not None]
        ),
        pending_value=sum((r.total or Decimal(0) for r in waiting), Decimal(0)),
        oldest_waiting_at=min((r.created_at for r in waiting), default=None),
        reviewed=sum(1 for r in runs if r.reviews),
        exception_causes=dict(causes.most_common()),
        funnel=funnel,
        volume=volume,
        recent=[RunOut.model_validate(r) for r in runs[:8]],
        erp_posted=sum(1 for p in postings if p.doc_number),
        erp_verified=len(verified),
        erp_mismatches=sum(1 for p in postings if p.verified is False),
        exceptions_by_owner=_owner_load(exceptions),
        corrections_by_vendor=dict(
            Counter(c.vendor_id or "unknown" for c in corrections).most_common()
        ),
        approvals_decided=sum(1 for a in approvals if a.decision != "PENDING"),
        payables_outstanding=owed.outstanding,
        payables_overdue=owed.overdue,
        payables_ageing=owed.ageing,
        payments_paid=owed.paid,
        payments_on_time_rate=owed.on_time_rate,
    )


def _owner_load(exceptions: list[CaseException]) -> list[OwnerLoad]:
    by_owner: dict[str, list[CaseException]] = {}
    for e in exceptions:
        by_owner.setdefault(e.owner_role, []).append(e)
    out = []
    for owner, items in sorted(by_owner.items(), key=lambda kv: -len(kv[1])):
        done = [e for e in items if e.resolved_at]
        out.append(
            OwnerLoad(
                owner=owner,
                open=sum(1 for e in items if e.status == "OPEN"),
                resolved=len(done),
                median_resolution_ms=_median(
                    [
                        int((e.resolved_at - e.created_at).total_seconds() * 1000)
                        for e in done
                        if e.resolved_at
                    ]
                ),
            )
        )
    return out


def _done_nodes(run: Run) -> set[str]:
    state: dict[str, Any] = run.state or {}
    done = set()
    if state.get("extraction"):
        done.add("extract")
    if state.get("assessment"):
        done.add("rules")
    return done


# --- evaluation ----------------------------------------------------------------------------------

EVAL_LATEST = Path(__file__).resolve().parents[3] / "data" / "eval" / "latest.json"


class EvalLatest(BaseModel):
    """The last `python -m scripts.eval --write` run (data/eval/latest.json)."""

    run_at: str
    live: bool
    models: list[str]
    summary: dict[str, Any]
    rag: dict[str, Any] | None = None


@router.get("/eval/latest", response_model=EvalLatest)
async def eval_latest() -> EvalLatest:
    if not EVAL_LATEST.exists():
        raise HTTPException(status_code=404, detail="no evaluation has been written yet")
    return EvalLatest.model_validate_json(EVAL_LATEST.read_text(encoding="utf-8"))
