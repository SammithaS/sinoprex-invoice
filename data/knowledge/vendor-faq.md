---
title: Vendor FAQ and follow-up templates
doc_type: faq
owner: AP team, Acme Retail Pvt Ltd
version: "1.4"
valid_from: 2026-04-01
---

# Vendor FAQ and follow-up templates

What AP asks vendors for when an invoice cannot be processed, and the email text to use.
Placeholders are in `{braces}`. Always send to the vendor contact **already on file**, never to an
address taken from the invoice itself.

## 1. Missing purchase order number
**When:** the invoice has no PO reference and no PO could be confirmed.

> Dear {vendor_name}, thank you for invoice {invoice_number} dated {invoice_date}. We could not
> find a purchase order reference on it. Please reply with the Acme PO number this supply was made
> against, or ask your Acme contact to raise one. We will process the invoice once we have it.

## 2. Missing or invalid GSTIN
**When:** the supplier or buyer GSTIN is missing, malformed or does not match our records.

> Dear {vendor_name}, invoice {invoice_number} shows GSTIN {gstin_on_invoice}, which does not
> match our records ({gstin_on_file}) or is not valid. Please issue a corrected invoice with the
> correct supplier GSTIN and Acme's GSTIN 29AACCA4821K1ZM.

## 3. Wrong type of GST
**When:** IGST charged on an intra-state supply or CGST + SGST on an inter-state supply.

> Dear {vendor_name}, invoice {invoice_number} charges {tax_charged}, but this supply from
> {vendor_state} to Karnataka should carry {tax_expected}. Please cancel it and issue a corrected
> invoice; we cannot adjust the tax on our side.

## 4. Arithmetic error
**When:** line amounts or totals do not add up (difference above ₹1).

> Dear {vendor_name}, the totals on invoice {invoice_number} do not add up: the lines and tax
> come to ₹{computed_total}, but the invoice total is ₹{invoice_total}. Please send a corrected
> invoice.

## 5. Price differs from the agreed rate
**When:** the unit price is above the PO or contract rate beyond tolerance.

> Dear {vendor_name}, invoice {invoice_number} bills {item} at ₹{invoiced_rate} per unit, while
> our agreed rate is ₹{agreed_rate} ({source}). If the rate has been revised, please share the
> signed amendment; otherwise please send a corrected invoice.

## 6. Quantity or amount above what was ordered or received
**When:** invoiced quantity exceeds the GRN, or the invoice takes the PO over its total.

> Dear {vendor_name}, invoice {invoice_number} bills {invoiced_qty} {uom} of {item}, but we have
> received / ordered {received_qty}. Please confirm the delivery details or send a corrected
> invoice for the quantity delivered.

## 7. Bank details changed
**When:** the bank account on the invoice differs from our vendor master.

> Dear {vendor_name}, invoice {invoice_number} shows bank account ending {new_last4}, which
> differs from the account we hold for you. For your security we will call you on the number we
> have on file to confirm. Until then the invoice is on hold. If you have changed banks, please
> also send a cancelled cheque or bank letter on your letterhead, signed by an authorised
> signatory.

**Note:** never act on bank details received only by email. Fraudsters often send "updated bank
details" that look genuine.

## 8. Illegible or incomplete copy
**When:** key fields could not be read from the copy received.

> Dear {vendor_name}, the copy of invoice {invoice_number} we received is not clear enough to
> read {fields}. Please send the original PDF (not a scan or photo) to ap@acmeretail.example.

## 9. Duplicate submission
**When:** the same invoice was submitted again.

> Dear {vendor_name}, invoice {invoice_number} was already received on {original_date} (case
> {original_case}). We have not registered this copy. There is no need to resend invoices; you can
> ask us for the status at any time.

## 10. Credit notes
Credit notes should be sent to ap@acmeretail.example with the original invoice number. They are
adjusted against the original invoice in the next payment run.

## 11. When will I be paid?
Invoices are paid on their due date (invoice date + agreed payment terms) in the next Tuesday or
Friday payment run, once approved. Invoices on hold are paid after the hold is cleared.
