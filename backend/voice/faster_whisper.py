"""faster-whisper STT engine (Voice Layer Plan V3) — `base.en`, int8, CPU.
Lazy-loaded behind an `asyncio.Lock` exactly like `memory/embedder.py` and
`search/reranker.py`; the stub engine has nothing to lazily load, this is
the first real one. English-only: `_lang` stays in the transcribe
signature because the registry (`voice/__init__.py`) is engine-agnostic,
but this engine ignores anything but English (V3).

Weights are cached under the vault's `.research-os/` (same pattern as
`writing/tectonic.py`'s compile cache) rather than the user's home
directory, so a fresh vault carries its own model cache and nothing is
written outside it. Voice.4 layers a background pre-fetch of this same
directory on top; until then, the first talk-key press downloads on demand
via `WhisperModel`'s own `download_model` call.
"""

import asyncio
import io

from faster_whisper import WhisperModel, decode_audio

from settings import get_vault_path
from voice.models import Transcript

_MODEL_SIZE = "base.en"

_model: WhisperModel | None = None
_lock = asyncio.Lock()


def _model_dir() -> str:
    path = get_vault_path() / ".research-os" / "voice" / "whisper"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _load_model() -> WhisperModel:
    return WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8", download_root=_model_dir())


async def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        async with _lock:
            if _model is None:
                _model = await asyncio.to_thread(_load_model)
    return _model


def _run_transcription(model: WhisperModel, waveform) -> str:
    # VAD filter (V14) trims non-speech from the already-captured clip so a
    # near-silent press does not hallucinate a sentence — a decode
    # parameter on a fixed clip, not an always-on listening mode (D36).
    segments, _info = model.transcribe(waveform, language="en", vad_filter=True)
    return " ".join(segment.text.strip() for segment in segments).strip()


async def transcribe(audio_bytes: bytes, _lang: str) -> Transcript:
    model = await _get_model()
    # `decode_audio` (V4) is faster-whisper's own public PyAV-backed decoder
    # — it turns whatever container the browser recorded (WebM/Opus, ...)
    # into the 16kHz mono float32 waveform `transcribe` expects when given a
    # numpy array directly, so no other module needs to know the codec.
    waveform = await asyncio.to_thread(decode_audio, io.BytesIO(audio_bytes))
    text = await asyncio.to_thread(_run_transcription, model, waveform)
    return Transcript(text=text)
