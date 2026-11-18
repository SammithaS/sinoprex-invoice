"""End-to-end evaluation (plan §9): every sample through the real LangGraph pipeline.

    uv run python -m scripts.eval                  # replayed LLM outputs, no API calls
    uv run python -m scripts.eval --live           # real LLM calls (extract, contracts, explain)
    uv run python -m scripts.eval --live --rag     # + RAG retrieval and judged answers
    uv run python -m scripts.eval --live --rag --write   # → docs/EVAL_RESULTS.md + latest.json
    uv run python -m scripts.eval northwind-clean technova-contract-rate   # some cases only

Each run is isolated: a fresh SQLite database with the seed data, an in-memory checkpointer and an
in-memory knowledge index (same chunks and embeddings as Neon), so the samples see exactly the
history the ground truth assumes, in manifest order, and Neon is never touched.

Per case: rules' decision vs ground truth, exception categories, cited sources, field-level
extraction accuracy, wall time until the case pauses or closes, and LLM calls / tokens (counted
by a callback on every chat-model call, failed calls included). A case that fails on a provider
error (503 / 429) is rerun, as an operator would, and the attempts are reported.
"""

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.tracers.context import register_configure_hook
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.db.models import Base, CaseException, Run
from app.db.seed import seed_reference_data
from app.extraction import ExtractionResult, extract_invoice
from app.extraction.models import LineItem
from app.graph import deps as deps_module
from app.graph.builder import build_graph
from app.graph.deps import GraphDeps
from app.graph.explain import Explanation, template_explanation
from app.graph.repo import clear_reference_cache
from app.graph.runner import RunService
from app.rag.contract_terms import ContractTermsResult
from app.rules.models import Vendor
from scripts.eval_extraction import compare, replay_factory

ROOT = Path(__file__).resolve().parents[2]
INVOICES = ROOT / "data" / "test-invoices"
RESULTS = ROOT / "docs" / "EVAL_RESULTS.md"
LATEST = ROOT / "data" / "eval" / "latest.json"
LAST_RUN = ROOT / "data" / "eval" / "last-run.json"  # full report of the last run, git-ignored
TODAY = date(2026, 11, 18)  # the date the ground truth assumes (due dates, contract validity)
PROVIDER_ERRORS = ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded", "Timeout")


# --- LLM usage ---------------------------------------------------------------------------------


class Usage(BaseCallbackHandler):
    """Counts chat-model calls and tokens (structured-output calls included)."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.calls = 0
        self.errors = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.models: dict[str, int] = {}

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        first = response.generations[0] if response.generations else []
        gen = first[0] if first else None
        msg = gen.message if isinstance(gen, ChatGeneration) else None
        with self._lock:
            self.calls += 1
            if isinstance(msg, AIMessage):
                usage: dict[str, Any] = dict(msg.usage_metadata or {})
                self.input_tokens += int(usage.get("input_tokens", 0))
                self.output_tokens += int(usage.get("output_tokens", 0))
                name = str(msg.response_metadata.get("model_name") or "?")
                self.models[name] = self.models.get(name, 0) + 1

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            self.calls += 1
            self.errors += 1


_usage_var: ContextVar[Usage | None] = ContextVar("eval_usage", default=None)
register_configure_hook(_usage_var, inheritable=True)


@contextmanager
def track_usage() -> Iterator[Usage]:
    usage = Usage()
    token = _usage_var.set(usage)
    try:
        yield usage
    finally:
        _usage_var.reset(token)


# --- the pipeline under test -------------------------------------------------------------------


async def _replay_extract(pdf: bytes, key: str | None, hints: Any = ()) -> ExtractionResult:
    assert key
    return await extract_invoice(pdf, model_factory=replay_factory(key))


async def _replay_contract(
    vendor: Vendor, lines: list[LineItem], key: str | None
) -> ContractTermsResult:
    from scripts.eval_rag import CONTRACT_FIXTURES, replay_contract_terms

    if key and (CONTRACT_FIXTURES / f"{key}.json").exists():
        return replay_contract_terms(key)
    return ContractTermsResult(terms=None, clauses=[], note="no recording")


async def _template_explain(data: Any, assessment: Any, final: Any, *_: Any) -> Explanation:
    return template_explanation(data, assessment, final)


async def make_service(live: bool, workdir: Path) -> tuple[RunService, AsyncEngine]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{workdir / 'eval.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await seed_reference_data(session)
    clear_reference_cache()
    if live:
        # Contract retrieval reads the knowledge index: point it at an in-memory copy of the
        # same chunks (real FastEmbed embeddings), so Neon isn't needed or touched.
        from scripts.eval_rag import memory_store

        store = await memory_store()
        setattr(deps_module, "get_knowledge_store", lambda: store)  # noqa: B010
        deps = GraphDeps(sessionmaker=maker, today=lambda: TODAY)
    else:
        deps = GraphDeps(
            sessionmaker=maker,
            extract=_replay_extract,
            contract_terms=_replay_contract,
            explain=_template_explain,
            today=lambda: TODAY,
        )
    return RunService(build_graph(deps, InMemorySaver()), maker, workdir / "uploads"), engine


def _provider_error(error: str | None) -> bool:
    text = (error or "").lower()
    return any(p.lower() in text for p in PROVIDER_ERRORS)


async def run_case(
    service: RunService, case: dict[str, Any], retries: int, backoff: float
) -> dict[str, Any]:
    gt = json.loads((INVOICES / case["ground_truth"]).read_text(encoding="utf-8"))
    pdf = (INVOICES / case["file"]).read_bytes()
    expected = gt["expected"]
    # ERP_MISMATCH comes from the read-back after posting (the sample's `erp_fault`), not the rules.
    erp = ["ERP_MISMATCH"] if gt.get("erp_fault") else []
    want_exc = sorted({*(expected.get("exceptions") or []), *erp})
    attempts = 0
    rerun_of: str | None = None
    seconds = 0.0
    with track_usage() as usage:
        while True:
            attempts += 1
            started = time.perf_counter()
            run = await service.create_run(
                pdf, case["file"], source="SAMPLE", sample_key=case["key"], rerun_of=rerun_of
            )
            await service.wait(run.id)
            seconds = time.perf_counter() - started
            async with service.sessionmaker() as session:
                row = await session.get(Run, run.id)
                assert row is not None
                run = row
            if run.status != "FAILED" or attempts > retries or not _provider_error(run.error):
                break
            rerun_of = run.id
            await asyncio.sleep(backoff * attempts)
    async with service.sessionmaker() as session:
        recorded = list(
            await session.scalars(select(CaseException).where(CaseException.run_id == run.id))
        )
    categories = sorted({e.category for e in recorded})
    state = run.state or {}
    out: dict[str, Any] = {
        "key": case["key"],
        "status": run.status,
        "attempts": attempts,
        "seconds": round(seconds, 1),
        "decision": run.decision,
        "expected_decision": expected["decision"],
        "decision_ok": run.decision == expected["decision"],
        "exceptions": categories,
        "expected_exceptions": want_exc,
        "exceptions_ok": categories == want_exc,
        "llm_calls": usage.calls,
        "llm_errors": usage.errors,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "models": usage.models,
        "error": run.error,
    }
    # What the case cites: the rules' citations plus its recorded exceptions' (e.g. the ERP
    # read-back exception raised after posting).
    cited = set((state.get("assessment") or {}).get("citations") or [])
    cited |= {c for e in recorded for c in e.citations or []}
    want = set(expected.get("citations") or [])
    out["citations_missing"] = sorted(want - cited)
    out["citations_ok"] = want <= cited
    if state.get("extraction"):
        scores = compare(ExtractionResult.model_validate(state["extraction"]), gt)
        out["fields_total"] = len(scores)
        out["fields_correct"] = sum(scores.values())
        out["wrong_fields"] = [k for k, ok in scores.items() if not ok]
    return out


# --- summary + report --------------------------------------------------------------------------


def _pct(n: float, d: float) -> float | None:
    return round(n / d, 4) if d else None


def _per_invoice(rows: list[dict[str, Any]], *keys: str) -> int | None:
    return round(sum(sum(r[k] for k in keys) for r in rows) / len(rows)) if rows else None


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in rows if r["status"] != "FAILED"]
    n = len(rows)
    fields_total = sum(r.get("fields_total", 0) for r in rows)
    secs = sorted(r["seconds"] for r in done)
    return {
        "cases": n,
        "failed": n - len(done),
        "decision_accuracy": _pct(sum(r["decision_ok"] for r in rows), n),
        "exception_accuracy": _pct(sum(r["exceptions_ok"] for r in rows), n),
        "citation_coverage": _pct(sum(r["citations_ok"] for r in rows), n),
        "field_accuracy": _pct(sum(r.get("fields_correct", 0) for r in rows), fields_total),
        "fields_scored": fields_total,
        "cases_all_fields_right": sum(
            1 for r in rows if r.get("fields_total") and not r.get("wrong_fields")
        ),
        "median_seconds": round(statistics.median(secs), 1) if secs else None,
        "p90_seconds": round(secs[int(0.9 * (len(secs) - 1))], 1) if secs else None,
        "max_seconds": secs[-1] if secs else None,
        "llm_calls_per_invoice": round(sum(r["llm_calls"] for r in rows) / n, 2) if n else None,
        "llm_errors": sum(r["llm_errors"] for r in rows),
        "tokens_per_invoice": _per_invoice(rows, "input_tokens", "output_tokens"),
        "input_tokens_per_invoice": _per_invoice(rows, "input_tokens"),
        "output_tokens_per_invoice": _per_invoice(rows, "output_tokens"),
        "reruns_for_provider_errors": sum(r["attempts"] - 1 for r in rows),
    }


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.1%}"


def _case_row(r: dict[str, Any]) -> str:
    dec = r["decision"] or r["status"]
    if not r["decision_ok"]:
        dec = f"**{dec}** (want {r['expected_decision']})"
    exc = ", ".join(r["exceptions"]) or "—"
    if not r["exceptions_ok"]:
        exc = f"**{exc}** (want {', '.join(r['expected_exceptions']) or '—'})"
    fields = "—"
    if r.get("fields_total"):
        fields = f"{r['fields_correct']}/{r['fields_total']}"
        if r.get("wrong_fields"):
            fields += f" ({', '.join(r['wrong_fields'][:3])})"
    tokens = r["input_tokens"] + r["output_tokens"]
    again = f" ×{r['attempts']}" if r["attempts"] > 1 else ""
    return (
        f"| {r['key']} | {dec} | {exc} | {fields} | {r['seconds']} s{again} | "
        f"{r['llm_calls']} | {tokens:,} |"
    )


def pipeline_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    mode = "live LLM calls" if report["live"] else "replayed LLM outputs"
    models = ", ".join(report["models"]) or "—"
    command = "python -m scripts.eval" + (" --live" if report["live"] else "")
    head = (
        f"_Run {report['run_at'][:10]} · {s['cases']} samples in manifest order · {mode} · "
        f"models: {models} · fresh SQLite + seed data, in-memory index · `{command}`._"
    )
    tokens = "—"
    if s["tokens_per_invoice"] is not None:
        tokens = (
            f"{s['tokens_per_invoice']:,} ({s['input_tokens_per_invoice']:,} in + "
            f"{s['output_tokens_per_invoice']:,} out)"
        )
    times = f"{s['median_seconds']} s / {s['p90_seconds']} s / {s['max_seconds']} s"
    fields = f"Field-level extraction accuracy ({s['fields_scored']} fields)"
    metrics = [
        (
            "Decision accuracy (rules' decision vs ground truth)",
            f"**{_fmt(s['decision_accuracy'])}**",
        ),
        ("Exception categories exactly right", _fmt(s["exception_accuracy"])),
        ("Expected citations present", _fmt(s["citation_coverage"])),
        (fields, f"**{_fmt(s['field_accuracy'])}**"),
        ("Invoices with every field right", f"{s['cases_all_fields_right']} / {s['cases']}"),
        ("Time to decision: median / p90 / max", times),
        ("LLM calls per invoice", str(s["llm_calls_per_invoice"])),
        ("Tokens per invoice", tokens),
        ("Failed LLM calls (caught by fallbacks)", str(s["llm_errors"])),
        ("Case reruns after provider errors (503/429)", str(s["reruns_for_provider_errors"])),
        ("Cases that ended FAILED", str(s["failed"])),
    ]
    lines = [
        "## Pipeline (extraction → rules → decision)",
        "",
        head,
        "",
        "| Metric | Value |",
        "|---|---|",
        *(f"| {name} | {value} |" for name, value in metrics),
        "",
        "| Case | Decision | Exceptions | Fields | Time | LLM calls | Tokens |",
        "|---|---|---|---|---|---|---|",
        *(_case_row(r) for r in report["cases"]),
    ]
    return "\n".join(lines) + "\n"


def replace_section(markdown: str, title_prefix: str, section: str) -> str:
    """Replace the `## <title_prefix>…` section (up to the next `## `), or insert it first."""
    parts = markdown.split("\n## ")
    head, rest = parts[0], parts[1:]
    kept = [p for p in rest if not p.startswith(title_prefix.removeprefix("## "))]
    body = section.strip().removeprefix("## ")
    return "\n## ".join([head.rstrip() + "\n", body + "\n", *kept]).rstrip() + "\n"


# --- main ----------------------------------------------------------------------------------------


def _row_tail(r: dict[str, Any]) -> str:
    fields = f"{r['fields_correct']}/{r['fields_total']}" if r.get("fields_total") else "—"
    tokens = r["input_tokens"] + r["output_tokens"]
    notes = [
        f"×{r['attempts']}" if r["attempts"] > 1 else "",
        "" if r["decision_ok"] else f"want {r['expected_decision']}",
        "" if r["exceptions_ok"] else f"exc {r['exceptions']} want {r['expected_exceptions']}",
        "" if r["citations_ok"] else f"missing cites {r['citations_missing']}",
        f"wrong: {', '.join(r['wrong_fields'][:5])}" if r.get("wrong_fields") else "",
        f"ERROR {(r['error'] or '')[:100]}" if r["status"] == "FAILED" else "",
    ]
    return f"{fields:>7} {r['seconds']:6.1f}s {r['llm_calls']:5} {tokens:7}  " + "  ".join(
        n for n in notes if n
    )


async def run(args: argparse.Namespace) -> int:
    manifest = json.loads((INVOICES / "manifest.json").read_text(encoding="utf-8"))
    cases = [m for m in manifest if not args.keys or m["key"] in args.keys]
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="invoiceiq-eval-") as tmp:
        service, engine = await make_service(args.live, Path(tmp))
        print(f"{'case':32} {'decision':13} ok  {'fields':>7} {'time':>7} calls  tokens")
        for case in cases:
            r = await run_case(service, case, args.retries if args.live else 0, args.backoff)
            rows.append(r)
            ok = "ok" if r["decision_ok"] and r["exceptions_ok"] else "XX"
            print(f"{r['key']:32} {r['decision'] or r['status']!s:13} {ok:3} " + _row_tail(r))
        await engine.dispose()
    models: dict[str, int] = {}
    for r in rows:
        for m, c in r["models"].items():
            models[m] = models.get(m, 0) + c
    report: dict[str, Any] = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "live": args.live,
        "configured_model": get_settings().gemini_model,
        "models": sorted(models, key=lambda m: -models[m]),
        "summary": summarise(rows),
        "cases": rows,
    }
    print("\n" + json.dumps(report["summary"], indent=2))
    if args.rag:
        report["rag"] = await run_rag(answers=args.live, write=args.write)
    # Always keep the full report (git-ignored), so a run's numbers are never only in a log.
    LAST_RUN.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"saved {LAST_RUN}")
    if args.write:
        if not args.keys:
            LATEST.parent.mkdir(parents=True, exist_ok=True)
            slim = {k: v for k, v in report.items() if k != "cases"}
            LATEST.write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(f"wrote {LATEST}")
        existing = "# Evaluation results\n"
        if RESULTS.exists():
            existing = RESULTS.read_text(encoding="utf-8")
        RESULTS.write_text(
            replace_section(existing, "## Pipeline", pipeline_markdown(report)),
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote {RESULTS}")
    return 0 if report["summary"]["failed"] == 0 else 1


async def run_rag(answers: bool, write: bool) -> dict[str, Any]:
    from app.rag.retriever import HybridRetriever
    from scripts.eval_rag import (
        answer_and_judge,
        load_questions,
        memory_store,
        retrieval_scores,
        write_results,
    )

    questions = load_questions()
    rows = await retrieval_scores(HybridRetriever(await memory_store()), questions)
    if answers:
        by_id = {q["id"]: q for q in questions}
        for r in rows:
            for attempt in range(3):
                try:
                    r.update(await answer_and_judge(by_id[r["id"]], r["hits"]))
                    break
                except Exception as exc:
                    if attempt == 2 or not _provider_error(str(exc)):
                        raise
                    await asyncio.sleep(20)
    n = len(rows)
    out: dict[str, Any] = {
        "questions": n,
        "hit_rate_at_5": round(sum(1 for r in rows if r["rank"]) / n, 4),
        "mrr_at_5": round(sum(1 / r["rank"] for r in rows if r["rank"]) / n, 4),
        "misses": [r["id"] for r in rows if not r["rank"]],
    }
    judged = [r for r in rows if "score" in r]
    if judged:
        out["answer_correctness"] = round(sum(r["score"] for r in judged) / len(judged), 4)
        out["answers_citing_relevant"] = round(
            sum(r["cites_relevant"] for r in judged) / len(judged), 4
        )
    print("\nRAG:", json.dumps(out))
    if write:
        write_results(rows, questions, "in-memory (same chunks as Neon)")
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("keys", nargs="*", help="case keys (default: all, manifest order)")
    parser.add_argument("--live", action="store_true", help="call the real LLMs")
    parser.add_argument("--rag", action="store_true", help="also run the RAG eval")
    parser.add_argument("--write", action="store_true", help="update EVAL_RESULTS.md, latest.json")
    parser.add_argument("--retries", type=int, default=3, help="reruns on 503/429 (live)")
    parser.add_argument("--backoff", type=float, default=20, help="seconds × attempt, reruns")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
