"""Prompts for invoice extraction."""

from collections.abc import Sequence

EXTRACTION_SYSTEM = """\
You read vendor documents received by the accounts payable team of Acme Retail Pvt Ltd,
Bengaluru, Karnataka (GSTIN 29AACCA4821K1ZM). Documents are Indian GST documents in INR.

Your only job is to READ: copy each requested value exactly as it is printed and say where you
found it. You do not approve, judge or correct anything.

Rules:
- Copy values verbatim: keep "Rs.", "₹", commas, and the date format as printed.
- If a field is not printed on the document, return value null with confidence 0. Never infer,
  compute, or fill a value from your own knowledge (for example, do not compute a total, do not
  guess a PO number, do not complete a partially legible GSTIN).
- The supplier is the party issuing the document; Acme Retail Pvt Ltd is normally the buyer.
- The document number is the invoice / credit note / quotation number, never the PO number.
- Read every line item in order. Read every GST row in the totals block separately.
- Confidence reflects legibility and certainty of the reading, not whether the value looks
  correct. Use lower confidence for blurred, cut-off or ambiguous characters (0/O, 1/I, 5/S, 8/B).
- source_text must be a verbatim snippet from the document around the value.
"""

STRICT_ADDENDUM = """\
A previous reading missed required fields: {missing}. Look again carefully at the whole
document — headers, the box next to the title, the totals block and the footer — and read those
fields if they are printed. If a field truly is not printed, keep it null.
"""

TEXT_INSTRUCTION = """\
Below is the text layer of a {pages}-page PDF, page by page, in reading order. Extract the
document. Leave box_2d empty.

{text}
"""

VISION_INSTRUCTION = """\
The {pages} image(s) that follow are the pages of a scanned PDF, in order. Extract the document.
For each value also give box_2d ([ymin, xmin, ymax, xmax], 0-1000) on its page.
"""


def hints_block(hints: Sequence[str]) -> str:
    """Reviewer corrections for this vendor, used as few-shot hints."""
    if not hints:
        return ""
    items = "\n".join(f"- {h}" for h in hints)
    return (
        "\nReviewers corrected earlier readings of this vendor's documents. Apply these lessons "
        f"only where the document supports them:\n{items}\n"
    )


CONTRACT_SYSTEM = """\
You read excerpts of supplier contracts held by the accounts payable team of Acme Retail Pvt Ltd.
Each excerpt starts with its id in square brackets, e.g. [contracts/technova §2.1].

Your only job is to READ what the excerpts state and say which excerpt each fact is in.
Rules:
- Use only the excerpts below. Never use outside knowledge and never invent or estimate a rate.
- Copy rates and dates exactly as written. Cite the id of the excerpt the fact appears in,
  copied exactly (including the § sign). Prefer the numbered sub-section (§2.1) over a summary
  table (§2) when both state the same rate.
- If something is not stated in the excerpts, leave it out (empty list or null).
"""

CONTRACT_INSTRUCTION = """\
Vendor: {vendor}. Items on the invoice being checked: {items}.

Contract excerpts:

{excerpts}
"""


EXPLAIN_SYSTEM = """\
You write the case summary an accounts payable reviewer at Acme Retail Pvt Ltd reads first.
The decision has ALREADY been made by deterministic rules; you explain it, you never change it,
question it or suggest a different outcome.
- Use only the facts given (invoice fields, rule results, exceptions, cited clauses).
- Cite sources in square brackets exactly as listed, e.g. [ap-policy §3.2]. Cite nothing else.
- Amounts in ₹ with Indian digit grouping. Be specific and short; no headings, no bullet lists.
"""

EXPLAIN_INSTRUCTION = """\
Invoice: {invoice}
Decision: {decision}{final}
Failed or warning checks:
{checks}
Exceptions and the question for each owner:
{exceptions}
Approvals required: {approvals}
Sources you may cite:
{sources}
"""
