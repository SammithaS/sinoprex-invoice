"""Policy numbers the rules use (ap-policy.md).

These are the defaults the Operating Procedure starts from; the graph will load the
active procedure version into a `Policy` at the start of each case.
"""

from decimal import Decimal

from pydantic import BaseModel, Field

from app.rules.models import ExceptionCategory


class ApprovalBand(BaseModel):
    up_to: Decimal | None  # None = no upper limit
    roles: list[str]


class Policy(BaseModel):
    version: str = "default"
    price_tolerance_pct: Decimal = Decimal("2")
    price_tolerance_invoice: Decimal = Decimal("5000")
    qty_tolerance: Decimal = Decimal("0")
    freight_cap: Decimal = Decimal("1000")
    rounding: Decimal = Decimal("1")
    duplicate_window_days: int = 30
    confidence_threshold: float = 0.8
    po_required_above: Decimal = Decimal("10000")
    po_inference_threshold: float = 0.75
    approval_matrix: list[ApprovalBand] = Field(
        default_factory=lambda: [
            ApprovalBand(up_to=Decimal("500000"), roles=[]),
            ApprovalBand(up_to=Decimal("2500000"), roles=["Finance Manager"]),
            ApprovalBand(up_to=None, roles=["Finance Manager", "CFO"]),
        ]
    )
    non_po_finance_above: Decimal = Decimal("100000")
    non_po_categories: list[str] = Field(
        default_factory=lambda: ["SaaS", "Utilities", "Rent", "Professional services"]
    )
    bank_change_approvers: list[str] = Field(
        default_factory=lambda: ["AP Manager", "Finance Controller"]
    )
    approvers: dict[str, str] = Field(
        default_factory=lambda: {
            "AP Clerk": "Priya Nair",
            "AP Manager": "Arjun Desai",
            "Finance Manager": "Meera Krishnan",
            "Finance Controller": "Sanjay Batra",
            "CFO": "Kavita Reddy",
            "Tax team": "Farhan Qureshi",
            "Vendor Master team": "Lakshmi Pillai",
            "Department Head — IT": "Rahul Menon",
            "Department Head — Admin": "Anita Rao",
            "Department Head — Operations": "Vikram Iyer",
            "Department Head — Marketing": "Sneha Kulkarni",
            "Department Head — Facilities": "Imran Sheikh",
        }
    )
    cost_centre_departments: dict[str, str] = Field(
        default_factory=lambda: {
            "CC-IT-01": "IT",
            "CC-ADM-02": "Admin",
            "CC-OPS-03": "Operations",
            "CC-MKT-04": "Marketing",
            "CC-FAC-05": "Facilities",
        }
    )
    # §8.1 — "Buyer" means the buyer named on the PO; "Receiver" the store on the GRN/PO.
    exception_owners: dict[ExceptionCategory, str] = Field(
        default_factory=lambda: {
            ExceptionCategory.PRICE_VARIANCE: "Buyer",
            ExceptionCategory.QTY_VARIANCE: "Receiving store",
            ExceptionCategory.NO_PO: "AP Clerk",
            ExceptionCategory.NO_RECEIPT: "Receiver",
            ExceptionCategory.DUPLICATE_SUSPECT: "AP Clerk",
            ExceptionCategory.BANK_CHANGE: "Vendor Master team",
            ExceptionCategory.TAX_MISMATCH: "Tax team",
            ExceptionCategory.LOW_CONFIDENCE: "AP Clerk",
            ExceptionCategory.OVER_PO_BALANCE: "Buyer",
            ExceptionCategory.ERP_MISMATCH: "AP Manager",
        }
    )
    # §7.3: approving despite these needs a written reason and a second approver.
    override_categories: list[ExceptionCategory] = Field(
        default_factory=lambda: [
            ExceptionCategory.PRICE_VARIANCE,
            ExceptionCategory.QTY_VARIANCE,
            ExceptionCategory.OVER_PO_BALANCE,
            ExceptionCategory.DUPLICATE_SUSPECT,
            ExceptionCategory.TAX_MISMATCH,
        ]
    )
    override_second_approver: str = "AP Manager"
    # GL account for PO-backed postings, by vendor category (fictional chart of accounts).
    gl_by_category: dict[str, str] = Field(
        default_factory=lambda: {
            "IT hardware": "5110",
            "Electrical": "5120",
            "Furniture": "5130",
            "Packaging": "5210",
            "Stationery": "6110",
            "Freight": "6310",
            "Printing": "6510",
            "Facility services": "6610",
            "SaaS": "6420",
            "General trading": "5900",
        }
    )
    default_gl: str = "5999"

    def department_head(self, cost_centre: str | None) -> str:
        dept = self.cost_centre_departments.get(cost_centre or "")
        return f"Department Head — {dept}" if dept else "Department Head"


DEFAULT_POLICY = Policy()
