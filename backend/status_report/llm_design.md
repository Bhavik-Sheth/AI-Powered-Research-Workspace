# LLM Gateway — Design & Architecture

`backend/llm/__init__.py` is the only place LiteLLM is imported in this codebase. Every other
module reaches a provider through `complete` (streaming, tool-calling) or `complete_structured`
(schema-validated extraction), passing a `tier` (`"primary"`/`"auxiliary"`) or an explicit
`ProviderOverride`. The Gateway resolves a tier to a concrete provider/model/credentials/budget
from the single-row `api_keys` settings table, truncates any request that would blow the tier's
known token budget, paces and retries every call through a shared per-provider rate limiter
(`backend/ratelimit.py`), learns a provider's real token ceiling from a live 429 body and persists
it back to settings for next time, and gives `complete_structured` one corrective retry when a
provider rejects or mis-shapes its structured output.

---

## Storage / data model

**`api_keys`** (`backend/db/models.py:39`) — single row (`CheckConstraint("id = 1")`):

| Column | Shape | What the Gateway reads/writes |
|---|---|---|
| `providers` | JSONB, `{provider: {ciphertext, nonce, base_url?, model_budgets: {model_string: int}}}` | `_resolve_tier` reads credentials + budget; `_persist_learned_budget` writes a learned budget via `settings.save_model_budget` |
| `primary_model` | `str \| None`, e.g. `"groq/llama-3.1-8b-instant"` | `_resolve_tier` reads for `tier="primary"` |
| `auxiliary_model` | `str \| None` | `_resolve_tier` reads for `tier="auxiliary"`, falling back to `primary_model` if unset (`__init__.py:162-163`) |

`model_budgets` is keyed by the **full** `provider/model` string (`__init__.py:177-180`), not the
litellm-prefixed form (`PROVIDER_PREFIX[provider]/model`) used for the actual API call — one
provider can host models with different TPM ceilings, so the key has to be model-specific.
`settings.save_model_budget` (`backend/settings/__init__.py:131`) enforces this same keying and
explicitly does *not* re-validate live (the caller already has proof from a real 429).

**In-process, not persisted**: `_provider_limiters: dict[str, RateLimiter]` (`__init__.py:322`) —
one `RateLimiter` per provider string (or `"__override__"` for pre-save validation calls), created
lazily and kept for the process lifetime. Resets on restart; nothing about pacing survives a
deploy.

**Fixed model shapes** (all Pydantic `BaseModel`, `__init__.py:75-129`): `ToolCall`, `Message`,
`ToolCallDelta`, `LLMChunk`, `ProviderOverride`, and the internal `_Resolved` NamedTuple that
carries a resolved request plus the provider/model_key coordinates needed to write back a
self-healed budget (`None`/`None` for an override, since there's no saved row to write to).

---

## Core mechanics

### Tier resolution (`_resolve`, `_resolve_tier`, `__init__.py:148-202`)

1. `_resolve(tier, override)` — if `override` is given (only `settings.save_provider`'s pre-save
   validation call passes one), builds a `_Resolved` directly from it: no budget, no
   provider/model_key (self-heal is skipped for it). Otherwise defers to `_resolve_tier`.
2. `_resolve_tier` opens a DB session, loads the single `ApiKeys` row, picks `primary_model` or
   (for `"auxiliary"`) `auxiliary_model` — falling back to `primary_model` if auxiliary isn't set.
   Raises `RuntimeError` if neither is configured.
3. Splits `model_string` on the first `/` into `our_provider`/`model`, looks up
   `row.providers[our_provider]` — raises if no credentials are stored for that provider.
4. Decrypts the API key via `settings.crypto.decrypt` if `ciphertext` is present, reads `base_url`,
   and looks up `model_budgets[model_string]` (may be `None` — no ceiling learned yet).
5. Returns the litellm-form model string via `PROVIDER_PREFIX` (`__init__.py:61-72`, e.g.
   `google` → `gemini`, `custom` → `openai`, `vllm` → `hosted_vllm`) plus everything above.

`db`/`settings` are imported **locally** inside `_resolve_tier` and `_persist_learned_budget`
(`__init__.py:151-152`, `270-271`) — `settings/` imports this module for the override path, so a
top-level import here would cycle.

### Token-budget fitting (`_fit_to_budget`, `__init__.py:205-234`)

Last-resort guard, not the primary sizing mechanism (comment attributes real windowing to the
caller). No-op if `budget is None`. Otherwise:
1. `available = budget - (max_tokens or _COMPLETION_TOKEN_MARGIN)` — reserves 512 tokens for the
   completion itself if the caller didn't pass an explicit `max_tokens`.
2. Counts tokens via `litellm.token_counter` over all messages as-shaped for the wire
   (`_to_wire_message`). If already under budget, returns messages unchanged.
3. Otherwise finds the single longest message by character count and binary-searches the largest
   character prefix of it that fits, replacing only that one message. Every other message is left
   untouched even if the request is still oversized as a whole (only one message ever gets
   truncated).

### Rate limiting / backoff (`ratelimit.py`, shared with `search/`)

- `RateLimiter.acquire()` (`ratelimit.py:29-50`) is a single-bucket async limiter: at most one call
  *starts* every `min_interval_s`; concurrent callers queue behind an `asyncio.Lock`. It only paces
  start times — a call already running is not throttled again.
- The LLM Gateway uses one `RateLimiter(_PROVIDER_MIN_INTERVAL_S=2.5)` per provider
  (`_get_provider_limiter`, `__init__.py:325-334`), shared across every caller of that provider
  process-wide — not per-model, per-tier, or per-caller. An override call (Settings Store's
  pre-save probe) shares its own `"__override__"` bucket rather than skipping throttling.
- `call_with_retry` (`ratelimit.py:110-140`) wraps every real `litellm.acompletion` call site.
  Every one of those calls passes `num_retries=0` — litellm's own internal retries are disabled
  entirely; `call_with_retry` is the sole retry authority (comment at `__init__.py:39-48` cites a
  verified live failure of relying on litellm's opaque retries against Groq's/Mistral's ceilings).
  On each attempt: acquire the provider's limiter, run the call; on a matching exception
  (`_LLM_RETRYABLE_EXCEPTIONS` = `RateLimitError`, `Timeout`, `APIConnectionError`,
  `ServiceUnavailableError`, `InternalServerError`), sleep for the provider's own stated wait time
  if parseable (`_parse_wait_seconds`, regex on `"try again in X s"`), else exponential backoff
  with jitter (`_backoff_delay`), then retry — up to `max_retries` (3 default; `complete_structured`
  passes 4 with a 30s cap, reasoning that background jobs can ride out a burst that an interactive
  `complete` call, with a person waiting, should not).
- Any non-matching exception propagates immediately, unretried.

### Self-healing rate-limit ceiling (`_call_with_rate_limit_self_heal`, `__init__.py:283-306`)

Wraps every real completion call (both `complete` and `complete_structured` route through it):
1. Fits messages to the tier's currently-known `budget` and calls `make_call`.
2. On `litellm.RateLimitError`, tries to parse the provider's stated ceiling out of the 429 body
   via `_parse_rate_limit_ceiling` (regex `[Ll]imit[:\s]+(\d+)`, tuned to Groq/OpenAI-compatible
   429 text like `"Limit 6000, Used 0, Requested 14327"`).
3. If unparseable, or this was an override call (`resolved.provider`/`model_key` are `None`, no row
   to write back to), re-raises unchanged.
4. Otherwise persists the learned ceiling via `_persist_learned_budget` → `settings.save_model_budget`
   (stripping the provider prefix first — `model_key` is the full `provider/model` string but
   `save_model_budget`'s `model` param is bare and re-adds the prefix itself; passing it through
   unstripped would double-prefix and silently orphan the saved budget from what `_resolve_tier`
   later looks up — `__init__.py:266-268`), refits messages to the new budget, and retries **once**.
   A second failure of any kind surfaces unchanged — this is a single-shot heal, not a loop.

This runs independently of, and beneath, `call_with_retry`'s own retry loop — a rate limit inside
`make_call` is caught here first (learn + refit + retry once); only if that retry also raises does
it propagate up to `call_with_retry`'s exception handling.

### Schema-repair retry (`complete_structured`, `__init__.py:490-555`)

1. Resolves the tier, builds `make_call`, wraps it through `_call_with_rate_limit_self_heal`, runs
   it through `call_with_retry` (max 4 retries, 2s base / 30s cap backoff — a higher budget than
   `complete` since structured calls run in background jobs).
2. Two distinct failure shapes trigger repair, both leading to the same `_repair_schema_violation`:
   - The provider outright rejects the call as `litellm.BadRequestError`, and
     `_is_schema_violation` matches one of three captured marker strings (`"tool_use_failed"`,
     `"tool call validation failed"`, `"did not match schema"`) in the exception message. Any other
     `BadRequestError` reason is re-raised unchanged.
   - The call returns 200 but `schema.model_validate_json(content)` raises `ValidationError`
     (defensive path for the model producing malformed JSON without the provider itself rejecting
     it).
3. `_repair_schema_violation`: builds one corrective `user`-role follow-up message (not a synthetic
   `assistant` turn — tried and found to make things worse, per the comment at `__init__.py:397-403`
   describing Groq's `<function=json_tool_call>` wrapper) showing the model its own rejected
   output, the validation error text, and the schema's own `model_json_schema()` spelled out. Fits
   to budget, retries through `call_with_retry` again, and validates the result. A second failure
   raises `RuntimeError("schema repair failed: ...")` — folded into the same catchable vocabulary
   as `LLMError` for callers that catch both.
4. Net worst case for one `complete_structured` call: one rate-limit retry (inside
   `_call_with_rate_limit_self_heal`) + one schema-repair retry = at most 3 network calls, per the
   docstring at `__init__.py:504-513`.

A simplified/flattened fallback schema was considered and rejected (comment `__init__.py:424-439`):
tested live against Groq's `tool_use_failed`, a flattened schema reproduced the identical rejection
because Groq's constrained decoding rejects the request before generation — a schema change doesn't
route around it, so the corrective-retry approach was kept instead.

### Streaming `complete` (`__init__.py:558-606`)

1. Resolves the tier (or override), builds `make_call` (stream=True, `num_retries=0`), wraps in
   `_call_with_rate_limit_self_heal`, runs the *first* call through `call_with_retry` with default
   retry settings (3 retries) — this only covers getting the stream *started*; once the harness has
   received the first chunk there's no way to un-yield it, so a mid-stream failure propagates
   unretried (comment `__init__.py:586-589`).
2. Iterates the litellm async stream, extracting `delta.content` and accumulating
   `delta.tool_calls` into `ToolCallDelta` objects (`index` identifies which call across
   fragments — providers may split a tool call's name/arguments across multiple chunks).
3. Yields an `LLMChunk` only when there's actual content or tool-call data — empty deltas are
   dropped rather than yielded as no-ops.
4. `tier="auxiliary"` silently falls back to `primary` when `auxiliary_model` isn't configured
   (via `_resolve_tier`'s fallback, not `complete` itself) — but no caller in this codebase
   currently calls `complete` (as opposed to `complete_structured`) with `tier="auxiliary"`.

---

## Callers & dependents

Grep for `from llm import` outside `backend/llm/` found seven callers, all live:

| Caller | Function/tier used | Why |
|---|---|---|
| `backend/settings/__init__.py:105` (`save_provider`) | `complete(..., override=..., max_tokens=5, timeout=15)` — no `tier` | Live-validates a not-yet-saved provider/key ("Say OK.") before persisting; `ProviderOverride` exists specifically because `tier` can't express "not yet saved" credentials (module docstring, `__init__.py:1-8`) |
| `backend/papers/__init__.py:465` (`extract_card_job`) | `complete_structured`, `tier="auxiliary"` | Per-section-window extraction of the five standard paper-card fields; one window's failure is logged and skipped rather than failing the whole paper |
| `backend/search/query_understanding.py:23` (`understand_query`) | `complete_structured`, `tier="auxiliary"` | The one LLM query-understanding pass (D21) — converts a natural-language search query into keywords + filters; everything downstream is deterministic |
| `backend/harness/__init__.py:174` (`_maybe_retrieve`) | `complete_structured`, `tier="auxiliary"` | Pre-turn yes/no + query decision for whether to pull memory context; failure silently skips memory rather than failing the turn |
| `backend/harness/__init__.py:488` (main turn loop) | `complete(..., tools=TOOL_SCHEMAS, tier="primary")` | The actual Companion turn — streaming, tool-calling, the only `primary`-tier `complete` call site found |
| `backend/matrix/__init__.py:119` (`_run_scoped_extraction`) | `complete_structured`, `tier="auxiliary"` | Custom matrix column's per-paper scoped extractive query (D27); failure returns `None` (renders as "not stated"), never raises |
| `backend/writing/citation_check.py:57` (`_find_unsupported_claims`) | `complete_structured`, `tier="auxiliary"` | Flags unsupported claims in LaTeX text; failure returns `[]`, degrading to just the missing-citation check |
| `backend/feed/__init__.py:70` (`_seed_from_focus_seed`) | `complete_structured`, `tier="auxiliary"` | One-time interest-profile seeding from a project's focus seed on first read |
| `backend/feed/__init__.py:371` (interest-profile reconciliation) | `complete_structured`, `tier="auxiliary"` | Re-extracts categories/keywords from the project's paper corpus and merges into the existing profile; failure keeps the existing profile |

**Pattern**: every caller except the harness's main turn loop and `save_provider`'s validation
probe uses `tier="auxiliary"` for `complete_structured`, and every one of those wraps the call in
`try/except (*LLMError, RuntimeError)` with a degrade-gracefully fallback (`[]`, `None`, or the
existing value) — none of them let an LLM failure propagate up and fail the surrounding
request/job. `tier="primary"` is used exactly once, for the live streaming Companion turn.

**Nothing found dead or wired to nothing** — every import resolves to a real, reachable call site;
no caller imports `llm` speculatively or catches its errors without using the result.

---

## Open questions / rough edges

- **Provider-wide rate limiter granularity.** `_get_provider_limiter` keys strictly by provider
  string (`"groq"`, `"mistral"`, ...), not by tier or model. If a user's primary and auxiliary
  models are both hosted on the same provider (e.g. both Groq), every caller across the whole app —
  the live Companion turn *and* every background auxiliary job — shares one 2.5s-interval bucket.
  A burst of background extraction jobs can measurably delay the interactive turn's next call start
  with no priority distinction between them.
- **`_fit_to_budget` truncates only one message.** If the single longest message still leaves the
  request oversized once every other message is counted, or if the *combination* of several
  medium-sized messages (not one dominant one) exceeds budget, the function returns a still-oversized
  request — the binary search only ever shrinks the one message it picked at the start, and never
  re-checks whether a second message also needs shrinking.
- **Self-heal is single-shot per call, not global.** `_call_with_rate_limit_self_heal` learns and
  persists a ceiling, but only refits and retries the *current* call once. Nothing invalidates the
  in-memory `_provider_limiters` pacing based on a newly-learned budget — the 2.5s flat interval is
  unrelated to and unaffected by whatever TPM ceiling gets learned.
- **`complete`'s `tier="auxiliary"` fallback path is untested by any real caller.** The fallback-to-
  primary behavior documented in the docstring ("D11") and implemented in `_resolve_tier` has no
  live caller passing `tier="auxiliary"` to `complete` (only to `complete_structured`) — it's
  reachable code with no current exerciser.
- **`_parse_rate_limit_ceiling` and `_parse_wait_seconds` are regex-fitted to specific providers'
  captured error text** (Groq's `"Limit 6000, Used 0..."`, Groq's `"try again in 22.29s"`). Comments
  note Mistral's undisclosed ceiling and non-numeric rate-limit message don't match either pattern —
  the self-heal and provider-stated-backoff paths silently degrade to "no learned ceiling" /
  "exponential backoff" for any provider whose error text doesn't happen to match these two regexes,
  with no fallback attempt to request the format from a different field.
- **`LLMError` explicitly excludes `litellm.exceptions.OpenAIError`** despite its name suggesting
  it should be included — the module comment (`__init__.py:23-26`) states this was verified
  empirically because litellm's real runtime exceptions subclass `openai`'s hierarchy directly, so
  `OpenAIError` would silently never match. Anyone extending `LLMError` without re-verifying this
  against a live litellm build risks quietly reintroducing an unreachable branch.
- **`_repair_schema_violation` is called from two separate sites** in `complete_structured`
  (`__init__.py:549` for the `BadRequestError` path, `__init__.py:555` for the 200-with-bad-JSON
  path) with hand-written argument lists at each call rather than one shared code path — both
  currently pass `timeout` through correctly, but nothing structurally prevents the two from
  drifting apart if one is edited without the other.
