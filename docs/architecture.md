# Streetwise — Architecture

_Rewritten 2026-07-01 for the transit-first pivot; amended same day after the
pre-implementation review (chronic/acute scope, `event_sources`, feed-down guard,
source-published latency timestamps, verification/lifecycle split, corroboration matcher
fix). Sections marked **(v2)** describe behavior the current code does not yet implement —
[dev-plan.md](./dev-plan.md) is the sequence that closes each gap._

## Stack

Python + FastAPI + Claude API + SQLite + Leaflet/OSM. Vanilla JS frontend, no build step.
Everything runs in one process; APScheduler drives a 5-minute poll cycle. Schema changes
ship as numbered, idempotent SQL files in `migrations/`, applied at startup.

## High-level flow

```
Fetch (CTA, Metra, Reddit)
  → Archive raw (raw_items — before anything else can fail)          (v2)
  → Dedup (event_sources + content hash — changed items re-enter)    (v2)
  → Extract (Claude: one call per source batch)
  → Resolve location (gazetteer first, Nominatim fallback, or none)  (v2)
  → Score + verify (Reported / Confirmed; corroboration merge)
  → Store (SQLite; nothing deleted, ever)                            (v2)
  → Push (SSE: new / update / clear / remove)                        (v2)
```

## Event lifecycle **(v2 — the core semantic change)**

Two independent axes, never conflated:

- **`verification`** — *how much we believe it*: `reported` (single unofficial source) or
  `confirmed` (official source, or independent corroboration). Immutable meaning; a
  cleared event keeps its verification so the durations archive can distinguish
  "confirmed derailment, 47 min" from "one rider's report, 47 min".
- **Lifecycle** — *whether it's over*: derived from `cleared_at`. Display state is
  computed: cleared if `cleared_at` is set, else the verification word.

```
            ┌──────────┐   official source or          ┌───────────┐
 signal ──▶ │ REPORTED │ ─ independent corroboration ▶ │ CONFIRMED │
            └──────────┘                               └───────────┘
                 │                                          │
        expired_at set (aged out,              cleared_at set (real end signal:
        NO duration claim)                     honest duration data)
```

- Official-source events (CTA, Metra) are born **confirmed**.
- Solo Reddit events are born **reported**; corroboration by a different source type
  promotes them (and records a latency observation — see below).
- **Scope** is a third, orthogonal attribute — `acute | chronic | planned` — because a
  months-long elevator outage and a derailment are both "confirmed" but must never carry
  the same product weight (see *Verdicts*, below).
- **Updates flow**: a source item whose content hash changed is re-extracted and merged
  into its existing event (`update_event` over SSE). An escalating alert ("minor delays"
  → "line suspended") must escalate in the UI.
- **Two distinct endings** (refined during 0.3 — vanishing means different things per
  feed class, see `backend/lifecycle.py`):
  - **Cleared** — evidence. CTA/Metra are *current-state* feeds: an alert's removal is a
    genuine "resolved" signal. An event with official sources clears when **all** of
    them are confirmed vanished; `cleared_at − detected_at` is honest duration data.
    Broadcast as `clear_event`.
  - **Expired** — age. Reddit is an *occurrence* feed: posts always drop out of the
    new/hot fetch window, so their absence proves nothing (and their presence keeps
    nothing alive — a lingering /hot post can't hold a cleared event open). Reported-only
    events leave the live view 3 h after their last update (`REPORTED_TTL_SECONDS`),
    recorded in `expired_at` — never `cleared_at`, so no fabricated duration ever enters
    the archive. Broadcast as `remove_event`. The TTL can never preempt a legitimate
    corroboration (its window is 30 min).
- **"Confirmed vanished" is poll-count-based, not wall-clock-based**: a source item is
  vanished iff its feed has completed **≥ 2 successful polls since the item's
  `last_seen_at`** (append-only `poll_log`). This makes the feed-down guard structural —
  a broken fetcher records no polls, so nothing can vanish and a dead feed can never
  read as a cleared city. It also survives restarts and long downtime cleanly: the first
  two fresh polls re-establish truth.
- `is_clearance` (extractor flag on "service resumed" items) is **captured but not acted
  on** — agencies mostly edit or remove alerts rather than announcing resumption, so
  vanish-detection does the work. Revisit in Phase 2 if it misses real clearances.
- The UI renders **states, never scores**. Numeric confidence is internal.

## Verdicts: acute-only **(v2)**

The `/lines` verdict per line = worst severity among **active, acute** events on that
line. Chronic and planned items (elevator outages, ADA notices, long construction —
which is most of the official feed on a quiet day) render in a separate "ongoing
conditions" tier on the line's detail view and **never degrade the verdict**. A board
that always shows "Minor" because some elevator is always out trains riders to ignore
it within a week; alarm fatigue is fatal to a status product.

## Data sources

All verified working as described; gotchas preserved from live debugging.

### CTA Alerts (rail + bus)
- Feed: `https://www.transitchicago.com/api/1.0/alerts.aspx?outputType=XML`
- Auth: none · Format: XML (`xml.etree.ElementTree`) · Poll: 5 min
- Key fields: `alert_id`, `headline`, `short_description`, `service_id`, `impact`,
  `event_start`, `event_end`
- The feed's own `Impact` classification ("Planned Work", "Elevator Status", "Service
  Disruption"…) is passed through to the extractor as the primary hint for `scope` —
  the agency already classifies chronic/planned for us.
- **No publish timestamp** (full field inventory checked 2026-07-02; `EventStart` is
  the disruption's schedule, not the alert's — planned work "starts" days after it
  posts). CTA-side latency observations therefore always use the flagged fetch-time
  fallback; unflagged lead-time pairs come from Reddit (`created_utc`) + Metra
  (`data-last-updated`).
- Born confirmed (official source)

### Metra Service Alerts
> The original spec's RSS feed (`metrarail.com/rss/alerts`) no longer resolves —
> `metrarail.com` redirects to `metra.com`, which has no RSS feed. Alerts are served via
> a per-line AJAX endpoint instead (decision log, 2026-06-15).

- System endpoint: `https://www.metra.com/service_alerts/update` — lines with active alerts
- Per-line endpoint: `https://www.metra.com/service_alerts/modal/{LINE}` — details
  (HTML fragment in JSON, parsed with regex)
- Auth: none, **but requires a browser `User-Agent`** — the default `curl` UA is blocked
  by CloudFront
- Key fields: `guid` (`data-alert-id`), `title`, `description`, `pubDate`
  (`data-last-updated`), `link`
- Born confirmed (official source)

### Reddit (the street sensor)
- Subreddits: r/chicago, r/Chicagoland · Library: PRAW · Poll: 5 min (free-tier limits)
- Keyword pre-filter before any Claude call: accident, crash, closed, delay,
  construction, police, fire, protest, flooding, Metra, CTA, L train, expressway, highway
- Born **reported**; promoted to confirmed only by corroboration. Road/civic Reddit
  events are retained as sensors (corroboration + context), not as the product's face.
- Posts only for MVP; comments in daily threads (often the fastest signal) are a Phase 2
  sensor upgrade.

### The gazetteer (built 0.4; sources probed live 2026-07-02)
- **CTA rail stations**: the City of Chicago "List of 'L' Stops" dataset (`8pix-ypme`) —
  144 parent stations with names, per-line booleans, exact coordinates; ~176 KB, no key.
  Chosen over raw CTA GTFS (a 98 MB zip whose station→route join needs `stop_times.txt`)
  — best public dataset over GTFS purism.
- **Metra stations**: Metra's GTFS API (`gtfsapi.metra.com`) is credential-gated; every
  public candidate URL 404/503s. The build fetches Metra stations when
  `METRA_GTFS_ACCESS_KEY`/`METRA_GTFS_SECRET_KEY` are set (free — metra.com/developers)
  and warns + ships CTA-only otherwise; Metra alerts fall back to Nominatim meanwhile.
- **Line metadata** (`lines` section): CTA rail ids/names/official brand colors (the
  design system's semantic primitives) + the 11 Metra line ids/names (colors null until
  verified against Metra brand assets — honesty over completeness).
- Built **offline** by `scripts/build_gazetteer.py` → `data/gazetteer.json` (committed).
  Regenerate quarterly or on announced service changes.
- **`lines.geojson` (route polylines) is deferred with its only consumer, 1.3b** — the
  verdict board expresses line-level status without geometry.

## Claude extraction

| Setting | Value |
|---|---|
| Model | `claude-sonnet-4-6` |
| Max tokens | 2048 |
| Temperature | 0 |
| Call pattern | One call per source per poll cycle |
| Input | JSON array of raw items |
| Output | JSON array of structured events |

### System prompt (v2 — shipped 0.5; keep verbatim-synced with `backend/extractor.py`)

```
You are a transit disruption extractor for Chicagoland. You receive a JSON object
{"source": "cta" | "metra" | "reddit", "items": [...]} — raw items from CTA alerts,
Metra alerts, or Reddit posts — and return structured disruption events as a JSON array.

Input hints: CTA items carry "routes" (the agency's affected route ids) and "impact"
(the agency's own classification, e.g. "Planned Work", "Elevator Status" — trust it
for scope). Metra items carry "line" (the line's site slug).

For each current, real-world disruption, return an object with these exact fields:
 - event_type: one of [delay, suspension, reroute, station_issue, accessibility,
   crowding, incident, construction, weather_impact, other]
 - mode: one of [cta_rail, cta_bus, metra, road, other]
 - lines: array of affected route identifiers, using official names exactly:
   CTA rail: "Red","Blue","Brown","Green","Orange","Pink","Purple","Yellow"
   CTA bus: the route number as a string, e.g. "66","22"
   Metra: the line code, e.g. "UP-N","BNSF","MD-W"
   Empty array if no specific line is identifiable.
 - station: the official station/stop name if the event is anchored to one, else null.
   Use the agency's name ("Jefferson Park", "Clybourn"), not a paraphrase.
 - location_string: a specific geocodable address or intersection ONLY for events not
   anchored to a station or line (e.g. a crash on a street). Null otherwise.
   Never return vague strings like "downtown" or "north side".
 - severity: minor (residual delays, minor reroute) |
   major (significant delays, partial suspension, station closure) |
   severe (line suspension, derailment, service stopped)
 - scope: acute (happening now, unexpected — delays, incidents, suspensions) |
   chronic (long-running conditions — elevator/escalator outages, accessibility
   notices, construction lasting weeks) |
   planned (scheduled work announced in advance)
 - summary: one plain-language sentence, present tense, that a rider on a platform
   would find useful. Name the line and the consequence. No agency jargon.
 - is_clearance: true if this item announces that a disruption has ENDED or service has
   resumed; false otherwise.
 - extraction_confidence: high | medium | low
   high = clear event, specific line/place, unambiguous impact
   medium = event likely but line, place, or impact uncertain
   low = vague or speculative, minimal transit signal
 - source_id: the id or guid of the source item

Return ONLY a JSON array. No explanation, no markdown, no preamble.
If no disruption events are detected, return an empty array: []
Drop items that are questions, opinions, or historical references with no current impact.
```

Design notes on the prompt:
- **Model output is untrusted input.** `_sanitize_event` coerces every enum to its
  known set before anything enters the pipeline; only unusable events (no source_id or
  no summary) are rejected outright. Deliberate defaults: unknown scope → acute (hiding
  a real disruption is worse than a noisy verdict row); unknown confidence → low
  (unearned certainty is never granted).
- **`is_clearance` items never create events** — a "service resumed" notice is not a
  disruption. The raw_items archive is the capture; the item is skipped before both the
  update and create paths, and the vanish detector owns the ending.
- `lines` + `station` make the gazetteer join deterministic — the model names entities,
  the gazetteer supplies coordinates. Free-text geocoding becomes the fallback, not the
  path. Deterministic hints ride the batch: CTA `service_id` routes + `impact`, Metra's
  per-line slug.
- `scope` exists because the official feeds are dominated by chronic items on a quiet
  day; without it, verdicts drown in elevator notices (see *Verdicts*).
- `is_clearance` is capture-only for MVP (see lifecycle).
- The v1 `impact_roads/transit/pedestrian` fields are **removed everywhere** (schema, UI,
  prompt) — they were never populated, and a field that exists but is always empty is
  debt. `severity` + `lines` + `scope` carry the intent and are actually extractable.
  `estimated_duration` is likewise dropped; measured durations (`cleared_at −
  detected_at`) replace guessed ones.
- The taxonomy is wide (10 types × 5 modes × 3 severities × 3 scopes); `/review` measures
  its accuracy. If type accuracy is poor, collapse the taxonomy rather than tuning the
  prompt indefinitely.

## Location resolution **(v2)** — no pin without a verified place

Resolution order, recorded in `geo_kind`:

1. **`station`** — `station` matches the gazetteer (exact, then alias/fuzzy). Exact
   coordinates, instant, offline. Expected to cover most transit events.
2. **`line`** — no station, but `lines` is non-empty: the event anchors to line geometry.
   No point marker exists.
3. **`point`** — `location_string` resolved via Nominatim (rate limit 1 req/s,
   descriptive User-Agent per policy; retry once with directional words stripped).
4. **`none`** — nothing resolved. List surfaces only. **The Chicago-center fallback pin
   is abolished** — a wrong point is worse than no point (under v1 behavior, a Kenosha
   elevator outage rendered as a Loop incident).

Two rules inside the matcher (`backend/locate.py`):
- **Ambiguity resolves to nothing, not a guess.** CTA has four "Western" stations (two
  on the Blue Line alone); a name still ambiguous after filtering by the event's
  `lines` falls through to the next tier — the no-fake-pins rule applied to names.
- The `location_string` is also tried against the gazetteer (normalized: comma tails,
  "Station"/"Metra"/parens noise stripped) — extractor strings like "Howard Station,
  Chicago, IL" join instantly even before prompt v2 provides a `station` field.

**Nominatim stays synchronous** (decision, 0.4): with the gazetteer eating the transit
volume it serves only rare road/unmatched events, and a deferred-geocode worker (queue,
retry bookkeeping, out-of-cycle broadcasts) is complexity a rare path doesn't justify.
Revisit if the 0.7 validation week shows cycle-duration or rate-limit pressure.

## Scoring, verification, corroboration

Numeric scoring is internal machinery that *feeds* verification; components are stored
separately so display math can evolve without re-ingesting.

| Component | Detail | Max |
|---|---|---|
| Source | CTA/Metra official +0.4 · Reddit +0.2 | +0.4 |
| Extraction confidence | high +0.3 · medium +0.15 · low +0.0 | +0.3 |
| Corroboration | 2+ independent source types +0.4 | +0.4 |

- Official sources → born **confirmed** regardless of total. Otherwise: **≥ 0.6
  confirmed · 0.4–0.59 reported · < 0.4 dropped** (raw item + extraction still archived
  in `raw_items` for eval).
- **Freshness is computed at read time, never stored.** The v1 ingest-time recency
  component was always evaluated at `detected_at == now`, contributing a constant +0.05 —
  a dead parameter, and why every v1 event scored exactly 0.75. Freshness drives display
  decay (ordering, opacity) in serialization and the frontend; it never changes
  verification.

### Corroboration **(matcher amended 2026-07-01)**
Two events corroborate iff **all** of:
- different source *types* (stored explicitly on `event_sources` — never inferred from
  ID shape; the v1 `isdigit()` heuristic could silently mis-corroborate, a trust bug);
- overlapping `lines`, or same `station`, or locations within 500 m;
- timestamps within 30 min.

`event_type` equality is **not** required — a derailment arrives from CTA as `incident`
and from Reddit as `delay`; demanding equal types starves the corroboration (and
latency) datasets. `event_type` is an informational tag, not a join key.

On match: merge into the **earlier** event (its `id` survives — identity must be stable
for links and history), union sources, promote to confirmed, record the latency
observation.

### Latency instrumentation **(v2 — the moat, measured)**
- `first_social_at` / `official_at` store the **source-published timestamps** (Reddit
  `created_utc`, agency publish/update times), *not* our fetch time — poll-interval
  noise of ±5 min per side would drown a median lead of similar magnitude.
- When a source item has no usable published timestamp, `fetched_at` substitutes and the
  observation is **flagged** (`latency_flagged=1`) so headline statistics exclude it.
- `lead_seconds = official_at − first_social_at` is **derived at read/analysis time,
  not stored** — both anchors are on the event row. Accumulated from day one; published
  only when statistically real (Phase 2). Given CTA's missing publish time, the
  headline-grade (unflagged) corpus is Reddit×Metra; Reddit×CTA pairs are collected but
  flagged.

## Data model **(v2)**

Migrations: numbered idempotent SQL files in `migrations/`, applied in order at startup,
recorded in `schema_migrations`. All timestamps are ISO 8601 UTC; wall-clock *display*
is always `America/Chicago`, regardless of device.

```sql
CREATE TABLE events (
  id               TEXT PRIMARY KEY,        -- UUID; survives merges (earlier event wins)
  city             TEXT NOT NULL DEFAULT 'chicago',
  event_type       TEXT NOT NULL,           -- informational tag (taxonomy in the prompt)
  mode             TEXT,                    -- cta_rail | cta_bus | metra | road | other
  lines            TEXT NOT NULL DEFAULT '[]',  -- JSON array: ["Red"], ["66"], ["UP-N"]
  station          TEXT,                    -- canonical gazetteer name, or NULL
  location_name    TEXT,                    -- human-readable place
  lat              REAL,                    -- only when geo_kind IN (station, point)
  lng              REAL,
  geo_kind         TEXT NOT NULL DEFAULT 'none',  -- station | line | point | none
  severity         TEXT,                    -- minor | major | severe
  scope            TEXT NOT NULL DEFAULT 'acute', -- acute | chronic | planned
  verification     TEXT NOT NULL,           -- reported | confirmed (survives clearance)
  summary          TEXT NOT NULL,
  score_source     REAL NOT NULL DEFAULT 0, -- components; freshness computed at read
  score_extraction REAL NOT NULL DEFAULT 0,
  score_corrob     REAL NOT NULL DEFAULT 0,
  detected_at      TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  cleared_at       TEXT,                    -- real end signal; duration = cleared_at − detected_at
  expired_at       TEXT,                    -- reported-only event aged out; NO duration claim
  first_social_at  TEXT,                    -- latency (source-published timestamps)
  official_at      TEXT,
  latency_flagged  INTEGER NOT NULL DEFAULT 0  -- 1 = a timestamp fell back to fetch time
);

-- Append-only log of successful polls per source: the substrate that makes "vanished"
-- provable (>= 2 polls since last_seen_at) and the feed-down guard structural.
CREATE TABLE poll_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source_type TEXT NOT NULL,
  polled_at   TEXT NOT NULL,
  items       INTEGER NOT NULL DEFAULT 0
);

-- Source of truth for which source items feed which event. Backs three mechanisms:
-- dedup ("seen this item/hash?"), update routing ("whose event is this changed item?"),
-- and clearance ("which active events have no source seen in 2 successful polls?").
-- There is deliberately NO events.sources JSON column — serialization joins this table.
CREATE TABLE event_sources (
  event_id      TEXT NOT NULL REFERENCES events(id),
  source_type   TEXT NOT NULL,              -- cta | metra | reddit (explicit, never inferred)
  source_id     TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,              -- touched every successful poll the item persists
  last_hash     TEXT NOT NULL,
  published_at  TEXT,                       -- the source's own timestamp, when available
  PRIMARY KEY (source_type, source_id)
);

-- Every fetched item + its extraction, forever: the eval corpus and the replay log.
CREATE TABLE raw_items (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source_type  TEXT NOT NULL,
  source_id    TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  fetched_at   TEXT NOT NULL,
  payload      TEXT NOT NULL,               -- raw item JSON
  extraction   TEXT,                        -- what Claude returned (NULL = dropped pre-call)
  review       TEXT,                        -- correct | wrong_event | wrong_location | wrong_summary
  UNIQUE (source_type, source_id, content_hash)
);
```

**Retention: nothing is deleted.** The v1 24-hour `DELETE` is abolished — it was
destroying the durations/latency archive that constitutes the long-term moat. "Active"
is a query (`cleared_at IS NULL`), not a lifecycle. SQLite handles years of this volume.

## API

| Endpoint | Method | Description |
|---|---|---|
| `/events` | GET | Active events. Params: `min_confidence`, `event_type`, `limit` (v2 adds `mode`, `line`, `scope`). Serialization computes confidence + freshness at read time and joins sources. |
| `/events/stream` | GET | SSE: `new_event`, `update_event`, `clear_event`, `remove_event`, `ping` (30 s heartbeat). |
| `/events/{id}` | GET | Single event, full source records. |
| `/lines` | GET **(v2)** | Per-line verdicts from **active acute** events only → `good`, `minor`, `major`, `severe` + driving event ids. Chronic/planned counts reported separately. Powers the verdict board. |
| `/status` | GET | `last_poll_at`, `next_poll_at`, `events_active`, per-source health. |
| `/review` | GET **(v2)** | Eval surface: raw item beside its extraction, one-tap verdicts writing `raw_items.review`. Internal; not linked from the product. |

The frontend must handle `clear_event`/`remove_event` by transitioning and removing
markers — the v1 client leaked markers forever because removals were never broadcast.

## Frontend contract (Phase 1 — see dev-plan)

- **Verdict board**: one row per line, official line color, status word (from acute
  events only). The primary surface; the map is the detail view.
- **Map (1.3)**: station markers for `geo_kind=station`, point markers for `point`,
  a list panel for `none`. State styling: confirmed = solid · reported = dashed/hollow ·
  cleared = greyed with duration. Age maps continuously to opacity.
- **Line-segment geometry (1.3b, optional polish)**: route polylines colored by line and
  weighted by state. Decoupled from the Phase 1 gate — the board already expresses
  line-level status; this is the fiddliest rendering work and must not block shipping.
- **Design tokens** in one `:root` block: official CTA line colors (semantic primitives —
  e.g. Red `#C60C30`, Blue `#00A1DE`), state colors with darker text-safe siblings
  (bright tokens are for map geometry and badges; verify AA before using any as text),
  two typefaces (condensed display for verdicts/badges, workhorse sans for body), a 4 px
  spacing scale, one radius.
- **Motion = liveness only**: new events pop, updates pulse once, clears desaturate and
  fade; transform/opacity only; gated on `prefers-reduced-motion`.

## Testing

The pure core — scorer/verification mapping, the corroboration matcher, the clearance
decision, gazetteer name matching — is kept side-effect-free (clock injected, no I/O) and
covered by pytest. Each Phase 0 step's "done when" includes its tests passing. This is
the part of the system the product's honesty rests on; it is the part that gets frozen
clocks and edge cases, not the HTTP plumbing.

## Known constraints & decisions

| Constraint / decision | Rationale |
|---|---|
| No auth on API endpoints | Solo tool. Add before any public exposure. |
| SQLite, not Postgres | Zero ops for one process. Revisit at multi-user. |
| Numbered idempotent migrations | A database we never delete cannot be hand-ALTERed safely. |
| Gazetteer before geocoder | Deterministic, instant, offline for the dominant (transit) case; Nominatim is fallback-only, rate-limited, off the critical path. |
| No Chicago-center fallback pin | A wrong point is worse than no point. `geo_kind=none` events are list-only. |
| Verification ⊥ lifecycle ⊥ scope | Three orthogonal axes. Conflating them (v1's single enum) corrupts the durations archive and drowns verdicts in elevator notices. |
| Verdicts from acute events only | Chronic items on a board = permanent "Minor" = alarm fatigue = product death. |
| Feed-down ≠ clearance | "Vanished" = ≥ 2 successful polls since `last_seen_at` (`poll_log`) — a broken fetcher records no polls, so it structurally cannot clear anything. |
| Expired ≠ cleared | Reddit is an occurrence feed: post absence proves nothing. Reported-only events age out via `expired_at` (`remove_event`), never `cleared_at` — no fabricated durations in the archive. |
| States in UI, scores internal | Riders act on "Confirmed", not "0.75". |
| Freshness at read time | Ingest-time recency is a constant, hence meaningless (v1 bug). |
| Nothing deleted | Durations + latency archive is the moat; it cannot be backfilled. |
| Source `type` stored explicitly | The v1 ID-shape heuristic could silently mis-corroborate — a trust bug. |
| Latency from source timestamps | Fetch-time measurement carries ±poll-interval noise per side; flagged fallbacks are excluded from headline stats. |
| Merges keep the earlier event's id | Stable identity for links/history; v1 could duplicate rows by keeping the newer id. |
| SSE, not WebSockets | One-directional push is all we need. |
| Vanilla JS, no build step | Ship fast; the surface is small. Revisit only if it hurts. |
| Metra fetch needs a browser UA | CloudFront blocks default `curl` UA (verified live). |
| All display times America/Chicago | Storage is UTC; wall-clock display pins to the city, not the device. |
| Status strip states real cadence | "Checked 2m ago", never a fake live-wire claim. |
