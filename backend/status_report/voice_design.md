# Voice Engine — Design & Architecture

Voice = a two-function boundary (`transcribe`, `synthesize`) that looks up one setting (`voice_engine`) and dispatches to a registered handler. Today exactly one handler exists — `stub` — which returns canned text and 0.3s of silence; no real STT/TTS library is wired in anywhere in the tree.

---

## Storage / data model

- `Transcript` (`backend/voice/models.py:6`) — the only shape this module defines: `{text: str}`.
- `api_keys.voice_engine` column (`backend/db/models.py:50`) — `String`, `server_default 'stub'`, with a DB check-constraint (`backend/alembic/versions/0001_...py:40`) restricting it to `'stub'`, `'faster_whisper'`, `'whisper_cpp'`. Only `'stub'` has a matching registry entry today, so any other stored value silently falls back to `stub.transcribe`/`stub.synthesize` (`backend/voice/__init__.py:26,33` — `.get(engine, stub.transcribe)`).
- No table stores transcripts, audio, or synthesis output — every call is stateless in, bytes/`Transcript` out.

## Core mechanics

**Engine registry** (`backend/voice/__init__.py:19-20`):
```python
_TRANSCRIBE_ENGINES: dict[str, Callable] = {"stub": stub.transcribe}
_SYNTHESIZE_ENGINES: dict[str, Callable] = {"stub": stub.synthesize}
```
Only `stub` is registered. The module docstring (`__init__.py:1-8`) states `faster_whisper`/`whisper_cpp` will register in the same dict later, lazy-loaded on first call the way `search/reranker.py` and `memory/embedder.py` do — but as of this read, neither file/module exists anywhere under `backend/voice/` or elsewhere in the repo. Both are references to future work, not implementations.

**Selection** — both public functions open a DB session, call `settings.get_voice_engine(session)` (`backend/settings/__init__.py:168-170`, reads `api_keys.voice_engine`), then dict-lookup the handler with `stub` as the fallback default:
- `transcribe(audio_bytes, *, lang="en")` (`__init__.py:23-27`)
- `synthesize(text, *, voice="default")` (`__init__.py:30-34`)

**Stub engine** (`backend/voice/stub.py`):
- `transcribe` ignores its arguments entirely and returns a fixed `Transcript(text="This is a stub transcription — the real speech-to-text engine has not been wired in yet.")` (`stub.py:12,31-32`).
- `synthesize` ignores its arguments and returns a pre-built 0.3s, 16kHz, mono, 16-bit silent WAV (`stub.py:14-29,35-36`), built once at import time via the `wave` module.

**Engine switching** — `settings.set_voice_engine(session, engine)` (`backend/settings/__init__.py:161-165`) exists to persist a different value into `api_keys.voice_engine`, but it has **zero callers** anywhere in the repo (backend or frontend) beyond its own definition. There is no settings-API route and no frontend control that ever calls it, so `voice_engine` can only ever be its DB default, `'stub'` — the registry-swap path is fully built but structurally unreachable today.

## Callers & dependents

**Live path (backend):**
- `backend/api/voice.py` — `POST /api/voice/transcribe` (`voice.py:21-24`, reads raw request body as `audio_bytes`) and `POST /api/voice/synthesize` (`voice.py:27-30`, takes `{text, voice}` JSON, returns `audio/wav`). Both routes call `voice.transcribe`/`voice.synthesize` directly — pure glue, no logic of its own.
- `backend/main.py:177` registers `voice_router` behind `require_bearer_token`, alongside every other API router — the routes are live on the running app.

**Live path (frontend):**
- `frontend/src/voice/useVoice.ts` is the only frontend module touching `getUserMedia`/`MediaRecorder`/`Audio` (per its own docstring, `useVoice.ts:5-9`).
  - `transcribeOnRelease()` (`useVoice.ts:56-67`) POSTs recorded audio to `/api/voice/transcribe` and returns `text`.
  - `playAudio(text)` (`useVoice.ts:69-80`) POSTs to `/api/voice/synthesize` and plays the returned WAV.
- `frontend/src/companion/CompanionPane.tsx` imports `useVoice` (`CompanionPane.tsx:5`) and wires push-to-talk: `handleMicRelease` (`CompanionPane.tsx:353-357`) calls `transcribeOnRelease()` and, if text comes back, calls `sendMessage(text, selection, "voice")` — routing the transcribed text through the exact same Companion send path as typed input, tagged `input_modality: "voice"`. This reaches `ws/__init__.py` (`input_modality` field, `ws/__init__.py:56,157,194`) and `harness.run_turn` (`harness/__init__.py:389,435`), which accepts the tag but (per this file's own reading) does nothing engine-specific with it beyond passing it through.

**Dead / unreachable code found:**
- `useVoice.ts`'s `playAudio` is exported (`useVoice.ts:82`) but never destructured or called anywhere in the frontend (`grep playAudio` finds only its own definition/export) — `/api/voice/synthesize` is a live, reachable HTTP endpoint, but nothing in the shipped UI ever calls it.
- `settings.set_voice_engine` (`backend/settings/__init__.py:161`) has no caller anywhere — no settings API route, no frontend control — so the `voice_engine` DB column can never move off its `'stub'` default through any live path today.
- `faster_whisper`/`whisper_cpp` are named only in comments/docstrings (`backend/voice/__init__.py:1-8`, `stub.py:1-4`) and the DB check-constraint string; no corresponding `.py` file or library import exists.
- App readiness tracks a `"voice"` capability (`backend/main.py:64`, `_INITIAL_READINESS`) but it is initialized to `"pending"` and never transitioned to `"ready"` or `"failed"` anywhere in `main.py` — it stays `"pending"` for the life of the process (same pattern affects `llm`, `search`, `embeddings`, `reranker` in this same dict, so this isn't voice-specific, but it does mean `ReadinessStrip.tsx`'s `voice: "Voice"` label (`ReadinessStrip.tsx:11`) has no live signal feeding it from this module).

## Open questions / rough edges

- The entire "swap in a real engine" story is unreachable: even if `faster_whisper`/`whisper_cpp` handlers existed and were added to the dicts, there is no way for a running instance to actually select them — `set_voice_engine` is orphaned.
- `transcribe`'s `lang` parameter and `synthesize`'s `voice` parameter are accepted by the public functions and threaded down to the stub, but the stub ignores both (`stub.py:31,35` — leading underscore names signal intentional non-use). No current engine reads them, so their contract is unverified.
- Every call opens a fresh `db.session()` just to read one string column (`__init__.py:24-25,31-32`) — on the hot push-to-talk path this is a DB round trip per transcribe/synthesize call for a value that, per the point above, can never actually change at runtime.
- No error handling inside `voice/__init__.py` or `stub.py` — the only error handling in the whole path lives in the frontend (`useVoice.ts` try/catch around each network call). A malformed/empty audio blob is not rejected by the backend at all; the stub happily returns canned text regardless of input.
