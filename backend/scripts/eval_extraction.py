"""Run extraction over the test invoices, score it against ground truth, optionally record
the raw model outputs as test fixtures.

    uv run python -m scripts.eval_extraction                      # all cases, real LLM
    uv run python -m scripts.eval_extraction technova-contract-rate deccan-scanned-poor
    uv run python -m scripts.eval_extraction --record             # also save fixtures
    uv run python -m scripts.eval_extraction --replay             # use saved fixtures, no LLM

Fixtures land in backend/tests/fixtures/extraction/<key>.json: the model's InvoiceRead plus
which model answered and token usage. Tests replay them so CI never calls an LLM.
"""

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable, RunnableLambda

from app.extraction import ExtractionResult, extract_invoice
from app.llm.models import Kind, extraction_model
from app.llm.schemas import InvoiceRead

ROOT = Path(__file__).resolve().parents[2]
INVOICES = ROOT / "data" / "test-invoices"
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "extraction"

SCALAR_FIELDS = (
    "vendor_gstin",
    "buyer_gstin",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "bank_account_number",
    "bank_ifsc",
    "subtotal",
    "cgst",
    "sgst",
    "igst",
    "tax_total",
    "total",
)
LINE_FIELDS = ("hsn_sac", "qty", "unit_price", "tax_rate", "amount")


def _norm(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value.normalize()) if value == value.to_integral_value() else str(value)
    text = str(value)
    try:
        d = Decimal(text)
        return str(d.normalize()) if d == d.to_integral_value() else str(d)
    except ArithmeticError:
        return text.strip()


def _zero_is_none(name: str, value: str | None) -> str | None:
    # Ground truth stores absent tax heads as "0.00"; extraction leaves them None.
    return None if name in {"cgst", "sgst", "igst"} and value == "0" else value


def compare(result: ExtractionResult, gt: dict[str, Any]) -> dict[str, bool]:
    """Field → correct? Scalars, doc type, and line items (position by position)."""
    fields = gt["fields"]
    data = result.data
    out: dict[str, bool] = {"doc_type": data.doc_type == fields["doc_type"]}
    for name in SCALAR_FIELDS:
        want = _zero_is_none(name, _norm(fields.get(name)))
        got = _zero_is_none(name, _norm(getattr(data, name)))
        out[name] = want == got
    want_lines = fields["lines"]
    out["line_count"] = len(data.lines) == len(want_lines)
    for i, want in enumerate(want_lines):
        got_line = data.lines[i] if i < len(data.lines) else None
        for name in LINE_FIELDS:
            got = _norm(getattr(got_line, name)) if got_line else None
            out[f"line{i + 1}.{name}"] = got == _norm(want[name])
    return out


# --- model factories ---------------------------------------------------------------------------


def replay_factory(key: str) -> Any:
    """Model factory that returns the recorded reading for `key`."""
    payload = json.loads((FIXTURES / f"{key}.json").read_text(encoding="utf-8"))
    return fixed_factory(
        InvoiceRead.model_validate(payload["read"]),
        model_name=payload.get("model"),
        usage=payload.get("usage"),
    )


def fixed_factory(
    *reads: InvoiceRead, model_name: str | None = "fake", usage: dict[str, int] | None = None
) -> Any:
    """Factory whose model returns the given readings in turn (the last one repeats)."""
    queue = list(reads)

    async def answer(_: list[BaseMessage]) -> dict[str, Any]:
        read = queue.pop(0) if len(queue) > 1 else queue[0]
        raw = AIMessage(
            content="",
            response_metadata={"model_name": model_name},
            usage_metadata={
                "input_tokens": (usage or {}).get("input_tokens", 0),
                "output_tokens": (usage or {}).get("output_tokens", 0),
                "total_tokens": sum((usage or {}).values()),
            },
        )
        return {"raw": raw, "parsed": read, "parsing_error": None}

    def factory(_: Kind) -> Runnable[list[BaseMessage], dict[str, Any]]:
        return RunnableLambda(answer)

    return factory


def recording_factory(key: str) -> Any:
    """Real model, but save what it read to the fixture file."""

    def factory(kind: Kind) -> Runnable[list[BaseMessage], dict[str, Any]]:
        model = extraction_model(kind)

        async def call(messages: list[BaseMessage]) -> dict[str, Any]:
            out = await model.ainvoke(messages)
            parsed, raw = out.get("parsed"), out.get("raw")
            if isinstance(parsed, InvoiceRead):
                meta = getattr(raw, "response_metadata", {}) or {}
                usage = getattr(raw, "usage_metadata", None) or {}
                FIXTURES.mkdir(parents=True, exist_ok=True)
                payload = {
                    "key": key,
                    "model": meta.get("model_name") or meta.get("model"),
                    "usage": {
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                    },
                    "read": parsed.model_dump(mode="json"),
                }
                text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
                (FIXTURES / f"{key}.json").write_text(text, encoding="utf-8", newline="\n")
            return out

        return RunnableLambda(call)

    return factory


# --- CLI ---------------------------------------------------------------------------------------


async def run(keys: list[str], mode: str) -> int:
    manifest = json.loads((INVOICES / "manifest.json").read_text(encoding="utf-8"))
    cases = [m for m in manifest if not keys or m["key"] in keys]
    totals = {"fields": 0, "correct": 0, "key_low": 0}
    failures = 0
    print(f"{'case':32} {'model':28} {'acc':>6} {'minKey':>6}  {'ms':>6}  wrong")
    for case in cases:
        key = case["key"]
        gt = json.loads((INVOICES / case["ground_truth"]).read_text(encoding="utf-8"))
        pdf = (INVOICES / case["file"]).read_bytes()
        if mode == "replay" and not (FIXTURES / f"{key}.json").exists():
            continue
        factory = {
            "live": lambda _k: extraction_model,
            "record": recording_factory,
            "replay": replay_factory,
        }[mode](key)
        try:
            result = await extract_invoice(pdf, model_factory=factory)
        except Exception as exc:
            failures += 1
            print(f"{key:32} ERROR {type(exc).__name__}: {exc}"[:160])
            continue
        scores = compare(result, gt)
        wrong = [k for k, ok in scores.items() if not ok]
        totals["fields"] += len(scores)
        totals["correct"] += len(scores) - len(wrong)
        acc = (len(scores) - len(wrong)) / len(scores)
        print(
            f"{key:32} {str(result.model)[:28]:28} {acc:6.1%} {result.min_key_confidence:6.2f}"
            f"  {result.latency_ms:6}  {', '.join(wrong[:6])}"
        )
    if totals["fields"]:
        print(
            f"\nfield accuracy: {totals['correct'] / totals['fields']:.1%} over {totals['fields']}"
        )
    return 1 if failures else 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # ₹ and → on Windows consoles
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("keys", nargs="*")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--record", action="store_true", help="save model outputs as fixtures")
    group.add_argument("--replay", action="store_true", help="use saved fixtures, no LLM calls")
    args = parser.parse_args()
    mode = "record" if args.record else "replay" if args.replay else "live"
    sys.exit(asyncio.run(run(args.keys, mode)))


if __name__ == "__main__":
    main()
