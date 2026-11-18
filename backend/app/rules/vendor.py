"""Supplier identification, status and bank-detail checks (policy §5.1, §6)."""

import re

from rapidfuzz import fuzz

from app.extraction.models import InvoiceData
from app.rules.models import ExceptionCategory, ReferenceData, RuleResult, RuleStatus, Vendor
from app.rules.money import sentence

NAME_MATCH_THRESHOLD = 90.0
_SUFFIXES = re.compile(r"\b(pvt|private|ltd|limited|llp|co|company|inc)\b\.?", re.IGNORECASE)


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", _SUFFIXES.sub(" ", name.replace(".", " "))).strip().lower()


def name_score(name: str, vendor: Vendor) -> float:
    target = _clean_name(name)
    return max(
        fuzz.token_sort_ratio(target, _clean_name(candidate))
        for candidate in (vendor.name, vendor.legal_name, *vendor.aliases)
    )


def identify_vendor(data: InvoiceData, ref: ReferenceData) -> tuple[Vendor | None, RuleResult]:
    """GSTIN exact match first, then fuzzy legal name / aliases."""
    gstin = (data.vendor_gstin or "").strip().upper()
    by_gstin = next((v for v in ref.vendors if v.gstin == gstin), None) if gstin else None
    if by_gstin:
        return by_gstin, RuleResult(
            code="vendor.identify",
            name="Supplier in vendor master",
            status=RuleStatus.PASS,
            message=f"Supplier matched to {by_gstin.legal_name} ({by_gstin.id}) by GSTIN.",
            citations=["ap-policy §6.1"],
            details={"vendor_id": by_gstin.id, "method": "gstin"},
        )

    best: tuple[float, Vendor | None] = (0.0, None)
    if data.vendor_name:
        for v in ref.vendors:
            score = name_score(data.vendor_name, v)
            if score > best[0]:
                best = (score, v)
    score, by_name = best
    if by_name and score >= NAME_MATCH_THRESHOLD:
        return by_name, RuleResult(
            code="vendor.identify",
            name="Supplier in vendor master",
            status=RuleStatus.FAIL,
            severity="review",
            category=ExceptionCategory.TAX_MISMATCH,
            message=f"Supplier name matches {by_name.legal_name} ({by_name.id}, score "
            f"{score:.0f}) but the invoice GSTIN {gstin or '—'} differs from the vendor "
            f"master ({by_name.gstin}).",
            citations=["ap-policy §5.1", "gst-rules §1.2"],
            details={"vendor_id": by_name.id, "method": "name", "score": f"{score:.0f}"},
        )
    return None, RuleResult(
        code="vendor.identify",
        name="Supplier in vendor master",
        status=RuleStatus.FAIL,
        severity="hold",
        message=f"Supplier {data.vendor_name or '—'} (GSTIN {gstin or '—'}) is not in the "
        "vendor master. Hold until the Vendor Master team onboards it — AP must not create "
        "vendors.",
        citations=["ap-policy §6.1", "ap-policy §1.3"],
        details={"best_name_score": f"{score:.0f}"},
    )


def check_vendor_status(vendor: Vendor) -> RuleResult:
    if vendor.status == "ACTIVE":
        return RuleResult(
            code="vendor.status",
            name="Vendor is active",
            status=RuleStatus.PASS,
            message=f"{vendor.name} is ACTIVE.",
            citations=["ap-policy §6.1"],
        )
    return RuleResult(
        code="vendor.status",
        name="Vendor is active",
        status=RuleStatus.FAIL,
        severity="reject",
        message=f"{vendor.name} is {vendor.status} in the vendor master — invoices from "
        "blocked vendors are rejected.",
        citations=["ap-policy §6.1"],
    )


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def check_bank_details(data: InvoiceData, vendor: Vendor) -> RuleResult:
    account = _digits(data.bank_account_number)
    if not account:
        return RuleResult(
            code="vendor.bank",
            name="Bank details unchanged",
            status=RuleStatus.SKIP,
            message="No bank account printed; payment goes to the account on file.",
        )
    changes: list[str] = []
    if vendor.bank_account_last4 and account[-4:] != vendor.bank_account_last4:
        changes.append(
            f"invoice bank account ends {account[-4:]}; vendor master has "
            f"••••{vendor.bank_account_last4}"
        )
    ifsc = (data.bank_ifsc or "").strip().upper()
    if ifsc and vendor.bank_ifsc and ifsc != vendor.bank_ifsc.upper():
        changes.append(f"IFSC {ifsc} differs from {vendor.bank_ifsc}")
    if not changes:
        return RuleResult(
            code="vendor.bank",
            name="Bank details unchanged",
            status=RuleStatus.PASS,
            message="Bank account matches the vendor master.",
            citations=["ap-policy §6.2"],
        )
    return RuleResult(
        code="vendor.bank",
        name="Bank details unchanged",
        status=RuleStatus.FAIL,
        severity="hold",
        category=ExceptionCategory.BANK_CHANGE,
        message=sentence("; ".join(changes))
        + ". Verify with the vendor using the contact on file before payment.",
        citations=["ap-policy §6.2", "ap-policy §6.3"],
        details={
            "invoice_last4": account[-4:],
            "master_last4": vendor.bank_account_last4 or "",
        },
    )
