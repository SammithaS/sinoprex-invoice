"""Invoice extraction: PDF → normalised InvoiceData with per-field evidence and confidence.

    result = await extract_invoice(pdf_bytes)

The LLM only reads (app.llm.schemas.InvoiceRead). Everything after that is deterministic:
normalisation, locating values on the page, arithmetic, and confidence calibration.
"""

import base64
import time
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import pymupdf
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from app.extraction import normalise as n
from app.extraction.document import DocumentInfo, inspect_pdf
from app.extraction.evidence import amount_variants, box_from_vision, date_variants, locate
from app.extraction.models import (
    KEY_FIELDS,
    REQUIRED_FIELDS,
    ExtractionResult,
    FieldEvidence,
    InvoiceData,
    LineItem,
)
from app.llm import prompts
from app.llm.models import Kind, extraction_model
from app.llm.schemas import InvoiceRead, ReadValue
from app.rules.gst import is_valid_gstin

ModelFactory = Callable[[Kind], Runnable[list[BaseMessage], dict[str, Any]]]

CONFIDENCE_THRESHOLD = 0.8
ROUNDING = Decimal("1.00")  # ₹1, GST rules §5

# Confidence calibration constants (tuned on the sample set).
FOUND_IN_TEXT = 0.95  # value found verbatim in the PDF's own text layer
NOT_FOUND_IN_TEXT = 0.6  # digital PDF but the value isn't in its text → possible hallucination
ARITHMETIC_AGREES = 0.9  # amounts that reconcile (lines → subtotal → total)
UNPARSEABLE = 0.2
GSTIN_OCR_FIXED = 0.75
GSTIN_INVALID = 0.4


class ExtractionError(RuntimeError):
    """The model returned nothing usable."""


# --- prompt building ---------------------------------------------------------------------------


def build_messages(
    info: DocumentInfo, hints: Sequence[str] = (), missing: Sequence[str] = ()
) -> list[BaseMessage]:
    system = prompts.EXTRACTION_SYSTEM + prompts.hints_block(hints)
    if missing:
        system += "\n" + prompts.STRICT_ADDENDUM.format(missing=", ".join(missing))
    if not info.is_scanned:
        body = prompts.TEXT_INSTRUCTION.format(pages=len(info.pages), text=info.text)
        return [SystemMessage(system), HumanMessage(body)]
    content: list[str | dict[str, Any]] = [
        {"type": "text", "text": prompts.VISION_INSTRUCTION.format(pages=len(info.images))}
    ]
    for png in info.images:
        data = base64.b64encode(png).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
    return [SystemMessage(system), HumanMessage(content=content)]


# --- normalisation + evidence -----------------------------------------------------------------


class _Builder:
    """Turns one InvoiceRead into InvoiceData + evidence for one document."""

    def __init__(self, info: DocumentInfo, doc: pymupdf.Document) -> None:
        self.info, self.doc = info, doc
        self.evidence: dict[str, FieldEvidence] = {}

    def field(
        self,
        name: str,
        read: ReadValue,
        normalised: str | None,
        variants: Sequence[str] = (),
        notes: Sequence[str] = (),
    ) -> None:
        raw = read.value
        conf = read.confidence if raw is not None else 0.0
        ev = FieldEvidence(
            value=normalised,
            raw=raw,
            confidence=conf,
            page=read.page,
            snippet=read.source_text,
            source="missing"
            if raw is None
            else ("vision" if self.info.is_scanned else "text_layer"),
            notes=list(notes),
        )
        if raw is not None:
            if normalised is None:
                ev.confidence = min(ev.confidence, UNPARSEABLE)
                ev.notes.append(f"could not interpret {raw!r}")
            if self.info.is_scanned:
                box = box_from_vision(read.box_2d, read.page, self.info.pages)
                if box:
                    ev.page, ev.bbox = box
            else:
                loc = locate(self.doc, [raw, *variants, read.source_text], read.page)
                if loc:
                    ev.page, ev.bbox = loc.page, loc.bbox
                    ev.snippet = ev.snippet or loc.matched
                    if normalised is not None:
                        ev.confidence = max(ev.confidence, FOUND_IN_TEXT)
                else:
                    ev.confidence = min(ev.confidence, NOT_FOUND_IN_TEXT)
                    ev.notes.append("not found in the PDF text layer")
        self.evidence[name] = ev

    def derived(self, name: str, value: Decimal | None, parts: Sequence[str]) -> None:
        confs = [self.evidence[p].confidence for p in parts if p in self.evidence]
        self.evidence[name] = FieldEvidence(
            value=None if value is None else str(value),
            confidence=min(confs, default=0.0) if value is not None else 0.0,
            source="derived" if value is not None else "missing",
            notes=[f"sum of {', '.join(parts)}"] if value is not None else [],
        )


def _amount_field(b: _Builder, name: str, read: ReadValue) -> Decimal | None:
    value = n.parse_amount(read.value)
    b.field(
        name,
        read,
        None if value is None else str(value),
        amount_variants(value) if value is not None else (),
    )
    return value


def to_invoice(
    read: InvoiceRead, info: DocumentInfo, doc: pymupdf.Document
) -> tuple[InvoiceData, dict[str, FieldEvidence]]:
    b = _Builder(info, doc)

    def text(name: str) -> str | None:
        r: ReadValue = getattr(read, name)
        value = n.clean_text(r.value)
        b.field(name, r, value)
        return value

    def gstin(name: str) -> str | None:
        r: ReadValue = getattr(read, name)
        value, fixed = n.normalise_gstin(r.value)
        notes = ["OCR characters corrected to pass the checksum"] if fixed else []
        b.field(name, r, value, [value] if value else (), notes)
        ev = b.evidence[name]
        if fixed:
            ev.confidence = min(ev.confidence, GSTIN_OCR_FIXED)
        elif value and not is_valid_gstin(value):
            ev.confidence = min(ev.confidence, GSTIN_INVALID)
            ev.notes.append("GSTIN checksum fails")
        return value

    def a_date(name: str) -> Any:
        r: ReadValue = getattr(read, name)
        value = n.parse_date(r.value)
        b.field(
            name, r, value.isoformat() if value else None, date_variants(value) if value else ()
        )
        return value

    data = InvoiceData(doc_type=read.doc_type)
    data.vendor_name = text("vendor_name")
    data.vendor_gstin = gstin("vendor_gstin")
    data.vendor_address = text("vendor_address")
    data.buyer_name = text("buyer_name")
    data.buyer_gstin = gstin("buyer_gstin")
    data.invoice_number = text("invoice_number")
    data.invoice_date = a_date("invoice_date")
    data.due_date = a_date("due_date")
    po = n.normalise_po_number(read.po_number.value)
    b.field("po_number", read.po_number, po, [po] if po else ())
    data.po_number = po
    pos = n.normalise_state_code(read.place_of_supply.value)
    b.field("place_of_supply", read.place_of_supply, pos)
    data.place_of_supply = pos
    data.service_period = text("service_period")
    data.original_invoice_ref = text("original_invoice_ref")
    data.bank_name = text("bank_name")
    acct = n.normalise_account(read.bank_account_number.value)
    b.field("bank_account_number", read.bank_account_number, acct, [acct] if acct else ())
    data.bank_account_number = acct
    ifsc = n.normalise_ifsc(read.bank_ifsc.value)
    b.field("bank_ifsc", read.bank_ifsc, ifsc, [ifsc] if ifsc else ())
    data.bank_ifsc = ifsc

    data.subtotal = _amount_field(b, "subtotal", read.subtotal)
    data.total = _amount_field(b, "total", read.total)

    # Tax rows → per-head totals (UTGST counts as SGST; cess is only in the tax total).
    heads: dict[str, list[str]] = {"cgst": [], "sgst": [], "igst": []}
    sums: dict[str, Decimal] = {}
    tax_parts: list[str] = []
    for i, row in enumerate(read.tax_rows, start=1):
        key = f"tax_row_{i}"
        amount = _amount_field(b, key, row.amount)
        b.evidence[key].notes.append(f"{row.head} @ {row.rate or '?'}")
        tax_parts.append(key)
        if amount is None:
            continue
        head = {"UTGST": "sgst", "CESS": None}.get(row.head, row.head.lower())
        if head:
            heads[head].append(key)
            sums[head] = sums.get(head, Decimal(0)) + amount
        sums["tax_total"] = sums.get("tax_total", Decimal(0)) + amount
    for head in ("cgst", "sgst", "igst"):
        value = sums.get(head)
        setattr(data, head, value)
        b.derived(head, value, heads[head])
    data.tax_total = sums.get("tax_total") if read.tax_rows else None
    b.derived("tax_total", data.tax_total, tax_parts)

    for i, line in enumerate(read.lines, start=1):
        data.lines.append(
            LineItem(
                line_no=i,
                description=n.clean_text(line.description),
                hsn_sac=n.clean_text(line.hsn_sac),
                qty=n.parse_quantity(line.qty),
                uom=n.clean_text(line.uom),
                unit_price=n.parse_amount(line.unit_price),
                tax_rate=n.parse_rate(line.tax_rate),
                amount=n.parse_amount(line.amount),
                confidence=line.confidence,
                page=line.page,
            )
        )
    return data, b.evidence


# --- confidence calibration --------------------------------------------------------------------


def arithmetic_agrees(data: InvoiceData) -> bool:
    """Lines → subtotal → total reconcile within ₹1. Corroborates the amounts were read right."""
    if data.subtotal is None or data.total is None or data.tax_total is None or not data.lines:
        return False
    line_sum = sum((ln.amount for ln in data.lines if ln.amount is not None), Decimal(0))
    lines_ok = (
        all(ln.amount is not None for ln in data.lines)
        and abs(line_sum - data.subtotal) <= ROUNDING
    )
    return lines_ok and abs(data.subtotal + data.tax_total - data.total) <= ROUNDING


def scan_cap(quality: float | None) -> float:
    """Ceiling on any confidence read from a page image, from its legibility (0..1)."""
    return 1.0 if quality is None else round(0.55 + 0.4 * quality, 2)


def calibrate(
    data: InvoiceData, evidence: dict[str, FieldEvidence], info: DocumentInfo
) -> list[str]:
    notes: list[str] = []
    amounts = ["subtotal", "total", "tax_total", "cgst", "sgst", "igst"]
    amounts += [k for k in evidence if k.startswith("tax_row_")]
    if arithmetic_agrees(data):
        for name in amounts:
            ev = evidence.get(name)
            if ev and ev.value is not None:
                ev.confidence = max(ev.confidence, ARITHMETIC_AGREES)
        for line in data.lines:
            line.confidence = max(line.confidence, ARITHMETIC_AGREES)
    if info.is_scanned:
        cap = scan_cap(info.quality)
        if cap < 1:
            notes.append(f"scan quality {info.quality:.2f} → confidence capped at {cap:.2f}")
        for ev in evidence.values():
            if ev.confidence > cap:
                ev.confidence = cap
        for line in data.lines:
            line.confidence = min(line.confidence, cap)
    for ev in evidence.values():
        ev.confidence = round(ev.confidence, 2)
    return notes


# --- entry point -------------------------------------------------------------------------------


def _missing(data: InvoiceData) -> list[str]:
    return [f for f in REQUIRED_FIELDS if getattr(data, f) is None]


async def _read(
    model: Runnable[list[BaseMessage], dict[str, Any]], messages: list[BaseMessage]
) -> tuple[InvoiceRead, str | None, int, int]:
    out = await model.ainvoke(messages)
    parsed = out.get("parsed")
    if not isinstance(parsed, InvoiceRead):
        raise ExtractionError(f"model returned no usable structure: {out.get('parsing_error')}")
    raw = out.get("raw")
    meta = getattr(raw, "response_metadata", {}) or {}
    usage = getattr(raw, "usage_metadata", None) or {}
    name = meta.get("model_name") or meta.get("model")
    return parsed, name, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def _merge(first: InvoiceRead, retry: InvoiceRead) -> InvoiceRead:
    """Fill fields the first reading left empty from the stricter retry."""
    merged = first.model_copy(deep=True)
    for name, value in retry:
        current = getattr(merged, name)
        if (
            isinstance(current, ReadValue)
            and current.value is None
            and isinstance(value, ReadValue)
        ):
            setattr(merged, name, value)
    if not merged.lines and retry.lines:
        merged.lines = retry.lines
    if not merged.tax_rows and retry.tax_rows:
        merged.tax_rows = retry.tax_rows
    return merged


async def extract_invoice(
    pdf: bytes,
    *,
    hints: Sequence[str] = (),
    model_factory: ModelFactory = extraction_model,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> ExtractionResult:
    started = time.perf_counter()
    info = inspect_pdf(pdf)
    model = model_factory("vision" if info.is_scanned else "text")
    read, model_name, tok_in, tok_out = await _read(model, build_messages(info, hints))
    attempts = 1
    warnings: list[str] = []

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        data, evidence = to_invoice(read, info, doc)
        missing = _missing(data)
        if missing and read.doc_type == "invoice":
            # Edge case: retry once with a stricter prompt, then continue with what we have.
            attempts = 2
            retry, name2, t_in, t_out = await _read(model, build_messages(info, hints, missing))
            tok_in, tok_out = tok_in + t_in, tok_out + t_out
            model_name = name2 or model_name
            data, evidence = to_invoice(_merge(read, retry), info, doc)
            missing = _missing(data)

    warnings += calibrate(data, evidence, info)

    hint = info.doc_type_hint
    if hint and hint != data.doc_type:
        warnings.append(
            f"model said {data.doc_type!r} but the title reads as {hint!r}; using {hint!r}"
        )
        data.doc_type = hint
    source = "both" if hint and hint == read.doc_type else ("text_layer" if hint else "llm")

    low = [
        f
        for f in KEY_FIELDS
        if evidence.get(f) and evidence[f].value is not None and evidence[f].confidence < threshold
    ]
    return ExtractionResult(
        data=data,
        evidence=evidence,
        is_scanned=info.is_scanned,
        scan_quality=info.quality,
        doc_type_source=source,
        model=model_name,
        attempts=attempts,
        missing_required=missing,
        low_confidence=low,
        warnings=warnings,
        input_tokens=tok_in,
        output_tokens=tok_out,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
