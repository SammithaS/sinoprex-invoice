from decimal import Decimal

from httpx import AsyncClient


async def test_health(client: AsyncClient) -> None:
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "env": "test", "database": "ok"}


async def test_api_key_required(client: AsyncClient) -> None:
    res = await client.get("/reference/vendors", headers={"X-API-Key": "wrong"})
    assert res.status_code == 401


async def test_vendors(client: AsyncClient) -> None:
    res = await client.get("/reference/vendors")
    assert res.status_code == 200
    vendors = res.json()
    assert len(vendors) == 10
    technova = next(v for v in vendors if v["id"] == "V001")
    assert technova["gstin"] == "29AABCT1332L1ZA"
    assert "bank_ifsc" not in technova  # only the last 4 digits are exposed
    assert {v["status"] for v in vendors} == {"ACTIVE", "BLOCKED"}


async def test_purchase_orders_have_lines_and_remaining(client: AsyncClient) -> None:
    res = await client.get("/reference/pos")
    assert res.status_code == 200
    pos = {p["po_number"]: p for p in res.json()}
    po = pos["PO-2026-0042"]
    assert po["vendor_name"] == "TechNova Solutions"
    assert po["lines"][0]["description"] == "Laptop docking station"
    assert Decimal(po["lines"][0]["unit_price"]) == Decimal("1100")
    assert Decimal(po["total"]) == Decimal("165200")
    assert Decimal(pos["PO-2026-0033"]["remaining"]) == Decimal("80535") - Decimal("12980")


async def test_goods_receipts(client: AsyncClient) -> None:
    res = await client.get("/reference/grns")
    assert res.status_code == 200
    grns = {g["grn_number"]: g for g in res.json()}
    assert grns["GRN-0311"]["po_number"] == "PO-2026-0042"
    assert grns["GRN-0311"]["lines"][0]["description"] == "Laptop docking station"
    # PO-2026-0035 (BlueLeaf) deliberately has no GRN — used for the ON_HOLD edge case.
    assert all(g["po_number"] != "PO-2026-0035" for g in grns.values())
