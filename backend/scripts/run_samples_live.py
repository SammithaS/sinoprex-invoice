"""Run every sample through a live API in manifest order and time it (Phase 7 check, Phase 11 eval).

    uv run python -m scripts.run_samples_live --base-url http://localhost:8010
    uv run python -m scripts.run_samples_live northwind-clean deccan-scanned-poor

Each case is timed from submission until it pauses for review or closes. Expected decisions
assume fresh invoice history (reseed first), since duplicates and split invoices depend on it.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

MANIFEST = Path(__file__).resolve().parents[2] / "data" / "test-invoices" / "manifest.json"
SETTLED = {"WAITING_REVIEW", "COMPLETED", "FAILED"}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("keys", nargs="*")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()

    manifest: list[dict[str, Any]] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = [m for m in manifest if not args.keys or m["key"] in args.keys]
    headers = {"X-API-Key": get_settings().api_key} if get_settings().api_key else {}
    rows = []
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=30) as client:
        for case in cases:
            started = time.perf_counter()
            run = client.post(f"/samples/{case['key']}/run").raise_for_status().json()
            while run["status"] not in SETTLED and time.perf_counter() - started < args.timeout:
                time.sleep(0.5)
                run = client.get(f"/runs/{run['id']}").raise_for_status().json()
            seconds = time.perf_counter() - started
            ok = run.get("decision") == case["expected_decision"]
            rows.append((case["key"], seconds, run["status"], run.get("decision"), ok))
            verdict = "ok" if ok else f"EXPECTED {case['expected_decision']}"
            print(
                f"{case['key']:32} {seconds:6.1f}s  {run['status']:15} "
                f"{run.get('decision') or '—':13} {verdict}",
                flush=True,
            )
    times = sorted(r[1] for r in rows)
    if times:
        print(
            f"\n{len(rows)} cases · median {times[len(times) // 2]:.1f}s · max {times[-1]:.1f}s · "
            f"{sum(r[1] <= 30 for r in rows)}/{len(rows)} under 30 s · "
            f"{sum(r[4] for r in rows)}/{len(rows)} expected decisions"
        )
    return 0 if all(r[2] != "FAILED" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
