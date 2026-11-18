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
