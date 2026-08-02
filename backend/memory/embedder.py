"""The fixed embedding model (D14, invariant #1) — `Alibaba-NLP/gte-modernbert-base`,
768-dim, in-process via `sentence-transformers`, never routed through
Ollama/vLLM. Not configurable; there is no provider/model choice here.
Lazy-loaded on first use, guarded by a lock (mirrors `search/reranker.py`).
"""

import asyncio

from sentence_transformers import SentenceTransformer

_MODEL_ID = "Alibaba-NLP/gte-modernbert-base"
_model: SentenceTransformer | None = None
_lock = asyncio.Lock()


async def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        async with _lock:
            if _model is None:
                _model = await asyncio.to_thread(SentenceTransformer, _MODEL_ID)
    return _model


async def embed(texts: list[str]) -> list[list[float]]:
    """One 768-dim vector per input text, same order."""
    if not texts:
        return []
    model = await _get_model()
    vectors = await asyncio.to_thread(model.encode, texts)
    return [vector.tolist() for vector in vectors]
