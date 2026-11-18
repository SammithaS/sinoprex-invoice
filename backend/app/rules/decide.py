"""The rules engine: extraction + reference data → rule results → decision.

Deterministic and LLM-free ("the AI extracts and retrieves; the rules decide"). Checks run in
stages; a stage that rejects the document (or can't identify the vendor) stops the later ones,
because matching a quotation or a blocked vendor's invoice against POs only adds noise.

Precedence (handoff notes / ap-policy): any critical FAIL → REJECTED; a missing dependency
(no GRN, bank change, unknown vendor) → ON_HOLD; any WARN/FAIL, low key-field confidence or an
approval gate → NEEDS_REVIEW; otherwise APPROVED.
"""

from decimal import Decimal

from app.extraction.models import ExtractionResult, InvoiceData
from app.rules import approval, arithmetic, duplicates, matching, tax, tolerance, vendor
from app.rules.models import (
    ApprovalRequirement,
    Assessment,
    CaseContext,
    Decision,
    ExceptionCategory,
    ExceptionItem,
    GoodsReceipt,
    InvoicedLine,
    InvoiceRecord,
    POMatch,
    ProposedCoding,
    PurchaseOrder,
    ReferenceData,
    RuleResult,
    RuleStatus,
    Vendor,
)
from app.rules.money import inr
from app.rules.policy import DEFAULT_POLICY, Policy

_RANK = {"reject": 3, "hold": 2, "review": 1}
_DECISION = {3: Decision.REJECTED, 2: Decision.ON_HOLD, 1: Decision.NEEDS_REVIEW}


def _failing(r: RuleResult) -> bool:
    return r.status in (RuleStatus.WARN, RuleStatus.FAIL)


_TOPIC_BY_CODE = {"vendor.bank": "bank", "vendor.status": "termination"}
_TOPIC_BY_CATEGORY = {
    ExceptionCategory.TAX_MISMATCH: "tax",
    ExceptionCategory.NO_RECEIPT: "receipt",
}


def _cite_contract_topics(results: list[RuleResult], topics: dict[str, str]) -> None:
    """A failing bank / tax / receipt / vendor-status check also cites the vendor's own contract
    clause on that topic, when the contract has one."""
    for r in results:
        if not _failing(r):
            continue
        topic = _TOPIC_BY_CODE.get(r.code) or (
            _TOPIC_BY_CATEGORY.get(r.category) if r.category else None
        )
        cite = topics.get(topic) if topic else None
        if cite and cite not in r.citations:
            r.citations.append(cite)


def _worst(results: list[RuleResult]) -> int:
    return max((_RANK[r.severity] for r in results if _failing(r) and r.severity), default=0)


def evaluate(
    extraction: ExtractionResult,
    ref: ReferenceData,
    ctx: CaseContext,
    policy: Policy = DEFAULT_POLICY,
) -> Assessment:
    data = extraction.data
    results: list[RuleResult] = []
    found_vendor: Vendor | None = None
    po: PurchaseOrder | None = None
    po_match: POMatch | None = None
    grn: GoodsReceipt | None = None
    coding: ProposedCoding | None = None
    approvals: list[ApprovalRequirement] = []
    duplicate_of: str | None = None

    def finish() -> Assessment:
        _cite_contract_topics(results, ctx.contract_topics)
        return _assemble(
            extraction,
            results,
            found_vendor,
            po,
            po_match,
            grn,
            coding,
            approvals,
            duplicate_of,
            policy,
        )

    # Stage 1 — is this an invoice at all?
    results.append(arithmetic.check_document_type(data))
    if _worst(results) == _RANK["reject"]:
        return finish()

    # Stage 2 — validate the document and the supplier.
    results.append(arithmetic.check_required_fields(data))
    results.append(arithmetic.check_confidence(extraction, policy))
    results.extend(arithmetic.check_arithmetic(data, policy))
    results.append(arithmetic.check_dates(data, ctx.received_on))
    results.append(tax.check_gstins(data, ref.company_gstin))
    results.append(tax.check_tax_type(data, ref.company_state_code, policy.rounding))
    found_vendor, vendor_rule = vendor.identify_vendor(data, ref)
    results.append(vendor_rule)
    if found_vendor is not None:
        results.append(vendor.check_vendor_status(found_vendor))
        results.append(vendor.check_bank_details(data, found_vendor))
        original, dup_rule = duplicates.check_exact_duplicate(
            data, found_vendor.id, ctx.file_hash, ref
        )
        results.append(dup_rule)
        duplicate_of = original.ref if original else None
    if found_vendor is None or _worst(results) == _RANK["reject"]:
        return finish()

    # Stage 3 — duplicates, PO / GRN / contract matching and tolerances.
    suspects, suspect_rule = duplicates.check_suspected_duplicate(
        data, found_vendor.id, ref, policy
    )
    results.append(suspect_rule)
    if suspects:
        duplicate_of = suspects[0].ref
    # Consumption checks leave out the suspected original: whether it is the same billing is
    # exactly the question the duplicate exception asks.
    exclude = {s.ref for s in suspects}

    po_match, po_rule = matching.find_po(data, found_vendor, ref, policy)
    non_po = (
        approval.non_po_eligibility(found_vendor, ctx.contract, policy)
        if po_match is None and not data.po_number
        else None
    )
    if non_po is not None:
        results.append(non_po)
        results.append(tolerance.check_prices(data, None, None, ctx.contract, policy))
        coding = approval.propose_coding(found_vendor, ref)
        approvals += approval.non_po_approvals(
            data.total or Decimal(0), coding.cost_centre if coding else None, policy
        )
    else:
        results.append(po_rule)
    if po_match is not None:
        po = ref.po(po_match.po_number)
        assert po is not None
        grn, grn_rule = tolerance.check_receipt(po, ref)
        results.append(grn_rule)
        results.append(tolerance.check_quantities(data, po, po_match, ref, policy, exclude))
        results.append(tolerance.check_prices(data, po, po_match, ctx.contract, policy))
        results.append(tolerance.check_po_balance(data, po, po_match, ref, policy, exclude))
        approvals += approval.po_approvals(data.total or Decimal(0), policy)

    # Stage 4 — approval gates.
    if any(r.code == "vendor.bank" and _failing(r) for r in results):
        approvals = approval.bank_change_approvals(policy) + approvals
    approvals = list({a.role: a for a in approvals}.values())
    results.append(approval.approval_rule(approvals))
    return finish()


# --- assembly ----------------------------------------------------------------------------------


def _owner(
    category: ExceptionCategory,
    policy: Policy,
    po: PurchaseOrder | None,
    grn: GoodsReceipt | None,
) -> tuple[str, str | None]:
    role = policy.exception_owners.get(category, "AP Clerk")
    if role == "Buyer":
        return "Buyer", po.buyer if po else None
    if role == "Receiving store":
        return "Receiving store", grn.received_by if grn else None
    if role == "Receiver":
        head = policy.department_head(po.cost_centre if po else None)
        return f"Receiver ({head})", policy.approvers.get(head)
    return role, policy.approvers.get(role)


def _default_question(
    r: RuleResult, data: InvoiceData, found_vendor: Vendor | None, po_match: POMatch | None
) -> tuple[str, str]:
    name = found_vendor.name if found_vendor else data.vendor_name or "the vendor"
    match r.category:
        case ExceptionCategory.LOW_CONFIDENCE:
            fields = r.details.get("fields") or r.details.get("missing") or "the key fields"
            return (
                f"Do the highlighted values ({fields.replace(',', ', ')}) match the document "
                "image?",
                "Correct any value that doesn't match; request a clear copy from the vendor if "
                "the image is unreadable.",
            )
        case ExceptionCategory.TAX_MISMATCH:
            return (
                f"Should {name} be asked to cancel this invoice and reissue it with the correct "
                "GST details?",
                "Do not pay or adjust the tax; ask the vendor for a corrected invoice.",
            )
        case ExceptionCategory.NO_PO if po_match is not None:
            return (
                f"Is this invoice for {po_match.po_number}? Confirm the PO to continue.",
                f"Confirm {po_match.po_number} (match score {po_match.score:.2f}).",
            )
        case ExceptionCategory.NO_PO:
            return (
                "Which purchase order does this invoice belong to?",
                "Ask the requester for the PO number, or return the invoice to the vendor.",
            )
        case ExceptionCategory.BANK_CHANGE:
            contact = found_vendor.email if found_vendor and found_vendor.email else "on file"
            last4 = r.details.get("invoice_last4", "")
            return (
                f"Has {name} confirmed the new bank account ending {last4} through the contact "
                f"on file ({contact})? Do not use contact details from the invoice or its email.",
                "Keep on hold; release needs the AP Manager and the Finance Controller.",
            )
        case _:
            return (r.message, "Review and decide.")


def _assemble(
    extraction: ExtractionResult,
    results: list[RuleResult],
    found_vendor: Vendor | None,
    po: PurchaseOrder | None,
    po_match: POMatch | None,
    grn: GoodsReceipt | None,
    coding: ProposedCoding | None,
    approvals: list[ApprovalRequirement],
    duplicate_of: str | None,
    policy: Policy,
) -> Assessment:
    data = extraction.data
    rank = _worst(results)
    decision = _DECISION.get(rank, Decision.APPROVED)

    exceptions: list[ExceptionItem] = []
    if decision is not Decision.REJECTED:
        for r in results:
            if not _failing(r) or r.category is None:
                continue
            question, recommendation = _default_question(r, data, found_vendor, po_match)
            role, name = _owner(r.category, policy, po, grn)
            exceptions.append(
                ExceptionItem(
                    category=r.category,
                    rule_code=r.code,
                    summary=r.message,
                    variance={
                        k: v
                        for k, v in r.details.items()
                        if k not in ("question", "recommendation")
                    },
                    citations=r.citations,
                    owner_role=role,
                    owner_name=name,
                    recommendation=r.details.get("recommendation", recommendation),
                    question=r.details.get("question", question),
                )
            )
    if decision is Decision.REJECTED:
        approvals = []

    failing = sorted(
        (r for r in results if _failing(r)),
        key=lambda r: -_RANK.get(r.severity or "review", 0),
    )
    reasons = [r.message for r in failing]
    if coding is not None:
        reasons.append(f"Proposed GL {coding.gl_account} / {coding.cost_centre} — {coding.basis}")
    if not reasons:
        reasons = [
            "All checks passed: fields read with confidence, supplier verified, PO and receipt "
            f"matched within tolerance, {inr(data.total or Decimal(0))} needs no approval."
        ]
    cite_from = [*failing, *(r for r in results if r.code in ("tolerance.price", "po.non_po"))]
    citations = list(dict.fromkeys(c for r in cite_from for c in r.citations))

    by_code = {r.code: r for r in results}

    def ok(*codes: str) -> bool:
        return all(c in by_code and not _failing(by_code[c]) for c in codes)

    touchless = {
        "key_fields_confident": ok("fields.required", "fields.confidence"),
        "supplier_verified": ok("vendor.identify", "vendor.status", "vendor.bank", "gst.gstin"),
        "matching_passed": decision is not Decision.REJECTED
        and (ok("po.match") or "po.non_po" in by_code)
        and not any(_failing(r) for r in results if r.code.startswith(("tolerance.", "match."))),
        "no_duplicate_or_tax_exception": ok("duplicate.exact", "gst.tax_type")
        and not _failing(by_code.get("duplicate.suspect", by_code["doc.type"])),
        "no_approval_gate": ok("approval.matrix"),
    }
    scores = arithmetic.key_confidence(extraction)
    return Assessment(
        decision=decision,
        rule_results=results,
        exceptions=exceptions,
        approvals=approvals,
        vendor_id=found_vendor.id if found_vendor else None,
        po_match=po_match,
        matched_grn=grn.grn_number if grn else None,
        po_cost_centre=po.cost_centre if po else None,
        proposed_coding=coding,
        duplicate_of=duplicate_of,
        min_key_confidence=min(scores.values(), default=0.0),
        touchless=touchless,
        reasons=reasons,
        citations=citations,
    )


def history_record(
    assessment: Assessment, data: InvoiceData, ref: str, file_hash: str | None = None
) -> InvoiceRecord | None:
    """How a processed case enters the invoice history seen by later cases."""
    if assessment.vendor_id is None or not data.invoice_number or data.invoice_date is None:
        return None
    lines: list[InvoicedLine] = []
    if assessment.po_match is not None:
        qty = {ln.line_no: ln.qty for ln in data.lines}
        for m in assessment.po_match.lines:
            q = qty.get(m.invoice_line_no)
            if m.po_line_no is not None and q is not None:
                lines.append(InvoicedLine(po_line_no=m.po_line_no, qty=q))
    return InvoiceRecord(
        ref=ref,
        vendor_id=assessment.vendor_id,
        invoice_number=data.invoice_number,
        invoice_date=data.invoice_date,
        total=data.total or Decimal(0),
        po_number=assessment.matched_po,
        status=assessment.decision.value,
        file_hash=file_hash,
        service_period=data.service_period,
        lines=lines,
    )
