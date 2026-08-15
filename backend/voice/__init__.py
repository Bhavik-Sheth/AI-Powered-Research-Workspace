"""Voice Engine — converts between audio and text through whichever engine
is configured (MODULES.md, D37). The one module boundary voice must be
swappable behind: no other module may import an STT/TTS library, name an
engine, or know a model exists. `stub` ships alongside the first real
profile, `faster_whisper` (Voice Layer Plan V11: one `voice_engine` value
names an engine *profile* — faster-whisper STT paired with Piper TTS — not
two independently selected libraries), lazy-loaded on first call exactly
like `search/reranker.py` and `memory/embedder.py` already are.

The selected engine name is cached in-process after the first DB read
(Voice Layer Plan §2, "decided without a question") so the hot
push-to-talk path stops opening a session per utterance.
`invalidate_engine_cache` lets a caller that just changed the setting
(Settings Store's `set_voice_engine`, Voice.5) drop the stale value
immediately instead of waiting for a restart.

A load or inference failure in the selected engine falls back to the stub
and never breaks the caller's turn (V2) — this module's public entry
points are the sanctioned aggregation boundary (Rules.md, Module Rules
"Errors" #3) for whatever an engine's underlying library can raise; no
narrower catch is possible when the failure mode is "the model file is
corrupt" or "the process is out of memory."
"""

import logging
from collections.abc import Awaitable, Callable

import db
import settings
from voice import faster_whisper, piper, stub
from voice.models import Transcript

__all__ = ["transcribe", "synthesize", "invalidate_engine_cache"]

logger = logging.getLogger(__name__)

_TRANSCRIBE_ENGINES: dict[str, Callable[[bytes, str], Awaitable[Transcript]]] = {
    "stub": stub.transcribe,
    "faster_whisper": faster_whisper.transcribe,
}
_SYNTHESIZE_ENGINES: dict[str, Callable[[str, str], Awaitable[bytes]]] = {
    "stub": stub.synthesize,
    "faster_whisper": piper.synthesize,
}

_cached_engine: str | None = None


def invalidate_engine_cache() -> None:
    """Drops the in-process engine-name cache. Call after `settings.set_voice_engine`
    so the change takes effect on the very next voice call, not the next restart."""
    global _cached_engine
    _cached_engine = None


async def _get_engine() -> str:
    global _cached_engine
    if _cached_engine is None:
        async with db.session() as session:
            _cached_engine = await settings.get_voice_engine(session)
    return _cached_engine


async def transcribe(audio_bytes: bytes, *, lang: str = "en") -> Transcript:
    engine = await _get_engine()
    handler = _TRANSCRIBE_ENGINES.get(engine, stub.transcribe)
    if handler is stub.transcribe:
        return await handler(audio_bytes, lang)
    try:
        return await handler(audio_bytes, lang)
    except Exception:
        logger.exception("event=voice_engine_failed engine=%s direction=transcribe", engine)
        return await stub.transcribe(audio_bytes, lang)


async def synthesize(text: str, *, voice: str = "default") -> bytes:
    engine = await _get_engine()
    handler = _SYNTHESIZE_ENGINES.get(engine, stub.synthesize)
    if handler is stub.synthesize:
        return await handler(text, voice)
    try:
        return await handler(text, voice)
    except Exception:
        logger.exception("event=voice_engine_failed engine=%s direction=synthesize", engine)
        return await stub.synthesize(text, voice)
