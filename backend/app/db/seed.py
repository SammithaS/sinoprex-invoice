"""Load data/seed/*.json into the database. Replaces existing reference data."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GoodsReceipt, GrnLine, Invoice, PoLine, PurchaseOrder, Vendor

DEFAULT_SEED_DIR = Path(__file__).resolve().parents[3] / "data" / "seed"


def _load(seed_dir: Path, name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((seed_dir / f"{name}.json").read_text("utf-8"))
    return data


async def seed_reference_data(
    session: AsyncSession, seed_dir: Path = DEFAULT_SEED_DIR
) -> dict[str, int]:
    # Children first, so foreign keys never dangle.
    for model in (GrnLine, GoodsReceipt, Invoice, PoLine, PurchaseOrder, Vendor):
        await session.execute(delete(model))

    vendors = _load(seed_dir, "vendors")
    session.add_all(
        Vendor(**{k: v for k, v in row.items() if k != "id"}, id=row["id"]) for row in vendors
    )
    await session.flush()

    lines_by_po: dict[str, dict[int, PoLine]] = {}
    pos = _load(seed_dir, "purchase_orders")
    for row in pos:
        po = PurchaseOrder(
            po_number=row["po_number"],
            vendor_id=row["vendor_id"],
            po_date=date.fromisoformat(row["po_date"]),
            status=row["status"],
            requires_grn=row["requires_grn"],
            cost_centre=row["cost_centre"],
            buyer=row["buyer"],
            subtotal=Decimal(row["subtotal"]),
            tax_total=Decimal(row["tax_total"]),
            total=Decimal(row["total"]),
            amount_invoiced=Decimal(row["amount_invoiced"]),
        )
        po.lines = [
            PoLine(
                line_no=ln["line_no"],
                description=ln["description"],
                hsn_sac=ln["hsn_sac"],
                qty=Decimal(ln["qty"]),
                uom=ln["uom"],
                unit_price=Decimal(ln["unit_price"]),
                tax_rate=Decimal(ln["tax_rate"]),
            )
            for ln in row["lines"]
        ]
        session.add(po)
        lines_by_po[po.po_number] = {ln.line_no: ln for ln in po.lines}
    await session.flush()

    po_ids = {
        po_number: next(iter(lines.values())).po_id for po_number, lines in lines_by_po.items()
    }
    grns = _load(seed_dir, "goods_receipts")
    for row in grns:
        po_lines = lines_by_po[row["po_number"]]
        grn = GoodsReceipt(
            grn_number=row["grn_number"],
            po_id=po_ids[row["po_number"]],
            received_date=date.fromisoformat(row["received_date"]),
            received_by=row["received_by"],
        )
        grn.lines = [
            GrnLine(po_line_id=po_lines[gl["line_no"]].id, qty_received=Decimal(gl["qty_received"]))
            for gl in row["lines"]
        ]
        session.add(grn)

    invoices = _load(seed_dir, "invoices")
    session.add_all(
        Invoice(
            vendor_id=row["vendor_id"],
            invoice_number=row["invoice_number"],
            invoice_date=date.fromisoformat(row["invoice_date"]),
            po_number=row["po_number"],
            subtotal=Decimal(row["subtotal"]),
            tax_total=Decimal(row["tax_total"]),
            total=Decimal(row["total"]),
            gl_account=row["gl_account"],
            cost_centre=row["cost_centre"],
            status=row["status"],
            source="SEED",
        )
        for row in invoices
    )
    await session.commit()
    return {
        "vendors": len(vendors),
        "purchase_orders": len(pos),
        "goods_receipts": len(grns),
        "invoices": len(invoices),
    }
