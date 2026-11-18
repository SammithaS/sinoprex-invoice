"""Invoice tax checks (policy §5.1–5.2, gst-rules §1.2, §2)."""

from decimal import Decimal

from app.extraction.models import InvoiceData
from app.rules.gst import gstin_state_code, is_valid_gstin
from app.rules.models import ExceptionCategory, RuleResult, RuleStatus
from app.rules.money import inr, sentence

_ZERO = Decimal("0")


def check_gstins(data: InvoiceData, company_gstin: str) -> RuleResult:
    problems: list[str] = []
    if data.vendor_gstin and not is_valid_gstin(data.vendor_gstin):
        problems.append(f"supplier GSTIN {data.vendor_gstin} fails the format/check-digit test")
    if data.buyer_gstin and data.buyer_gstin.strip().upper() != company_gstin:
        problems.append(f"buyer GSTIN {data.buyer_gstin} is not Acme's ({company_gstin})")
    if not problems:
        return RuleResult(
            code="gst.gstin",
            name="GSTINs valid",
            status=RuleStatus.PASS if data.vendor_gstin else RuleStatus.SKIP,
            message="Supplier GSTIN is valid and the invoice is addressed to Acme's GSTIN."
            if data.vendor_gstin
            else "No supplier GSTIN to check.",
            citations=["gst-rules §1.2"],
        )
    return RuleResult(
        code="gst.gstin",
        name="GSTINs valid",
        status=RuleStatus.FAIL,
        severity="review",
        category=ExceptionCategory.TAX_MISMATCH,
        message=sentence("; ".join(problems)) + ".",
        citations=["gst-rules §1.2", "ap-policy §5.1"],
    )


def check_tax_type(data: InvoiceData, company_state: str, tolerance: Decimal) -> RuleResult:
    """CGST+SGST for intra-state supplies, IGST for inter-state ones, at the line rates."""
    if not data.vendor_gstin:
        return RuleResult(
            code="gst.tax_type",
            name="Correct type and amount of GST",
            status=RuleStatus.SKIP,
            message="No supplier GSTIN, so the supplier state is unknown.",
        )
    supplier_state = gstin_state_code(data.vendor_gstin)
    place = data.place_of_supply or company_state
    intra = supplier_state == place
    cgst, sgst, igst = (data.cgst or _ZERO), (data.sgst or _ZERO), (data.igst or _ZERO)
    supply = "intra-state" if intra else "inter-state"
    where = f"supplier state {supplier_state}, place of supply {place}"
    problems: list[str] = []
    if intra and igst > 0:
        problems.append(f"IGST {inr(igst)} charged on an intra-state supply ({where})")
    if not intra and (cgst > 0 or sgst > 0):
        problems.append(f"CGST/SGST {inr(cgst + sgst)} charged on an inter-state supply ({where})")
    if intra and abs(cgst - sgst) > tolerance:
        problems.append(f"CGST {inr(cgst)} and SGST {inr(sgst)} should be equal")

    rated = [ln for ln in data.lines if ln.amount is not None and ln.tax_rate is not None]
    if rated and len(rated) == len(data.lines):
        expected = sum(((ln.amount or _ZERO) * (ln.tax_rate or _ZERO) / 100 for ln in rated), _ZERO)
        charged = cgst + sgst + igst
        if abs(expected - charged) > tolerance:
            problems.append(
                f"tax at the line rates is {inr(expected)}, invoice charges {inr(charged)}"
            )

    citations = ["gst-rules §2.2" if intra else "gst-rules §2.3"]
    if not problems:
        heads = "CGST + SGST" if intra else "IGST"
        return RuleResult(
            code="gst.tax_type",
            name="Correct type and amount of GST",
            status=RuleStatus.PASS,
            message=f"{sentence(supply)} supply charged {heads} at the line rates.",
            citations=citations,
        )
    return RuleResult(
        code="gst.tax_type",
        name="Correct type and amount of GST",
        status=RuleStatus.FAIL,
        severity="review",
        category=ExceptionCategory.TAX_MISMATCH,
        message=sentence("; ".join(problems))
        + ". The vendor must issue a corrected invoice; AP must not adjust the tax.",
        citations=[*citations, "gst-rules §2.4", "ap-policy §5.2"],
        details={"supply": supply, "supplier_state": supplier_state, "place_of_supply": place},
    )
