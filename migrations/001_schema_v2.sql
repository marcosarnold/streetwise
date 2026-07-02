-- 001 — Schema v2 (2026-07-01, transit-first pivot).
-- Assumes a fresh database: there is no v1→v2 data migration because the only v1 rows
-- were sandbox artifacts with fabricated coordinates (backed up in backups/, not migrated).
-- Rules encoded here (docs/architecture.md):
--   * Nothing is deleted — "active" means cleared_at IS NULL, a query not a lifecycle.
--   * verification ⊥ lifecycle ⊥ scope: three orthogonal axes, never one enum.
--   * events has NO sources column — event_sources is the source of truth.

CREATE TABLE IF NOT EXISTS events (
    id               TEXT PRIMARY KEY,             -- UUID; survives merges (earlier event wins)
    city             TEXT NOT NULL DEFAULT 'chicago',
    event_type       TEXT NOT NULL,                -- informational tag, not a join key
    mode             TEXT,                         -- cta_rail | cta_bus | metra | road | other (NULL until prompt v2)
    lines            TEXT NOT NULL DEFAULT '[]',   -- JSON array: ["Red"], ["66"], ["UP-N"]
    station          TEXT,                         -- canonical gazetteer name, or NULL
    location_name    TEXT,
    lat              REAL,                         -- only when geo_kind IN ('station','point')
    lng              REAL,
    geo_kind         TEXT NOT NULL DEFAULT 'none'
                     CHECK (geo_kind IN ('station', 'line', 'point', 'none')),
    severity         TEXT
                     CHECK (severity IN ('minor', 'major', 'severe') OR severity IS NULL),
    scope            TEXT NOT NULL DEFAULT 'acute'
                     CHECK (scope IN ('acute', 'chronic', 'planned')),
    verification     TEXT NOT NULL
                     CHECK (verification IN ('reported', 'confirmed')),
    summary          TEXT NOT NULL,
    score_source     REAL NOT NULL DEFAULT 0,      -- components; freshness is computed at read time
    score_extraction REAL NOT NULL DEFAULT 0,
    score_corrob     REAL NOT NULL DEFAULT 0,
    detected_at      TEXT NOT NULL,                -- ISO 8601 UTC (display always America/Chicago)
    updated_at       TEXT NOT NULL,
    cleared_at       TEXT,                         -- lifecycle; duration = cleared_at - detected_at
    first_social_at  TEXT,                         -- latency: source-published timestamps (step 0.6)
    official_at      TEXT,
    latency_flagged  INTEGER NOT NULL DEFAULT 0    -- 1 = a latency timestamp fell back to fetch time
);

CREATE INDEX IF NOT EXISTS idx_events_active ON events (cleared_at, detected_at);

-- Which source items feed which event. Backs three mechanisms: dedup ("seen this
-- item/hash?"), update routing ("whose event is this changed item?"), and clearance
-- ("which active events have no source seen in 2 successful polls?").
CREATE TABLE IF NOT EXISTS event_sources (
    event_id      TEXT NOT NULL REFERENCES events (id),
    source_type   TEXT NOT NULL CHECK (source_type IN ('cta', 'metra', 'reddit')),
    source_id     TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,                   -- touched every successful poll the item persists
    last_hash     TEXT NOT NULL,
    published_at  TEXT,                            -- the source's own timestamp, when available
    PRIMARY KEY (source_type, source_id)           -- dedup is per source type by design
);

CREATE INDEX IF NOT EXISTS idx_event_sources_event ON event_sources (event_id);

-- Every fetched item + its extraction, forever: the eval corpus and the replay log.
-- A changed item gets a new row (new content_hash) — history is preserved, not overwritten.
CREATE TABLE IF NOT EXISTS raw_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type  TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    payload      TEXT NOT NULL,                    -- raw item JSON
    extraction   TEXT,                             -- what Claude returned (NULL = no event / dropped)
    review       TEXT,                             -- correct | wrong_event | wrong_location | wrong_summary
    UNIQUE (source_type, source_id, content_hash)
);
