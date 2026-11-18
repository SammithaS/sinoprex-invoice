"""Build `ReferenceData` from the seed JSON (tests, scripts). Phase 6 loads it from the DB."""

import json
from pathlib import Path
from typing import Any

from app.rules.models import GoodsReceipt, InvoiceRecord, PurchaseOrder, ReferenceData, Vendor

SEED_DIR = Path(__file__).resolve().parents[3] / "data" / "seed"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_seed_reference(seed_dir: Path = SEED_DIR) -> ReferenceData:
    company = _load(seed_dir / "company.json")
    return ReferenceData(
        company_gstin=company["gstin"],
        company_state_code=company["state_code"],
        vendors=[Vendor.model_validate(v) for v in _load(seed_dir / "vendors.json")],
        purchase_orders=[
            PurchaseOrder.model_validate(p) for p in _load(seed_dir / "purchase_orders.json")
        ],
        goods_receipts=[
            GoodsReceipt.model_validate(g) for g in _load(seed_dir / "goods_receipts.json")
        ],
        invoices=[
            InvoiceRecord.model_validate({**i, "ref": i["invoice_number"]})
            for i in _load(seed_dir / "invoices.json")
        ],
    )
