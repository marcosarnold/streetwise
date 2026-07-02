-- 002 — Clearance detection (2026-07-02, dev-plan 0.3).
--
-- expired_at is deliberately distinct from cleared_at: cleared = a real end signal
-- (official alert vanished from a successfully polled feed → honest duration data);
-- expired = a reported-only event aged out of the live view (Reddit posts always drop
-- out of the fetch window, so their absence proves nothing — no duration is claimed).

ALTER TABLE events ADD COLUMN expired_at TEXT;

-- Append-only log of successful polls per source. "Confirmed vanished" = the feed has
-- completed >= 2 successful polls since the item's last_seen_at — which makes the
-- feed-down guard structural: a broken fetcher records no polls, so nothing can vanish
-- and a dead feed can never read as a cleared city.
CREATE TABLE IF NOT EXISTS poll_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    polled_at   TEXT NOT NULL,
    items       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_poll_log_source ON poll_log (source_type, polled_at);
