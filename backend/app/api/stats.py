"""Dashboard KPIs (plan §2b.9, §11 `GET /stats`).

Computed from the `runs` table in Python — fine at demo scale (hundreds of runs). ERP posting
and correction metrics join in Phase 8.
"""

import statistics
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.schemas import RunOut
from app.db.models import Run
from app.deps import SessionDep

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


class Stats(BaseModel):
    runs: int
    completed: int
    waiting_review: int
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
    waiting = [r for r in runs if r.status == "WAITING_REVIEW"]
    touchless = [r for r in completed if r.decision == "APPROVED" and not r.reviews]

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
        waiting_review=len(waiting),
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
    )


def _done_nodes(run: Run) -> set[str]:
    state: dict[str, Any] = run.state or {}
    done = set()
    if state.get("extraction"):
        done.add("extract")
    if state.get("assessment"):
        done.add("rules")
    return done
