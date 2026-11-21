---
title: Accounts Payable Policy
doc_type: policy
owner: Finance Controller, Acme Retail Pvt Ltd
version: "3.2"
valid_from: 2026-04-01
---

# Accounts Payable Policy — Acme Retail Pvt Ltd

This policy governs how vendor invoices are received, checked, approved, posted and paid by
Acme Retail Pvt Ltd ("Acme"), Bengaluru (GSTIN 29AACCA4821K1ZM). It applies to every invoice,
whether it arrives by email, upload or post. All amounts are in Indian Rupees (INR).

## 1. Scope and principles

### 1.1 What counts as an invoice
Only a **tax invoice** issued by a registered supplier can be processed for payment. Quotations,
proforma invoices, delivery challans, statements of account and purchase orders are not invoices
and must be returned to the sender. **Credit notes** are processed separately by the AP team
against the original invoice and are not paid.

### 1.2 Separation of duties
The person who raises a purchase order may not approve the invoice against it. No single person
may both change vendor master data and approve a payment to that vendor.

### 1.3 Automation
Invoices may be processed without human touch ("straight-through") only when every check in
section 9 passes. Automated systems may **propose** coding, approvers and vendor follow-ups but
may never create vendors, change bank details or release payments.

## 2. Receipt and registration

### 2.1 Single point of entry
All invoices must be sent to `ap@acmeretail.example` or uploaded to SinoprexInvoice. Invoices sent to
individual employees must be forwarded to AP the same day.

### 2.2 Registration
Every invoice is registered as a case on receipt, with the original file kept unchanged. The
receipt date starts the cycle-time clock.

### 2.3 Legibility
An invoice whose key fields (supplier GSTIN, invoice number, invoice date, taxable value, tax
amounts, total) cannot be read with confidence must be checked by an AP clerk against the image
before it proceeds. If still unreadable, request a clear copy from the vendor.

## 3. Matching and tolerances

### 3.1 Purchase-order backed invoices
Goods and services above ₹10,000 must be backed by an approved purchase order (PO). The PO
number should appear on the invoice. If it is missing, AP may identify the PO from the vendor,
items and amount, but a person must confirm the match before posting.

### 3.2 Price tolerance
The invoiced unit price may exceed the PO unit price (or the contracted rate, where a contract
applies) by **at most 2%** per line, and the total price variance on an invoice may not exceed
**₹5,000**. Anything above either limit is a `PRICE_VARIANCE` exception and goes to the buyer
named on the PO. Where a vendor contract and the PO disagree, the **contracted rate prevails**
(see section 3.6).

### 3.3 Quantity tolerance
The invoiced quantity may not exceed the quantity received on the goods receipt note (GRN).
There is **no tolerance** on quantity for goods. Excess quantity is a `QTY_VARIANCE` exception
and goes to the receiving store.

### 3.4 Three-way match
POs marked "GRN required" (all goods) need a GRN before the invoice can be paid. If no GRN
exists, the invoice is put **on hold** (`NO_RECEIPT`) and the receiver is asked to confirm
delivery. Service POs marked "no GRN required" (e.g. housekeeping) use a two-way match against
the PO; the service owner confirms the service by approving.

### 3.5 PO balance and partial invoicing
A PO may be invoiced in several parts. The total of all invoices against a PO may not exceed the
PO total. An invoice that takes the PO over its total is an `OVER_PO_BALANCE` exception and goes
to the buyer, who either amends the PO or rejects the excess.

### 3.6 Source hierarchy
When sources disagree, use this order: (1) a signed vendor contract or amendment valid on the
invoice date, (2) the purchase order, (3) the GRN for quantities, (4) the vendor's invoice. A
contracted rate is only used if the contract text containing it can be cited.

### 3.7 Freight and other charges
Freight, packing or handling charges not on the PO are allowed up to **₹1,000** per invoice when
the contract permits them; anything else is a price variance.

## 4. Duplicate invoices

### 4.1 Exact duplicates
An invoice with the same vendor and the same invoice number as one already registered, or a file
identical to one already received, is an **exact duplicate** and is **rejected**. The vendor is
told the original case reference.

### 4.2 Suspected duplicates
An invoice from the same vendor with the **same total amount within 30 days** of another invoice,
but a different invoice number, is a **suspected duplicate** (`DUPLICATE_SUSPECT`). An AP clerk
must compare the two documents before it proceeds.

## 5. Tax checks

### 5.1 GST validation
The supplier GSTIN must be valid (format and check digit) and must match the vendor master. The
buyer GSTIN must be Acme's: 29AACCA4821K1ZM. See the GST rules note for details.

### 5.2 Type of tax
Supplies from Karnataka suppliers to Acme (Karnataka) attract CGST + SGST; supplies from other
states attract IGST. The wrong type of tax is a `TAX_MISMATCH` exception for the tax team; the
vendor must issue a corrected invoice, because Acme cannot claim input credit on wrongly charged
tax.

### 5.3 Arithmetic
Line amounts must equal quantity × unit price, and taxable value + tax must equal the invoice
total, allowing **₹1** for rounding. An invoice whose arithmetic is wrong by more than ₹1 is
**rejected** and the vendor is asked for a corrected invoice.

## 6. Vendor master and bank details

### 6.1 Approved vendors only
Invoices are processed only for vendors in the vendor master with status ACTIVE. An invoice from
a **BLOCKED** vendor is **rejected**. An invoice from a supplier not in the vendor master is put
**on hold** until the vendor is onboarded by the Vendor Master team; AP must not create vendors.

### 6.2 Bank detail changes
If the bank account on an invoice differs from the vendor master, the invoice is put **on hold**
(`BANK_CHANGE`). The Vendor Master team must **verify the change with the vendor using the contact
details already on file** — never the contact details on the invoice or in the email that
carried it. The change and the release of the invoice need **two approvers**: the AP Manager and
the Finance Controller.

### 6.3 Fraud indicators
Urgent payment requests, changed bank details, new email domains, or pressure to bypass the
process must be reported to the Finance Controller.

## 7. Approval matrix

### 7.1 PO-backed invoices
| Invoice total (incl. GST) | Approval needed after a clean match |
|---|---|
| Up to ₹5,00,000 | None — may post straight-through |
| Above ₹5,00,000 up to ₹25,00,000 | Finance Manager |
| Above ₹25,00,000 | Finance Manager **and** CFO |

### 7.2 Non-PO invoices
Invoices without a PO (permitted only for recurring subscriptions, utilities, rent and
professional fees) always need approval by the **Department Head** of the cost centre charged.
Non-PO invoices above ₹1,00,000 also need the Finance Manager.

### 7.3 Exceptions and overrides
Approving an invoice despite a tolerance, duplicate or tax exception (an **override**) needs a
written reason and a **second approver** (four-eyes): the exception owner plus the AP Manager.
Approving above one's own matrix limit is not allowed.

### 7.4 Approvers
| Role | Name |
|---|---|
| AP Clerk | Priya Nair |
| AP Manager | Arjun Desai |
| Finance Manager | Meera Krishnan |
| Finance Controller | Sanjay Batra |
| CFO | Kavita Reddy |
| Tax team lead | Farhan Qureshi |
| Vendor Master team | Lakshmi Pillai |
| Department Head — IT (CC-IT-01) | Rahul Menon |
| Department Head — Admin (CC-ADM-02) | Anita Rao |
| Department Head — Operations (CC-OPS-03) | Vikram Iyer |
| Department Head — Marketing (CC-MKT-04) | Sneha Kulkarni |
| Department Head — Facilities (CC-FAC-05) | Imran Sheikh |

## 8. Exception handling

### 8.1 Exception owners
| Exception | Owner |
|---|---|
| `PRICE_VARIANCE` | Buyer named on the PO |
| `QTY_VARIANCE` | Receiving store |
| `NO_PO` | AP Clerk (confirms inferred PO) / Department Head (non-PO) |
| `NO_RECEIPT` | Receiver of the goods (store or department) |
| `DUPLICATE_SUSPECT` | AP Clerk |
| `BANK_CHANGE` | Vendor Master team |
| `TAX_MISMATCH` | Tax team |
| `LOW_CONFIDENCE` | AP Clerk |
| `OVER_PO_BALANCE` | Buyer named on the PO |

### 8.2 Escalation
An exception not resolved within **2 working days** is escalated to the AP Manager; after
**5 working days** to the Finance Manager.

### 8.3 Specific questions
Every exception handed to an owner must state the exact variance, the policy clause and one
specific question the owner can answer (for example, "Has the agreed rate changed? If yes,
attach the amendment.").

## 9. Straight-through processing
An invoice may be approved and posted without human touch only if **all** of these hold:
1. all key fields were read with confidence of at least 0.8;
2. supplier and buyer GSTINs are valid and the vendor is ACTIVE with unchanged bank details;
3. the PO match (and GRN match, where required) passed within tolerance;
4. no duplicate or tax exception was raised;
5. no approval is required by the matrix in section 7; and
6. the posting to the ERP was confirmed by reading it back.

## 10. Posting and payment

### 10.1 ERP posting
Approved invoices are posted to the ERP with the normalised invoice data, GL coding, cost centre,
attachment, match evidence and approval history. The posting is read back and compared with the
approved values; any difference (amount, document number, status, attachment, coding) is an
exception and the case stays open.

### 10.2 Payment runs
Payment runs are on **Tuesdays and Fridays**. Invoices are paid on the due date per the vendor's
payment terms, never earlier unless an early-payment discount is agreed in the contract.

### 10.3 Records
The case evidence (original file, extracted values, checks, approvals, ERP result) is retained
for **8 years** as required under the CGST Act.
