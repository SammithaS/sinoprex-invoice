"""Operating Procedure versions, exception routing, approvals with four-eyes, mock ERP
posting + read-back, corrections → extraction hints, evidence bundle, demo reset."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import procedure
from app.db.models import Approval, CaseException, ErpDocument, ErpPosting, Run
from app.erp import ErpDocumentIn, MockErp, compare
from app.evidence import build_bundle, bundle_pdf
from app.extraction import ExtractionResult
from app.graph.runner import FourEyesError, RunService
from app.rules.policy import Policy
from tests.test_graph import (  # noqa: F401  (fixtures: db, service, api)
    INVOICES,
    api,
    db,
    make_service,
    reload,
    replay_extract,
    run_sample,
    service,
)


async def approvals_of(service: RunService, run_id: str) -> list[Approval]:
    async with service.sessionmaker() as session:
        return list(
            await session.scalars(
                select(Approval).where(Approval.run_id == run_id).order_by(Approval.level)
            )
        )


async def exceptions_of(service: RunService, run_id: str) -> list[CaseException]:
    async with service.sessionmaker() as session:
        return list(
            await session.scalars(select(CaseException).where(CaseException.run_id == run_id))
        )


async def pending_id(service: RunService, run_id: str) -> int:
    run = await reload(service, run_id)
    assert run.status == "WAITING_APPROVAL", (run.status, run.error)
    approval_id: int = (run.review_request or {})["approval"]["id"]
    return approval_id


async def approve(service: RunService, run_id: str, who: str, decision: str = "APPROVE") -> Run:
    await service.decide_approval(run_id, await pending_id(service, run_id), decision, who, "ok")
    await service.wait(run_id)
    return await reload(service, run_id)


# --- procedure versions --------------------------------------------------------------------------


async def test_initial_procedure_is_the_policy_defaults(
    db: async_sessionmaker[AsyncSession],
) -> None:
    async with db() as session:
        policy = await procedure.active_policy(session)
    assert policy.version == "v1" and policy.price_tolerance_pct == Decimal("2")


async def test_changing_the_tolerance_changes_the_next_case(
    db: async_sessionmaker[AsyncSession], service: RunService
) -> None:
    """1.2% over the PO passes at 2%, is flagged at 1%."""
    first = await run_sample(service, "sharma-within-tolerance")
    assert first.decision == "APPROVED" and first.procedure_version == "v1"

    async with db() as session:
        strict = Policy(price_tolerance_pct=Decimal("1"))
        v2 = await procedure.create_version(session, strict, "Tighter price tolerance", "QA", True)
    assert v2.version == 2 and procedure.diff(Policy().model_dump(mode="json"), v2.policy) == [
        {"path": "price_tolerance_pct", "old": "2", "new": "1"}
    ]

    # Rerun the same invoice: the earlier attempt is superseded, so only the procedure differs.
    pdf = (INVOICES / "sharma-within-tolerance.pdf").read_bytes()
    again = await service.create_run(
        pdf, first.file_name, sample_key=first.sample_key, rerun_of=first.id
    )
    await service.wait(again.id)
    again = await reload(service, again.id)
    assert again.procedure_version == "v2" and again.decision == "NEEDS_REVIEW"
    assert [e.category for e in await exceptions_of(service, again.id)] == ["PRICE_VARIANCE"]


# --- approvals --------------------------------------------------------------------------------


async def test_amount_above_limit_waits_for_the_finance_manager(service: RunService) -> None:
    run = await run_sample(service, "northwind-over-approval-limit")
    assert run.status == "WAITING_APPROVAL" and run.decision == "NEEDS_REVIEW"
    (row,) = await approvals_of(service, run.id)
    assert (row.role, row.approver_name, row.kind) == (
        "Finance Manager",
        "Meera Krishnan",
        "MATRIX",
    )

    run = await approve(service, run.id, "Meera Krishnan")
    assert run.status == "COMPLETED" and run.final_decision == "APPROVED"
    (row,) = await approvals_of(service, run.id)
    assert row.decision == "APPROVED" and row.decided_by == "Meera Krishnan"


async def test_bank_change_needs_two_different_approvers(service: RunService) -> None:
    run = await run_sample(service, "rangoli-bank-change")
    assert run.status == "WAITING_REVIEW"  # the Vendor Master team verifies first
    await service.review(
        run.id,
        {"action": "APPROVE", "comment": "Confirmed by phone", "reviewer": "Lakshmi Pillai"},
    )
    await service.wait(run.id)
    first, second = await approvals_of(service, run.id)
    assert (first.role, second.role) == ("AP Manager", "Finance Controller")
    assert second.is_second_approver

    with pytest.raises(FourEyesError):  # the reviewer can't also approve
        await service.decide_approval(run.id, first.id, "APPROVE", "Lakshmi Pillai", None)
    run = await approve(service, run.id, "Arjun Desai")
    assert run.status == "WAITING_APPROVAL"
    with pytest.raises(FourEyesError):  # nor can the first approver take the second level
        await service.decide_approval(run.id, second.id, "APPROVE", "arjun desai", None)
    run = await approve(service, run.id, "Sanjay Batra")
    assert run.status == "COMPLETED" and run.final_decision == "APPROVED"
    assert [e.status for e in await exceptions_of(service, run.id)] == ["OVERRIDDEN"]


async def test_override_of_a_price_variance_needs_a_second_approver(service: RunService) -> None:
    run = await run_sample(service, "technova-contract-rate")
    excs = await exceptions_of(service, run.id)
    assert [e.category for e in excs] == ["PRICE_VARIANCE"]
    assert any("amendment" in s for s in excs[0].attempted_steps or [])
    await service.review(
        run.id, {"action": "APPROVE", "comment": "Amendment attached", "reviewer": "Rahul Menon"}
    )
    await service.wait(run.id)
    (row,) = await approvals_of(service, run.id)
    assert (row.kind, row.role, row.citation) == ("OVERRIDE", "AP Manager", "ap-policy §7.3")
    run = await approve(service, run.id, "Arjun Desai")
    assert run.final_decision == "APPROVED"
    # The ERP posting records both the reviewer and the second approver.
    async with service.sessionmaker() as session:
        posting = await session.scalar(select(ErpPosting).where(ErpPosting.run_id == run.id))
    assert posting is not None
    payload = posting.request["payload"]
    assert [r["reviewer"] for r in payload["approvals"]] == ["Rahul Menon"]
    assert [(x["role"], x["decision"], x["by"]) for x in payload["sign_offs"]] == [
        ("AP Manager", "APPROVED", "Arjun Desai")
    ]


async def test_an_approver_can_reject(service: RunService) -> None:
    run = await run_sample(service, "northwind-over-approval-limit")
    with pytest.raises(ValueError):  # a rejection needs a reason
        await service.decide_approval(run.id, await pending_id(service, run.id), "REJECT", "M", "")
    run = await approve(service, run.id, "Meera Krishnan", decision="REJECT")
    assert run.status == "COMPLETED" and run.final_decision == "REJECTED"
    async with service.sessionmaker() as session:
        assert await session.scalar(select(ErpPosting).where(ErpPosting.run_id == run.id)) is None


async def test_non_po_invoice_is_coded_and_approved_by_the_department_head(
    service: RunService,
) -> None:
    run = await run_sample(service, "cloudnest-saas-nonpo")
    (row,) = await approvals_of(service, run.id)
    assert (row.kind, row.role, row.approver_name) == (
        "NON_PO",
        "Department Head — IT",
        "Rahul Menon",
    )
    run = await approve(service, run.id, "Rahul Menon")
    async with service.sessionmaker() as session:
        doc = await session.scalar(select(ErpDocument).where(ErpDocument.idempotency_key == run.id))
    assert doc is not None and (doc.gl_account, doc.cost_centre) == ("6420", "CC-IT-01")


async def test_unknown_supplier_cannot_be_approved(service: RunService) -> None:
    run = await run_sample(service, "sunrise-unknown-vendor")
    assert run.status == "WAITING_REVIEW" and "APPROVE" not in (run.review_request or {})["actions"]
    with pytest.raises(ValueError):
        await service.review(run.id, {"action": "APPROVE"})


# --- ERP ----------------------------------------------------------------------------------------


async def test_clean_invoice_is_posted_and_verified(service: RunService) -> None:
    run = await run_sample(service, "northwind-clean")
    async with service.sessionmaker() as session:
        posting = await session.scalar(select(ErpPosting).where(ErpPosting.run_id == run.id))
        doc = await session.scalar(select(ErpDocument).where(ErpDocument.idempotency_key == run.id))
    assert posting is not None and posting.verified and not posting.mismatches
    assert doc is not None and doc.amount == Decimal("345150.00")
    assert (doc.gl_account, doc.cost_centre, doc.po_number) == ("5120", "CC-FAC-05", "PO-2026-0041")


async def test_erp_readback_mismatch_keeps_the_case_open_until_fixed(service: RunService) -> None:
    """Edge case #8: the mock ERP drops a paisa; the case stays open until it's corrected."""
    run = await run_sample(service, "malabar-erp-mismatch")
    assert run.status == "WAITING_REVIEW" and run.decision == "APPROVED"
    request = run.review_request or {}
    assert request["kind"] == "erp" and request["actions"] == ["RECHECK"]
    assert "148679.99" in request["headline"] and "148680" in request["headline"]
    (exc,) = await exceptions_of(service, run.id)
    assert (exc.category, exc.status) == ("ERP_MISMATCH", "OPEN")

    await service.review(run.id, {"action": "RECHECK"})  # not fixed yet → pauses again
    await service.wait(run.id)
    assert (await reload(service, run.id)).status == "WAITING_REVIEW"

    erp = MockErp(service.sessionmaker)
    await erp.correct(request["doc_number"], Decimal("148680.00"))
    await service.review(run.id, {"action": "RECHECK", "reviewer": "Arjun Desai"})
    await service.wait(run.id)
    run = await reload(service, run.id)
    assert run.status == "COMPLETED" and run.final_decision == "APPROVED"
    (exc,) = await exceptions_of(service, run.id)
    assert exc.status == "RESOLVED"


async def test_erp_posting_is_idempotent(db: async_sessionmaker[AsyncSession]) -> None:
    erp = MockErp(db)
    doc = ErpDocumentIn(
        idempotency_key="run-1",
        vendor_id="V001",
        invoice_number="X-1",
        invoice_date=date(2026, 11, 10),
        amount=Decimal("100.00"),
        tax_amount=Decimal("18.00"),
        gl_account="5110",
        cost_centre="CC-IT-01",
        attachment_url="/runs/run-1/file",
    )
    first, again = await erp.post(doc), await erp.post(doc)
    assert first.created and not again.created and first.doc_number == again.doc_number
    assert compare(doc, await erp.read(first.doc_number), first.doc_number) == []
    # a re-run (new run id) of the same vendor + invoice number gets the original document back
    rerun = await erp.post(doc.model_copy(update={"idempotency_key": "run-2"}))
    assert not rerun.created and rerun.doc_number == first.doc_number
    other = doc.model_copy(update={"idempotency_key": "run-3", "invoice_number": "X-2"})
    faulty = await erp.post(other, "amount_minus_one_paisa")
    assert faulty.created and faulty.doc_number != first.doc_number
    assert compare(other, faulty, faulty.doc_number)[0].startswith("posted amount 99.99")


# --- corrections, evidence, reset ---------------------------------------------------------------


async def test_corrections_become_extraction_hints(
    db: async_sessionmaker[AsyncSession], tmp_path: Path, api: AsyncClient
) -> None:
    seen: list[Sequence[str]] = []

    async def spy(pdf: bytes, key: str | None, hints: Sequence[str]) -> ExtractionResult:
        seen.append(list(hints))
        return await replay_extract(pdf, key, hints)

    service = make_service(db, tmp_path, extract=spy)
    api._transport.app.state.run_service = service  # type: ignore[attr-defined]
    first = await run_sample(service, "technova-keyboards")
    res = await api.post(
        f"/runs/{first.id}/corrections",
        json={"field": "invoice_number", "new_value": "TN/2026/1038-A", "reviewer": "Priya Nair"},
    )
    assert res.status_code == 201 and res.json()["old_value"] == "TN/2026/1038"
    assert (
        await api.post(f"/runs/{first.id}/corrections", json={"field": "x", "new_value": "y"})
    ).status_code == 422
    await run_sample(service, "technova-contract-rate")  # same vendor (TechNova GSTIN)
    assert seen[0] == [] and len(seen[1]) == 1 and "TN/2026/1038-A" in seen[1][0]


async def test_evidence_bundle_has_the_full_audit_trail(
    service: RunService, api: AsyncClient
) -> None:
    run = await run_sample(service, "northwind-over-approval-limit")
    run = await approve(service, run.id, "Meera Krishnan")
    async with service.sessionmaker() as session:
        bundle = await build_bundle(session, run.id)
    assert bundle is not None
    assert bundle["case"]["final_decision"] == "APPROVED"
    assert bundle["procedure"]["version"] == "v1" and bundle["procedure"]["policy"]
    assert bundle["approvals"][0]["decided_by"] == "Meera Krishnan"
    assert bundle["erp"]["verified"] is True
    assert bundle["rules"]["results"] and bundle["timeline"]
    assert bundle_pdf(bundle).startswith(b"%PDF")
    assert (await api.get(f"/cases/{run.id}/evidence")).json()["case"]["run_id"] == run.id
    pdf = await api.get(f"/cases/{run.id}/evidence.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    state = run.state or {}
    assert state.get("evidence_sha256")


async def test_approvals_api_and_four_eyes_over_http(service: RunService, api: AsyncClient) -> None:
    run = await run_sample(service, "rangoli-bank-change")
    res = await api.post(
        f"/runs/{run.id}/review", json={"action": "APPROVE", "reviewer": "Lakshmi Pillai"}
    )
    assert res.status_code == 202
    await service.wait(run.id)
    inbox = (await api.get("/approvals/inbox")).json()
    assert [i["approval"]["role"] for i in inbox] == ["AP Manager"]
    approval_id = inbox[0]["approval"]["id"]
    same = await api.post(
        f"/approvals/{approval_id}/decide",
        json={"decision": "APPROVE", "approver": "Lakshmi Pillai"},
    )
    assert same.status_code == 403 and "four-eyes" in same.json()["detail"]
    ok = await api.post(
        f"/approvals/{approval_id}/decide", json={"decision": "APPROVE", "approver": "Arjun Desai"}
    )
    assert ok.status_code == 200
    await service.wait(run.id)
    records = (await api.get(f"/runs/{run.id}/records")).json()
    assert [a["decision"] for a in records["approvals"]] == ["APPROVED", "PENDING"]
    assert records["exceptions"][0]["category"] == "BANK_CHANGE"


async def test_procedure_api(api: AsyncClient) -> None:
    active = (await api.get("/procedures/active")).json()
    assert active["label"] == "v1" and active["policy"]["price_tolerance_pct"] == "2"
    body = {"policy": {**active["policy"], "price_tolerance_pct": "3"}, "note": "Looser"}
    created = await api.post("/procedures", json=body)
    assert created.status_code == 201 and created.json()["label"] == "v2"
    assert created.json()["changes"] == [{"path": "price_tolerance_pct", "old": "2", "new": "3"}]
    history = (await api.get("/procedures")).json()
    assert [(p["label"], p["active"]) for p in history] == [("v2", True), ("v1", False)]
    assert (await api.post("/procedures/1/activate")).json()["active"] is True
    assert (await api.post("/procedures/9/activate")).status_code == 404


async def test_erp_ledger_and_reset(service: RunService, api: AsyncClient) -> None:
    await run_sample(service, "northwind-clean")
    ledger = (await api.get("/erp/documents")).json()
    assert len(ledger) == 1 and ledger[0]["verified"] is True
    doc = ledger[0]["document"]["doc_number"]
    fixed = await api.post(f"/erp/documents/{doc}/correct", json={"amount": "345150.00"})
    assert fixed.status_code == 200
    reset = (await api.post("/admin/reset-demo")).json()
    assert reset == {"runs_deleted": 1, "erp_documents_deleted": 1}
    assert (await api.get("/runs")).json() == []


def test_cases_paused_before_procedure_versions_use_the_defaults() -> None:
    """A case paused before procedure versions existed has no `policy`; resuming it must work."""
    from app.graph.nodes import _policy
    from app.rules.policy import Policy

    assert _policy({}) == Policy()
    assert _policy({"policy": Policy(price_tolerance_pct=1).model_dump(mode="json")}) != Policy()


async def test_eval_latest_endpoint(
    api: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.stats as stats_api

    monkeypatch.setattr(stats_api, "EVAL_LATEST", tmp_path / "latest.json")
    assert (await api.get("/eval/latest")).status_code == 404
    (tmp_path / "latest.json").write_text(
        '{"run_at": "2026-11-18T20:00:00", "live": true, "models": ["m"],'
        ' "summary": {"decision_accuracy": 1.0}, "rag": {"hit_rate_at_5": 0.9}}',
        encoding="utf-8",
    )
    body = (await api.get("/eval/latest")).json()
    assert body["summary"]["decision_accuracy"] == 1.0 and body["rag"]["hit_rate_at_5"] == 0.9
