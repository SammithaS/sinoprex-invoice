"""Graph nodes, one per pipeline stage (plan §6). Each emits progress over the custom stream.

    intake → document_check → extract → vendor_match → retrieve_policy → rules → register_case
      → human_review (interrupt; only when the rules didn't decide APPROVED/REJECTED)
      → explain → close_case

Nodes return plain-JSON state updates. A node that raises is caught by `step()`: the error is
recorded, a `step_failed` event is emitted and the graph routes to END (the run is FAILED, never
stuck). Phase 8 adds route_exception / approval_routing / post_erp / verify_post.
"""

import asyncio
import contextlib
import functools
import time
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt

from app.extraction import ExtractionResult
from app.extraction.document import inspect_pdf
from app.graph import repo
from app.graph.deps import GraphDeps
from app.graph.state import CaseState
from app.rules.decide import evaluate, history_record
from app.rules.models import Assessment, CaseContext, ContractTerms, RuleStatus, Vendor
from app.rules.money import inr
from app.rules.vendor import identify_vendor

NodeFn = Callable[[CaseState], Awaitable[dict[str, Any]]]
NEEDS_HUMAN = ("NEEDS_REVIEW", "ON_HOLD")

STAGES: dict[str, str] = {
    "intake": "Receive & register case",
    "document_check": "Check document",
    "extract": "Extract fields",
    "vendor_match": "Identify supplier",
    "retrieve_policy": "Retrieve contract & policy",
    "rules": "Validate, match & decide",
    "register_case": "Register in invoice history",
    "human_review": "Human review",
    "explain": "Explain decision",
    "close_case": "Close case",
}


def emit(event: dict[str, Any]) -> None:
    with contextlib.suppress(RuntimeError):  # outside a graph run (unit tests)
        get_stream_writer()(event)


def step(name: str) -> Callable[[NodeFn], NodeFn]:
    """Timing, start/finish events and error capture around a node."""

    def wrap(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def run(state: CaseState) -> dict[str, Any]:
            emit({"type": "step_started", "node": name, "label": STAGES[name]})
            started = time.perf_counter()
            try:
                update = await fn(state)
            except GraphBubbleUp:  # interrupt() — not an error
                raise
            except Exception as exc:
                ms = int((time.perf_counter() - started) * 1000)
                message = f"{type(exc).__name__}: {exc}"[:500]
                emit(
                    {
                        "type": "step_finished",
                        "node": name,
                        "status": "failed",
                        "message": message,
                        "duration_ms": ms,
                    }
                )
                return {"failed": True, "errors": [f"{name}: {message}"]}
            ms = int((time.perf_counter() - started) * 1000)
            meta = update.pop("_step", {})
            emit(
                {
                    "type": "step_finished",
                    "node": name,
                    "status": meta.get("status", "done"),
                    "message": meta.get("message", ""),
                    "duration_ms": ms,
                    "data": meta.get("data"),
                }
            )
            return update

        return run

    return wrap


def _done(message: str, status: str = "done", data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": status, "message": message, "data": data}


async def _read(state: CaseState) -> bytes:
    return await asyncio.to_thread(Path(state["file_path"]).read_bytes)


def build_nodes(deps: GraphDeps) -> dict[str, NodeFn]:
    async def reference() -> Any:
        async with deps.sessionmaker() as session:
            return await repo.load_reference(session)

    @step("intake")
    async def intake(state: CaseState) -> dict[str, Any]:
        path = Path(state["file_path"])
        size = (await asyncio.to_thread(path.stat)).st_size
        received = deps.today().isoformat()
        return {
            "received_on": received,
            "procedure_version": deps.policy.version,
            "_step": _done(
                f"Registered {state['file_name']} ({size / 1024:.0f} KB), SHA-256 "
                f"{state['file_hash'][:12]}…, procedure {deps.policy.version}."
            ),
        }

    @step("document_check")
    async def document_check(state: CaseState) -> dict[str, Any]:
        info = await asyncio.to_thread(inspect_pdf, await _read(state))
        doc = {
            "page_count": info.page_count,
            "is_scanned": info.is_scanned,
            "quality": info.quality,
            "doc_type_hint": info.doc_type_hint,
        }
        kind = (
            f"Scanned PDF (image quality {info.quality:.2f}) → vision extraction"
            if info.is_scanned and info.quality is not None
            else "Digital PDF with a text layer → text extraction"
        )
        hint = f"; title says {info.doc_type_hint}" if info.doc_type_hint else ""
        return {
            "document": doc,
            "_step": _done(f"{kind}, {info.page_count} page(s){hint}.", data=doc),
        }

    @step("extract")
    async def extract(state: CaseState) -> dict[str, Any]:
        pdf = await _read(state)
        result = await deps.extract(pdf, state.get("sample_key"))
        d = result.data
        low = ", ".join(result.low_confidence) or "none"
        message = (
            f"{d.doc_type} {d.invoice_number or '—'} from {d.vendor_name or '—'}, "
            f"total {inr(d.total) if d.total is not None else '—'}; "
            f"{len(d.lines)} line(s); min key confidence {result.min_key_confidence:.2f}; "
            f"low confidence: {low}; model {result.model or '—'}."
        )
        return {
            "extraction": result.model_dump(mode="json"),
            "_step": _done(
                message,
                "warn" if result.low_confidence else "done",
                {
                    "model": result.model,
                    "latency_ms": result.latency_ms,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
            ),
        }

    @step("vendor_match")
    async def vendor_match(state: CaseState) -> dict[str, Any]:
        data = ExtractionResult.model_validate(state["extraction"]).data
        vendor, rule = identify_vendor(data, await reference())
        status = "done" if rule.status is RuleStatus.PASS else "warn"
        return {
            "vendor": vendor.model_dump(mode="json") if vendor else None,
            "_step": _done(rule.message, status),
        }

    @step("retrieve_policy")
    async def retrieve_policy(state: CaseState) -> dict[str, Any]:
        data = ExtractionResult.model_validate(state["extraction"]).data
        if not state.get("vendor") or data.doc_type != "invoice":
            why = "no vendor in the master" if not state.get("vendor") else f"a {data.doc_type}"
            return {
                "contract": None,
                "clauses": [],
                "contract_note": None,
                "_step": _done(f"Skipped: {why}.", "skipped"),
            }
        vendor = Vendor.model_validate(state["vendor"])
        result = await deps.contract_terms(vendor, data.lines, state.get("sample_key"))
        clauses = [
            {"citation": c.citation, "heading": c.heading, "text": c.text} for c in result.clauses
        ]
        rates = [
            f"{r.item} ₹{r.rate} [{r.citation}]"
            for r in (result.terms.rates if result.terms else [])
        ]
        message = (
            f"Retrieved {len(clauses)} clause(s) from {vendor.name}'s contract; "
            f"{len(rates)} cited rate(s)"
            + (f"; dropped {len(result.dropped)} ungrounded reading(s)" if result.dropped else "")
            + "."
            if clauses
            else result.note or "No contract clauses found."
        )
        return {
            "contract": result.terms.model_dump(mode="json") if result.terms else None,
            "clauses": clauses,
            "contract_note": result.note,
            "_step": _done(
                message,
                "done" if clauses else "warn",
                {
                    "rates": rates,
                    "dropped": result.dropped,
                    "model": result.model,
                    "citations": [c["citation"] for c in clauses],
                },
            ),
        }

    @step("rules")
    async def rules(state: CaseState) -> dict[str, Any]:
        extraction = ExtractionResult.model_validate(state["extraction"])
        ctx = CaseContext(
            case_ref=state["run_id"],
            file_hash=state["file_hash"],
            received_on=date.fromisoformat(state["received_on"]),
            contract=ContractTerms.model_validate(state["contract"])
            if state.get("contract")
            else None,
        )
        assessment = evaluate(extraction, await reference(), ctx, deps.policy)
        for r in assessment.rule_results:
            emit(
                {
                    "type": "rule",
                    "node": "rules",
                    "code": r.code,
                    "name": r.name,
                    "status": r.status.value,
                    "message": r.message,
                    "citations": r.citations,
                }
            )
        cats = ", ".join(e.category.value for e in assessment.exceptions) or "no exceptions"
        status = {"APPROVED": "done", "REJECTED": "failed"}.get(assessment.decision.value, "warn")
        return {
            "assessment": assessment.model_dump(mode="json"),
            "decision": assessment.decision.value,
            "_step": _done(
                f"{assessment.decision.value}: {cats}.",
                "done" if status == "failed" else status,
                {"decision": assessment.decision.value},
            ),
        }

    @step("register_case")
    async def register_case(state: CaseState) -> dict[str, Any]:
        assessment = Assessment.model_validate(state["assessment"])
        data = ExtractionResult.model_validate(state["extraction"]).data
        record = history_record(assessment, data, state["run_id"], state["file_hash"])
        if record is None:
            return {
                "_step": _done("Not registered: supplier or invoice number unknown.", "skipped")
            }
        async with deps.sessionmaker() as session:
            await repo.register_invoice(session, state["run_id"], record, data, state["file_path"])
        return {
            "_step": _done(
                f"Registered {record.invoice_number} as {record.status} for "
                "duplicate and PO-balance checks on later cases."
            )
        }

    @step("human_review")
    async def human_review(state: CaseState) -> dict[str, Any]:
        assessment = Assessment.model_validate(state["assessment"])
        payload = review_request(state, assessment)
        emit(
            {
                "type": "paused",
                "node": "human_review",
                "message": payload["headline"],
                "data": payload,
            }
        )
        answer = interrupt(payload)  # the graph pauses here until POST /runs/{id}/review
        action = str(answer.get("action", "")).upper()
        review = {
            "action": action,
            "comment": answer.get("comment"),
            "reviewer": answer.get("reviewer"),
        }
        final = {"APPROVE": "APPROVED", "REJECT": "REJECTED"}.get(action)
        who = review["reviewer"] or "reviewer"
        message = f"{who}: {action}" + (f" — {review['comment']}" if review["comment"] else "")
        return {
            "reviews": [review],
            "final_decision": final,
            "_step": _done(message, "done"),
        }

    @step("explain")
    async def explain(state: CaseState) -> dict[str, Any]:
        assessment = Assessment.model_validate(state["assessment"])
        data = ExtractionResult.model_validate(state["extraction"]).data
        final = state.get("final_decision")
        result = await deps.explain(
            data, assessment, final, state.get("clauses") or [], deps.policy_text
        )
        how = "template (LLM unavailable)" if result.fallback else (result.model or "LLM")
        return {
            "explanation": result.summary,
            "next_action": result.next_action,
            "_step": _done(f"Summary written by {how}.", data={"model": result.model}),
        }

    @step("close_case")
    async def close_case(state: CaseState) -> dict[str, Any]:
        final = state.get("final_decision") or state["decision"]
        async with deps.sessionmaker() as session:
            await repo.set_invoice_status(session, state["run_id"], final)
        return {"final_decision": final, "_step": _done(f"Closed as {final}.")}

    return {
        "intake": intake,
        "document_check": document_check,
        "extract": extract,
        "vendor_match": vendor_match,
        "retrieve_policy": retrieve_policy,
        "rules": rules,
        "register_case": register_case,
        "human_review": human_review,
        "explain": explain,
        "close_case": close_case,
    }


def review_request(state: CaseState, a: Assessment) -> dict[str, Any]:
    """What the reviewer sees while the graph is paused (the interrupt payload)."""
    extraction = ExtractionResult.model_validate(state["extraction"])
    low = [
        {
            "field": f,
            "value": ev.value,
            "confidence": ev.confidence,
            "page": ev.page,
            "bbox": ev.bbox,
        }
        for f, ev in extraction.evidence.items()
        if f in extraction.low_confidence
    ]
    questions = [e.question for e in a.exceptions]
    headline = (
        questions[0]
        if questions
        else f"Approval required: {', '.join(x.role for x in a.approvals)}."
        if a.approvals
        else a.reasons[0]
    )
    return {
        "headline": headline,
        "decision": a.decision.value,
        "reasons": a.reasons,
        "exceptions": [e.model_dump(mode="json") for e in a.exceptions],
        "approvals": [x.model_dump(mode="json") for x in a.approvals],
        "citations": a.citations,
        "low_confidence_fields": low,
        "actions": ["APPROVE", "REJECT", "REQUEST_INFO"],
    }
