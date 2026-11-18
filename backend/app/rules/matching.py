"""Purchase-order, line and contract-rate matching (policy §3.1, §3.6). Pure functions."""

import re
from collections.abc import Collection
from datetime import date
from decimal import Decimal

from rapidfuzz import fuzz

from app.extraction.models import InvoiceData, LineItem
from app.rules.models import (
    DEAD_STATUSES,
    ContractRate,
    ContractTerms,
    ExceptionCategory,
    InvoiceRecord,
    LineMatch,
    POLine,
    POMatch,
    PurchaseOrder,
    ReferenceData,
    RuleResult,
    RuleStatus,
    Vendor,
)
from app.rules.policy import Policy

LINE_MATCH_THRESHOLD = 0.6
ZERO = Decimal("0")


def _norm_text(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _hsn(code: str | None) -> str:
    return re.sub(r"\D", "", code or "")


def line_similarity(
    description: str | None, hsn: str | None, other_desc: str, other_hsn: str | None
) -> float:
    """0–1: fuzzy description similarity, nudged by whether HSN/SAC codes agree."""
    score = fuzz.token_set_ratio(_norm_text(description), _norm_text(other_desc)) / 100
    a, b = _hsn(hsn), _hsn(other_hsn)
    if a and b:
        score = min(1.0, score + 0.15) if a == b else score - 0.3
    return max(0.0, score)


def match_lines(lines: list[LineItem], po_lines: list[POLine]) -> list[LineMatch]:
    """Assign each invoice line to at most one PO line (greedy on best similarity)."""
    pairs = sorted(
        (
            (line_similarity(ln.description, ln.hsn_sac, pl.description, pl.hsn_sac), i, j)
            for i, ln in enumerate(lines)
            for j, pl in enumerate(po_lines)
        ),
        reverse=True,
    )
    taken_inv: dict[int, tuple[int, float]] = {}
    taken_po: set[int] = set()
    for score, i, j in pairs:
        if score < LINE_MATCH_THRESHOLD or i in taken_inv or j in taken_po:
            continue
        taken_inv[i] = (j, score)
        taken_po.add(j)
    out = []
    for i, ln in enumerate(lines):
        if i in taken_inv:
            j, score = taken_inv[i]
            out.append(
                LineMatch(
                    invoice_line_no=ln.line_no,
                    po_line_no=po_lines[j].line_no,
                    score=round(score, 3),
                )
            )
        else:
            out.append(LineMatch(invoice_line_no=ln.line_no, po_line_no=None, score=0.0))
    return out


def po_consumed(
    ref: ReferenceData, po_number: str, exclude: Collection[str] = ()
) -> list[InvoiceRecord]:
    """Live invoices already registered against a PO."""
    return [
        inv
        for inv in ref.invoices
        if inv.po_number == po_number and inv.status not in DEAD_STATUSES and inv.ref not in exclude
    ]


def score_po(data: InvoiceData, po: PurchaseOrder, ref: ReferenceData, policy: Policy) -> POMatch:
    """How well an invoice without a PO reference fits a candidate PO (0–1)."""
    matches = match_lines(data.lines, po.lines)
    if not data.lines:
        return POMatch(po_number=po.po_number, inferred=True, score=0.0, lines=matches)
    po_by_no = {pl.line_no: pl for pl in po.lines}
    desc = sum(m.score for m in matches) / len(matches)
    price_ok = qty_ok = 0
    for ln, m in zip(data.lines, matches, strict=True):
        pl = po_by_no.get(m.po_line_no) if m.po_line_no is not None else None
        if pl is None:
            continue
        if ln.unit_price is not None and pl.unit_price > 0:
            variance = (ln.unit_price - pl.unit_price) / pl.unit_price * 100
            price_ok += variance <= policy.price_tolerance_pct
        qty_ok += ln.qty is not None and ln.qty <= pl.qty
    n = len(data.lines)
    remaining = po.total - sum((i.total for i in po_consumed(ref, po.po_number)), ZERO)
    amount_ok = data.total is not None and data.total <= remaining + policy.rounding
    score = 0.5 * desc + 0.25 * price_ok / n + 0.15 * qty_ok / n + 0.1 * amount_ok
    return POMatch(po_number=po.po_number, inferred=True, score=round(score, 3), lines=matches)


def find_po(
    data: InvoiceData, vendor: Vendor, ref: ReferenceData, policy: Policy
) -> tuple[POMatch | None, RuleResult]:
    """Explicit PO reference if it is valid for this vendor; otherwise infer and score."""
    printed = (data.po_number or "").strip().upper()
    po = ref.po(printed) if printed else None
    if po is not None and po.vendor_id == vendor.id:
        match = POMatch(
            po_number=po.po_number,
            inferred=False,
            score=1.0,
            lines=match_lines(data.lines, po.lines),
        )
        return match, RuleResult(
            code="po.match",
            name="Purchase order identified",
            status=RuleStatus.PASS,
            message=f"Invoice quotes {po.po_number} (vendor {vendor.id}, status {po.status}).",
            citations=["ap-policy §3.1"],
            details={"po_number": po.po_number},
        )

    why = (
        f"PO {printed} is not in the PO master"
        if printed and po is None
        else f"PO {printed} belongs to another vendor"
        if printed
        else "No PO number printed"
    )
    candidates = [
        score_po(data, p, ref, policy)
        for p in ref.purchase_orders
        if p.vendor_id == vendor.id and p.status != "CLOSED"
    ]
    best = max(candidates, key=lambda m: m.score, default=None)
    if best and best.score >= policy.po_inference_threshold:
        lines = ", ".join(f"line {m.po_line_no}" for m in best.lines if m.po_line_no is not None)
        return best, RuleResult(
            code="po.match",
            name="Purchase order identified",
            status=RuleStatus.WARN,
            severity="review",
            category=ExceptionCategory.NO_PO,
            message=f"{why}; vendor, items, quantity and price match {best.po_number} "
            f"({lines}, match score {best.score:.2f}). A person must confirm the match.",
            citations=["ap-policy §3.1"],
            details={
                "inferred_po": best.po_number,
                "score": f"{best.score:.2f}",
                "candidates": str(len(candidates)),
            },
        )
    return None, RuleResult(
        code="po.match",
        name="Purchase order identified",
        status=RuleStatus.FAIL,
        severity="review",
        category=ExceptionCategory.NO_PO,
        message=f"{why}, and no open PO for {vendor.name} fits the invoice"
        + (f" (best {best.po_number} scored {best.score:.2f})." if best else "."),
        citations=["ap-policy §3.1"],
        details={"candidates": str(len(candidates))},
    )


def contract_rate_for(
    line: LineItem, terms: ContractTerms | None, on: date | None
) -> ContractRate | None:
    """The cited contract rate for this line valid on the invoice date, if any."""
    if terms is None:
        return None
    scored = [
        (line_similarity(line.description, line.hsn_sac, r.item, r.hsn_sac), r)
        for r in terms.rates
        if on is None
        or (
            (r.valid_from is None or r.valid_from <= on)
            and (r.valid_to is None or on <= r.valid_to)
        )
    ]
    scored = [s for s in scored if s[0] >= LINE_MATCH_THRESHOLD]
    if not scored:
        return None
    return max(scored, key=lambda s: s[0])[1]
