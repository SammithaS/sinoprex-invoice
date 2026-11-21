"""Mock ERP behind an `ErpProvider` interface.

Posting is idempotent: the idempotency key is the run id, so a retried or resumed `post_erp`
returns the document already posted. `read()` is what `verify_post` compares with the approved
values. A real ERP adapter would implement the same two calls.

Demo fault: when the posting request carries `fault="amount_minus_one_paisa"`
the mock stores the amount one paisa short, so the read-back check fails as in edge case #8.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ErpDocument

FAULTS = frozenset({"amount_minus_one_paisa"})


class ErpDocumentIn(BaseModel):
    idempotency_key: str
    vendor_id: str
    invoice_number: str
    invoice_date: date
    po_number: str | None = None
    amount: Decimal
    tax_amount: Decimal
    currency: str = "INR"
    gl_account: str | None = None
    cost_centre: str | None = None
    attachment_url: str | None = None
    payload: dict[str, Any] | None = None  # match evidence, approval history


class ErpDocumentOut(ErpDocumentIn):
    doc_number: str
    status: str
    created: bool  # False when the idempotency key was already posted


class ErpProvider(Protocol):
    async def post(self, doc: ErpDocumentIn, fault: str | None = None) -> ErpDocumentOut: ...
    async def read(self, doc_number: str) -> ErpDocumentOut | None: ...


def _out(d: ErpDocument, created: bool) -> ErpDocumentOut:
    return ErpDocumentOut(
        idempotency_key=d.idempotency_key,
        vendor_id=d.vendor_id,
        invoice_number=d.invoice_number,
        invoice_date=d.invoice_date,
        po_number=d.po_number,
        amount=d.amount,
        tax_amount=d.tax_amount,
        currency=d.currency,
        gl_account=d.gl_account,
        cost_centre=d.cost_centre,
        attachment_url=d.attachment_url,
        payload=d.payload,
        doc_number=d.doc_number,
        status=d.status,
        created=created,
    )


class MockErp:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def post(self, doc: ErpDocumentIn, fault: str | None = None) -> ErpDocumentOut:
        async with self.sessionmaker() as session:
            existing = await session.scalar(
                select(ErpDocument).where(ErpDocument.idempotency_key == doc.idempotency_key)
            )
            if existing is None:
                # Real ERPs refuse a second document for the same vendor + invoice number, so a
                # re-run of an already-posted invoice gets the original document back.
                existing = await session.scalar(
                    select(ErpDocument).where(
                        ErpDocument.vendor_id == doc.vendor_id,
                        ErpDocument.invoice_number == doc.invoice_number,
                        ErpDocument.status == "POSTED",
                    )
                )
            if existing is not None:
                return _out(existing, created=False)
            count = await session.scalar(select(func.count(ErpDocument.id))) or 0
            amount = (
                doc.amount - Decimal("0.01") if fault == "amount_minus_one_paisa" else doc.amount
            )
            row = ErpDocument(
                doc_number=f"AP-2026-{count + 1:06d}",
                idempotency_key=doc.idempotency_key,
                vendor_id=doc.vendor_id,
                invoice_number=doc.invoice_number,
                invoice_date=doc.invoice_date,
                po_number=doc.po_number,
                amount=amount,
                tax_amount=doc.tax_amount,
                currency=doc.currency,
                gl_account=doc.gl_account,
                cost_centre=doc.cost_centre,
                attachment_url=doc.attachment_url,
                payload=doc.payload,
                status="POSTED",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _out(row, created=True)

    async def read(self, doc_number: str) -> ErpDocumentOut | None:
        async with self.sessionmaker() as session:
            row = await session.scalar(
                select(ErpDocument).where(ErpDocument.doc_number == doc_number)
            )
            return _out(row, created=False) if row else None

    async def correct(self, doc_number: str, amount: Decimal) -> ErpDocumentOut | None:
        """What an AP clerk does in the ERP when the read-back shows a wrong amount."""
        async with self.sessionmaker() as session:
            row = await session.scalar(
                select(ErpDocument).where(ErpDocument.doc_number == doc_number)
            )
            if row is None:
                return None
            row.amount = amount
            row.updated_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()
            await session.refresh(row)
            return _out(row, created=False)


def compare(expected: ErpDocumentIn, actual: ErpDocumentOut | None, doc_number: str) -> list[str]:
    """Read-back check (policy §10.1): document number, amount, status, attachment, coding."""
    if actual is None:
        return [f"document {doc_number} not found in the ERP"]
    problems: list[str] = []
    if actual.doc_number != doc_number:
        problems.append(f"document number {actual.doc_number} ≠ {doc_number}")
    if actual.amount != expected.amount:
        problems.append(f"posted amount {actual.amount} ≠ approved {expected.amount}")
    if actual.tax_amount != expected.tax_amount:
        problems.append(f"posted tax {actual.tax_amount} ≠ approved {expected.tax_amount}")
    if actual.status != "POSTED":
        problems.append(f"status {actual.status}, expected POSTED")
    if not actual.attachment_url:
        problems.append("no attachment on the posted document")
    for name in ("vendor_id", "invoice_number", "gl_account", "cost_centre"):
        if getattr(actual, name) != getattr(expected, name):
            problems.append(
                f"{name.replace('_', ' ')} {getattr(actual, name)} ≠ {getattr(expected, name)}"
            )
    return problems
