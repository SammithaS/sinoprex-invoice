"""Phase 6: the LangGraph pipeline, human-in-the-loop pause/resume, events and the runs API.

The graph runs for real (in-memory checkpointer, SQLite database); extraction and contract
readings are replayed from the recorded LLM fixtures, so no LLM is called.
"""

import json
from collections.abc import AsyncIterator, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Base, Invoice, Review, Run, RunStep
from app.db.seed import seed_reference_data
from app.db.session import get_session
from app.erp import MockErp
from app.extraction import ExtractionResult, extract_invoice
from app.extraction.models import InvoiceData, LineItem
from app.graph.builder import build_graph
from app.graph.deps import GraphDeps
from app.graph.explain import Explanation, explain_case, strip_unknown_citations
from app.graph.repo import clear_reference_cache
from app.graph.runner import RunService
from app.llm.schemas import ExplanationRead
from app.main import create_app
from app.rag.contract_terms import ContractTermsResult
from app.rules.models import Assessment, Vendor
from scripts.eval_extraction import replay_factory
from scripts.eval_rag import CONTRACT_FIXTURES, replay_contract_terms

INVOICES = Path(__file__).resolve().parents[2] / "data" / "test-invoices"
TODAY = date(2026, 11, 18)


async def replay_extract(
    pdf: bytes, sample_key: str | None, hints: Sequence[str] = ()
) -> ExtractionResult:
    assert sample_key, "tests only run samples"
    return await extract_invoice(pdf, model_factory=replay_factory(sample_key))


async def replay_contract(
    vendor: Vendor, lines: list[LineItem], sample_key: str | None
) -> ContractTermsResult:
    if sample_key and (CONTRACT_FIXTURES / f"{sample_key}.json").exists():
        return replay_contract_terms(sample_key)
    return ContractTermsResult(terms=None, clauses=[], note="no recording")


def explain_model(summary: str) -> Any:
    async def answer(_: list[BaseMessage]) -> dict[str, Any]:
        read = ExplanationRead(summary=summary, next_action="Do the thing.")
        meta = {"model_name": "fake-explainer"}
        return {
            "raw": AIMessage(content="", response_metadata=meta),
            "parsed": read,
            "parsing_error": None,
        }

    return lambda: RunnableLambda(answer)


async def fake_explain(
    data: InvoiceData,
    assessment: Assessment,
    final: str | None,
    clauses: list[dict[str, Any]],
    policy_text: dict[str, str],
) -> Explanation:
    cites = " ".join(f"[{c}]" for c in assessment.citations)
    text = f"{assessment.decision.value} {cites} [made-up §9]"
    return await explain_case(data, assessment, final, clauses, policy_text, explain_model(text))


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await seed_reference_data(session)
    clear_reference_cache()
    yield maker
    await engine.dispose()


def make_service(
    db: async_sessionmaker[AsyncSession], tmp_path: Path, **overrides: Any
) -> RunService:
    deps = GraphDeps(
        sessionmaker=db,
        extract=overrides.get("extract", replay_extract),
        contract_terms=replay_contract,
        explain=fake_explain,
        today=lambda: TODAY,
        policy_text={},
    )
    return RunService(build_graph(deps, InMemorySaver()), db, tmp_path / "uploads")


@pytest.fixture
def service(db: async_sessionmaker[AsyncSession], tmp_path: Path) -> RunService:
    return make_service(db, tmp_path)


async def run_sample(service: RunService, key: str) -> Run:
    pdf = (INVOICES / f"{key}.pdf").read_bytes()
    run = await service.create_run(pdf, f"{key}.pdf", source="SAMPLE", sample_key=key)
    await service.wait(run.id)
    return await reload(service, run.id)


async def reload(service: RunService, run_id: str) -> Run:
    async with service.sessionmaker() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        return run


async def steps(service: RunService, run_id: str) -> list[RunStep]:
    async with service.sessionmaker() as session:
        return list(
            await session.scalars(
                select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq)
            )
        )


def finished_nodes(rows: list[RunStep]) -> list[str]:
    return [s.node or "" for s in rows if s.type == "step_finished"]


# --- straight-through ---------------------------------------------------------------------------


async def test_clean_invoice_runs_straight_through(service: RunService) -> None:
    run = await run_sample(service, "northwind-clean")
    assert run.status == "COMPLETED", run.error
    assert run.decision == run.final_decision == "APPROVED"
    assert run.vendor_id == "V008" and run.invoice_number == "NWE/DL/2026/0763"
    assert run.duration_ms is not None and run.procedure_version == "v1"
    rows = await steps(service, run.id)
    assert finished_nodes(rows) == [
        "intake",
        "document_check",
        "extract",
        "vendor_match",
        "retrieve_policy",
        "rules",
        "register_case",
        "route_exception",
        "approval_routing",
        "post_erp",
        "verify_post",
        "explain",
        "close_case",
    ]
    assert [s.seq for s in rows] == list(range(1, len(rows) + 1))
    rules = [s for s in rows if s.type == "rule"]
    assert rules and all(s.status in ("PASS", "SKIP") for s in rules)
    assert run.explanation and "[made-up §9]" not in run.explanation  # unknown citation stripped
    async with service.sessionmaker() as session:
        inv = await session.scalar(select(Invoice).where(Invoice.run_id == run.id))
    assert inv is not None and inv.status == "APPROVED" and inv.source == "RUN"
    assert inv.po_lines == [{"po_line_no": 1, "qty": "150"}, {"po_line_no": 2, "qty": "1"}]


async def test_rejected_document_skips_review(service: RunService) -> None:
    run = await run_sample(service, "northwind-quote")
    assert run.status == "COMPLETED" and run.final_decision == "REJECTED"
    assert "human_review" not in finished_nodes(await steps(service, run.id))


# --- human in the loop (Phase 6 "done when") -----------------------------------------------------


async def test_scanned_invoice_pauses_and_approval_resumes_to_completion(
    service: RunService,
) -> None:
    run = await run_sample(service, "deccan-scanned-poor")
    assert run.status == "WAITING_REVIEW" and run.decision == "NEEDS_REVIEW"
    request = run.review_request
    assert request is not None
    assert request["exceptions"][0]["category"] == "LOW_CONFIDENCE"
    assert request["low_confidence_fields"] and request["headline"].endswith("?")
    rows = await steps(service, run.id)
    assert rows[-1].type == "paused" and "explain" not in finished_nodes(rows)

    await service.review(
        run.id,
        {"action": "APPROVE", "comment": "Checked against the scan", "reviewer": "Priya Nair"},
    )
    await service.wait(run.id)
    run = await reload(service, run.id)
    assert run.status == "COMPLETED", run.error
    assert run.decision == "NEEDS_REVIEW" and run.final_decision == "APPROVED"
    after = finished_nodes(await steps(service, run.id))
    assert after[-6:] == [
        "human_review",
        "approval_routing",
        "post_erp",
        "verify_post",
        "explain",
        "close_case",
    ]
    async with service.sessionmaker() as session:
        reviews = list(await session.scalars(select(Review).where(Review.run_id == run.id)))
        inv = await session.scalar(select(Invoice).where(Invoice.run_id == run.id))
    assert [(r.action, r.reviewer) for r in reviews] == [("APPROVE", "Priya Nair")]
    assert inv is not None and inv.status == "APPROVED"


async def test_request_info_keeps_waiting_then_reject(service: RunService) -> None:
    run = await run_sample(service, "rangoli-bank-change")
    assert run.status == "WAITING_REVIEW" and run.decision == "ON_HOLD"
    await service.review(run.id, {"action": "REQUEST_INFO", "comment": "Called the vendor"})
    await service.wait(run.id)
    run = await reload(service, run.id)
    assert run.status == "WAITING_REVIEW" and run.final_decision is None
    await service.review(run.id, {"action": "REJECT", "comment": "Vendor denies the change"})
    await service.wait(run.id)
    run = await reload(service, run.id)
    assert run.status == "COMPLETED" and run.final_decision == "REJECTED"


async def test_review_only_when_waiting(service: RunService) -> None:
    run = await run_sample(service, "northwind-clean")
    with pytest.raises(ValueError):
        await service.review(run.id, {"action": "APPROVE"})


# --- case history across runs --------------------------------------------------------------------


async def test_later_cases_see_earlier_ones(service: RunService) -> None:
    first = await run_sample(service, "northwind-clean")
    dup = await run_sample(service, "northwind-resubmitted")
    assert dup.final_decision == "REJECTED"
    assert first.id in (dup.state or {})["assessment"]["duplicate_of"]
    for key in ("sharma-split-1", "sharma-split-2"):
        assert (await run_sample(service, key)).final_decision == "APPROVED"
    third = await run_sample(service, "sharma-split-3")
    cats = {e["category"] for e in (third.review_request or {})["exceptions"]}
    assert cats == {"OVER_PO_BALANCE", "QTY_VARIANCE"}


async def test_rerun_supersedes_the_earlier_attempt(service: RunService) -> None:
    first = await run_sample(service, "technova-keyboards")
    async with service.sessionmaker() as session:
        old = await session.get(Run, first.id)
        assert old is not None
    pdf = (INVOICES / "technova-keyboards.pdf").read_bytes()
    again = await service.create_run(
        pdf, old.file_name, sample_key=old.sample_key, rerun_of=first.id
    )
    await service.wait(again.id)
    again = await reload(service, again.id)
    assert again.final_decision == "APPROVED"  # not an exact duplicate of its own first attempt
    async with service.sessionmaker() as session:
        rows = {
            i.run_id: i.status
            for i in await session.scalars(
                select(Invoice).where(Invoice.run_id.in_([first.id, again.id]))
            )
        }
    assert rows == {first.id: "SUPERSEDED", again.id: "APPROVED"}


# --- failure and recovery ------------------------------------------------------------------------


async def test_node_error_fails_the_run_with_a_message(
    db: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    async def broken(pdf: bytes, key: str | None, hints: Sequence[str]) -> ExtractionResult:
        raise RuntimeError("model quota exhausted")

    service = make_service(db, tmp_path, extract=broken)
    run = await run_sample(service, "northwind-clean")
    assert run.status == "FAILED" and "model quota exhausted" in (run.error or "")
    failed = [s for s in await steps(service, run.id) if s.status == "failed"]
    assert [s.node for s in failed] == ["extract"]


async def test_recover_starts_runs_a_restart_cut_off(service: RunService) -> None:
    pdf = (INVOICES / "technova-keyboards.pdf").read_bytes()
    path = service.storage_dir / "x.pdf"
    service.storage_dir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    async with service.sessionmaker() as session:
        session.add(
            Run(
                id="r-1",
                status="QUEUED",
                file_name="x.pdf",
                file_path=str(path),
                file_hash="0" * 64,
                sample_key="technova-keyboards",
                source="SAMPLE",
            )
        )
        await session.commit()
    assert await service.recover() == 1
    await service.wait("r-1")
    assert (await reload(service, "r-1")).status == "COMPLETED"


# --- events -----------------------------------------------------------------------------------


async def test_events_replay_the_timeline_and_end_with_status(service: RunService) -> None:
    run = await run_sample(service, "northwind-clean")
    events = [e async for e in service.events(run.id)]
    assert events[0]["type"] == "step_started" and events[0]["node"] == "intake"
    assert events[-1] == {
        "type": "status",
        "status": "COMPLETED",
        "decision": "APPROVED",
        "final_decision": "APPROVED",
        "error": None,
    }
    later = [e async for e in service.events(run.id, after=len(events) - 3)]
    assert len(later) == 3


async def test_live_events_follow_a_running_run(service: RunService) -> None:
    pdf = (INVOICES / "technova-keyboards.pdf").read_bytes()
    run = await service.create_run(pdf, "k.pdf", sample_key="technova-keyboards")
    seen = [e async for e in service.events(run.id)]
    seqs = [e["seq"] for e in seen if "seq" in e]
    assert seqs == sorted(set(seqs))  # no duplicates, in order
    assert seen[-1]["type"] == "status" and seen[-1]["status"] == "COMPLETED"
    assert any(e.get("node") == "close_case" for e in seen)


def test_unknown_citations_are_stripped() -> None:
    text = "Over the rate [contracts/technova §2.1] and [invented §1.1]."
    assert strip_unknown_citations(text, {"contracts/technova §2.1"}) == (
        "Over the rate [contracts/technova §2.1] and."
    )
    both = "Low confidence [ap-policy §2.3, ap-policy §9; made-up §1]."
    assert strip_unknown_citations(both, {"ap-policy §2.3", "ap-policy §9"}) == (
        "Low confidence [ap-policy §2.3] [ap-policy §9]."
    )


# --- API ---------------------------------------------------------------------------------------


@pytest.fixture
async def api(
    db: async_sessionmaker[AsyncSession], service: RunService, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        async with db() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    app.state.run_service = service
    app.state.erp = MockErp(db)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": "test-key"}
    ) as c:
        yield c
    get_settings.cache_clear()


async def test_samples_api(api: AsyncClient) -> None:
    res = await api.get("/samples")
    assert res.status_code == 200 and len(res.json()) == 25
    assert (await api.post("/samples/nope/run")).status_code == 404


async def test_run_review_and_events_over_http(api: AsyncClient, service: RunService) -> None:
    res = await api.post("/samples/deccan-scanned-poor/run")
    assert res.status_code == 202
    run_id = res.json()["id"]
    await service.wait(run_id)

    detail = (await api.get(f"/runs/{run_id}")).json()
    assert detail["status"] == "WAITING_REVIEW"
    assert detail["review_request"]["decision"] == "NEEDS_REVIEW"
    assert detail["steps"][0]["node"] == "intake"
    assert (await api.get("/runs", params={"status": "WAITING_REVIEW"})).json()[0]["id"] == run_id
    pdf = await api.get(f"/runs/{run_id}/file")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    page = await api.get(f"/runs/{run_id}/pages/1")
    assert page.status_code == 200 and page.content[1:4] == b"PNG"
    assert (await api.get(f"/runs/{run_id}/pages/9")).status_code == 404

    res = await api.post(f"/runs/{run_id}/review", json={"action": "APPROVE", "reviewer": "QA"})
    assert res.status_code == 202
    await service.wait(run_id)
    assert (await api.post(f"/runs/{run_id}/review", json={"action": "APPROVE"})).status_code == 409

    stream = await api.get(f"/runs/{run_id}/events")
    assert stream.headers["content-type"].startswith("text/event-stream")
    body = stream.text
    assert "event: step_finished" in body and "event: paused" in body
    last = [line for line in body.splitlines() if line.startswith("data: ")][-1]
    assert json.loads(last[6:])["final_decision"] == "APPROVED"

    resumed = await api.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": "2"})
    first_id = next(ln for ln in resumed.text.splitlines() if ln.startswith("id: "))
    assert first_id == "id: 3"

    rerun = await api.post(f"/runs/{run_id}/rerun")
    assert rerun.status_code == 202 and rerun.json()["rerun_of"] == run_id
    await service.wait(rerun.json()["id"])


async def test_upload_validation(api: AsyncClient) -> None:
    res = await api.post("/runs", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert res.status_code == 415
    assert (await api.get("/runs/missing")).status_code == 404


async def test_stats(api: AsyncClient, service: RunService) -> None:
    for key in ("northwind-clean", "deccan-scanned-poor", "northwind-quote"):
        res = await api.post(f"/samples/{key}/run")
        await service.wait(res.json()["id"])
    s = (await api.get("/stats")).json()
    assert s["runs"] == 3 and s["completed"] == 2 and s["waiting_review"] == 1
    assert s["decisions"] == {"APPROVED": 1, "NEEDS_REVIEW": 1, "REJECTED": 1}
    assert s["straight_through"] == 1 and s["straight_through_rate"] == 0.5
    assert s["exception_causes"] == {"LOW_CONFIDENCE": 1}
    assert float(s["pending_value"]) == 218300.0
    counts = [f["count"] for f in s["funnel"]]
    assert counts == [3, 3, 3, 2, 1] and counts == sorted(counts, reverse=True)
    assert len(s["recent"]) == 3 and sum(d["total"] for d in s["volume"]) == 3
