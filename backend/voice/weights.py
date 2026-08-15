"""Voice model weights — paths under the vault's `.research-os/`, a presence
check, and the blocking fetch itself (Voice Layer Plan V9). Download and
load are deliberately separate concerns: this module only gets bytes onto
disk, via a background job (`jobs/__init__.py`'s `fetch_voice_models_job`)
run once on first launch. `faster_whisper.py`/`piper.py` still load into
RAM lazily on the first talk-key press — from the exact directories this
module owns, so the two never disagree about where a model lives.
"""

import asyncio
from pathlib import Path

from faster_whisper import download_model
from huggingface_hub.errors import LocalEntryNotFoundError
from piper.download_voices import download_voice

from api.health import readiness
from settings import get_vault_path

WHISPER_MODEL_SIZE = "base.en"
PIPER_VOICE_NAME = "en_US-lessac-medium"


def whisper_dir() -> Path:
    path = get_vault_path() / ".research-os" / "voice" / "whisper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def piper_dir() -> Path:
    path = get_vault_path() / ".research-os" / "voice" / "piper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def piper_model_path() -> Path:
    return piper_dir() / f"{PIPER_VOICE_NAME}.onnx"


def present() -> bool:
    """True once both models' weights are on disk — the signal Sidecar
    Bootstrap's lifespan uses to flip the `voice` readiness capability
    straight to `ready` without waiting on a first talk-key press."""
    if not piper_model_path().exists():
        return False
    try:
        download_model(WHISPER_MODEL_SIZE, local_files_only=True, cache_dir=str(whisper_dir()))
    except LocalEntryNotFoundError:
        return False
    return True


def fetch() -> None:
    """Blocking — downloads whichever of the two models is missing. Called
    once, off the request path, by `fetch_voice_models_job`; a caller that
    wants this off the event loop wraps it in `asyncio.to_thread`."""
    download_model(WHISPER_MODEL_SIZE, cache_dir=str(whisper_dir()))
    if not piper_model_path().exists():
        download_voice(PIPER_VOICE_NAME, piper_dir())


async def fetch_voice_models_job(_ctx: dict) -> None:
    """Background one-shot, enqueued by Sidecar Bootstrap's lifespan on any
    launch where a real voice profile is selected and the weights aren't on
    disk yet (V9) — so the first talk-key press never blocks on a cold
    download. Flips the `voice` readiness capability straight to `ready`
    once both models land, or `failed` if the fetch itself fails, without
    waiting for the next launch to notice."""
    try:
        await asyncio.to_thread(fetch)
    except Exception:
        readiness["voice"] = "failed"
        raise
    readiness["voice"] = "ready"
