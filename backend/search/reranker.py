"""Cross-encoder rerank (D15) — swappable, no reindex on swap. Lazy-loaded on
first use, guarded by a lock, so search stays usable before it's ready (D2).

The first load fetches `cross-encoder/ms-marco-MiniLM-L-6-v2` from the
Hugging Face Hub with no local cache — ~91MB of weights plus a small
tokenizer. `asyncio.to_thread` cannot be cancelled once its thread has
started, so a stalled or offline network turns that download into an
unbounded hang. `_get_model` bounds the *wait* for that load with
`asyncio.wait_for`, not the load itself: the in-flight thread keeps running
behind an `asyncio.shield`, so a caller that times out neither kills the
download nor holds `_lock` — the next call (or the same caller retried
later) attaches to the same in-flight load and gets it once it lands,
instead of restarting the download from scratch. `rerank` degrades exactly
the way one literature source timing out already degrades `search_papers`
(Rules.md: "Partial source failure degrades, it does not fail") — it lets
the timeout propagate rather than swallowing it, so the caller decides how
to name the degradation.
"""

import asyncio

from sentence_transformers import CrossEncoder

_MODEL_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# ~91MB of weights with no local cache. Bounded low enough that a stalled or
# offline network never blocks a search; the download this frees keeps
# running and is picked up by the next call (or this same call, retried) if
# it finishes after the fact.
_MODEL_LOAD_TIMEOUT_S = 30.0

_model: CrossEncoder | None = None
_load_task: "asyncio.Task[CrossEncoder] | None" = None
_lock = asyncio.Lock()


def _on_load_done(task: "asyncio.Task[CrossEncoder]") -> None:
    """Runs once the shared load finishes, however many callers timed out
    waiting on it. Success caches the model for every future call; failure
    (not a caller's timeout — the load itself erroring) clears `_load_task`
    so the next call retries with a fresh download instead of replaying a
    stale exception forever."""
    global _model, _load_task
    if not task.cancelled() and task.exception() is None:
        _model = task.result()
    _load_task = None


async def _get_model() -> CrossEncoder:
    global _load_task

    if _model is not None:
        return _model

    async with _lock:
        if _model is not None:
            return _model
        if _load_task is None:
            _load_task = asyncio.create_task(
                asyncio.to_thread(CrossEncoder, _MODEL_ID), name="reranker-model-load"
            )
            _load_task.add_done_callback(_on_load_done)
        load_task = _load_task

    # `shield` keeps `load_task` running even if this wait times out or this
    # coroutine itself is cancelled — only the wrapper future awaited here
    # is torn down, never the shared load.
    return await asyncio.wait_for(asyncio.shield(load_task), timeout=_MODEL_LOAD_TIMEOUT_S)


async def rerank(query: str, documents: list[str]) -> list[float]:
    """One relevance score per document, in the same order as `documents`.

    Raises `TimeoutError` if the cross-encoder isn't loaded within
    `_MODEL_LOAD_TIMEOUT_S`; callers degrade the same way `search_papers`
    already degrades a failing literature source rather than blocking."""
    if not documents:
        return []
    model = await _get_model()
    scores = await asyncio.to_thread(model.predict, [(query, doc) for doc in documents])
    return [float(score) for score in scores]
