"""Cross-encoder rerank of the feed's top-N candidates (D28). Same model and
lazy-load pattern as `search/reranker.py` and `memory/reranker.py`; kept as
its own copy rather than a shared import for the same reason those two are
separate — Research Feed must not depend on Search Federation, and reaching
into Memory Index's internals for this would cross its declared public
interface (MODULES.md lists only `chunk_and_embed_job`/`query_memory`).
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
