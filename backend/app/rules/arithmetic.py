"""Document, required-field, confidence, arithmetic and date checks (policy §1.1, §2.3, §5.3)."""

from datetime import date, timedelta
from decimal import Decimal

from app.extraction.models import KEY_FIELDS, REQUIRED_FIELDS, ExtractionResult, InvoiceData
from app.rules.models import ExceptionCategory, RuleResult, RuleStatus
from app.rules.money import inr, sentence
from app.rules.policy import Policy

ZERO = Decimal("0")


def check_document_type(data: InvoiceData) -> RuleResult:
    if data.doc_type == "invoice":
        return RuleResult(
            code="doc.type",
            name="Document is a tax invoice",
            status=RuleStatus.PASS,
            message="Document is a tax invoice.",
        )
    label = {"credit_note": "a credit note", "quote": "a quotation"}.get(
        data.doc_type, "not an invoice"
    )
    citations = ["ap-policy §1.1"]
    if data.doc_type == "credit_note":
        citations.append("gst-rules §6")
        message = (
            "Document is a credit note — credit notes are adjusted against the original "
            "invoice by AP, not paid."
        )
    else:
        message = f"Document is {label}, not a tax invoice — return it to the sender."
    return RuleResult(
        code="doc.type",
        name="Document is a tax invoice",
        status=RuleStatus.FAIL,
        severity="reject",
        message=message,
        citations=citations,
        details={"doc_type": data.doc_type},
    )


def check_required_fields(data: InvoiceData) -> RuleResult:
    missing = [f for f in REQUIRED_FIELDS if getattr(data, f) in (None, "")]
    if not data.lines:
        missing.append("lines")
    if not missing:
        return RuleResult(
            code="fields.required",
            name="Mandatory fields present",
            status=RuleStatus.PASS,
            message="All mandatory invoice fields are present.",
            citations=["gst-rules §3"],
        )
    return RuleResult(
        code="fields.required",
        name="Mandatory fields present",
        status=RuleStatus.FAIL,
        severity="review",
        category=ExceptionCategory.LOW_CONFIDENCE,
        message=f"Missing mandatory fields: {', '.join(missing)}.",
        citations=["gst-rules §3", "ap-policy §2.3"],
        details={"missing": ",".join(missing)},
    )


def key_confidence(extraction: ExtractionResult) -> dict[str, float]:
    return {
        f: ev.confidence
        for f in KEY_FIELDS
        if (ev := extraction.evidence.get(f)) is not None and ev.value is not None
    }


def check_confidence(extraction: ExtractionResult, policy: Policy) -> RuleResult:
    scores = key_confidence(extraction)
    low = sorted((f for f, c in scores.items() if c < policy.confidence_threshold))
    lowest = min(scores.values(), default=0.0)
    details = {"min_key_confidence": f"{lowest:.2f}", "threshold": f"{policy.confidence_threshold}"}
    if not low:
        return RuleResult(
            code="fields.confidence",
            name="Key fields read with confidence",
            status=RuleStatus.PASS,
            message=f"All key fields read with confidence ≥ {policy.confidence_threshold} "
            f"(lowest {lowest:.2f}).",
            citations=["ap-policy §9"],
            details=details,
        )
    shown = ", ".join(f"{f} {scores[f]:.2f}" for f in low)
    where = " on a scanned page" if extraction.is_scanned else ""
    return RuleResult(
        code="fields.confidence",
        name="Key fields read with confidence",
        status=RuleStatus.WARN,
        severity="review",
        category=ExceptionCategory.LOW_CONFIDENCE,
        message=f"Low-confidence key fields{where}: {shown} "
        f"(threshold {policy.confidence_threshold}).",
        citations=["ap-policy §2.3", "ap-policy §9"],
        details={**details, "fields": ",".join(low)},
    )


def check_arithmetic(data: InvoiceData, policy: Policy) -> list[RuleResult]:
    """Line amounts, subtotal, tax heads and grand total, each within the rounding allowance."""
    tol = policy.rounding
    problems: list[str] = []
    details: dict[str, str] = {}

    for line in data.lines:
        if line.qty is None or line.unit_price is None or line.amount is None:
            continue
        expected = line.qty * line.unit_price
        if abs(expected - line.amount) > tol:
            problems.append(
                f"line {line.line_no}: {line.qty} × {inr(line.unit_price)} = {inr(expected)}, "
                f"billed {inr(line.amount)}"
            )
            details[f"line{line.line_no}"] = str(line.amount - expected)

    line_sum = sum((ln.amount for ln in data.lines if ln.amount is not None), ZERO)
    if data.subtotal is not None and data.lines and abs(line_sum - data.subtotal) > tol:
        problems.append(f"lines add up to {inr(line_sum)}, subtotal says {inr(data.subtotal)}")
        details["subtotal"] = str(data.subtotal - line_sum)

    heads = [h for h in (data.cgst, data.sgst, data.igst) if h is not None]
    tax = data.tax_total if data.tax_total is not None else (sum(heads, ZERO) if heads else None)
    if data.tax_total is not None and heads and abs(sum(heads, ZERO) - data.tax_total) > tol:
        problems.append(
            f"tax heads add up to {inr(sum(heads, ZERO))}, tax total says {inr(data.tax_total)}"
        )
        details["tax_total"] = str(data.tax_total - sum(heads, ZERO))

    if data.subtotal is not None and tax is not None and data.total is not None:
        expected_total = data.subtotal + tax
        diff = data.total - expected_total
        if abs(diff) > tol:
            problems.append(
                f"subtotal {inr(data.subtotal)} + tax {inr(tax)} = {inr(expected_total)}, "
                f"invoice total says {inr(data.total)} (off by {inr(abs(diff))})"
            )
            details["total"] = str(diff)

    if not problems:
        return [
            RuleResult(
                code="math.totals",
                name="Arithmetic adds up",
                status=RuleStatus.PASS,
                message="Line amounts, subtotal, tax and total agree within ₹1.",
                citations=["ap-policy §5.3"],
            )
        ]
    return [
        RuleResult(
            code="math.totals",
            name="Arithmetic adds up",
            status=RuleStatus.FAIL,
            severity="reject",
            message="Arithmetic is wrong by more than ₹1 — "
            + "; ".join(problems)
            + ". Ask the vendor for a corrected invoice.",
            citations=["ap-policy §5.3", "gst-rules §5"],
            details=details,
        )
    ]


def check_dates(data: InvoiceData, received_on: date) -> RuleResult:
    issues: list[str] = []
    if data.invoice_date is not None:
        if data.invoice_date > received_on:
            issues.append(f"invoice date {data.invoice_date} is after receipt ({received_on})")
        elif data.invoice_date < received_on - timedelta(days=365):
            issues.append(f"invoice date {data.invoice_date} is more than a year old")
        if data.due_date is not None and data.due_date < data.invoice_date:
            issues.append(f"due date {data.due_date} is before the invoice date")
    if not issues:
        return RuleResult(
            code="dates.sanity",
            name="Dates are plausible",
            status=RuleStatus.PASS if data.invoice_date else RuleStatus.SKIP,
            message="Invoice and due dates are plausible."
            if data.invoice_date
            else "No invoice date to check.",
        )
    return RuleResult(
        code="dates.sanity",
        name="Dates are plausible",
        status=RuleStatus.WARN,
        severity="review",
        category=ExceptionCategory.LOW_CONFIDENCE,
        message=sentence("; ".join(issues)) + ".",
        citations=["gst-rules §3"],
    )
