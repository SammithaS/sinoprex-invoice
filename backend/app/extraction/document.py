"""Document check: open a PDF, decide digital vs scanned, classify its type, render page images
and score scan quality. PyMuPDF only — no LLM here."""

import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Literal

import pymupdf
from PIL import Image, ImageFilter, ImageStat

DocType = Literal["invoice", "credit_note", "quote", "other"]

MAX_PAGES = 5  # invoices longer than this are unusual; only the first pages are sent to the LLM
MIN_TEXT_CHARS = 80  # a page with fewer extractable characters is treated as an image
VISION_DPI = 170


@dataclass
class PageInfo:
    number: int  # 1-based
    width: float  # PDF points
    height: float
    text: str
    has_text_layer: bool
    quality: float | None = None  # 0..1, only for image pages


@dataclass
class DocumentInfo:
    sha256: str
    page_count: int
    pages: list[PageInfo]
    is_scanned: bool
    doc_type_hint: DocType | None  # from keywords in the text layer; None if unknown/scanned
    images: list[bytes] = field(default_factory=list)  # PNG per page (scanned docs only)

    @property
    def text(self) -> str:
        return "\n".join(f"--- page {p.number} ---\n{p.text}" for p in self.pages)

    @property
    def quality(self) -> float | None:
        """Worst page quality for scanned documents, None for digital ones."""
        scores = [p.quality for p in self.pages if p.quality is not None]
        return min(scores) if scores else None


class DocumentError(ValueError):
    """The file isn't a readable PDF."""


_TYPE_PATTERNS: list[tuple[DocType, re.Pattern[str]]] = [
    ("credit_note", re.compile(r"(?i)\bcredit\s+note\b")),
    ("quote", re.compile(r"(?i)\b(quotation|proforma|pro-forma|estimate)\b")),
    ("invoice", re.compile(r"(?i)\b(tax\s+invoice|invoice\s+no|bill\s+of\s+supply)\b")),
]


def classify_text(text: str) -> DocType | None:
    """Keyword classification of a text layer. Title keywords win over the generic ones, so a
    credit note that mentions 'against invoice no.' is still a credit note."""
    head = text[:600]  # titles sit near the top
    for doc_type, pattern in _TYPE_PATTERNS:
        if pattern.search(head):
            return doc_type
    for doc_type, pattern in _TYPE_PATTERNS:
        if pattern.search(text):
            return doc_type
    return None


def scan_quality(img: Image.Image) -> float:
    """0..1 legibility score for a scanned page from edge sharpness and ink/paper contrast.

    Calibrated on the generated scans (digital ≈ 1.0, good ≈ 1.0, medium ≈ 0.95, poor ≈ 0.4):
    blur flattens edges, and washed-out scans lose contrast. A heuristic, tuned on the sample scans.
    """
    grey = img.convert("L")
    top = grey.crop((0, 0, grey.width, max(1, grey.height // 2)))  # where the text is
    laplace = top.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), 1, 128))
    edge = ImageStat.Stat(laplace).stddev[0]
    hist = top.histogram()
    total = sum(hist)

    def percentile(q: float) -> int:
        seen = 0
        for value, count in enumerate(hist):
            seen += count
            if seen >= q * total:
                return value
        return 255

    contrast = percentile(0.98) - percentile(0.02)
    return round(max(0.0, min(1.0, edge / 30, contrast / 170)), 2)


def _render(page: pymupdf.Page, dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def inspect_pdf(data: bytes) -> DocumentInfo:
    """Read a PDF: text layer per page, digital vs scanned, type hint, and (for scans) page images
    for the vision model plus a quality score per page."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise DocumentError(f"not a readable PDF: {exc}") from exc
    with doc:
        if doc.page_count == 0:
            raise DocumentError("PDF has no pages")
        if doc.needs_pass:
            raise DocumentError("PDF is password-protected")
        pages: list[PageInfo] = []
        images: list[bytes] = []
        for i, page in enumerate(doc.pages()):
            if i >= MAX_PAGES:
                break
            text = page.get_text("text", sort=True).strip()
            pages.append(
                PageInfo(
                    number=i + 1,
                    width=page.rect.width,
                    height=page.rect.height,
                    text=text,
                    has_text_layer=len(text) >= MIN_TEXT_CHARS,
                )
            )
        is_scanned = not any(p.has_text_layer for p in pages)
        if is_scanned:
            for info, page in zip(pages, doc.pages(), strict=False):
                img = _render(page, VISION_DPI)
                info.quality = scan_quality(img)
                images.append(_png(img))
        full_text = "\n".join(p.text for p in pages)
        return DocumentInfo(
            sha256=hashlib.sha256(data).hexdigest(),
            page_count=doc.page_count,
            pages=pages,
            is_scanned=is_scanned,
            doc_type_hint=None if is_scanned else classify_text(full_text),
            images=images,
        )
