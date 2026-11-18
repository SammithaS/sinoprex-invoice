"""Render invoice specs to PDF (ReportLab) and degrade them into "scanned" copies (PyMuPDF).

Four vendor layouts so extraction can't overfit one template:
  classic — boxed header, ruled item table (TechNova, Northwind, Malabar, Sunrise)
  band    — coloured top band, modern SaaS-style (Kaveri, CloudNest, Rangoli)
  ledger  — Tally-style monospaced grid (Sharma, Zenith)
  service — letter-style service bill (Deccan, BlueLeaf)

Built-in PDF fonts have no ₹ glyph, so amounts print as "Rs." / "INR" — as many real Indian
invoices do; extraction must normalise either form.
"""

import io
import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pymupdf
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from scripts.invoice_specs import VENDORS, Computed, InvoiceSpec, Scan, VendorProfile, money

ACME: dict[str, str] = {
    "name": "Acme Retail Pvt Ltd",
    "gstin": "29AACCA4821K1ZM",
    "state": "Karnataka (29)",
}
ACME_ADDRESS = ("4th Floor, Prestige Meridian, MG Road", "Bengaluru, Karnataka 560001")

STATE_NAMES = {
    "07": "Delhi",
    "24": "Gujarat",
    "27": "Maharashtra",
    "29": "Karnataka",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "36": "Telangana",
}

# --- money helpers ---------------------------------------------------------------------------

_ONES = [
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
_TENS = ["_", "_", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def format_inr(value: Decimal) -> str:
    """Indian digit grouping: 147500 → '1,47,500.00'."""
    q = money(value)
    sign = "-" if q < 0 else ""
    whole, frac = f"{abs(q):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{sign}{whole}.{frac}"


def _words_below_1000(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} Hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        parts.append(_ONES[n])
    return " ".join(parts)


def amount_in_words(value: Decimal) -> str:
    """Indian system: 147500.50 → 'Rupees One Lakh Forty Seven Thousand Five Hundred and Fifty
    Paise Only'."""
    q = money(value)
    rupees = int(q)
    paise = int((q - rupees) * 100)
    parts = []
    for size, name in ((10**7, "Crore"), (10**5, "Lakh"), (1000, "Thousand")):
        if rupees >= size:
            parts.append(f"{_words_below_1000(rupees // size)} {name}")
            rupees %= size
    if rupees or not parts:
        parts.append(_words_below_1000(rupees) or "Zero")
    text = "Rupees " + " ".join(parts)
    if paise:
        text += f" and {_words_below_1000(paise)} Paise"
    return text + " Only"


def display_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d-%m-%Y")


def due_date(spec: InvoiceSpec) -> str | None:
    if spec.due_days is None:
        return None
    return (date.fromisoformat(spec.date) + timedelta(days=spec.due_days)).isoformat()


def printed_total(spec: InvoiceSpec, comp: Computed) -> Decimal:
    return Decimal(spec.printed_total) if spec.printed_total else comp.total


def bank_details(spec: InvoiceSpec) -> tuple[str, str, str]:
    v = VENDORS[spec.vendor]
    return spec.bank or (v.bank_name, v.bank_account, v.bank_ifsc)


# --- shared building blocks ------------------------------------------------------------------


def _styles(font: str, bold: str) -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName=font, fontSize=8.6, leading=11.2)
    return {
        "base": base,
        "small": ParagraphStyle("small", parent=base, fontSize=7.6, leading=9.6),
        "bold": ParagraphStyle("bold", parent=base, fontName=bold),
        "right": ParagraphStyle("right", parent=base, alignment=TA_RIGHT),
        "h1": ParagraphStyle("h1", parent=base, fontName=bold, fontSize=15, leading=19),
        "h2": ParagraphStyle("h2", parent=base, fontName=bold, fontSize=11, leading=14),
        "title": ParagraphStyle(
            "title", parent=base, fontName=bold, fontSize=13, leading=16, alignment=TA_RIGHT
        ),
    }


def _doc_title(spec: InvoiceSpec) -> str:
    return {"invoice": "TAX INVOICE", "quote": "QUOTATION", "credit_note": "CREDIT NOTE"}[
        spec.doc_type
    ]


def _number_label(spec: InvoiceSpec) -> str:
    return {"invoice": "Invoice No.", "quote": "Quotation No.", "credit_note": "Credit Note No."}[
        spec.doc_type
    ]


def _meta_rows(spec: InvoiceSpec) -> list[tuple[str, str]]:
    rows = [(_number_label(spec), spec.number), ("Date", display_date(spec.date))]
    due = due_date(spec)
    if spec.doc_type == "invoice" and due:
        rows.append(("Due Date", display_date(due)))
    if spec.doc_type == "quote":
        rows.append(("Valid Until", display_date(_plus_days(spec.date, 30))))
    if spec.po_number:
        rows.append(("PO No.", spec.po_number))
    if spec.references:
        rows.append(("Against Invoice", spec.references))
    if spec.service_period:
        rows.append(("Service Period", spec.service_period))
    if spec.doc_type != "quote":
        rows.append(("Place of Supply", ACME["state"]))  # all deliveries are to Bengaluru
    return rows


def _plus_days(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def _vendor_block(v: VendorProfile, st: dict[str, ParagraphStyle], name_style: str) -> list[Any]:
    state = STATE_NAMES.get(v.gstin[:2], "")
    return [
        Paragraph(v.legal_name, st[name_style]),
        *[Paragraph(line, st["base"]) for line in v.address],
        Paragraph(f"Ph: {v.phone} &nbsp; Email: {v.email}", st["small"]),
        Paragraph(f"<b>GSTIN:</b> {v.gstin} &nbsp; State: {state} ({v.gstin[:2]})", st["base"]),
    ]


def _buyer_block(st: dict[str, ParagraphStyle], label: str = "Bill To") -> list[Any]:
    return [
        Paragraph(f"<b>{label}</b>", st["base"]),
        Paragraph(ACME["name"], st["bold"]),
        *[Paragraph(line, st["base"]) for line in ACME_ADDRESS],
        Paragraph(f"<b>GSTIN:</b> {ACME['gstin']} &nbsp; State: {ACME['state']}", st["base"]),
    ]


def _items_rows(comp: Computed, st: dict[str, ParagraphStyle]) -> list[list[Any]]:
    header = ["#", "Description", "HSN/SAC", "Qty", "Unit", "Rate (Rs.)", "GST %", "Amount (Rs.)"]
    rows: list[list[Any]] = [header]
    for line in comp.lines:
        rows.append(
            [
                line["line_no"],
                Paragraph(line["description"], st["base"]),
                line["hsn_sac"],
                _qty(line["qty"]),
                line["uom"],
                format_inr(Decimal(line["unit_price"])),
                f"{line['tax_rate']}%",
                format_inr(Decimal(line["amount"])),
            ]
        )
    return rows


def _qty(q: str) -> str:
    d = Decimal(q)
    return f"{d:,.0f}" if d == d.to_integral_value() else f"{d:,.2f}"


ITEM_WIDTHS = [7 * mm, 66 * mm, 17 * mm, 14 * mm, 12 * mm, 22 * mm, 12 * mm, 28 * mm]


def _tax_rows(spec: InvoiceSpec, comp: Computed) -> list[tuple[str, Decimal]]:
    """One tax row per rate (and per head), the way Indian GST invoices print them."""
    rows = [("Taxable Value", comp.subtotal)]
    for rate in sorted({line["tax_rate"] for line in comp.lines}, key=Decimal):
        amounts = [Decimal(line["amount"]) for line in comp.lines if line["tax_rate"] == rate]
        if comp.intra_state:
            half = sum((money(a * Decimal(rate) / 200) for a in amounts), Decimal(0))
            pct = f"{Decimal(rate) / 2:g}%"
            rows += [(f"CGST @ {pct}", half), (f"SGST @ {pct}", half)]
        else:
            igst = sum((money(a * Decimal(rate) / 100) for a in amounts), Decimal(0))
            rows.append((f"IGST @ {rate}%", igst))
    return rows


def _totals_table(
    spec: InvoiceSpec, comp: Computed, st: dict[str, ParagraphStyle], grand_bg: Any
) -> Table:
    rows: list[list[Any]] = [[k, format_inr(v)] for k, v in _tax_rows(spec, comp)]
    label = "Total Credit" if spec.doc_type == "credit_note" else "Grand Total (INR)"
    rows.append([label, f"Rs. {format_inr(printed_total(spec, comp))}"])
    t = Table(rows, colWidths=[42 * mm, 34 * mm], hAlign="RIGHT")
    t.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), st["base"].fontName, 8.6),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONT", (0, -1), (-1, -1), st["bold"].fontName, 9.6),
                ("BACKGROUND", (0, -1), (-1, -1), grand_bg),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]
        )
    )
    return t


def _footer_blocks(spec: InvoiceSpec, comp: Computed, st: dict[str, ParagraphStyle]) -> list[Any]:
    v = VENDORS[spec.vendor]
    bank, acct, ifsc = bank_details(spec)
    out: list[Any] = [
        Spacer(1, 4 * mm),
        Paragraph(
            f"<b>Amount in words:</b> {amount_in_words(printed_total(spec, comp))}", st["base"]
        ),
    ]
    for note in spec.notes:
        out += [Spacer(1, 2 * mm), Paragraph(f"<b>Note:</b> {note}", st["base"])]
    if spec.doc_type == "invoice":
        out += [
            Spacer(1, 3 * mm),
            Paragraph("<b>Bank Details for Payment</b>", st["base"]),
            Paragraph(f"Bank: {bank} &nbsp; A/c No.: {acct} &nbsp; IFSC: {ifsc}", st["base"]),
            Paragraph(f"A/c Name: {v.legal_name}", st["base"]),
        ]
    out += [
        Spacer(1, 6 * mm),
        Table(
            [
                [
                    Paragraph(
                        "Declaration: We declare that this document shows the actual price of the "
                        "goods/services described and that all particulars are true and correct.",
                        st["small"],
                    ),
                    Paragraph(
                        f"For <b>{v.legal_name}</b><br/><br/><br/>Authorised Signatory",
                        st["right"],
                    ),
                ]
            ],
            colWidths=[100 * mm, 78 * mm],
            style=[("VALIGN", (0, 0), (-1, -1), "TOP")],
        ),
    ]
    return out


class _Band(Flowable):  # type: ignore[misc]  # reportlab ships no type hints
    """Full-width coloured band with the vendor name, for the "band" layout."""

    def __init__(self, text: str, sub: str, colour: Any, width: float) -> None:
        super().__init__()
        self.text, self.sub, self.colour, self.width = text, sub, colour, width
        self.height = 20 * mm

    def wrap(self, avail_w: float, avail_h: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(self.colour)
        c.roundRect(0, 0, self.width, self.height, 3 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(6 * mm, self.height - 9 * mm, self.text)
        c.setFont("Helvetica", 8.5)
        c.drawString(6 * mm, 5 * mm, self.sub)


# --- layouts ---------------------------------------------------------------------------------


def _classic(spec: InvoiceSpec, comp: Computed) -> list[Any]:
    v = VENDORS[spec.vendor]
    st = _styles("Helvetica", "Helvetica-Bold")
    accent = colors.HexColor(v.accent)
    st["h1"].textColor = accent
    meta = Table(
        [
            [Paragraph(f"<b>{k}</b>", st["base"]), Paragraph(val, st["base"])]
            for k, val in _meta_rows(spec)
        ],
        colWidths=[26 * mm, 46 * mm],
    )
    meta.setStyle(
        TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)])
    )
    head = Table(
        [
            [
                _vendor_block(v, st, "h1"),
                [Paragraph(_doc_title(spec), st["title"]), Spacer(1, 2 * mm), meta],
            ]
        ],
        colWidths=[104 * mm, 74 * mm],
    )
    head.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.8, accent),
                ("LINEAFTER", (0, 0), (0, 0), 0.5, accent),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    buyer = Table([[_buyer_block(st)]], colWidths=[178 * mm])
    buyer.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.grey)]))
    items = Table(_items_rows(comp, st), colWidths=ITEM_WIDTHS, repeatRows=1)
    items.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8.4),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.2),
                ("BACKGROUND", (0, 0), (-1, 0), accent),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9CA3AF")),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return [
        head,
        Spacer(1, 3 * mm),
        buyer,
        Spacer(1, 4 * mm),
        items,
        Spacer(1, 3 * mm),
        _totals_table(spec, comp, st, colors.HexColor("#F3F4F6")),
        *_footer_blocks(spec, comp, st),
    ]


def _band(spec: InvoiceSpec, comp: Computed) -> list[Any]:
    v = VENDORS[spec.vendor]
    st = _styles("Helvetica", "Helvetica-Bold")
    accent = colors.HexColor(v.accent)
    state = STATE_NAMES.get(v.gstin[:2], "")
    band = _Band(
        v.legal_name, f"{', '.join(v.address)}  |  GSTIN {v.gstin}  |  {state}", accent, 178 * mm
    )
    meta_cells = [
        [Paragraph(f"<font color='#6B7280'>{k}</font><br/><b>{val}</b>", st["base"])]
        for k, val in _meta_rows(spec)
    ]
    meta = Table(meta_cells, colWidths=[70 * mm])
    meta.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.HexColor("#E5E7EB"))]))
    title = Paragraph(f"<font color='{v.accent}'>{_doc_title(spec)}</font>", st["h1"])
    top = Table(
        [[[title, Spacer(1, 3 * mm), *_buyer_block(st, "Billed To")], meta]],
        colWidths=[108 * mm, 70 * mm],
    )
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    items = Table(_items_rows(comp, st), colWidths=ITEM_WIDTHS, repeatRows=1)
    items.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8.4),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.2),
                ("TEXTCOLOR", (0, 0), (-1, 0), accent),
                ("LINEBELOW", (0, 0), (-1, 0), 1.2, accent),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#E5E7EB")),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return [
        band,
        Spacer(1, 5 * mm),
        top,
        Spacer(1, 5 * mm),
        items,
        Spacer(1, 3 * mm),
        _totals_table(spec, comp, st, colors.HexColor("#F5F3FF")),
        Paragraph(f"Contact: {v.phone} · {v.email}", st["small"]),
        *_footer_blocks(spec, comp, st),
    ]


def _ledger(spec: InvoiceSpec, comp: Computed) -> list[Any]:
    v = VENDORS[spec.vendor]
    st = _styles("Courier", "Courier-Bold")
    for s in st.values():
        s.fontSize, s.leading = s.fontSize - 0.4, s.leading - 0.4
    state = STATE_NAMES.get(v.gstin[:2], "")
    title = Paragraph(f"<para alignment='center'><b>{_doc_title(spec)}</b></para>", st["h2"])
    name = Paragraph(f"<para alignment='center'><b>{v.legal_name.upper()}</b></para>", st["h1"])
    addr = Paragraph(
        f"<para alignment='center'>{', '.join(v.address)}<br/>GSTIN/UIN: {v.gstin} &nbsp; "
        f"State Name: {state}, Code: {v.gstin[:2]}<br/>Ph: {v.phone} &nbsp; {v.email}</para>",
        st["base"],
    )
    meta = "<br/>".join(
        f"{k:<16}:".replace(" ", "&nbsp;") + f" {val}" for k, val in _meta_rows(spec)
    )
    grid = Table(
        [
            [[name, addr]],
            [title],
            [[*_buyer_block(st, "Buyer (Bill to)")], Paragraph(meta, st["base"])],
        ],
        colWidths=[94 * mm, 84 * mm],
    )
    grid.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (1, 0)),
                ("SPAN", (0, 1), (1, 1)),
                ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    items = Table(_items_rows(comp, st), colWidths=ITEM_WIDTHS, repeatRows=1)
    items.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Courier", 8),
                ("FONT", (0, 0), (-1, 0), "Courier-Bold", 7.6),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.black),
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.black),
                ("LINEAFTER", (0, 0), (-2, -1), 0.4, colors.black),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    totals = _totals_table(spec, comp, st, colors.white)
    totals.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Courier", 8.2),
                ("FONT", (0, -1), (-1, -1), "Courier-Bold", 9),
            ]
        )
    )
    return [
        grid,
        items,
        Spacer(1, 2 * mm),
        totals,
        *_footer_blocks(spec, comp, st),
        Spacer(1, 3 * mm),
        Paragraph(
            "<para alignment='center'>This is a Computer Generated Invoice</para>", st["small"]
        ),
    ]


def _service(spec: InvoiceSpec, comp: Computed) -> list[Any]:
    v = VENDORS[spec.vendor]
    st = _styles("Times-Roman", "Times-Bold")
    for s in st.values():
        s.fontSize, s.leading = s.fontSize + 0.8, s.leading + 1
    accent = colors.HexColor(v.accent)
    st["h1"].textColor = accent
    state = STATE_NAMES.get(v.gstin[:2], "")
    letterhead = [
        Paragraph(v.legal_name, st["h1"]),
        Paragraph(f"{' · '.join(v.address)}", st["base"]),
        Paragraph(f"GSTIN {v.gstin} ({state}) · {v.phone} · {v.email}", st["small"]),
    ]
    rule = Table([[""]], colWidths=[178 * mm], rowHeights=[1.2])
    rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.5, accent)]))
    meta = Table(
        [[Paragraph(f"<b>{k}:</b> {val}", st["base"])] for k, val in _meta_rows(spec)],
        colWidths=[80 * mm],
    )
    meta.setStyle(
        TableStyle(
            [("TOPPADDING", (0, 0), (-1, -1), 0.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5)]
        )
    )
    top = Table([[_buyer_block(st, "To"), meta]], colWidths=[98 * mm, 80 * mm])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    intro = Paragraph(
        f"Dear Sir/Madam,<br/>Please find below our {_doc_title(spec).lower()} for services "
        f"rendered to {ACME['name']}.",
        st["base"],
    )
    items = Table(_items_rows(comp, st), colWidths=ITEM_WIDTHS, repeatRows=1)
    items.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Times-Roman", 9),
                ("FONT", (0, 0), (-1, 0), "Times-Bold", 8.8),
                ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.black),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                ("LINEBELOW", (0, -1), (-1, -1), 0.8, colors.black),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            ]
        )
    )
    return [
        *letterhead,
        rule,
        Spacer(1, 3 * mm),
        Paragraph(f"<font color='{v.accent}'><b>{_doc_title(spec)}</b></font>", st["h2"]),
        Spacer(1, 2 * mm),
        top,
        Spacer(1, 4 * mm),
        intro,
        Spacer(1, 3 * mm),
        items,
        Spacer(1, 3 * mm),
        KeepTogether([_totals_table(spec, comp, st, colors.HexColor("#FEF3C7"))]),
        *_footer_blocks(spec, comp, st),
    ]


LAYOUTS = {"classic": _classic, "band": _band, "ledger": _ledger, "service": _service}


def render_pdf(spec: InvoiceSpec, comp: Computed) -> bytes:
    v = VENDORS[spec.vendor]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"{_doc_title(spec).title()} {spec.number}",
        author=v.legal_name,
        creator="InvoiceIQ test-data generator",
        invariant=1,  # deterministic output (fixed timestamps and document id)
    )
    doc.build(LAYOUTS[v.layout](spec, comp))
    return buf.getvalue()


# --- "scanning" ------------------------------------------------------------------------------

# dpi, rotation (deg), noise amplitude, blur radius, jpeg quality, contrast floor, speckles
SCAN_PROFILES: dict[str, tuple[int, float, int, float, int, int, int]] = {
    "good": (200, 0.4, 10, 0.0, 85, 0, 40),
    "medium": (150, 1.6, 22, 0.4, 60, 18, 250),
    "poor": (110, -2.4, 40, 0.9, 32, 45, 900),
}


def scan_pdf(pdf: bytes, quality: Scan, seed: str) -> bytes:
    """Rasterise each page and degrade it like a cheap office scanner; returns an image-only PDF
    (no text layer)."""
    dpi, angle, amp, blur, jpeg_q, floor, speckles = SCAN_PROFILES[quality]
    rng = random.Random(seed)
    src = pymupdf.open(stream=pdf, filetype="pdf")
    out = pymupdf.open()
    for page in src.pages():
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        if floor:  # washed-out greys, as from a tired scanner lamp
            img = img.point(lambda p, f=floor: f + p * (255 - f) // 255)
        img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=250)
        noise = Image.frombytes("L", img.size, rng.randbytes(img.width * img.height))
        noise = noise.point(lambda p, a=amp: 128 + (p - 128) * a // 128)
        img = ImageChops.add(img, noise, scale=1.0, offset=-128)
        draw = ImageDraw.Draw(img)
        for _ in range(speckles):
            x, y = rng.randrange(img.width), rng.randrange(img.height)
            r = rng.choice((1, 1, 1, 2))
            draw.ellipse((x, y, x + r, y + r), fill=rng.randrange(40, 140))
        if quality != "good":  # dark scanner edge on one side
            draw.rectangle((0, 0, rng.randrange(6, 14), img.height), fill=70)
        if blur:
            img = img.filter(ImageFilter.GaussianBlur(blur))
        jpg = io.BytesIO()
        img.save(jpg, "JPEG", quality=jpeg_q, optimize=True)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=jpg.getvalue())
    out.set_metadata({"producer": "Office Scanner MFP-2200", "creator": "Scan to PDF"})
    data: bytes = out.tobytes(garbage=4, deflate=True, no_new_id=True)
    return data
