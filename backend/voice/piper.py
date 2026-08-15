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
from pathlib import Path

from piper import PiperVoice
from piper.download_voices import download_voice

from settings import get_vault_path

_VOICE_NAME = "en_US-lessac-medium"

_voice: PiperVoice | None = None
_lock = asyncio.Lock()


def _voice_dir() -> Path:
    path = get_vault_path() / ".research-os" / "voice" / "piper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_voice() -> PiperVoice:
    voice_dir = _voice_dir()
    model_path = voice_dir / f"{_VOICE_NAME}.onnx"
    if not model_path.exists():
        # `piper-tts` has no built-in "fetch by name" the way faster-whisper
        # does (V9's job takes over this fetch once Voice.4 lands); this is
        # the same public helper its own CLI (`python -m piper.download_voices`)
        # uses, called synchronously inside this lazy-load's own thread.
        download_voice(_VOICE_NAME, voice_dir)
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
