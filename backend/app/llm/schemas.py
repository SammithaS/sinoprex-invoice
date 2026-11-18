"""Structured-output schemas the LLM fills in.

The model copies values **as printed** (strings); Python normalises them afterwards
(`app.extraction.normalise`). Keeping the LLM's job to "read and point" makes its output easy to
verify against the PDF text layer and keeps arithmetic out of the model.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ReadValue(BaseModel):
    """One value read from the document, with where it came from."""

    value: str | None = Field(
        description="The value exactly as printed (keep currency symbols, commas and date "
        "format as shown). null if the field is not on the document — never infer or compute it."
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description="How sure you are this is the correct, fully legible value for this field: "
        "1.0 = printed clearly and unambiguous; 0.7 = legible but you had to choose between "
        "candidates or some characters are unclear; below 0.5 = mostly guessed. 0 when null.",
    )
    page: int | None = Field(default=None, description="1-based page number where it appears.")
    source_text: str | None = Field(
        default=None,
        description="The exact text snippet (max ~80 chars) the value was read from, verbatim.",
    )
    box_2d: list[int] | None = Field(
        default=None,
        description="Only for page images: [ymin, xmin, ymax, xmax] of the value on the page, "
        "normalised to 0-1000. Omit for text input.",
    )


class ReadLine(BaseModel):
    """One line item, as printed."""

    description: str | None
    hsn_sac: str | None = Field(description="HSN (goods) or SAC (services) code as printed.")
    qty: str | None
    uom: str | None = Field(description="Unit of measure as printed, e.g. NOS, REAM, MONTH.")
    unit_price: str | None
    tax_rate: str | None = Field(description="GST rate as printed for the line, e.g. '18%'.")
    amount: str | None = Field(description="Line taxable amount as printed (before GST).")
    confidence: float = Field(ge=0, le=1, description="Confidence for the line as a whole.")
    page: int | None = None


class ReadTaxRow(BaseModel):
    """One GST row from the totals block."""

    head: Literal["CGST", "SGST", "UTGST", "IGST", "CESS"]
    rate: str | None = Field(description="Rate as printed, e.g. '9%'.")
    amount: ReadValue


class InvoiceRead(BaseModel):
    """Everything the model read from one vendor document."""

    doc_type: Literal["invoice", "credit_note", "quote", "other"] = Field(
        description="'invoice' for a tax invoice / bill of supply; 'credit_note' for a credit "
        "note; 'quote' for a quotation, estimate or proforma; 'other' for anything else "
        "(PO, delivery challan, statement, letter)."
    )
    vendor_name: ReadValue = Field(description="Supplier (seller) legal name.")
    vendor_gstin: ReadValue = Field(description="Supplier GSTIN (15 characters).")
    vendor_address: ReadValue
    buyer_name: ReadValue = Field(description="Recipient / bill-to name.")
    buyer_gstin: ReadValue = Field(description="Recipient / bill-to GSTIN.")
    invoice_number: ReadValue = Field(
        description="The document's own number (invoice / credit note / quotation number). "
        "Not the PO number."
    )
    invoice_date: ReadValue = Field(description="Document date as printed.")
    due_date: ReadValue
    po_number: ReadValue = Field(description="Buyer's purchase order number, if printed.")
    place_of_supply: ReadValue = Field(description="Place of supply (state name and/or code).")
    service_period: ReadValue
    original_invoice_ref: ReadValue = Field(
        description="For credit/debit notes: the original invoice referred to."
    )
    bank_name: ReadValue
    bank_account_number: ReadValue
    bank_ifsc: ReadValue
    subtotal: ReadValue = Field(description="Total taxable value before GST.")
    tax_rows: list[ReadTaxRow] = Field(
        description="Every GST row in the totals block, one per head and rate, as printed "
        "(e.g. 'CGST @ 9%' and 'SGST @ 9%', or 'IGST @ 12%' and 'IGST @ 18%'). Do not add them up."
    )
    total: ReadValue = Field(description="Grand total payable (or credited), as printed.")
    lines: list[ReadLine]


# --- contract terms (RAG, Phase 5) ---------------------------------------------------------------


class ContractRateRead(BaseModel):
    """One agreed rate, copied from a contract excerpt."""

    item: str = Field(description="The item or service name as written in the contract.")
    hsn_sac: str | None = Field(default=None, description="HSN/SAC code as written, if any.")
    uom: str | None = Field(default=None, description="Unit, e.g. NOS, REAM, MONTH.")
    rate: str = Field(description='The agreed rate exactly as written, e.g. "₹1,100".')
    valid_from: str | None = Field(
        default=None, description="Start of validity as written (YYYY-MM-DD), if stated."
    )
    valid_to: str | None = Field(
        default=None, description="End of validity as written (YYYY-MM-DD), if stated."
    )
    section: str = Field(
        description="The id of the excerpt this rate is in, copied exactly, e.g. "
        '"contracts/technova §2.1".'
    )


class ContractTermsRead(BaseModel):
    """What the contract excerpts say. Leave anything out that the excerpts don't state."""

    rates: list[ContractRateRead] = Field(
        default_factory=list,
        description="Every agreed numeric rate in the excerpts (skip rates like 'as quoted on "
        "the PO'). Superseded and revised rates are separate entries with their own validity.",
    )
    rate_revision_section: str | None = Field(
        default=None,
        description="Id of the excerpt that says how or whether rates may be revised.",
    )
    no_po_section: str | None = Field(
        default=None,
        description="Id of the excerpt that says no purchase order is raised, if any.",
    )
    payment_terms_days: int | None = Field(
        default=None, description="Days from invoice to payment due, if stated."
    )
    payment_terms_section: str | None = Field(
        default=None, description="Id of the excerpt with the payment terms."
    )


# --- RAG evaluation (scripts/eval_rag.py) ------------------------------------------------------


class GroundedAnswer(BaseModel):
    answer: str = Field(description="Short answer using only the excerpts.")
    citations: list[str] = Field(
        default_factory=list, description="Ids of the excerpts the answer relies on, exactly."
    )


class AnswerJudgement(BaseModel):
    facts_covered: list[bool] = Field(
        description="For each expected fact, in order: does the answer state it correctly?"
    )
    contradicts_facts: bool = Field(
        description="True if the answer states something that contradicts an expected fact."
    )
    reason: str = Field(description="One sentence.")
