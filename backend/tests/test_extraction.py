"""Phase 3 — extraction. No network: the LLM is replaced by
* a synthetic "perfect reader" built from ground truth (exercises everything after the LLM
  on the real generated PDFs), and
* recorded real model outputs from tests/fixtures/extraction/ when present.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pytest

from app.config import Settings
from app.extraction import extract_invoice
from app.extraction import normalise as n
from app.extraction.document import DocumentError, classify_text, inspect_pdf
from app.extraction.extractor import build_messages, scan_cap
from app.llm.models import LLMNotConfiguredError, candidate_models
from app.llm.schemas import InvoiceRead, ReadLine, ReadTaxRow, ReadValue
from scripts.eval_extraction import FIXTURES, compare, fixed_factory, replay_factory
from scripts.invoice_render import display_date, format_inr

INVOICES = Path(__file__).resolve().parents[2] / "data" / "test-invoices"
MANIFEST = json.loads((INVOICES / "manifest.json").read_text(encoding="utf-8"))
KEYS = [m["key"] for m in MANIFEST]


def gt(key: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (INVOICES / "ground-truth" / f"{key}.json").read_text(encoding="utf-8")
    )
    return data


def pdf(key: str) -> bytes:
    return (INVOICES / f"{key}.pdf").read_bytes()


# --- synthetic reader ----------------------------------------------------------------------


def _v(value: str | None, conf: float = 0.95) -> ReadValue:
    return ReadValue(value=value, confidence=conf if value is not None else 0.0, page=1)


def _money(text: str) -> str:
    return format_inr(Decimal(text))


def perfect_read(key: str, conf: float = 0.95) -> InvoiceRead:
    """What an ideal reader would return: every value as printed on the generated PDF."""
    f = gt(key)["fields"]
    tax_rows: list[ReadTaxRow] = []
    for rate in sorted({ln["tax_rate"] for ln in f["lines"]}, key=Decimal):
        amounts = [Decimal(ln["amount"]) for ln in f["lines"] if ln["tax_rate"] == rate]
        if Decimal(f["igst"]):
            igst = sum((a * Decimal(rate) / 100 for a in amounts), Decimal(0))
            tax_rows.append(ReadTaxRow(head="IGST", rate=f"{rate}%", amount=_v(format_inr(igst))))
        else:
            half = sum((a * Decimal(rate) / 200 for a in amounts), Decimal(0))
            heads: tuple[Literal["CGST"], Literal["SGST"]] = ("CGST", "SGST")
            for head in heads:
                tax_rows.append(
                    ReadTaxRow(
                        head=head, rate=f"{Decimal(rate) / 2:g}%", amount=_v(format_inr(half))
                    )
                )
    lines = [
        ReadLine(
            description=ln["description"],
            hsn_sac=ln["hsn_sac"],
            qty=f"{Decimal(ln['qty']):,.0f}",
            uom=ln["uom"],
            unit_price=_money(ln["unit_price"]),
            tax_rate=f"{ln['tax_rate']}%",
            amount=_money(ln["amount"]),
            confidence=conf,
            page=1,
        )
        for ln in f["lines"]
    ]
    return InvoiceRead(
        doc_type=f["doc_type"],
        vendor_name=_v(f["vendor_name"], conf),
        vendor_gstin=_v(f["vendor_gstin"], conf),
        vendor_address=_v(None),
        buyer_name=_v(f["buyer_name"], conf),
        buyer_gstin=_v(f["buyer_gstin"], conf),
        invoice_number=_v(f["invoice_number"], conf),
        invoice_date=_v(display_date(f["invoice_date"]), conf),
        due_date=_v(display_date(f["due_date"]) if f["due_date"] else None, conf),
        po_number=_v(f["po_number"], conf),
        place_of_supply=_v("Karnataka (29)" if f["place_of_supply"] else None, conf),
        service_period=_v(f["service_period"], conf),
        original_invoice_ref=_v(f["original_invoice_ref"], conf),
        bank_name=_v(f["bank_name"], conf),
        bank_account_number=_v(f["bank_account_number"], conf),
        bank_ifsc=_v(f["bank_ifsc"], conf),
        subtotal=_v(_money(f["subtotal"]), conf),
        tax_rows=tax_rows,
        total=_v(f"Rs. {_money(f['total'])}", conf),
        lines=lines,
    )


# --- normalisation -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Rs. 1,47,500.00", "147500.00"),
        ("₹1,47,500", "147500.00"),
        ("INR 147500/-", "147500.00"),
        ("1,250.5", "1250.50"),
        ("(1,000.00)", "-1000.00"),
        ("abc", None),
        (None, None),
    ],
)
def test_parse_amount(text: str | None, want: str | None) -> None:
    got = n.parse_amount(text)
    assert (str(got) if got is not None else None) == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("10-11-2026", date(2026, 11, 10)),
        ("10/11/26", date(2026, 11, 10)),
        ("10.11.2026", date(2026, 11, 10)),
        ("10-Nov-2026", date(2026, 11, 10)),
        ("10th November, 2026", date(2026, 11, 10)),
        ("2026-11-10", date(2026, 11, 10)),
        ("31-02-2026", None),
        ("next Tuesday", None),
    ],
)
def test_parse_date(text: str, want: date | None) -> None:
    assert n.parse_date(text) == want


def test_gstin_ocr_fix_only_when_checksum_passes() -> None:
    assert n.normalise_gstin("29AABCT1332L1ZA") == ("29AABCT1332L1ZA", False)
    assert n.normalise_gstin("29 AABCT 1332 L1ZA") == ("29AABCT1332L1ZA", False)
    assert n.normalise_gstin("29AABCTI332L1ZA") == ("29AABCT1332L1ZA", True)  # I → 1
    bad, fixed = n.normalise_gstin("29AABCT1332L1ZB")  # wrong check character: no "fix"
    assert (bad, fixed) == ("29AABCT1332L1ZB", False)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("PO-2026-0042", "PO-2026-0042"),
        ("PO 2026/0042", "PO-2026-0042"),
        ("P.O. No. PO-2026-42", "PO-2026-0042"),
        ("po-2026-0042", "PO-2026-0042"),
        ("Ref: verbal", None),
    ],
)
def test_po_number(text: str, want: str | None) -> None:
    assert n.normalise_po_number(text) == want


def test_misc_normalisers() -> None:
    assert n.normalise_state_code("Karnataka (29)") == "29"
    assert n.normalise_state_code("Tamil Nadu") == "33"
    assert n.parse_rate("18%") == Decimal(18)
    assert n.parse_rate("9% + 9%") == Decimal(18)
    assert n.parse_quantity("5,000") == Decimal(5000)
    assert n.parse_quantity("2.50 kg") == Decimal("2.5")
    assert n.normalise_ifsc("hdfc 0000123") == "HDFC0000123"
    assert n.normalise_account("A/c 5020 0031 2844 17") == "50200031284417"


# --- document check ------------------------------------------------------------------------


@pytest.mark.parametrize("key", KEYS)
def test_document_check(key: str) -> None:
    truth = gt(key)
    info = inspect_pdf(pdf(key))
    assert info.is_scanned == truth["is_scanned"]
    if truth["is_scanned"]:
        assert info.images and info.doc_type_hint is None and info.quality is not None
    else:
        assert not info.images and info.quality is None
        assert info.doc_type_hint == truth["fields"]["doc_type"]


def test_scan_quality_orders_the_scans() -> None:
    q = {k: inspect_pdf(pdf(k)).quality for k in KEYS if gt(k)["is_scanned"]}
    poor, medium, good = (
        q["deccan-scanned-poor"],
        q["kaveri-scanned-medium"],
        q["blueleaf-scanned-good"],
    )
    assert poor is not None and medium is not None and good is not None
    assert poor < medium <= good
    assert scan_cap(poor) < 0.8 <= scan_cap(medium)


def test_classify_text_prefers_title() -> None:
    assert classify_text("CREDIT NOTE\nAgainst Invoice No. 12") == "credit_note"
    assert classify_text("QUOTATION\nQuotation No. Q1") == "quote"
    assert classify_text("TAX INVOICE\nInvoice No. 7") == "invoice"
    assert classify_text("Statement of account") is None


def test_not_a_pdf() -> None:
    with pytest.raises(DocumentError):
        inspect_pdf(b"hello")


def test_messages_text_vs_vision() -> None:
    digital = build_messages(inspect_pdf(pdf("technova-contract-rate")))
    assert "TN/2026/1042" in str(digital[1].content)
    scanned = build_messages(inspect_pdf(pdf("deccan-scanned-poor")), hints=["Use the IRN"])
    parts = scanned[1].content
    assert isinstance(parts, list)
    assert any(isinstance(p, dict) and p.get("type") == "image_url" for p in parts)
    assert "Use the IRN" in str(scanned[0].content)


# --- full pipeline with the synthetic reader -----------------------------------------------


@pytest.mark.parametrize("key", KEYS)
async def test_pipeline_perfect_reader(key: str) -> None:
    truth = gt(key)
    result = await extract_invoice(pdf(key), model_factory=fixed_factory(perfect_read(key)))
    wrong = [k for k, ok in compare(result, truth).items() if not ok]
    assert wrong == []
    assert result.missing_required == []
    if not truth["is_scanned"]:
        # Every value was found in the text layer → high confidence + a highlight box.
        for name in ("vendor_gstin", "invoice_number", "invoice_date", "total", "subtotal"):
            ev = result.evidence[name]
            assert ev.source == "text_layer" and ev.bbox is not None, name
            assert ev.confidence >= 0.95, name
        assert result.low_confidence == []


async def test_poor_scan_is_low_confidence_even_if_the_model_is_sure() -> None:
    key = "deccan-scanned-poor"
    result = await extract_invoice(pdf(key), model_factory=fixed_factory(perfect_read(key, 1.0)))
    assert result.is_scanned and result.scan_quality is not None
    assert result.min_key_confidence < 0.8
    assert "invoice_number" in result.low_confidence
    assert any("capped" in w for w in result.warnings)


async def test_good_scan_keeps_high_confidence() -> None:
    key = "blueleaf-scanned-good"
    result = await extract_invoice(pdf(key), model_factory=fixed_factory(perfect_read(key)))
    assert result.low_confidence == []


async def test_value_not_in_text_layer_is_distrusted() -> None:
    key = "technova-contract-rate"
    read = perfect_read(key)
    read.invoice_number = _v("TN/2026/9999")  # hallucinated: not on the PDF
    result = await extract_invoice(pdf(key), model_factory=fixed_factory(read))
    ev = result.evidence["invoice_number"]
    assert ev.confidence <= 0.6 and ev.bbox is None
    assert "invoice_number" in result.low_confidence


async def test_retry_fills_missing_required_fields() -> None:
    key = "northwind-clean"
    first = perfect_read(key)
    first.total = _v(None)
    first.invoice_date = _v(None)
    result = await extract_invoice(pdf(key), model_factory=fixed_factory(first, perfect_read(key)))
    assert result.attempts == 2
    assert result.missing_required == []
    assert result.data.total == Decimal("345150.00")


async def test_missing_after_retry_is_reported() -> None:
    key = "northwind-clean"
    read = perfect_read(key)
    read.total = _v(None)
    result = await extract_invoice(pdf(key), model_factory=fixed_factory(read))
    assert result.attempts == 2
    assert result.missing_required == ["total"]


async def test_title_overrides_model_doc_type() -> None:
    key = "northwind-quote"
    read = perfect_read(key)
    read.doc_type = "invoice"
    result = await extract_invoice(pdf(key), model_factory=fixed_factory(read))
    assert result.data.doc_type == "quote"
    assert any("title reads as 'quote'" in w for w in result.warnings)


async def test_ocr_gstin_fix_lowers_confidence() -> None:
    key = "kaveri-scanned-medium"
    read = perfect_read(key)
    read.vendor_gstin = _v("29BNKPK3389DIZN")  # 'I' read for the entity digit '1'? (not fixable)
    result = await extract_invoice(pdf(key), model_factory=fixed_factory(read))
    assert result.evidence["vendor_gstin"].confidence <= 0.4  # checksum fails, no safe fix
    read.vendor_gstin = _v("29BNKPK33B9D1ZN")  # B for 8 in a digit position → fixable
    result = await extract_invoice(pdf(key), model_factory=fixed_factory(read))
    assert result.data.vendor_gstin == "29BNKPK3389D1ZN"
    assert result.evidence["vendor_gstin"].confidence <= 0.75


async def test_mixed_rate_tax_rows_are_summed_in_python() -> None:
    result = await extract_invoice(
        pdf("sharma-split-3"), model_factory=fixed_factory(perfect_read("sharma-split-3"))
    )
    assert result.data.igst == Decimal("4716.00")
    assert result.data.tax_total == Decimal("4716.00")
    assert result.evidence["igst"].source == "derived"


async def test_math_error_is_read_not_corrected() -> None:
    result = await extract_invoice(
        pdf("malabar-math-error"), model_factory=fixed_factory(perfect_read("malabar-math-error"))
    )
    assert result.data.total == Decimal("112200.00")  # as printed, wrong on purpose
    assert result.data.subtotal is not None and result.data.tax_total is not None
    assert result.data.subtotal + result.data.tax_total == Decimal("112100.00")


# --- providers -----------------------------------------------------------------------------


def test_no_keys_is_a_clear_error() -> None:
    with pytest.raises(LLMNotConfiguredError, match="GOOGLE_API_KEY"):
        candidate_models("vision", Settings(google_api_key="", groq_api_key=""))


def test_fallback_order() -> None:
    both = Settings(google_api_key="g", groq_api_key="q", llm_provider="gemini")
    text = [label for label, _ in candidate_models("text", both)]
    assert text[0].startswith("gemini:") and text[1].startswith("groq:")
    assert len(text) == 3
    vision = [label for label, _ in candidate_models("vision", both)]
    assert all(label.startswith("gemini:") for label in vision)  # Groq never sees images
    groq_first = Settings(google_api_key="g", groq_api_key="q", llm_provider="groq")
    assert candidate_models("text", groq_first)[0][0].startswith("groq:")
    groq_only = Settings(google_api_key="", groq_api_key="q")
    assert [lbl.split(":")[0] for lbl, _ in candidate_models("text", groq_only)] == ["groq"]


# --- recorded real model outputs -----------------------------------------------------------

RECORDED = sorted(p.stem for p in FIXTURES.glob("*.json")) if FIXTURES.exists() else []


@pytest.mark.skipif(
    not RECORDED, reason="no recorded fixtures (run scripts.eval_extraction --record)"
)
@pytest.mark.parametrize("key", RECORDED)
async def test_recorded_extraction(key: str) -> None:
    truth = gt(key)
    result = await extract_invoice(pdf(key), model_factory=replay_factory(key))
    scores = compare(result, truth)
    accuracy = sum(scores.values()) / len(scores)
    assert accuracy >= 0.9, [k for k, ok in scores.items() if not ok]
    assert result.data.doc_type == truth["fields"]["doc_type"]
    for name, ev in result.evidence.items():
        assert 0 <= ev.confidence <= 1, name
    if key == "deccan-scanned-poor":
        assert result.low_confidence, "poor scan must go to review"
