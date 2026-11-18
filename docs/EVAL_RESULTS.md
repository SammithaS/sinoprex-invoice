# Evaluation results

## RAG

_Run 2026-11-18 · 18 questions (`data/eval/rag-questions.json`) · Neon PGVector index · FastEmbed bge-small-en-v1.5 · hybrid (vector + full-text, RRF)._

| Metric | Value |
|---|---|
| Retrieval hit-rate@5 | **89%** |
| Retrieval MRR@5 | 0.76 |
| Answer correctness (LLM judge, facts covered) | **82%** |
| Answers citing a relevant section | 89% |

| # | Question | Rank of first relevant | Answer score |
|---|---|---|---|
| q01 | What is the price tolerance on an invoice line, and the cap per invoice? | 1 | 1.00 |
| q02 | What is TechNova's agreed rate for the laptop docking station? | 1 | 1.00 |
| q03 | Has TechNova's docking station rate been revised? | 3 | 1.00 |
| q04 | What rate applies to Sharma's A4 copier paper from October 2026? | 1 | 0.50 |
| q05 | Who must approve a PO-backed invoice of ₹30 lakh? | 3 | 1.00 |
| q06 | What must AP do when the bank account on an invoice differs from the vendor master? | 1 | 0.33 |
| q07 | Can we pay an invoice when the goods have not been received yet? | 1 | 1.00 |
| q08 | A Karnataka supplier charged IGST to Acme. Is that correct? | 2 | 0.50 |
| q09 | When is an invoice treated as a suspected duplicate? | 1 | 1.00 |
| q10 | Which GL account and cost centre are CloudNest invoices coded to? | 1 | 1.00 |
| q11 | Does the Deccan housekeeping contract need a goods receipt? | miss | 0.50 |
| q12 | Can Zenith Traders' invoices still be paid? | miss | 0.00 |
| q13 | What GST rate applies to goods transport by road? | 1 | 1.00 |
| q14 | How should credit notes be handled? | 1 | 1.00 |
| q15 | On which days are payment runs held? | 1 | 1.00 |
| q16 | What is Rangoli Packaging's payment term? | 2 | 1.00 |
| q17 | What should a vendor send if their invoice had an arithmetic error? | 1 | 1.00 |
| q18 | When is an exception escalated, and to whom? | 1 | 1.00 |
