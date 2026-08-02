"""`POST /api/voice/transcribe`, `POST /api/voice/synthesize` (TRD, Voice.1).
Route glue only: engine selection and the stub/real split live entirely
inside Voice Engine (Rules.md, D37).
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

import voice
from voice.models import Transcript

router = APIRouter()


class SynthesizeRequest(BaseModel):
    text: str
    voice: str = "default"


@router.post("/api/voice/transcribe", response_model=Transcript)
async def transcribe_audio(request: Request, lang: str = "en") -> Transcript:
    audio_bytes = await request.body()
    return await voice.transcribe(audio_bytes, lang=lang)


@router.post("/api/voice/synthesize")
async def synthesize_speech(body: SynthesizeRequest) -> Response:
    audio_bytes = await voice.synthesize(body.text, voice=body.voice)
    return Response(content=audio_bytes, media_type="audio/wav")
