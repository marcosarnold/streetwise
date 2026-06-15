"""Main poll cycle orchestrator: fetch -> extract -> geocode -> score -> store."""

import json
import uuid
from datetime import datetime, timezone
from typing import Callable

from backend.extractor import extract_events
from backend.fetchers.cta import fetch_cta_alerts
from backend.fetchers.metra import fetch_metra_alerts
from backend.fetchers.reddit import fetch_reddit_posts
from backend.geocoder import geocode
from backend.scorer import CORROBORATION_SCORE, are_corroborating, score_event
from backend.store import (
    cleanup_old_events,
    get_active_events,
    get_connection,
    get_known_source_ids,
    init_db,
    upsert_event,
)

DROP_THRESHOLD = 0.4


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
    with get_connection() as conn:
        cleanup_old_events(conn)

    items = fetch_fn()

    known_ids = get_known_source_ids()
    new_items = [i for i in items if i[id_key] not in known_ids]

    batch = [batch_mapper(i) for i in new_items]
    extracted = extract_events(batch)

    now = datetime.now(timezone.utc)
    active_events = _load_active_events()

    stored = []
    for event in extracted:
        confidence = score_event(
            source_type=source_type,
            extraction_confidence=event["extraction_confidence"],
            detected_at=now.isoformat(),
            corroborated=False,
            now=now,
        )
        if confidence < DROP_THRESHOLD:
            continue

        geo = geocode(event["location_string"])

        record = {
            "id": str(uuid.uuid4()),
            "event_type": event["event_type"],
            "location_name": event["location_string"],
            "lat": geo["lat"],
            "lng": geo["lng"],
            "geocode_failed": int(geo["geocode_failed"]),
            "summary": event["summary"],
            "confidence": confidence,
            "sources": [event["source_id"]],
            "estimated_duration": event["estimated_duration"],
            "detected_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }

        match = None
        if not record["geocode_failed"]:
            match = _find_corroborating_event(record, source_type, active_events)

        is_new = match is None
        if match:
            record = _merge_events(match, record, now)
            active_events = [e for e in active_events if e["id"] != match["id"]]

        upsert_event(record)
        record["_is_new"] = is_new
        stored.append(record)
        active_events.append(record)

    return stored


def _load_active_events() -> list[dict]:
    events = get_active_events(min_confidence=0.0)
    for event in events:
        event["sources"] = json.loads(event["sources"]) if event["sources"] else []
    return events


def _infer_source_type(source_id: str) -> str:
    if source_id.startswith("DevAPI-"):
        return "metra"
    if source_id.isdigit():
        return "cta"
    return "reddit"


def _find_corroborating_event(
    record: dict, source_type: str, active_events: list[dict]
) -> dict | None:
    for existing in active_events:
        if existing["geocode_failed"]:
            continue

        existing_source_types = {_infer_source_type(s) for s in existing["sources"]}
        if source_type in existing_source_types:
            continue

        candidate = {
            "source_type": source_type,
            "event_type": record["event_type"],
            "lat": record["lat"],
            "lng": record["lng"],
            "detected_at": record["detected_at"],
        }
        other = {
            "source_type": next(iter(existing_source_types)),
            "event_type": existing["event_type"],
            "lat": existing["lat"],
            "lng": existing["lng"],
            "detected_at": existing["detected_at"],
        }
        if are_corroborating(candidate, other):
            return existing

    return None


def _merge_events(existing: dict, new_record: dict, now: datetime) -> dict:
    """Merge a corroborating new event into the existing record."""
    primary = existing if existing["confidence"] >= new_record["confidence"] else new_record
    base_confidence = max(existing["confidence"], new_record["confidence"])

    return {
        "id": primary["id"],
        "event_type": primary["event_type"],
        "location_name": primary["location_name"],
        "lat": primary["lat"],
        "lng": primary["lng"],
        "geocode_failed": primary["geocode_failed"],
        "summary": primary["summary"],
        "confidence": min(base_confidence + CORROBORATION_SCORE, 1.0),
        "sources": existing["sources"] + new_record["sources"],
        "estimated_duration": primary["estimated_duration"],
        "detected_at": existing["detected_at"],
        "updated_at": now.isoformat(),
    }


if __name__ == "__main__":
    stored = run_full_cycle()
    print(f"Stored/updated {len(stored)} event(s)")
    for event in stored:
        print(event)
