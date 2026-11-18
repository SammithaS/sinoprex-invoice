---
title: GST rules for accounts payable
doc_type: gst
owner: Tax team, Acme Retail Pvt Ltd
version: "2.0"
valid_from: 2026-04-01
---

# GST rules for accounts payable

A working summary of the Goods and Services Tax (GST) rules the AP team applies to vendor
invoices. It is guidance for checks, not legal advice; the CGST Act and Rules prevail.

## 1. GSTIN

### 1.1 Structure
A GSTIN has 15 characters:
- characters 1–2: state code (e.g. 29 Karnataka, 27 Maharashtra, 33 Tamil Nadu, 07 Delhi,
  24 Gujarat, 32 Kerala, 36 Telangana);
- characters 3–12: the holder's PAN;
- character 13: entity number for the same PAN in that state (1–9, then A–Z);
- character 14: the letter Z by default;
- character 15: a check character computed from the first 14 characters (mod-36 checksum).

### 1.2 Checks on every invoice
1. The supplier GSTIN has the right format and check character.
2. The supplier GSTIN matches the vendor master record.
3. The state code of the supplier GSTIN matches the supplier address state.
4. The buyer GSTIN is Acme's: **29AACCA4821K1ZM** (Karnataka).

An invalid supplier GSTIN means input tax credit cannot be claimed; the invoice is a
`TAX_MISMATCH` exception for the tax team.

## 2. Type of tax: CGST + SGST or IGST

### 2.1 Place of supply
For goods, the place of supply is where the goods are delivered; for most services it is the
location of the recipient. All Acme purchases are delivered to or received in Karnataka, so the
place of supply is **Karnataka (29)** unless the invoice says otherwise.

### 2.2 Intra-state supply
Supplier in Karnataka (GSTIN starts with 29) → **CGST + SGST**, each at half the GST rate
(e.g. 18% = 9% CGST + 9% SGST). Charging IGST on an intra-state supply is an error.

### 2.3 Inter-state supply
Supplier in another state → **IGST** at the full rate (e.g. 18% IGST). Charging CGST + SGST on an
inter-state supply is an error.

### 2.4 Consequence of the wrong tax type
Tax paid under the wrong head cannot be claimed as input credit by Acme. The vendor must cancel
the invoice and issue a corrected one; AP must not "adjust" the tax itself.

## 3. Mandatory fields on a tax invoice (Rule 46, CGST Rules)
A valid tax invoice must show:
1. supplier name, address and GSTIN;
2. a consecutive invoice number, unique for the financial year (up to 16 characters);
3. invoice date;
4. recipient name, address and GSTIN (for registered recipients);
5. HSN code for goods or SAC for services;
6. description, quantity and unit of each item;
7. taxable value of each item;
8. rate and amount of tax (CGST, SGST/UTGST, IGST shown separately);
9. place of supply with state name (for inter-state supplies);
10. total invoice value, in figures and words;
11. signature or digital signature of the supplier.

An invoice missing items 1–3, 5, 7, 8 or 10 cannot be processed.

## 4. GST rates used by Acme's main purchases

| Item / service | HSN / SAC | GST rate |
|---|---|---|
| Computer accessories, docking stations, keyboards | 8471 / 8473 | 18% |
| Cables | 8544 | 18% |
| Monitors | 8528 | 18% |
| Copier paper | 4802 | 12% |
| Thermal paper rolls | 4811 | 18% |
| Pens and markers | 9608 | 18% |
| Printed matter (flyers, catalogues, standees) | 4911 | 18% |
| Corrugated boxes | 4819 | 18% |
| Packing tape | 3919 | 18% |
| LED lights | 9405 | 18% |
| Generators | 8502 | 18% |
| Office furniture | 9401 / 9403 | 18% |
| Goods transport by road (GTA, forward charge) | 9965 | 12% |
| Housekeeping / cleaning services | 998533 | 18% |
| Electrical installation services | 995461 | 18% |
| Software subscription (SaaS) | 997331 | 18% |

A rate on the invoice that differs from this table is a `TAX_MISMATCH` exception unless the tax
team has recorded a reason.

## 5. Rounding
Tax is computed per line on the taxable value and rounded to the paisa. A difference of up to
**₹1** between the sum of lines and the invoice total is accepted as rounding. The invoice total
may be rounded to the nearest rupee if the rounding is shown as a separate line.

## 6. Credit notes and debit notes
A credit note reduces an earlier invoice and must reference the original invoice number and
date. It is not an invoice and is never paid; AP adjusts it against the original invoice.

## 7. E-invoicing
Suppliers with aggregate turnover above ₹5 crore must issue e-invoices with an IRN and QR code.
For this project's demo vendors, the IRN is optional; a missing IRN is noted but is not an
exception.
