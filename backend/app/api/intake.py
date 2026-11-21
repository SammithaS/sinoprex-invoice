"""Email intake from n8n (`POST /intake/email`, HMAC-signed) and the integrations status.

The intake route is not behind the shared API key: n8n proves itself with the webhook
signature instead (plan §11). Each PDF attachment becomes one run (source EMAIL); a redelivered
email (same message id and file name) returns the existing run instead of creating another.
"""

import base64
import binascii
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.api.runs import ServiceDep
from app.config import get_settings
from app.db.models import Notification
from app.deps import SessionDep
from app.integrations.intake import ingest_email
from app.integrations.notify import Notifier
from app.integrations.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, SignatureError, verify

intake_router = APIRouter(tags=["intake"])
router = APIRouter(tags=["intake"])

MAX_BODY = 80 * 1024 * 1024  # 5 PDFs of 10 MB, base64-encoded, plus headers


class Attachment(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str


class IntakeEmail(BaseModel):
    message_id: str = Field(min_length=1, max_length=200)
    sender: str = Field(default="", max_length=200, alias="from")
    subject: str = Field(default="", max_length=500)
    received_at: str | None = None
    attachments: list[Attachment] = Field(default_factory=list, max_length=20)


class IntakeRun(BaseModel):
    run_id: str
    file_name: str
    link: str
    duplicate: bool


class Rejected(BaseModel):
    filename: str
    reason: str


class IntakeOut(BaseModel):
    message_id: str
    runs: list[IntakeRun]
    rejected: list[Rejected]


def _decode(att: Attachment) -> bytes | str:
    """The attachment's bytes, or why it was refused."""
    try:
        return base64.b64decode(att.content_base64, validate=True)
    except (binascii.Error, ValueError):
        return "attachment is not valid base64"


@intake_router.post("/intake/email", response_model=IntakeOut, status_code=202)
async def intake_email(request: Request, service: ServiceDep, session: SessionDep) -> IntakeOut:
    settings = get_settings()
    platform = getattr(request.app.state, "apps", None)
    secret = (await platform.n8n_intake_secret() if platform else None) or (
        settings.n8n_webhook_secret
    )
    if not secret:
        raise HTTPException(
            status_code=503, detail="email intake is off (install the n8n Connector app)"
        )
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY:
        raise HTTPException(status_code=413, detail="request too large")
    body = await request.body()
    try:
        verify(
            secret,
            request.headers.get(TIMESTAMP_HEADER),
            request.headers.get(SIGNATURE_HEADER),
            body,
            tolerance=settings.signature_tolerance_seconds,
        )
    except SignatureError as exc:
        raise HTTPException(status_code=401, detail=f"bad signature: {exc}") from exc
    try:
        mail = IntakeEmail.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc

    app_url = (settings.app_url or settings.frontend_origin).rstrip("/")
    ingested, refused = await ingest_email(
        service,
        session,
        message_id=mail.message_id,
        sender=mail.sender or None,
        attachments=[(att.filename, _decode(att)) for att in mail.attachments],
        max_attachments=settings.intake_max_attachments,
    )
    runs = [
        IntakeRun(
            run_id=r.run_id,
            file_name=r.file_name,
            link=f"{app_url}/runs/{r.run_id}",
            duplicate=r.duplicate,
        )
        for r in ingested
    ]
    rejected = [Rejected(filename=r.filename, reason=r.reason) for r in refused]
    return IntakeOut(message_id=mail.message_id, runs=runs, rejected=rejected)


# --- status (behind the API key) ---------------------------------------------------------------


class NotificationOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    run_id: str
    event: str
    status: str
    attempts: int
    error: str | None
    created_at: datetime


class IntegrationsOut(BaseModel):
    n8n_configured: bool
    intake_enabled: bool
    api_key_set: bool
    webhooks: dict[str, str]
    recent: list[NotificationOut]


@router.get("/integrations", response_model=IntegrationsOut)
async def integrations(request: Request, session: SessionDep) -> dict[str, Any]:
    settings = get_settings()
    notifier: Notifier | None = getattr(request.app.state, "notifier", None)
    rows = await session.scalars(select(Notification).order_by(Notification.id.desc()).limit(20))
    platform = getattr(request.app.state, "apps", None)
    try:
        app_secret = await platform.n8n_intake_secret() if platform else None
    except Exception:  # no or wrong SECRETS_KEY: report the n8n Connector as off
        app_secret = None
    return {
        "n8n_configured": bool(
            app_secret or (settings.n8n_base_url and settings.n8n_webhook_secret)
        ),
        "intake_enabled": bool(app_secret or settings.n8n_webhook_secret),
        "api_key_set": bool(settings.api_key),
        "webhooks": (
            {
                "review_needed": notifier.webhook_url("review_needed"),
                "vendor_followup": notifier.webhook_url("vendor_followup"),
            }
            if notifier and settings.n8n_base_url
            else {}
        ),
        "recent": list(rows),
    }
