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
    """Reviewer corrections for this vendor, used as few-shot hints (Phase 8 fills these)."""
    if not hints:
        return ""
    items = "\n".join(f"- {h}" for h in hints)
    return (
        "\nReviewers corrected earlier readings of this vendor's documents. Apply these lessons "
        f"only where the document supports them:\n{items}\n"
    )
