"""Phase 10: signed webhooks, email intake, the tunnel guard and n8n notifications.

No n8n is called: outbound webhooks go to an httpx MockTransport, which checks the signature
the same way the n8n workflows do.
"""

import base64
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db.models import Notification, Run
from app.graph.runner import RunService
from app.integrations.followup import compose, templates
from app.integrations.notify import REVIEW_NEEDED, VENDOR_FOLLOWUP, Notifier
from app.integrations.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SignatureError,
    canonical_json,
    sign,
    signed_headers,
    verify,
)
from app.main import create_app
from tests.test_graph import INVOICES, api, db, run_sample, service  # noqa: F401  (fixtures)

SECRET = "test-webhook-secret"


# --- signing -------------------------------------------------------------------------------------


def test_signature_round_trip_and_refusals() -> None:
    body = b'{"a":1}'
    h = signed_headers(SECRET, body, now=1_000_000)
    verify(SECRET, h[TIMESTAMP_HEADER], h[SIGNATURE_HEADER], body, now=1_000_100)
    bad = [
        ("other-secret", h[TIMESTAMP_HEADER], h[SIGNATURE_HEADER], body, 1_000_000),
        (SECRET, h[TIMESTAMP_HEADER], h[SIGNATURE_HEADER], b'{"a":2}', 1_000_000),
        (SECRET, h[TIMESTAMP_HEADER], h[SIGNATURE_HEADER], body, 1_000_000 + 301),  # stale
        (SECRET, "1000001", h[SIGNATURE_HEADER], body, 1_000_000),  # timestamp is signed too
        (SECRET, None, h[SIGNATURE_HEADER], body, 1_000_000),
        ("", h[TIMESTAMP_HEADER], h[SIGNATURE_HEADER], body, 1_000_000),
    ]
    for secret, ts, sig, b, now in bad:
        with pytest.raises(SignatureError):
            verify(secret, ts, sig, b, now=now)
    # Known vector: HMAC-SHA256("k", "1.x"), as the n8n Crypto node computes it.
    import hashlib
    import hmac

    assert sign("k", "1", b"x") == "sha256=" + hmac.new(b"k", b"1.x", hashlib.sha256).hexdigest()


def test_canonical_json_is_compact_and_float_free() -> None:
    from decimal import Decimal

    body = canonical_json({"b": 1, "a": "₹1,250", "f": 0.5, "d": Decimal("147500.00"), "n": None})
    assert body.decode() == '{"b":1,"a":"₹1,250","f":"0.5","d":"147500.00","n":null}'


# --- follow-up text ----------------------------------------------------------------------------


def test_faq_templates_are_parsed() -> None:
    t = templates()
    assert {"1", "5", "7", "8", "9"} <= set(t)
    assert t["5"][0] == "Price differs from the agreed rate"
    assert "{agreed_rate}" in t["5"][1]


def test_followup_uses_the_template_when_it_can_be_filled() -> None:
    mail = compose(
        vendor_name="Deccan Office Supplies",
        invoice_number="DOS/88",
        invoice_date="2026-11-02",
        exceptions=[{"category": "LOW_CONFIDENCE", "summary": "Total unreadable"}],
        low_confidence_fields=["total", "invoice_date"],
        reviewer_comment="Please send the original PDF.",
    )
    assert mail["citation"] == "vendor-faq §8"
    assert "read total, invoice date" in mail["message"]
    assert "{" not in mail["message"] and "Please send the original PDF." in mail["message"]


def test_price_variance_fills_the_faq_template() -> None:
    mail = compose(
        vendor_name="TechNova Solutions",
        invoice_number="TN/2026/1042",
        invoice_date="2026-11-10",
        exceptions=[
            {
                "category": "PRICE_VARIANCE",
                "summary": "…",
                "variance": {
                    "line": "1",
                    "billed_rate": "1250.00",
                    "reference_rate": "1100.00",
                    "reference_source": "contracts/technova §2.1",
                },
            }
        ],
        low_confidence_fields=[],
        reviewer_comment=None,
        lines=[{"line_no": 1, "description": "Laptop docking station"}],
    )
    assert mail["citation"] == "vendor-faq §5"
    assert mail["message"].startswith(
        "Dear TechNova Solutions, invoice TN/2026/1042 bills Laptop docking station at ₹1,250 "
        "per unit, while our agreed rate is ₹1,100 (contracts/technova §2.1)."
    )


def test_indian_grouping() -> None:
    from app.integrations.followup import _amount

    assert [_amount(x) for x in ("1250.00", "1234567.5", "100", "x")] == [
        "1,250",
        "12,34,567.50",
        "100",
        "x",
    ]


def test_followup_falls_back_instead_of_leaving_placeholders() -> None:
    mail = compose(
        vendor_name="TechNova Solutions",
        invoice_number="TN/2026/1042",
        invoice_date="2026-11-10",
        exceptions=[
            {
                "category": "PRICE_VARIANCE",
                "summary": "Laptop docking station billed at ₹1,250 vs contracted ₹1,100.",
                "variance": {},
            }
        ],
        low_confidence_fields=[],
        reviewer_comment=None,
    )
    assert "{" not in mail["message"]
    assert "₹1,250 vs contracted ₹1,100" in mail["message"]
    assert mail["subject"] == "Invoice TN/2026/1042: information needed"
    assert mail["citation"] is None  # the template wasn't used, so it isn't cited


# --- intake --------------------------------------------------------------------------------------


def signed_post(payload: dict[str, Any], secret: str = SECRET, ts: float | None = None) -> Any:
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json", **signed_headers(secret, body, now=ts)}
    return body, headers


def email(*names: str, message_id: str = "msg-1") -> dict[str, Any]:
    pdf = (INVOICES / "technova-contract-rate.pdf").read_bytes()
    atts = []
    for n in names:
        content = pdf if n.endswith(".pdf") else b"hello"
        atts.append({"filename": n, "content_base64": base64.b64encode(content).decode()})
    return {
        "message_id": message_id,
        "from": "Vendor <accounts@technova.example>",
        "subject": "Invoice",
        "attachments": atts,
    }


@pytest.fixture
async def intake_api(
    api: AsyncClient, service: RunService, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("N8N_WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()
    yield api
    await service.wait_all()


async def test_email_with_a_pdf_creates_one_run(
    intake_api: AsyncClient, service: RunService
) -> None:
    body, headers = signed_post(email("TN-1042.pdf", "notes.txt"))
    headers["X-API-Key"] = "wrong"  # the API key isn't what protects this route
    res = await intake_api.post("/intake/email", content=body, headers=headers)
    assert res.status_code == 202, res.text
    out = res.json()
    assert [r["file_name"] for r in out["runs"]] == ["TN-1042.pdf"]
    assert out["runs"][0]["link"].endswith(f"/runs/{out['runs'][0]['run_id']}")
    assert out["rejected"] == [{"filename": "notes.txt", "reason": "not a PDF file name"}]
    run_id = out["runs"][0]["run_id"]
    await service.wait(run_id)
    async with service.sessionmaker() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        assert run.source == "EMAIL" and run.intake_from == "Vendor <accounts@technova.example>"
        # (tests replay extractions for named samples only, so this run isn't read here)
        assert Path(run.file_path).name == f"{run.file_hash}.pdf"

    # n8n redelivers the same email: same run, nothing new.
    body, headers = signed_post(email("TN-1042.pdf"))
    again = (await intake_api.post("/intake/email", content=body, headers=headers)).json()
    assert again["runs"][0] == {**out["runs"][0], "duplicate": True}
    async with service.sessionmaker() as session:
        assert len(list(await session.scalars(select(Run)))) == 1


async def post(client: AsyncClient, payload: dict[str, Any], **kw: Any) -> httpx.Response:
    body, headers = signed_post(payload, **kw)
    return await client.post("/intake/email", content=body, headers=headers)


async def test_intake_refuses_bad_signatures_and_fake_pdfs(intake_api: AsyncClient) -> None:
    assert (await post(intake_api, email("a.pdf"), secret="guess")).status_code == 401
    assert (await post(intake_api, email("a.pdf"), ts=time.time() - 3600)).status_code == 401
    unsigned = await intake_api.post("/intake/email", json=email("a.pdf"))
    assert unsigned.status_code == 401

    fake = email()
    fake["attachments"] = [
        {"filename": "x.pdf", "content_base64": base64.b64encode(b"MZ").decode()},
        {"filename": "y.pdf", "content_base64": "%%%"},
    ]
    out = (await post(intake_api, fake)).json()
    assert out["runs"] == []
    assert [r["reason"] for r in out["rejected"]] == [
        "file content is not a PDF",
        "attachment is not valid base64",
    ]
    assert (await post(intake_api, {"message_id": ""})).status_code == 422


async def test_intake_is_off_without_a_secret(api: AsyncClient) -> None:
    body, headers = signed_post(email("a.pdf"))
    assert (await api.post("/intake/email", content=body, headers=headers)).status_code == 503


async def test_tunnel_guard_without_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        tunnelled = {"cf-ray": "8f00-BOM"}
        res = await c.post("/admin/reset-demo", headers=tunnelled)
        assert res.status_code == 403 and "API_KEY" in res.json()["detail"]
        assert (await c.get("/runs", headers=tunnelled)).status_code == 403
        # the signed intake route is reachable (here: off, no secret → 503, not 403)
        assert (await c.post("/intake/email", headers=tunnelled)).status_code == 503
    get_settings.cache_clear()


# --- notifications -------------------------------------------------------------------------------


class FakeN8n:
    """Records webhook calls after checking their signature like the n8n workflows do."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        verify(
            SECRET,
            request.headers.get(TIMESTAMP_HEADER),
            request.headers.get(SIGNATURE_HEADER),
            body,
        )
        payload = json.loads(body)
        # n8n re-serialises the parsed body with JSON.stringify: compact, same key order.
        assert json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode() == body
        self.calls.append((str(request.url), payload))
        return httpx.Response(self.status, json={"ok": True})


def make_notifier(db: async_sessionmaker[AsyncSession], fake: FakeN8n, **kw: Any) -> Notifier:
    settings = Settings(
        app_env="test",
        n8n_base_url="https://acme.app.n8n.cloud/",
        n8n_webhook_secret=SECRET,
        app_url="http://localhost:3000",
        **kw,
    )
    return Notifier(db, settings, transport=httpx.MockTransport(fake.handler), retry_delay=0)


async def notifications(db: async_sessionmaker[AsyncSession]) -> list[Notification]:
    async with db() as session:
        return list(await session.scalars(select(Notification).order_by(Notification.id)))


async def test_review_needed_then_vendor_followup(
    db: async_sessionmaker[AsyncSession], service: RunService
) -> None:
    fake = FakeN8n()
    service.notifier = notifier = make_notifier(db, fake)
    run = await run_sample(service, "technova-contract-rate")
    await notifier.drain()
    ((url, review),) = fake.calls
    assert url == "https://acme.app.n8n.cloud/webhook/invoiceiq-review"
    assert review["event"] == REVIEW_NEEDED and review["kind"] == "review"
    assert review["link"] == f"http://localhost:3000/runs/{run.id}"
    assert "1,250" in review["question"] or "1250" in review["question"]
    assert review["exceptions"][0]["category"] == "PRICE_VARIANCE"

    await service.review(
        run.id, {"action": "REQUEST_INFO", "comment": "Amendment?", "reviewer": "Priya Nair"}
    )
    await service.wait(run.id)
    await notifier.drain()
    url, follow = fake.calls[1]
    assert url.endswith("/webhook/invoiceiq-vendor-followup")
    assert follow["event"] == VENDOR_FOLLOWUP
    assert follow["vendor_email"] == "accounts@technova.example"  # on file, not from the PDF
    assert follow["requested_by"] == "Priya Nair"
    assert "{" not in follow["message"] and "Amendment?" in follow["message"]
    assert follow["citation"] == "vendor-faq §5"
    assert "bills Laptop docking station at ₹1,250 per unit" in follow["message"]
    assert [n.status for n in await notifications(db)] == ["SENT", "SENT"]


async def test_failed_and_skipped_notices_are_recorded(
    db: async_sessionmaker[AsyncSession], service: RunService
) -> None:
    fake = FakeN8n(status=503)
    service.notifier = notifier = make_notifier(db, fake)
    run = await run_sample(service, "sunrise-unknown-vendor")
    await notifier.drain()
    assert len(fake.calls) == 3  # retried
    await service.review(run.id, {"action": "REQUEST_INFO", "comment": "Who are you?"})
    await service.wait(run.id)
    await notifier.drain()
    rows = await notifications(db)
    assert [(n.event, n.status) for n in rows] == [
        (REVIEW_NEEDED, "FAILED"),
        (VENDOR_FOLLOWUP, "SKIPPED"),  # unknown supplier: no contact on file to write to
    ]
    assert rows[0].attempts == 3 and "503" in (rows[0].error or "")


async def test_notices_are_skipped_when_n8n_is_not_set_up(
    db: async_sessionmaker[AsyncSession], service: RunService
) -> None:
    service.notifier = notifier = Notifier(db, Settings(app_env="test"))
    await run_sample(service, "technova-contract-rate")
    await notifier.drain()
    (row,) = await notifications(db)
    assert row.status == "SKIPPED" and "not configured" in (row.error or "")


async def test_integrations_status(api: AsyncClient, tmp_path: Path) -> None:
    res = (await api.get("/integrations")).json()
    assert res["n8n_configured"] is False and res["recent"] == []
