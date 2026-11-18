"""Receipt, quantity, price and PO-balance checks (policy §3.2–3.7). Pure functions."""

import re
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal

from app.extraction.models import InvoiceData, LineItem
from app.rules.matching import contract_rate_for, po_consumed
from app.rules.models import (
    ContractTerms,
    ExceptionCategory,
    GoodsReceipt,
    LineMatch,
    POMatch,
    PurchaseOrder,
    ReferenceData,
    RuleResult,
    RuleStatus,
)
from app.rules.money import inr, pct, q2
from app.rules.policy import Policy

ZERO = Decimal("0")
HUNDRED = Decimal("100")
_FREIGHT = re.compile(r"\b(freight|packing|packaging|handling|courier|delivery charge)", re.I)


def _short(text: str | None, limit: int = 40) -> str:
    text = (text or "item").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


def check_receipt(po: PurchaseOrder, ref: ReferenceData) -> tuple[GoodsReceipt | None, RuleResult]:
    if not po.requires_grn:
        return None, RuleResult(
            code="match.grn",
            name="Goods receipt exists",
            status=RuleStatus.SKIP,
            message=f"{po.po_number} is a service PO (no GRN required): two-way match against "
            "the PO; the service owner confirms by approving.",
            citations=["ap-policy §3.4"],
        )
    grns = ref.grns_for(po.po_number)
    if not grns:
        return None, RuleResult(
            code="match.grn",
            name="Goods receipt exists",
            status=RuleStatus.FAIL,
            severity="hold",
            category=ExceptionCategory.NO_RECEIPT,
            message=f"{po.po_number} requires a GRN and none has been posted. Hold until the "
            "receiver confirms delivery.",
            citations=["ap-policy §3.4"],
            details={
                "po_number": po.po_number,
                "question": f"Have the goods/services on {po.po_number} been received? If yes, "
                "post the GRN with the quantities received.",
                "recommendation": "Keep on hold; release automatically once a GRN is posted.",
            },
        )
    names = ", ".join(g.grn_number for g in grns)
    return grns[-1], RuleResult(
        code="match.grn",
        name="Goods receipt exists",
        status=RuleStatus.PASS,
        message=f"Goods receipt {names} posted for {po.po_number} (three-way match).",
        citations=["ap-policy §3.4"],
        details={"grn": grns[-1].grn_number},
    )


def check_quantities(
    data: InvoiceData,
    po: PurchaseOrder,
    match: POMatch,
    ref: ReferenceData,
    policy: Policy,
    exclude: Collection[str] = (),
) -> RuleResult:
    """Cumulative invoiced quantity per PO line ≤ received (GRN) or ordered (service PO)."""
    grns = ref.grns_for(po.po_number) if po.requires_grn else []
    received: dict[int, Decimal] = {}
    for g in grns:
        for gl in g.lines:
            received[gl.line_no] = received.get(gl.line_no, ZERO) + gl.qty_received
    earlier: dict[int, Decimal] = {}
    for inv in po_consumed(ref, po.po_number, exclude):
        for il in inv.lines or []:
            earlier[il.po_line_no] = earlier.get(il.po_line_no, ZERO) + il.qty

    po_lines = {pl.line_no: pl for pl in po.lines}
    by_no = {ln.line_no: ln for ln in data.lines}
    over: list[str] = []
    first_question = ""
    details: dict[str, str] = {}
    for m in match.lines:
        ln = by_no.get(m.invoice_line_no)
        pl = po_lines.get(m.po_line_no) if m.po_line_no is not None else None
        if ln is None or pl is None or ln.qty is None:
            continue
        limit = min(received.get(pl.line_no, ZERO), pl.qty) if grns else pl.qty
        basis = f"received ({', '.join(g.grn_number for g in grns)})" if grns else "ordered"
        prior = earlier.get(pl.line_no, ZERO)
        total = prior + ln.qty
        if total > limit + policy.qty_tolerance:
            history = f" ({_qty(prior)} earlier + {_qty(ln.qty)} now)" if prior else ""
            over.append(
                f"{_short(pl.description)}: {_qty(total)} billed in total{history} vs "
                f"{_qty(pl.qty)} ordered and {_qty(limit)} {basis}"
            )
            details[f"po_line{pl.line_no}_excess"] = _qty(total - limit)
            first_question = first_question or (
                f"{_qty(total)} × {_short(pl.description)} are billed against {po.po_number} but "
                f"{_qty(limit)} were {'received' if grns else 'ordered'} — did the store "
                f"receive the extra {_qty(total - limit)}?"
            )
    if not over:
        return RuleResult(
            code="tolerance.qty",
            name="Quantity within received",
            status=RuleStatus.PASS,
            message="Invoiced quantities (including earlier invoices on this PO) are within "
            + ("the quantities received." if grns else "the PO quantities."),
            citations=["ap-policy §3.3"],
        )
    return RuleResult(
        code="tolerance.qty",
        name="Quantity within received",
        status=RuleStatus.FAIL,
        severity="review",
        category=ExceptionCategory.QTY_VARIANCE,
        message="; ".join(over) + ". There is no quantity tolerance for goods.",
        citations=["ap-policy §3.3"],
        details={
            **details,
            "question": first_question,
            "recommendation": "Pay only up to the received quantity; ask the vendor for a "
            "credit note for the excess.",
        },
    )


@dataclass
class _LineVariance:
    line: LineItem
    reference: Decimal
    source: str  # citation or PO number
    variance_pct: Decimal
    over_amount: Decimal


def check_prices(
    data: InvoiceData,
    po: PurchaseOrder | None,
    match: POMatch | None,
    contract: ContractTerms | None,
    policy: Policy,
) -> RuleResult:
    """Unit price vs the cited contract rate (preferred) or the PO rate, per line and in total."""
    po_lines = {pl.line_no: pl for pl in po.lines} if po else {}
    matches: dict[int, LineMatch] = {m.invoice_line_no: m for m in (match.lines if match else [])}
    variances: list[_LineVariance] = []
    unpriced: list[str] = []
    extras: list[str] = []
    freight_total = ZERO
    used: list[str] = []
    for ln in data.lines:
        if ln.unit_price is None or ln.qty is None:
            continue
        m = matches.get(ln.line_no)
        pl = po_lines.get(m.po_line_no) if m and m.po_line_no is not None else None
        rate = contract_rate_for(ln, contract, data.invoice_date)
        if rate is not None:
            reference, source = rate.rate, rate.citation
        elif pl is not None:
            reference, source = pl.unit_price, po.po_number if po else "PO"
        else:
            if _FREIGHT.search(ln.description or ""):
                freight_total += ln.amount or ln.qty * ln.unit_price
            elif po is not None:
                extras.append(
                    f"{_short(ln.description)} ({inr(ln.amount or ZERO)}) is not on the PO"
                )
            else:
                unpriced.append(_short(ln.description))
            continue
        if m is not None:
            m.reference_rate, m.reference_source = reference, source
        used.append(source)
        var_pct = (ln.unit_price - reference) / reference * HUNDRED if reference else ZERO
        over = max(ZERO, (ln.unit_price - reference) * ln.qty)
        variances.append(_LineVariance(ln, reference, source, var_pct, over))

    if freight_total > policy.freight_cap:
        extras.append(
            f"freight/handling {inr(freight_total)} exceeds the {inr(policy.freight_cap)} cap"
        )

    over_total = q2(sum((v.over_amount for v in variances), ZERO))
    breaches = [v for v in variances if v.variance_pct > policy.price_tolerance_pct]
    cited = sorted({s for s in used if "§" in s})
    if not variances and not extras:
        return RuleResult(
            code="tolerance.price",
            name="Price within tolerance",
            status=RuleStatus.SKIP,
            message="No PO or cited contract rate to compare prices against"
            + (f" ({', '.join(unpriced)})." if unpriced else "."),
        )
    if not breaches and not extras and over_total <= policy.price_tolerance_invoice:
        above = [v for v in variances if v.variance_pct > 0]
        if above:
            worst = max(above, key=lambda v: v.variance_pct)
            message = (
                f"{_short(worst.line.description)} billed at {inr(worst.line.unit_price or ZERO)} "
                f"vs {inr(worst.reference)} ({worst.source}), +{pct(worst.variance_pct)} — within "
                f"the {policy.price_tolerance_pct}% / {inr(policy.price_tolerance_invoice)} "
                f"tolerance ({inr(over_total)} over in total)."
            )
        else:
            message = "All unit prices are at or below the " + (
                "contracted rates." if cited else "PO rates."
            )
        return RuleResult(
            code="tolerance.price",
            name="Price within tolerance",
            status=RuleStatus.PASS,
            message=message,
            citations=[*cited, "ap-policy §3.2"],
            details={"over_total": str(over_total)},
        )

    parts = [
        f"{_short(v.line.description)} billed at {inr(v.line.unit_price or ZERO)} vs "
        + ("contracted " if "§" in v.source else "PO rate ")
        + f"{inr(v.reference)} (+{pct(v.variance_pct)}, {inr(v.over_amount)} over)"
        for v in (breaches or [v for v in variances if v.over_amount > 0])
    ]
    parts.extend(extras)
    if not breaches and over_total > policy.price_tolerance_invoice:
        parts.append(
            f"total price variance {inr(over_total)} exceeds {inr(policy.price_tolerance_invoice)}"
        )
    citations = list(cited)
    if contract and contract.rate_revision_citation and cited:
        citations.append(contract.rate_revision_citation)
    citations += ["ap-policy §3.2"] + (["ap-policy §3.6"] if cited else [])
    if extras:
        citations.append("ap-policy §3.7")
    first = breaches[0] if breaches else None
    if first is not None:
        question = (
            f"Has {data.vendor_name or 'the vendor'}'s rate for {_short(first.line.description)} "
            f"changed from {inr(first.reference)} to {inr(first.line.unit_price or ZERO)}? "
            "If yes, attach the signed amendment."
        )
    else:
        question = (
            "Should the charges not covered by the PO or contract be accepted? "
            "If yes, amend the PO."
        )
    return RuleResult(
        code="tolerance.price",
        name="Price within tolerance",
        status=RuleStatus.FAIL,
        severity="review",
        category=ExceptionCategory.PRICE_VARIANCE,
        message="; ".join(parts) + ".",
        citations=citations,
        details={
            "over_total": str(over_total),
            **(
                {
                    "line": str(first.line.line_no),
                    "billed_rate": str(first.line.unit_price),
                    "reference_rate": str(first.reference),
                    "reference_source": first.source,
                    "variance_pct": str(q2(first.variance_pct)),
                }
                if first
                else {}
            ),
            "question": question,
            "recommendation": "Hold payment of the excess: pay at the agreed rate unless a signed "
            "amendment is provided.",
        },
    )


def check_po_balance(
    data: InvoiceData,
    po: PurchaseOrder,
    match: POMatch,
    ref: ReferenceData,
    policy: Policy,
    exclude: Collection[str] = (),
) -> RuleResult:
    """Cumulative billing against the PO may not exceed the PO total (policy §3.5).

    The invoice is valued at PO prices (unit price × qty, plus the PO tax rate) so a price
    variance inside tolerance doesn't double up as a balance breach; price is §3.2's job.
    """
    earlier = po_consumed(ref, po.po_number, exclude)
    invoiced = sum((i.total for i in earlier), ZERO)
    remaining = po.total - invoiced
    po_lines = {pl.line_no: pl for pl in po.lines}
    by_no = {ln.line_no: ln for ln in data.lines}
    at_po = ZERO
    for m in match.lines:
        ln = by_no.get(m.invoice_line_no)
        if ln is None:
            continue
        pl = po_lines.get(m.po_line_no) if m.po_line_no is not None else None
        if pl is not None and ln.qty is not None:
            at_po += ln.qty * pl.unit_price * (1 + pl.tax_rate / HUNDRED)
        elif ln.amount is not None:
            at_po += ln.amount * (1 + (ln.tax_rate or ZERO) / HUNDRED)
    at_po = q2(at_po)
    total = data.total or at_po
    details = {
        "po_total": str(po.total),
        "invoiced_before": str(invoiced),
        "remaining": str(remaining),
        "earlier_invoices": ",".join(i.ref for i in earlier),
    }
    if at_po <= remaining + policy.rounding:
        return RuleResult(
            code="tolerance.po_balance",
            name="Within PO remaining balance",
            status=RuleStatus.PASS,
            message=f"{po.po_number}: {inr(total)} against a remaining balance of "
            f"{inr(remaining)} (PO {inr(po.total)}, {inr(invoiced)} invoiced before).",
            citations=["ap-policy §3.5"],
            details=details,
        )
    excess = total - remaining
    return RuleResult(
        code="tolerance.po_balance",
        name="Within PO remaining balance",
        status=RuleStatus.FAIL,
        severity="review",
        category=ExceptionCategory.OVER_PO_BALANCE,
        message=f"Invoice {inr(total)} exceeds the {po.po_number} remaining balance "
        f"{inr(remaining)} by {inr(excess)} (PO {inr(po.total)}, {inr(invoiced)} invoiced in "
        f"{len(earlier)} earlier invoice{'s' if len(earlier) != 1 else ''}).",
        citations=["ap-policy §3.5"],
        details={
            **details,
            "excess": str(excess),
            "question": f"Will you amend {po.po_number} to cover the extra {inr(excess)}, or "
            "should the excess be rejected?",
            "recommendation": "Approve only up to the PO remaining balance unless the PO is "
            "amended.",
        },
    )
