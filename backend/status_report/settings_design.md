# Settings Store — Design & Architecture

Settings Store is a single-row Postgres table (`api_keys`) plus two small satellite modules
(`crypto.py` for AES-256-GCM key encryption, `models.py` for the wire-shape Pydantic types). It
holds every piece of local configuration the app needs before a request can be made: provider
credentials, the chosen primary/auxiliary model, per-model token budgets, the vault path, the
onboarding flag, and the voice engine choice. Nothing here is provider-neutral abstraction — it's
one JSONB blob (`providers`) plus a handful of scalar columns, read and written directly by a
small set of async functions in `settings/__init__.py`.

---

## Storage / data model

**One table, one row** (`backend/db/models.py:39-55`, `ApiKeys`):

| Column | Type | What it holds |
|---|---|---|
| `id` | `SmallInteger`, `CHECK id = 1` | Enforces the single-row invariant at the DB level |
| `providers` | `JSONB`, default `{}` | Keyed by provider name (`"groq"`, `"openai"`, `"ollama"`, ...); each value is a dict with `ciphertext`/`nonce`/`last4` (key-based providers), `base_url` (local providers), `validated_at`, and `model_budgets` |
| `primary_model` | `String \| null` | `"{provider}/{model}"` string, e.g. `"groq/llama-3.1-8b-instant"` |
| `auxiliary_model` | `String \| null` | Same shape, the cheap/fast tier |
| `vault_path` | `String \| null` | Present in the schema but never read or written by `settings/__init__.py` — the real vault path lives outside the DB (see below) |
| `voice_engine` | `String`, default `'stub'` | Which entry of `voice/__init__.py`'s engine dict to use |
| `onboarding_completed_at` | `TIMESTAMP \| null` | Set once, gates the onboarding wizard |
| `created_at` / `updated_at` | `TIMESTAMP` | Standard bookkeeping |

`ApiKeys.providers` is the only place credential and budget data actually lives — `vault_path` on
the row is dead: `get_vault_path`/`set_vault_path` (`settings/__init__.py:35-51`) never touch it.

**Wire-shape models** (`settings/models.py`):
- `Provider` — a `Literal` of 10 fixed provider names, including `"custom"`, `"ollama"`, `"vllm"`.
- `LOCAL_PROVIDERS = {"ollama", "vllm"}` — the providers that take a `base_url` instead of an
  `api_key`.
- `ProviderCredentials` — the *input* shape: `api_key`, `base_url`, `model`, `tier`
  (`"primary"`/`"auxiliary"`), `model_budgets` (a `dict[str, int]` keyed by the full
  `provider/model` string).
- `ProviderInfo` — the *output* shape: `last4`, `base_url`, `validated_at`, `model_budgets`. Never
  carries `ciphertext`/`nonce`/plaintext key — enforced by `get_settings` rebuilding this object
  field-by-field from the stored dict (`settings/__init__.py:66-74`), not by returning the raw
  JSONB.
- `ModelSettings` — the full read shape: `providers: dict[str, ProviderInfo]`, `primary_model`,
  `auxiliary_model`, `onboarding_completed_at`.

**Encryption** (`settings/crypto.py`): the AES-256 master key is generated once and stored in the
OS keyring (`libsecret` via the `keyring` package) under service
`"research-companion-os"` / username `"provider-credentials-master-key"` — never in the vault, the
DB, or the repo (`crypto.py:1-6, 18-24`). `encrypt` returns `(ciphertext_b64, nonce_b64)` with a
fresh random 12-byte nonce per call; `decrypt` reverses it. Plaintext keys exist only transiently
inside `save_provider` and `_resolve_tier` — never persisted or logged.

**Vault path bootstrap** (`settings/__init__.py:25-51`): a small file at
`~/.config/research-companion-os/vault-path` (outside the vault entirely, since Postgres itself
lives *inside* the vault per D8 — the path must be resolvable before the DB exists). Read once at
process start by `get_vault_path`; if absent, falls back to `DEFAULT_VAULT_PATH = ~/ResearchOS`.
`set_vault_path` only writes this bootstrap pointer — it does not touch the running process's
already-resolved vault, so the effect is deferred to the next launch (comment at
`settings/__init__.py:42-49`).

---

## Core mechanics

**`get_settings(session)`** (`settings/__init__.py:63-80`) — read path. Fetches (or lazily creates,
via `_get_or_create_row`) the singleton row, rebuilds each provider entry as a redacted
`ProviderInfo` (only `last4`, `base_url`, `validated_at`, `model_budgets` survive), and returns a
`ModelSettings`.

**`save_provider(session, provider, credentials)`** (`settings/__init__.py:90-128`) — the write
path with a live validation gate:
1. Rejects up front if a key-based provider has no `api_key`, or a local provider (`ollama`/`vllm`)
   has no `base_url`.
2. Builds a `ProviderOverride` and calls `llm.complete(..., override=override, max_tokens=5,
   timeout=15)` with the message `"Say OK."` — a real request against the *not-yet-saved*
   credentials. Any `LLMError` or `httpx.HTTPError` aborts with `ValueError`; nothing is written on
   failure.
3. On success, encrypts the key (if any) via `crypto.encrypt`, stores `ciphertext`/`nonce`/`last4`,
   stores `base_url` (if any) and `model_budgets` (if any), stamps `validated_at`.
4. Merges the new provider entry into `row.providers` (full dict replace of that key, not a
   partial JSONB update — `row.providers = {**row.providers, provider: entry}`).
5. Sets `primary_model` or `auxiliary_model` to `"{provider}/{model}"` depending on
   `credentials.tier`.
6. Returns the freshly re-read `ModelSettings` via `get_settings`.

This function is flagged in the module docstring (`settings/__init__.py:1-11`) as an intentional,
narrow reversal of the module dependency order — Settings Store imports `llm.complete` even though
MODULES.md has LLM Gateway depend on Settings Store, because D13 requires validating credentials
*before* they exist in the store, which only `llm`'s `override` parameter can do.

**`save_model_budget(session, provider, model, budget)`** (`settings/__init__.py:131-146`) — a
narrower write with no live-call validation. Builds the key as `f"{provider}/{model}"` and merges
it into `providers[provider].model_budgets`, leaving everything else in that provider's entry
untouched. The docstring is explicit that the caller must always pass the *bare* model name (not
already `provider/model`-prefixed) or the stored key silently double-prefixes and is never found
again.

**`discover_models(provider, base_url)`** (`settings/__init__.py:149-158`) — queries a local
endpoint's model list: `GET {base_url}/api/tags` for `ollama` (reads `.models[].name`), or
`GET {base_url}/v1/models` for anything else routed here (reads `.data[].id`, the OpenAI-compatible
shape used for `vllm`). No DB access — a pure HTTP passthrough.

**`complete_onboarding` / `set_voice_engine` / `get_voice_engine`** — trivial single-field
read/write helpers on the same row, no validation logic.

---

## Callers & dependents

All live, confirmed by reading each call site:

- **`backend/api/settings.py`** — the REST surface: `GET /api/settings/models` →
  `get_settings`; `PUT /api/settings/models` → `save_provider` (422 on `ValueError`);
  `PUT /api/settings/models/budget` → `save_model_budget` (422 if `budget <= 0`);
  `POST /api/settings/models/discover` → `discover_models` (502 on `httpx.HTTPError`);
  `PUT /api/settings/vault-path` → `set_vault_path`; `PUT /api/settings/onboarding-complete` →
  `complete_onboarding`.
- **Frontend, live and reachable** — `frontend/src/settings/SettingsPanel.tsx`,
  `frontend/src/settings/ProviderForm.tsx`, `frontend/src/onboarding/ProjectStep.tsx`,
  `frontend/src/onboarding/VaultStep.tsx`, and `frontend/src/app/App.tsx` all call into the
  generated SDK functions (`packages/api-client/src/client/sdk.gen.ts`) that hit the routes above
  — confirmed by grepping for each generated function name (`getModelsApiSettingsModelsGet`,
  `putModelsApiSettingsModelsPut`, `putModelBudgetApiSettingsModelsBudgetPut`,
  `discoverModelsApiSettingsModelsDiscoverPost`, `putVaultPathApiSettingsVaultPathPut`,
  `putOnboardingCompleteApiSettingsOnboardingCompletePut`) directly in those files.
- **`backend/llm/__init__.py` — `_resolve_tier`** (`llm/__init__.py:148-189`): the normal read path
  for every non-override LLM call. Reads `row.primary_model`/`auxiliary_model`, looks up
  `row.providers[provider]`, decrypts the key via `settings.crypto.decrypt` if present, and reads
  `model_budgets.get(model_string)` for the per-model ceiling. Live — this runs on every `complete`
  call that doesn't pass an explicit `override`.
- **`backend/llm/__init__.py` — `_persist_learned_budget`** (`llm/__init__.py:258-277`): calls
  `settings.save_model_budget` from the rate-limit self-heal path
  (`_call_with_rate_limit_self_heal`, `llm/__init__.py:283-306`) after a `RateLimitError` whose body
  names a real ceiling. Live — confirmed it strips the provider prefix off `model_key` before
  calling, matching `save_model_budget`'s documented bare-name contract.
- **`backend/api/settings.py` — `PUT /api/settings/models/budget`** also reaches
  `save_model_budget` directly, as a hand-set override path (comment cites "Bug Fix Plan Phase
  2.2") — same function, two live callers (one human-driven, one automatic self-heal).
- **`get_vault_path`** — imported and called live from many modules:
  `api/writing.py`, `api/runs.py`, `api/experiments.py`, `api/papers.py`, `sandbox/__init__.py`,
  `writing/tectonic.py`, `writing/__init__.py`, `papers/__init__.py`, `vault/__init__.py`. All
  confirmed to call `get_vault_path()` directly at the point of use (building paths into the vault
  for PDFs, notebooks, manuscripts, experiment dirs, etc.) — this is the most widely-depended-on
  export of the module.
- **`get_settings`** also called from `papers/__init__.py:458-459`, to read
  `auxiliary_model`/`primary_model` for labeling a paper-extraction record with which model
  produced it.
- **`get_voice_engine`** — called live from `voice/__init__.py:25,32` (`transcribe`/`synthesize`),
  which is the module boundary D37 requires for swappable engines.
- **`backend/scripts/configure_provider.py`** — a dev CLI that calls `settings.save_provider` and
  `settings.discover_models` directly, bypassing the API layer but going through the same validated
  path (its own docstring confirms this is intentional — "not a second way into the app").

**Found dead / unreached:**
- **`set_voice_engine`** (`settings/__init__.py:161-165`) has zero callers anywhere in the
  codebase — no API route, no script, no test invokes it. `voice_engine` can only ever be its
  server-default `'stub'`; the write half of that setting is unreachable code.
- **`ApiKeys.vault_path`** column (`db/models.py:49`) is never read or written by
  `settings/__init__.py` — the real vault-path mechanism is the separate bootstrap file under
  `~/.config/research-companion-os/`. The column is schema-present, functionally dead.

---

## Open questions / rough edges

- **`vault_path` column vs. bootstrap file**: the DB row has a `vault_path` column that nothing in
  this module touches; the actual mechanism is a flat file outside the vault. Whether the column is
  a leftover from an earlier design or intended for something else isn't answerable from the code
  alone — it's simply unused today.
- **`save_provider` full-entry replace, not merge, at the JSONB level for the *provider* key**: line
  `row.providers = {**row.providers, provider: entry}` (`settings/__init__.py:121`) replaces the
  entire per-provider dict, and `entry` is built fresh each call — so re-saving a provider with a
  new key but *without* re-supplying `model_budgets` in the request wipes any previously learned
  budgets for that provider (the `if credentials.model_budgets:` guard at
  `settings/__init__.py:119-120` only writes the key when the caller sends new budgets, but there's
  no merge with what was already stored — `entry` starts empty each call, unlike
  `save_model_budget`, which explicitly merges via `entry.get("model_budgets", {})`
  (`settings/__init__.py:142-144`)). A user re-validating a rotated key would silently lose any
  self-healed budgets for that provider.
- **Single-row concurrency**: `_get_or_create_row` does a plain `session.get` + insert-if-missing
  with no explicit row lock; two concurrent first-time writes (e.g. onboarding submitted twice)
  could race on the flush, though the `id = 1` check constraint means only one would ultimately
  stick — the failure mode for the loser isn't handled explicitly in this module (would surface as
  a DB integrity error bubbling up through whichever endpoint lost the race).
- **`set_vault_path` effect is deferred but not communicated in the return value**: `PUT
  /api/settings/vault-path` returns `{"path": body.path}` immediately, as if it took effect, while
  the actual vault path used by the running process is unchanged until restart — the docstring
  flags this (`settings/__init__.py:42-49`) but the API response gives no signal of the deferral to
  a caller that doesn't already know.
- **`discover_models`'s `provider` parameter is typed `Literal["ollama", "vllm"]`** but the function
  body treats anything that isn't `"ollama"` as the generic OpenAI-compatible `/v1/models` shape —
  correct for `vllm` today, but the type signature would silently permit (and mis-route) any other
  literal added later without updating the branching logic.
