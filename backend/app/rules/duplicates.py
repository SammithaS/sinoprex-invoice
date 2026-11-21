"""Duplicate checks (policy §4). Pure functions over the registered invoice history."""

import re

from app.extraction.models import InvoiceData
from app.rules.models import (
    DEAD_STATUSES,
    ExceptionCategory,
    InvoiceRecord,
    ReferenceData,
    RuleResult,
    RuleStatus,
)
from app.rules.money import inr
from app.rules.policy import Policy


def normalise_invoice_number(number: str) -> str:
    """Compare invoice numbers on letters and digits only ("NWE/DL/26-0763" ≈ "NWE DL 260763")."""
    return re.sub(r"[^A-Z0-9]", "", number.upper())


def _norm_period(period: str | None) -> str | None:
    return re.sub(r"[^a-z0-9]", "", period.lower()) if period else None


def check_exact_duplicate(
    data: InvoiceData, vendor_id: str, file_hash: str | None, ref: ReferenceData
) -> tuple[InvoiceRecord | None, RuleResult]:
    live = [i for i in ref.invoices if i.status not in DEAD_STATUSES]
    if file_hash:
        same_file = next((i for i in live if i.file_hash == file_hash), None)
        if same_file:
            return same_file, _exact(same_file, "the identical file was already received")
    if data.invoice_number:
        key = normalise_invoice_number(data.invoice_number)
        same_no = next(
            (
                i
                for i in live
                if i.vendor_id == vendor_id and normalise_invoice_number(i.invoice_number) == key
            ),
            None,
        )
        if same_no:
            return same_no, _exact(
                same_no, f"invoice number {data.invoice_number} is already registered"
            )
    return None, RuleResult(
        code="duplicate.exact",
        name="Not an exact duplicate",
        status=RuleStatus.PASS,
        message="No invoice with the same vendor and number, or the same file, is registered.",
        citations=["ap-policy §4.1"],
    )


def _exact(original: InvoiceRecord, why: str) -> RuleResult:
    return RuleResult(
        code="duplicate.exact",
        name="Not an exact duplicate",
        status=RuleStatus.FAIL,
        severity="reject",
        message=f"Exact duplicate: {why} as {original.ref} ({original.invoice_number}, "
        f"{original.invoice_date}, {inr(original.total)}, status {original.status}). "
        "Tell the vendor the original case reference.",
        citations=["ap-policy §4.1"],
        details={"original": original.ref},
    )


def check_suspected_duplicate(
    data: InvoiceData, vendor_id: str, ref: ReferenceData, policy: Policy
) -> tuple[list[InvoiceRecord], RuleResult]:
    """Same vendor, same total, within the window, different number — unless the service
    periods show they bill different months (recurring subscriptions)."""
    if data.total is None or data.invoice_date is None:
        return [], RuleResult(
            code="duplicate.suspect",
            name="No likely duplicate",
            status=RuleStatus.SKIP,
            message="No total or invoice date to compare.",
        )
    period = _norm_period(data.service_period)
    hits = [
        i
        for i in ref.invoices
        if i.vendor_id == vendor_id
        and i.status not in DEAD_STATUSES
        and i.total == data.total
        and abs((i.invoice_date - data.invoice_date).days) <= policy.duplicate_window_days
        and not (period and i.service_period and _norm_period(i.service_period) != period)
    ]
    if not hits:
        return [], RuleResult(
            code="duplicate.suspect",
            name="No likely duplicate",
            status=RuleStatus.PASS,
            message=f"No other invoice from this vendor for {inr(data.total)} within "
            f"{policy.duplicate_window_days} days.",
            citations=["ap-policy §4.2"],
        )
    first = hits[0]
    days = abs((first.invoice_date - data.invoice_date).days)
    return hits, RuleResult(
        code="duplicate.suspect",
        name="No likely duplicate",
        status=RuleStatus.WARN,
        severity="review",
        category=ExceptionCategory.DUPLICATE_SUSPECT,
        message=f"Same vendor and same total {inr(data.total)} as {first.ref} "
        f"({first.invoice_number}, {first.invoice_date}) — {days} days apart, different "
        "invoice number.",
        citations=["ap-policy §4.2"],
        details={
            "suspected_original": first.ref,
            "days_apart": str(days),
            "question": f"Is {data.invoice_number} a separate supply, or a re-bill of "
            f"{first.invoice_number} ({first.ref})? Compare the two documents.",
            "recommendation": "Do not pay until the two invoices are compared; reject if it "
            "re-bills the same goods.",
        },
    )
