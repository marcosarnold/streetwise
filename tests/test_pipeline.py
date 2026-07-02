"""Pipeline end-to-end with all I/O monkeypatched: no network, no Claude calls.

These are the schema-v2 adoption guarantees (dev-plan 0.1): raw archived first, typed
source links, honest geo_kind, verification derivation, per-type dedup, no fake pins.
"""

import json

from backend import pipeline, store

CTA_ALERT = {
    "alert_id": "1001",
    "headline": "Red Line delays",
    "short_description": "Trains are operating with residual delays near Howard.",
    "service_id": "Red",
    "impact": "Service Disruption",
    "event_start": "2026-07-01T11:50:00",
    "event_end": "",
}

CTA_EXTRACTED = {
    "event_type": "transit_disruption",
    "location_string": "Howard Station, Chicago, IL",
    "summary": "Red Line trains delayed near Howard.",
    "estimated_duration": "hours",
    "extraction_confidence": "high",
    "source_id": "1001",
}

REDDIT_POST = {
    "id": "abc99",
    "title": "Red line stopped at Howard??",
    "selftext": "Been sitting here 20 minutes",
    "subreddit": "chicago",
    "url": "https://reddit.com/abc99",
}

REDDIT_EXTRACTED = {
    "event_type": "transit_disruption",
    "location_string": "Howard Station, Chicago, IL",
    "summary": "Riders report Red Line trains stopped at Howard.",
    "estimated_duration": "unknown",
    "extraction_confidence": "high",
    "source_id": "abc99",
}


def _wire_cta(monkeypatch, alerts, extracted, geo={"lat": 42.019, "lng": -87.673}):
    monkeypatch.setattr(pipeline, "fetch_cta_alerts", lambda: alerts)
    monkeypatch.setattr(pipeline, "extract_events", lambda batch: extracted if batch else [])
    monkeypatch.setattr(pipeline, "geocode", lambda s: geo)


def test_cta_cycle_writes_v2_rows(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    stored = pipeline.run_cta_cycle()

    assert len(stored) == 1
    event = store.get_event(stored[0]["id"])
    assert event["verification"] == "confirmed"  # official source: born confirmed
    assert event["geo_kind"] == "point"
    assert event["scope"] == "acute"
    assert event["confidence"] == 0.7  # 0.4 source + 0.3 extraction; no dead recency bonus
    assert event["sources"] == [{
        "type": "cta", "id": "1001",
        "first_seen_at": event["sources"][0]["first_seen_at"], "published_at": None,
    }]

    # Raw archived with the extraction paired for /review.
    with store.get_connection() as conn:
        raw = conn.execute("SELECT * FROM raw_items").fetchone()
    assert json.loads(raw["payload"])["alert_id"] == "1001"
    assert json.loads(raw["extraction"])["source_id"] == "1001"


def test_second_poll_dedupes_and_touches_last_seen(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    pipeline.run_cta_cycle()

    calls = []
    monkeypatch.setattr(pipeline, "extract_events",
                        lambda batch: calls.append(batch) or [])
    with store.get_connection() as conn:
        before = conn.execute("SELECT last_seen_at FROM event_sources").fetchone()[0]

    assert pipeline.run_cta_cycle() == []
    assert calls == [[]]  # known item never reaches Claude

    with store.get_connection() as conn:
        after = conn.execute("SELECT last_seen_at FROM event_sources").fetchone()[0]
    assert after > before  # still-present item keeps its event alive (clearance substrate)


def test_geocode_failure_means_no_pin(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED], geo=None)
    stored = pipeline.run_cta_cycle()

    event = store.get_event(stored[0]["id"])
    assert event["geo_kind"] == "none"
    assert event["lat"] is None and event["lng"] is None  # never a fabricated point


def test_solo_reddit_is_reported(tmp_db, monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_reddit_posts", lambda: [REDDIT_POST])
    monkeypatch.setattr(pipeline, "extract_events",
                        lambda batch: [REDDIT_EXTRACTED] if batch else [])
    monkeypatch.setattr(pipeline, "geocode", lambda s: {"lat": 42.019, "lng": -87.673})

    stored = pipeline.run_reddit_cycle()
    event = store.get_event(stored[0]["id"])
    assert event["verification"] == "reported"  # 0.2 + 0.3 = 0.5: visible, unverified
    assert event["confidence"] == 0.5


def test_corroboration_merges_promotes_and_keeps_earlier_id(tmp_db, monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_reddit_posts", lambda: [REDDIT_POST])
    monkeypatch.setattr(pipeline, "extract_events",
                        lambda batch: [REDDIT_EXTRACTED] if batch else [])
    monkeypatch.setattr(pipeline, "geocode", lambda s: {"lat": 42.019, "lng": -87.673})
    reddit_stored = pipeline.run_reddit_cycle()
    reddit_id = reddit_stored[0]["id"]

    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    cta_stored = pipeline.run_cta_cycle()

    merged = cta_stored[0]
    assert merged["_is_new"] is False
    assert merged["id"] == reddit_id  # the earlier event's identity survives the merge

    event = store.get_event(reddit_id)
    assert event["verification"] == "confirmed"
    assert event["score_corrob"] == 0.4
    assert event["confidence"] == 1.1  # 0.4 + 0.3 + 0.4 (uncapped components; states cap meaning)
    assert {s["type"] for s in event["sources"]} == {"cta", "reddit"}

    # Only one event exists — no orphaned duplicate (the v1 merge bug).
    assert len(store.get_active_events(min_confidence=0.0)) == 1


def test_same_source_type_never_corroborates(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    pipeline.run_cta_cycle()

    second = dict(CTA_ALERT, alert_id="1002")
    second_extracted = dict(CTA_EXTRACTED, source_id="1002")
    _wire_cta(monkeypatch, [second], [second_extracted])
    stored = pipeline.run_cta_cycle()

    assert stored[0]["_is_new"] is True  # two CTA alerts are two events, not corroboration
    assert len(store.get_active_events(min_confidence=0.0)) == 2
