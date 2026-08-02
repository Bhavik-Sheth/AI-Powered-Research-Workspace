"""The stub engine (D37) — canned STT text, silence for TTS. Ships the
full module boundary and plumbing now; `faster-whisper`/Piper drop in
behind this exact interface later without changing any caller — that
swap is the only piece allowed to slip post-v1.
"""

import io
import wave

from voice.models import Transcript

_CANNED_TEXT = "This is a stub transcription — the real speech-to-text engine has not been wired in yet."

_SILENCE_SECONDS = 0.3
_SAMPLE_RATE = 16_000


def _silence_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(b"\x00\x00" * int(_SAMPLE_RATE * _SILENCE_SECONDS))
    return buffer.getvalue()


_SILENCE = _silence_wav()


async def transcribe(_audio_bytes: bytes, _lang: str) -> Transcript:
    return Transcript(text=_CANNED_TEXT)


async def synthesize(_text: str, _voice: str) -> bytes:
    return _SILENCE
