"""Database access the graph needs: reference data in, case history out."""

import time
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import GoodsReceipt, Invoice, PurchaseOrder, Vendor
from app.extraction.models import InvoiceData
from app.rules import models as rm

COMPANY_GSTIN = "29AACCA4821K1ZM"
COMPANY_STATE = "29"


# Vendors, POs and GRNs change rarely (seed, master-data edits); invoice history changes
# with every case. Cache the former briefly, always read the latter.
MASTER_TTL_SECONDS = 30.0
_master: tuple[float, rm.ReferenceData] | None = None


def clear_reference_cache() -> None:
    global _master
    _master = None


async def load_reference(session: AsyncSession) -> rm.ReferenceData:
    """Vendors, POs, GRNs and the live invoice history, as the rules engine sees them."""
    global _master
    now = time.monotonic()
    if _master is None or now - _master[0] > MASTER_TTL_SECONDS:
        _master = (now, await _load_master(session))
    invoices = (await session.scalars(select(Invoice))).all()
    return _master[1].model_copy(update={"invoices": [_record(i) for i in invoices]})


async def _load_master(session: AsyncSession) -> rm.ReferenceData:
    vendors = (await session.scalars(select(Vendor))).all()
    pos = (
        await session.scalars(select(PurchaseOrder).options(selectinload(PurchaseOrder.lines)))
    ).all()
    grns = (
        await session.scalars(
            select(GoodsReceipt).options(
                selectinload(GoodsReceipt.lines),
                selectinload(GoodsReceipt.purchase_order),
            )
        )
    ).all()
    line_no = {line.id: line.line_no for po in pos for line in po.lines}
    return rm.ReferenceData(
        company_gstin=COMPANY_GSTIN,
        company_state_code=COMPANY_STATE,
        vendors=[
            rm.Vendor(
                id=v.id,
                name=v.name,
                legal_name=v.legal_name,
                aliases=list(v.aliases or []),
                gstin=v.gstin,
                state_code=v.state_code,
                bank_account_last4=v.bank_account_last4,
                bank_ifsc=v.bank_ifsc,
                category=v.category,
                email=v.email,
                status="BLOCKED" if v.status == "BLOCKED" else "ACTIVE",
            )
            for v in vendors
        ],
        purchase_orders=[
            rm.PurchaseOrder(
                po_number=p.po_number,
                vendor_id=p.vendor_id,
                po_date=p.po_date,
                status=p.status,
                requires_grn=p.requires_grn,
                cost_centre=p.cost_centre,
                buyer=p.buyer,
                total=p.total,
                lines=[
                    rm.POLine(
                        line_no=ln.line_no,
                        description=ln.description,
                        hsn_sac=ln.hsn_sac,
                        qty=ln.qty,
                        uom=ln.uom,
                        unit_price=ln.unit_price,
                        tax_rate=ln.tax_rate,
                    )
                    for ln in p.lines
                ],
            )
            for p in pos
        ],
        goods_receipts=[
            rm.GoodsReceipt(
                grn_number=g.grn_number,
                po_number=g.purchase_order.po_number,
                received_date=g.received_date,
                received_by=g.received_by,
                lines=[
                    rm.GRNLine(line_no=line_no[gl.po_line_id], qty_received=gl.qty_received)
                    for gl in g.lines
                ],
            )
            for g in grns
        ],
        invoices=[],
    )


def _record(i: Invoice) -> rm.InvoiceRecord:
    return rm.InvoiceRecord(
        ref=i.run_id or i.invoice_number,
        vendor_id=i.vendor_id,
        invoice_number=i.invoice_number,
        invoice_date=i.invoice_date,
        total=i.total,
        po_number=i.po_number,
        status=i.status,
        file_hash=i.file_hash,
        service_period=i.service_period,
        gl_account=i.gl_account,
        cost_centre=i.cost_centre,
        lines=[rm.InvoicedLine.model_validate(x) for x in i.po_lines]
        if i.po_lines is not None
        else None,
    )


async def register_invoice(
    session: AsyncSession,
    run_id: str,
    record: rm.InvoiceRecord,
    data: InvoiceData,
    file_url: str | None,
) -> None:
    """Upsert this run's row in the invoice history (idempotent per run)."""
    row = await session.scalar(select(Invoice).where(Invoice.run_id == run_id))
    if row is None:
        row = Invoice(run_id=run_id, source="RUN")
        session.add(row)
    row.vendor_id = record.vendor_id
    row.invoice_number = record.invoice_number
    row.invoice_date = record.invoice_date
    row.po_number = record.po_number
    row.subtotal = data.subtotal or Decimal(0)
    row.tax_total = data.tax_total or Decimal(0)
    row.total = record.total
    row.file_hash = record.file_hash
    row.file_url = file_url
    row.status = record.status
    row.service_period = record.service_period
    row.po_lines = [ln.model_dump(mode="json") for ln in record.lines or []]
    row.extracted = data.model_dump(mode="json")
    await session.commit()


async def set_invoice_status(session: AsyncSession, run_id: str, status: str) -> None:
    await session.execute(update(Invoice).where(Invoice.run_id == run_id).values(status=status))
    await session.commit()
