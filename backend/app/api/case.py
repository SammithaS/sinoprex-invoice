"""Phase 8 endpoints: approvals inbox, corrections, evidence bundle, mock ERP, demo reset."""

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.api.runs import ServiceDep
from app.api.schemas import RunOut
from app.db.models import (
    Approval,
    CaseException,
    Correction,
    ErpDocument,
    ErpPosting,
    Review,
    Run,
    RunStep,
)
from app.db.seed import seed_reference_data
from app.deps import SessionDep
from app.erp import MockErp
from app.evidence import build_bundle, bundle_bytes, bundle_pdf
from app.graph.repo import clear_reference_cache
from app.graph.runner import FourEyesError

router = APIRouter()


class _Orm(BaseModel):
    model_config = {"from_attributes": True}


class ExceptionOut(_Orm):
    id: int
    category: str
    summary: str
    variance: dict[str, Any] | None
    citations: list[str] | None
    owner_role: str
    owner_name: str | None
    recommendation: str | None
    question: str
    attempted_steps: list[str] | None
    status: str
    resolution: str | None
    resolved_by: str | None
    resolved_at: datetime | None
    created_at: datetime


class ApprovalOut(_Orm):
    id: int
    run_id: str
    level: int
    kind: str
    role: str
    approver_name: str | None
    reason: str
    citation: str | None
    is_second_approver: bool
    decision: str
    decided_by: str | None
    comment: str | None
    decided_at: datetime | None


class ErpPostingOut(_Orm):
    doc_number: str | None
    request: dict[str, Any]
    readback: dict[str, Any] | None
    verified: bool | None
    mismatches: list[str] | None
    created_at: datetime


class CorrectionOut(_Orm):
    id: int
    field: str
    old_value: str | None
    new_value: str
    reviewer: str | None
    created_at: datetime


class CaseRecordsOut(BaseModel):
    exceptions: list[ExceptionOut]
    approvals: list[ApprovalOut]
    erp: ErpPostingOut | None
    corrections: list[CorrectionOut]


@router.get("/runs/{run_id}/records", response_model=CaseRecordsOut, tags=["runs"])
async def case_records(session: SessionDep, run_id: str) -> CaseRecordsOut:
    """Exceptions, approvals, ERP posting and corrections of one case."""
    if await session.get(Run, run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return CaseRecordsOut(
        exceptions=[
            ExceptionOut.model_validate(e)
            for e in await session.scalars(
                select(CaseException)
                .where(CaseException.run_id == run_id)
                .order_by(CaseException.id)
            )
        ],
        approvals=[
            ApprovalOut.model_validate(a)
            for a in await session.scalars(
                select(Approval).where(Approval.run_id == run_id).order_by(Approval.level)
            )
        ],
        erp=(
            ErpPostingOut.model_validate(p)
            if (p := await session.scalar(select(ErpPosting).where(ErpPosting.run_id == run_id)))
            else None
        ),
        corrections=[
            CorrectionOut.model_validate(c)
            for c in await session.scalars(
                select(Correction).where(Correction.run_id == run_id).order_by(Correction.id)
            )
        ],
    )


# --- approvals ---------------------------------------------------------------------------------


class InboxItem(BaseModel):
    approval: ApprovalOut
    run: RunOut
    request: dict[str, Any]


class DecideIn(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    approver: str
    comment: str | None = None


@router.get("/approvals/inbox", response_model=list[InboxItem], tags=["approvals"])
async def approvals_inbox(session: SessionDep) -> list[InboxItem]:
    """Approvals a case is waiting for right now (one per paused run), oldest first."""
    runs = await session.scalars(
        select(Run).where(Run.status == "WAITING_APPROVAL").order_by(Run.created_at)
    )
    out = []
    for run in runs:
        request = run.review_request or {}
        approval_id = (request.get("approval") or {}).get("id")
        row = await session.get(Approval, approval_id) if approval_id else None
        if row is not None:
            out.append(
                InboxItem(
                    approval=ApprovalOut.model_validate(row),
                    run=RunOut.model_validate(run),
                    request=request,
                )
            )
    return out


@router.post("/approvals/{approval_id}/decide", response_model=RunOut, tags=["approvals"])
async def decide(service: ServiceDep, session: SessionDep, approval_id: int, body: DecideIn) -> Run:
    row = await session.get(Approval, approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail="approval not found")
    try:
        return await service.decide_approval(
            row.run_id, approval_id, body.decision, body.approver, body.comment
        )
    except FourEyesError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


# --- corrections -------------------------------------------------------------------------------


class CorrectionIn(BaseModel):
    field: str
    new_value: str
    reviewer: str | None = None


@router.post(
    "/runs/{run_id}/corrections", response_model=CorrectionOut, status_code=201, tags=["runs"]
)
async def add_correction(session: SessionDep, run_id: str, body: CorrectionIn) -> Correction:
    """Record a reviewer's correction; recent ones for the vendor become extraction hints."""
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    evidence = ((run.state or {}).get("extraction") or {}).get("evidence") or {}
    coding = ((run.state or {}).get("assessment") or {}).get("proposed_coding") or {}
    if body.field in evidence:
        old = evidence[body.field].get("value")
    elif body.field in ("gl_account", "cost_centre") and coding:
        old = coding.get(body.field)
    else:
        raise HTTPException(status_code=422, detail=f"unknown field {body.field!r}")
    row = Correction(
        run_id=run_id,
        vendor_id=run.vendor_id,
        field=body.field,
        old_value=old,
        new_value=body.new_value.strip(),
        reviewer=body.reviewer,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# --- evidence bundle ---------------------------------------------------------------------------


@router.get("/cases/{run_id}/evidence", tags=["cases"])
async def evidence(session: SessionDep, run_id: str) -> dict[str, Any]:
    bundle = await build_bundle(session, run_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="case not found")
    return bundle


@router.get("/cases/{run_id}/evidence.json", tags=["cases"])
async def evidence_json(session: SessionDep, run_id: str) -> Response:
    bundle = await build_bundle(session, run_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="case not found")
    return Response(
        bundle_bytes(bundle),
        media_type="application/json",
        headers={"content-disposition": f'attachment; filename="case-{run_id}.json"'},
    )


@router.get("/cases/{run_id}/evidence.pdf", tags=["cases"])
async def evidence_pdf(session: SessionDep, run_id: str) -> Response:
    bundle = await build_bundle(session, run_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="case not found")
    pdf = await asyncio.to_thread(bundle_pdf, bundle)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"content-disposition": f'attachment; filename="case-{run_id}.pdf"'},
    )


# --- mock ERP ----------------------------------------------------------------------------------


class ErpDocumentRow(_Orm):
    doc_number: str
    idempotency_key: str
    vendor_id: str
    invoice_number: str
    invoice_date: date
    po_number: str | None
    amount: Decimal
    tax_amount: Decimal
    gl_account: str | None
    cost_centre: str | None
    attachment_url: str | None
    status: str
    created_at: datetime
    updated_at: datetime | None


class LedgerRow(BaseModel):
    document: ErpDocumentRow
    run_id: str | None
    verified: bool | None
    mismatches: list[str] | None


class CorrectIn(BaseModel):
    amount: Decimal


def get_erp(request: Request) -> MockErp:
    from app.db.session import get_sessionmaker

    override = getattr(request.app.state, "erp", None)
    return override if isinstance(override, MockErp) else MockErp(get_sessionmaker())


ErpDep = Annotated[MockErp, Depends(get_erp)]


@router.get("/erp/documents", response_model=list[LedgerRow], tags=["erp"])
async def ledger(session: SessionDep) -> list[LedgerRow]:
    docs = list(await session.scalars(select(ErpDocument).order_by(ErpDocument.created_at.desc())))
    postings = {
        p.doc_number: p
        for p in await session.scalars(select(ErpPosting).where(ErpPosting.doc_number.is_not(None)))
    }
    return [
        LedgerRow(
            document=ErpDocumentRow.model_validate(d),
            run_id=d.idempotency_key,
            verified=postings[d.doc_number].verified if d.doc_number in postings else None,
            mismatches=postings[d.doc_number].mismatches if d.doc_number in postings else None,
        )
        for d in docs
    ]


@router.get("/erp/documents/{doc_number}", response_model=ErpDocumentRow, tags=["erp"])
async def read_document(session: SessionDep, doc_number: str) -> ErpDocument:
    doc = await session.scalar(select(ErpDocument).where(ErpDocument.doc_number == doc_number))
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    return doc


@router.post("/erp/documents/{doc_number}/correct", response_model=ErpDocumentRow, tags=["erp"])
async def correct_document(
    erp: ErpDep, session: SessionDep, doc_number: str, body: CorrectIn
) -> ErpDocument:
    """What an AP clerk does in the ERP after a read-back mismatch; then re-check the case."""
    if await erp.correct(doc_number, body.amount) is None:
        raise HTTPException(status_code=404, detail="document not found")
    doc = await session.scalar(select(ErpDocument).where(ErpDocument.doc_number == doc_number))
    assert doc is not None
    await session.refresh(doc)
    return doc


# --- demo reset --------------------------------------------------------------------------------


class ResetOut(BaseModel):
    runs_deleted: int
    erp_documents_deleted: int


@router.post("/admin/reset-demo", response_model=ResetOut, tags=["admin"])
async def reset_demo(request: Request, session: SessionDep) -> ResetOut:
    """Start the demo over: remove every run (and its records and checkpoints), the mock ERP
    ledger and the invoice history, then reload the seed. Procedure versions are kept."""
    run_ids = list(await session.scalars(select(Run.id)))
    erp_count = len(list(await session.scalars(select(ErpDocument.id))))
    await session.execute(delete(ErpDocument))
    await seed_reference_data(session)  # also clears invoice history (incl. run rows)
    for model in (RunStep, Review, CaseException, Approval, ErpPosting, Correction, Run):
        await session.execute(delete(model))  # children first (SQLite has no FK cascade)
    await session.commit()
    clear_reference_cache()
    service = getattr(request.app.state, "run_service", None)
    checkpointer = getattr(getattr(service, "graph", None), "checkpointer", None)
    if checkpointer is not None:
        for run_id in run_ids:
            await checkpointer.adelete_thread(run_id)
    return ResetOut(runs_deleted=len(run_ids), erp_documents_deleted=erp_count)
