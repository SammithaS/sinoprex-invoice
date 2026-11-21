"""Ask AP: tools over cases and the knowledge base, the agent loop, citations, the API.

The tools run for real (SQLite with a processed TechNova case, in-memory knowledge store);
the chat model is scripted, so no LLM is called.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.assistant.agent import (
    MAX_ROUNDS,
    NOT_FOUND,
    build_assistant,
    finalize_answer,
    recent_history,
)
from app.assistant.service import AssistantService, valid_thread_id
from app.assistant.tools import AssistantTools, resolve_vendor
from app.rag.index import reindex
from app.rag.retriever import HybridRetriever
from app.rag.store import MemoryKnowledgeStore
from tests.test_graph import api, db, run_sample, service  # noqa: F401  (fixtures)
from tests.test_rag import HashEmbeddings

TECHNOVA = "technova-contract-rate"
INVOICE = "TN/2026/1042"


@pytest.fixture
async def tools(db: async_sessionmaker[AsyncSession], service: Any) -> AssistantTools:
    store = MemoryKnowledgeStore(HashEmbeddings())
    await reindex(store)
    run = await run_sample(service, TECHNOVA)
    assert run.invoice_number == INVOICE and run.decision == "NEEDS_REVIEW"
    return AssistantTools(db, HybridRetriever(store))


def payload(message: BaseMessage) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(str(message.content))
    return data


# --- tools -------------------------------------------------------------------------------------


async def test_vendor_names_resolve_fuzzily(db: async_sessionmaker[AsyncSession]) -> None:
    async with db() as session:
        assert [v.id for v in await resolve_vendor(session, "technova")][:1] == ["V001"]
        assert [v.id for v in await resolve_vendor(session, "TechNova Solutions")][:1] == ["V001"]
        assert [v.id for v in await resolve_vendor(session, "29AABCT1332L1ZA")] == ["V001"]
        assert await resolve_vendor(session, "Zebra Plumbing Co") == []


async def test_find_cases_filters(tools: AssistantTools) -> None:
    content, sources = await tools.find_cases(vendor="TechNova")
    cases = json.loads(content)["cases"]
    assert [c["invoice_number"] for c in cases] == [INVOICE]
    assert cases[0]["exceptions"] == ["PRICE_VARIANCE"]
    assert sources[0]["citation"] == INVOICE and sources[0]["kind"] == "case"

    content, _ = await tools.find_cases(exception_category="price variance")
    assert [c["invoice_number"] for c in json.loads(content)["cases"]] == [INVOICE]
    content, _ = await tools.find_cases(decision="approved")
    assert json.loads(content)["cases"] == []
    content, sources = await tools.find_cases(decision="maybe")
    assert "error" in json.loads(content) and sources == []
    content, _ = await tools.find_cases(vendor="Nobody Industries")
    assert "no vendor matches" in json.loads(content)["note"]


async def test_get_case_has_the_reason_and_its_citations(tools: AssistantTools) -> None:
    content, sources = await tools.get_case("tn/2026/1042")  # case-blind
    case = json.loads(content)["case"]
    assert case["rules_decision"] == "NEEDS_REVIEW"
    (exc,) = case["exceptions"]
    assert exc["category"] == "PRICE_VARIANCE"
    assert "1,250" in exc["summary"] or "1250" in exc["summary"]
    assert "contracts/technova §2.1" in exc["citations"]
    cited = {s["citation"] for s in sources}
    assert {INVOICE, "contracts/technova §2.1", "ap-policy §3.2"} <= cited
    by_run_id, _ = await tools.get_case(case["run_id"][:8])
    assert json.loads(by_run_id)["case"]["run_id"] == case["run_id"]
    missing, none = await tools.get_case("XX/0000")
    assert "error" in json.loads(missing) and none == []


async def test_search_policies_cites_chunks(tools: AssistantTools) -> None:
    content, sources = await tools.search_policies("price tolerance against the PO", "TechNova")
    results = json.loads(content)["results"]
    assert results and all(r["citation"] for r in results)
    assert results[0]["citation"].startswith("contracts/technova")  # vendor's contract first
    assert any(s["citation"].startswith("ap-policy") for s in sources)
    empty, nothing = await tools.search_policies("   ")
    assert "error" in json.loads(empty) and nothing == []


async def test_case_stats(tools: AssistantTools) -> None:
    content, sources = await tools.case_stats()
    body = json.loads(content)
    assert body["runs"] == 1 and body["exception_causes"] == {"PRICE_VARIANCE": 1}
    assert sources[0]["citation"] == "dashboard"


# --- agent -------------------------------------------------------------------------------------


def _tool_results(messages: Sequence[BaseMessage]) -> dict[str, dict[str, Any]]:
    return {m.name or "": payload(m) for m in messages if isinstance(m, ToolMessage)}


def why_flagged_model(seen: list[int]) -> Any:
    """Scripted: find the case → read it → answer from what the tool returned."""

    async def step(messages: list[BaseMessage]) -> AIMessage:
        seen.append(len(messages))
        results = _tool_results(messages[messages.index(_last_human(messages)) :])
        if "find_cases" not in results:
            return AIMessage("", tool_calls=[_call("find_cases", vendor="TechNova", id="c1")])
        if "get_case" not in results:
            ref = results["find_cases"]["cases"][0]["cite_as"]
            return AIMessage("", tool_calls=[_call("get_case", ref=ref, id="c2")])
        case = results["get_case"]["case"]
        exc = case["exceptions"][0]
        cites = " ".join(f"[{c}]" for c in exc["citations"])
        return AIMessage(
            f"It was flagged for {exc['category']}: {exc['summary']} {cites} "
            f"[{case['cite_as']}] [ap-policy §99]"
        )

    return step


def _last_human(messages: Sequence[BaseMessage]) -> BaseMessage:
    return [m for m in messages if m.type == "human"][-1]


def _call(name: str, id: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args, "id": id, "type": "tool_call"}


def factory(step: Any) -> Any:
    def make(_: Sequence[BaseTool] | None) -> Runnable[list[BaseMessage], BaseMessage]:
        return RunnableLambda(step)

    return make


async def test_why_was_the_technova_invoice_flagged(tools: AssistantTools) -> None:
    """End to end with a scripted model: the right reason, with checked citations."""
    seen: list[int] = []
    graph = build_assistant(tools.as_tools(), factory(why_flagged_model(seen)), InMemorySaver())
    svc = AssistantService(graph)
    events = [e async for e in svc.ask("chat-1", "Why was the TechNova invoice flagged?")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "thread" and kinds[-1] == "answer"
    assert [e["name"] for e in events if e["type"] == "tool_call"] == ["find_cases", "get_case"]
    answer = events[-1]
    assert "PRICE_VARIANCE" in answer["answer"] and "1,250" in answer["answer"]
    cited = [c["citation"] for c in answer["citations"]]
    assert "contracts/technova §2.1" in cited and INVOICE in cited
    assert answer["dropped_citations"] == ["ap-policy §99"]  # not offered by any tool → removed
    assert "§99" not in answer["answer"]
    case_chip = next(c for c in answer["citations"] if c["citation"] == INVOICE)
    assert case_chip["kind"] == "case" and case_chip["run_id"]

    # Same thread: the follow-up sees the earlier turn; history is restorable.
    await _drain(svc.ask("chat-1", "And why was it flagged, again?"))
    assert seen[3] > seen[0]
    history = await svc.history("chat-1")
    assert [t["question"] for t in history] == [
        "Why was the TechNova invoice flagged?",
        "And why was it flagged, again?",
    ]


async def test_tool_loop_is_capped(tools: AssistantTools) -> None:
    calls: list[bool] = []

    def make(bound: Sequence[BaseTool] | None) -> Runnable[list[BaseMessage], BaseMessage]:
        async def step(messages: list[BaseMessage]) -> AIMessage:
            calls.append(bound is not None)
            if bound is None:
                return AIMessage("I could not find it.")
            return AIMessage("", tool_calls=[_call("case_stats", id=f"s{len(calls)}")])

        return RunnableLambda(step)

    svc = AssistantService(build_assistant(tools.as_tools(), make, InMemorySaver()))
    events = [e async for e in svc.ask("chat-2", "loop forever")]
    assert calls == [True] * MAX_ROUNDS + [False]
    assert events[-1]["answer"] == "I could not find it."


async def test_model_failure_is_an_error_event(tools: AssistantTools) -> None:
    async def boom(_: list[BaseMessage]) -> AIMessage:
        raise RuntimeError("503 overloaded")

    svc = AssistantService(build_assistant(tools.as_tools(), factory(boom), InMemorySaver()))
    events = [e async for e in svc.ask("chat-3", "hello")]
    assert events[-1]["type"] == "error" and "503" not in events[-1]["message"]


async def test_a_tool_dump_is_rewritten_as_prose(tools: AssistantTools) -> None:
    def make(bound: Sequence[BaseTool] | None) -> Runnable[list[BaseMessage], BaseMessage]:
        async def step(messages: list[BaseMessage]) -> AIMessage:
            if bound is None:  # the retry, with the nudge
                return AIMessage("It was flagged for a price variance [TN/2026/1042].")
            if not any(isinstance(m, ToolMessage) for m in messages):
                return AIMessage("", tool_calls=[_call("get_case", ref=INVOICE, id="g1")])
            return AIMessage(str(messages[-1].content))  # echoes the JSON

        return RunnableLambda(step)

    svc = AssistantService(build_assistant(tools.as_tools(), make, InMemorySaver()))
    events = [e async for e in svc.ask("chat-4", "why?")]
    assert events[-1]["answer"] == "It was flagged for a price variance [TN/2026/1042]."


def test_dump_detection() -> None:
    from app.assistant.agent import looks_like_dump

    assert looks_like_dump('{"case": {}}')
    assert looks_like_dump("case: cite_as TN/1, run_id 123, status")
    assert not looks_like_dump("Billed ₹1,250 vs ₹1,100 [contracts/technova §2.1].")


def test_empty_answer_says_not_found() -> None:
    from langchain_core.messages import HumanMessage

    out = finalize_answer([HumanMessage("q"), AIMessage("")])
    assert out["answer"] == NOT_FOUND and out["citations"] == []


def test_history_keeps_whole_turns() -> None:
    from langchain_core.messages import HumanMessage

    msgs: list[BaseMessage] = []
    for i in range(10):
        msgs += [
            HumanMessage(f"q{i}"),
            AIMessage("", tool_calls=[_call("case_stats", id=f"t{i}")]),
            ToolMessage("{}", tool_call_id=f"t{i}"),
            AIMessage(f"a{i}"),
        ]
    kept = recent_history(msgs, turns=3)
    assert kept[0].content == "q7" and len(kept) == 12


def test_thread_ids_cannot_reach_run_checkpoints() -> None:
    assert valid_thread_id("chat-0b7c5e7e-1f0e-4b5a-9a4e-7d2f9d4f1c11")
    assert not valid_thread_id("0b7c5e7e-1f0e-4b5a-9a4e-7d2f9d4f1c11")  # a run's thread
    assert not valid_thread_id("chat-../../x")


async def _drain(it: Any) -> None:
    async for _ in it:
        pass


# --- API ---------------------------------------------------------------------------------------


def parse_sse(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.strip().split("\n\n"):
        data = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


async def test_chat_api(tools: AssistantTools, api: AsyncClient, tmp_path: Path) -> None:
    graph = build_assistant(tools.as_tools(), factory(why_flagged_model([])), InMemorySaver())
    api._transport.app.state.assistant = AssistantService(graph)  # type: ignore[attr-defined]

    res = await api.post("/chat", json={"message": "Why was the TechNova invoice flagged?"})
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(res.text)
    thread_id = events[0]["thread_id"]
    assert valid_thread_id(thread_id)
    assert events[-1]["type"] == "answer" and events[-1]["citations"]

    history = (await api.get(f"/chat/{thread_id}")).json()
    assert history["turns"][0]["answer"] == events[-1]["answer"]
    assert history["turns"][0]["tools"][0] == {"name": "find_cases", "args": {"vendor": "TechNova"}}
    assert (await api.get("/chat/not-a-thread")).status_code == 422
    assert (await api.post("/chat", json={"message": ""})).status_code == 422
    bad = await api.post("/chat", json={"message": "hi", "thread_id": "run-1"})
    assert bad.status_code == 422
