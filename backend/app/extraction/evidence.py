"""Find where a value sits on the page, for source-evidence highlights.

Digital PDFs: search the text layer for the value as read, its source snippet, or common printed
variants (Indian digit grouping, day-first dates). Scanned PDFs: convert the vision model's
normalised box to PDF points.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pymupdf

from app.extraction.document import PageInfo


@dataclass(frozen=True)
class Location:
    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points
    matched: str


def format_indian(value: Decimal) -> str:
    """147500 → '1,47,500.00'."""
    whole, frac = f"{abs(value):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{'-' if value < 0 else ''}{whole}.{frac}"


def amount_variants(value: Decimal) -> list[str]:
    return [format_indian(value), f"{value:,.2f}", f"{value:.2f}"]


def date_variants(value: date) -> list[str]:
    return [
        value.strftime("%d-%m-%Y"),
        value.strftime("%d/%m/%Y"),
        value.strftime("%d.%m.%Y"),
        value.strftime("%d-%b-%Y"),
        value.isoformat(),
    ]


def _union(rects: Sequence[pymupdf.Rect]) -> tuple[float, float, float, float]:
    """Box of the FIRST occurrence. `search_for` returns one rect per line of a match that wraps
    and one per occurrence; a vendor name printed in the header and the footer must not become
    one box covering the page. Rects that continue on the next line belong to the same match."""
    r = pymupdf.Rect(rects[0])
    for other in rects[1:]:
        gap = other.y0 - r.y1
        if gap > max(4.0, other.height):  # not the next line: another occurrence
            break
        r |= other
    return (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))


def locate(
    doc: pymupdf.Document, candidates: Iterable[str | None], prefer_page: int | None = None
) -> Location | None:
    """First candidate string found on a page (preferred page first). Snippets longer than one
    line are tried as written; PyMuPDF's search is case-insensitive and spans line breaks."""
    order = list(range(doc.page_count))
    if prefer_page and 1 <= prefer_page <= doc.page_count:
        order.remove(prefer_page - 1)
        order.insert(0, prefer_page - 1)
    seen: set[str] = set()
    for cand in candidates:
        if not cand:
            continue
        needle = " ".join(str(cand).split())
        if len(needle) < 2 or needle in seen:
            continue
        seen.add(needle)
        for idx in order:
            hits = doc[idx].search_for(needle)
            if hits:
                return Location(page=idx + 1, bbox=_union(hits), matched=needle)
    return None


def box_from_vision(
    box_2d: Sequence[int] | None, page: int | None, pages: Sequence[PageInfo]
) -> tuple[int, tuple[float, float, float, float]] | None:
    """Gemini's [ymin, xmin, ymax, xmax] on a 0-1000 grid → (page, PDF-point bbox)."""
    if not box_2d or len(box_2d) != 4 or not pages:
        return None
    number = page if page and 1 <= page <= len(pages) else 1
    info = pages[number - 1]
    ymin, xmin, ymax, xmax = (max(0, min(1000, int(v))) for v in box_2d)
    if ymax <= ymin or xmax <= xmin:
        return None
    sx, sy = info.width / 1000, info.height / 1000
    return number, (
        round(xmin * sx, 1),
        round(ymin * sy, 1),
        round(xmax * sx, 1),
        round(ymax * sy, 1),
    )
