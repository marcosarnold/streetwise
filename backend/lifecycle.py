"""Event lifecycle: clearance and expiry decisions (dev-plan 0.3).

Two distinct endings, never conflated (docs/architecture.md):

- CLEARED — a real end signal. CTA/Metra feeds are current-state feeds: an alert's
  removal means the disruption resolved. An event with official sources clears when
  ALL of them are confirmed vanished. cleared_at - detected_at is honest duration data.
- EXPIRED — no signal, just age. Reddit is an occurrence feed: posts always drop out
  of the new/hot window, so their absence proves nothing (and their presence keeps
  nothing alive). Reported-only events leave the live view after a TTL with NO
  duration claim, and the frontend gets remove_event, not clear_event.

"Confirmed vanished" = the source's feed has completed >= VANISH_POLLS successful
polls since the item's last_seen_at. Poll-count-based, not wall-clock-based, so the
feed-down guard is structural: a broken fetcher records no polls, nothing can vanish,
and a dead feed can never paint the map green.

The decision functions are pure (clock injected, no I/O) — this is the code whose
correctness the product's honesty rests on, so it gets the frozen-clock tests.
"""

from datetime import datetime, timezone

from backend.store import (
    get_active_events,
    get_event,
    recent_poll_times,
    set_event_cleared,
    set_event_expired,
)

OFFICIAL_SOURCES = {"cta", "metra"}
VANISH_POLLS = 2

# How long a reported-only (solo Reddit) event stays in the live view after its last
# update. Conservative: the corroboration window is 30 min, so expiry at 3 h can never
# preempt a legitimate promotion; read-time freshness (0.6) dims it well before this.
REPORTED_TTL_SECONDS = 3 * 3600


# ---------------------------------------------------------------- pure decisions

def is_vanished(last_seen_at: str, poll_times: list[str]) -> bool:
    """A source item is confirmed vanished iff its feed completed >= VANISH_POLLS
    successful polls strictly after the item was last seen. Timestamps are our own
    ISO-8601 UTC strings, so lexicographic comparison is chronological."""
    return sum(1 for p in poll_times if p > last_seen_at) >= VANISH_POLLS


def should_clear(event: dict, poll_times_by_type: dict[str, list[str]]) -> bool:
    """Clear only on evidence: every OFFICIAL source confirmed vanished. Reddit
    sources are ignored on both sides — they neither hold an event open nor end it."""
    official = [s for s in event.get("sources", []) if s["type"] in OFFICIAL_SOURCES]
    if not official:
        return False
    return all(
        is_vanished(s["last_seen_at"], poll_times_by_type.get(s["type"], []))
        for s in official
    )


def should_expire(event: dict, now: datetime) -> bool:
    """Reported-only events (no official anchor) age out — expiry, not clearance."""
    if any(s["type"] in OFFICIAL_SOURCES for s in event.get("sources", [])):
        return False
    updated = datetime.fromisoformat(event["updated_at"])
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (now - updated).total_seconds() > REPORTED_TTL_SECONDS


# ---------------------------------------------------------------- sweep (I/O shell)

def sweep(now: datetime | None = None) -> dict[str, list[dict]]:
    """Run one lifecycle pass over active events. Returns the events that just ended,
    re-read from the store, for SSE broadcast: {"cleared": [...], "expired": [...]}."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()

    poll_times_by_type = {t: recent_poll_times(t) for t in OFFICIAL_SOURCES}

    cleared, expired = [], []
    for event in get_active_events(min_confidence=0.0):
        if should_clear(event, poll_times_by_type):
            set_event_cleared(event["id"], now_iso)
            cleared.append(get_event(event["id"]))
        elif should_expire(event, now):
            set_event_expired(event["id"], now_iso)
            expired.append(get_event(event["id"]))
    return {"cleared": cleared, "expired": expired}
