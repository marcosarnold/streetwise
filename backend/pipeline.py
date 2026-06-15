"""Main poll cycle orchestrator: fetch -> extract -> geocode -> score -> store."""

import uuid
from datetime import datetime, timezone

from backend.extractor import extract_events
from backend.fetchers.cta import fetch_cta_alerts
from backend.geocoder import geocode
from backend.scorer import score_event
from backend.store import (
    cleanup_old_events,
    get_connection,
    get_known_source_ids,
    init_db,
    upsert_event,
)

DROP_THRESHOLD = 0.4


def run_cta_cycle() -> list[dict]:
    """Run one poll cycle for the CTA source. Returns the events stored."""
    init_db()
    with get_connection() as conn:
        cleanup_old_events(conn)

    alerts = fetch_cta_alerts()

    known_ids = get_known_source_ids()
    new_alerts = [a for a in alerts if a["alert_id"] not in known_ids]

    batch = [
        {
            "id": a["alert_id"],
            "headline": a["headline"],
            "description": a["short_description"],
            "impact": a["impact"],
            "event_start": a["event_start"],
            "event_end": a["event_end"],
        }
        for a in new_alerts
    ]

    extracted = extract_events(batch)

    now = datetime.now(timezone.utc)
    stored = []
    for event in extracted:
        confidence = score_event(
            source_type="cta",
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
        upsert_event(record)
        stored.append(record)

    return stored


if __name__ == "__main__":
    stored = run_cta_cycle()
    print(f"Stored {len(stored)} event(s)")
    for event in stored:
        print(event)
