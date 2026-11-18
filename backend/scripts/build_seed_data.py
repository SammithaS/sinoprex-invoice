"""Generate the mock company's reference data as JSON under data/seed/.

Deterministic: running it twice produces identical files. GSTINs carry valid
checksums; PO totals are computed from their lines.

    uv run python -m scripts.build_seed_data
"""

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from app.rules.gst import STATE_CODES, build_gstin, is_valid_gstin

SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"

COMPANY = {
    "name": "Acme Retail Pvt Ltd",
    "city": "Bengaluru",
    "state_code": "29",
    "gstin": build_gstin("29", "AACCA4821K"),
    "address": "4th Floor, Prestige Meridian, MG Road, Bengaluru 560001",
}

# id, name, legal name, aliases, state, PAN, bank last4, IFSC, terms, category, status
_VENDORS = [
    (
        "V001",
        "TechNova Solutions",
        "TechNova Solutions Pvt Ltd",
        ["TechNova", "Technova Soln"],
        "29",
        "AABCT1332L",
        "4417",
        "HDFC0000123",
        30,
        "IT hardware",
        "ACTIVE",
    ),
    (
        "V002",
        "Sharma Office Supplies",
        "Sharma Office Supplies",
        ["Sharma Stationers"],
        "27",
        "AKZPS6612M",
        "0921",
        "ICIC0001045",
        15,
        "Stationery",
        "ACTIVE",
    ),
    (
        "V003",
        "BlueLeaf Logistics",
        "BlueLeaf Logistics Pvt Ltd",
        ["Blue Leaf Logistics"],
        "33",
        "AAECB7720Q",
        "3305",
        "SBIN0004821",
        30,
        "Freight",
        "ACTIVE",
    ),
    (
        "V004",
        "Kaveri Print House",
        "Kaveri Print House",
        ["Kaveri Printers"],
        "29",
        "BNKPK3389D",
        "7781",
        "CNRB0002210",
        15,
        "Printing",
        "ACTIVE",
    ),
    (
        "V005",
        "CloudNest Software",
        "CloudNest Software Pvt Ltd",
        ["CloudNest", "Cloud Nest"],
        "29",
        "AAICC9054H",
        "1196",
        "KKBK0008011",
        7,
        "SaaS",
        "ACTIVE",
    ),
    (
        "V006",
        "Deccan Facility Services",
        "Deccan Facility Services LLP",
        ["Deccan FS"],
        "36",
        "AAQFD2240R",
        "5502",
        "UTIB0000561",
        30,
        "Facility services",
        "ACTIVE",
    ),
    (
        "V007",
        "Rangoli Packaging",
        "Rangoli Packaging Industries",
        ["Rangoli Pack"],
        "24",
        "AAPFR8813C",
        "6634",
        "BARB0VADODA",
        45,
        "Packaging",
        "ACTIVE",
    ),
    (
        "V008",
        "Northwind Electricals",
        "Northwind Electricals Pvt Ltd",
        ["Northwind Elec"],
        "07",
        "AADCN4471G",
        "2048",
        "PUNB0011200",
        30,
        "Electrical",
        "ACTIVE",
    ),
    (
        "V009",
        "Malabar Furniture Works",
        "Malabar Furniture Works",
        ["Malabar Furniture"],
        "32",
        "ABMFM5567P",
        "8890",
        "FDRL0001102",
        30,
        "Furniture",
        "ACTIVE",
    ),
    (
        "V010",
        "Zenith Traders",
        "Zenith Traders",
        ["Zenith Trading Co"],
        "29",
        "AGHPZ1290B",
        "3141",
        "IDIB000B123",
        30,
        "General trading",
        "BLOCKED",
    ),
]

# po number, vendor, date, status, requires grn, cost centre, buyer,
# lines: (description, hsn/sac, qty, uom, unit price, tax %), amount already invoiced
_POS = [
    (
        "PO-2026-0042",
        "V001",
        "2026-10-20",
        "OPEN",
        True,
        "CC-IT-01",
        "Rahul Menon",
        [
            ("Laptop docking station", "8473", 100, "NOS", "1100", 18),
            ("USB-C charging cable 2m", "8544", 200, "NOS", "150", 18),
        ],
        "0",
    ),
    (
        "PO-2026-0029",
        "V001",
        "2026-08-04",
        "CLOSED",
        True,
        "CC-IT-01",
        "Rahul Menon",
        [("24-inch LED monitor", "8528", 4, "NOS", "12000", 18)],
        "56640",
    ),
    (
        "PO-2026-0045",
        "V001",
        "2026-11-02",
        "OPEN",
        True,
        "CC-IT-01",
        "Rahul Menon",
        [("Wireless keyboard and mouse set", "8471", 50, "NOS", "1450", 18)],
        "0",
    ),
    (
        "PO-2026-0038",
        "V002",
        "2026-10-01",
        "OPEN",
        True,
        "CC-ADM-02",
        "Anita Rao",
        [
            ("A4 copier paper 75gsm (ream)", "4802", 300, "REAM", "240", 12),
            ("Ball pen, blue (box of 50)", "9608", 40, "BOX", "350", 18),
        ],
        "0",
    ),
    (
        "PO-2026-0031",
        "V002",
        "2026-08-20",
        "CLOSED",
        True,
        "CC-ADM-02",
        "Anita Rao",
        [("Whiteboard marker set", "9608", 100, "SET", "180", 18)],
        "21240",
    ),
    (
        "PO-2026-0035",
        "V003",
        "2026-09-25",
        "OPEN",
        True,
        "CC-OPS-03",
        "Vikram Iyer",
        [("Line haul Chennai–Bengaluru, 32ft container", "9965", 6, "TRIP", "12500", 12)],
        "0",
    ),
    (
        "PO-2026-0047",
        "V003",
        "2026-11-05",
        "OPEN",
        True,
        "CC-OPS-03",
        "Vikram Iyer",
        [("Last-mile delivery, Bengaluru stores", "9965", 120, "DROP", "450", 12)],
        "0",
    ),
    (
        "PO-2026-0033",
        "V004",
        "2026-09-12",
        "PARTIALLY_INVOICED",
        True,
        "CC-MKT-04",
        "Sneha Kulkarni",
        [
            ("Festive flyers A5, 4-colour", "4911", 20000, "NOS", "1.10", 18),
            ("Standee 6x3 ft with stand", "4911", 25, "NOS", "1850", 18),
        ],
        "12980",
    ),
    (
        "PO-2026-0048",
        "V004",
        "2026-11-06",
        "OPEN",
        True,
        "CC-MKT-04",
        "Sneha Kulkarni",
        [("Shelf talkers, laminated", "4911", 5000, "NOS", "4.50", 18)],
        "0",
    ),
    (
        "PO-2026-0036",
        "V006",
        "2026-10-01",
        "OPEN",
        False,
        "CC-ADM-02",
        "Anita Rao",
        [("Housekeeping services, Oct 2026 (HQ)", "998533", 1, "MONTH", "185000", 18)],
        "0",
    ),
    (
        "PO-2026-0039",
        "V006",
        "2026-11-01",
        "OPEN",
        False,
        "CC-ADM-02",
        "Anita Rao",
        [("Housekeeping services, Nov 2026 (HQ)", "998533", 1, "MONTH", "185000", 18)],
        "0",
    ),
    (
        "PO-2026-0040",
        "V007",
        "2026-10-10",
        "OPEN",
        True,
        "CC-OPS-03",
        "Vikram Iyer",
        [
            ("Corrugated box 5-ply 18x12x12", "4819", 4000, "NOS", "38", 18),
            ("Packing tape 48mm x 65m", "3919", 600, "ROLL", "62", 18),
        ],
        "0",
    ),
    (
        "PO-2026-0041",
        "V008",
        "2026-10-15",
        "OPEN",
        True,
        "CC-FAC-05",
        "Imran Sheikh",
        [
            ("LED panel light 2x2 ft, 36W", "9405", 150, "NOS", "1650", 18),
            ("Electrical installation labour", "995461", 1, "LOT", "45000", 18),
        ],
        "0",
    ),
    (
        "PO-2026-0044",
        "V008",
        "2026-10-28",
        "OPEN",
        True,
        "CC-FAC-05",
        "Imran Sheikh",
        [("Diesel generator 125 kVA with AMF panel", "8502", 1, "NOS", "745000", 18)],
        "0",
    ),
    (
        "PO-2026-0043",
        "V009",
        "2026-10-22",
        "OPEN",
        True,
        "CC-FAC-05",
        "Imran Sheikh",
        [
            ("Ergonomic office chair", "9401", 40, "NOS", "8900", 18),
            ("Workstation desk 1200x600", "9403", 40, "NOS", "11200", 18),
        ],
        "0",
    ),
    (
        "PO-2026-0046",
        "V009",
        "2026-11-03",
        "OPEN",
        True,
        "CC-FAC-05",
        "Imran Sheikh",
        [("Meeting table 8-seater", "9403", 3, "NOS", "42000", 18)],
        "0",
    ),
    (
        "PO-2026-0030",
        "V010",
        "2026-08-12",
        "CLOSED",
        True,
        "CC-OPS-03",
        "Vikram Iyer",
        [("Assorted hangers (bundle of 100)", "3926", 50, "BDL", "900", 18)],
        "53100",
    ),
    (
        "PO-2026-0049",
        "V002",
        "2026-11-10",
        "OPEN",
        True,
        "CC-ADM-02",
        "Anita Rao",
        [("Thermal receipt rolls 79mm", "4811", 1000, "ROLL", "42", 18)],
        "0",
    ),
]

# grn number, po number, date, received by, {line_no: qty received}
_GRNS = [
    ("GRN-0311", "PO-2026-0042", "2026-11-08", "Store BLR-01", {1: 100}),
    ("GRN-0288", "PO-2026-0029", "2026-08-18", "Store BLR-01", {1: 4}),
    ("GRN-0305", "PO-2026-0038", "2026-10-09", "HQ admin", {1: 300, 2: 40}),
    ("GRN-0292", "PO-2026-0031", "2026-08-28", "HQ admin", {1: 100}),
    ("GRN-0299", "PO-2026-0033", "2026-09-26", "Marketing", {1: 20000, 2: 25}),
    ("GRN-0308", "PO-2026-0040", "2026-10-24", "Warehouse HSR", {1: 4000, 2: 600}),
    ("GRN-0310", "PO-2026-0041", "2026-11-04", "Facilities", {1: 150, 2: 1}),
    ("GRN-0313", "PO-2026-0043", "2026-11-12", "Facilities", {1: 40, 2: 40}),
    ("GRN-0290", "PO-2026-0030", "2026-08-25", "Warehouse HSR", {1: 50}),
    ("GRN-0314", "PO-2026-0044", "2026-11-14", "Facilities", {1: 1}),
]

# vendor, invoice no, date, po, subtotal, tax, gl, cost centre, status
_PRIOR_INVOICES = [
    ("V001", "TN/2026/0997", "2026-08-19", "PO-2026-0029", "48000", "8640", None, None, "PAID"),
    ("V002", "SOS-2026-0412", "2026-08-30", "PO-2026-0031", "18000", "3240", None, None, "PAID"),
    ("V004", "KPH/2026/118", "2026-09-28", "PO-2026-0033", "11000", "1980", None, None, "POSTED"),
    ("V010", "ZT-0087", "2026-08-27", "PO-2026-0030", "45000", "8100", None, None, "PAID"),
    # CloudNest SaaS — non-PO history, consistently coded to GL 6420 / CC-IT-01.
    ("V005", "CN-INV-2026-06", "2026-06-01", None, "42000", "7560", "6420", "CC-IT-01", "PAID"),
    ("V005", "CN-INV-2026-07", "2026-07-01", None, "42000", "7560", "6420", "CC-IT-01", "PAID"),
    ("V005", "CN-INV-2026-08", "2026-08-01", None, "42000", "7560", "6420", "CC-IT-01", "PAID"),
    ("V005", "CN-INV-2026-09", "2026-09-01", None, "42000", "7560", "6420", "CC-IT-01", "PAID"),
    ("V005", "CN-INV-2026-10", "2026-10-01", None, "42000", "7560", "6420", "CC-IT-01", "PAID"),
]


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build() -> dict[str, object]:
    vendors = []
    for vid, name, legal, aliases, st, pan, last4, ifsc, terms, cat, status in _VENDORS:
        gstin = build_gstin(st, pan)
        assert is_valid_gstin(gstin), gstin
        slug = name.lower().split()[0]
        vendors.append(
            {
                "id": vid,
                "name": name,
                "legal_name": legal,
                "aliases": aliases,
                "gstin": gstin,
                "state_code": st,
                "state": STATE_CODES[st],
                "pan": pan,
                "bank_account_last4": last4,
                "bank_ifsc": ifsc,
                "payment_terms_days": terms,
                "category": cat,
                "email": f"accounts@{slug}.example",
                "status": status,
            }
        )

    pos = []
    for number, vid, dt, status, needs_grn, cc, buyer, lines, invoiced in _POS:
        out_lines, subtotal, tax = [], Decimal(0), Decimal(0)
        for i, (desc, hsn, qty, uom, price, rate) in enumerate(lines, start=1):
            amount = _money(Decimal(qty) * Decimal(price))
            subtotal += amount
            tax += _money(amount * Decimal(rate) / 100)
            out_lines.append(
                {
                    "line_no": i,
                    "description": desc,
                    "hsn_sac": hsn,
                    "qty": str(qty),
                    "uom": uom,
                    "unit_price": price,
                    "tax_rate": str(rate),
                }
            )
        pos.append(
            {
                "po_number": number,
                "vendor_id": vid,
                "po_date": dt,
                "status": status,
                "requires_grn": needs_grn,
                "cost_centre": cc,
                "buyer": buyer,
                "subtotal": str(subtotal),
                "tax_total": str(tax),
                "total": str(subtotal + tax),
                "amount_invoiced": invoiced,
                "lines": out_lines,
            }
        )

    grns = [
        {
            "grn_number": g,
            "po_number": po,
            "received_date": dt,
            "received_by": by,
            "lines": [{"line_no": n, "qty_received": str(q)} for n, q in qty.items()],
        }
        for g, po, dt, by, qty in _GRNS
    ]

    invoices = [
        {
            "vendor_id": v,
            "invoice_number": no,
            "invoice_date": dt,
            "po_number": po,
            "subtotal": sub,
            "tax_total": tax,
            "total": str(Decimal(sub) + Decimal(tax)),
            "gl_account": gl,
            "cost_centre": cc,
            "status": st,
        }
        for v, no, dt, po, sub, tax, gl, cc, st in _PRIOR_INVOICES
    ]
    return {
        "company": COMPANY,
        "vendors": vendors,
        "purchase_orders": pos,
        "goods_receipts": grns,
        "invoices": invoices,
    }


def main() -> None:
    SEED_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in build().items():
        path = SEED_DIR / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(SEED_DIR.parents[1])}")


if __name__ == "__main__":
    main()
