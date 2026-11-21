"""The case evidence bundle (plan §2b, §13.9): everything that explains a decision, in one place.

JSON for machines; a plain PDF for people (rendered with PyMuPDF, already a dependency).
Policy §10.3 keeps the evidence for 8 years; `close_case` archives a copy next to the upload.
"""

import hashlib
import json
from typing import Any

import pymupdf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Approval, CaseException, Correction, ErpPosting, Procedure, Run


async def build_bundle(session: AsyncSession, run_id: str) -> dict[str, Any] | None:
    run = await session.scalar(
        select(Run)
        .where(Run.id == run_id)
        .options(selectinload(Run.steps), selectinload(Run.reviews))
    )
    if run is None:
        return None
    state = run.state or {}
    label = run.procedure_version or ""
    version = int(label[1:]) if label.startswith("v") and label[1:].isdigit() else 0
    proc = await session.get(Procedure, version) if version else None
    exceptions = list(
        await session.scalars(
            select(CaseException).where(CaseException.run_id == run_id).order_by(CaseException.id)
        )
    )
    approvals = list(
        await session.scalars(
            select(Approval).where(Approval.run_id == run_id).order_by(Approval.level)
        )
    )
    posting = await session.scalar(select(ErpPosting).where(ErpPosting.run_id == run_id))
    corrections = list(
        await session.scalars(
            select(Correction).where(Correction.run_id == run_id).order_by(Correction.id)
        )
    )
    extraction = state.get("extraction") or {}
    assessment = state.get("assessment") or {}
    return {
        "case": {
            "run_id": run.id,
            "status": run.status,
            "file_name": run.file_name,
            "file_sha256": run.file_hash,
            "source": run.source,
            "received_at": run.created_at.isoformat(),
            "closed_at": run.finished_at.isoformat() if run.finished_at else None,
            "cycle_ms": run.duration_ms,
            "decision_by_rules": run.decision,
            "final_decision": run.final_decision,
            "vendor": {"id": run.vendor_id, "name": run.vendor_name},
            "invoice_number": run.invoice_number,
            "total": str(run.total) if run.total is not None else None,
        },
        "procedure": {
            "version": run.procedure_version,
            "policy": proc.policy if proc else None,
        },
        "extraction": {
            "model": extraction.get("model"),
            "is_scanned": extraction.get("is_scanned"),
            "fields": extraction.get("evidence"),
            "lines": (extraction.get("data") or {}).get("lines"),
        },
        "contract": {"terms": state.get("contract"), "clauses": state.get("clauses")},
        "rules": {
            "results": assessment.get("rule_results"),
            "touchless": assessment.get("touchless"),
            "po_match": assessment.get("po_match"),
            "matched_grn": assessment.get("matched_grn"),
            "proposed_coding": assessment.get("proposed_coding"),
            "citations": assessment.get("citations"),
        },
        "exceptions": [
            {
                "category": e.category,
                "summary": e.summary,
                "variance": e.variance,
                "citations": e.citations,
                "owner": f"{e.owner_role} ({e.owner_name})" if e.owner_name else e.owner_role,
                "question": e.question,
                "attempted_steps": e.attempted_steps,
                "recommendation": e.recommendation,
                "status": e.status,
                "resolution": e.resolution,
                "resolved_by": e.resolved_by,
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            }
            for e in exceptions
        ],
        "reviews": [
            {
                "action": r.action,
                "comment": r.comment,
                "reviewer": r.reviewer,
                "at": r.created_at.isoformat(),
            }
            for r in run.reviews
        ],
        "approvals": [
            {
                "level": a.level,
                "kind": a.kind,
                "role": a.role,
                "expected_approver": a.approver_name,
                "reason": a.reason,
                "citation": a.citation,
                "second_approver": a.is_second_approver,
                "decision": a.decision,
                "decided_by": a.decided_by,
                "comment": a.comment,
                "at": a.decided_at.isoformat() if a.decided_at else None,
            }
            for a in approvals
        ],
        "erp": {
            "doc_number": posting.doc_number,
            "request": posting.request,
            "readback": posting.readback,
            "verified": posting.verified,
            "mismatches": posting.mismatches,
        }
        if posting
        else None,
        "corrections": [
            {
                "field": c.field,
                "old": c.old_value,
                "new": c.new_value,
                "by": c.reviewer,
                "at": c.created_at.isoformat(),
            }
            for c in corrections
        ],
        "explanation": {"summary": run.explanation, "next_action": state.get("next_action")},
        "timeline": [
            {
                "seq": s.seq,
                "type": s.type,
                "node": s.node,
                "status": s.status,
                "message": s.message,
                "ms": s.duration_ms,
                "at": s.created_at.isoformat(),
            }
            for s in run.steps
            if s.type != "rule"
        ],
    }


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, indent=2, ensure_ascii=False, default=str).encode("utf-8")


def bundle_sha256(bundle: dict[str, Any]) -> str:
    return hashlib.sha256(bundle_bytes(bundle)).hexdigest()


def bundle_pdf(bundle: dict[str, Any]) -> bytes:
    """A readable audit-trail PDF: one heading per section, plain text below."""
    case = bundle["case"]
    sections: list[tuple[str, list[str]]] = [
        (
            "Case",
            [
                f"Invoice {case['invoice_number'] or '—'} from {case['vendor']['name'] or '—'}, "
                f"total INR {case['total'] or '—'}",
                f"File {case['file_name']} (SHA-256 {case['file_sha256']})",
                f"Received {case['received_at']}; closed {case['closed_at'] or 'open'}",
                f"Rules decided {case['decision_by_rules']}; final {case['final_decision']}",
                f"Operating procedure {bundle['procedure']['version']}",
            ],
        ),
        (
            "Rule checks",
            [
                f"{r['status']:4}  {r['name']}: {r['message']}"
                for r in (bundle["rules"]["results"] or [])
            ],
        ),
        (
            "Exceptions",
            [
                f"{e['category']} → {e['owner']}: {e['question']} [{e['status']}"
                + (f" by {e['resolved_by']}" if e["resolved_by"] else "")
                + "]"
                for e in bundle["exceptions"]
            ]
            or ["None"],
        ),
        (
            "Reviews",
            [
                f"{r['at']}  {r['reviewer']}: {r['action']} — {r['comment'] or ''}"
                for r in bundle["reviews"]
            ]
            or ["None"],
        ),
        (
            "Approvals",
            [
                f"L{a['level']} {a['role']} ({a['kind']}): {a['decision']}"
                + (f" by {a['decided_by']} at {a['at']}" if a["decided_by"] else "")
                for a in bundle["approvals"]
            ]
            or ["None required"],
        ),
        (
            "ERP posting",
            [
                f"Document {bundle['erp']['doc_number']}; read-back "
                + (
                    "verified"
                    if bundle["erp"]["verified"]
                    else "MISMATCH: " + "; ".join(bundle["erp"]["mismatches"] or [])
                )
            ]
            if bundle["erp"]
            else ["Not posted"],
        ),
        ("Explanation", [bundle["explanation"]["summary"] or "—"]),
        (
            "Timeline",
            [
                f"{t['at']}  {t['node'] or ''} {t['type']} {t['status'] or ''} {t['message'] or ''}"
                for t in bundle["timeline"]
            ],
        ),
    ]
    doc = pymupdf.open()
    page = doc.new_page()
    y = 50.0
    margin, width, bottom = 50.0, page.rect.width - 100, page.rect.height - 50

    def write(text: str, size: float, bold: bool = False) -> None:
        nonlocal page, y
        font = "hebo" if bold else "helv"
        for a, b in (("₹", "INR "), ("→", "->"), ("≠", "!="), ("≥", ">="), ("…", "...")):
            text = text.replace(a, b)
        lines = _wrap(text, size, width)
        for line in lines:
            if y + size * 1.4 > bottom:
                page = doc.new_page()
                y = 50.0
            page.insert_text((margin, y), line, fontsize=size, fontname=font)
            y += size * 1.4

    write("SinoprexInvoice — case evidence bundle", 16, bold=True)
    write(f"Case {case['run_id']}", 9)
    y += 6
    for title, lines in sections:
        y += 6
        write(title, 12, bold=True)
        for line in lines:
            write(line, 8.5)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _wrap(text: str, size: float, width: float) -> list[str]:
    max_chars = max(20, int(width / (size * 0.5)))
    out: list[str] = []
    for para in text.splitlines() or [""]:
        words, line = para.split(" "), ""
        for w in words:
            if len(line) + len(w) + 1 > max_chars and line:
                out.append(line)
                line = w
            else:
                line = f"{line} {w}".strip()
        out.append(line)
    return out
