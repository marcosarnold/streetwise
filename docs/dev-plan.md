# Streetwise — Development Plan

_Rewritten 2026-07-01 for the transit-first pivot; steps renumbered same day after the
pre-implementation review (0.0 added; the old 0.1/0.2 merged into one schema step). Build
in order — each step produces something verifiable, and later steps assume earlier
semantics. The pure-core logic each step introduces ships with its pytest coverage; a
step is not done with failing or missing tests._

## Phase 0 — Truthfulness (make the existing pipeline honest)

Ordering rationale: 0.0 is a zero-dependency unblock for everything downstream (staged
corroboration in 0.6, live Reddit in the 0.7 validation week). 0.1 is the foundation —
every later mechanism (updates, clearance, latency) lands on its tables, and it stops
the irreversible data loss running on every poll cycle today. 0.2–0.3 fix lifecycle
semantics; 0.4–0.5 fix location + extraction shape; 0.6 makes verification/latency real;
0.7 is the gate.

| # | Work | Done when… |
|---|---|---|
| 0.0 | **Credentials & network preflight.** Register the Reddit app; fill `REDDIT_*` in `.env`. Verify Nominatim responds from this network (the sandbox 403 may not apply here). | `.env` has working Reddit credentials; a Nominatim test query returns coordinates from this machine. |
| 0.1 | **Schema v2.** `migrations/` runner (numbered idempotent SQL, recorded in `schema_migrations`). New schema per architecture.md: `events` v2 (verification/scope/geo_kind/score components; no `impact_*`, no `estimated_duration`, no sources JSON), `event_sources`, `raw_items`. Delete-nothing (24-h `DELETE` abolished; v1 dev DB backed up, not destroyed). Pipeline adopts the schema minimally: raw items archived before extraction, typed source links, `geo_kind` written honestly (`point` or `none` — **the Chicago-center pin dies here**), score components stored, verification derived (officials born confirmed). Frontend: skip markers without coordinates; render typed sources. | Fresh DB migrates cleanly and idempotently; a monkeypatched pipeline test writes v2 rows end-to-end (raw archived, sources linked, no fake pins); pytest green. `SELECT count(*) FROM events` only ever grows. |
| 0.2 | **Content-hash dedup + update flow.** Skip only unchanged items (hash match in `event_sources.last_hash`); changed items re-extract and merge into their existing event via `event_sources` routing; broadcast `update_event`. | Re-polling unchanged feeds creates nothing. A stored item with altered content re-enters and updates its event rather than being skipped; an escalating CTA alert escalates. Tests cover both paths. |
| 0.3 | **Clearance detection + full SSE vocabulary.** Vanish-detection: active events whose every source has `last_seen_at` older than 2 intervals → `cleared_at` set, `clear_event` broadcast — **evaluated only for sources whose current poll succeeded** (feed-down ≠ clearance). Frontend transitions and removes markers (fixes the v1 marker leak). | Kill a test alert from a (successfully polled) feed: within 2 cycles the event shows cleared with a duration, then leaves the live view without reload. A simulated fetch failure clears nothing. Tests cover the guard. |
| 0.4 | **GTFS gazetteer.** `scripts/build_gazetteer.py` → `data/gazetteer.json` + `data/lines.geojson` (CTA + Metra static GTFS, official line colors). Resolution order: station → line → Nominatim point → none; Nominatim off the poll critical path (late geocodes arrive as `update_event`). | Every current Metra station alert resolves to exact station coordinates with `geo_kind=station`, offline, instantly. Gazetteer name-matching has tests (exact, alias, miss). |
| 0.5 | **Extraction prompt v2.** New prompt per architecture.md (`mode`, `lines`, `station`, `severity`, `scope`, `is_clearance` capture-only; plain-language summaries). CTA's `Impact` field passes through in the batch as the `scope` hint. | A live CTA batch returns official line names that join the gazetteer without fuzzing; chronic elevator alerts come back `scope=chronic`; summaries read like something a rider would text a friend. |
| 0.6 | **Verification + corroboration matcher + latency capture.** Matcher per architecture.md (source types explicit; lines/station/500 m + 30 min; **no** `event_type` equality). Merges keep the earlier event's id. `first_social_at`/`official_at` from source-published timestamps (flagged fallback to fetch time); `lead_seconds` recorded on corroboration. Read-time freshness in serialization. | Official events are confirmed, solo Reddit reported, and a day's scores are *not* all one value. A staged Reddit-then-CTA pair (different `event_type` labels) corroborates and yields an unflagged lead-time observation. Matcher + timestamp fallback have tests. |
| 0.7 | **`/review` eval surface + validation week.** Raw item beside extraction, one-tap verdicts into `raw_items.review`. Then run one full week of continuous operation against the PRD success criteria. | A week of data reviewed; every PRD criterion has a measured number; failures have follow-up issues. **This gate decides whether Phase 1 proceeds.** |

## Phase 1 — The verdict (the product surface)

Ordering rationale: tokens before components (everything renders in them), the board
before map polish (it is the primary surface), cards before motion (motion decorates
states that must exist first). Line-segment map geometry is deliberately **not** in the
gate (1.3b).

| # | Work | Done when… |
|---|---|---|
| 1.1 | **Design tokens.** One `:root` block: CTA line colors, state colors + text-safe siblings (AA-checked), two typefaces, 4 px spacing scale, one radius. Documented in architecture.md. | Every color/size in the CSS traces to a token; no raw hex in components. |
| 1.2 | **`/lines` endpoint + verdict board.** Per-line verdicts from **active acute events only**; chronic/planned surface as a quiet "ongoing conditions" count. Board renders every CTA + Metra line with its official color and a status word; tapping a row focuses its events. Desktop left rail; mobile top strip. | A stranger reads the board for five seconds and correctly answers "is the Blue Line okay?" — and a month-old elevator outage does not make the Red Line look degraded. |
| 1.3 | **Map v2.** Station markers (`geo_kind=station`), point markers (`point`), list panel for `none`. State styling: confirmed = solid · reported = dashed/hollow · cleared = greyed with duration; age → opacity. | Every rendered mark traces to a verified place; unverified reports are visually distinct at a glance. |
| 1.3b | *(Optional polish — not in the gate.)* Line-segment geometry from `lines.geojson`, colored by line, weighted by state. | Ship only if 1.1–1.6 are done and dogfooding demands it. |
| 1.4 | **Event card.** State badge → summary → line badge(s) → location → "detected N min ago" → provenance in words ("Metra alert + 2 rider reports") → duration when cleared. Chronic items labeled as ongoing conditions. No "—" rows, no numbers shown twice, no raw scores or IDs. | Card reads like a stance, not a database row. |
| 1.5 | **Honest chrome + empty state.** Status strip ("Checked 2m ago · next in 3 · CTA ✓ Metra ✓ Reddit ✕"); "All clear on CTA & Metra" as a designed state; name + favicon + one-sentence explainer on first visit. All wall-clock display pinned America/Chicago. | A blank-map session is **never** ambiguous — it's either All Clear or a named failure. |
| 1.6 | **Liveness motion.** New = pop, update = single pulse, clear = desaturate + fade; age → opacity curve. Transform/opacity only; `prefers-reduced-motion` gated. | Motion communicates only state changes; nothing loops or decorates. |
| 1.7 | **Founder dogfood.** Two weeks of real commute use. | I check Streetwise before my own commute without forcing myself (PRD criterion 7), or I can articulate exactly why not. |

## Dependencies (`requirements.txt`)

```
fastapi
uvicorn[standard]
anthropic
praw            # Reddit API
httpx           # CTA/Metra/Nominatim fetches
pydantic
python-dotenv
apscheduler     # 5-minute poll scheduler
pytest          # pure-core tests (scorer, matcher, clearance, store)
```

(`feedparser` is removed — the Metra RSS feed it served no longer exists.)

## Environment (`.env`)

```
ANTHROPIC_API_KEY=sk-ant-...
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=streetwise/1.0 by u/yourusername
# METRA_GTFS_* — add if Metra's GTFS download requires developer credentials (verify at metra.com/developers)
```

## Current status

- **0.0 — done with one open item (2026-07-01).** Nominatim verified reachable from the
  dev machine (the 403 was a sandbox artifact); note: named places resolve, intersection
  strings often return empty — live evidence for gazetteer-first. **Open: Reddit
  credentials** — `REDDIT_CLIENT_ID`/`SECRET` in `.env` are still placeholders (register
  at reddit.com/prefs/apps). Does not block 0.1–0.5; required by 0.6's staged
  corroboration and the 0.7 validation week.
- **0.1 — done (2026-07-01).** `migrations/001_schema_v2.sql` + startup runner;
  `store.py` rewritten (events v2, `event_sources`, `raw_items`, delete-nothing,
  hydrated serialization, per-type dedup); pipeline adopts v2 (raw archived first, typed
  source links, honest `geo_kind` — Chicago-center pin dead, dead recency constant
  removed, merges keep the earlier event's id); geocoder returns `None` instead of a
  fake point; SSE broadcasts re-read the store (one canonical serialization); frontend
  skips events without coordinates and shows verification instead of a confidence
  number. 14 pytest cases green (store contracts + monkeypatched pipeline end-to-end).
  v1 sandbox DB preserved at `backups/streetwise-v1-sandbox.db`. Implementation note:
  derived `confidence` is the uncapped component sum (can exceed 1.0 when corroborated,
  e.g. 1.1) — it's internal machinery feeding states, and thresholds all sit ≤ 0.6, so
  the v1 `min(…, 1.0)` cap added nothing.
- **Next: 0.2 — content-hash dedup + update flow.**
- Known v1 defects mapped to remaining steps: updates silently swallowed by ID-only
  dedup (0.2); markers never removed client-side (0.3); real location resolution (0.4);
  `mode`/`lines`/`station`/`severity`/`scope` extraction (0.5); read-time freshness +
  matcher fix + latency capture (0.6).
