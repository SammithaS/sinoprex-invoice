"""RAG evaluation and contract-terms fixtures.

    uv run python -m scripts.eval_rag                    # retrieval hit-rate@5 / MRR (memory index)
    uv run python -m scripts.eval_rag --pg               # same against the Neon index
    uv run python -m scripts.eval_rag --answers          # + LLM answers judged against the facts
    uv run python -m scripts.eval_rag --record-contracts # record ContractTerms readings per case
    uv run python -m scripts.eval_rag --answers --write  # also update docs/EVAL_RESULTS.md
    uv run python -m scripts.eval_rag --pg --case technova-contract-rate   # live, end to end

Questions live in data/eval/rag-questions.json: each lists the sections that answer it
(`relevant`, any one counts as a hit) and the facts a correct answer states (`answer_facts`).
Contract fixtures land in tests/fixtures/contract_terms/<case>.json and are replayed by the tests,
so CI never calls an LLM.
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableLambda

from app.extraction import extract_invoice
from app.llm.models import structured_model
from app.llm.schemas import AnswerJudgement, ContractTermsRead, GroundedAnswer
from app.rag.contract_terms import (
    ContractTermsResult,
    contract_terms_for_invoice,
    default_contract_model,
    ground,
)
from app.rag.embeddings import get_embeddings
from app.rag.index import get_knowledge_store, reindex
from app.rag.ingest import Chunk, load_knowledge
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, MemoryKnowledgeStore
from scripts.eval_extraction import replay_factory

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "data" / "eval" / "rag-questions.json"
INVOICES = ROOT / "data" / "test-invoices"
RESULTS = ROOT / "docs" / "EVAL_RESULTS.md"
CONTRACT_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "contract_terms"

ANSWER_SYSTEM = """\
You answer questions from the accounts payable team of Acme Retail Pvt Ltd using ONLY the
excerpts given. Each excerpt starts with its id in square brackets. Answer in one or two
sentences and cite the ids you used. If the excerpts don't answer the question, say so."""

JUDGE_SYSTEM = """\
You grade an answer against a list of expected facts. A fact is covered if the answer states it
(wording may differ; numbers must match). Be strict about numbers, dates and names."""


def load_questions() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    return data


async def memory_store() -> MemoryKnowledgeStore:
    store = MemoryKnowledgeStore(get_embeddings())
    await reindex(store)
    return store


# --- retrieval ---------------------------------------------------------------------------------


async def retrieval_scores(
    retriever: HybridRetriever, questions: list[dict[str, Any]], k: int = 5
) -> list[dict[str, Any]]:
    rows = []
    for q in questions:
        hits = await retriever.search(q["question"], k=k)
        cited = [h.citation for h in hits]
        rank = next((i + 1 for i, c in enumerate(cited) if c in q["relevant"]), None)
        rows.append({"id": q["id"], "rank": rank, "retrieved": cited, "hits": hits})
    return rows


# --- answers + judge ---------------------------------------------------------------------------


async def answer_and_judge(q: dict[str, Any], hits: list[Any]) -> dict[str, Any]:
    excerpts = "\n\n".join(f"[{h.citation}]\n{h.chunk.text}" for h in hits) or "(none)"
    out = await structured_model(GroundedAnswer).ainvoke(
        [
            SystemMessage(ANSWER_SYSTEM),
            HumanMessage(f"Question: {q['question']}\n\nExcerpts:\n\n{excerpts}"),
        ]
    )
    answer = out["parsed"]
    assert isinstance(answer, GroundedAnswer)
    facts = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(q["answer_facts"]))
    verdict = await structured_model(AnswerJudgement).ainvoke(
        [
            SystemMessage(JUDGE_SYSTEM),
            HumanMessage(
                f"Question: {q['question']}\n\nExpected facts:\n{facts}\n\nAnswer: {answer.answer}"
            ),
        ]
    )
    judgement = verdict["parsed"]
    assert isinstance(judgement, AnswerJudgement)
    covered = sum(judgement.facts_covered[: len(q["answer_facts"])])
    score = 0.0 if judgement.contradicts_facts else covered / len(q["answer_facts"])
    cites_ok = bool(set(answer.citations) & set(q["relevant"]))
    return {
        "answer": answer.answer,
        "score": score,
        "cites_relevant": cites_ok,
        "reason": judgement.reason,
    }


# --- contract-terms fixtures -------------------------------------------------------------------


def contract_cases() -> list[tuple[str, str]]:
    """(case key, vendor id) for every test invoice from a vendor in the master."""
    manifest = json.loads((INVOICES / "manifest.json").read_text(encoding="utf-8"))
    out = []
    for m in manifest:
        gt = json.loads((INVOICES / m["ground_truth"]).read_text(encoding="utf-8"))
        if gt.get("vendor_id"):
            out.append((m["key"], gt["vendor_id"]))
    return out


def recording_contract_factory(key: str, retrieved: list[str]) -> Any:
    def factory() -> Runnable[list[BaseMessage], dict[str, Any]]:
        model = default_contract_model()

        async def call(messages: list[BaseMessage]) -> dict[str, Any]:
            out = await model.ainvoke(messages)
            parsed, raw = out.get("parsed"), out.get("raw")
            if isinstance(parsed, ContractTermsRead):
                meta = getattr(raw, "response_metadata", {}) or {}
                usage = getattr(raw, "usage_metadata", None) or {}
                payload = {
                    "key": key,
                    "model": meta.get("model_name") or meta.get("model"),
                    "usage": {
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                    },
                    "retrieved": retrieved,
                    "read": parsed.model_dump(mode="json"),
                }
                CONTRACT_FIXTURES.mkdir(parents=True, exist_ok=True)
                text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
                (CONTRACT_FIXTURES / f"{key}.json").write_text(text, encoding="utf-8", newline="\n")
            return out

        return RunnableLambda(call)

    return factory


def replay_contract_terms(key: str, chunks: list[Chunk] | None = None) -> ContractTermsResult:
    """Ground a recorded reading against the excerpts that were retrieved for it."""
    payload = json.loads((CONTRACT_FIXTURES / f"{key}.json").read_text(encoding="utf-8"))
    pool = chunks if chunks is not None else load_knowledge()
    wanted = set(payload["retrieved"])
    clauses = [c for c in pool if c.citation in wanted]
    vendor_id = clauses[0].vendor_id if clauses else ""
    read = ContractTermsRead.model_validate(payload["read"])
    terms, dropped = ground(read, vendor_id, clauses)
    return ContractTermsResult(
        terms=terms, clauses=clauses, dropped=dropped, model=payload["model"]
    )


def fixed_contract_factory(read: ContractTermsRead) -> Any:
    async def answer(_: list[BaseMessage]) -> dict[str, Any]:
        return {"raw": AIMessage(content=""), "parsed": read, "parsing_error": None}

    return lambda: RunnableLambda(answer)


async def record_contracts(store: KnowledgeStore, keys: list[str]) -> None:
    retriever = HybridRetriever(store)
    for key, vendor_id in contract_cases():
        if keys and key not in keys:
            continue
        pdf = (INVOICES / f"{key}.pdf").read_bytes()
        extraction = await extract_invoice(pdf, model_factory=replay_factory(key))
        from app.rag.contract_terms import retrieve_contract_clauses

        hits = await retrieve_contract_clauses(retriever, vendor_id, extraction.data.lines)
        retrieved = [h.citation for h in hits]
        started = time.perf_counter()
        result = await contract_terms_for_invoice(
            retriever,
            vendor_id,
            extraction.data.vendor_name or vendor_id,
            extraction.data.lines,
            model_factory=recording_contract_factory(key, retrieved),
        )
        ms = int((time.perf_counter() - started) * 1000)
        rates = [f"{r.citation}={r.rate}" for r in (result.terms.rates if result.terms else [])]
        print(f"{key:30} {str(result.model)[:26]:26} {ms:6}ms  {', '.join(rates)}")
        for d in result.dropped:
            print(f"{'':30} dropped: {d}")


async def run_case(store: KnowledgeStore, key: str) -> None:
    """Live: retrieve → LLM reads contract → ground → rules. Prints the price check."""
    from app.rules.decide import evaluate
    from app.rules.models import CaseContext
    from app.rules.reference import load_seed_reference
    from app.rules.vendor import identify_vendor

    pdf = (INVOICES / f"{key}.pdf").read_bytes()
    extraction = await extract_invoice(pdf, model_factory=replay_factory(key))
    ref = load_seed_reference()
    vendor, _ = identify_vendor(extraction.data, ref)
    if vendor is None:
        print("vendor not in master")
        return
    result = await contract_terms_for_invoice(
        HybridRetriever(store), vendor.id, vendor.legal_name, extraction.data.lines
    )
    print(f"model: {result.model}  clauses: {[c.citation for c in result.clauses]}")
    for r in result.terms.rates if result.terms else []:
        print(f"  rate {r.item}: {r.rate} [{r.citation}] {r.valid_from}..{r.valid_to}")
    for d in result.dropped:
        print(f"  dropped: {d}")
    ctx = CaseContext(case_ref=key, received_on=date.today(), contract=result.terms)
    a = evaluate(extraction, ref, ctx)
    print(f"decision: {a.decision.value}  exceptions: {[e.category.value for e in a.exceptions]}")
    price = next(r for r in a.rule_results if r.code == "tolerance.price")
    print(f"price rule: {price.status.value} — {price.message}  {price.citations}")


# --- report ------------------------------------------------------------------------------------


def write_results(
    rows: list[dict[str, Any]], questions: list[dict[str, Any]], backend: str
) -> None:
    n = len(rows)
    hit = sum(1 for r in rows if r["rank"]) / n
    mrr = sum(1 / r["rank"] for r in rows if r["rank"]) / n
    answered = [r for r in rows if "score" in r]
    lines = [
        "## RAG",
        "",
        f"_Run {date.today().isoformat()} · {n} questions (`data/eval/rag-questions.json`) · "
        f"{backend} index · FastEmbed bge-small-en-v1.5 · hybrid (vector + full-text, RRF)._",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Retrieval hit-rate@5 | **{hit:.0%}** |",
        f"| Retrieval MRR@5 | {mrr:.2f} |",
    ]
    if answered:
        acc = sum(r["score"] for r in answered) / len(answered)
        cites = sum(r["cites_relevant"] for r in answered) / len(answered)
        lines += [
            f"| Answer correctness (LLM judge, facts covered) | **{acc:.0%}** |",
            f"| Answers citing a relevant section | {cites:.0%} |",
        ]
    lines += ["", "| # | Question | Rank of first relevant | Answer score |", "|---|---|---|---|"]
    by_id = {q["id"]: q for q in questions}
    for r in rows:
        score = f"{r['score']:.2f}" if "score" in r else "—"
        lines.append(
            f"| {r['id']} | {by_id[r['id']]['question']} | {r['rank'] or 'miss'} | {score} |"
        )
    section = "\n".join(lines) + "\n"
    existing = RESULTS.read_text(encoding="utf-8") if RESULTS.exists() else "# Evaluation results\n"
    if "## RAG" in existing:
        head, _, tail = existing.partition("## RAG")
        rest = tail.split("\n## ", 1)
        existing = head + (("## " + rest[1]) if len(rest) > 1 else "")
    RESULTS.write_text(existing.rstrip() + "\n\n" + section, encoding="utf-8", newline="\n")
    print(f"wrote {RESULTS}")


async def run(args: argparse.Namespace) -> int:
    store: KnowledgeStore
    if args.pg:
        store = get_knowledge_store()
        print("reindex:", await reindex(store))
    else:
        store = await memory_store()
    if args.case:
        await run_case(store, args.case)
        return 0
    if args.record_contracts:
        await record_contracts(store, args.keys)
        return 0
    questions = load_questions()
    rows = await retrieval_scores(HybridRetriever(store), questions)
    for row, q in zip(rows, questions, strict=True):
        if args.answers:
            row.update(await answer_and_judge(q, row["hits"]))
        extra = f"  score {row['score']:.2f}" if "score" in row else ""
        print(f"{q['id']} rank={row['rank'] or 'MISS':>4}  {row['retrieved'][:3]}{extra}")
    n = len(rows)
    print(f"\nhit-rate@5 {sum(1 for r in rows if r['rank']) / n:.0%}")
    if args.answers:
        print(f"answer correctness {sum(r['score'] for r in rows) / n:.0%}")
    if args.write:
        write_results(rows, questions, "Neon PGVector" if args.pg else "in-memory")
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("keys", nargs="*", help="case keys for --record-contracts")
    parser.add_argument("--pg", action="store_true", help="use the Neon index (re-indexes first)")
    parser.add_argument("--answers", action="store_true", help="LLM answers + LLM judge")
    parser.add_argument("--record-contracts", action="store_true")
    parser.add_argument("--case", help="run one test case live, end to end")
    parser.add_argument("--write", action="store_true", help="update docs/EVAL_RESULTS.md")
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
