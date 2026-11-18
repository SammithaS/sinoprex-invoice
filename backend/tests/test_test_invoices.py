"""Consistency checks for the Phase 2 test data: specs ↔ seed data ↔ knowledge base ↔ files."""

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from app.rules.gst import is_valid_gstin
from scripts.generate_invoices import GT_DIR, OUT_DIR, ground_truth, run_order
from scripts.invoice_render import amount_in_words, format_inr
from scripts.invoice_specs import SPECS, VENDORS, InvoiceSpec, compute

DATA = Path(__file__).resolve().parents[2] / "data"
KNOWLEDGE = DATA / "knowledge"
SEED = DATA / "seed"


def _seed(name: str) -> Any:
    return json.loads((SEED / f"{name}.json").read_text(encoding="utf-8"))


VENDOR_MASTER = {v["id"]: v for v in _seed("vendors")}
POS = {p["po_number"]: p for p in _seed("purchase_orders")}
GRNS = {g["grn_number"]: g for g in _seed("goods_receipts")}
IDS = [s.key for s in SPECS]

# Cases whose lines deliberately differ from the PO.
PRICE_DEVIATIONS = {"technova-contract-rate", "sharma-within-tolerance"}
QTY_DEVIATIONS = {"sharma-split-3"}


# --- money helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("0", "0.00"),
        ("999", "999.00"),
        ("1000", "1,000.00"),
        ("147500", "1,47,500.00"),
        ("879100", "8,79,100.00"),
        ("12345678.5", "1,23,45,678.50"),
    ],
)
def test_format_inr(value: str, text: str) -> None:
    assert format_inr(Decimal(value)) == text


def test_amount_in_words() -> None:
    assert amount_in_words(Decimal("147500")) == (
        "Rupees One Lakh Forty Seven Thousand Five Hundred Only"
    )
    assert amount_in_words(Decimal("12345678.50")) == (
        "Rupees One Crore Twenty Three Lakh Forty Five Thousand Six Hundred Seventy Eight "
        "and Fifty Paise Only"
    )
    assert amount_in_words(Decimal("1062")) == "Rupees One Thousand Sixty Two Only"


# --- specs ----------------------------------------------------------------------------------


def test_keys_unique_and_enough_cases() -> None:
    assert len(IDS) == len(set(IDS))
    assert len(SPECS) >= 25
    assert sum(1 for s in SPECS if s.scan) >= 3


def test_every_plan_edge_case_is_covered() -> None:
    covered = {s.edge_case.split()[0] for s in SPECS}
    assert {f"#{i}" for i in range(1, 9)} <= covered
    assert {s.expected.decision for s in SPECS} == {
        "APPROVED",
        "NEEDS_REVIEW",
        "ON_HOLD",
        "REJECTED",
    }


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_vendor_profile_matches_master(spec: InvoiceSpec) -> None:
    profile = VENDORS[spec.vendor]
    assert is_valid_gstin(profile.gstin)
    if profile.vendor_id is None:  # deliberately unknown supplier
        assert profile.gstin not in {v["gstin"] for v in VENDOR_MASTER.values()}
        return
    master = VENDOR_MASTER[profile.vendor_id]
    assert profile.gstin == master["gstin"]
    assert profile.bank_ifsc == master["bank_ifsc"]
    assert profile.bank_account.endswith(master["bank_account_last4"])
    if spec.bank:  # bank-change case: the printed account must differ from the master
        assert not spec.bank[1].endswith(master["bank_account_last4"])


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_arithmetic(spec: InvoiceSpec) -> None:
    comp = compute(spec)
    assert sum(Decimal(line["amount"]) for line in comp.lines) == comp.subtotal
    assert comp.subtotal + comp.tax_total == comp.total
    if comp.intra_state:
        assert comp.igst == 0 and comp.cgst == comp.sgst
    else:
        assert comp.cgst == comp.sgst == 0
    gt = ground_truth(spec)
    if spec.printed_total:
        assert Decimal(gt["fields"]["total"]) - comp.total > 1  # deliberately wrong by > ₹1
    else:
        assert Decimal(gt["fields"]["total"]) == comp.total


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_po_and_grn_references(spec: InvoiceSpec) -> None:
    exp = spec.expected
    vendor_id = VENDORS[spec.vendor].vendor_id
    if spec.po_number and spec.vendor != "sunrise":
        assert spec.po_number in POS
    if spec.vendor == "sunrise":
        assert spec.po_number not in POS  # unknown vendor quotes a PO that doesn't exist
    if exp.matched_po:
        po = POS[exp.matched_po]
        assert po["vendor_id"] == vendor_id
        assert spec.po_number in (None, exp.matched_po)
        po_lines = {line["description"]: line for line in po["lines"]}
        for line in spec.lines:
            match = po_lines[line.description]
            if spec.key not in PRICE_DEVIATIONS:
                assert Decimal(line.unit_price) == Decimal(match["unit_price"])
            assert Decimal(line.tax_rate) == Decimal(match["tax_rate"])
            assert line.hsn_sac == match["hsn_sac"]
            if spec.key not in QTY_DEVIATIONS:
                assert Decimal(line.qty) <= Decimal(match["qty"])
    if exp.matched_grn:
        assert GRNS[exp.matched_grn]["po_number"] == exp.matched_po
        assert GRNS[exp.matched_grn]["received_date"] <= spec.date
    elif exp.matched_po and POS[exp.matched_po]["requires_grn"] and exp.decision != "REJECTED":
        assert all(g["po_number"] != exp.matched_po for g in GRNS.values())


def test_split_invoices_exceed_po_only_on_the_third() -> None:
    by_key = {s.key: compute(s).total for s in SPECS}
    po_total = Decimal(POS["PO-2026-0038"]["total"])
    first_two = by_key["sharma-split-1"] + by_key["sharma-split-2"]
    assert first_two <= po_total
    assert first_two + by_key["sharma-split-3"] - po_total == Decimal("5376.00")


def test_run_order_respects_dependencies() -> None:
    order = [s.key for s in run_order(SPECS)]
    for spec in SPECS:
        for dep in spec.depends_on:
            assert order.index(dep) < order.index(spec.key)


# --- knowledge base -------------------------------------------------------------------------

_CITATION = re.compile(r"^(?P<doc>[a-z\-/]+) §(?P<section>[\d.]+)$")


def _sections(doc: str) -> set[str]:
    text = (KNOWLEDGE / f"{doc}.md").read_text(encoding="utf-8")
    return set(re.findall(r"^#{2,3} (\d+(?:\.\d+)?)\.? ", text, flags=re.MULTILINE))


@pytest.mark.parametrize("spec", [s for s in SPECS if s.expected.citations], ids=lambda s: s.key)
def test_citations_resolve_to_knowledge_sections(spec: InvoiceSpec) -> None:
    for citation in spec.expected.citations:
        m = _CITATION.match(citation)
        assert m, citation
        assert m["section"] in _sections(m["doc"]), citation


def test_every_vendor_has_a_contract_with_metadata() -> None:
    for vid in VENDOR_MASTER:
        slug = VENDOR_MASTER[vid]["name"].lower().split()[0]
        text = (KNOWLEDGE / "contracts" / f"{slug}.md").read_text(encoding="utf-8")
        assert f"vendor_id: {vid}" in text
        assert "valid_from:" in text and "valid_to:" in text


def test_technova_contract_rate_is_below_the_invoiced_rate() -> None:
    text = (KNOWLEDGE / "contracts" / "technova.md").read_text(encoding="utf-8")
    section = text.split("### 2.1 ", 1)[1].split("###", 1)[0]
    assert "₹1,100 per NOS" in section


# --- generated files are up to date ---------------------------------------------------------


def test_manifest_lists_every_case() -> None:
    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert [m["key"] for m in manifest] == [s.key for s in run_order(SPECS)]


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_committed_ground_truth_is_current(spec: InvoiceSpec) -> None:
    on_disk = json.loads((GT_DIR / f"{spec.key}.json").read_text(encoding="utf-8"))
    assert on_disk == json.loads(json.dumps(ground_truth(spec), ensure_ascii=False))


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_pdf_text_layer(spec: InvoiceSpec) -> None:
    with pymupdf.open(OUT_DIR / f"{spec.key}.pdf") as doc:
        text = "".join(page.get_text() for page in doc.pages())
    if spec.scan:
        assert text.strip() == ""  # image-only, like a real scan
    else:
        assert spec.number in text
        assert VENDORS[spec.vendor].gstin in text
        assert format_inr(Decimal(ground_truth(spec)["fields"]["total"])) in text
