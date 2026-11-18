"""Unit tests for each rule module on small synthetic invoices (no PDFs, no LLM)."""

from datetime import date
from decimal import Decimal as D

import pytest

from app.extraction.models import ExtractionResult, FieldEvidence, InvoiceData, LineItem
from app.rules import approval, arithmetic, duplicates, matching, tax, tolerance, vendor
from app.rules.decide import evaluate, history_record
from app.rules.models import (
    CaseContext,
    ContractRate,
    ContractTerms,
    Decision,
    InvoicedLine,
    InvoiceRecord,
    POMatch,
    ReferenceData,
    RuleStatus,
)
from app.rules.money import inr, sentence
from app.rules.policy import Policy
from app.rules.reference import load_seed_reference

POLICY = Policy()
TODAY = date(2026, 11, 18)


@pytest.fixture
def ref() -> ReferenceData:
    return load_seed_reference()


def technova(**overrides: object) -> InvoiceData:
    """A clean intra-state TechNova invoice against PO-2026-0045 (50 × ₹1,450, 18%)."""
    base: dict[str, object] = {
        "doc_type": "invoice",
        "vendor_name": "TechNova Solutions Pvt Ltd",
        "vendor_gstin": "29AABCT1332L1ZA",
        "buyer_gstin": "29AACCA4821K1ZM",
        "invoice_number": "TN/2026/9001",
        "invoice_date": date(2026, 11, 11),
        "po_number": "PO-2026-0045",
        "place_of_supply": "29",
        "bank_account_number": "50200031284417",
        "lines": [
            LineItem(
                line_no=1,
                description="Wireless keyboard and mouse set",
                hsn_sac="8471",
                qty=D("50"),
                unit_price=D("1450"),
                tax_rate=D("18"),
                amount=D("72500"),
            )
        ],
        "subtotal": D("72500"),
        "cgst": D("6525"),
        "sgst": D("6525"),
        "tax_total": D("13050"),
        "total": D("85550"),
    }
    base.update(overrides)
    return InvoiceData.model_validate(base)


def extraction(data: InvoiceData, confidence: float = 0.97) -> ExtractionResult:
    evidence = {
        name: FieldEvidence(value=str(getattr(data, name)), confidence=confidence)
        for name in ("vendor_gstin", "invoice_number", "invoice_date", "total")
        if getattr(data, name) is not None
    }
    return ExtractionResult(
        data=data, evidence=evidence, is_scanned=False, doc_type_source="text_layer"
    )


def ctx(**kw: object) -> CaseContext:
    return CaseContext.model_validate({"received_on": TODAY, **kw})


# --- money ---------------------------------------------------------------------------------------


def test_inr_uses_indian_grouping() -> None:
    assert inr(D("147500")) == "₹1,47,500"
    assert inr(D("147499.99")) == "₹1,47,499.99"
    assert inr(D("2500000")) == "₹25,00,000"
    assert inr(D("950")) == "₹950"
    assert sentence("iGST charged") == "IGST charged"


# --- document / arithmetic ----------------------------------------------------------------------


@pytest.mark.parametrize("doc_type", ["quote", "credit_note", "other"])
def test_non_invoice_documents_are_rejected(doc_type: str) -> None:
    r = arithmetic.check_document_type(technova(doc_type=doc_type))
    assert r.status is RuleStatus.FAIL and r.severity == "reject"
    assert "ap-policy §1.1" in r.citations


def test_arithmetic_passes_within_one_rupee() -> None:
    (r,) = arithmetic.check_arithmetic(technova(total=D("85550.90")), POLICY)
    assert r.status is RuleStatus.PASS


def test_total_off_by_more_than_one_rupee_rejects() -> None:
    (r,) = arithmetic.check_arithmetic(technova(total=D("85650")), POLICY)
    assert r.status is RuleStatus.FAIL and r.severity == "reject"
    assert r.details["total"] == "100"


def test_line_amount_mismatch_is_caught() -> None:
    line = technova().lines[0].model_copy(update={"amount": D("70000")})
    (r,) = arithmetic.check_arithmetic(technova(lines=[line]), POLICY)
    assert r.status is RuleStatus.FAIL and "line1" in r.details


def test_missing_required_fields_go_to_review() -> None:
    r = arithmetic.check_required_fields(technova(invoice_number=None))
    assert r.status is RuleStatus.FAIL and r.category == "LOW_CONFIDENCE"


def test_low_confidence_key_fields_go_to_review() -> None:
    r = arithmetic.check_confidence(extraction(technova(), confidence=0.6), POLICY)
    assert r.status is RuleStatus.WARN and r.category == "LOW_CONFIDENCE"


def test_future_invoice_date_is_flagged() -> None:
    r = arithmetic.check_dates(technova(invoice_date=date(2026, 12, 1)), TODAY)
    assert r.status is RuleStatus.WARN


# --- GST ------------------------------------------------------------------------------------------


def test_igst_on_intra_state_supply_is_a_tax_mismatch() -> None:
    data = technova(cgst=None, sgst=None, igst=D("13050"))
    r = tax.check_tax_type(data, "29", D("1"))
    assert r.status is RuleStatus.FAIL and r.category == "TAX_MISMATCH"
    assert r.message.startswith("IGST")


def test_cgst_sgst_on_inter_state_supply_is_a_tax_mismatch() -> None:
    data = technova(vendor_gstin="27AKZPS6612M1Z4")
    r = tax.check_tax_type(data, "29", D("1"))
    assert r.status is RuleStatus.FAIL and "gst-rules §2.3" in r.citations


def test_wrong_tax_amount_is_caught() -> None:
    data = technova(cgst=D("6000"), sgst=D("6000"))
    r = tax.check_tax_type(data, "29", D("1"))
    assert r.status is RuleStatus.FAIL and "line rates" in r.message


def test_invalid_supplier_gstin_and_wrong_buyer() -> None:
    r = tax.check_gstins(technova(vendor_gstin="29AABCT1332L1ZV"), "29AACCA4821K1ZM")
    assert r.status is RuleStatus.FAIL
    r = tax.check_gstins(technova(buyer_gstin="29AAAAA0000A1Z5"), "29AACCA4821K1ZM")
    assert r.status is RuleStatus.FAIL and "Buyer GSTIN" in r.message


# --- vendor ---------------------------------------------------------------------------------------


def test_vendor_by_gstin_then_by_name(ref: ReferenceData) -> None:
    v, r = vendor.identify_vendor(technova(), ref)
    assert v is not None and v.id == "V001" and r.status is RuleStatus.PASS
    v, r = vendor.identify_vendor(
        technova(vendor_gstin="29AABCT1332L2ZB", vendor_name="Technova Soln"), ref
    )
    assert v is not None and v.id == "V001"
    assert r.status is RuleStatus.FAIL and r.category == "TAX_MISMATCH"


def test_unknown_vendor_is_held(ref: ReferenceData) -> None:
    v, r = vendor.identify_vendor(
        technova(vendor_gstin="29ABCFS4410K1Z3", vendor_name="Sunrise Electronics"), ref
    )
    assert v is None and r.severity == "hold"


def test_bank_change_is_held(ref: ReferenceData) -> None:
    v = ref.vendor("V001")
    assert v is not None
    r = vendor.check_bank_details(technova(bank_account_number="50200031289999"), v)
    assert r.status is RuleStatus.FAIL and r.severity == "hold" and r.category == "BANK_CHANGE"
    assert vendor.check_bank_details(technova(bank_account_number=None), v).status is (
        RuleStatus.SKIP
    )


# --- matching -------------------------------------------------------------------------------------


def test_line_matching_uses_description_and_hsn(ref: ReferenceData) -> None:
    po = ref.po("PO-2026-0042")
    assert po is not None
    lines = [
        LineItem(line_no=1, description="USB C charging cable, 2 m", hsn_sac="8544"),
        LineItem(line_no=2, description="Docking station for laptops", hsn_sac="8473"),
    ]
    matches = matching.match_lines(lines, po.lines)
    assert [m.po_line_no for m in matches] == [2, 1]


def test_po_inference_when_no_po_is_printed(ref: ReferenceData) -> None:
    v = ref.vendor("V001")
    assert v is not None
    match, r = matching.find_po(technova(po_number=None), v, ref, POLICY)
    assert match is not None and match.po_number == "PO-2026-0045" and match.inferred
    assert r.category == "NO_PO" and r.status is RuleStatus.WARN


def test_po_of_another_vendor_is_not_accepted(ref: ReferenceData) -> None:
    v = ref.vendor("V001")
    assert v is not None
    _, r = matching.find_po(technova(po_number="PO-2026-0038"), v, ref, POLICY)
    assert r.category == "NO_PO" and "another vendor" in r.message


def test_contract_rate_respects_validity_dates() -> None:
    terms = ContractTerms(
        vendor_id="V002",
        source="contracts/sharma",
        rates=[
            ContractRate(
                item="A4 copier paper",
                hsn_sac="4802",
                rate=D("230"),
                valid_from=date(2026, 4, 1),
                valid_to=date(2026, 9, 30),
                citation="contracts/sharma §2.1",
            ),
            ContractRate(
                item="A4 copier paper",
                hsn_sac="4802",
                rate=D("240"),
                valid_from=date(2026, 10, 1),
                valid_to=date(2027, 3, 31),
                citation="contracts/sharma §2.2",
            ),
        ],
    )
    line = LineItem(line_no=1, description="A4 copier paper 75gsm (ream)", hsn_sac="4802")
    sept = matching.contract_rate_for(line, terms, date(2026, 9, 15))
    octo = matching.contract_rate_for(line, terms, date(2026, 10, 15))
    assert sept is not None and sept.citation == "contracts/sharma §2.1"
    assert octo is not None and octo.citation == "contracts/sharma §2.2"
    assert matching.contract_rate_for(line, terms, date(2027, 5, 1)) is None


# --- tolerances -----------------------------------------------------------------------------------


def _match(ref: ReferenceData, data: InvoiceData) -> POMatch:
    po = ref.po(data.po_number or "")
    assert po is not None
    return POMatch(
        po_number=po.po_number,
        inferred=False,
        score=1.0,
        lines=matching.match_lines(data.lines, po.lines),
    )


def _priced(price: str, qty: str = "50") -> InvoiceData:
    line = (
        technova()
        .lines[0]
        .model_copy(update={"unit_price": D(price), "qty": D(qty), "amount": D(price) * D(qty)})
    )
    return technova(lines=[line])


@pytest.mark.parametrize(
    ("price", "status"),
    [("1450", RuleStatus.PASS), ("1479", RuleStatus.PASS), ("1480", RuleStatus.FAIL)],
)
def test_price_tolerance_two_percent_per_line(
    ref: ReferenceData, price: str, status: RuleStatus
) -> None:
    data = _priced(price)
    po = ref.po("PO-2026-0045")
    r = tolerance.check_prices(data, po, _match(ref, data), None, POLICY)
    assert r.status is status


def test_price_tolerance_five_thousand_per_invoice(ref: ReferenceData) -> None:
    # +1.9% per unit is inside 2%, but on 200 units it is ₹5,500 over in total.
    data = _priced("1477.50", qty="200")
    r = tolerance.check_prices(data, ref.po("PO-2026-0045"), _match(ref, data), None, POLICY)
    assert r.status is RuleStatus.FAIL and "exceeds ₹5,000" in r.message


def test_contract_rate_beats_po_rate(ref: ReferenceData) -> None:
    terms = ContractTerms(
        vendor_id="V001",
        source="contracts/technova",
        rates=[
            ContractRate(
                item="Wireless keyboard and mouse set",
                hsn_sac="8471",
                rate=D("1300"),
                citation="contracts/technova §2.4",
            )
        ],
        rate_revision_citation="contracts/technova §2.5",
    )
    data = technova()
    r = tolerance.check_prices(data, ref.po("PO-2026-0045"), _match(ref, data), terms, POLICY)
    assert r.status is RuleStatus.FAIL
    assert r.details["reference_source"] == "contracts/technova §2.4"
    assert "contracts/technova §2.5" in r.citations


def test_cumulative_quantity_vs_grn(ref: ReferenceData) -> None:
    data = _priced("1450", qty="10")
    ref.invoices.append(
        InvoiceRecord(
            ref="earlier",
            vendor_id="V001",
            invoice_number="TN/1",
            invoice_date=date(2026, 11, 10),
            total=D("50000"),
            po_number="PO-2026-0045",
            status="APPROVED",
            lines=[InvoicedLine(po_line_no=1, qty=D("45"))],
        )
    )
    po = ref.po("PO-2026-0045")
    assert po is not None
    r = tolerance.check_quantities(data, po, _match(ref, data), ref, POLICY)
    assert r.status is RuleStatus.FAIL and "55 billed in total (45 earlier + 10 now)" in r.message
    # Excluding the earlier invoice (e.g. it is the suspected duplicate) clears it.
    r = tolerance.check_quantities(data, po, _match(ref, data), ref, POLICY, exclude={"earlier"})
    assert r.status is RuleStatus.PASS


def test_rejected_invoices_do_not_consume_the_po(ref: ReferenceData) -> None:
    ref.invoices.append(
        InvoiceRecord(
            ref="bad",
            vendor_id="V001",
            invoice_number="TN/2",
            invoice_date=date(2026, 11, 10),
            total=D("85550"),
            po_number="PO-2026-0045",
            status="REJECTED",
        )
    )
    data = technova()
    po = ref.po("PO-2026-0045")
    assert po is not None
    r = tolerance.check_po_balance(data, po, _match(ref, data), ref, POLICY)
    assert r.status is RuleStatus.PASS


def test_missing_grn_holds(ref: ReferenceData) -> None:
    po = ref.po("PO-2026-0035")
    assert po is not None
    grn, r = tolerance.check_receipt(po, ref)
    assert grn is None and r.severity == "hold" and r.category == "NO_RECEIPT"


def test_service_po_needs_no_grn(ref: ReferenceData) -> None:
    po = ref.po("PO-2026-0036")
    assert po is not None
    _, r = tolerance.check_receipt(po, ref)
    assert r.status is RuleStatus.SKIP


# --- duplicates -----------------------------------------------------------------------------------


def _prior(**kw: object) -> InvoiceRecord:
    base: dict[str, object] = {
        "ref": "case-1",
        "vendor_id": "V001",
        "invoice_number": "TN/2026/9001",
        "invoice_date": date(2026, 11, 1),
        "total": D("85550"),
        "status": "APPROVED",
    }
    base.update(kw)
    return InvoiceRecord.model_validate(base)


def test_exact_duplicate_ignores_punctuation(ref: ReferenceData) -> None:
    ref.invoices.append(_prior(invoice_number="TN-2026-9001"))
    original, r = duplicates.check_exact_duplicate(technova(), "V001", None, ref)
    assert original is not None and r.severity == "reject"


def test_same_file_hash_is_an_exact_duplicate(ref: ReferenceData) -> None:
    ref.invoices.append(_prior(invoice_number="OTHER", file_hash="abc"))
    original, _ = duplicates.check_exact_duplicate(technova(), "V001", "abc", ref)
    assert original is not None


def test_suspected_duplicate_window(ref: ReferenceData) -> None:
    ref.invoices.append(_prior(invoice_number="TN/2026/8000", invoice_date=date(2026, 10, 20)))
    hits, r = duplicates.check_suspected_duplicate(technova(), "V001", ref, POLICY)
    assert hits and r.category == "DUPLICATE_SUSPECT"
    ref.invoices[-1] = _prior(invoice_number="TN/2026/8000", invoice_date=date(2026, 10, 1))
    hits, _ = duplicates.check_suspected_duplicate(technova(), "V001", ref, POLICY)
    assert not hits  # 41 days apart


def test_different_service_periods_are_not_duplicates(ref: ReferenceData) -> None:
    ref.invoices.append(
        _prior(invoice_number="X", invoice_date=date(2026, 11, 1), service_period="Oct 2026")
    )
    data = technova(service_period="Nov 2026")
    hits, _ = duplicates.check_suspected_duplicate(data, "V001", ref, POLICY)
    assert not hits


# --- approvals ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "roles"),
    [
        ("500000", []),
        ("500000.01", ["Finance Manager"]),
        ("2500000", ["Finance Manager"]),
        ("2500001", ["Finance Manager", "CFO"]),
    ],
)
def test_approval_matrix_bands(total: str, roles: list[str]) -> None:
    assert [a.role for a in approval.po_approvals(D(total), POLICY)] == roles


def test_non_po_approvals() -> None:
    small = approval.non_po_approvals(D("49560"), "CC-IT-01", POLICY)
    assert [(a.role, a.name) for a in small] == [("Department Head — IT", "Rahul Menon")]
    big = approval.non_po_approvals(D("150000"), "CC-ADM-02", POLICY)
    assert [a.role for a in big] == ["Department Head — Admin", "Finance Manager"]


def test_coding_proposal_from_reviewed_history(ref: ReferenceData) -> None:
    v = ref.vendor("V005")
    assert v is not None
    coding = approval.propose_coding(v, ref)
    assert coding is not None and coding.gl_account == "6420" and "5 reviewed cases" in coding.basis


# --- decision precedence --------------------------------------------------------------------------


def test_clean_invoice_is_approved_touchless(ref: ReferenceData) -> None:
    a = evaluate(extraction(technova()), ref, ctx())
    assert a.decision is Decision.APPROVED and all(a.touchless.values())


def test_reject_beats_hold_beats_review(ref: ReferenceData) -> None:
    # bank change (hold) + price variance (review) → ON_HOLD
    held = _priced("1600").model_copy(update={"bank_account_number": "999999999999"})
    held = held.model_copy(
        update={
            "subtotal": D("80000"),
            "cgst": D("7200"),
            "sgst": D("7200"),
            "tax_total": D("14400"),
            "total": D("94400"),
        }
    )
    a = evaluate(extraction(held), ref, ctx())
    assert a.decision is Decision.ON_HOLD
    assert {e.category for e in a.exceptions} >= {"BANK_CHANGE", "PRICE_VARIANCE"}
    # … plus a wrong total (reject) → REJECTED, with no exceptions or approvals
    a = evaluate(extraction(held.model_copy(update={"total": D("99999")})), ref, ctx())
    assert a.decision is Decision.REJECTED and not a.exceptions and not a.approvals


def test_low_confidence_alone_means_review(ref: ReferenceData) -> None:
    a = evaluate(extraction(technova(), confidence=0.5), ref, ctx())
    assert a.decision is Decision.NEEDS_REVIEW
    assert [e.category for e in a.exceptions] == ["LOW_CONFIDENCE"]


def test_policy_change_changes_the_decision(ref: ReferenceData) -> None:
    data = _priced("1470")  # +1.4%
    data = data.model_copy(
        update={
            "subtotal": D("73500"),
            "cgst": D("6615"),
            "sgst": D("6615"),
            "tax_total": D("13230"),
            "total": D("86730"),
        }
    )
    assert evaluate(extraction(data), ref, ctx()).decision is Decision.APPROVED
    strict = Policy(price_tolerance_pct=D("1"))
    assert evaluate(extraction(data), ref, ctx(), strict).decision is Decision.NEEDS_REVIEW


def test_history_record_carries_po_line_quantities(ref: ReferenceData) -> None:
    data = technova()
    a = evaluate(extraction(data), ref, ctx())
    rec = history_record(a, data, "case-9", "hash")
    assert rec is not None and rec.po_number == "PO-2026-0045" and rec.status == "APPROVED"
    assert rec.lines == [InvoicedLine(po_line_no=1, qty=D("50"))]
