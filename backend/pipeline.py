"""Main poll cycle orchestrator: fetch -> archive -> extract -> geocode -> score -> store.

Schema-v2 adoption (step 0.1). Still pending, by design, in later steps:
content-hash update flow (0.2), clearance (0.3), gazetteer resolution (0.4),
prompt v2 fields (0.5 — until then mode/lines/station/severity are empty and
scope defaults to 'acute'), matcher/latency changes (0.6).
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Callable

from backend.extractor import extract_events
from backend.fetchers.cta import fetch_cta_alerts
from backend.fetchers.metra import fetch_metra_alerts
from backend.fetchers.reddit import fetch_reddit_posts
from backend.geocoder import geocode
from backend.scorer import (
    CORROBORATION_SCORE,
    are_corroborating,
    extraction_score,
    source_score,
)
from backend.store import (
    archive_raw_item,
    content_hash,
    get_active_events,
    get_active_source_types,
    init_db,
    known_source_ids,
    link_source,
    touch_sources_seen,
    update_raw_extraction,
    upsert_event,
)

DROP_THRESHOLD = 0.4
CONFIRM_THRESHOLD = 0.6
OFFICIAL_SOURCES = {"cta", "metra"}


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
            # The agency's own classification ("Planned Work", "Elevator Status"…) —
            # the primary hint for `scope` once prompt v2 lands (step 0.5).
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


def run_full_cycle() -> list[dict]:
    """Run a poll cycle across all sources."""
    return run_cta_cycle() + run_metra_cycle() + run_reddit_cycle()


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
    # is the eval corpus and the replay log. Duplicate (id, content) pairs no-op.
    hashes: dict[str, str] = {}
    for item in items:
        sid = str(item[id_key])
        hashes[sid] = content_hash(item)
        archive_raw_item(source_type, sid, hashes[sid], json.dumps(item), now_iso)

    # Items still present in this (successful) poll keep their events alive —
    # last_seen_at is what clearance detection (0.3) will read.
    known = known_source_ids(source_type)
    touch_sources_seen(source_type, [i for i in hashes if i in known], now_iso)

    new_items = [i for i in items if str(i[id_key]) not in known]
    batch = [batch_mapper(i) for i in new_items]
    extracted = extract_events(batch)

    active_events = get_active_events(min_confidence=0.0)
    source_types_by_event = get_active_source_types()

    stored = []
    for event in extracted:
        sid = str(event.get("source_id", ""))
        update_raw_extraction(source_type, sid, hashes.get(sid), json.dumps(event))

        s_src = source_score(source_type)
        s_ext = extraction_score(event["extraction_confidence"])
        # v1 also added an ingest-time recency bonus; it was a constant (+0.05 for every
        # event) and is gone. Freshness lives in read-time serialization, not the score.
        if s_src + s_ext < DROP_THRESHOLD:
            continue  # raw item + extraction stay archived for /review

        geo = geocode(event["location_string"]) if event.get("location_string") else None

        record = {
            "id": str(uuid.uuid4()),
            "event_type": event["event_type"],
            # mode/lines/station/severity arrive with prompt v2 (0.5); scope defaults
            # to acute so verdict behavior is unchanged until the extractor can tell.
            "mode": None,
            "lines": [],
            "station": None,
            "severity": None,
            "scope": "acute",
            "location_name": event.get("location_string"),
            "lat": geo["lat"] if geo else None,
            "lng": geo["lng"] if geo else None,
            "geo_kind": "point" if geo else "none",  # no fake pins — none means list-only
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

        match = None
        if geo:  # unlocated events can't distance-match (station/line matching: 0.6)
            match = _find_corroborating_event(
                record, source_type, active_events, source_types_by_event
            )

        if match is None:
            upsert_event(record)
            link_source(record["id"], source_type, sid, now_iso, hashes.get(sid, ""))
            record["_is_new"] = True
            active_events.append(record)
            source_types_by_event[record["id"]] = {source_type}
        else:
            record = _merge_events(match, record, now_iso)
            upsert_event(record)
            link_source(record["id"], source_type, sid, now_iso, hashes.get(sid, ""))
            record["_is_new"] = False
            active_events = [e for e in active_events if e["id"] != record["id"]] + [record]
            source_types_by_event.setdefault(record["id"], set()).add(source_type)

        stored.append(record)

    return stored


def _find_corroborating_event(
    record: dict,
    source_type: str,
    active_events: list[dict],
    source_types_by_event: dict[str, set[str]],
) -> dict | None:
    for existing in active_events:
        if existing.get("lat") is None:
            continue

        # Source types come from event_sources — explicit, never inferred from ID shape
        # (the v1 heuristic could silently mis-corroborate: a trust bug).
        existing_types = source_types_by_event.get(existing["id"], set())
        if not existing_types or source_type in existing_types:
            continue

        candidate = {
            "source_type": source_type,
            "event_type": record["event_type"],
            "lat": record["lat"],
            "lng": record["lng"],
            "detected_at": record["detected_at"],
        }
        other = {
            "source_type": next(iter(existing_types)),
            "event_type": existing["event_type"],
            "lat": existing["lat"],
            "lng": existing["lng"],
            "detected_at": existing["detected_at"],
        }
        if are_corroborating(candidate, other):
            return existing

    return None


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

    merged = {c: existing.get(c) for c in (
        "id", "city", "event_type", "mode", "lines", "station",
        "detected_at", "first_social_at", "official_at", "latency_flagged",
    )}
    merged.update({
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
    stored = run_full_cycle()
    print(f"Stored/updated {len(stored)} event(s)")
    for event in stored:
        print(event)
