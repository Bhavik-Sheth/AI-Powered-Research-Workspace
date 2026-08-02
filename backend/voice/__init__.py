"""Voice Engine — converts between audio and text through whichever engine
is configured (MODULES.md, D37). The one module boundary voice must be
swappable behind: no other module may import an STT/TTS library, name an
engine, or know a model exists. `stub` ships now; `faster_whisper` /
`whisper_cpp` register in this same dict later, lazy-loaded on first call
exactly like `search/reranker.py` and `memory/embedder.py` already are —
the stub itself has nothing to lazily load.
"""

from collections.abc import Awaitable, Callable

import db
import settings
from voice import stub
from voice.models import Transcript

__all__ = ["transcribe", "synthesize"]

_TRANSCRIBE_ENGINES: dict[str, Callable[[bytes, str], Awaitable[Transcript]]] = {"stub": stub.transcribe}
_SYNTHESIZE_ENGINES: dict[str, Callable[[str, str], Awaitable[bytes]]] = {"stub": stub.synthesize}


async def transcribe(audio_bytes: bytes, *, lang: str = "en") -> Transcript:
    async with db.session() as session:
        engine = await settings.get_voice_engine(session)
    handler = _TRANSCRIBE_ENGINES.get(engine, stub.transcribe)
    return await handler(audio_bytes, lang)


async def synthesize(text: str, *, voice: str = "default") -> bytes:
    async with db.session() as session:
        engine = await settings.get_voice_engine(session)
    handler = _SYNTHESIZE_ENGINES.get(engine, stub.synthesize)
    return await handler(text, voice)
