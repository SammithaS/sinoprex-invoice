"""Chat-model factory: provider switch + fallbacks, structured output.

Text documents:   primary provider → other provider → Gemini lite model (whichever have keys)
Scanned (vision): Gemini → Gemini lite model (Groq is used for text only, per the plan)
"""

from collections.abc import Callable, Sequence
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.llm.schemas import InvoiceRead

Kind = Literal["text", "vision"]
Builder = Callable[[], Runnable[Any, dict[str, Any]]]


class LLMNotConfiguredError(RuntimeError):
    """No API key for any provider that can handle this request."""


def _gemini(settings: Settings, model: str) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model,
        api_key=settings.google_api_key,
        # No temperature: Gemini 3 models use fixed sampling defaults and ignore it.
        max_retries=settings.llm_max_retries,
        timeout=settings.llm_timeout_seconds,
        thinking_level=settings.gemini_thinking_level,
    )


def _groq(settings: Settings) -> BaseChatModel:
    from langchain_groq import ChatGroq

    return ChatGroq(
        model_name=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=0,
        max_retries=settings.llm_max_retries,
        timeout=settings.llm_timeout_seconds,
    )


def _structured(
    model: BaseChatModel, method: str, schema: type[BaseModel]
) -> Runnable[Any, dict[str, Any]]:
    # include_raw keeps the AIMessage so we can record which model answered and token usage.
    return model.with_structured_output(schema, method=method, include_raw=True)  # type: ignore[return-value]


def candidate_models(
    kind: Kind, settings: Settings | None = None, schema: type[BaseModel] = InvoiceRead
) -> list[tuple[str, Runnable[Any, dict[str, Any]]]]:
    """(label, runnable) pairs in fallback order, for providers that have a key."""
    s = settings or get_settings()
    gemini: list[tuple[str, Builder]] = (
        [
            (
                f"gemini:{s.gemini_model}",
                lambda: _structured(_gemini(s, s.gemini_model), "json_schema", schema),
            ),
            (
                f"gemini:{s.gemini_fallback_model}",
                lambda: _structured(_gemini(s, s.gemini_fallback_model), "json_schema", schema),
            ),
        ]
        if s.google_api_key
        else []
    )
    groq: list[tuple[str, Builder]] = (
        [(f"groq:{s.groq_model}", lambda: _structured(_groq(s), "function_calling", schema))]
        if s.groq_api_key and kind == "text"
        else []
    )
    if kind == "vision":
        ordered = gemini
    elif s.llm_provider == "groq":
        ordered = groq + gemini
    else:
        ordered = gemini[:1] + groq + gemini[1:]
    if not ordered:
        need = "GOOGLE_API_KEY" if kind == "vision" else "GOOGLE_API_KEY or GROQ_API_KEY"
        raise LLMNotConfiguredError(f"set {need} in backend/.env to use the LLM ({kind})")
    return [(label, build()) for label, build in ordered]


def extraction_model(
    kind: Kind, settings: Settings | None = None
) -> Runnable[list[BaseMessage], dict[str, Any]]:
    """Structured extraction runnable with fallbacks. Output: {'raw', 'parsed', 'parsing_error'}."""
    return structured_model(InvoiceRead, kind, settings)


def chat_model(
    tools: Sequence[BaseTool] | None = None, settings: Settings | None = None
) -> Runnable[list[BaseMessage], BaseMessage]:
    """Tool-calling chat model (Ask AP): primary text provider → other → Gemini lite."""
    s = settings or get_settings()
    built: list[tuple[str, Callable[[], BaseChatModel]]] = []
    if s.google_api_key:
        built.append(("gemini", lambda: _gemini(s, s.gemini_model)))
    if s.groq_api_key:
        built.append(("groq", lambda: _groq(s)))
    if s.google_api_key:
        built.append(("gemini-lite", lambda: _gemini(s, s.gemini_fallback_model)))
    if s.llm_provider == "groq":
        built.sort(key=lambda t: t[0] != "groq")
    if not built:
        raise LLMNotConfiguredError("set GOOGLE_API_KEY or GROQ_API_KEY in backend/.env (Ask AP)")
    models: list[Runnable[list[BaseMessage], BaseMessage]] = []
    for _, build in built:
        m = build()
        models.append(m.bind_tools(list(tools)) if tools else m)
    primary, rest = models[0], models[1:]
    return primary.with_fallbacks(rest) if rest else primary


def structured_model(
    schema: type[BaseModel], kind: Kind = "text", settings: Settings | None = None
) -> Runnable[list[BaseMessage], dict[str, Any]]:
    """Any structured-output call with the same provider order and fallbacks."""
    models = [r for _, r in candidate_models(kind, settings, schema)]
    primary, rest = models[0], models[1:]
    if not rest:
        return primary
    return primary.with_fallbacks(rest)
