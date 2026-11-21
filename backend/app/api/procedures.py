"""Operating Procedure versions (list, create, activate)."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app import procedure
from app.db.models import Procedure
from app.deps import SessionDep
from app.rules.policy import Policy

router = APIRouter(tags=["procedure"])


class ProcedureOut(BaseModel):
    version: int
    label: str
    active: bool
    note: str | None
    created_by: str | None
    created_at: datetime
    policy: Policy
    changes: list[dict[str, Any]]  # vs the previous version


class ProcedureIn(BaseModel):
    policy: Policy
    note: str | None = None
    created_by: str | None = None
    activate: bool = True


def _out(p: Procedure, previous: Procedure | None) -> ProcedureOut:
    return ProcedureOut(
        version=p.version,
        label=procedure.label(p.version),
        active=p.active,
        note=p.note,
        created_by=p.created_by,
        created_at=p.created_at,
        policy=Policy.model_validate({**p.policy, "version": procedure.label(p.version)}),
        changes=procedure.diff(previous.policy, p.policy) if previous else [],
    )


@router.get("/procedures", response_model=list[ProcedureOut])
async def list_procedures(session: SessionDep) -> list[ProcedureOut]:
    await procedure.ensure_initial(session)
    rows = list(await session.scalars(select(Procedure).order_by(Procedure.version)))
    outs = [_out(p, rows[i - 1] if i else None) for i, p in enumerate(rows)]
    return list(reversed(outs))


@router.get("/procedures/active", response_model=ProcedureOut)
async def active_procedure(session: SessionDep) -> ProcedureOut:
    p = await procedure.ensure_initial(session)
    prev = await session.get(Procedure, p.version - 1)
    return _out(p, prev)


@router.post("/procedures", response_model=ProcedureOut, status_code=201)
async def create_procedure(session: SessionDep, body: ProcedureIn) -> ProcedureOut:
    p = await procedure.create_version(
        session, body.policy, body.note, body.created_by, body.activate
    )
    await session.refresh(p)
    return _out(p, await session.get(Procedure, p.version - 1))


@router.post("/procedures/{version}/activate", response_model=ProcedureOut)
async def activate_procedure(session: SessionDep, version: int) -> ProcedureOut:
    p = await procedure.activate(session, version)
    if p is None:
        raise HTTPException(status_code=404, detail="procedure version not found")
    return _out(p, await session.get(Procedure, version - 1))
