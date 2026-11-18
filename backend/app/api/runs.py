"""Runs: upload, list, detail, live events (SSE), review/resume, rerun, samples (plan §11)."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import ReviewIn, RunDetailOut, RunOut, SampleOut
from app.db.models import Run
from app.deps import SessionDep
from app.graph.runner import RunService
from app.graph.state import ReviewInput

router = APIRouter(tags=["runs"])

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "data" / "test-invoices"
MAX_UPLOAD = 10 * 1024 * 1024


def get_run_service(request: Request) -> RunService:
    service: RunService | None = getattr(request.app.state, "run_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="run service is not available")
    return service


ServiceDep = Annotated[RunService, Depends(get_run_service)]


async def _detail(session: AsyncSession, run_id: str) -> Run:
    run = await session.scalar(
        select(Run)
        .where(Run.id == run_id)
        .options(selectinload(Run.steps), selectinload(Run.reviews))
    )
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.post("/runs", response_model=RunOut, status_code=202)
async def create_run(service: ServiceDep, file: Annotated[UploadFile, File()]) -> Run:
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="file larger than 10 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="only PDF files are accepted")
    return await service.create_run(data, file.filename or "invoice.pdf")


@router.get("/runs", response_model=list[RunOut])
async def list_runs(session: SessionDep, status: str | None = None, limit: int = 100) -> list[Run]:
    q = select(Run).order_by(Run.created_at.desc()).limit(min(limit, 500))
    if status:
        q = q.where(Run.status == status)
    return list(await session.scalars(q))


@router.get("/runs/{run_id}", response_model=RunDetailOut)
async def get_run(session: SessionDep, run_id: str) -> Run:
    return await _detail(session, run_id)


@router.get("/runs/{run_id}/file")
async def get_run_file(session: SessionDep, run_id: str) -> FileResponse:
    run = await session.get(Run, run_id)
    if run is None or not await asyncio.to_thread(Path(run.file_path).exists):
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(run.file_path, media_type="application/pdf", filename=run.file_name)


PAGE_SCALE = 2  # PNG pixels per PDF point: evidence boxes (PDF points) × 2 = pixels


def _render_page(path: str, page_no: int) -> bytes | None:
    import pymupdf

    with pymupdf.open(path) as doc:
        if not 1 <= page_no <= doc.page_count:
            return None
        pix = doc[page_no - 1].get_pixmap(matrix=pymupdf.Matrix(PAGE_SCALE, PAGE_SCALE))
        data: bytes = pix.tobytes("png")
        return data


@router.get("/runs/{run_id}/pages/{page_no}")
async def get_run_page(session: SessionDep, run_id: str, page_no: int) -> Response:
    """Page image for the preview. 1 PDF point = 2 px, so evidence bboxes overlay directly."""
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    png = await asyncio.to_thread(_render_page, run.file_path, page_no)
    if png is None:
        raise HTTPException(status_code=404, detail="page not found")
    return Response(png, media_type="image/png", headers={"cache-control": "private, max-age=3600"})


@router.get("/runs/{run_id}/events", response_class=EventSourceResponse)
async def run_events(
    service: ServiceDep,
    session: SessionDep,
    run_id: str,
    after: int = 0,
    last_event_id: Annotated[str | None, Header()] = None,
) -> AsyncIterator[ServerSentEvent]:
    """Persisted timeline first (so a page refresh restores it), then live events.

    A reconnecting EventSource sends `Last-Event-ID`; it wins over `?after=`.
    """
    if await session.get(Run, run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    if last_event_id and last_event_id.isdigit():
        after = int(last_event_id)
    async for event in service.events(run_id, after):
        yield ServerSentEvent(
            event=str(event.get("type", "message")),
            id=str(event["seq"]) if "seq" in event else None,
            raw_data=json.dumps(event, default=str, ensure_ascii=False),
        )


@router.post("/runs/{run_id}/review", response_model=RunOut, status_code=202)
async def review_run(service: ServiceDep, run_id: str, body: ReviewIn) -> Run:
    review: ReviewInput = {"action": body.action}
    if body.comment:
        review["comment"] = body.comment
    if body.reviewer:
        review["reviewer"] = body.reviewer
    try:
        return await service.review(run_id, review)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/runs/{run_id}/rerun", response_model=RunOut, status_code=202)
async def rerun(service: ServiceDep, session: SessionDep, run_id: str) -> Run:
    old = await session.get(Run, run_id)
    if old is None:
        raise HTTPException(status_code=404, detail="run not found")
    if old.status not in ("COMPLETED", "FAILED"):
        raise HTTPException(status_code=409, detail=f"run is {old.status}")
    data = await asyncio.to_thread(Path(old.file_path).read_bytes)
    return await service.create_run(
        data, old.file_name, source=old.source, sample_key=old.sample_key, rerun_of=old.id
    )


def _manifest() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(
        (SAMPLES_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    return data


@router.get("/samples", response_model=list[SampleOut])
async def list_samples() -> list[SampleOut]:
    return [
        SampleOut(
            key=m["key"],
            title=m["title"],
            edge_case=m["edge_case"],
            is_scanned=m["is_scanned"],
            expected_decision=m["expected_decision"],
            depends_on=m["depends_on"],
        )
        for m in _manifest()
    ]


@router.post("/samples/{key}/run", response_model=RunOut, status_code=202)
async def run_sample(service: ServiceDep, key: str) -> Run:
    sample = next((m for m in _manifest() if m["key"] == key), None)
    if sample is None:
        raise HTTPException(status_code=404, detail="unknown sample")
    data = (SAMPLES_DIR / sample["file"]).read_bytes()
    return await service.create_run(data, sample["file"], source="SAMPLE", sample_key=key)
