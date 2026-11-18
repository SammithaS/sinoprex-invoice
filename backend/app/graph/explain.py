"""The `explain` step: an LLM summary of a decision the rules already made.

The LLM sees only the rule results, exceptions and cited clauses; citations it writes that
weren't offered are stripped. If the call fails, a deterministic summary is used — the
explanation can never block or change a case.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from app.extraction.models import InvoiceData
from app.llm.models import structured_model
from app.llm.prompts import EXPLAIN_INSTRUCTION, EXPLAIN_SYSTEM
from app.llm.schemas import ExplanationRead
from app.rules.models import Assessment, RuleStatus
from app.rules.money import inr

ExplainModelFactory = Callable[[], Runnable[list[BaseMessage], dict[str, Any]]]
_BRACKETS = re.compile(r"\[([^\[\]]+)\]")

NEXT_ACTION = {
    "APPROVED": "Post to the ERP and schedule payment on the due date.",
    "REJECTED": "Return the document to the vendor with the reason.",
    "ON_HOLD": "Keep on hold until the missing dependency is resolved.",
    "NEEDS_REVIEW": "Answer the open question(s) and approve or reject.",
}


@dataclass
class Explanation:
    summary: str
    next_action: str
    model: str | None = None
    fallback: bool = False


def default_explain_model() -> Runnable[list[BaseMessage], dict[str, Any]]:
    return structured_model(ExplanationRead, "text")


def _invoice_line(data: InvoiceData) -> str:
    total = inr(data.total) if data.total is not None else "—"
    return (
        f"{data.doc_type} {data.invoice_number or '—'} from {data.vendor_name or '—'} dated "
        f"{data.invoice_date or '—'}, total {total}, PO {data.po_number or 'none printed'}"
    )


def template_explanation(
    data: InvoiceData, assessment: Assessment, final_decision: str | None
) -> Explanation:
    decision = final_decision or assessment.decision.value
    cites = " ".join(f"[{c}]" for c in assessment.citations[:3])
    reasons = " ".join(assessment.reasons[:3])
    text = f"{_invoice_line(data)}. Decision: {decision}. {reasons} {cites}".strip()
    return Explanation(summary=text, next_action=NEXT_ACTION.get(decision, ""), fallback=True)


def strip_unknown_citations(text: str, allowed: set[str]) -> str:
    """Keep only offered citations. Handles "[a §1, b §2]" and "[a §1; b §2]" as well."""

    def keep(m: re.Match[str]) -> str:
        parts = [p.strip() for p in re.split(r"[;,]", m.group(1))]
        known = [p for p in parts if p in allowed]
        return " ".join(f"[{p}]" for p in known)

    cleaned = _BRACKETS.sub(keep, text)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)  # no space left before punctuation
    return re.sub(r"\s{2,}", " ", cleaned).strip()


async def explain_case(
    data: InvoiceData,
    assessment: Assessment,
    final_decision: str | None,
    clauses: list[dict[str, Any]],
    policy_text: dict[str, str],
    model_factory: ExplainModelFactory = default_explain_model,
) -> Explanation:
    failing = [r for r in assessment.rule_results if r.status in (RuleStatus.WARN, RuleStatus.FAIL)]
    sources: dict[str, str] = {c["citation"]: c["text"] for c in clauses}
    sources.update(policy_text)
    allowed = set(assessment.citations) | set(sources)
    final = f" (after review: {final_decision})" if final_decision else ""
    prompt = EXPLAIN_INSTRUCTION.format(
        invoice=_invoice_line(data),
        decision=assessment.decision.value,
        final=final,
        checks="\n".join(f"- {r.status.value} {r.name}: {r.message} {r.citations}" for r in failing)
        or "- none (all checks passed)",
        exceptions="\n".join(
            f"- {e.category.value} → {e.owner_role}: {e.question}" for e in assessment.exceptions
        )
        or "- none",
        approvals=", ".join(a.role for a in assessment.approvals) or "none",
        sources="\n".join(f"[{c}] {sources.get(c, '')[:300]}" for c in sorted(allowed)) or "none",
    )
    try:
        out = await model_factory().ainvoke([SystemMessage(EXPLAIN_SYSTEM), HumanMessage(prompt)])
        parsed = out.get("parsed")
        if not isinstance(parsed, ExplanationRead):
            raise ValueError(out.get("parsing_error"))
        meta = getattr(out.get("raw"), "response_metadata", {}) or {}
        return Explanation(
            summary=strip_unknown_citations(parsed.summary, allowed),
            next_action=parsed.next_action,
            model=meta.get("model_name") or meta.get("model"),
        )
    except Exception:
        return template_explanation(data, assessment, final_decision)
