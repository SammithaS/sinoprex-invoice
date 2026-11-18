"""GSTIN helpers — pure functions, no I/O.

GSTIN layout (15 chars): 2-digit state code · 10-char PAN · entity number (1-9, A-Z)
· the letter 'Z' · a check character (mod-36 Luhn-style checksum).
"""

import re

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

# GST state codes used in this project's seed data (subset of the official list).
STATE_CODES: dict[str, str] = {
    "07": "Delhi",
    "24": "Gujarat",
    "27": "Maharashtra",
    "29": "Karnataka",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "36": "Telangana",
}


def gstin_check_char(first14: str) -> str:
    """Compute the 15th (check) character for the first 14 characters of a GSTIN."""
    if len(first14) != 14:
        raise ValueError("expected the first 14 characters of a GSTIN")
    total = 0
    for i, ch in enumerate(first14.upper()):
        value = _CHARSET.index(ch) * (2 if i % 2 else 1)
        total += value // 36 + value % 36
    return _CHARSET[(36 - total % 36) % 36]


def is_valid_gstin(gstin: str) -> bool:
    """True if the GSTIN has the right shape and its check character matches."""
    g = gstin.strip().upper()
    if not _GSTIN_RE.match(g):
        return False
    return gstin_check_char(g[:14]) == g[14]


def build_gstin(state_code: str, pan: str, entity: str = "1") -> str:
    """Build a checksum-valid GSTIN from a state code and PAN (used for seed data)."""
    first14 = f"{state_code}{pan.upper()}{entity}Z"
    return first14 + gstin_check_char(first14)


def gstin_state_code(gstin: str) -> str:
    return gstin.strip()[:2]


def gstin_pan(gstin: str) -> str:
    return gstin.strip().upper()[2:12]
