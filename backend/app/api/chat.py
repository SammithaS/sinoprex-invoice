"""Ask AP: `POST /chat` streams one answer over SSE; `GET /chat/{thread_id}` restores a chat."""

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field, field_validator

from app.assistant.service import AssistantService, new_thread_id, valid_thread_id

router = APIRouter(tags=["assistant"])

MAX_QUESTION = 2000


def get_assistant(request: Request) -> AssistantService:
    service: AssistantService | None = getattr(request.app.state, "assistant", None)
    if service is None:
        raise HTTPException(status_code=503, detail="the assistant is not available")
    return service


AssistantDep = Annotated[AssistantService, Depends(get_assistant)]


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_QUESTION)
    thread_id: str | None = None

    # Checked here, not in the handler: a streaming handler can't send a 422 once it has started.
    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message is empty")
        return v.strip()

    @field_validator("thread_id")
    @classmethod
    def _thread_id(cls, v: str | None) -> str | None:
        if v is not None and not valid_thread_id(v):
            raise ValueError("invalid thread id")
        return v


class ChatSource(BaseModel):
    citation: str
    kind: str
    title: str
    excerpt: str = ""
    run_id: str | None = None


class ToolUse(BaseModel):
    name: str
    args: dict[str, Any] = {}


class ChatTurn(BaseModel):
    question: str
    answer: str
    citations: list[ChatSource]
    tools: list[ToolUse] = []

    @field_validator("tools", mode="before")
    @classmethod
    def _names_only(cls, v: Any) -> Any:
        # Early chats stored tool names only.
        return [{"name": t} if isinstance(t, str) else t for t in v or []]


class ChatHistoryOut(BaseModel):
    thread_id: str
    turns: list[ChatTurn]


@router.post("/chat", response_class=EventSourceResponse)
async def chat(body: ChatIn, assistant: AssistantDep) -> AsyncIterator[ServerSentEvent]:
    """Events: thread, tool_call, tool_result, token, answer, error (see app.assistant.service)."""
    thread_id = body.thread_id or new_thread_id()
    async for event in assistant.ask(thread_id, body.message):
        yield ServerSentEvent(
            event=str(event["type"]),
            raw_data=json.dumps(event, default=str, ensure_ascii=False),
        )


@router.get("/chat/{thread_id}", response_model=ChatHistoryOut)
async def chat_history(thread_id: str, assistant: AssistantDep) -> dict[str, Any]:
    if not valid_thread_id(thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")
    return {"thread_id": thread_id, "turns": await assistant.history(thread_id)}
