"""An in-memory ERPNext (Frappe REST API) for tests: just the resources and methods we use."""

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx

COMPANY, ABBR = "Sinoprex Demo", "SD"
BASE = "https://erp.example.com"


def _like(value: str, pattern: str) -> bool:
    rx = "^" + ".*".join(re.escape(p) for p in pattern.split("%")) + "$"
    return re.match(rx, value, re.S) is not None


@dataclass
class FakeErpNext:
    docs: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    shift_grand_total: float = 0.0  # simulate ERPNext storing a different total
    counters: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.put("Company", {"name": COMPANY, "abbr": ABBR})
        self.put("Supplier Group", {"name": "All Supplier Groups", "is_group": 1})
        self.put("Supplier Group", {"name": "Services", "is_group": 0})
        for name, root, parent in (
            (f"Expenses - {ABBR}", "Expense", None),
            (f"Indirect Expenses - {ABBR}", "Expense", f"Expenses - {ABBR}"),
            (f"Liabilities - {ABBR}", "Liability", None),
            (f"Duties and Taxes - {ABBR}", "Liability", f"Liabilities - {ABBR}"),
        ):
            self.put(
                "Account",
                {
                    "name": name,
                    "account_name": name.removesuffix(f" - {ABBR}"),
                    "root_type": root,
                    "is_group": 1,
                    "company": COMPANY,
                    "parent_account": parent,
                },
            )
        self.put("Cost Center", {"name": f"{COMPANY} - {ABBR}", "is_group": 1})

    def put(self, doctype: str, doc: dict[str, Any]) -> dict[str, Any]:
        self.docs.setdefault(doctype, {})[doc["name"]] = doc
        return doc

    def all(self, doctype: str) -> list[dict[str, Any]]:
        return list(self.docs.get(doctype, {}).values())

    def _next(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}-2026-{self.counters[prefix]:05d}"

    def _name(self, doctype: str, doc: dict[str, Any]) -> str:
        if doctype == "Purchase Invoice":
            return self._next("ACC-PINV")
        if doctype == "Payment Entry":
            return self._next("ACC-PAY")
        if doctype == "Supplier":
            return str(doc["supplier_name"])
        if doctype == "Cost Center":
            return f"{doc['cost_center_name']} - {ABBR}"
        if doctype == "Account":
            number = f"{doc['account_number']} - " if doc.get("account_number") else ""
            return f"{number}{doc['account_name']} - {ABBR}"
        if doctype == "Item":
            return str(doc["item_code"])
        return self._next(doctype.lower())

    def _insert(self, doctype: str, doc: dict[str, Any]) -> dict[str, Any]:
        doc = {**doc, "name": self._name(doctype, doc)}
        if doctype == "Purchase Invoice":
            net = sum(float(i["rate"]) * float(i["qty"]) for i in doc["items"])
            tax = sum(float(t["tax_amount"]) for t in doc.get("taxes", []))
            doc.update(
                grand_total=round(net + tax + self.shift_grand_total, 2),
                total_taxes_and_charges=round(tax, 2),
                status="Unpaid",
            )
        if doctype == "Payment Entry" and doc.get("docstatus") == 1:
            for ref in doc["references"]:
                self.docs["Purchase Invoice"][ref["reference_name"]]["status"] = "Paid"
        return self.put(doctype, doc)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != "token key-123:secret-456":
            return httpx.Response(401, json={"exception": "AuthenticationError: bad token"})
        path = unquote(request.url.path)
        body = json.loads(request.content) if request.content else {}
        if path == "/api/method/frappe.auth.get_logged_user":
            return httpx.Response(200, json={"message": "sinoprex@example.com"})
        if path.endswith("get_payment_entry"):
            pi = self.docs["Purchase Invoice"][body["dn"]]
            return httpx.Response(
                200,
                json={
                    "message": {
                        "doctype": "Payment Entry",
                        "payment_type": "Pay",
                        "party_type": "Supplier",
                        "party": pi["supplier"],
                        "paid_amount": pi["grand_total"],
                        "references": [
                            {
                                "reference_doctype": "Purchase Invoice",
                                "reference_name": pi["name"],
                                "allocated_amount": pi["grand_total"],
                            }
                        ],
                    }
                },
            )
        m = re.fullmatch(r"/api/resource/([^/]+)(?:/(.+))?", path)
        if not m:
            return httpx.Response(404, json={"exception": "no route"})
        doctype, name = m.group(1), m.group(2)
        if request.method == "POST" and name is None:
            return httpx.Response(200, json={"data": self._insert(doctype, body)})
        if name is not None:
            doc = self.docs.get(doctype, {}).get(name)
            if doc is None:
                return httpx.Response(
                    404, json={"exception": f"DoesNotExistError: {doctype} {name} not found"}
                )
            return httpx.Response(200, json={"data": doc})
        filters = json.loads(request.url.params.get("filters", "[]"))
        fields = json.loads(request.url.params.get("fields", '["name"]'))
        rows = [
            d
            for d in self.all(doctype)
            if all(
                _like(str(d.get(f) or ""), str(v)) if op == "like" else d.get(f) == v
                for f, op, v in filters
            )
        ]
        return httpx.Response(200, json={"data": [{k: d.get(k) for k in fields} for d in rows]})
