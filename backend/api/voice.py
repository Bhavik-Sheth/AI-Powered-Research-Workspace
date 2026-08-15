"""`POST /api/voice/transcribe`, `POST /api/voice/synthesize` (TRD, Voice.1).
Route glue only: engine selection and the stub/real split live entirely
inside Voice Engine (Rules.md, D37).
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import voice
from voice.models import Transcript

router = APIRouter()

# A real push-to-talk clip is a few seconds of compressed WebM/Opus; this is
# a generous ceiling against an empty or runaway body, not a tuned limit.
_MAX_AUDIO_BYTES = 10 * 1024 * 1024


class SynthesizeRequest(BaseModel):
    text: str
    voice: str = "default"


@router.post("/api/voice/transcribe", response_model=Transcript)
async def transcribe_audio(request: Request, lang: str = "en") -> Transcript:
    audio_bytes = await request.body()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="empty audio body")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=400, detail="audio body too large")
    return await voice.transcribe(audio_bytes, lang=lang)


@router.post("/api/voice/synthesize")
async def synthesize_speech(body: SynthesizeRequest) -> Response:
    audio_bytes = await voice.synthesize(body.text, voice=body.voice)
    return Response(content=audio_bytes, media_type="audio/wav")
