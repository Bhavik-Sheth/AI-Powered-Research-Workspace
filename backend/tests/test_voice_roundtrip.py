"""Live round-trip (Voice Layer Plan, Voice.8): synthesizes a phrase with
Piper and transcribes it back with faster-whisper, asserting the words
survive. Needs the real weights on disk (fetched by `voice.weights.fetch`,
or downloaded on demand on first call) — excluded from the default run
like every other `live`-marked scenario in this suite (`pytest -m live`
opts in; see `pyproject.toml`'s `addopts`). Goes through `backend/voice/`'s
own engine modules, never the underlying libraries directly, so this test
respects the same D37 boundary `test_voice_boundary.py` enforces.
"""

import asyncio

import pytest

from voice import faster_whisper, piper

pytestmark = pytest.mark.live

_PHRASE = "The quick brown fox jumps over the lazy dog."


def test_piper_to_faster_whisper_round_trip():
    async def _run() -> str:
        wav_bytes = await piper.synthesize(_PHRASE, "default")
        transcript = await faster_whisper.transcribe(wav_bytes, "en")
        return transcript.text

    text = asyncio.run(_run())
    assert _PHRASE.rstrip(".").lower() in text.rstrip(".").lower()
