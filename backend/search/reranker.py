"""Cross-encoder rerank (D15) — swappable, no reindex on swap. Lazy-loaded on
first use, guarded by a lock, so search stays usable before it's ready (D2).
"""

import asyncio

from sentence_transformers import CrossEncoder

_MODEL_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_model: CrossEncoder | None = None
_lock = asyncio.Lock()


async def _get_model() -> CrossEncoder:
    global _model
    if _model is None:
        async with _lock:
            if _model is None:
                _model = await asyncio.to_thread(CrossEncoder, _MODEL_ID)
    return _model


async def rerank(query: str, documents: list[str]) -> list[float]:
    """One relevance score per document, in the same order as `documents`."""
    if not documents:
        return []
    model = await _get_model()
    scores = await asyncio.to_thread(model.predict, [(query, doc) for doc in documents])
    return [float(score) for score in scores]
