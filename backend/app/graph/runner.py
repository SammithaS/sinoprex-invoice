"""Runs the graph in the background, persists its timeline and fans events out to SSE clients.

One run = one case = one LangGraph thread (`thread_id = run id`). The graph is driven with
`astream(stream_mode=["updates", "custom"])`: custom events (step started/finished, rule
results, pause) become `run_steps` rows and live SSE events; after each node update the run row
gets the latest state snapshot. An `__interrupt__` update means the graph paused in
`human_review`: the run is WAITING_REVIEW until `review()` resumes it with `Command(resume=…)`.
Because state lives in the checkpointer, a run interrupted by a restart is resumed by
`recover()` from its last checkpoint.
"""

import asyncio
import contextlib
import hashlib
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StreamMode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Invoice, Review, Run, RunStep
from app.graph.state import CaseState, ReviewInput

if TYPE_CHECKING:
    from app.integrations.notify import Notifier

log = logging.getLogger(__name__)

TERMINAL = ("COMPLETED", "FAILED")
STREAM_MODES: list[StreamMode] = ["updates", "custom"]
# Nodes whose output changes what the run page shows: refresh the run row after these only.
SNAPSHOT_NODES = frozenset(
    {
        "extract",
        "vendor_match",
        "retrieve_policy",
        "rules",
        "human_review",
        "approval_routing",
        "post_erp",
        "verify_post",
        "explain",
        "close_case",
    }
)
# What a paused run waits for, by the interrupt payload's `kind`.
WAITING = {"review": "WAITING_REVIEW", "erp": "WAITING_REVIEW", "approval": "WAITING_APPROVAL"}


class FourEyesError(ValueError):
    """The approver already acted on this case (policy §1.2, §7.3)."""


ACTIVE = ("QUEUED", "RUNNING")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class EventBroker:
    """In-process pub/sub of run events (one API process; n8n/webhooks come in Phase 10)."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subs[run_id].add(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs[run_id].discard(q)
        if not self._subs[run_id]:
            self._subs.pop(run_id, None)

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        for q in list(self._subs.get(run_id, ())):
            q.put_nowait(event)


class RunService:
    def __init__(
        self,
        graph: CompiledStateGraph[CaseState, None, CaseState, CaseState],
        sessionmaker: async_sessionmaker[AsyncSession],
        storage_dir: Path,
        broker: EventBroker | None = None,
        notifier: "Notifier | None" = None,
    ) -> None:
        self.graph = graph
        self.notifier = notifier
        self.sessionmaker = sessionmaker
        self.storage_dir = storage_dir
        self.broker = broker or EventBroker()
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # --- starting work ---------------------------------------------------------------------

    async def create_run(
        self,
        data: bytes,
        file_name: str,
        *,
        source: str = "UPLOAD",
        sample_key: str | None = None,
        rerun_of: str | None = None,
        intake_ref: str | None = None,
        intake_from: str | None = None,
    ) -> Run:
        digest = hashlib.sha256(data).hexdigest()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        path = self.storage_dir / f"{digest}.pdf"
        if not path.exists():
            await asyncio.to_thread(path.write_bytes, data)
        run = Run(
            id=str(uuid.uuid4()),
            status="QUEUED",
            file_name=file_name,
            file_path=str(path),
            file_hash=digest,
            sample_key=sample_key,
            source=source,
            rerun_of=rerun_of,
            intake_ref=intake_ref,
            intake_from=intake_from,
        )
        async with self.sessionmaker() as session:
            if rerun_of:
                # The earlier attempt no longer counts as a registered invoice (ASSUMPTIONS #56).
                old = await session.scalar(select(Invoice).where(Invoice.run_id == rerun_of))
                if old is not None:
                    old.status = "SUPERSEDED"
            session.add(run)
            await session.commit()
        self.start(run.id, self.initial_state(run))
        return run

    @staticmethod
    def initial_state(run: Run) -> CaseState:
        return {
            "run_id": run.id,
            "file_path": run.file_path,
            "file_name": run.file_name,
            "file_hash": run.file_hash,
            "sample_key": run.sample_key,
            "reviews": [],
            "errors": [],
        }

    def start(self, run_id: str, payload: CaseState | Command[Any] | None) -> None:
        task = asyncio.create_task(self._drive(run_id, payload), name=f"run-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))

    async def review(self, run_id: str, review: ReviewInput) -> Run:
        async with self.sessionmaker() as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            if run.status != "WAITING_REVIEW":
                raise ValueError(f"run is {run.status}, not waiting for review")
            allowed = (run.review_request or {}).get("actions") or ["APPROVE", "REJECT"]
            if review["action"] not in allowed:
                raise ValueError(
                    f"{review['action']} is not allowed here; use {', '.join(allowed)}"
                )
            session.add(
                Review(
                    run_id=run_id,
                    action=review["action"],
                    comment=review.get("comment"),
                    reviewer=review.get("reviewer"),
                )
            )
            run.status = "RUNNING"
            run.review_request = None
            await session.commit()
        self.broker.publish(run_id, {"type": "status", "status": "RUNNING"})
        self.start(run_id, Command(resume=dict(review)))
        return run

    async def decide_approval(
        self,
        run_id: str,
        approval_id: int,
        decision: str,
        approver: str,
        comment: str | None,
    ) -> Run:
        """Resume a run paused in approval_routing. Four-eyes: the approver must not already have
        reviewed or approved this case."""
        async with self.sessionmaker() as session:
            run = await session.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            request = run.review_request or {}
            if run.status != "WAITING_APPROVAL" or request.get("approval", {}).get("id") != (
                approval_id
            ):
                raise ValueError("this approval is not the one the case is waiting for")
            if not approver.strip():
                raise FourEyesError("an approver name is required")
            involved = {p.strip().lower() for p in request.get("already_involved") or []}
            if approver.strip().lower() in involved:
                raise FourEyesError(
                    f"{approver} already acted on this case; a different person must approve "
                    "(four-eyes)"
                )
            if decision == "REJECT" and not (comment or "").strip():
                raise ValueError("a reason is required to reject")
            run.status = "RUNNING"
            run.review_request = None
            await session.commit()
        self.broker.publish(run_id, {"type": "status", "status": "RUNNING"})
        self.start(
            run_id,
            Command(resume={"decision": decision, "approver": approver, "comment": comment}),
        )
        return run

    async def wait(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await task

    async def wait_all(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def recover(self) -> int:
        """Resume runs a restart cut off: from their checkpoint, or from scratch if none."""
        async with self.sessionmaker() as session:
            runs = (await session.scalars(select(Run).where(Run.status.in_(ACTIVE)))).all()
        for run in runs:
            snapshot = await self.graph.aget_state(self._config(run.id))
            payload: CaseState | None = None if snapshot.values else self.initial_state(run)
            log.info(
                "recovering run %s from %s", run.id, "checkpoint" if payload is None else "start"
            )
            self.start(run.id, payload)
        return len(runs)

    # --- driving the graph -------------------------------------------------------------------

    @staticmethod
    def _config(run_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": run_id}}

    async def _drive(self, run_id: str, payload: CaseState | Command[Any] | None) -> None:
        config = self._config(run_id)
        interrupt_value: dict[str, Any] | None = None
        error: str | None = None
        async with self.sessionmaker() as session:
            run = await session.get(Run, run_id)
            assert run is not None
            seq = (
                await session.scalar(select(func.max(RunStep.seq)).where(RunStep.run_id == run_id))
                or 0
            )
            run.status = "RUNNING"
            run.started_at = run.started_at or _now()
            await session.commit()
            self.broker.publish(run_id, {"type": "status", "status": "RUNNING"})
            try:
                async for part in self.graph.astream(payload, config, stream_mode=STREAM_MODES):
                    mode, chunk = part  # multi-mode stream yields (mode, chunk) tuples
                    if not isinstance(chunk, dict):
                        continue
                    if mode == "custom":
                        seq += 1
                        await self._record(session, run_id, seq, chunk)
                    elif "__interrupt__" in chunk:
                        interrupt_value = chunk["__interrupt__"][0].value
                    elif SNAPSHOT_NODES & chunk.keys():
                        await self._snapshot(session, run)  # also commits pending step rows
            except Exception as exc:  # the graph itself broke (nodes catch their own errors)
                log.exception("run %s crashed", run_id)
                error = f"{type(exc).__name__}: {exc}"[:500]
            await self._snapshot(session, run)
            state = (await self.graph.aget_state(config)).values
            if error or state.get("failed"):
                run.status = "FAILED"
                run.error = error or "; ".join(state.get("errors") or [])
            elif interrupt_value is not None:
                run.status = WAITING.get(interrupt_value.get("kind", "review"), "WAITING_REVIEW")
                run.review_request = interrupt_value
            else:
                run.status = "COMPLETED"
            if run.status in TERMINAL:
                run.finished_at = _now()
                if run.started_at:
                    run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
            await session.commit()
            self._notify(run, payload)
            self.broker.publish(
                run_id,
                {
                    "type": "status",
                    "status": run.status,
                    "decision": run.decision,
                    "final_decision": run.final_decision,
                    "error": run.error,
                },
            )

    def _notify(self, run: Run, payload: CaseState | Command[Any] | None) -> None:
        """A case that now waits for a person → n8n tells them; a request for information
        → n8n writes to the vendor (the case keeps waiting for the answer)."""
        if self.notifier is None or run.status not in ("WAITING_REVIEW", "WAITING_APPROVAL"):
            return
        from app.integrations.notify import REVIEW_NEEDED, VENDOR_FOLLOWUP

        resume = payload.resume if isinstance(payload, Command) else None
        if isinstance(resume, dict) and resume.get("action") == "REQUEST_INFO":
            context = {"comment": resume.get("comment"), "reviewer": resume.get("reviewer")}
            self.notifier.schedule(run.id, VENDOR_FOLLOWUP, context)
        else:
            self.notifier.schedule(run.id, REVIEW_NEEDED)

    async def _record(
        self, session: AsyncSession, run_id: str, seq: int, event: dict[str, Any]
    ) -> None:
        step = RunStep(
            run_id=run_id,
            seq=seq,
            type=event.get("type", "event"),
            node=event.get("node"),
            status=event.get("status"),
            message=event.get("message") or event.get("label"),
            duration_ms=event.get("duration_ms"),
            data={
                k: v
                for k, v in event.items()
                if k not in ("type", "node", "status", "message", "duration_ms")
            }
            or None,
        )
        session.add(step)
        # Live clients get the event now; the row is committed at the next step boundary
        # (one Neon round trip per stage instead of one per event — ASSUMPTIONS #64).
        self.broker.publish(run_id, {"seq": seq, **event})
        if step.type == "step_finished" and step.node not in SNAPSHOT_NODES:
            await session.commit()

    async def _snapshot(self, session: AsyncSession, run: Run) -> None:
        values = (await self.graph.aget_state(self._config(run.id))).values
        if not values:
            return
        extraction = values.get("extraction") or {}
        data = extraction.get("data") or {}
        assessment = values.get("assessment") or {}
        vendor = values.get("vendor") or {}
        run.decision = values.get("decision")
        run.final_decision = values.get("final_decision")
        run.vendor_id = vendor.get("id") or assessment.get("vendor_id")
        run.vendor_name = vendor.get("name") or data.get("vendor_name")
        run.invoice_number = data.get("invoice_number")
        run.total = data.get("total")
        run.confidence = assessment.get("min_key_confidence")
        run.procedure_version = values.get("procedure_version")
        run.explanation = values.get("explanation")
        run.state = {k: v for k, v in values.items() if k not in ("file_path",)}
        await session.commit()

    # --- reading events (SSE) ----------------------------------------------------------------

    async def events(self, run_id: str, after: int = 0) -> AsyncIterator[dict[str, Any]]:
        """Persisted steps after `after`, then live events, until the run ends.

        Subscribing before reading the table means nothing emitted in between is lost;
        duplicates are dropped by sequence number.
        """
        queue = self.broker.subscribe(run_id)
        try:
            async with self.sessionmaker() as session:
                run = await session.get(Run, run_id)
                if run is None:
                    return
                steps = (
                    await session.scalars(
                        select(RunStep)
                        .where(RunStep.run_id == run_id, RunStep.seq > after)
                        .order_by(RunStep.seq)
                    )
                ).all()
                status = run.status
                snapshot = {
                    "type": "status",
                    "status": run.status,
                    "decision": run.decision,
                    "final_decision": run.final_decision,
                    "error": run.error,
                }
            last = after
            for s in steps:
                last = s.seq
                yield {
                    "seq": s.seq,
                    "type": s.type,
                    "node": s.node,
                    "status": s.status,
                    "message": s.message,
                    "duration_ms": s.duration_ms,
                    **(s.data or {}),
                }
            yield snapshot
            if status in TERMINAL:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield {"type": "ping"}
                    continue
                if "seq" in event and event["seq"] <= last:
                    continue
                last = event.get("seq", last)
                yield event
                if event.get("type") == "status" and event.get("status") in TERMINAL:
                    return
        finally:
            self.broker.unsubscribe(run_id, queue)


# --- checkpointer --------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """Postgres (Neon) checkpointer when DATABASE_URL is Postgres, else in-memory (tests)."""
    url = settings.database_url
    if not url.startswith(("postgres://", "postgresql://")):
        yield InMemorySaver()
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    # Neon's direct URL works as-is for psycopg (sslmode/channel_binding are libpq options).
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=4,
        open=False,
        check=AsyncConnectionPool.check_connection,  # Neon drops idle connections
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    try:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
    finally:
        await pool.close()
