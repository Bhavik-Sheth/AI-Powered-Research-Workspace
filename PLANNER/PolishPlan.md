# Polish Plan — Dashboard charts + whole-app tie-up

Written 2026-09-05. Scope: make the Dashboard earn its place as the landing view, and
close the visual//a11y gaps left after the BaseHub facelift.

Every claim below was **measured against the running app and live DB**, not assumed.
Where a number appears, the command that produced it is named so it can be re-run.

---

## 0. Evidence gathered first

| Question | Answer | How verified |
|---|---|---|
| Is "time spent" tracked anywhere? | **No.** No session/duration/reading table. `projects.last_opened_at` is the only clock. | `grep time_spent\|duration\|session\|elapsed` over `db/models.py` → 1 hit |
| Is *any* real time data available? | **Yes.** `turn_traces.total_ms` (29/29 populated, avg 4561ms, max 60436ms) and `experiment_runs.started_at→finished_at` (4 runs, 3–4s). | live `psql` counts |
| Are token/cost columns usable? | **No — NULL on all 29 rows.** Schema has them; nothing writes them. | `count(model)=0, count(prompt_tokens)=0` |
| Does the dashboard API already return more than the UI shows? | **Yes — 4 stat tiles are fetched and discarded.** | live `GET /dashboard` vs `Dashboard.tsx` JSX |
| Does the current graph palette pass viz checks? | **No — 3 failures, one hard.** | `dataviz/scripts/validate_palette.js` |
| Does the theme pass WCAG text contrast? | **Yes, except placeholders (3.17:1).** | computed WCAG ratios, all tokens × all surfaces |

---

## 1. The pie chart — recommendation: **don't add one**

You asked for a pie chart. I'd advise against it here, for three concrete reasons:

1. **The correct form is already on the screen.** Part-to-whole → *stacked bar*, per the
   dataviz form heuristic. `Dashboard.tsx:118` already renders exactly that as
   `.dashboard__progress-meter` (4 bands = the 4 `experiments.status` values). A pie of
   the same four numbers would be a **duplicate in a worse form**.
2. **It is a named anti-pattern for this data.** "Donut/pie for comparing close values"
   → use a bar. Your current split is `planned 5 / remaining 1 / in_progress 1 / done 0`;
   three of those four slices would be near-identical slivers, and one would be *invisible*
   (zero). Pies handle zero-and-near-tie badly; the meter already handles it.
3. **A 2-slice pie is explicitly called out** as the wrong answer for "a single ratio
   against a limit" — a meter is right.

**What to do instead** — keep the meter, and give it the thing it's actually missing: a
**hero number**. Right now the meter shows proportion but never states the fact.

```
PROGRESS
1 of 7 experiments done                 ← hero figure (the fact)
▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░    ← existing meter (the shape)
■ Planned 5   ■ Remaining 1   ■ In progress 1   ■ Done 0
```

If you still want a donut after seeing this, it *is* legal (part-to-whole at a glance,
≤ 6 segments) — but it should **replace** the meter, never sit beside it. My
recommendation is to keep the meter.

---

## 2. "Time spent" — three honest options

This is the part that needs a decision from you, because **the data does not exist yet.**

### Option A — chart the time you already measure (zero new tracking) ✅ recommended first
Real, measured, already in the DB:
- **Companion turn latency** — `turn_traces.total_ms`, 29 rows, avg 4.5s, max 60s.
- **Experiment run wall-clock** — `experiment_runs`, real container durations.

Label it honestly: this is *the agent's* time, not yours. It answers a question you
actually have ("is the Companion slow, and which turns blew up?") and costs one query.

### Option B — instrument real reading time (new scope)
To answer "how long did I spend on paper X" you need a new `reading_sessions`
(`paper_id`, `started_at`, `ended_at`) fed by reader-tab focus + a heartbeat.

⚠️ **Architectural catch — read before agreeing.** D3 says *the vault is truth, Postgres
is a rebuildable index*. Reading-session history is **new primary data**, not a projection
of anything in the vault — so a Postgres-only table would be silently destroyed by any
index rebuild. Doing this properly means giving it a vault home (e.g. an append-only
`.research-os/activity.jsonl`) and writing through Vault Writer like everything else
(D4: the app is the sole writer). That is a real slice, not an afternoon.

### Option C — infer time from `created_at` gaps
**Don't.** It fabricates a number that looks authoritative and isn't. Explicitly rejected.

---

## 3. Free win — the dashboard already fetches 4 tiles it throws away

`GET /api/projects/:id/dashboard` returns, right now, on your live project:

| Tile | total | qualifier |
|---|---|---|
| Papers | 11 | 8 unmarked |
| Notes | 5 | 5 unlinked |
| Experiments | 7 | 1 in progress |
| Feed | 610 | new since Sat |

`DashboardStat`'s own docstring says *"One stat-row tile (UI_DESIGN.md §4.1)"*, and
**BaseHub mock screen 01 draws exactly this 4-up row**. `Dashboard.tsx` renders
`focus`, `progress`, `pending_experiments`, `relevant_papers`, tabs and `needs_attention`
— and never touches `summary.papers/notes/experiments/feed`.

So the API, the schema docstring, and the design mock all already agree on a component
that was never built. **Backend work required: none.** This is the single highest
value-per-effort item in this document.

Per the dataviz form heuristic this is correctly *not a chart*: "a handful of headline
numbers → **KPI row of stat tiles**".

---

## 4. Proposed dashboard layout

Current: seven equal-weight sections stacked vertically, no hierarchy, long scroll.
Nothing tells you where to look, and the project name is the only thing above the fold
that isn't a list.

```
┌──────────────────────────────────────────────────────────────┐
│  Attention Sinks: A Study                                    │  h1
├───────────┬───────────┬───────────┬───────────────────────────┤
│ PAPERS    │ NOTES     │ EXPERIMENTS│ FEED                     │  §3 KPI row
│ 11        │ 5         │ 7          │ 610                      │  (free)
│ 8 unmarked│ 5 unlinked│ 1 in prog. │ new since Sat            │
├───────────┴───────────┴───────────┴───────────────────────────┤
│ CURRENTLY WORKING ON        │  NEEDS ATTENTION (3)            │
│ sparse retrieval heads      │  ⌐ 8 papers unmarked            │  2-col:
│                             │  ⌐ 2 experiments no next step   │  focus left,
│ PROGRESS                    │  ✕ S2 key failed validation     │  triage right
│ 1 of 7 done   ← hero        │                                 │
│ ▓▓▓▓░░░░░░░░  ← meter       │                                 │
├─────────────────────────────┴─────────────────────────────────┤
│ CONTINUE WHERE YOU LEFT OFF          RELEVANT PAPERS          │
└───────────────────────────────────────────────────────────────┘
```

Reflows to one column under `@container center-pane (max-width: 720px)` — the pattern
already established across the app in the facelift.

**Chart inventory for the dashboard** (deliberately small):

| # | What | Form | Color job | Data | New backend? |
|---|---|---|---|---|---|
| 1 | 4 headline counts | KPI stat tiles | none (text) | already returned | **no** |
| 2 | Experiment progress | keep stacked meter + hero number | Ember ordinal ramp | already returned | no |
| 3 | Activity, last 30d | sparkline inside each tile | single Ember hue | `created_at` on papers/notes/highlights | 1 query |
| 4 | Companion latency | horizontal bar, p50/p95 | Ember ordinal | `turn_traces.total_ms` | 1 query |
| 5 | Token spend by model | bar | categorical | **blocked — see §5** | plumbing fix |

Deliberately **excluded**: pie/donut (§1), radar, wordcloud, dual-axis anything, and a
"relevance score" chart — those scores are raw reranker log-odds (`-4.9 … -11.2`) and are
meaningless to a reader as numbers. Show rank order, not the score.

---

## 5. Bug found — token/cost telemetry is plumbed but never connected

`turn_traces` has `model`, `prompt_tokens`, `completion_tokens`. All NULL, all 29 rows.

- `llm/__init__.py:644` **does** construct `Usage(prompt_tokens=…, completion_tokens=…)`.
- `harness/trace.py:27-29` **does** accept them as parameters.
- **No caller passes them.** All 7 `trace.finish()` call sites
  (`harness/loop.py` ×5, `subagents.py`, `resume.py`) omit all three.

The `TurnTraces` docstring cites HarnessPlan H1, and `llm/__init__.py:120` names these
exact columns as the intended destination — so this is unfinished plumbing, not a design
choice. Threading `Usage` through the loop's existing return path unlocks item #5 above
and real cost visibility. Small, contained fix.

---

## 6. Bug found — graph palette fails the colorblind checks

Ran `dataviz/scripts/validate_palette.js` against the current
`frontend/src/graph/nodeStyle.ts` colors on the dark card surface:

```
[FAIL] Lightness band      5 of 6 outside the dark band L 0.48–0.67
[FAIL] CVD separation      #8f86e0 (author) ↔ #2ea8ff (paper)  ΔE 4.9 deutan
[FAIL] Normal-vision floor #8f86e0 ↔ #2ea8ff  ΔE 11.6  — below the 15 hard floor
```

The last one matters most: **purple and blue nodes are hard to tell apart even with
full colour vision.** Node shapes provide secondary encoding, which legalises a CVD warn
— but the normal-vision floor is a hard gate that secondary encoding does not excuse.

Re-stepped into the dark band and reordered so near hues aren't adjacent. **Verified passing:**

```
paper   #2389e2     dataset #20a04e     repo  #ba5db3
method  #bd7400     author  #8f6edb     idea  #d55753

[PASS] all 6 in band · chroma ok · CVD ΔE 14.1 · normal-vision ΔE 22.4 · contrast ≥3:1
```

Apply to `nodeStyle.ts` LEGEND **and** the `--graph-node-*` tokens (they must stay in
sync — Cytoscape's canvas can't read CSS custom properties, which is why the values are
duplicated).

---

## 7. Bug found — placeholder text fails WCAG AA

Computed every text token against every surface. All pass **except** `--text-faint`:

| Token | on `#0c0c0c` | on `#121212` | on `#252525` |
|---|---|---|---|
| `--text-faint #646464` | 3.31:1 | **3.17:1** | **2.59:1** |

It is used in three places (`tokens.css:154` `::placeholder`, `AppShell.css:101` search
placeholder, `buttons.css:43` `.btn--outline` border).

- As a **border** it's fine — non-text UI needs only 3:1.
- As **placeholder text** it fails AA (needs 4.5:1).

**Fix:** split the token. Keep `--text-faint` for the border; add
`--text-placeholder: #7d7d7d` — verified **4.75:1** on `#0c0c0c` and **4.55:1** on
`#121212` (the surface inputs actually sit on), while staying visibly dimmer than
`--text-muted`.

---

## 8. Remaining tie-up items

| Item | Detail | Effort |
|---|---|---|
| a11y lint errors | 2 pre-existing errors in `settings/VoiceSettingsForm.tsx` — non-native interactive element + `tabIndex` on non-interactive. Only lint errors in the app. | S |
| Bundle size | `index-*.js` is **2.5 MB**; `cynefin` chunk 676 KB, katex 256 KB. Mermaid + KaTeX load eagerly for every user, including those who never open Writing. Lazy-load the Writing tab. | M |
| Companion offline UX | Live app showed *"Reconnecting…"* / *"Message will queue until reconnected"*. Worth a deliberate degraded state rather than a raw status string. | S |
| Empty states | UX rule: *"Show helpful message and action; don't blank empty screens."* Dashboard's empty branches are text-only with no action. Give each an action (e.g. "No focus set — **Set one**"). | S |
| Relevance scores | Raw reranker log-odds surfaced as ordering only; never render the number. | S |
| Reduced motion | Existing spinners already honour `prefers-reduced-motion` — extend to any new chart animation. | S |

---

## 9. Suggested order

**Phase 1 — free wins, no backend — ✅ DONE 2026-09-05**
1. ✅ KPI stat-tile row (§3) — `Dashboard.tsx` `KpiTile`, `Dashboard.css .dashboard__kpi*`
2. ✅ Progress hero number (§1) — `ExperimentProgress` / `.dashboard__progress-hero`
3. ✅ Two-column dashboard layout + `@container center-pane (max-width: 760px)` reflow (§4)
4. ✅ `--text-placeholder: #7d7d7d` (§7) — tokens.css + AppShell.css, WCAG AA on every input surface
5. ✅ Graph palette swap (§6) — `nodeStyle.ts` LEGEND + `--graph-node-*`. Adjacent-pair CVD
   now passes clean (ΔE 22.4); all-pairs still can't (no 6-hue palette can — cap is 3) but
   shape + label + outline are the required secondary encoding and all present. Verdict:
   large improvement on the prior blue/purple ΔE 11.6 collision.

Verified: `tsc` clean, `eslint` clean on touched files (the 2 standing errors are
pre-existing in `settings/VoiceSettingsForm.tsx`, Phase 3 item 8a), tests 5/5, dashboard
screenshotted at 1440 / 1100 / 900 px — 4→2 KPI columns and 2→1 body columns both reflow.

**Phase 1b — bento restyle (user request, 2026-09-05):** the listed layout became a
bento of bordered tiles (`.dashboard__tile`). KPI row is its own 4-up grid
(`.dashboard__kpis`, 4→2→1 via `@container center-pane`); the body is a **two-column CSS
masonry** (`.dashboard__grid { column-count: 2 }`, `break-inside: avoid` per tile,
`column-span: all` for `--wide` tiles) so a short tile never leaves dead space under it
waiting on a tall neighbour — the fix for the ragged-whitespace the earlier
`grid` + `align-items: start` version had. "Gradient of orange", applied as a system not
per-card:
`--ember-bloom` (one faint radial Ember wash from each tile's top-left), an Ember-gradient
tick before every `.dashboard__section-label`, and the progress meter's `--ramp-ember-1..4`
ordinal ramp (dark planned → bright done). New tokens in tokens.css; no component colour
literals.

**Phase 1c — progress donut (user request, 2026-09-05):** the stacked bar became an inline
SVG donut (`ExperimentProgress` in Dashboard.tsx) — circumference-100 circle, one `<circle>`
per non-zero status arc via `stroke-dasharray`/`-dashoffset`, a 1.6-unit (~2px) surface gap
between arcs, `-90°` rotation so it opens at 12 o'clock. Centre states the fact (`done` /
"of N done"), the legend keeps every exact count readable (the a11y fallback for small
slices), `role="img"` + `aria-label` carry the sentence. Arcs use `--ramp-ember-1..4`.
Note: this overrides §1's recommendation against a donut here — done at the user's request;
the zero-slice and near-ties are handled by not drawing empty arcs and by the count legend.

**Phase 2 — small backend, real charts**
6. Activity sparklines — one `created_at` histogram query (§4 #3)
7. Companion latency panel — `turn_traces.total_ms` (§4 #4)
8. Thread `Usage` into `trace.finish()` (§5) → unlocks token/cost

**Phase 3 — decisions + heavier lifts**
9. Decide on time-tracking Option A / B / C (§2)
10. Lazy-load Writing tab, cut the 2.5 MB bundle (§8)
11. a11y lint, empty states, offline UX (§8)

---

## 10. Acceptance checks (runnable, not opinions)

```bash
# every categorical palette must pass before it ships
node <dataviz>/scripts/validate_palette.js "<hexes>" --mode dark --surface "#121212"

# sequential/ordinal ramps
node <dataviz>/scripts/validate_palette.js "#7a3400,#b35100,#ff6c02,#ff9b51" \
     --ordinal --mode dark --surface "#121212"      # currently ALL PASS

cd frontend && npx tsc -b && npm run lint && npm test
```

Plus the standing rules this app already follows: no chart may use colour alone to carry
meaning; ≥2 series always gets a legend; one y-axis, never two; and no new colour literal
in a component — everything through `design/tokens.css`.
