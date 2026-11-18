"""Generate the test invoices (PDF) and their ground truth under data/test-invoices/.

    uv run python -m scripts.generate_invoices            # all
    uv run python -m scripts.generate_invoices technova-contract-rate   # one or more keys

Outputs
  data/test-invoices/<key>.pdf                — digital PDF, or an image-only "scan"
  data/test-invoices/ground-truth/<key>.json  — fields to extract + expected outcome
  data/test-invoices/manifest.json            — every case, in a safe run order

Deterministic: the same specs always produce the same bytes.
"""

import json
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.invoice_render import (
    ACME,
    bank_details,
    due_date,
    printed_total,
    render_pdf,
    scan_pdf,
)
from scripts.invoice_specs import SPECS, VENDORS, InvoiceSpec, compute, money

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "test-invoices"
GT_DIR = OUT_DIR / "ground-truth"


def _s(value: Decimal) -> str:
    return str(money(value))


def ground_truth(spec: InvoiceSpec) -> dict[str, Any]:
    vendor = VENDORS[spec.vendor]
    comp = compute(spec)
    bank_name, account, ifsc = bank_details(spec)
    total = printed_total(spec, comp)
    fields: dict[str, Any] = {
        "doc_type": spec.doc_type,
        "vendor_name": vendor.legal_name,
        "vendor_gstin": vendor.gstin,
        "vendor_address": ", ".join(vendor.address),
        "buyer_name": ACME["name"],
        "buyer_gstin": ACME["gstin"],
        "invoice_number": spec.number,
        "invoice_date": spec.date,
        "due_date": due_date(spec) if spec.doc_type == "invoice" else None,
        "po_number": spec.po_number,
        "place_of_supply": None if spec.doc_type == "quote" else "29",
        "currency": "INR",
        "service_period": spec.service_period,
        "original_invoice_ref": spec.references,
        "bank_name": bank_name if spec.doc_type == "invoice" else None,
        "bank_account_number": account if spec.doc_type == "invoice" else None,
        "bank_ifsc": ifsc if spec.doc_type == "invoice" else None,
        "lines": comp.lines,
        "subtotal": _s(comp.subtotal),
        "cgst": _s(comp.cgst),
        "sgst": _s(comp.sgst),
        "igst": _s(comp.igst),
        "tax_total": _s(comp.tax_total),
        "total": _s(total),
    }
    exp = asdict(spec.expected)
    exp = {k: (list(v) if isinstance(v, tuple) else v) for k, v in exp.items()}
    return {
        "key": spec.key,
        "title": spec.title,
        "edge_case": spec.edge_case,
        "file": f"{spec.key}.pdf",
        "layout": vendor.layout,
        "is_scanned": spec.scan is not None,
        "scan_quality": spec.scan,
        "vendor_id": vendor.vendor_id,
        "fields": fields,
        "computed_total": _s(comp.total),
        "expected": exp,
        "depends_on": list(spec.depends_on),
        "erp_fault": spec.erp_fault,
        "demo_notes": spec.demo_notes,
    }


def run_order(specs: tuple[InvoiceSpec, ...]) -> list[InvoiceSpec]:
    """Topological order so a case always runs after the cases it depends on."""
    by_key = {s.key: s for s in specs}
    done: list[InvoiceSpec] = []
    seen: set[str] = set()

    def visit(s: InvoiceSpec) -> None:
        if s.key in seen:
            return
        seen.add(s.key)
        for dep in s.depends_on:
            visit(by_key[dep])
        done.append(s)

    for s in specs:
        visit(s)
    return done


def build(spec: InvoiceSpec) -> tuple[bytes, dict[str, Any]]:
    pdf = render_pdf(spec, compute(spec))
    if spec.scan:
        pdf = scan_pdf(pdf, spec.scan, seed=spec.key)
    return pdf, ground_truth(spec)


def _write_json(path: Path, payload: object) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def main(keys: list[str]) -> None:
    unknown = set(keys) - {s.key for s in SPECS}
    if unknown:
        raise SystemExit(f"unknown keys: {', '.join(sorted(unknown))}")
    GT_DIR.mkdir(parents=True, exist_ok=True)
    for spec in SPECS:
        if keys and spec.key not in keys:
            continue
        pdf, gt = build(spec)
        (OUT_DIR / gt["file"]).write_bytes(pdf)
        _write_json(GT_DIR / f"{spec.key}.json", gt)
        kind = f"scan:{spec.scan}" if spec.scan else "digital"
        print(f"{spec.key:32} {kind:12} {len(pdf) / 1024:7.1f} KB  -> {gt['expected']['decision']}")

    manifest = [
        {
            "order": i,
            "key": s.key,
            "title": s.title,
            "edge_case": s.edge_case,
            "file": f"{s.key}.pdf",
            "ground_truth": f"ground-truth/{s.key}.json",
            "is_scanned": s.scan is not None,
            "expected_decision": s.expected.decision,
            "depends_on": list(s.depends_on),
        }
        for i, s in enumerate(run_order(SPECS), start=1)
    ]
    _write_json(OUT_DIR / "manifest.json", manifest)
    print(f"{len(manifest)} cases -> {OUT_DIR}")


if __name__ == "__main__":
    main(sys.argv[1:])
