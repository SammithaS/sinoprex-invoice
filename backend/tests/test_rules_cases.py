"""Every test invoice yields its expected decision and exceptions.

Extraction comes from the recorded LLM fixtures (no LLM calls); reference data from the seed;
contract terms from the recorded RAG readings (retrieved clauses + LLM reading, re-grounded
against the knowledge base on every run). Cases run in
manifest order and each one enters the invoice history, so split invoicing and duplicates see
the earlier cases (`depends_on`).
"""

import asyncio
import hashlib
import json
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from app.extraction import extract_invoice
from app.rag.clauses import topic_citations
from app.rules.decide import evaluate, history_record
from app.rules.models import Assessment, CaseContext, Decision, RuleStatus
from app.rules.reference import load_seed_reference
from app.rules.vendor import identify_vendor
from scripts.eval_extraction import replay_factory
from scripts.eval_rag import CONTRACT_FIXTURES, replay_contract_terms

INVOICES = Path(__file__).resolve().parents[2] / "data" / "test-invoices"
KNOWLEDGE = Path(__file__).resolve().parents[2] / "data" / "knowledge"
MANIFEST = json.loads((INVOICES / "manifest.json").read_text(encoding="utf-8"))
KEYS = [m["key"] for m in MANIFEST]
RECEIVED_ON = date(2026, 11, 18)

# Expected citations that come from the ERP read-back, not the rules.
DEFERRED_CITATIONS = {"ap-policy §10.1"}


def gt(key: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (INVOICES / "ground-truth" / f"{key}.json").read_text(encoding="utf-8")
    )
    return data


@cache
def run_all() -> dict[str, Assessment]:
    async def go() -> dict[str, Assessment]:
        ref = load_seed_reference()
        out: dict[str, Assessment] = {}
        for case in MANIFEST:
            key = case["key"]
            pdf = (INVOICES / case["file"]).read_bytes()
            extraction = await extract_invoice(pdf, model_factory=replay_factory(key))
            found, _ = identify_vendor(extraction.data, ref)
            ctx = CaseContext(
                case_ref=key,
                file_hash=hashlib.sha256(pdf).hexdigest(),
                received_on=RECEIVED_ON,
                contract=replay_contract_terms(key).terms
                if found and (CONTRACT_FIXTURES / f"{key}.json").exists()
                else None,
                contract_topics=topic_citations(found.id if found else None),
            )
            assessment = evaluate(extraction, ref, ctx)
            out[key] = assessment
            record = history_record(assessment, extraction.data, key, ctx.file_hash)
            if record is not None:
                ref.invoices.append(record)
        return out

    return asyncio.run(go())


@pytest.mark.parametrize("key", KEYS)
def test_expected_decision(key: str) -> None:
    a = run_all()[key]
    exp = gt(key)["expected"]
    failing = [f"{r.code}: {r.message}" for r in a.rule_results if r.status != RuleStatus.PASS]
    assert a.decision.value == exp["decision"], failing
    assert sorted({e.category.value for e in a.exceptions}) == sorted(exp["exceptions"]), failing
    assert [x.role for x in a.approvals] == exp["approvals"]


@pytest.mark.parametrize("key", KEYS)
def test_expected_matches_and_coding(key: str) -> None:
    a = run_all()[key]
    exp = gt(key)["expected"]
    if a.decision is not Decision.REJECTED:  # rejected cases stop before matching
        assert a.matched_po == exp["matched_po"]
        assert a.matched_grn == exp["matched_grn"]
    coding = exp["proposed_coding"]
    if coding is None:
        assert a.proposed_coding is None
    else:
        assert a.proposed_coding is not None
        assert a.proposed_coding.gl_account == coding["gl_account"]
        assert a.proposed_coding.cost_centre == coding["cost_centre"]


@pytest.mark.parametrize("key", KEYS)
def test_expected_citations(key: str) -> None:
    a = run_all()[key]
    want = set(gt(key)["expected"]["citations"]) - DEFERRED_CITATIONS
    assert want <= set(a.citations), sorted(want - set(a.citations))


def _section_exists(citation: str) -> bool:
    doc, _, section = citation.partition(" §")
    text = (KNOWLEDGE / f"{doc}.md").read_text(encoding="utf-8")
    return f"## {section} " in text or f"## {section}." in text


def test_every_citation_resolves() -> None:
    cited = {
        c
        for a in run_all().values()
        for c in [*a.citations, *(c for r in a.rule_results for c in r.citations)]
    }
    missing = sorted(c for c in cited if not _section_exists(c))
    assert not missing


def test_every_exception_names_an_owner_and_a_question() -> None:
    for key, a in run_all().items():
        for e in a.exceptions:
            assert e.owner_role and "?" in e.question, (key, e)
            assert e.citations, (key, e)


def test_rejected_cases_have_no_exceptions_or_approvals() -> None:
    for a in run_all().values():
        if a.decision is Decision.REJECTED:
            assert not a.exceptions and not a.approvals


def test_touchless_only_when_every_check_passes() -> None:
    for key, a in run_all().items():
        assert (a.decision is Decision.APPROVED) == all(a.touchless.values()), (key, a.touchless)


# --- spot checks on the headline edge cases -------------------------------------------------------


def test_contract_rate_variance_is_line_level_and_cited() -> None:
    a = run_all()["technova-contract-rate"]
    (exc,) = a.exceptions
    assert exc.variance["billed_rate"] == "1250.00"
    assert exc.variance["reference_rate"] == "1100.00"
    assert exc.variance["reference_source"] == "contracts/technova §2.1"
    assert exc.variance["over_total"] == "15000.00"
    assert exc.owner_name == "Rahul Menon"
    assert "₹1,100" in exc.question and "₹1,250" in exc.question


def test_split_invoice_three_reports_the_exact_excess() -> None:
    a = run_all()["sharma-split-3"]
    balance = next(e for e in a.exceptions if e.category == "OVER_PO_BALANCE")
    assert balance.variance["remaining"] == "35140.00"
    assert balance.variance["excess"] == "5376.00"
    assert "₹5,376" in balance.summary
    qty = next(e for e in a.exceptions if e.category == "QTY_VARIANCE")
    assert "320 billed in total (200 earlier + 120 now)" in qty.summary


def test_exact_duplicate_links_the_original() -> None:
    assert run_all()["northwind-resubmitted"].duplicate_of == "northwind-clean"
    assert run_all()["northwind-suspect-duplicate"].duplicate_of == "northwind-clean"


def test_low_confidence_scan_names_the_fields() -> None:
    (exc,) = run_all()["deccan-scanned-poor"].exceptions
    assert exc.owner_role == "AP Clerk"
    assert "scanned" in exc.summary


def test_bank_change_needs_two_approvers_and_uses_contact_on_file() -> None:
    a = run_all()["rangoli-bank-change"]
    (exc,) = a.exceptions
    assert exc.owner_role == "Vendor Master team"
    assert "9917" in exc.question and "contact on file" in exc.question
    assert [x.name for x in a.approvals] == ["Arjun Desai", "Sanjay Batra"]
