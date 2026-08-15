# Voice Layer Plan — real engines behind the D37 boundary

Companion to `PLANNER/ImplementationPlan.md` (which stops at **Voice.1**, the stub) and to
`backend/status_report/voice_design.md` (the audit of what actually shipped). `DECISIONS.md`
(D1–D37) remains the architectural authority — where this plan departs from `TRD.md`, `PRD.md` or
`Schema.md`, the departure is listed in §8 and the source document gets amended, never quietly
contradicted.

Decisions taken in this grill session are labelled **V1–V15**. They are voice-scoped and do not
renumber anything in `DECISIONS.md`.

---

## Progress report (2026-08-15) — read this before resuming

**Committed and done: Voice.2 through Voice.6.** Commits `e0307fb`, `d39d3e2`, `9336a28`,
`43e2a5f`, `7015647` on `main`. Real faster-whisper STT, real Piper TTS, a weight-fetch job with a
live `pending → ready` readiness signal, a Settings voice section (engine selector + rebindable
push-to-talk key, `set_voice_engine`'s first caller), and the talk-key state machine with its 2s
cancel window. All five phases live-verified against a real Postgres/Docker stack and the actual
running app (Playwright), 54/54 backend tests green throughout, frontend builds clean.

**The "Undo race" flagged in the previous version of this note was a test-script timing fluke, not
a real bug** — re-run with a shimmed `MediaRecorder` (this sandbox's fake audio device doesn't
reliably drive `MediaRecorder`'s stop/dataavailable events at all, confirmed on the *unmodified*
pre-existing mouse mic button too) and a mocked `/api/voice/transcribe`, `cancelPendingVoiceMessage`
correctly prevented the send in 4/4 repeated runs, and letting the window elapse without cancelling
sent it exactly once. No code change was needed in `useVoice.ts` beyond the debug logging added and
then removed while diagnosing. Root cause of the one earlier failure: `page.evaluate`'s click was
issued too close to (and once, past) the 2s window in that particular run — not a state-machine
defect.

**Next up: Voice.7 (streaming sentence-by-sentence playback + barge-in), then Voice.8 (tests + the
Voice sign-off checkpoint)** — see the build order below. Voice.7 depends on Voice.6 and is not yet
started.

---

## 1. The premise

**Voice.1 already shipped.** The D37 module boundary, the engine registry, both REST routes, the
`useVoice` hook, push-to-talk capture and the `input_modality: "voice"` tag are live and working.
The v1 scope floor (PRD §3, Grill R1) is therefore **already satisfied**.

What is missing is the half that makes voice worth using: a real engine on either end, and a reply
you can actually hear. The audit found the exact shape of the hole:

- `stub.transcribe` ignores its input and returns one canned sentence.
- `stub.synthesize` ignores its input and returns 0.3 s of silence.
- `settings.set_voice_engine` has **zero callers** — `voice_engine` can never leave `'stub'`.
- `useVoice.playAudio` is exported and **never called** — nothing in the UI ever speaks.
- The `voice` readiness capability is initialised `"pending"` and never transitions.

This plan closes all five, and nothing else. **The boundary itself is not redesigned** — the two
public signatures, the registry, and the mirror module are correct as built.

### The load-bearing property, restated

Voice produces text and consumes text. **The agent cannot tell how a turn arrived.** Every decision
below is checked against that: if a change would make a spoken turn take a different code path
through the harness than a typed one, it is wrong regardless of how good it feels.

---

## 2. Decisions (V1–V15)

| # | Decision | Rationale |
|---|---|---|
| **V1** | Ship **real STT and real TTS**, and make replies actually speak. | Closes US8 literally: "hold a key, speak to the Companion, and hear the reply." |
| **V2** | **No spike.** Build straight through; a model-load failure sets the `voice` capability to `failed` and falls back to the stub, never crashing a turn. | PRD §15 action 5 mandated a spike; the user waived it. The waiver is safe only because failure is graceful — so graceful failure is not optional, it is the condition of the waiver. |
| **V3** | STT = **`faster-whisper`, `base.en`, `int8`, CPU**. | Exactly D37. English-only: `lang` stays in the signature because the boundary is engine-agnostic, but this engine ignores anything but English. |
| **V4** | Audio decoding (WebM/Opus → 16 kHz mono PCM) happens **inside `backend/voice/` via PyAV**. | PyAV is already a `faster-whisper` dependency. Keeps codec knowledge inside the boundary; `transcribe(audio_bytes)` is unchanged; the frontend stays dumb; no ffmpeg subprocess and no undeclared system dependency beyond TRD §6.5's Docker + libsecret. |
| **V5** | The Companion speaks **only replies to turns that arrived by voice**. Typed turns stay silent. | Symmetric and needs no new setting. Decided in the UI from the modality it already sent — **the agent still never sees `input_modality`**, so D36 and `Schema.md`'s note hold. |
| **V6** | Spoken text = the **final assistant prose, cleaned**: markdown syntax and `[n]` citation superscripts stripped, tool chips and tool results skipped, cited evidence read inline as part of the prose. | No second LLM call. Generating a separate "spoken version" would be a code path the agent knows about — a direct D36 violation. Reading evidence inline keeps D24's provenance audible rather than silently dropping the quote behind a claim. |
| **V7** | Synthesis is **sentence-by-sentence as the turn streams**, through a playback queue. `✕ Stop` stops audio; holding the talk key barges in and cuts playback. | Speech starts while the turn is still running. The queue and its teardown live entirely inside `frontend/src/voice/`, so the boundary is not widened. |
| **V8** | TTS = **`piper-tts` pip package, in-process**, voice `en_US-lessac-medium` cached in `.research-os/`. **No `speech-dispatcher` engine.** | Matches D15's "heavy ML collapsed into in-process Python libraries" — same shape as docling and sentence-transformers. D37 names speech-dispatcher as a fallback, but the stub engine is already the degradation path; a third engine is a spare tyre for a spare tyre. |
| **V9** | Weights (~150 MB Whisper + ~63 MB Piper) are fetched by a **background `jobs/` task on first launch**. Instantiation into RAM stays **lazy on first talk-key press**. | Download ≠ load, so D37's "must not load until the first press" holds exactly. Rules' "one-time setup never sits in a request path" is why it is a job and not a lifespan step. |
| **V10** | **Settings gets a voice-engine selector**, and an Alembic migration flips `api_keys.voice_engine`'s `server_default` from `'stub'` to `'faster_whisper'`. | PRD §12 already specifies `frontend/src/settings/ # … voice engine`. Gives the orphaned `set_voice_engine` its first caller and leaves the stub selectable as an escape hatch when an engine misbehaves. |
| **V11** | `voice_engine`'s value names an **engine profile**, not a single library: `stub` → (stub STT, stub TTS); `faster_whisper` → (faster-whisper STT, **Piper** TTS). Both registry dicts get a `faster_whisper` key. | *Found while planning.* There is one column but two registry dicts, so `'faster_whisper'` would have found no TTS handler and silently fallen back to `stub.synthesize` — audible as silence, with no error. Profile semantics fixes it with **no new column and no CHECK-constraint change**. `Schema.md`'s wording is amended (§8). |
| **V12** | Talk key = **hold `Ctrl+Shift`**, **rebindable from Settings**. New persisted column on the single-row settings store; the mic button keeps working for mouse use. | Modifier-only, so it can never conflict with typed text in the composer, the PDF reader, or CodeMirror. Rebindable because a fixed chord is a guess about one user's muscle memory. |
| **V13** | On key release the transcript **sends immediately, with a ~2 s cancel window** and an undo affordance in the Companion status line. | Keeps the hands-free flow US8 describes, while making a misheard word or a hallucinated fragment recoverable instead of a wasted agent turn. |
| **V14** | **Silero VAD filter enabled inside the faster-whisper engine** (`vad_filter=True`). | It trims non-speech from an **already-captured clip** — a decode parameter, not an interaction model. The mic is still push-to-talk only, still never opens unprompted, still zero idle CPU, so every concern D36 actually names is untouched. It is the standard fix for `base.en` hallucinating "Thank you." over near-silence. Lives inside the boundary; nothing outside knows. |
| **V15** | Tests = a **boundary-enforcement test** plus a **Piper→Whisper round-trip test** on a fixture clip, the latter marked so it is excluded from default runs. | Grill R2 automates only what is invisible to the eye. Voice is visibly right or wrong when you press the key — but the D37 import boundary is exactly the thing you cannot see by using the app, and it rots silently. |

**Decided without a question, stated here for the record:** Piper voice `en_US-lessac-medium`; the
`voice` readiness capability goes `ready` once weights are on disk and `failed` if the fetch or a
load fails; the `voice_engine` value is cached in-process (invalidated by `set_voice_engine`) so the
hot push-to-talk path stops opening a DB session per utterance; `packages/api-client/` is
regenerated in the same commit as the backend change that causes it, per Rules.

---

## 3. Target package layout

`backend/voice/` today is three files. This plan adds three more and touches the existing entry
point. **Nothing outside this package may import any of these.**

```
backend/voice/
  __init__.py          public entry point: transcribe · synthesize  (+ engine-profile cache)
  models.py            Transcript                                    (unchanged)
  stub.py              canned text + silent WAV                      (unchanged)
  faster_whisper.py    NEW — STT engine: lazy CTranslate2 load, PyAV decode, VAD filter
  piper.py             NEW — TTS engine: lazy onnxruntime load, sentence → WAV bytes
  weights.py           NEW — weight paths under .research-os/, presence check, fetch
```

```
frontend/src/voice/
  useVoice.ts          the one hook — extended with the talk-key state machine,
                       the sentence playback queue, and barge-in
```

---

## 4. Build order

Sub-units follow `ImplementationPlan.md`'s format. **Voice.1 is done**; numbering continues from it.
Each sub-unit is independently runnable — the app works after every one.

### Voice.2 — faster-whisper registered as the real STT engine

**What this delivers.** Holding the talk key and speaking produces *your actual words* in the
composer. The engine loads lazily on first press behind an `asyncio.Lock`, decodes whatever the
browser recorded via PyAV, and runs `base.en` int8 on CPU with the VAD filter on. A load failure
degrades to the stub and marks the capability `failed` rather than breaking the turn.

**Depends on.** Voice.1 (shipped).

**Touches.** `backend/voice/faster_whisper.py` (new — mirrors `memory/embedder.py`'s lazy-load
pattern); `backend/voice/__init__.py` (register in `_TRANSCRIBE_ENGINES`; cache the engine profile);
`backend/pyproject.toml` (`faster-whisper`); `backend/api/voice.py` (reject empty/oversized bodies
with the standard recoverable error envelope).

---

### Voice.3 — Piper registered as the real TTS engine

**What this delivers.** `POST /api/voice/synthesize` returns real speech instead of 0.3 s of
silence. Same lazy-load-behind-a-lock shape as Voice.2. Registered under the `faster_whisper`
profile key per V11, so one column selects both halves.

**Depends on.** Voice.2.

**Touches.** `backend/voice/piper.py` (new); `backend/voice/__init__.py` (register in
`_SYNTHESIZE_ENGINES`); `backend/pyproject.toml` (`piper-tts`).

---

### Voice.4 — Weight fetch job and a live readiness signal

**What this delivers.** On first launch after this ships, a background job downloads both models
into `.research-os/` while the app stays usable. The readiness strip stops lying: `voice` moves
`pending → ready` when weights are present, `failed` when the fetch fails. Nothing is loaded into
RAM until the first talk-key press.

**Depends on.** Voice.3.

**Touches.** `backend/voice/weights.py` (new — paths, presence check, fetch); `backend/jobs/`
(register a one-shot `fetch_voice_models` handler); `backend/main.py` (enqueue on lifespan when
weights are absent and the profile is not `stub`; transition the `voice` capability).

---

### Voice.5 — Settings: engine selector and rebindable talk key

**What this delivers.** The Settings Panel gains a voice section: engine profile (`faster_whisper` /
`stub`) and a push-to-talk key-capture control. `set_voice_engine` gets its first caller. The
default flips to `faster_whisper`, so a fresh install has real voice out of the box.

**Depends on.** Voice.4.

**Touches.** Alembic migration (flip `api_keys.voice_engine` `server_default` to `'faster_whisper'`;
add `api_keys.voice_ptt_binding TEXT NOT NULL DEFAULT 'Ctrl+Shift'` — the existing CHECK constraint
already permits `faster_whisper`, so it is untouched); `backend/db/models.py`;
`backend/settings/__init__.py` (`get/set_ptt_binding`); `backend/api/settings.py` (`GET`/`PUT
/api/settings/voice`); `frontend/src/settings/` (voice section); `packages/api-client/`
(regenerated in the same commit).

---

### Voice.6 — Talk-key state machine and the cancel window

**What this delivers.** Holding `Ctrl+Shift` anywhere in the app opens the mic; releasing it
transcribes and sends. The send is deferred ~2 s behind an undo affordance in the Companion status
line, so a misheard utterance can be pulled back. The binding is read from settings, not hardcoded.

**Depends on.** Voice.5.

**Touches.** `frontend/src/voice/useVoice.ts` (key-down/key-up state machine — this is
MODULES.md's specified but never-built key machine); `frontend/src/companion/CompanionPane.tsx`
(cancel-window affordance in the status line; existing mic button unchanged);
`frontend/src/state/` (read the binding).

---

### Voice.7 — Streaming sentence-by-sentence playback and barge-in

**What this delivers.** A reply to a spoken turn is heard as it is written. Assistant text is
buffered until a sentence boundary, cleaned per V6, synthesized and queued. `✕ Stop` stops audio;
holding the talk key barges in and clears the queue. Typed turns stay silent.

**Depends on.** Voice.6.

**Touches.** `frontend/src/voice/useVoice.ts` (playback queue, sentence splitter, markdown/citation
cleaner, teardown — `playAudio` stops being dead code); `frontend/src/companion/CompanionPane.tsx`
(feed streamed assistant text to the queue when the turn's modality was voice; wire `Stop` to
cancel playback).

---

### Voice.8 — Tests and the Voice sign-off checkpoint

**What this delivers.** The D37 boundary is enforced by CI rather than by memory, and the engines
are proven to actually round-trip. This is the Voice phase's hard sign-off checkpoint: no further
phase work begins until PRD §13's US8 checklist plus §7 below pass.

**Depends on.** Voice.7.

**Touches.** `backend/tests/` — a boundary test asserting no module outside `backend/voice/` imports
`faster_whisper`, `ctranslate2`, `piper` or `av`, and that `frontend/src/voice/` is the only place
touching `getUserMedia` or `Audio`; a `live`-marked round-trip test that synthesizes a phrase with
Piper and transcribes it back with Whisper, asserting the words survive.

---

## 5. Migrations

One Alembic revision, shipped in the same commit as the model change (Rules):

| Change | Note |
|---|---|
| `api_keys.voice_engine` `server_default` `'stub'` → `'faster_whisper'` | Existing CHECK already permits the value. Existing rows are **not** rewritten — an installed app keeps whatever it has and can move via the new Settings control. |
| `api_keys.voice_ptt_binding TEXT NOT NULL DEFAULT 'Ctrl+Shift'` | New. Backs V12. |

---

## 6. Risks

- **RAM, not speed.** The machine is CPU-only with ~5 GB free while torch, `gte-modernbert-base` and
  the cross-encoder are already resident. `base.en` int8 is comfortably real-time on 14 cores;
  **the untested variable is memory pressure**, and it is untested precisely because V2 waived the
  spike. Mitigation is V2's own condition: a load failure degrades to the stub and marks the
  capability `failed`. CTranslate2 does not pull in torch, which keeps the added footprint small.
- **Streaming synthesis teardown.** V7's queue is the most intricate piece in the plan. The failure
  mode to watch is audio outliving its turn — playback continuing after `✕ Stop` or after a
  barge-in. Teardown is tested by hand in the §7 checklist.
- **Sentence splitting mid-stream.** Splitting partial markdown can emit a fragment mid-code-fence
  or mid-citation. The splitter must hold text back until a terminator appears **outside** a fence.
- **First-launch bandwidth.** V9 spends ~200 MB unasked on first launch. Accepted deliberately in
  exchange for an instant first press.

---

## 7. Acceptance — the Voice sign-off checklist

PRD §13's US8 checklist still applies in full and is not restated here. This plan adds:

- [ ] Speaking a sentence produces that sentence's words — not canned stub text.
- [ ] The STT model is **not** loaded until the first talk-key press (verify: no CTranslate2 memory
      at idle after a fresh launch).
- [ ] Weights download in the background on first launch; the app stays usable throughout; the
      readiness strip shows `voice` moving `pending → ready`.
- [ ] A reply to a **spoken** turn is heard aloud, starting before the turn finishes. A reply to a
      **typed** turn is silent.
- [ ] Spoken output contains no asterisks, brackets or citation numbers read aloud.
- [ ] `✕ Stop` halts both the turn and the audio. Holding the talk key during playback cuts the
      audio immediately.
- [ ] Near-silence or an accidental tap does **not** produce a hallucinated sentence.
- [ ] The Settings voice section switches the profile to `stub` and back, and the change takes
      effect without a restart.
- [ ] Rebinding the talk key in Settings takes effect immediately; the new binding survives a
      restart.
- [ ] The cancel window pulls back a misheard utterance before it reaches the agent.
- [ ] The boundary test passes: no STT/TTS import outside `backend/voice/`, no `getUserMedia` or
      `Audio` outside `frontend/src/voice/`.

---

## 8. Departures from the locked documents

Each of these amends its source document rather than contradicting it silently.

| Departure | Source | Amendment |
|---|---|---|
| `voice_engine` names an **engine profile** (STT + TTS pair), not one library | `Schema.md` — "Selected engine in the `backend/voice/` registry" | Reword to "selected engine **profile**; `faster_whisper` selects faster-whisper STT and Piper TTS". No column or constraint change. **(V11)** |
| **No `speech-dispatcher` fallback engine** | D37, `TRD.md` §1.5 | Record that the stub engine already serves as the degradation path; speech-dispatcher is dropped, not deferred. **(V8)** |
| **VAD filter enabled** inside the STT engine | D36 — "push-to-talk, not always-on VAD" | Clarify that D36 bans VAD as an **interaction model**; a decode-time filter on an already-captured clip is in scope. Push-to-talk is unchanged. **(V14)** |
| New `api_keys.voice_ptt_binding` column | `Schema.md` `api_keys` table | Add the column and its default to the table definition. **(V12)** |
| `voice_engine` `server_default` is `faster_whisper` | `Schema.md` — `DEFAULT stub` | Update the stated default. **(V10)** |
| **No spike** before voice work | PRD §15 action 5, `TRD.md` §7.2 | Record the waiver **and its condition**: graceful degradation to the stub on load failure is mandatory, which is what the spike would otherwise have de-risked. **(V2)** |
| TTS streams **sentence-by-sentence**; a **~2 s cancel window** on send | Unspecified in all source docs | New behaviour, not a contradiction. Recorded here as the specification. **(V7, V13)** |

---

## 9. Out of scope

Not in this plan, and not to be raised as an objection: always-on VAD (post-v1, D36);
`whisper.cpp` as a second STT engine (the CHECK constraint keeps the door open, nothing more);
multilingual STT (`base.en` is English-only by V3); a `speech-dispatcher` engine (V8); voice-specific
agent behaviour of any kind — **a spoken turn and a typed turn take the identical path through the
harness, and that is the one property this whole layer exists to preserve.**
