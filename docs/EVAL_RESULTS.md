# Evaluation results

Produced by `python -m scripts.eval --live --rag --write` (backend/). Offline mode (`python -m scripts.eval`, recorded LLM outputs) scores 100% on every pipeline metric and is what CI-style runs use; the numbers below are from **live** model calls.

Reliability note: on 2026-11-18 Gemini returned many 503s (overloaded) — the pipeline's fallbacks caught most of them, but 9 of 25 cases still ended on a provider error and were rerun (15 reruns in all; the times shown are the successful attempt's), and a second full run the same evening hit the free-tier daily quota (429) and was discarded. Automatic retries inside the LLM calls were added afterwards.

## Pipeline (extraction → rules → decision)

_Run 2026-11-18 · 25 samples in manifest order · live LLM calls · models: gemini-3.5-flash-lite (+ Groq / Gemini fallbacks) · fresh SQLite + seed data, in-memory index · `python -m scripts.eval --live`._

| Metric | Value |
|---|---|
| Decision accuracy (rules' decision vs ground truth) | **100.0%** |
| Exception categories exactly right | 100.0% |
| Expected citations present | 100.0% |
| Field-level extraction accuracy (560 fields) | **100.0%** |
| Invoices with every field right | 25 / 25 |
| Time to decision: median / p90 / max | 30.4 s / 52.9 s / 110.2 s |
| LLM calls per invoice | 4.84 |
| Tokens per invoice (in + out) | 3,886 (1,971 in + 1,915 out) |
| Failed LLM calls (caught by fallbacks) | 65 |
| Case reruns after provider errors (503/429) | 15 |
| Cases that ended FAILED | 0 |

| Case | Decision | Exceptions | Fields | Time | LLM calls | Tokens |
|---|---|---|---|---|---|---|
| northwind-clean | APPROVED | — | 26/26 | 45.8 s | 5 | 4,351 |
| technova-keyboards | APPROVED | — | 21/21 | 20.7 s ×2 | 6 | 4,272 |
| kaveri-po-remainder | APPROVED | — | 26/26 | 24.3 s ×3 | 10 | 5,003 |
| sharma-within-tolerance | APPROVED | — | 21/21 | 36.3 s ×2 | 9 | 4,583 |
| deccan-scanned-poor | NEEDS_REVIEW | LOW_CONFIDENCE | 21/21 | 36.8 s ×3 | 7 | 4,466 |
| blueleaf-scanned-good | APPROVED | — | 21/21 | 52.4 s ×3 | 8 | 5,208 |
| kaveri-scanned-medium | APPROVED | — | 21/21 | 46.4 s ×2 | 5 | 5,341 |
| sharma-split-1 | APPROVED | — | 21/21 | 47.2 s | 3 | 3,936 |
| sharma-split-2 | APPROVED | — | 26/26 | 52.9 s ×2 | 9 | 5,430 |
| sharma-split-3 | NEEDS_REVIEW | OVER_PO_BALANCE, QTY_VARIANCE | 26/26 | 30.4 s | 3 | 2,546 |
| technova-contract-rate | NEEDS_REVIEW | PRICE_VARIANCE | 21/21 | 26.4 s | 2 | 3,947 |
| northwind-resubmitted | REJECTED | — | 26/26 | 19.1 s | 2 | 2,965 |
| northwind-suspect-duplicate | NEEDS_REVIEW | DUPLICATE_SUSPECT | 26/26 | 30.9 s | 3 | 2,364 |
| malabar-missing-po | NEEDS_REVIEW | NO_PO | 21/21 | 16.0 s ×4 | 11 | 3,453 |
| rangoli-bank-change | ON_HOLD | BANK_CHANGE | 26/26 | 87.9 s ×3 | 10 | 3,892 |
| cloudnest-saas-nonpo | NEEDS_REVIEW | — | 21/21 | 9.4 s | 2 | 3,277 |
| malabar-erp-mismatch | APPROVED | ERP_MISMATCH | 21/21 | 34.3 s | 2 | 3,508 |
| northwind-quote | REJECTED | — | 21/21 | 22.7 s | 2 | 2,400 |
| sharma-credit-note | REJECTED | — | 21/21 | 18.1 s | 2 | 2,475 |
| kaveri-igst-intra-state | NEEDS_REVIEW | TAX_MISMATCH | 21/21 | 21.6 s | 3 | 2,128 |
| northwind-over-approval-limit | NEEDS_REVIEW | — | 21/21 | 17.1 s | 2 | 3,517 |
| blueleaf-no-grn | ON_HOLD | NO_RECEIPT | 21/21 | 110.2 s | 3 | 4,006 |
| malabar-math-error | REJECTED | — | 21/21 | 27.5 s | 4 | 8,090 |
| zenith-blocked-vendor | REJECTED | — | 21/21 | 54.8 s | 5 | 3,741 |
| sunrise-unknown-vendor | ON_HOLD | — | 21/21 | 23.2 s | 3 | 2,253 |

## RAG

_Retrieval: 18 questions (`data/eval/rag-questions.json`) · in-memory index, same chunks and FastEmbed bge-small-en-v1.5 embeddings as Neon · hybrid (vector + full-text, RRF) · vendor-first when a question names a vendor. Answers: Gemini, judged by an LLM against the expected facts, from the 2026-11-18 live run (retrieval before the vendor-first change)._

| Metric | Value |
|---|---|
| Retrieval hit-rate@5 | **94%** (was 89%) |
| Retrieval MRR@5 | 0.75 (was 0.66) |
| Answer correctness (LLM judge, facts covered) | **84%** |
| Answers citing a relevant section | 89% |

| # | Question | Rank of first relevant |
|---|---|---|
| q01 | What is the price tolerance on an invoice line, and the cap per invoice? | 1 |
| q02 | What is TechNova's agreed rate for the laptop docking station? | 1 |
| q03 | Has TechNova's docking station rate been revised? | 3 |
| q04 | What rate applies to Sharma's A4 copier paper from October 2026? | 2 |
| q05 | Who must approve a PO-backed invoice of ₹30 lakh? | 2 |
| q06 | What must AP do when the bank account on an invoice differs from the vendor master? | 1 |
| q07 | Can we pay an invoice when the goods have not been received yet? | 1 |
| q08 | A Karnataka supplier charged IGST to Acme. Is that correct? | 4 |
| q09 | When is an invoice treated as a suspected duplicate? | 1 |
| q10 | Which GL account and cost centre are CloudNest invoices coded to? | 3 |
| q11 | Does the Deccan housekeeping contract need a goods receipt? | 1 |
| q12 | Can Zenith Traders' invoices still be paid? | miss |
| q13 | What GST rate applies to goods transport by road? | 2 |
| q14 | How should credit notes be handled? | 1 |
| q15 | On which days are payment runs held? | 1 |
| q16 | What is Rangoli Packaging's payment term? | 1 |
| q17 | What should a vendor send if their invoice had an arithmetic error? | 1 |
| q18 | When is an exception escalated, and to whom? | 1 |
