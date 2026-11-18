"""Runs Ask-AP turns and turns the graph's stream into chat events for SSE.

Events (in order): `thread` {thread_id} · `tool_call` {name, args} · `tool_result` {name, ok,
sources} · `token` {text} (draft answer text; a tool call after it discards it) · `answer`
{question, answer, citations, …} (the final, citation-checked answer) · `error` {message}.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.assistant.agent import message_text

log = logging.getLogger(__name__)

THREAD_PREFIX = "chat-"


def new_thread_id() -> str:
    return f"{THREAD_PREFIX}{uuid.uuid4()}"


def valid_thread_id(thread_id: str) -> bool:
    if not thread_id.startswith(THREAD_PREFIX):
        return False  # never read a run's checkpoint thread through the chat API
    try:
        uuid.UUID(thread_id.removeprefix(THREAD_PREFIX))
    except ValueError:
        return False
    return True


class AssistantService:
    def __init__(self, graph: CompiledStateGraph[Any, Any, Any, Any]) -> None:
        self.graph = graph

    @staticmethod
    def _config(thread_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}

    async def history(self, thread_id: str) -> list[dict[str, Any]]:
        snapshot = await self.graph.aget_state(self._config(thread_id))
        answers: list[dict[str, Any]] = (snapshot.values or {}).get("answers", [])
        return answers

    async def ask(self, thread_id: str, question: str) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "thread", "thread_id": thread_id}
        payload = {"messages": [HumanMessage(question)], "rounds": 0}
        try:
            stream = self.graph.astream(
                payload, self._config(thread_id), stream_mode=["messages", "updates"]
            )
            async for item in stream:
                mode, data = cast(tuple[str, Any], item)
                if mode == "messages":
                    chunk, meta = cast(tuple[BaseMessage, dict[str, Any]], data)
                    if meta.get("langgraph_node") != "agent":
                        continue
                    if not isinstance(chunk, AIMessageChunk | AIMessage):
                        continue
                    if getattr(chunk, "tool_call_chunks", None) or chunk.tool_calls:
                        continue
                    text = message_text(chunk)
                    if text:
                        yield {"type": "token", "text": text}
                    continue
                for node, update in cast(dict[str, Any], data or {}).items():
                    for event in self._update_events(node, update or {}):
                        yield event
        except Exception as exc:
            log.exception("assistant turn failed")
            yield {"type": "error", "message": _friendly(exc)}

    @staticmethod
    def _update_events(node: str, update: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if node == "agent":
            for m in update.get("messages", []):
                if isinstance(m, AIMessage):
                    for call in m.tool_calls or []:
                        events.append(
                            {"type": "tool_call", "name": call["name"], "args": call["args"]}
                        )
        elif node == "tools":
            for m in update.get("messages", []):
                if isinstance(m, ToolMessage):
                    sources = m.artifact if isinstance(m.artifact, list) else []
                    events.append(
                        {
                            "type": "tool_result",
                            "name": m.name,
                            "ok": m.status != "error",
                            "sources": len(sources),
                        }
                    )
        elif node == "finalize":
            for answer in update.get("answers", []):
                events.append({"type": "answer", **answer})
        return events


def _friendly(exc: Exception) -> str:
    from app.llm.models import LLMNotConfiguredError

    if isinstance(exc, LLMNotConfiguredError):
        return str(exc)
    return "The assistant could not answer right now (the language model failed). Try again."
