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
# OPTIONAL — social signal is deferred (decision log 2026-07-02). Leave unset: the
# Reddit cycle is dormant and self-activates if credentials ever appear.
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=streetwise/1.0 by u/yourusername
# Metra developer bearer token (metra.com/metra-gtfs-api) — used only by the *realtime*
# feeds at gtfspublic.metrarr.com (alerts/positions/tripupdates). The static schedule
# zip that builds the gazetteer is public and needs no key.
METRA_GTFS_API_KEY=...
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
- **0.2 — done (2026-07-02).** Polls now partition items by content hash against
  `event_sources.last_hash`: unchanged → `touch_sources_seen` only (never reaches
  Claude); changed → re-extracted and folded into the existing event via
  `find_event_id_by_source` routing (`update_event` over SSE); new → the 0.1
  create/corroborate path. Updates preserve `id`, `detected_at`, verification,
  `score_source`/`score_corrob`, and latency fields; the new text wins `event_type`,
  `summary`, extraction score; location re-geocodes only when `location_string`
  actually changed. Two semantics worth remembering: (a) changed content is
  acknowledged (`mark_source_content`) after a *successful* extraction call even when
  it yields no event — retry on transport failure, never loop on "seen it, nothing
  there"; (b) a degrading update just lowers the score and the default `/events`
  threshold hides it — no deletion, no special case. 19 pytest cases green.
- **0.3 — done (2026-07-02).** `backend/lifecycle.py` (pure decisions + sweep):
  events with official sources clear when all of them are confirmed vanished — defined
  as ≥ 2 successful polls since `last_seen_at`, backed by the append-only `poll_log`
  (migration 002), which makes the feed-down guard structural (a failed cycle logs no
  poll, so a dead feed can clear nothing). **Design refinement over the spec** (decision
  log 2026-07-02): Reddit is an occurrence feed, so reported-only events never get
  `cleared_at` — they expire after 3 h into a separate `expired_at` column with no
  duration claim (`remove_event`, not `clear_event`), and a lingering /hot post can't
  hold a real clearance open. `main.poll_cycle` runs the sweep after the three sources
  and broadcasts `clear_event`/`remove_event`; `map.js` drops markers on both (the v1
  leak fix — clear/remove kept as distinct paths so 1.6 can add the cleared-fade).
  25 pytest cases green, including frozen-clock lifecycle decisions and a
  dead-feed-clears-nothing integration test.
- **0.4 — done (2026-07-02).** `scripts/build_gazetteer.py` → `data/gazetteer.json`
  (committed): 144 CTA rail stations from the city's L-Stops dataset (chosen over the
  98 MB raw GTFS), CTA official line colors, the 11 Metra line ids/names. Metra
  *stations* shipped credential-gated and empty at the time (all public URLs probed
  dead) — **superseded 2026-07-06**: the static zip is public after all; see the
  endpoint-resolved entry below. `backend/locate.py` resolves
  station → line → Nominatim point → none; ambiguous names (four "Westerns", two on the
  Blue Line alone) resolve to nothing, never a guess; extractor strings like "Howard
  Station, Chicago, IL" join the gazetteer today, before prompt v2. Verified: 4 real
  resolutions in 0.7 ms with Nominatim monkeypatched to explode. Two scope decisions
  (decision log): `lines.geojson` deferred with its only consumer (1.3b); Nominatim
  stays synchronous (the deferred-geocode worker is complexity a now-rare path doesn't
  justify — revisit if 0.7 shows pressure). 30 pytest cases green.
- **0.5 — done (2026-07-02).** Prompt v2 shipped in `extractor.py` (2048 tokens; input
  wrapped as `{"source", "items"}` with deterministic hints: CTA `service_id` routes +
  `impact`, Metra's per-line slug — the Metra fetcher now keeps the line it was already
  iterating). Model output is untrusted input: `_sanitize_event` coerces every enum
  (unknown scope → acute, unknown confidence → low); only events with no source_id or
  no summary are rejected. `is_clearance` extractions never create or update events —
  archived in raw_items (the capture), skipped before both routing paths. Updates carry
  severity/scope/mode/lines (an escalation is often exactly a severity change).
  **Live validation passed**: 12 real CTA alerts → 9 events; every station joined the
  gazetteer (incl. "Western" disambiguated via `lines=["Brown"]` and the slash-name
  "State/Lake"); all Elevator Status items came back `scope=chronic`; summaries read
  like rider texts; "Added Service" items correctly dropped. Live proof of the A1
  finding: today's entire CTA feed is chronic — acute-only verdicts (1.2) will read
  "Good service" where v1 showed a degraded system. `scope=planned` not yet observed
  live (no Planned Work item landed in the sample) — verify during 0.7. 39 pytest
  cases green.
- **0.6 — done (2026-07-02).** Matcher reworked per A6 (`scorer.are_corroborating`):
  30-min window + any anchor — lines overlap, same station, or ≤ 500 m; `event_type`
  equality gone (labels differ across sources); source-type disjointness stays in the
  pipeline via `event_sources`. Coordinate gates removed: two line-anchored events with
  no points corroborate on lines overlap (tested). Merges coalesce anchors ("enrich,
  never erase" — a CTA corroborator brings the lines/station a Reddit event lacked) and
  latency fields. Latency (A4): `official_at`/`first_social_at` from source-published
  timestamps — Reddit `created_utc`, Metra `pubDate`; **CTA's XML has no publish time**
  (field inventory checked live; `EventStart` is the disruption's schedule and would
  poison the data), so CTA-side observations always carry the flagged fetch-time
  fallback and the unflagged headline corpus is Reddit×Metra. `lead_seconds` derived,
  not stored. Staged Reddit→Metra pair test: unflagged 20-minute lead measured
  end-to-end. Read-time freshness: `age_minutes` stamped at serialization in
  `_hydrate`. 44 pytest cases green.
- **Social signal deferred (2026-07-02, decision log).** Reddit API access could not be
  obtained; the Reddit cycle is now credential-gated (`pipeline.reddit_configured()`) —
  absent from `/status` and the frontend dots rather than "unhealthy", self-activating
  if credentials ever appear. All states/corroboration/latency machinery stays built and
  unit-tested (the staged-pair tests are its live spec). The validation week runs on
  official feeds; PRD criterion 4 is met by unit tests. The durations archive is the
  MVP moat; latency waits for an accessible social source (Bluesky the candidate).
- **Metra endpoint resolved; gazetteer complete (2026-07-06).** The developer portal
  revealed the current hosts: realtime GTFS-rt feeds at
  `gtfspublic.metrarr.com/gtfs/public/{alerts,positions,tripupdates}` (bearer
  `METRA_GTFS_API_KEY` — verified 200 with token, 401 without), and the static schedule
  zip at `schedules.metrarail.com/gtfs/schedule.zip`, which turned out to be **public —
  no key needed**. `build_gazetteer.py` rewritten: downloads the ~470 KB zip and, since
  it ships all join tables, computes the real station→line mapping
  (stop_times → trips → routes) plus line colors from Metra's own `route_color`.
  Gazetteer now: 144 CTA + 241 Metra stations, 19 lines, zero env dependencies.
  Verified: "Western Ave" + `lines=["MD-N"]` resolves to the Metra station (not CTA's
  Western); Metra alerts no longer depend on Nominatim for station-named locations.
  Follow-up noted for later, not now: the authenticated GTFS-rt *alerts* feed is a
  candidate replacement for the per-line AJAX scrape in `fetchers/metra.py` (real
  publish timestamps, one request instead of eleven) — a 0.7+ decision, not a blocker.
- **Next: 0.7 — `/review` eval surface + the validation week (official feeds). Gates
  Phase 1.**
