"""ERPNext (open-source ERP, Frappe REST API) behind the same `ErpProvider` interface.

- `post()` creates and submits a Purchase Invoice: one line for the net amount (the GL account
  and cost centre we coded) plus one "Actual" tax row, so ERPNext's grand total equals the
  approved invoice total. The case id goes into the remarks; a retried post finds the document
  by it (or by supplier + bill number) instead of creating a second one.
- `read()` fetches the document from ERPNext and maps it back (supplier GSTIN → vendor id,
  account number → GL code, cost centre without the company suffix), so the existing read-back
  `compare()` checks what ERPNext really holds.
- A local copy of every posted document is kept in `erp_documents` (system "erpnext"), so the
  ledger, the payables queue and the dashboard work the same as with the mock ERP.
- `sync_masters()` creates what the postings need in ERPNext (suppliers, cost centres, expense
  accounts, the AP item, the input-tax account); `pay()` records a vendor payment as a submitted
  Payment Entry against the Purchase Invoice.

Auth: an ERPNext API key + secret (`Authorization: token <key>:<secret>`).
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ErpDocument, PurchaseOrder, Vendor
from app.erp import ErpDocumentIn, ErpDocumentOut
from app.rules.policy import Policy

PI = "Purchase Invoice"
_REF = re.compile(r"Sinoprex case (\S+)")
_PO = re.compile(r"PO (\S+)")


class ErpNextError(RuntimeError):
    """ERPNext refused a request (auth, validation, missing master data …)."""


class ErpNextClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        client: Callable[[], httpx.AsyncClient],
    ) -> None:
        self.base = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"token {api_key}:{api_secret}",
            "Accept": "application/json",
        }
        self.client = client

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        async with self.client() as c:
            res = await c.request(
                method, f"{self.base}{path}", params=params, json=body, headers=self.headers
            )
        try:
            data = res.json()
        except ValueError:
            data = {}
        if res.status_code >= 400:
            raise ErpNextError(f"ERPNext: {_error_text(data) or f'HTTP {res.status_code}'}")
        return data.get("data", data.get("message"))

    async def whoami(self) -> str:
        return str(await self._call("GET", "/api/method/frappe.auth.get_logged_user"))

    async def get(self, doctype: str, name: str) -> dict[str, Any] | None:
        try:
            doc: dict[str, Any] = await self._call("GET", f"/api/resource/{doctype}/{name}")
        except ErpNextError as exc:
            if "not found" in str(exc).lower() or "404" in str(exc):
                return None
            raise
        return doc

    async def find(
        self,
        doctype: str,
        filters: list[list[Any]],
        fields: tuple[str, ...] = ("name",),
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = await self._call(
            "GET",
            f"/api/resource/{doctype}",
            params={
                "filters": json.dumps(filters),
                "fields": json.dumps(list(fields)),
                "limit_page_length": limit,
            },
        )
        return rows or []

    async def insert(self, doctype: str, doc: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = await self._call("POST", f"/api/resource/{doctype}", body=doc)
        return out

    async def method(self, name: str, **args: Any) -> Any:
        return await self._call("POST", f"/api/method/{name}", body=args)


def _error_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    msgs = data.get("_server_messages")
    if isinstance(msgs, str):
        try:
            first = json.loads(json.loads(msgs)[0])
            return str(first.get("message", ""))[:300]
        except (ValueError, IndexError, TypeError, AttributeError):
            pass
    return str(data.get("exception") or data.get("message") or "")[:300]


class ErpNextErp:
    """`ErpProvider` on ERPNext; keeps the local `erp_documents` copy in step."""

    def __init__(
        self,
        client: ErpNextClient,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        company: str,
        item_code: str,
        tax_account: str,
        app_url: str = "",
    ) -> None:
        self.c = client
        self.sessionmaker = sessionmaker
        self.company = company
        self.item_code = item_code
        self.tax_account = tax_account
        self.app_url = app_url.rstrip("/")
        self._abbr: str | None = None
        self._accounts: dict[str, str] = {}  # GL code → account name (and back)

    # --- master data lookups ---------------------------------------------------------------------

    async def abbr(self) -> str:
        if self._abbr is None:
            company = await self.c.get("Company", self.company)
            if company is None:
                raise ErpNextError(f"company '{self.company}' not found in ERPNext")
            self._abbr = str(company["abbr"])
        return self._abbr

    async def supplier(self, vendor: Vendor) -> str:
        rows = await self.c.find("Supplier", [["tax_id", "=", vendor.gstin]])
        if not rows:
            raise ErpNextError(
                f"supplier {vendor.legal_name} (GSTIN {vendor.gstin}) is not in ERPNext; "
                "run 'Sync master data' in the ERPNext Connector app"
            )
        return str(rows[0]["name"])

    async def account(self, gl_code: str) -> str:
        if gl_code not in self._accounts:
            rows = await self.c.find(
                "Account",
                [["account_number", "=", gl_code], ["company", "=", self.company]],
            )
            if not rows:
                raise ErpNextError(f"no ERPNext account with number {gl_code}; sync master data")
            self._accounts[gl_code] = str(rows[0]["name"])
        return self._accounts[gl_code]

    async def tax_head(self) -> str:
        """The input-tax account, given with or without the company suffix."""
        suffix = f" - {await self.abbr()}"
        return self.tax_account if self.tax_account.endswith(suffix) else self.tax_account + suffix

    async def cost_center(self, code: str | None) -> str:
        return f"{code or 'Main'} - {await self.abbr()}"

    # --- ErpProvider ----------------------------------------------------------------------------

    async def _existing(self, doc: ErpDocumentIn, supplier: str) -> dict[str, Any] | None:
        by_case: list[list[Any]] = [["remarks", "like", f"%Sinoprex case {doc.idempotency_key}%"]]
        by_bill: list[list[Any]] = [
            ["supplier", "=", supplier],
            ["bill_no", "=", doc.invoice_number],
        ]
        for filters in (by_case, by_bill):
            filters.append(["docstatus", "=", 1])
            rows = await self.c.find(PI, filters)
            if rows:
                return await self.c.get(PI, str(rows[0]["name"]))
        return None

    async def post(self, doc: ErpDocumentIn, fault: str | None = None) -> ErpDocumentOut:
        async with self.sessionmaker() as session:
            vendor = await session.get(Vendor, doc.vendor_id)
        if vendor is None:
            raise ErpNextError(f"vendor {doc.vendor_id} not in the vendor master")
        supplier = await self.supplier(vendor)
        existing = await self._existing(doc, supplier)
        created = existing is None
        if existing is None:
            cc = await self.cost_center(doc.cost_centre)
            remarks = f"Sinoprex case {doc.idempotency_key}"
            if doc.po_number:
                remarks += f"; PO {doc.po_number}"
            existing = await self.c.insert(
                PI,
                {
                    "supplier": supplier,
                    "company": self.company,
                    "bill_no": doc.invoice_number,
                    "bill_date": str(doc.invoice_date),
                    "currency": doc.currency,
                    "update_stock": 0,
                    "disable_rounded_total": 1,
                    "remarks": remarks,
                    "items": [
                        {
                            "item_code": self.item_code,
                            "qty": 1,
                            "rate": float(doc.amount - doc.tax_amount),
                            "expense_account": await self.account(doc.gl_account or ""),
                            "cost_center": cc,
                        }
                    ],
                    "taxes": [
                        {
                            "charge_type": "Actual",
                            "account_head": await self.tax_head(),
                            "description": "GST (input)",
                            "tax_amount": float(doc.tax_amount),
                            "category": "Total",
                            "add_deduct_tax": "Add",
                            "cost_center": cc,
                        }
                    ],
                    "docstatus": 1,  # insert and submit
                },
            )
            if doc.attachment_url:
                await self.c.insert(
                    "File",
                    {
                        "file_url": self._absolute(doc.attachment_url),
                        "file_name": f"{doc.invoice_number.replace('/', '-')}.pdf",
                        "attached_to_doctype": PI,
                        "attached_to_name": existing["name"],
                        "is_private": 1,
                    },
                )
        out = await self._to_out(existing, created=created, expected=doc)
        await self._mirror(out, doc)
        return out

    async def read(self, doc_number: str) -> ErpDocumentOut | None:
        pi = await self.c.get(PI, doc_number)
        if pi is None:
            return None
        out = await self._to_out(pi, created=False)
        async with self.sessionmaker() as session:
            row = await session.scalar(
                select(ErpDocument).where(ErpDocument.doc_number == doc_number)
            )
            if row is not None:  # keep the local copy in step with what ERPNext holds
                row.amount, row.tax_amount = out.amount, out.tax_amount
                if out.status == "PAID" or row.status != "PAID":
                    row.status = out.status
                row.updated_at = datetime.now(UTC).replace(tzinfo=None)
                await session.commit()
        return out

    def _absolute(self, url: str) -> str:
        return url if url.startswith("http") or not self.app_url else f"{self.app_url}{url}"

    async def _to_out(
        self, pi: dict[str, Any], *, created: bool, expected: ErpDocumentIn | None = None
    ) -> ErpDocumentOut:
        remarks = str(pi.get("remarks") or "")
        ref = _REF.search(remarks)
        po = _PO.search(remarks)
        items = pi.get("items") or [{}]
        vendor_id = await self._vendor_id(str(pi.get("supplier") or ""))
        gl = await self._gl_code(str(items[0].get("expense_account") or ""))
        abbr = await self.abbr()
        cc = str(items[0].get("cost_center") or "").removesuffix(f" - {abbr}") or None
        files = await self.c.find(
            "File",
            [["attached_to_doctype", "=", PI], ["attached_to_name", "=", pi["name"]]],
            fields=("name", "file_url"),
        )
        attachment = None
        if files:
            url = str(files[0].get("file_url") or "")
            attachment = url.removeprefix(self.app_url) if self.app_url else url
        status = {0: "DRAFT", 2: "CANCELLED"}.get(int(pi.get("docstatus") or 0), "POSTED")
        if status == "POSTED" and str(pi.get("status")) == "Paid":
            status = "PAID"
        return ErpDocumentOut(
            idempotency_key=ref.group(1).rstrip(";") if ref else "",
            vendor_id=vendor_id,
            invoice_number=str(pi.get("bill_no") or ""),
            invoice_date=date.fromisoformat(str(pi.get("bill_date"))),
            po_number=po.group(1) if po else None,
            amount=Decimal(str(pi.get("grand_total") or 0)).quantize(Decimal("0.01")),
            tax_amount=Decimal(str(pi.get("total_taxes_and_charges") or 0)).quantize(
                Decimal("0.01")
            ),
            currency=str(pi.get("currency") or "INR"),
            gl_account=gl,
            cost_centre=cc,
            attachment_url=attachment,
            payload=expected.payload if expected else None,
            doc_number=str(pi["name"]),
            status=status,
            created=created,
        )

    async def _vendor_id(self, supplier: str) -> str:
        sup = await self.c.get("Supplier", supplier) if supplier else None
        gstin = (sup or {}).get("tax_id")
        async with self.sessionmaker() as session:
            vid = await session.scalar(select(Vendor.id).where(Vendor.gstin == gstin))
        return vid or supplier

    async def _gl_code(self, account: str) -> str | None:
        for known, name in self._accounts.items():
            if name == account:
                return known
        acc = await self.c.get("Account", account) if account else None
        number = (acc or {}).get("account_number")
        if not number:
            return None
        self._accounts[str(number)] = account
        return str(number)

    async def _mirror(self, out: ErpDocumentOut, doc: ErpDocumentIn) -> None:
        """The local copy the ledger and payments use (what ERPNext returned, not what we sent)."""
        async with self.sessionmaker() as session:
            row = await session.scalar(
                select(ErpDocument).where(ErpDocument.doc_number == out.doc_number)
            )
            if row is None:
                row = await session.scalar(
                    select(ErpDocument).where(ErpDocument.idempotency_key == doc.idempotency_key)
                )
            if row is None:
                row = ErpDocument(doc_number=out.doc_number, idempotency_key=doc.idempotency_key)
                session.add(row)
            row.system = "erpnext"
            row.doc_number = out.doc_number
            row.vendor_id, row.invoice_number = doc.vendor_id, doc.invoice_number
            row.invoice_date, row.po_number = doc.invoice_date, doc.po_number
            row.amount, row.tax_amount, row.currency = out.amount, out.tax_amount, out.currency
            row.gl_account, row.cost_centre = out.gl_account, out.cost_centre
            row.attachment_url = doc.attachment_url
            row.status = out.status if row.status != "PAID" else row.status
            row.payload = doc.payload
            row.updated_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()

    # --- payments -------------------------------------------------------------------------------

    async def pay(self, doc_number: str, utr: str, paid_on: date) -> str:
        """Record a bank payment as a submitted Payment Entry (once per UTR)."""
        existing = await self.c.find(
            "Payment Entry", [["reference_no", "=", utr], ["docstatus", "=", 1]]
        )
        if existing:
            return str(existing[0]["name"])
        entry: dict[str, Any] = await self.c.method(
            "erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry",
            dt=PI,
            dn=doc_number,
        )
        entry.update({"reference_no": utr, "reference_date": str(paid_on), "docstatus": 1})
        entry.pop("name", None)
        done = await self.c.insert("Payment Entry", entry)
        return str(done["name"])

    # --- master data ----------------------------------------------------------------------------

    async def sync_masters(self, policy: Policy) -> dict[str, int]:
        """Create (never change) the suppliers, cost centres and accounts postings need."""
        abbr = await self.abbr()
        created = {"suppliers": 0, "cost_centres": 0, "accounts": 0, "items": 0}
        async with self.sessionmaker() as session:
            vendors = list(await session.scalars(select(Vendor).order_by(Vendor.id)))
            po_ccs = set(await session.scalars(select(PurchaseOrder.cost_centre)))

        groups = await self.c.find("Supplier Group", [["is_group", "=", 0]])
        group = str(groups[0]["name"]) if groups else "All Supplier Groups"
        for v in vendors:
            if not await self.c.find("Supplier", [["tax_id", "=", v.gstin]]):
                await self.c.insert(
                    "Supplier",
                    {
                        "supplier_name": v.legal_name,
                        "supplier_group": group,
                        "supplier_type": "Company",
                        "tax_id": v.gstin,
                        "country": "India",
                        "disabled": 1 if v.status != "ACTIVE" else 0,
                    },
                )
                created["suppliers"] += 1

        root_cc = f"{self.company} - {abbr}"
        for cc in sorted(po_ccs | set(policy.cost_centre_departments)):
            if await self.c.get("Cost Center", f"{cc} - {abbr}") is None:
                await self.c.insert(
                    "Cost Center",
                    {
                        "cost_center_name": cc,
                        "parent_cost_center": root_cc,
                        "company": self.company,
                        "is_group": 0,
                    },
                )
                created["cost_centres"] += 1

        expense_parent = await self._group_account("Expense")
        names = {code: cat for cat, code in policy.gl_by_category.items()}
        names.setdefault(policy.default_gl, "Other purchases")
        for code, label in sorted(names.items()):
            found = await self.c.find(
                "Account", [["account_number", "=", code], ["company", "=", self.company]]
            )
            if not found:
                await self.c.insert(
                    "Account",
                    {
                        "account_name": label,
                        "account_number": code,
                        "parent_account": expense_parent,
                        "company": self.company,
                        "root_type": "Expense",
                        "account_type": "Expense Account",
                        "is_group": 0,
                    },
                )
                created["accounts"] += 1

        tax_head = await self.tax_head()
        if await self.c.get("Account", tax_head) is None:
            parent = await self._group_account("Liability", like="Duties and Taxes%")
            await self.c.insert(
                "Account",
                {
                    "account_name": tax_head.removesuffix(f" - {abbr}"),
                    "parent_account": parent,
                    "company": self.company,
                    "root_type": "Liability",
                    "account_type": "Tax",
                    "is_group": 0,
                },
            )
            created["accounts"] += 1

        if await self.c.get("Item", self.item_code) is None:
            await self.c.insert(
                "Item",
                {
                    "item_code": self.item_code,
                    "item_name": "Supplier invoice (AP)",
                    "item_group": "Services",
                    "stock_uom": "Nos",
                    "is_stock_item": 0,
                    "is_purchase_item": 1,
                    "is_sales_item": 0,
                },
            )
            created["items"] += 1
        return created

    async def _group_account(self, root_type: str, like: str | None = None) -> str:
        filters: list[list[Any]] = [
            ["company", "=", self.company],
            ["root_type", "=", root_type],
            ["is_group", "=", 1],
        ]
        if like:
            filters.append(["account_name", "like", like])
        rows = await self.c.find("Account", filters, fields=("name", "parent_account"))
        # The deepest group is the safest parent (not the root of the chart).
        rows.sort(key=lambda r: bool(r.get("parent_account")), reverse=True)
        if not rows:
            raise ErpNextError(f"no {root_type} group account in {self.company}")
        return str(rows[0]["name"])
