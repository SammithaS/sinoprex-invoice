"""Case records: exceptions, approvals, ERP postings, corrections (all idempotent per run,
because a node re-executes from its start when the graph resumes)."""

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Approval, CaseException, Correction, ErpPosting, Vendor
from app.rules.models import ApprovalRequirement, ExceptionItem

_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- exceptions ------------------------------------------------------------------------------


async def save_exceptions(
    session: AsyncSession, run_id: str, items: list[tuple[ExceptionItem, list[str]]]
) -> list[CaseException]:
    await session.execute(
        delete(CaseException).where(
            CaseException.run_id == run_id, CaseException.category != "ERP_MISMATCH"
        )
    )
    rows = [
        CaseException(
            run_id=run_id,
            category=e.category.value,
            rule_code=e.rule_code,
            summary=e.summary,
            variance=e.variance,
            citations=e.citations,
            owner_role=e.owner_role,
            owner_name=e.owner_name,
            recommendation=e.recommendation,
            question=e.question,
            attempted_steps=steps,
            status="OPEN",
        )
        for e, steps in items
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def resolve_exceptions(
    session: AsyncSession, run_id: str, status: str, resolution: str | None, by: str | None
) -> None:
    rows = await session.scalars(
        select(CaseException).where(CaseException.run_id == run_id, CaseException.status == "OPEN")
    )
    for row in rows:
        if row.category == "ERP_MISMATCH":
            continue
        row.status, row.resolution, row.resolved_by, row.resolved_at = status, resolution, by, now()
    await session.commit()


async def open_erp_mismatch(
    session: AsyncSession, run_id: str, summary: str, question: str, owner: str | None
) -> None:
    existing = await session.scalar(
        select(CaseException).where(
            CaseException.run_id == run_id,
            CaseException.category == "ERP_MISMATCH",
            CaseException.status == "OPEN",
        )
    )
    if existing is None:
        session.add(
            CaseException(
                run_id=run_id,
                category="ERP_MISMATCH",
                rule_code="erp.readback",
                summary=summary,
                citations=["ap-policy §10.1"],
                owner_role="AP Manager",
                owner_name=owner,
                recommendation="Correct the document in the ERP, then re-check the posting.",
                question=question,
                attempted_steps=["Read the posted document back from the ERP and compared it."],
                status="OPEN",
            )
        )
    else:
        existing.summary = summary
    await session.commit()


async def resolve_erp_mismatch(session: AsyncSession, run_id: str) -> None:
    rows = await session.scalars(
        select(CaseException).where(
            CaseException.run_id == run_id,
            CaseException.category == "ERP_MISMATCH",
            CaseException.status == "OPEN",
        )
    )
    for row in rows:
        row.status, row.resolution, row.resolved_at = "RESOLVED", "Read-back matches.", now()
    await session.commit()


# --- approvals -------------------------------------------------------------------------------


def _kind(req: ApprovalRequirement) -> str:
    return {
        "ap-policy §7.1": "MATRIX",
        "ap-policy §7.2": "NON_PO",
        "ap-policy §6.2": "BANK_CHANGE",
        "ap-policy §7.3": "OVERRIDE",
    }.get(req.citation, "MATRIX")


async def ensure_approvals(
    session: AsyncSession, run_id: str, reqs: list[ApprovalRequirement]
) -> list[Approval]:
    """One row per required approver, in order; rows that exist keep their decisions."""
    existing = {
        a.level: a for a in await session.scalars(select(Approval).where(Approval.run_id == run_id))
    }
    for level, req in enumerate(reqs, start=1):
        if level in existing:
            continue
        kind = _kind(req)
        session.add(
            Approval(
                run_id=run_id,
                level=level,
                kind=kind,
                role=req.role,
                approver_name=req.name,
                reason=req.reason,
                citation=req.citation,
                is_second_approver=level > 1 and kind in ("BANK_CHANGE", "OVERRIDE"),
            )
        )
    await session.commit()
    rows = await session.scalars(
        select(Approval).where(Approval.run_id == run_id).order_by(Approval.level)
    )
    return list(rows)


async def decide_approval(
    session: AsyncSession, approval_id: int, decision: str, by: str | None, comment: str | None
) -> Approval | None:
    row = await session.get(Approval, approval_id)
    if row is None or row.decision != "PENDING":
        return row
    row.decision, row.decided_by, row.comment, row.decided_at = decision, by, comment, now()
    await session.commit()
    return row


def approval_dict(a: Approval) -> dict[str, Any]:
    return {
        "id": a.id,
        "level": a.level,
        "kind": a.kind,
        "role": a.role,
        "approver_name": a.approver_name,
        "reason": a.reason,
        "citation": a.citation,
        "is_second_approver": a.is_second_approver,
        "decision": a.decision,
        "decided_by": a.decided_by,
        "comment": a.comment,
    }


# --- ERP postings ----------------------------------------------------------------------------


async def save_posting(
    session: AsyncSession, run_id: str, request: dict[str, Any], response: dict[str, Any]
) -> ErpPosting:
    row = await session.scalar(select(ErpPosting).where(ErpPosting.run_id == run_id))
    if row is None:
        row = ErpPosting(run_id=run_id, request=request)
        session.add(row)
    row.request, row.response, row.doc_number = request, response, response.get("doc_number")
    await session.commit()
    return row


async def save_readback(
    session: AsyncSession, run_id: str, readback: dict[str, Any] | None, mismatches: list[str]
) -> None:
    row = await session.scalar(select(ErpPosting).where(ErpPosting.run_id == run_id))
    if row is not None:
        row.readback, row.mismatches, row.verified = readback, mismatches, not mismatches
        await session.commit()


# --- corrections → extraction hints ----------------------------------------------------------


async def hints_for_text(session: AsyncSession, text: str, limit: int = 5) -> list[str]:
    """Recent reviewer corrections for the vendor(s) whose GSTIN appears in the text layer."""
    gstins = set(_GSTIN.findall(text.upper()))
    if not gstins:
        return []
    vendor_ids = list(await session.scalars(select(Vendor.id).where(Vendor.gstin.in_(gstins))))
    if not vendor_ids:
        return []
    rows = await session.scalars(
        select(Correction)
        .where(Correction.vendor_id.in_(vendor_ids))
        .order_by(Correction.created_at.desc())
        .limit(limit)
    )
    return [
        f"{c.field}: an earlier reading was {c.old_value!r}; the reviewer corrected it to "
        f"{c.new_value!r}."
        for c in rows
    ]
