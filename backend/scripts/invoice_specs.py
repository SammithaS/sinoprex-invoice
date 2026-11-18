"""Test-invoice specifications: what each generated PDF contains and what the pipeline should do.

Every spec becomes one PDF in data/test-invoices/ plus a ground-truth JSON file. Amounts are
computed from the lines (see `compute`), so the ground truth always matches the printed values
— except where a spec deliberately prints a wrong total (`printed_total`).
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.rules.gst import build_gstin

ACME_STATE = "29"

Layout = Literal["classic", "band", "ledger", "service"]
DocType = Literal["invoice", "quote", "credit_note"]
Scan = Literal["good", "medium", "poor"]
Decision = Literal["APPROVED", "NEEDS_REVIEW", "ON_HOLD", "REJECTED"]


@dataclass(frozen=True)
class VendorProfile:
    """What a vendor prints on its documents (master data lives in data/seed/vendors.json)."""

    vendor_id: str | None  # None = not in Acme's vendor master
    legal_name: str
    gstin: str
    address: tuple[str, ...]
    phone: str
    email: str
    bank_name: str
    bank_account: str
    bank_ifsc: str
    layout: Layout
    accent: str  # hex colour used by the "band" and "classic" layouts


def _gstin(state: str, pan: str) -> str:
    return build_gstin(state, pan)


VENDORS: dict[str, VendorProfile] = {
    "technova": VendorProfile(
        "V001",
        "TechNova Solutions Pvt Ltd",
        _gstin("29", "AABCT1332L"),
        ("No. 18, 2nd Cross, Koramangala 5th Block", "Bengaluru, Karnataka 560095"),
        "+91 80 4120 7788",
        "accounts@technova.example",
        "HDFC Bank, Koramangala",
        "50200031284417",
        "HDFC0000123",
        "classic",
        "#1E3A8A",
    ),
    "sharma": VendorProfile(
        "V002",
        "Sharma Office Supplies",
        _gstin("27", "AKZPS6612M"),
        ("Shop 4, Kalbadevi Road", "Mumbai, Maharashtra 400002"),
        "+91 22 2205 1934",
        "accounts@sharma.example",
        "ICICI Bank, Kalbadevi",
        "104505000921",
        "ICIC0001045",
        "ledger",
        "#000000",
    ),
    "blueleaf": VendorProfile(
        "V003",
        "BlueLeaf Logistics Pvt Ltd",
        _gstin("33", "AAECB7720Q"),
        ("Plot 22, SIDCO Industrial Estate, Ambattur", "Chennai, Tamil Nadu 600098"),
        "+91 44 2625 3305",
        "accounts@blueleaf.example",
        "State Bank of India, Ambattur",
        "38211903305",
        "SBIN0004821",
        "service",
        "#0E7490",
    ),
    "kaveri": VendorProfile(
        "V004",
        "Kaveri Print House",
        _gstin("29", "BNKPK3389D"),
        ("12/3, Industrial Suburb, Rajajinagar", "Bengaluru, Karnataka 560010"),
        "+91 80 2335 7781",
        "accounts@kaveri.example",
        "Canara Bank, Rajajinagar",
        "2210101007781",
        "CNRB0002210",
        "band",
        "#7C3AED",
    ),
    "cloudnest": VendorProfile(
        "V005",
        "CloudNest Software Pvt Ltd",
        _gstin("29", "AAICC9054H"),
        ("WeWork Galaxy, 43 Residency Road", "Bengaluru, Karnataka 560025"),
        "+91 80 6911 1196",
        "billing@cloudnest.example",
        "Kotak Mahindra Bank, Residency Road",
        "8011241196",
        "KKBK0008011",
        "band",
        "#0284C7",
    ),
    "deccan": VendorProfile(
        "V006",
        "Deccan Facility Services LLP",
        _gstin("36", "AAQFD2240R"),
        ("8-2-293, Road No. 14, Banjara Hills", "Hyderabad, Telangana 500034"),
        "+91 40 2354 5502",
        "accounts@deccan.example",
        "Axis Bank, Banjara Hills",
        "917020045615502",
        "UTIB0000561",
        "service",
        "#B45309",
    ),
    "rangoli": VendorProfile(
        "V007",
        "Rangoli Packaging Industries",
        _gstin("24", "AAPFR8813C"),
        ("Survey 114, GIDC Makarpura", "Vadodara, Gujarat 390010"),
        "+91 265 264 6634",
        "accounts@rangoli.example",
        "Bank of Baroda, Makarpura",
        "27150200006634",
        "BARB0VADODA",
        "band",
        "#DB2777",
    ),
    "northwind": VendorProfile(
        "V008",
        "Northwind Electricals Pvt Ltd",
        _gstin("07", "AADCN4471G"),
        ("B-41, Okhla Industrial Area Phase II", "New Delhi, Delhi 110020"),
        "+91 11 4105 2048",
        "accounts@northwind.example",
        "Punjab National Bank, Okhla",
        "1120002100002048",
        "PUNB0011200",
        "classic",
        "#B91C1C",
    ),
    "malabar": VendorProfile(
        "V009",
        "Malabar Furniture Works",
        _gstin("32", "ABMFM5567P"),
        ("NH 66 Bypass, Edappally", "Kochi, Kerala 682024"),
        "+91 484 280 8890",
        "accounts@malabar.example",
        "Federal Bank, Edappally",
        "11020100088890",
        "FDRL0001102",
        "classic",
        "#166534",
    ),
    "zenith": VendorProfile(
        "V010",
        "Zenith Traders",
        _gstin("29", "AGHPZ1290B"),
        ("45, Avenue Road", "Bengaluru, Karnataka 560002"),
        "+91 80 2287 3141",
        "accounts@zenith.example",
        "Indian Bank, Avenue Road",
        "6012003141",
        "IDIB000B123",
        "ledger",
        "#000000",
    ),
    "sunrise": VendorProfile(
        None,
        "Sunrise Electronics",
        _gstin("29", "ABCFS4410K"),
        ("221, SP Road", "Bengaluru, Karnataka 560002"),
        "+91 80 2221 0077",
        "sales@sunrise-elec.example",
        "Union Bank of India, SP Road",
        "520101004410077",
        "UBIN0552101",
        "classic",
        "#334155",
    ),
}

# Rangoli's "new" bank account — the fraud-signal edge case.
RANGOLI_CHANGED_BANK = ("Yes Bank, Alkapuri", "045661209917", "YESB0000456")


@dataclass(frozen=True)
class Line:
    description: str
    hsn_sac: str
    qty: str
    uom: str
    unit_price: str
    tax_rate: str


@dataclass(frozen=True)
class Expected:
    decision: Decision
    exceptions: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()
    final_decision: Decision | None = None  # after the humans in the loop act as intended
    reasons: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()
    matched_po: str | None = None
    matched_grn: str | None = None
    proposed_coding: dict[str, str] | None = None


@dataclass(frozen=True)
class InvoiceSpec:
    key: str
    title: str
    edge_case: str  # plan §8 reference, e.g. "#3" or "clean"
    vendor: str  # key into VENDORS
    number: str
    date: str
    lines: tuple[Line, ...]
    expected: Expected
    po_number: str | None = None  # as printed (None = not printed)
    due_days: int | None = None
    doc_type: DocType = "invoice"
    tax_mode: Literal["auto", "igst", "cgst_sgst"] = "auto"
    bank: tuple[str, str, str] | None = None  # override (bank name, account, IFSC)
    printed_total: str | None = None  # deliberately wrong total
    scan: Scan | None = None
    notes: tuple[str, ...] = ()  # printed remarks
    references: str | None = None  # credit note → original invoice
    depends_on: tuple[str, ...] = ()
    erp_fault: str | None = None
    service_period: str | None = None
    demo_notes: str = ""


@dataclass
class Computed:
    """Line and header amounts, as printed on the document."""

    intra_state: bool
    lines: list[dict[str, str]] = field(default_factory=list)
    subtotal: Decimal = Decimal(0)
    cgst: Decimal = Decimal(0)
    sgst: Decimal = Decimal(0)
    igst: Decimal = Decimal(0)

    @property
    def tax_total(self) -> Decimal:
        return self.cgst + self.sgst + self.igst

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.tax_total


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute(spec: InvoiceSpec) -> Computed:
    vendor = VENDORS[spec.vendor]
    if spec.tax_mode == "auto":
        intra = vendor.gstin[:2] == ACME_STATE
    else:
        intra = spec.tax_mode == "cgst_sgst"
    out = Computed(intra_state=intra)
    for i, ln in enumerate(spec.lines, start=1):
        amount = money(Decimal(ln.qty) * Decimal(ln.unit_price))
        rate = Decimal(ln.tax_rate)
        if intra:
            half = money(amount * rate / 200)
            out.cgst += half
            out.sgst += half
        else:
            out.igst += money(amount * rate / 100)
        out.subtotal += amount
        out.lines.append(
            {
                "line_no": str(i),
                "description": ln.description,
                "hsn_sac": ln.hsn_sac,
                "qty": ln.qty,
                "uom": ln.uom,
                "unit_price": str(money(Decimal(ln.unit_price))),
                "tax_rate": ln.tax_rate,
                "amount": str(amount),
            }
        )
    return out


# --- line shorthands (descriptions match the seeded POs) -------------------------------------

DOCK = ("Laptop docking station", "8473", "NOS", "18")
KBD = ("Wireless keyboard and mouse set", "8471", "NOS", "18")
PAPER = ("A4 copier paper 75gsm (ream)", "4802", "REAM", "12")
PENS = ("Ball pen, blue (box of 50)", "9608", "BOX", "18")
ROLLS = ("Thermal receipt rolls 79mm", "4811", "ROLL", "18")
MARKERS = ("Whiteboard marker set", "9608", "SET", "18")
LED = ("LED panel light 2x2 ft, 36W", "9405", "NOS", "18")
LABOUR = ("Electrical installation labour", "995461", "LOT", "18")
DG = ("Diesel generator 125 kVA with AMF panel", "8502", "NOS", "18")


def ln(item: tuple[str, str, str, str], qty: str, price: str) -> Line:
    desc, hsn, uom, rate = item
    return Line(desc, hsn, qty, uom, price, rate)


NW_LINES = (ln(LED, "150", "1650"), ln(LABOUR, "1", "45000"))

SPECS: tuple[InvoiceSpec, ...] = (
    # --- clean, touchless ----------------------------------------------------------------------
    InvoiceSpec(
        key="northwind-clean",
        title="Clean 3-way match (inter-state, IGST)",
        edge_case="clean",
        vendor="northwind",
        number="NWE/DL/2026/0763",
        date="2026-11-06",
        po_number="PO-2026-0041",
        due_days=30,
        lines=NW_LINES,
        expected=Expected(
            "APPROVED",
            matched_po="PO-2026-0041",
            matched_grn="GRN-0310",
            reasons=("PO and GRN match exactly; IGST correct for Delhi → Karnataka.",),
        ),
    ),
    InvoiceSpec(
        key="technova-keyboards",
        title="Clean 3-way match (intra-state, CGST + SGST)",
        edge_case="clean",
        vendor="technova",
        number="TN/2026/1038",
        date="2026-11-11",
        po_number="PO-2026-0045",
        due_days=30,
        lines=(ln(KBD, "50", "1450"),),
        expected=Expected(
            "APPROVED",
            matched_po="PO-2026-0045",
            matched_grn="GRN-0315",
            citations=("contracts/technova §2.4",),
        ),
    ),
    InvoiceSpec(
        key="kaveri-po-remainder",
        title="Partial invoice that completes a PO",
        edge_case="clean",
        vendor="kaveri",
        number="KPH/2026/131",
        date="2026-10-20",
        po_number="PO-2026-0033",
        due_days=15,
        lines=(
            Line("Festive flyers A5, 4-colour", "4911", "10000", "NOS", "1.10", "18"),
            Line("Standee 6x3 ft with stand", "4911", "25", "NOS", "1850", "18"),
        ),
        expected=Expected(
            "APPROVED",
            matched_po="PO-2026-0033",
            matched_grn="GRN-0299",
            reasons=("Invoice equals the PO's remaining balance (₹67,555) after KPH/2026/118.",),
        ),
    ),
    InvoiceSpec(
        key="sharma-within-tolerance",
        title="Price 1.2% above PO — inside the 2% tolerance",
        edge_case="tolerance",
        vendor="sharma",
        number="SOS-2026-0529",
        date="2026-11-15",
        po_number="PO-2026-0049",
        due_days=15,
        lines=(ln(ROLLS, "1000", "42.50"),),
        expected=Expected(
            "APPROVED",
            matched_po="PO-2026-0049",
            matched_grn="GRN-0319",
            reasons=(
                "Unit price ₹42.50 vs PO ₹42.00 = +1.19%, total +₹500: within ap-policy §3.2.",
            ),
            citations=("ap-policy §3.2",),
        ),
    ),
    # --- edge case 1: scanned ------------------------------------------------------------------
    InvoiceSpec(
        key="deccan-scanned-poor",
        title="Scanned, low-quality invoice",
        edge_case="#1",
        vendor="deccan",
        number="DFS/HYD/2026/1010",
        date="2026-11-03",
        po_number="PO-2026-0036",
        due_days=30,
        service_period="1–31 October 2026",
        lines=(
            Line("Housekeeping services, Oct 2026 (HQ)", "998533", "1", "MONTH", "185000", "18"),
        ),
        scan="poor",
        expected=Expected(
            "NEEDS_REVIEW",
            exceptions=("LOW_CONFIDENCE",),
            final_decision="APPROVED",
            matched_po="PO-2026-0036",
            reasons=(
                "Scanned image: key fields below 0.8 confidence → AP clerk checks the image.",
                "Service PO, no GRN required (2-way match).",
            ),
            citations=("ap-policy §2.3",),
        ),
    ),
    InvoiceSpec(
        key="blueleaf-scanned-good",
        title="Scanned invoice, good quality",
        edge_case="#1 (control)",
        vendor="blueleaf",
        number="BLL/TN/26-27/0431",
        date="2026-11-15",
        po_number="PO-2026-0047",
        due_days=30,
        service_period="1–14 November 2026",
        lines=(Line("Last-mile delivery, Bengaluru stores", "9965", "120", "DROP", "450", "12"),),
        scan="good",
        expected=Expected(
            "APPROVED",
            matched_po="PO-2026-0047",
            matched_grn="GRN-0317",
            reasons=("Clean scan; fields should extract with high confidence.",),
        ),
    ),
    InvoiceSpec(
        key="kaveri-scanned-medium",
        title="Scanned invoice, medium quality (skewed)",
        edge_case="#1 (control)",
        vendor="kaveri",
        number="KPH/2026/137",
        date="2026-11-13",
        po_number="PO-2026-0048",
        due_days=15,
        lines=(Line("Shelf talkers, laminated", "4911", "5000", "NOS", "4.50", "18"),),
        scan="medium",
        expected=Expected(
            "APPROVED",
            matched_po="PO-2026-0048",
            matched_grn="GRN-0318",
            reasons=("Skewed scan; NEEDS_REVIEW is acceptable if any key field reads below 0.8.",),
        ),
    ),
    # --- edge case 2: split invoicing ----------------------------------------------------------
    InvoiceSpec(
        key="sharma-split-1",
        title="Split invoicing — part 1 of 3",
        edge_case="#2",
        vendor="sharma",
        number="SOS-2026-0518",
        date="2026-10-12",
        po_number="PO-2026-0038",
        due_days=15,
        lines=(ln(PAPER, "100", "240"),),
        expected=Expected(
            "APPROVED",
            matched_po="PO-2026-0038",
            matched_grn="GRN-0305",
            reasons=("₹26,880 of ₹97,160 PO; remaining ₹70,280.",),
            citations=("contracts/sharma §2.2",),
        ),
    ),
    InvoiceSpec(
        key="sharma-split-2",
        title="Split invoicing — part 2 of 3",
        edge_case="#2",
        vendor="sharma",
        number="SOS-2026-0526",
        date="2026-10-26",
        po_number="PO-2026-0038",
        due_days=15,
        lines=(ln(PAPER, "100", "240"), ln(PENS, "20", "350")),
        depends_on=("sharma-split-1",),
        expected=Expected(
            "APPROVED",
            matched_po="PO-2026-0038",
            matched_grn="GRN-0305",
            reasons=("Cumulative ₹62,020 of ₹97,160; remaining ₹35,140.",),
        ),
    ),
    InvoiceSpec(
        key="sharma-split-3",
        title="Split invoicing — part 3 exceeds the PO",
        edge_case="#2",
        vendor="sharma",
        number="SOS-2026-0533",
        date="2026-11-09",
        po_number="PO-2026-0038",
        due_days=15,
        lines=(ln(PAPER, "120", "240"), ln(PENS, "20", "350")),
        depends_on=("sharma-split-1", "sharma-split-2"),
        expected=Expected(
            "NEEDS_REVIEW",
            exceptions=("OVER_PO_BALANCE", "QTY_VARIANCE"),
            matched_po="PO-2026-0038",
            matched_grn="GRN-0305",
            reasons=(
                "Invoice ₹40,516 exceeds the PO remaining balance ₹35,140 by ₹5,376.",
                "Paper billed 320 reams in total vs 300 ordered and received.",
            ),
            citations=("ap-policy §3.5", "ap-policy §3.3"),
        ),
    ),
    # --- edge case 3: price above contract -----------------------------------------------------
    InvoiceSpec(
        key="technova-contract-rate",
        title="Unit price above the contracted rate",
        edge_case="#3",
        vendor="technova",
        number="TN/2026/1042",
        date="2026-11-10",
        po_number="PO-2026-0042",
        due_days=30,
        lines=(ln(DOCK, "100", "1250"),),
        notes=("Prices revised as per our letter dated 01-Oct-2026.",),
        expected=Expected(
            "NEEDS_REVIEW",
            exceptions=("PRICE_VARIANCE",),
            matched_po="PO-2026-0042",
            matched_grn="GRN-0311",
            reasons=(
                "Docking station billed at ₹1,250 vs contracted ₹1,100 (+13.6%, ₹15,000 over).",
                "No signed amendment; contract rates are firm for the term.",
            ),
            citations=("contracts/technova §2.1", "contracts/technova §2.5", "ap-policy §3.2"),
        ),
        demo_notes="Question for the buyer: has TechNova's docking-station rate changed from "
        "₹1,100 to ₹1,250? If yes, attach the amendment.",
    ),
    # --- edge case 4: duplicates ---------------------------------------------------------------
    InvoiceSpec(
        key="northwind-resubmitted",
        title="Exact duplicate — same invoice number resubmitted",
        edge_case="#4",
        vendor="northwind",
        number="NWE/DL/2026/0763",
        date="2026-11-06",
        po_number="PO-2026-0041",
        due_days=30,
        lines=NW_LINES,
        notes=("REMINDER: payment for this invoice is pending. Kindly release at the earliest.",),
        depends_on=("northwind-clean",),
        expected=Expected(
            "REJECTED",
            reasons=("Same vendor and invoice number as northwind-clean (exact duplicate).",),
            citations=("ap-policy §4.1",),
        ),
    ),
    InvoiceSpec(
        key="northwind-suspect-duplicate",
        title="Likely duplicate — new number, same amount",
        edge_case="#4",
        vendor="northwind",
        number="NWE/DL/2026/0771",
        date="2026-11-12",
        po_number="PO-2026-0041",
        due_days=30,
        lines=NW_LINES,
        depends_on=("northwind-clean",),
        expected=Expected(
            "NEEDS_REVIEW",
            exceptions=("DUPLICATE_SUSPECT",),
            matched_po="PO-2026-0041",
            matched_grn="GRN-0310",
            reasons=(
                "Same vendor and total (₹3,45,150) as NWE/DL/2026/0763, 6 days apart.",
                "PO-2026-0041 is already fully invoiced by NWE/DL/2026/0763.",
            ),
            citations=("ap-policy §4.2",),
        ),
    ),
    # --- edge case 5: missing PO ---------------------------------------------------------------
    InvoiceSpec(
        key="malabar-missing-po",
        title="No PO reference — PO must be inferred",
        edge_case="#5",
        vendor="malabar",
        number="MFW/2026/0218",
        date="2026-11-13",
        due_days=30,
        lines=(Line("Ergonomic office chair", "9401", "40", "NOS", "8900", "18"),),
        expected=Expected(
            "NEEDS_REVIEW",
            exceptions=("NO_PO",),
            final_decision="APPROVED",
            matched_po="PO-2026-0043",
            matched_grn="GRN-0313",
            reasons=("No PO printed; vendor, item, qty and price match PO-2026-0043 line 1.",),
            citations=("ap-policy §3.1",),
        ),
    ),
    # --- edge case 6: bank change --------------------------------------------------------------
    InvoiceSpec(
        key="rangoli-bank-change",
        title="Bank details changed (fraud signal)",
        edge_case="#6",
        vendor="rangoli",
        number="RPI/26-27/0284",
        date="2026-10-27",
        po_number="PO-2026-0040",
        due_days=45,
        lines=(
            Line("Corrugated box 5-ply 18x12x12", "4819", "4000", "NOS", "38", "18"),
            Line("Packing tape 48mm x 65m", "3919", "600", "ROLL", "62", "18"),
        ),
        bank=RANGOLI_CHANGED_BANK,
        notes=("Please note our NEW bank account details below. Kindly update your records.",),
        expected=Expected(
            "ON_HOLD",
            exceptions=("BANK_CHANGE",),
            approvals=("AP Manager", "Finance Controller"),
            matched_po="PO-2026-0040",
            matched_grn="GRN-0308",
            reasons=(
                "Invoice bank account ends 9917; vendor master has ••••6634.",
                "Verify with the vendor via the contact on file before payment.",
            ),
            citations=("ap-policy §6.2", "contracts/rangoli §5.1"),
        ),
    ),
    # --- edge case 7: non-PO -------------------------------------------------------------------
    InvoiceSpec(
        key="cloudnest-saas-nonpo",
        title="Non-PO invoice — monthly SaaS subscription",
        edge_case="#7",
        vendor="cloudnest",
        number="CN-INV-2026-11",
        date="2026-11-01",
        due_days=7,
        service_period="1–30 November 2026",
        lines=(
            Line(
                "CloudNest Retail POS Suite — 60 user licences (Nov 2026)",
                "997331",
                "1",
                "MONTH",
                "42000",
                "18",
            ),
        ),
        expected=Expected(
            "NEEDS_REVIEW",
            approvals=("Department Head — IT",),
            final_decision="APPROVED",
            reasons=(
                "Non-PO branch: recurring subscription permitted without PO.",
                "Proposed GL 6420 / CC-IT-01 — same vendor coded so in 5 reviewed cases.",
            ),
            proposed_coding={"gl_account": "6420", "cost_centre": "CC-IT-01"},
            citations=("ap-policy §7.2", "contracts/cloudnest §2.3"),
        ),
    ),
    # --- edge case 8: ERP read-back mismatch ---------------------------------------------------
    InvoiceSpec(
        key="malabar-erp-mismatch",
        title="ERP read-back mismatch (mock ERP drops a paisa)",
        edge_case="#8",
        vendor="malabar",
        number="MFW/2026/0225",
        date="2026-11-14",
        po_number="PO-2026-0046",
        due_days=30,
        lines=(Line("Meeting table 8-seater", "9403", "3", "NOS", "42000", "18"),),
        erp_fault="amount_minus_one_paisa",
        expected=Expected(
            "APPROVED",
            matched_po="PO-2026-0046",
            matched_grn="GRN-0316",
            reasons=(
                "Rules pass; the mock ERP stores ₹1,48,679.99 ≠ approved ₹1,48,680.00 → "
                "read-back exception, case stays open.",
            ),
            citations=("ap-policy §10.1",),
        ),
    ),
    # --- other checks --------------------------------------------------------------------------
    InvoiceSpec(
        key="northwind-quote",
        title="Not an invoice — a quotation",
        edge_case="other",
        vendor="northwind",
        number="NWE/Q/2026/0145",
        date="2026-11-10",
        doc_type="quote",
        lines=(Line("Online UPS 10 kVA with batteries", "8504", "2", "NOS", "185000", "18"),),
        notes=("Quotation valid for 30 days. Delivery 3 weeks from PO.",),
        expected=Expected(
            "REJECTED",
            reasons=("Document is a quotation, not a tax invoice.",),
            citations=("ap-policy §1.1",),
        ),
    ),
    InvoiceSpec(
        key="sharma-credit-note",
        title="Not an invoice — a credit note",
        edge_case="other",
        vendor="sharma",
        number="SOS-CN-2026-014",
        date="2026-11-05",
        doc_type="credit_note",
        references="SOS-2026-0412 dated 30-08-2026",
        lines=(ln(MARKERS, "5", "180"),),
        notes=("Credit for 5 marker sets damaged in transit.",),
        expected=Expected(
            "REJECTED",
            reasons=("Credit note: adjust against SOS-2026-0412, not paid.",),
            citations=("ap-policy §1.1", "gst-rules §6"),
        ),
    ),
    InvoiceSpec(
        key="kaveri-igst-intra-state",
        title="IGST charged on an intra-state supply",
        edge_case="other",
        vendor="kaveri",
        number="KPH/2026/140",
        date="2026-11-12",
        po_number="PO-2026-0050",
        due_days=15,
        tax_mode="igst",
        lines=(Line("Product catalogue print, 16pp A4", "4911", "2000", "NOS", "32", "18"),),
        expected=Expected(
            "NEEDS_REVIEW",
            exceptions=("TAX_MISMATCH",),
            matched_po="PO-2026-0050",
            matched_grn="GRN-0320",
            reasons=("Karnataka supplier to Karnataka buyer must charge CGST + SGST, not IGST.",),
            citations=("gst-rules §2.2", "ap-policy §5.2", "contracts/kaveri §2.5"),
        ),
    ),
    InvoiceSpec(
        key="northwind-over-approval-limit",
        title="Amount above ₹5 lakh — Finance Manager approval",
        edge_case="other",
        vendor="northwind",
        number="NWE/DL/2026/0790",
        date="2026-11-15",
        po_number="PO-2026-0044",
        due_days=30,
        lines=(ln(DG, "1", "745000"),),
        expected=Expected(
            "NEEDS_REVIEW",
            approvals=("Finance Manager",),
            final_decision="APPROVED",
            matched_po="PO-2026-0044",
            matched_grn="GRN-0314",
            reasons=("Total ₹8,79,100 > ₹5,00,000 → Finance Manager approval.",),
            citations=("ap-policy §7.1",),
        ),
    ),
    InvoiceSpec(
        key="blueleaf-no-grn",
        title="Goods/services not yet received (no GRN)",
        edge_case="other",
        vendor="blueleaf",
        number="BLL/TN/26-27/0412",
        date="2026-10-28",
        po_number="PO-2026-0035",
        due_days=30,
        service_period="Trips 02-Oct to 24-Oct-2026",
        lines=(
            Line(
                "Line haul Chennai–Bengaluru, 32ft container",
                "9965",
                "6",
                "TRIP",
                "12500",
                "12",
            ),
        ),
        expected=Expected(
            "ON_HOLD",
            exceptions=("NO_RECEIPT",),
            matched_po="PO-2026-0035",
            reasons=("PO requires a GRN and none exists; receiver must confirm delivery.",),
            citations=("ap-policy §3.4", "contracts/blueleaf §2.5"),
        ),
    ),
    InvoiceSpec(
        key="malabar-math-error",
        title="Totals do not add up",
        edge_case="other",
        vendor="malabar",
        number="MFW/2026/0229",
        date="2026-11-16",
        po_number="PO-2026-0051",
        due_days=30,
        lines=(Line("Steel storage cabinet, 4-shelf", "9403", "10", "NOS", "9500", "18"),),
        printed_total="112200.00",
        expected=Expected(
            "REJECTED",
            matched_po="PO-2026-0051",
            reasons=("Taxable ₹95,000 + IGST ₹17,100 = ₹1,12,100, but the total says ₹1,12,200.",),
            citations=("ap-policy §5.3",),
        ),
    ),
    InvoiceSpec(
        key="zenith-blocked-vendor",
        title="Invoice from a blocked vendor",
        edge_case="other",
        vendor="zenith",
        number="ZT-0112",
        date="2026-11-09",
        po_number="PO-2026-0030",
        due_days=30,
        lines=(Line("Assorted hangers (bundle of 100)", "3926", "20", "BDL", "900", "18"),),
        expected=Expected(
            "REJECTED",
            reasons=(
                "Vendor V010 is BLOCKED; contract terminated 2026-09-30.",
                "PO-2026-0030 is closed.",
            ),
            citations=("ap-policy §6.1", "contracts/zenith §2.2"),
        ),
    ),
    InvoiceSpec(
        key="sunrise-unknown-vendor",
        title="Supplier not in the vendor master",
        edge_case="other",
        vendor="sunrise",
        number="SE/2026/077",
        date="2026-11-11",
        po_number="PO-2026-0052",
        due_days=15,
        lines=(Line("Handheld barcode scanner, USB", "8471", "10", "NOS", "3200", "18"),),
        expected=Expected(
            "ON_HOLD",
            reasons=(
                "GSTIN valid but not in the vendor master; the agent may not create vendors.",
                "PO-2026-0052 does not exist.",
            ),
            citations=("ap-policy §6.1",),
        ),
    ),
)
