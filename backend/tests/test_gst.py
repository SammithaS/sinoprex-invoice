import json

import pytest

from app.db.seed import DEFAULT_SEED_DIR
from app.rules.gst import build_gstin, gstin_pan, gstin_state_code, is_valid_gstin


def test_build_gstin_produces_valid_checksum() -> None:
    gstin = build_gstin("29", "AABCT1332L")
    assert gstin == "29AABCT1332L1ZA"
    assert is_valid_gstin(gstin)


@pytest.mark.parametrize(
    "gstin",
    [
        "29AABCT1332L1ZV",  # wrong check character
        "29AABCT1332L1XA",  # 14th char must be Z
        "2AABCT1332L1ZA",  # too short
        "29AABCT1332L0ZA",  # entity number cannot be 0
    ],
)
def test_invalid_gstins_rejected(gstin: str) -> None:
    assert not is_valid_gstin(gstin)


def test_lowercase_and_whitespace_normalised() -> None:
    assert is_valid_gstin(" 29aabct1332l1za ")


def test_parts() -> None:
    assert gstin_state_code("27AKZPS6612M1Z4") == "27"
    assert gstin_pan("27AKZPS6612M1Z4") == "AKZPS6612M"


def test_all_seed_vendor_gstins_valid_and_consistent() -> None:
    vendors = json.loads((DEFAULT_SEED_DIR / "vendors.json").read_text("utf-8"))
    assert len(vendors) >= 8
    for v in vendors:
        assert is_valid_gstin(v["gstin"]), v["gstin"]
        assert gstin_state_code(v["gstin"]) == v["state_code"]
        assert gstin_pan(v["gstin"]) == v["pan"]
