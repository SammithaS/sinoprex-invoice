"""Ask-AP tools: read-only, parameterised lookups over what the pipeline already stores.

Every tool returns (content, artifact): `content` is compact JSON for the model, `artifact` is
the list of sources the answer may cite (plan §6: `[ap-policy §3.2]`, `[TN/2026/1042]`). No tool
writes anything and none takes free-form SQL; filters are bound parameters or Python-side
matches over small master data.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer

from app.db.models import Approval, CaseException, ErpPosting, Review, Run, Vendor
from app.rag.ingest import default_knowledge_dir, load_knowledge
from app.rag.retriever import HybridRetriever
from app.rag.store import ChunkFilter

Source = dict[str, Any]  # {citation, kind: "knowledge" | "case", title, excerpt, run_id?}
ToolOutput = tuple[str, list[Source]]

DECISIONS = ("APPROVED", "NEEDS_REVIEW", "ON_HOLD", "REJECTED")
STATUSES = ("QUEUED", "RUNNING", "WAITING_REVIEW", "WAITING_APPROVAL", "COMPLETED", "FAILED")
MAX_CASES = 20
EXCERPT = 700  # characters of a knowledge chunk shown to the model
# Listing and matching need a few columns, not each run's graph state (large JSON on Neon).
_LIGHT = (
    defer(Run.state, raiseload=True),
    defer(Run.review_request, raiseload=True),
    defer(Run.explanation, raiseload=True),
)


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=_default)


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def case_citation(run: Run) -> str:
    """What an answer writes to cite a case: its invoice number, else the run id."""
    return run.invoice_number or f"run {run.id[:8]}"


def _case_source(run: Run, excerpt: str) -> Source:
    return {
        "citation": case_citation(run),
        "kind": "case",
        "title": " · ".join(x for x in (run.vendor_name, run.invoice_number) if x) or run.file_name,
        "excerpt": excerpt,
        "run_id": run.id,
    }


@lru_cache(maxsize=1)
def clause_texts() -> dict[str, tuple[str, str]]:
    """citation → (heading, text) from the knowledge files (what the index is built from)."""
    out: dict[str, tuple[str, str]] = {}
    for c in load_knowledge(default_knowledge_dir()):
        out.setdefault(c.citation, (c.heading, c.text[:EXCERPT]))
    return out


def _norm(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


async def resolve_vendor(session: AsyncSession, name: str) -> list[Vendor]:
    """Vendors whose name, legal name, alias, id or GSTIN matches `name` (fuzzy, case-blind)."""
    q = name.strip()
    if not q:
        return []
    vendors = list(await session.scalars(select(Vendor)))
    nq = _norm(q)
    exact = [v for v in vendors if nq in {_norm(v.id), _norm(v.gstin)}]
    if exact:
        return exact
    scored: list[tuple[float, Vendor]] = []
    for v in vendors:
        names = [v.name, v.legal_name, *(v.aliases or [])]
        best = max(fuzz.partial_ratio(q.casefold(), n.casefold()) for n in names)
        if any(nq and nq in _norm(n) for n in names):
            best = max(best, 100.0)
        if best >= 85:
            scored.append((best, v))
    scored.sort(key=lambda t: -t[0])
    return [v for _, v in scored]


@dataclass
class AssistantTools:
    sessionmaker: async_sessionmaker[AsyncSession]
    retriever: HybridRetriever

    # --- knowledge -------------------------------------------------------------------------

    async def search_policies(self, query: str, vendor: str | None = None) -> ToolOutput:
        if not query.strip():
            return _dump({"error": "query is empty"}), []
        vendor_id: str | None = None
        if vendor:
            async with self.sessionmaker() as session:
                found = await resolve_vendor(session, vendor)
            if not found:
                return _dump({"error": f"no vendor matches {vendor!r}"}), []
            vendor_id = found[0].id
        hits = await self.retriever.search(query, k=5, flt=ChunkFilter())
        if vendor_id:
            # The vendor's own contract first, then general policy that still matched.
            own = await self.retriever.search(query, k=4, flt=ChunkFilter(vendor_id=vendor_id))
            seen = {h.citation for h in own}
            hits = [*own, *(h for h in hits if h.citation not in seen and not h.chunk.vendor_id)]
            hits = hits[:6]
        if not hits:
            return _dump({"results": [], "note": "nothing relevant in the knowledge base"}), []
        results = []
        sources: list[Source] = []
        for h in hits:
            text = h.chunk.text[:EXCERPT]
            results.append({"citation": h.citation, "heading": h.chunk.heading, "text": text})
            sources.append(
                {
                    "citation": h.citation,
                    "kind": "knowledge",
                    "title": h.chunk.heading,
                    "excerpt": text,
                }
            )
        return _dump({"results": results}), sources

    # --- cases -----------------------------------------------------------------------------

    async def find_cases(
        self,
        vendor: str | None = None,
        invoice_number: str | None = None,
        decision: str | None = None,
        status: str | None = None,
        exception_category: str | None = None,
        limit: int = 10,
    ) -> ToolOutput:
        limit = max(1, min(int(limit), MAX_CASES))
        stmt = (
            select(Run)
            .options(*_LIGHT)
            .where(Run.status != "SUPERSEDED")
            .order_by(Run.created_at.desc())
        )
        notes: list[str] = []
        async with self.sessionmaker() as session:
            if vendor:
                found = await resolve_vendor(session, vendor)
                if not found:
                    return _dump({"cases": [], "note": f"no vendor matches {vendor!r}"}), []
                stmt = stmt.where(Run.vendor_id.in_([v.id for v in found]))
            if decision:
                d = decision.strip().upper().replace(" ", "_")
                if d not in DECISIONS:
                    return _dump({"error": f"decision must be one of {list(DECISIONS)}"}), []
                # Final (after review) where known, else the rules' decision.
                stmt = stmt.where(
                    (Run.final_decision == d) | (Run.final_decision.is_(None) & (Run.decision == d))
                )
            if status:
                st = status.strip().upper().replace(" ", "_")
                if st not in STATUSES:
                    return _dump({"error": f"status must be one of {list(STATUSES)}"}), []
                stmt = stmt.where(Run.status == st)
            if exception_category:
                cat = exception_category.strip().upper().replace(" ", "_")
                ids = select(CaseException.run_id).where(CaseException.category == cat)
                stmt = stmt.where(Run.id.in_(ids))
            runs = list(await session.scalars(stmt.limit(200)))
            if invoice_number:
                want = _norm(invoice_number)
                runs = [r for r in runs if want and want in _norm(r.invoice_number or "")]
            total = len(runs)
            runs = runs[:limit]
            cats = await self._categories(session, [r.id for r in runs])
        if total > limit:
            notes.append(f"showing {limit} of {total} matching cases (newest first)")
        cases = [
            {
                "cite_as": case_citation(r),
                "run_id": r.id,
                "vendor": r.vendor_name,
                "invoice_number": r.invoice_number,
                "total_inr": r.total,
                "rules_decision": r.decision,
                "final_decision": r.final_decision,
                "status": r.status,
                "exceptions": cats.get(r.id, []),
                "received": r.created_at.date().isoformat() if r.created_at else None,
            }
            for r in runs
        ]
        sources = [
            _case_source(r, f"{r.status} · {r.final_decision or r.decision or '—'}") for r in runs
        ]
        body: dict[str, Any] = {"cases": cases}
        if not cases:
            body["note"] = "no processed cases match"
        if notes:
            body["note"] = "; ".join(notes)
        return _dump(body), sources

    @staticmethod
    async def _categories(session: AsyncSession, run_ids: Sequence[str]) -> dict[str, list[str]]:
        if not run_ids:
            return {}
        rows = await session.execute(
            select(CaseException.run_id, CaseException.category).where(
                CaseException.run_id.in_(run_ids)
            )
        )
        out: dict[str, list[str]] = {}
        for run_id, cat in rows:
            out.setdefault(run_id, []).append(cat)
        return out

    async def get_case(self, ref: str) -> ToolOutput:
        """One case by run id (full or first 8 characters) or invoice number; latest wins."""
        ref = ref.strip()
        if not ref:
            return _dump({"error": "ref is empty"}), []
        async with self.sessionmaker() as session:
            runs = list(
                await session.scalars(
                    select(Run)
                    .options(*_LIGHT)
                    .where(Run.status != "SUPERSEDED")
                    .order_by(Run.created_at.desc())
                )
            )
            nref = _norm(ref)
            matches = [r for r in runs if r.id == ref or (len(ref) >= 8 and r.id.startswith(ref))]
            if not matches:
                matches = [r for r in runs if r.invoice_number and _norm(r.invoice_number) == nref]
            if not matches:
                return _dump({"error": f"no case found for {ref!r}"}), []
            run = await session.get(Run, matches[0].id, populate_existing=True)
            assert run is not None
            exceptions = list(
                await session.scalars(
                    select(CaseException)
                    .where(CaseException.run_id == run.id)
                    .order_by(CaseException.id)
                )
            )
            approvals = list(
                await session.scalars(
                    select(Approval).where(Approval.run_id == run.id).order_by(Approval.level)
                )
            )
            reviews = list(
                await session.scalars(
                    select(Review).where(Review.run_id == run.id).order_by(Review.id)
                )
            )
            posting = await session.scalar(select(ErpPosting).where(ErpPosting.run_id == run.id))
        state = run.state or {}
        assessment = state.get("assessment") or {}
        checks = [
            {
                "rule": r.get("name"),
                "status": r.get("status"),
                "message": r.get("message"),
                "citations": r.get("citations") or [],
            }
            for r in assessment.get("rule_results", [])
            if r.get("status") in ("WARN", "FAIL")
        ]
        case: dict[str, Any] = {
            "cite_as": case_citation(run),
            "run_id": run.id,
            "vendor": run.vendor_name,
            "invoice_number": run.invoice_number,
            "total_inr": run.total,
            "status": run.status,
            "rules_decision": run.decision,
            "final_decision": run.final_decision,
            "procedure_version": run.procedure_version,
            "reasons": assessment.get("reasons", []),
            "policy_citations": assessment.get("citations", []),
            "failed_or_warned_checks": checks,
            "exceptions": [
                {
                    "category": e.category,
                    "summary": e.summary,
                    "variance": e.variance,
                    "citations": e.citations or [],
                    "owner": e.owner_role + (f" ({e.owner_name})" if e.owner_name else ""),
                    "question": e.question,
                    "status": e.status,
                    "resolution": e.resolution,
                }
                for e in exceptions
            ],
            "approvals": [
                {
                    "role": a.role,
                    "reason": a.reason,
                    "citation": a.citation,
                    "decision": a.decision,
                    "decided_by": a.decided_by,
                    "comment": a.comment,
                }
                for a in approvals
            ],
            "reviews": [
                {"action": r.action, "reviewer": r.reviewer, "comment": r.comment} for r in reviews
            ],
            "erp": (
                {
                    "document": posting.doc_number,
                    "read_back_verified": posting.verified,
                    "mismatches": posting.mismatches or [],
                }
                if posting
                else None
            ),
            "explanation": run.explanation,
            "error": run.error,
        }
        if len(matches) > 1:
            case["note"] = f"{len(matches)} cases match {ref!r}; this is the latest"
        excerpt = "; ".join(assessment.get("reasons", [])[:3]) or (run.explanation or "")[:300]
        sources = [_case_source(run, excerpt)]
        # The clauses the decision rests on may be cited too: the rules cited them for this case.
        why: dict[str, str] = {}
        for item in [*checks, *case["exceptions"]]:
            for c in item["citations"]:
                why.setdefault(c, str(item.get("message") or item.get("summary") or ""))
        for c in assessment.get("citations", []):
            why.setdefault(c, "")
        clauses = clause_texts()
        for c, rule_text in why.items():
            heading, text = clauses.get(c, (c, rule_text))
            sources.append({"citation": c, "kind": "knowledge", "title": heading, "excerpt": text})
        return _dump({"case": case}), sources

    async def case_stats(self) -> ToolOutput:
        from app.api.stats import stats

        async with self.sessionmaker() as session:
            s = await stats(session)
        body = {
            "runs": s.runs,
            "completed": s.completed,
            "waiting_review": s.waiting_review,
            "waiting_approval": s.waiting_approval,
            "failed": s.failed,
            "decisions": s.decisions,
            "straight_through": s.straight_through,
            "straight_through_rate": s.straight_through_rate,
            "median_cycle_seconds": s.median_cycle_ms // 1000 if s.median_cycle_ms else None,
            "pending_value_inr": s.pending_value,
            "exception_causes": s.exception_causes,
            "erp_posted": s.erp_posted,
            "erp_verified": s.erp_verified,
        }
        source: Source = {
            "citation": "dashboard",
            "kind": "stats",
            "title": "Dashboard statistics (last 30 days)",
            "excerpt": f"{s.runs} runs, {s.completed} completed, {s.waiting_review} waiting review",
        }
        return _dump(body), [source]

    # --- LangChain tools -------------------------------------------------------------------

    def as_tools(self) -> list[BaseTool]:
        return [
            StructuredTool.from_function(
                coroutine=self.search_policies,
                name="search_policies",
                description=(
                    "Search the AP policy, GST rules, vendor FAQ and vendor contracts. Returns "
                    "excerpts with their citation (e.g. 'ap-policy §3.2'). Pass `vendor` (a name) "
                    "to search that vendor's contract first."
                ),
                response_format="content_and_artifact",
            ),
            StructuredTool.from_function(
                coroutine=self.find_cases,
                name="find_cases",
                description=(
                    "List processed invoice cases, newest first. Filters (all optional): vendor "
                    "name, invoice_number (partial ok), decision (APPROVED, NEEDS_REVIEW, "
                    "ON_HOLD, REJECTED), status (WAITING_REVIEW, WAITING_APPROVAL, COMPLETED, "
                    "FAILED, RUNNING), exception_category (e.g. PRICE_VARIANCE, DUPLICATE), "
                    "limit (≤ 20)."
                ),
                response_format="content_and_artifact",
            ),
            StructuredTool.from_function(
                coroutine=self.get_case,
                name="get_case",
                description=(
                    "Full detail of one case by invoice number or run id: decision, reasons, "
                    "failed checks with citations, exceptions (owner, question), approvals, "
                    "reviews, ERP posting and the explanation."
                ),
                response_format="content_and_artifact",
            ),
            StructuredTool.from_function(
                coroutine=self.case_stats,
                name="case_stats",
                description=(
                    "Dashboard numbers for the last 30 days: case counts, decisions, "
                    "straight-through rate, cycle time, waiting value, exception causes, ERP."
                ),
                response_format="content_and_artifact",
            ),
        ]
