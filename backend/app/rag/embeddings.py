"""Local embeddings via FastEmbed (BAAI/bge-small-en-v1.5, 384-d, ONNX, no API key)."""

import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.embeddings import Embeddings

from app.config import get_settings


class FastEmbedEmbeddings(Embeddings):
    """LangChain `Embeddings` over a FastEmbed model; the model loads on first use."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = str(Path(cache_dir).expanduser()) if cache_dir else None
        self._model: Any = None
        self._lock = threading.Lock()  # queries embed in executor threads

    def _load(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(self.model_name, cache_dir=self.cache_dir)
        return self._model

    def warm_up(self) -> None:
        """Load the model now so the first case doesn't pay for it (~2-3 s)."""
        self.embed_query("warm up")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._load().embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        # bge models embed queries with an instruction prefix; FastEmbed's query_embed adds it.
        return [float(x) for x in next(iter(self._load().query_embed(text)))]


@lru_cache
def get_embeddings() -> FastEmbedEmbeddings:
    s = get_settings()
    return FastEmbedEmbeddings(s.embedding_model, s.embedding_cache_dir)
