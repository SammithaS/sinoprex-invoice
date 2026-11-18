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

from app.api.runs import MAX_UPLOAD, ServiceDep
from app.config import get_settings
from app.db.models import Notification, Run
from app.deps import SessionDep
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


def _pdf(att: Attachment) -> bytes | str:
    """The PDF bytes, or why the attachment was refused."""
    if not att.filename.lower().endswith(".pdf"):
        return "not a PDF file name"
    try:
        data = base64.b64decode(att.content_base64, validate=True)
    except (binascii.Error, ValueError):
        return "attachment is not valid base64"
    if len(data) > MAX_UPLOAD:
        return "larger than 10 MB"
    if not data.startswith(b"%PDF"):
        return "file content is not a PDF"
    return data


@intake_router.post("/intake/email", response_model=IntakeOut, status_code=202)
async def intake_email(request: Request, service: ServiceDep, session: SessionDep) -> IntakeOut:
    settings = get_settings()
    if not settings.n8n_webhook_secret:
        raise HTTPException(status_code=503, detail="email intake is off (N8N_WEBHOOK_SECRET)")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY:
        raise HTTPException(status_code=413, detail="request too large")
    body = await request.body()
    try:
        verify(
            settings.n8n_webhook_secret,
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
    runs: list[IntakeRun] = []
    rejected: list[Rejected] = []
    seen: set[str] = set()
    for i, att in enumerate(mail.attachments):
        if i >= settings.intake_max_attachments:
            rejected.append(Rejected(filename=att.filename, reason="too many attachments"))
            continue
        result = _pdf(att)
        if isinstance(result, str):
            rejected.append(Rejected(filename=att.filename, reason=result))
            continue
        ref = f"{mail.message_id}#{att.filename}"[:300]
        if ref in seen:
            continue
        seen.add(ref)
        existing = await session.scalar(select(Run).where(Run.intake_ref == ref))
        if existing is not None:
            run, duplicate = existing, True
        else:
            run = await service.create_run(
                result,
                att.filename,
                source="EMAIL",
                intake_ref=ref,
                intake_from=mail.sender or None,
            )
            duplicate = False
        runs.append(
            IntakeRun(
                run_id=run.id,
                file_name=run.file_name,
                link=f"{app_url}/runs/{run.id}",
                duplicate=duplicate,
            )
        )
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
    return {
        "n8n_configured": bool(settings.n8n_base_url and settings.n8n_webhook_secret),
        "intake_enabled": bool(settings.n8n_webhook_secret),
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
