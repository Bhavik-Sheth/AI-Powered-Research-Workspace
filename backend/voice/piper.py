"""Piper TTS engine (Voice Layer Plan V8) — `en_US-lessac-medium`, in-process
via the `piper-tts` package. Lazy-loaded behind an `asyncio.Lock`, same
shape as `faster_whisper.py`. Registered under the `faster_whisper` engine
*profile* in `voice/__init__.py` (V11): one `voice_engine` value selects
faster-whisper for STT and Piper for TTS, not two independently selected
columns. No `speech-dispatcher` fallback engine — the stub already serves
as the degradation path (V8).
"""

import asyncio
import io
import wave

from piper import PiperVoice
from piper.download_voices import download_voice

from voice import weights

_voice: PiperVoice | None = None
_lock = asyncio.Lock()


def _load_voice() -> PiperVoice:
    model_path = weights.piper_model_path()
    if not model_path.exists():
        # `piper-tts` has no built-in "fetch by name" the way faster-whisper
        # does; `weights.fetch()` pre-populates this from a background job
        # on first launch (V9) — if that hasn't run yet, fall back to the
        # same public helper its own CLI uses, synchronously in this
        # lazy-load's own thread.
        download_voice(weights.PIPER_VOICE_NAME, weights.piper_dir())
    return PiperVoice.load(model_path)


async def _get_voice() -> PiperVoice:
    global _voice
    if _voice is None:
        async with _lock:
            if _voice is None:
                _voice = await asyncio.to_thread(_load_voice)
    return _voice


def _synthesize_wav(voice: PiperVoice, text: str) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)
    return buffer.getvalue()


async def synthesize(text: str, _voice_name: str) -> bytes:
    voice = await _get_voice()
    return await asyncio.to_thread(_synthesize_wav, voice, text)
