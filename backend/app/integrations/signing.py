"""HMAC-SHA256 webhook signatures, the same scheme both ways (backend ⇄ n8n).

    X-IIQ-Timestamp: <unix seconds>
    X-IIQ-Signature: sha256=<hex HMAC-SHA256(secret, f"{timestamp}.{raw body}")>

The timestamp is inside the signed text, so an old request can't be replayed with a fresh
timestamp; requests older (or newer) than the tolerance are refused. Outbound bodies are compact
JSON (`canonical_json`) so n8n can re-create the exact bytes with `JSON.stringify(body)`.
"""

import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any

TIMESTAMP_HEADER = "X-IIQ-Timestamp"
SIGNATURE_HEADER = "X-IIQ-Signature"


class SignatureError(ValueError):
    """Missing, stale or wrong signature."""


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def signed_headers(secret: str, body: bytes, now: float | None = None) -> dict[str, str]:
    ts = str(int(now if now is not None else time.time()))
    return {TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign(secret, ts, body)}


def verify(
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    *,
    tolerance: int = 300,
    now: float | None = None,
) -> None:
    if not secret:
        raise SignatureError("signing secret is not configured")
    if not timestamp or not signature:
        raise SignatureError("missing signature headers")
    if not timestamp.isdigit():
        raise SignatureError("bad timestamp")
    age = (now if now is not None else time.time()) - int(timestamp)
    if abs(age) > tolerance:
        raise SignatureError("timestamp outside the allowed window")
    expected = sign(secret, timestamp, body)
    if not hmac.compare_digest(expected, signature.strip().lower()):
        raise SignatureError("signature does not match")


def _plain(value: Any) -> Any:
    """JSON-safe values that serialise identically in Python and JavaScript (no floats)."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, int | str):
        return value
    if isinstance(value, float | Decimal):
        return str(value)
    return str(value)


def canonical_json(payload: Any) -> bytes:
    return json.dumps(_plain(payload), separators=(",", ":"), ensure_ascii=False).encode()
