"""Main poll cycle orchestrator: fetch -> archive -> extract -> geocode -> score -> store.

Schema-v2 adoption (step 0.1). Still pending, by design, in later steps:
content-hash update flow (0.2), clearance (0.3), gazetteer resolution (0.4),
prompt v2 fields (0.5 — until then mode/lines/station/severity are empty and
scope defaults to 'acute'), matcher/latency changes (0.6).
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Callable

from backend.extractor import extract_events
from backend.fetchers.cta import fetch_cta_alerts
from backend.fetchers.metra import fetch_metra_alerts
from backend.fetchers.reddit import fetch_reddit_posts
from backend.locate import resolve_location
from backend.scorer import (
    CORROBORATION_SCORE,
    are_corroborating,
    extraction_score,
    source_score,
)
from backend.store import (
    EVENT_COLUMNS,
    archive_raw_item,
    content_hash,
    find_event_id_by_source,
    get_active_events,
    get_active_source_types,
    get_event,
    init_db,
    known_source_hashes,
    link_source,
    mark_source_content,
    record_poll,
    touch_sources_seen,
    update_raw_extraction,
    upsert_event,
)

DROP_THRESHOLD = 0.4
CONFIRM_THRESHOLD = 0.6
OFFICIAL_SOURCES = {"cta", "metra"}


def _published_at(source_type: str, raw_item: dict) -> str | None:
    """The source's OWN published timestamp — the latency measurement anchor (A4).
    Fetch-time fallback (and its flag) is the caller's job. Returns None for CTA: the
    alerts XML carries no publish time (full field inventory checked 2026-07-02), and
    EventStart is the disruption's schedule, not the alert's — using it would poison
    the latency data (planned work "starts" days after it posts)."""
    if source_type == "metra":
        return raw_item.get("pubDate") or None  # data-last-updated: a real publish time
    if source_type == "reddit":
        created = raw_item.get("created_utc")
        try:
            return datetime.fromtimestamp(float(created), tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            return None
    return None


def run_cta_cycle() -> list[dict]:
    """Run one poll cycle for the CTA source. Returns the events stored/updated."""
    return _run_cycle(
        source_type="cta",
        fetch_fn=fetch_cta_alerts,
        id_key="alert_id",
        batch_mapper=lambda a: {
            "id": a["alert_id"],
            "headline": a["headline"],
            "description": a["short_description"],
            # The agency's own affected-route ids — a deterministic `lines` hint.
            "routes": a["service_id"],
            # The agency's own classification ("Planned Work", "Elevator Status"…) —
            # the primary hint for `scope`.
            "impact": a["impact"],
            "event_start": a["event_start"],
            "event_end": a["event_end"],
        },
    )


def run_metra_cycle() -> list[dict]:
    """Run one poll cycle for the Metra source. Returns the events stored/updated."""
    return _run_cycle(
        source_type="metra",
        fetch_fn=fetch_metra_alerts,
        id_key="guid",
        batch_mapper=lambda a: {
            "id": a["guid"],
            "headline": a["title"],
            "description": a["description"],
            "pub_date": a["pubDate"],
            "line": a.get("line"),  # which line's modal served it — a `lines` hint
            "link": a["link"],
        },
    )


def run_reddit_cycle() -> list[dict]:
    """Run one poll cycle for the Reddit source. Returns the events stored/updated."""
    return _run_cycle(
        source_type="reddit",
        fetch_fn=fetch_reddit_posts,
        id_key="id",
        batch_mapper=lambda p: {
            "id": p["id"],
            "headline": p["title"],
            "description": p["selftext"],
            "subreddit": p["subreddit"],
            "url": p["url"],
        },
    )


def reddit_configured() -> bool:
    """Social signal is deferred out of the MVP (decision log 2026-07-02: no accessible
    API) — the cycle and its status dot self-activate when credentials appear, the same
    pattern as the Metra gazetteer keys. The machinery it feeds (verification states,
    corroboration, latency capture, TTL expiry) stays built and unit-tested."""
    def cred(name: str) -> str:
        value = os.environ.get(name, "").strip()
        return "" if value == "..." else value  # the .env template's own placeholder

    return bool(cred("REDDIT_CLIENT_ID") and cred("REDDIT_CLIENT_SECRET"))


def run_full_cycle() -> list[dict]:
    """Run a poll cycle across all configured sources."""
    stored = run_cta_cycle() + run_metra_cycle()
    if reddit_configured():
        stored += run_reddit_cycle()
    return stored


def _run_cycle(
    source_type: str,
    fetch_fn: Callable[[], list[dict]],
    id_key: str,
    batch_mapper: Callable[[dict], dict],
) -> list[dict]:
    init_db()

    items = fetch_fn()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Archive every fetched item before anything downstream can fail — the raw record
    # is the eval corpus and the replay log. Duplicate (id, content) pairs no-op;
    # changed content gets a new row, so history accumulates.
    items_by_id: dict[str, dict] = {}
    hashes: dict[str, str] = {}
    for item in items:
        sid = str(item[id_key])
        items_by_id[sid] = item
        hashes[sid] = content_hash(item)
        archive_raw_item(source_type, sid, hashes[sid], json.dumps(item), now_iso)

    # Partition by content, not just id: unchanged items never reach Claude; changed
    # items re-enter as updates (an escalating alert must escalate — v1 skipped it forever).
    prev = known_source_hashes(source_type)
    unchanged = [sid for sid, h in hashes.items() if prev.get(sid) == h]
    changed = [sid for sid in hashes if sid in prev and prev[sid] != hashes[sid]]
    new = [sid for sid in hashes if sid not in prev]

    # Still-present items keep their events alive — last_seen_at is what clearance
    # detection (0.3) will read.
    touch_sources_seen(source_type, unchanged, now_iso)

    batch = [batch_mapper(items_by_id[sid]) for sid in new + changed]
    extracted = extract_events(batch, source_type)

    # The extraction CALL succeeded: acknowledge every changed item's new content now,
    # even those that yielded no event — an unacknowledged hash would re-extract every
    # cycle forever. (If extract_events raised, nothing is acknowledged and the next
    # cycle retries: retry on transport failure, don't retry on "seen it, nothing there".)
    for sid in changed:
        mark_source_content(source_type, sid, hashes[sid], now_iso)

    active_events = get_active_events(min_confidence=0.0)
    source_types_by_event = get_active_source_types()

    stored = []
    for event in extracted:
        sid = str(event.get("source_id", ""))
        update_raw_extraction(source_type, sid, hashes.get(sid), json.dumps(event))

        # A "service resumed" item is not a disruption — creating an event from it
        # would be actively wrong, and rewriting an existing event's summary with it
        # would too. The archive line above IS the capture (is_clearance is
        # capture-only per the decision log); the vanish detector owns endings.
        if event.get("is_clearance"):
            continue

        s_src = source_score(source_type)
        s_ext = extraction_score(event["extraction_confidence"])

        # Update routing: a source item already linked to an event is re-describing it —
        # fold the fresh extraction into that event, never create a sibling. This sits
        # BEFORE the drop threshold on purpose: an update that degrades below the display
        # threshold still applies; the /events min_confidence filter hides it (honest
        # degradation, no special case, nothing deleted).
        existing_id = find_event_id_by_source(source_type, sid)
        if existing_id is not None:
            existing = get_event(existing_id)
            if existing is not None:
                record = _apply_update(existing, event, s_ext, now_iso)
                upsert_event(record)
                record["_is_new"] = False
                stored.append(record)
                active_events = [e for e in active_events if e["id"] != record["id"]]
                active_events.append(record)
            continue

        # v1 also added an ingest-time recency bonus; it was a constant (+0.05 for every
        # event) and is gone. Freshness lives in read-time serialization, not the score.
        if s_src + s_ext < DROP_THRESHOLD:
            continue  # raw item + extraction stay archived for /review

        # Gazetteer first, Nominatim fallback, or nothing (backend/locate.py). The
        # station/lines fields are extractor-provided from prompt v2 (0.5) onward;
        # until then the location_string itself often names a station and still joins.
        resolved = resolve_location(
            event.get("station"), event.get("lines"), event.get("location_string")
        )

        record = {
            "id": str(uuid.uuid4()),
            "event_type": event["event_type"],
            "mode": event.get("mode"),
            "lines": event.get("lines") or [],
            "station": resolved["station"],
            "severity": event.get("severity"),
            "scope": event.get("scope") or "acute",
            "location_name": resolved["location_name"],
            "lat": resolved["lat"],
            "lng": resolved["lng"],
            "geo_kind": resolved["geo_kind"],  # no fake pins — none means list-only
            "verification": "confirmed"
            if (source_type in OFFICIAL_SOURCES or s_src + s_ext >= CONFIRM_THRESHOLD)
            else "reported",
            "summary": event["summary"],
            "score_source": s_src,
            "score_extraction": s_ext,
            "score_corrob": 0.0,
            "detected_at": now_iso,
            "updated_at": now_iso,
        }

        # Latency anchors (A4): the source's own published timestamp when it has one;
        # fetch-time otherwise, flagged so headline statistics can exclude it.
        published = _published_at(source_type, items_by_id.get(sid, {}))
        official = source_type in OFFICIAL_SOURCES
        record["official_at"] = (published or now_iso) if official else None
        record["first_social_at"] = (published or now_iso) if not official else None
        record["latency_flagged"] = 0 if published else 1

        match = _find_corroborating_event(
            record, source_type, active_events, source_types_by_event
        )

        if match is None:
            upsert_event(record)
            link_source(record["id"], source_type, sid, now_iso,
                        hashes.get(sid, ""), published)
            record["_is_new"] = True
            active_events.append(record)
            source_types_by_event[record["id"]] = {source_type}
        else:
            record = _merge_events(match, record, now_iso)
            upsert_event(record)
            link_source(record["id"], source_type, sid, now_iso,
                        hashes.get(sid, ""), published)
            record["_is_new"] = False
            active_events = [e for e in active_events if e["id"] != record["id"]] + [record]
            source_types_by_event.setdefault(record["id"], set()).add(source_type)

        stored.append(record)

    # Reaching here means the whole cycle succeeded (fetch included) — only now may the
    # poll be logged. poll_log is what lets clearance prove "vanished": a cycle that
    # raised records nothing, so a dead feed can never clear events (lifecycle.py).
    record_poll(source_type, now_iso, len(items))

    return stored


_MATCH_KEYS = ("lines", "station", "lat", "lng", "detected_at")


def _find_corroborating_event(
    record: dict,
    source_type: str,
    active_events: list[dict],
    source_types_by_event: dict[str, set[str]],
) -> dict | None:
    """First active event from a DIFFERENT source type that shares an anchor with the
    record (lines/station/proximity — scorer.are_corroborating). No coordinate gate:
    two line-anchored events with no point at all can corroborate on lines overlap."""
    candidate = {k: record.get(k) for k in _MATCH_KEYS}
    for existing in active_events:
        # Source types come from event_sources — explicit, never inferred from ID shape
        # (the v1 heuristic could silently mis-corroborate: a trust bug).
        existing_types = source_types_by_event.get(existing["id"], set())
        if not existing_types or source_type in existing_types:
            continue
        if are_corroborating(candidate, {k: existing.get(k) for k in _MATCH_KEYS}):
            return existing
    return None


def _apply_update(existing: dict, event: dict, s_ext: float, now_iso: str) -> dict:
    """Fold a changed source item's fresh extraction into its existing event.

    Preserved on principle: id and detected_at (an update changes what we know, not
    when it began), verification (a re-read never downgrades it), score_source and
    score_corrob, and the latency fields. What the new text carries wins: event_type,
    summary, extraction score — and location, re-geocoded only if it actually changed
    (Nominatim is rate-limited; an unchanged location must not cost a call).
    """
    updated = {c: existing.get(c) for c in EVENT_COLUMNS}

    loc = event.get("location_string")
    if loc and loc != existing.get("location_name"):
        resolved = resolve_location(event.get("station"), event.get("lines"), loc)
        updated["location_name"] = resolved["location_name"]
        updated["station"] = resolved["station"]
        updated["lat"] = resolved["lat"]
        updated["lng"] = resolved["lng"]
        updated["geo_kind"] = resolved["geo_kind"]

    updated["event_type"] = event["event_type"]
    updated["summary"] = event["summary"]
    # An escalation is often precisely a severity/scope change — the new read wins
    # where it speaks; silence preserves what we knew.
    for key in ("mode", "severity", "scope"):
        if event.get(key):
            updated[key] = event[key]
    if event.get("lines"):
        updated["lines"] = event["lines"]
    updated["score_extraction"] = s_ext
    updated["updated_at"] = now_iso
    return updated


def _merge_events(existing: dict, new_record: dict, now_iso: str) -> dict:
    """Merge a corroborating new event into the existing one.

    The EXISTING event's id always survives — identity must stay stable for links and
    history. (v1 kept whichever record scored higher; when that was the new one, the
    old row was orphaned as a duplicate.)
    """
    def base(e: dict) -> float:
        return e["score_source"] + e["score_extraction"]

    primary = existing if base(existing) >= base(new_record) else new_record
    located = primary if primary.get("lat") is not None else (
        existing if existing.get("lat") is not None else new_record
    )

    merged = {c: existing.get(c) for c in ("id", "city", "event_type", "detected_at")}
    merged.update({
        # Anchors enrich, never erase: a CTA corroborator usually brings the lines/
        # station a Reddit-born event lacked.
        "mode": existing.get("mode") or new_record.get("mode"),
        "lines": existing.get("lines") or new_record.get("lines") or [],
        "station": existing.get("station") or new_record.get("station"),
        # Latency (A4): each side keeps its earliest timestamp; the pair is flagged if
        # EITHER side had to fall back to fetch time (headline stats exclude flagged).
        "official_at": existing.get("official_at") or new_record.get("official_at"),
        "first_social_at": existing.get("first_social_at") or new_record.get("first_social_at"),
        "latency_flagged": int(bool(existing.get("latency_flagged"))
                               or bool(new_record.get("latency_flagged"))),
        "location_name": primary.get("location_name"),
        "lat": located.get("lat"),
        "lng": located.get("lng"),
        "geo_kind": located.get("geo_kind") or "none",
        "severity": primary.get("severity"),
        "scope": primary.get("scope") or "acute",
        "verification": "confirmed",  # independent corroboration is the promotion
        "summary": primary["summary"],
        "score_source": primary["score_source"],
        "score_extraction": primary["score_extraction"],
        "score_corrob": CORROBORATION_SCORE,
        "updated_at": now_iso,
        "cleared_at": None,
    })
    return merged


if __name__ == "__main__":
    from backend.lifecycle import sweep

    stored = run_full_cycle()
    print(f"Stored/updated {len(stored)} event(s)")
    for event in stored:
        print(event)
    swept = sweep()
    print(f"Lifecycle: {len(swept['cleared'])} cleared, {len(swept['expired'])} expired")
