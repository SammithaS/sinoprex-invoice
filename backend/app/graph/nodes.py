"""Graph nodes, one per pipeline stage (plan §6). Each emits progress over the custom stream.

    intake → document_check → extract → vendor_match → retrieve_policy → rules → register_case
      → route_exception → human_review (pause; only when there are exceptions to answer)
      → approval_routing (one pause per required approver; four-eyes) → post_erp → verify_post
      (pause while the ERP read-back differs) → explain → close_case

A rejected case skips review, approvals and posting. Nodes return plain-JSON state updates; a
node that raises is caught by `step()`: the error is recorded, a `step_failed` event is emitted
and the graph routes to END (the run is FAILED, never stuck). Nodes that pause re-execute from
their start when resumed, so everything they write to the database is idempotent.
"""

import asyncio
import contextlib
import functools
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt

from app import procedure
from app.erp import ErpDocumentIn, compare
from app.evidence import build_bundle, bundle_bytes, bundle_sha256
from app.extraction import ExtractionResult
from app.extraction.document import inspect_pdf
from app.graph import case_repo, repo
from app.graph.deps import GraphDeps
from app.graph.state import CaseState
from app.rules.decide import evaluate, history_record
from app.rules.models import (
    ApprovalRequirement,
    Assessment,
    CaseContext,
    ContractTerms,
    ExceptionItem,
    RuleStatus,
    Vendor,
)
from app.rules.money import inr
from app.rules.policy import Policy
from app.rules.vendor import identify_vendor

NodeFn = Callable[[CaseState], Awaitable[dict[str, Any]]]
NEEDS_HUMAN = ("NEEDS_REVIEW", "ON_HOLD")
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")

STAGES: dict[str, str] = {
    "intake": "Receive & register case",
    "document_check": "Check document",
    "extract": "Extract fields",
    "vendor_match": "Identify supplier",
    "retrieve_policy": "Retrieve contract & policy",
    "rules": "Validate, match & decide",
    "register_case": "Register in invoice history",
    "route_exception": "Route exceptions",
    "human_review": "Human review",
    "approval_routing": "Approval routing",
    "post_erp": "Post to ERP",
    "verify_post": "Verify ERP posting",
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


def _policy(state: CaseState) -> Policy:
    # Cases paused before procedure versions existed (Phase 8) have no policy in their state;
    # they ran under the code defaults, which is what v1 is (ASSUMPTIONS #74).
    raw = state.get("policy")
    return Policy.model_validate(raw) if raw else Policy()


def needs_review(state: CaseState) -> bool:
    """A person answers the exceptions first; approval-only cases go straight to approvers."""
    a = state.get("assessment") or {}
    if a.get("exceptions"):
        return True
    return state.get("decision") in NEEDS_HUMAN and not a.get("approvals")


def build_nodes(deps: GraphDeps) -> dict[str, NodeFn]:
    assert deps.erp is not None

    async def reference() -> Any:
        async with deps.sessionmaker() as session:
            return await repo.load_reference(session)

    @step("intake")
    async def intake(state: CaseState) -> dict[str, Any]:
        path = Path(state["file_path"])
        size = (await asyncio.to_thread(path.stat)).st_size
        if deps.policy_override is not None:
            policy = deps.policy_override
        else:
            async with deps.sessionmaker() as session:
                policy = await procedure.active_policy(session)
        return {
            "received_on": deps.today().isoformat(),
            "procedure_version": policy.version,
            "policy": policy.model_dump(mode="json"),
            "_step": _done(
                f"Registered {state['file_name']} ({size / 1024:.0f} KB), SHA-256 "
                f"{state['file_hash'][:12]}…; operating procedure {policy.version}."
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
            "gstins": sorted(set(_GSTIN.findall(info.text.upper()))),
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
        gstins = (state.get("document") or {}).get("gstins") or []
        hints: list[str] = []
        if gstins:  # reviewer corrections for this vendor become few-shot hints (§2b.8)
            async with deps.sessionmaker() as session:
                hints = await case_repo.hints_for_text(session, " ".join(gstins))
        result = await deps.extract(pdf, state.get("sample_key"), hints)
        d = result.data
        low = ", ".join(result.low_confidence) or "none"
        message = (
            f"{d.doc_type} {d.invoice_number or '—'} from {d.vendor_name or '—'}, "
            f"total {inr(d.total) if d.total is not None else '—'}; "
            f"{len(d.lines)} line(s); min key confidence {result.min_key_confidence:.2f}; "
            f"low confidence: {low}; model {result.model or '—'}"
            + (f"; {len(hints)} correction hint(s) used" if hints else "")
            + "."
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
                    "hints": hints,
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
        assessment = evaluate(extraction, await reference(), ctx, _policy(state))
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
        status = {"APPROVED": "done", "REJECTED": "done"}.get(assessment.decision.value, "warn")
        return {
            "assessment": assessment.model_dump(mode="json"),
            "decision": assessment.decision.value,
            "_step": _done(
                f"{assessment.decision.value}: {cats} (procedure {state['procedure_version']}).",
                status,
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

    @step("route_exception")
    async def route_exception(state: CaseState) -> dict[str, Any]:
        assessment = Assessment.model_validate(state["assessment"])
        if not assessment.exceptions:
            return {"_step": _done("No exceptions to route.", "skipped")}
        items = [(e, attempted_steps(e, state)) for e in assessment.exceptions]
        async with deps.sessionmaker() as session:
            await case_repo.save_exceptions(session, state["run_id"], items)
        owners = "; ".join(
            f"{e.category.value} → {e.owner_role}" + (f" ({e.owner_name})" if e.owner_name else "")
            for e in assessment.exceptions
        )
        return {"_step": _done(f"Routed: {owners}.", "warn")}

    @step("human_review")
    async def human_review(state: CaseState) -> dict[str, Any]:
        assessment = Assessment.model_validate(state["assessment"])
        payload = review_request(state, assessment)
        emit(
            {
                "type": "paused",
                "node": "human_review",
                "message": payload["headline"],
                "data": {"kind": "review"},
            }
        )
        answer = interrupt(payload)  # the graph pauses here until POST /runs/{id}/review
        action = str(answer.get("action", "")).upper()
        review = {
            "action": action,
            "comment": answer.get("comment"),
            "reviewer": answer.get("reviewer"),
        }
        final = {"REJECT": "REJECTED"}.get(action)
        if action in ("APPROVE", "REJECT"):
            async with deps.sessionmaker() as session:
                await case_repo.resolve_exceptions(
                    session,
                    state["run_id"],
                    "OVERRIDDEN" if action == "APPROVE" else "REJECTED",
                    review["comment"],
                    review["reviewer"],
                )
        who = review["reviewer"] or "reviewer"
        message = f"{who}: {action}" + (f" — {review['comment']}" if review["comment"] else "")
        return {"reviews": [review], "final_decision": final, "_step": _done(message, "done")}

    @step("approval_routing")
    async def approval_routing(state: CaseState) -> dict[str, Any]:
        reqs = required_approvals(state)
        async with deps.sessionmaker() as session:
            rows = await case_repo.ensure_approvals(session, state["run_id"], reqs)
        decided = [a for a in rows if a.decision != "PENDING"]
        if any(a.decision == "REJECTED" for a in decided):
            return {
                "final_decision": "REJECTED",
                "approvals_pending": 0,
                "_step": _done("Rejected by an approver.", "done"),
            }
        pending = next((a for a in rows if a.decision == "PENDING"), None)
        if pending is None:
            note = (
                "No approval required by the procedure."
                if not rows
                else "Approved by " + ", ".join(f"{a.role} ({a.decided_by})" for a in rows) + "."
            )
            return {"final_decision": "APPROVED", "approvals_pending": 0, "_step": _done(note)}
        payload = approval_request(state, pending_row=case_repo.approval_dict(pending), rows=rows)
        emit(
            {
                "type": "paused",
                "node": "approval_routing",
                "message": payload["headline"],
                "data": {"kind": "approval", "approval_id": pending.id},
            }
        )
        answer = interrupt(payload)  # POST /approvals/{id}/decide resumes here
        decision = "APPROVED" if str(answer.get("decision")).upper() == "APPROVE" else "REJECTED"
        async with deps.sessionmaker() as session:
            await case_repo.decide_approval(
                session, pending.id, decision, answer.get("approver"), answer.get("comment")
            )
        left = sum(1 for a in rows if a.decision == "PENDING") - 1
        if decision == "REJECTED":
            final: str | None = "REJECTED"
        else:
            final = "APPROVED" if left == 0 else None  # the last approver closes the gate
        return {
            "final_decision": final,
            "approvals_pending": left if decision == "APPROVED" else 0,
            "_step": _done(
                f"L{pending.level} {pending.role}: {decision.lower()} by "
                f"{answer.get('approver') or '—'}"
                + (f" — {answer.get('comment')}" if answer.get("comment") else "")
                + ".",
            ),
        }

    @step("post_erp")
    async def post_erp(state: CaseState) -> dict[str, Any]:
        doc = erp_document(state)
        assert deps.erp is not None
        fault = deps.erp_fault(state.get("sample_key"))
        out = await deps.erp.post(doc, fault=fault)
        async with deps.sessionmaker() as session:
            await case_repo.save_posting(
                session, state["run_id"], doc.model_dump(mode="json"), out.model_dump(mode="json")
            )
        how = "posted" if out.created else "already in the ERP from an earlier attempt"
        return {
            "erp": {"doc_number": out.doc_number, "request": doc.model_dump(mode="json")},
            "_step": _done(
                f"{how.capitalize()} as {out.doc_number}: {inr(doc.amount)} to GL "
                f"{doc.gl_account} / {doc.cost_centre}.",
                data={"doc_number": out.doc_number, "fault": fault},
            ),
        }

    @step("verify_post")
    async def verify_post(state: CaseState) -> dict[str, Any]:
        assert deps.erp is not None
        erp: dict[str, Any] = state.get("erp") or {}
        expected = ErpDocumentIn.model_validate(erp["request"])
        actual = await deps.erp.read(erp["doc_number"])
        problems = compare(expected, actual, erp["doc_number"])
        async with deps.sessionmaker() as session:
            await case_repo.save_readback(
                session,
                state["run_id"],
                actual.model_dump(mode="json") if actual else None,
                problems,
            )
            if not problems:
                await case_repo.resolve_erp_mismatch(session, state["run_id"])
        if not problems:
            return {
                "erp_verified": True,
                "_step": _done(
                    f"Read back {erp['doc_number']}: number, amount, tax, status, attachment and "
                    "coding match."
                ),
            }
        summary = "; ".join(problems)
        question = (
            f"{erp['doc_number']} was posted with {summary}. Correct it in the ERP, then "
            "re-check the posting."
        )
        policy = _policy(state)
        async with deps.sessionmaker() as session:
            await case_repo.open_erp_mismatch(
                session, state["run_id"], summary, question, policy.approvers.get("AP Manager")
            )
        emit(
            {
                "type": "paused",
                "node": "verify_post",
                "message": question,
                "data": {"kind": "erp"},
            }
        )
        interrupt(
            {
                "kind": "erp",
                "headline": question,
                "decision": state.get("final_decision") or state.get("decision"),
                "reasons": [f"ERP read-back mismatch: {summary}."],
                "mismatches": problems,
                "doc_number": erp["doc_number"],
                "exceptions": [],
                "approvals": [],
                "citations": ["ap-policy §10.1"],
                "low_confidence_fields": [],
                "actions": ["RECHECK"],
            }
        )
        return {
            "erp_verified": False,
            "_step": _done(f"Read-back still differs: {summary}.", "warn"),
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
            bundle = await build_bundle(session, state["run_id"])
        sha = None
        if bundle is not None:
            bundle["case"]["final_decision"] = final
            sha = bundle_sha256(bundle)
            if deps.evidence_dir is not None:
                deps.evidence_dir.mkdir(parents=True, exist_ok=True)
                out = deps.evidence_dir / f"{state['run_id']}.json"
                await asyncio.to_thread(out.write_bytes, bundle_bytes(bundle))
        return {
            "final_decision": final,
            "evidence_sha256": sha,
            "_step": _done(
                f"Closed as {final}; evidence bundle archived"
                + (f" (SHA-256 {sha[:12]}…)" if sha else "")
                + "."
            ),
        }

    return {
        "intake": intake,
        "document_check": document_check,
        "extract": extract,
        "vendor_match": vendor_match,
        "retrieve_policy": retrieve_policy,
        "rules": rules,
        "register_case": register_case,
        "route_exception": route_exception,
        "human_review": human_review,
        "approval_routing": approval_routing,
        "post_erp": post_erp,
        "verify_post": verify_post,
        "explain": explain,
        "close_case": close_case,
    }


# --- helpers -------------------------------------------------------------------------------------


def attempted_steps(e: ExceptionItem, state: CaseState) -> list[str]:
    """What the agent already tried or checked before handing the exception over (§2b.4)."""
    v = e.variance
    po = ((state.get("assessment") or {}).get("po_match") or {}).get("po_number")
    contract = state.get("contract") or {}
    earlier = v.get("earlier_invoices") or ""
    n_earlier = len(earlier.split(",")) if earlier else 0
    before = inr(Decimal(v.get("invoiced_before", "0")))
    revisions = contract.get("rate_revision_citation")
    scanned = (state.get("document") or {}).get("is_scanned")
    steps = {
        "PRICE_VARIANCE": "Looked for a signed rate amendment in the contract: "
        + (f"[{revisions}] applies; none found." if revisions else "no revisions clause found."),
        "QTY_VARIANCE": f"Summed the quantities on every GRN and earlier invoice against {po}.",
        "OVER_PO_BALANCE": f"Summed {n_earlier} earlier invoice(s) on {po}: "
        f"{before} invoiced before.",
        "NO_RECEIPT": f"Re-checked for a goods receipt on {v.get('po_number', po)} at "
        "decision time: none posted.",
        "DUPLICATE_SUSPECT": f"Compared with {v.get('suspected_original', 'the earlier invoice')}"
        f": same vendor and total, {v.get('days_apart', '?')} days apart.",
        "BANK_CHANGE": "Compared the bank account with the vendor master; did not use any contact "
        "details printed on the invoice.",
        "TAX_MISMATCH": "Checked the supplier GSTIN state code against the place of supply and the "
        "tax heads charged.",
        "LOW_CONFIDENCE": "Compared every key field with the "
        + ("page image (scanned document)." if scanned else "text layer."),
        "NO_PO": "Scored the vendor's open purchase orders: best match "
        f"{v.get('inferred_po', '—')} (score {v.get('score', '—')}).",
    }
    out = [steps.get(e.category.value, "Checked the rule's inputs.")]
    if e.citations:
        out.append("Retrieved " + ", ".join(f"[{c}]" for c in e.citations) + ".")
    return out


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
    headline = questions[0] if questions else a.reasons[0]
    # An invoice from a supplier that isn't in the vendor master can't be approved here: AP must
    # not create vendors (policy §6.1); it waits for onboarding or is rejected.
    actions = ["APPROVE", "REJECT", "REQUEST_INFO"] if a.vendor_id else ["REJECT", "REQUEST_INFO"]
    return {
        "kind": "review",
        "headline": headline,
        "decision": a.decision.value,
        "reasons": a.reasons,
        "exceptions": [e.model_dump(mode="json") for e in a.exceptions],
        "approvals": [x.model_dump(mode="json") for x in a.approvals],
        "citations": a.citations,
        "low_confidence_fields": low,
        "actions": actions,
        "override_needs_second_approver": [
            e.category.value
            for e in a.exceptions
            if e.category in _policy(state).override_categories
        ],
    }


def required_approvals(state: CaseState) -> list[ApprovalRequirement]:
    """Matrix / non-PO / bank-change approvals from the rules, plus a four-eyes second approver
    when a reviewer approved despite an override-type exception (policy §7.3)."""
    a = Assessment.model_validate(state["assessment"])
    policy = _policy(state)
    reqs = list(a.approvals)
    reviews = state.get("reviews") or []
    overridden = [
        e.category.value for e in a.exceptions if e.category in policy.override_categories
    ]
    approved_by_reviewer = any(r.get("action") == "APPROVE" for r in reviews)
    if overridden and approved_by_reviewer:
        role = policy.override_second_approver
        reqs.append(
            ApprovalRequirement(
                role=role,
                name=policy.approvers.get(role),
                reason=f"Override of {', '.join(overridden)} needs a second approver.",
                citation="ap-policy §7.3",
            )
        )
    seen: set[str] = set()
    unique = []
    for r in reqs:
        if r.role not in seen:
            seen.add(r.role)
            unique.append(r)
    return unique


def approval_request(
    state: CaseState, pending_row: dict[str, Any], rows: list[Any]
) -> dict[str, Any]:
    a = Assessment.model_validate(state["assessment"])
    data = ExtractionResult.model_validate(state["extraction"]).data
    involved = [r.get("reviewer") for r in state.get("reviews") or [] if r.get("reviewer")]
    involved += [x.decided_by for x in rows if x.decided_by]
    return {
        "kind": "approval",
        "headline": f"{pending_row['role']} approval: {pending_row['reason']}",
        "approval": pending_row,
        "decision": a.decision.value,
        "reasons": a.reasons,
        "invoice": {
            "number": data.invoice_number,
            "vendor": data.vendor_name,
            "total": str(data.total) if data.total is not None else None,
            "po": a.po_match.po_number if a.po_match else None,
        },
        "proposed_coding": a.proposed_coding.model_dump(mode="json") if a.proposed_coding else None,
        "exceptions": [e.model_dump(mode="json") for e in a.exceptions],
        "approvals": [case_approval(x) for x in rows],
        "citations": a.citations,
        "low_confidence_fields": [],
        "actions": ["APPROVE", "REJECT"],
        # Four-eyes: none of these people may take this approval.
        "already_involved": sorted(set(involved)),
    }


def case_approval(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "level": row.level,
        "role": row.role,
        "kind": row.kind,
        "decision": row.decision,
        "decided_by": row.decided_by,
        "approver_name": row.approver_name,
    }


def erp_document(state: CaseState) -> ErpDocumentIn:
    a = Assessment.model_validate(state["assessment"])
    data = ExtractionResult.model_validate(state["extraction"]).data
    policy = _policy(state)
    vendor = Vendor.model_validate(state["vendor"]) if state.get("vendor") else None
    if vendor is None or data.invoice_number is None or data.invoice_date is None:
        raise ValueError("cannot post: supplier, invoice number or date missing")
    gl: str
    cc: str | None
    coding = a.proposed_coding
    if coding is not None:  # non-PO: the proposal a department head approved
        gl, cc = coding.gl_account, coding.cost_centre
    else:  # PO-backed: GL by vendor category, cost centre from the PO
        gl = policy.gl_by_category.get(vendor.category or "", policy.default_gl)
        cc = a.po_cost_centre
    return ErpDocumentIn(
        idempotency_key=state["run_id"],
        vendor_id=vendor.id,
        invoice_number=data.invoice_number,
        invoice_date=data.invoice_date,
        po_number=a.po_match.po_number if a.po_match else None,
        amount=data.total or Decimal(0),
        tax_amount=data.tax_total or Decimal(0),
        gl_account=gl,
        cost_centre=cc,
        attachment_url=f"/runs/{state['run_id']}/file",
        payload={
            "match": {
                "po": a.po_match.model_dump(mode="json") if a.po_match else None,
                "grn": a.matched_grn,
            },
            "approvals": state.get("reviews") or [],
            "procedure": state.get("procedure_version"),
        },
    )
