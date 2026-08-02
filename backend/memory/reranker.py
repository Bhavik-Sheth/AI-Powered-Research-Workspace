"""Cross-encoder rerank for retrieved chunks (D18 node 4). Same model and
lazy-load pattern as `search/reranker.py`; kept as its own copy rather than
a shared import because Memory Index must not depend on Search Federation
(MODULES.md's dependency ordering — Memory Index is listed above it).
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
