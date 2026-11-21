# Test invoices

25 generated vendor documents covering every edge case the pipeline handles, plus ground
truth for evaluation. **Do not edit by hand** — change `backend/scripts/invoice_specs.py` and run:

```bash
cd backend
uv run python -m scripts.generate_invoices          # all, or pass one or more keys
```

Output is deterministic (same specs → identical bytes), and `tests/test_test_invoices.py` fails if
these files drift from the specs.

## Files

- `<key>.pdf` — the document. Digital PDFs have a text layer; scans are image-only (rasterised,
  skewed, noisy, JPEG-compressed) so extraction has to use the vision model.
- `ground-truth/<key>.json` — `fields` (what extraction should return, amounts as strings),
  `expected` (decision before humans act, exception categories, approvals, final decision after
  the intended human action, cited clauses, matched PO/GRN, proposed coding), `depends_on`,
  `erp_fault`.
- `manifest.json` — every case in a safe run order (dependencies first).

## Layouts

| Layout | Vendors | Look |
|---|---|---|
| classic | TechNova, Northwind, Malabar, Sunrise | boxed header, coloured table header |
| band | Kaveri, CloudNest, Rangoli | coloured top band, modern |
| ledger | Sharma, Zenith | Tally-style monospaced grid |
| service | Deccan, BlueLeaf | letter-style service bill |

Amounts print as "Rs." / "INR" (built-in PDF fonts have no ₹ glyph), with Indian digit grouping
and amount in words.

## Cases (run order)

Totals in INR. *Decision* is the expected outcome before any human acts.

| # | Key | Plan | Scenario | Type | Total | Decision | Exceptions |
|---|---|---|---|---|---|---|---|
| 1 | `northwind-clean` | clean | Clean 3-way match (inter-state, IGST) | digital | 345150.00 | **APPROVED** | — |
| 2 | `technova-keyboards` | clean | Clean 3-way match (intra-state, CGST + SGST) | digital | 85550.00 | **APPROVED** | — |
| 3 | `kaveri-po-remainder` | clean | Partial invoice that completes a PO | digital | 67555.00 | **APPROVED** | — |
| 4 | `sharma-within-tolerance` | tolerance | Price 1.2% above PO — inside the 2% tolerance | digital | 50150.00 | **APPROVED** | — |
| 5 | `deccan-scanned-poor` | #1 | Scanned, low-quality invoice | scan (poor) | 218300.00 | **NEEDS_REVIEW** | `LOW_CONFIDENCE` |
| 6 | `blueleaf-scanned-good` | #1 (control) | Scanned invoice, good quality | scan (good) | 60480.00 | **APPROVED** | — |
| 7 | `kaveri-scanned-medium` | #1 (control) | Scanned invoice, medium quality (skewed) | scan (medium) | 26550.00 | **APPROVED** | — |
| 8 | `sharma-split-1` | #2 | Split invoicing — part 1 of 3 | digital | 26880.00 | **APPROVED** | — |
| 9 | `sharma-split-2` | #2 | Split invoicing — part 2 of 3 | digital | 35140.00 | **APPROVED** | — |
| 10 | `sharma-split-3` | #2 | Split invoicing — part 3 exceeds the PO | digital | 40516.00 | **NEEDS_REVIEW** | `OVER_PO_BALANCE`, `QTY_VARIANCE` |
| 11 | `technova-contract-rate` | #3 | Unit price above the contracted rate | digital | 147500.00 | **NEEDS_REVIEW** | `PRICE_VARIANCE` |
| 12 | `northwind-resubmitted` | #4 | Exact duplicate — same invoice number resubmitted | digital | 345150.00 | **REJECTED** | — |
| 13 | `northwind-suspect-duplicate` | #4 | Likely duplicate — new number, same amount | digital | 345150.00 | **NEEDS_REVIEW** | `DUPLICATE_SUSPECT` |
| 14 | `malabar-missing-po` | #5 | No PO reference — PO must be inferred | digital | 420080.00 | **NEEDS_REVIEW** | `NO_PO` |
| 15 | `rangoli-bank-change` | #6 | Bank details changed (fraud signal) | digital | 223256.00 | **ON_HOLD** | `BANK_CHANGE` |
| 16 | `cloudnest-saas-nonpo` | #7 | Non-PO invoice — monthly SaaS subscription | digital | 49560.00 | **NEEDS_REVIEW** | — |
| 17 | `malabar-erp-mismatch` | #8 | ERP read-back mismatch (mock ERP drops a paisa) | digital | 148680.00 | **APPROVED** | — |
| 18 | `northwind-quote` | other | Not an invoice — a quotation | digital | 436600.00 | **REJECTED** | — |
| 19 | `sharma-credit-note` | other | Not an invoice — a credit note | digital | 1062.00 | **REJECTED** | — |
| 20 | `kaveri-igst-intra-state` | other | IGST charged on an intra-state supply | digital | 75520.00 | **NEEDS_REVIEW** | `TAX_MISMATCH` |
| 21 | `northwind-over-approval-limit` | other | Amount above ₹5 lakh — Finance Manager approval | digital | 879100.00 | **NEEDS_REVIEW** | — |
| 22 | `blueleaf-no-grn` | other | Goods/services not yet received (no GRN) | digital | 84000.00 | **ON_HOLD** | `NO_RECEIPT` |
| 23 | `malabar-math-error` | other | Totals do not add up | digital | 112200.00 | **REJECTED** | — |
| 24 | `zenith-blocked-vendor` | other | Invoice from a blocked vendor | digital | 21240.00 | **REJECTED** | — |
| 25 | `sunrise-unknown-vendor` | other | Supplier not in the vendor master | digital | 37760.00 | **ON_HOLD** | — |

## Order-dependent cases

- `sharma-split-2/3` assume the earlier split invoices were processed (PO balance).
- `northwind-resubmitted` and `northwind-suspect-duplicate` assume `northwind-clean` was
  processed. Uploading any PDF twice also exercises the exact file-hash duplicate check.
- `malabar-erp-mismatch` passes all rules; the mock ERP is told by `erp_fault` to store
  the amount one paisa short so the read-back check fails.
