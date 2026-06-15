# Streetwise — Architecture

## Stack
Python + FastAPI + Claude API + SQLite + Leaflet/OSM

## High-Level Flow
The pipeline runs on a 5-minute polling cycle:

```
Fetch (CTA, Metra, Reddit) → Extract (Claude API) → Geocode (Nominatim) → Score (Confidence) → Store + Push (SQLite + SSE)
```

## Components

| Component | Responsibility |
|---|---|
| **Fetcher** | Polls CTA XML feed, Metra RSS, and Reddit API on a 5-min cycle. Deduplicates raw items by ID/hash before passing downstream. |
| **Event Extractor** | Single Claude API call per source batch. Returns structured JSON: `event_type`, `location_string`, `summary`, `time`, `extraction_confidence`. |
| **Geocoder** | Passes `location_string` to Nominatim OSM API. Returns lat/lng. Falls back to bounding-box center (Chicago) if unresolvable. |
| **Scorer** | Computes confidence score from source type, extraction confidence, corroboration, and recency. |
| **Store** | Writes events to SQLite. Maintains 24-hour rolling window. Marks events resolved when source signals clearance. |
| **SSE Server** | FastAPI endpoint that streams new and updated events to connected frontend clients in real time. |
| **Map UI** | Leaflet + OpenStreetMap. Subscribes to SSE. Renders markers by impact level. Dimmed markers for unverified events. |

## Data Sources

### CTA Alerts
- Feed: `https://www.transitchicago.com/api/1.0/alerts.aspx?outputType=XML`
- Auth: none
- Format: XML (`xml.etree.ElementTree`)
- Poll: every 5 min
- Key fields: `alert_id`, `headline`, `short_description`, `service_id`, `impact`, `event_start`, `event_end`
- Dedup: by `alert_id`, skip if already stored & unchanged
- Base confidence: +0.4 (official source)

### Metra Service Alerts
> The original spec's RSS feed (`metrarail.com/rss/alerts`) no longer resolves —
> `metrarail.com` now redirects to `metra.com`, which has no RSS feed. Alerts are
> served via a per-line AJAX endpoint instead. `fetchers/metra.py` adapts to this
> while still producing `guid`/`title`/`description`/`pubDate`/`link` fields.

- System endpoint: `https://www.metra.com/service_alerts/update` — lists lines with active alerts
- Per-line endpoint: `https://www.metra.com/service_alerts/modal/{LINE}` — alert details (HTML fragment in JSON)
- Auth: none (requires a browser `User-Agent`, default `curl` UA is blocked by CloudFront)
- Format: JSON wrapping an HTML fragment, parsed with regex into structured dicts
- Poll: every 5 min
- Key fields: `guid` (`data-alert-id`), `title`, `description`, `pubDate` (from `data-last-updated`), `link`
- Dedup: by `guid`
- Base confidence: +0.4 (official source)

### Reddit
- Subreddits: r/chicago, r/Chicagoland
- Auth: Reddit API key (register at reddit.com/prefs/apps)
- Library: PRAW
- Query: new + hot posts, filtered for mobility keywords before sending to Claude
- Keyword filter: accident, crash, closed, delay, construction, police, fire, protest, flooding, Metra, CTA, L train, expressway, highway
- Poll: every 5 min (stay within free tier rate limits)
- Dedup: by post id
- Base confidence: +0.2 (social source)

## Claude API — Event Extraction

| Setting | Value |
|---|---|
| Model | `claude-sonnet-4-6` |
| Max tokens | 1024 per call |
| Temperature | 0 (deterministic) |
| Call pattern | One call per source per poll cycle (3 calls / 5-min cycle) |
| Input | Batch of raw posts/alerts as JSON array in user message |
| Output | JSON array of structured events |

### System Prompt (verbatim)
```
You are a mobility event extractor for the Chicagoland area. You receive raw
text from transit alerts, RSS feeds, or Reddit posts and return structured
mobility events as a JSON array.

For each mobility event detected, return an object with these exact fields:
 - event_type: one of [accident, construction, transit_disruption, police_activity,
 civic_event, weather_impact, other]
 - location_string: a specific, geocodable address or intersection in Chicago.
 Example: "I-90 westbound near Cicero Ave, Chicago, IL"
 Do NOT return vague strings like "downtown" or "north side".
 - summary: one sentence describing the event and its mobility impact.
 - estimated_duration: short | hours | ongoing | unknown
 - extraction_confidence: high | medium | low
 high = clear event, specific location, unambiguous impact
 medium = event likely but location or impact uncertain
 low = vague or speculative, minimal mobility signal
 - source_id: the id or guid of the source item

Return ONLY a JSON array. No explanation, no markdown, no preamble.
If no mobility events are detected, return an empty array: []
Drop posts that are questions, opinions, or historical references with no
current mobility impact.
```

## Geocoding Flow
1. Claude returns `location_string` (e.g. "I-90 westbound near Cicero Ave, Chicago, IL").
2. Pass to Nominatim: `https://nominatim.openstreetmap.org/search?q={location}&format=json&limit=1`
3. Extract lat/lng from the first result.
4. If no result, retry once with a simplified version of the string.
5. If still no result, set lat/lng to Chicago center (41.8781, −87.6298) and flag `geocode_failed=true`.
6. Rate limit: 1 request/second — add `time.sleep(1)` between calls.
7. Set a descriptive `User-Agent` header per Nominatim policy.

## Confidence Scoring

`score = min(source_score + extraction_score + corroboration_score + recency_score, 1.0)`

| Component | Detail | Max |
|---|---|---|
| Source type | CTA/Metra official: +0.4 / Reddit: +0.2 | +0.4 |
| Extraction confidence | high: +0.3 / medium: +0.15 / low: +0.0 | +0.3 |
| Corroboration | Same event from 2+ independent sources: +0.4 | +0.4 |
| Recency | <15 min: +0.05 / 15–60 min: +0.02 / >60 min: +0.0 | +0.05 |

### Display Thresholds

| Score | Behavior | Example |
|---|---|---|
| ≥ 0.6 | Show on map, full opacity | CTA alert, recent, high extraction → 0.75 |
| 0.4 – 0.59 | Show dimmed, "unverified" badge | Reddit, high extraction, no corroboration → 0.55 |
| < 0.4 | Drop silently, do not store | Reddit, medium extraction, no corroboration → 0.37 |

### Corroboration Matching
Two events corroborate if **all** of:
- Different sources (e.g. Reddit + CTA, not two Reddit posts)
- Same `event_type`
- Geocoded locations within 500m AND timestamps within 30 min

When corroboration is detected, merge into a single event record. Keep the higher-confidence
source as primary. Append both `source_id`s to a `sources` array.

## Data Model — `events` table (SQLite)

```sql
CREATE TABLE events (
  id                 TEXT PRIMARY KEY,        -- UUID generated at extraction time
  city               TEXT DEFAULT 'chicago',
  event_type         TEXT NOT NULL,           -- accident | construction | transit_disruption |
                                               -- police_activity | civic_event | weather_impact | other
  location_name      TEXT,                    -- human-readable location from Claude
  lat                REAL,                    -- from Nominatim
  lng                REAL,                    -- from Nominatim
  geocode_failed     INTEGER DEFAULT 0,       -- 1 if Nominatim returned no result
  summary            TEXT NOT NULL,
  impact_roads       TEXT,                    -- low | moderate | high
  impact_transit     TEXT,                    -- low | moderate | high
  impact_pedestrian  TEXT,                    -- low | moderate | high
  confidence         REAL NOT NULL,           -- 0.0 to 1.0
  sources            TEXT,                    -- JSON array of source IDs
  estimated_duration TEXT,                    -- short | hours | ongoing | unknown
  detected_at        TEXT NOT NULL,           -- ISO 8601 UTC
  updated_at         TEXT NOT NULL,           -- ISO 8601 UTC
  expires_at         TEXT                     -- ISO 8601 UTC (null = no known end time)
);
```

Retention policy: delete rows where `detected_at < NOW() - 24 hours`. Run cleanup at the start of
each poll cycle.

## FastAPI Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/events` | GET | Returns all active events (confidence ≥ 0.4). Query params: `min_confidence`, `event_type`, `limit`. |
| `/events/stream` | GET | SSE endpoint. Streams `new_event` and `update_event` messages. |
| `/events/{id}` | GET | Returns a single event by ID including full source list. |
| `/status` | GET | Pipeline health: `last_poll_at`, `events_active`, `sources_healthy` (CTA, Metra, Reddit booleans). |

### SSE Event Format
```
data: {"type": "new_event", "event": { ...event object... }}
data: {"type": "update_event", "event": { ...event object... }}
data: {"type": "ping"}   // heartbeat every 30s
```

## Frontend — Leaflet Map

- Vanilla HTML/JS/CSS, no framework, no build step.
- Leaflet.js + OpenStreetMap tiles.
- EventSource API for SSE.
- Served as static files by FastAPI.

| Aspect | Behavior |
|---|---|
| Default view | Chicago center (41.8781, −87.6298), zoom 11 |
| Marker color | Red = high impact / Amber = moderate / Gray = low |
| Marker opacity | Full (1.0) for confidence ≥ 0.6 / Dimmed (0.4) for 0.4–0.59 |
| Unverified badge | Small "?" overlay on dimmed markers |
| Popup on click | event_type, summary, impact levels, confidence score, sources, detected_at |
| Auto-update | SSE pushes trigger `addLayer()` / `updateMarker()` without reload |
| Status bar | Top-right: last updated timestamp + source health indicators |

## Project Structure

```
streetwise/
├── backend/
│   ├── main.py          # FastAPI app + SSE endpoint
│   ├── pipeline.py       # Main poll cycle orchestrator
│   ├── fetchers/
│   │   ├── cta.py        # CTA XML fetcher
│   │   ├── metra.py       # Metra RSS fetcher
│   │   └── reddit.py      # Reddit PRAW fetcher
│   ├── extractor.py       # Claude API call + JSON parsing
│   ├── geocoder.py        # Nominatim wrapper
│   ├── scorer.py           # Confidence scoring logic
│   ├── store.py            # SQLite read/write + cleanup
│   └── models.py            # Pydantic models for Event
├── frontend/
│   ├── index.html         # Map UI
│   ├── map.js              # Leaflet init + SSE client
│   └── style.css
├── .env                      # ANTHROPIC_API_KEY, REDDIT_* credentials
├── requirements.txt
└── README.md
```

## Known Constraints & Decisions

| Constraint / Decision | Rationale |
|---|---|
| No auth on API endpoints | Solo validation tool. Add auth before any public exposure. |
| SQLite not Postgres | Zero ops for solo use. Swap to Postgres when multi-user/multi-process. |
| Nominatim not Google Maps | Free tier sufficient for MVP. Switch if geocoding accuracy is a blocker. |
| No verification agent | Corroboration via simple proximity + time matching in `scorer.py`. Revisit if false positives are high. |
| Recency is a tiebreaker only | Max +0.05. Affects display ordering, not map visibility. |
| SSE not WebSockets | Simpler, sufficient for one-directional push. Upgrade if bidirectional comms needed. |
| Vanilla JS frontend | No build step, no dependencies. Ship fast, validate data quality first. |
