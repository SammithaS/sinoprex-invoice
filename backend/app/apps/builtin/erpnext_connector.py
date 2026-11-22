"""ERPNext Connector: post approved invoices to ERPNext instead of the mock ERP; record payments."""

from datetime import date
from typing import TYPE_CHECKING, Any

from app.apps.context import AppContext, ConnectionValues, Transports
from app.apps.manifest import AppManifest, ConnectionRequirement
from app.db.models import ErpDocument
from app.erpnext import ErpNextClient, ErpNextErp

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.apps.platform import AppPlatform

APP_ID = "erpnext-connector"


def erpnext(
    values: ConnectionValues,
    transports: Transports,
    sessionmaker: "async_sessionmaker[AsyncSession]",
    app_url: str = "",
) -> ErpNextErp:
    client = ErpNextClient(
        values["base_url"], values["api_key"], values["api_secret"], transports.client
    )
    return ErpNextErp(
        client,
        sessionmaker,
        company=values["company"],
        item_code=values.get("item_code") or "SINOPREX-AP",
        tax_account=values.get("tax_account") or "Input GST",
        app_url=app_url,
    )


async def active_erpnext(platform: "AppPlatform") -> ErpNextErp | None:
    """The ERPNext adapter while the connector app is active, else None (mock ERP)."""
    values = await platform.active_connection(APP_ID, "erpnext")
    if values is None:
        return None
    s = platform.settings
    return erpnext(
        values, platform.transports, platform.sessionmaker, s.app_url or s.frontend_origin
    )


def _erp(ctx: AppContext) -> ErpNextErp:
    return erpnext(ctx.connections["erpnext"], ctx.transports, ctx.sessionmaker, ctx.app_url)


async def _check(ctx: AppContext) -> None:
    erp = _erp(ctx)
    await erp.c.whoami()
    await erp.abbr()  # the company exists


async def sync_masters(ctx: AppContext, payload: dict[str, Any]) -> str:
    from app.procedure import active_policy

    async with ctx.sessionmaker() as session:
        policy = await active_policy(session)
    made = await _erp(ctx).sync_masters(policy)
    if not any(made.values()):
        return "SKIP: ERPNext already has every supplier, cost centre and account"
    return "Created in ERPNext: " + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in made.items())


async def record_payment(ctx: AppContext, payload: dict[str, Any]) -> str:
    if payload.get("system") != "erpnext":
        return "SKIP: the invoice is in the mock ERP"
    if not payload.get("utr"):
        return "SKIP: no bank reference yet"
    async with ctx.sessionmaker() as session:
        from sqlalchemy import select

        doc = await session.scalar(
            select(ErpDocument).where(ErpDocument.doc_number == payload["doc_number"])
        )
    paid_on = doc.paid_at.date() if doc and doc.paid_at else date.today()
    entry = await _erp(ctx).pay(payload["doc_number"], payload["utr"], paid_on)
    return f"Payment Entry {entry} recorded against {payload['doc_number']}"


APP = AppManifest(
    id=APP_ID,
    name="ERPNext Connector",
    tagline="Post approved invoices to ERPNext and record their payments",
    description=(
        "While active, approved invoices are posted to ERPNext as submitted Purchase Invoices "
        "(supplier by GSTIN, your GL account and cost centre, GST as a tax row, the invoice "
        "attached) and read back from ERPNext for the posting check. When a payout reaches the "
        "bank, a Payment Entry with the UTR is recorded against the invoice. 'Run now' creates "
        "the suppliers, cost centres and expense accounts the postings need. Paused, postings go "
        "to the built-in mock ERP again."
    ),
    icon="server",
    category="ERP",
    connections=(ConnectionRequirement("erpnext", "erpnext", "ERPNext site"),),
    events=("payment.processed",),
    handlers={"payment.processed": record_payment, "sync_masters": sync_masters},
    on_validate=_check,
    run_now="sync_masters",
)
