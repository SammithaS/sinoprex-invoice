"""Read-only reference data: vendors, purchase orders, goods receipts."""

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.schemas import (
    GoodsReceiptOut,
    GrnLineOut,
    PoLineOut,
    PurchaseOrderOut,
    VendorOut,
)
from app.db.models import GoodsReceipt, GrnLine, PurchaseOrder, Vendor
from app.deps import SessionDep

router = APIRouter(prefix="/reference", tags=["reference"])


@router.get("/vendors", response_model=list[VendorOut])
async def list_vendors(session: SessionDep) -> list[Vendor]:
    result = await session.scalars(select(Vendor).order_by(Vendor.name))
    return list(result)


@router.get("/pos", response_model=list[PurchaseOrderOut])
async def list_purchase_orders(session: SessionDep) -> list[PurchaseOrderOut]:
    result = await session.scalars(
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.vendor))
        .order_by(PurchaseOrder.po_number)
    )
    return [
        PurchaseOrderOut(
            po_number=po.po_number,
            vendor_id=po.vendor_id,
            vendor_name=po.vendor.name,
            po_date=po.po_date,
            status=po.status,
            subtotal=po.subtotal,
            tax_total=po.tax_total,
            total=po.total,
            amount_invoiced=po.amount_invoiced,
            remaining=po.total - po.amount_invoiced,
            requires_grn=po.requires_grn,
            cost_centre=po.cost_centre,
            buyer=po.buyer,
            lines=[PoLineOut.model_validate(line) for line in po.lines],
        )
        for po in result
    ]


@router.get("/grns", response_model=list[GoodsReceiptOut])
async def list_goods_receipts(session: SessionDep) -> list[GoodsReceiptOut]:
    result = await session.scalars(
        select(GoodsReceipt)
        .options(
            selectinload(GoodsReceipt.purchase_order).selectinload(PurchaseOrder.vendor),
            selectinload(GoodsReceipt.lines).selectinload(GrnLine.po_line),
        )
        .order_by(GoodsReceipt.grn_number)
    )
    return [
        GoodsReceiptOut(
            grn_number=grn.grn_number,
            po_number=grn.purchase_order.po_number,
            vendor_name=grn.purchase_order.vendor.name,
            received_date=grn.received_date,
            received_by=grn.received_by,
            lines=[
                GrnLineOut(
                    po_line_no=gl.po_line.line_no,
                    description=gl.po_line.description,
                    qty_received=gl.qty_received,
                )
                for gl in sorted(grn.lines, key=lambda gl: gl.po_line.line_no)
            ],
        )
        for grn in result
    ]
