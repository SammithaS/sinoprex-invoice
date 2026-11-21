"""Outbound notices to n8n, sent as signed webhooks.

- `review_needed`: a case paused for a person (review, approval or ERP recheck) → n8n emails the
  reviewer a deep link, and reminds after 24 h if it's still waiting.
- `vendor_followup`: a reviewer chose REQUEST_INFO → n8n emails the vendor contact *on file* the
  FAQ template for the case's exception (never an address printed on the invoice).

Sending runs in the background (a slow or down n8n never holds up a case), with three attempts,
and every outcome is a `notifications` row (SENT / FAILED / SKIPPED) shown in Settings.
"""

import asyncio
import json
import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Notification, Run, Vendor
from app.integrations.followup import compose
from app.integrations.signing import canonical_json, signed_headers

log = logging.getLogger(__name__)

REVIEW_NEEDED = "review_needed"
VENDOR_FOLLOWUP = "vendor_followup"
ATTEMPTS = 3


class Notifier:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay: float = 1.0,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.transport = transport
        self.retry_delay = retry_delay
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.n8n_base_url and self.settings.n8n_webhook_secret)

    def webhook_url(self, event: str) -> str:
        path = (
            self.settings.n8n_review_path
            if event == REVIEW_NEEDED
            else self.settings.n8n_vendor_path
        )
        return f"{self.settings.n8n_base_url.rstrip('/')}/webhook/{path}"

    @property
    def app_url(self) -> str:
        return (self.settings.app_url or self.settings.frontend_origin).rstrip("/")

    # --- scheduling ----------------------------------------------------------------------------

    def schedule(self, run_id: str, event: str, context: dict[str, Any] | None = None) -> None:
        task = asyncio.create_task(self.send(run_id, event, context or {}))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # --- payloads ------------------------------------------------------------------------------

    async def payload(
        self, session: AsyncSession, run: Run, event: str, context: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """(payload, reason it can't be sent)."""
        request = run.review_request or {}
        state = run.state or {}
        base: dict[str, Any] = {
            "event": event,
            "run_id": run.id,
            "link": f"{self.app_url}/runs/{run.id}",
            "vendor": run.vendor_name,
            "invoice_number": run.invoice_number,
            "total_inr": run.total,
            "status": run.status,
        }
        if event == REVIEW_NEEDED:
            approval = request.get("approval") or {}
            return {
                **base,
                "kind": request.get("kind", "review"),
                "decision": run.decision,
                "question": request.get("headline"),
                "exceptions": [
                    {
                        "category": e.get("category"),
                        "summary": e.get("summary"),
                        "owner": e.get("owner_role"),
                    }
                    for e in request.get("exceptions") or []
                ],
                "approver_role": approval.get("role"),
                "approver_name": approval.get("approver_name"),
                "review_queue": f"{self.app_url}/review",
                "approvals_inbox": f"{self.app_url}/approvals",
            }, None
        # vendor follow-up: only to the contact on file
        vendor = await session.get(Vendor, run.vendor_id) if run.vendor_id else None
        if vendor is None or not vendor.email:
            return None, "no vendor contact on file (unknown supplier)"
        data = (state.get("extraction") or {}).get("data") or {}
        assessment = state.get("assessment") or {}
        low = [f.get("field", "") for f in request.get("low_confidence_fields") or []]
        mail = compose(
            vendor_name=vendor.name,
            invoice_number=run.invoice_number,
            invoice_date=data.get("invoice_date"),
            exceptions=assessment.get("exceptions") or request.get("exceptions") or [],
            low_confidence_fields=low,
            reviewer_comment=context.get("comment"),
            lines=data.get("lines") or [],
        )
        return {
            **base,
            "vendor_id": vendor.id,
            "vendor_email": vendor.email,
            "requested_by": context.get("reviewer"),
            **mail,
        }, None

    # --- sending -------------------------------------------------------------------------------

    async def send(self, run_id: str, event: str, context: dict[str, Any]) -> None:
        try:
            await self._send(run_id, event, context)
        except Exception:  # a notice must never take anything else down
            log.exception("notification %s for run %s failed", event, run_id)

    async def _send(self, run_id: str, event: str, context: dict[str, Any]) -> None:
        async with self.sessionmaker() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return
            payload, skip = await self.payload(session, run, event, context)
            stored = json.loads(canonical_json(payload)) if payload is not None else None
            row = Notification(run_id=run_id, event=event, payload=stored, attempts=0)
            if skip is None and not self.enabled:
                skip = "n8n is not configured (N8N_BASE_URL / N8N_WEBHOOK_SECRET)"
            if skip is not None or payload is None:
                row.status, row.error = "SKIPPED", skip
                session.add(row)
                await session.commit()
                return
            body = canonical_json(payload)
            url = self.webhook_url(event)
            error: str | None = None
            async with httpx.AsyncClient(timeout=15, transport=self.transport) as client:
                for attempt in range(1, ATTEMPTS + 1):
                    row.attempts = attempt
                    headers = {
                        "content-type": "application/json",
                        **signed_headers(self.settings.n8n_webhook_secret, body),
                    }
                    try:
                        res = await client.post(url, content=body, headers=headers)
                        if res.status_code < 300:
                            error = None
                            break
                        error = f"HTTP {res.status_code}: {res.text[:200]}"
                        if res.status_code < 500 and res.status_code != 429:
                            break  # a client error won't fix itself
                    except httpx.HTTPError as exc:
                        error = f"{type(exc).__name__}: {exc}"[:300]
                    if attempt < ATTEMPTS:
                        await asyncio.sleep(self.retry_delay * attempt)
            row.status = "FAILED" if error else "SENT"
            row.error = error
            session.add(row)
            await session.commit()
            if error:
                log.warning("notification %s for run %s failed: %s", event, run_id, error)
