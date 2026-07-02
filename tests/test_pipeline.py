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
        "first_seen_at": event["sources"][0]["first_seen_at"],
        "last_seen_at": event["sources"][0]["last_seen_at"],
        "published_at": None,
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


def test_changed_alert_updates_its_event(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    event_id = pipeline.run_cta_cycle()[0]["id"]
    original = store.get_event(event_id)

    escalated_alert = dict(
        CTA_ALERT, short_description="Red Line suspended between Howard and Belmont."
    )
    escalated_extract = dict(
        CTA_EXTRACTED,
        event_type="suspension",
        summary="Red Line suspended between Howard and Belmont.",
    )
    geocalls = []
    monkeypatch.setattr(pipeline, "fetch_cta_alerts", lambda: [escalated_alert])
    monkeypatch.setattr(pipeline, "extract_events",
                        lambda batch: [escalated_extract] if batch else [])
    monkeypatch.setattr(pipeline, "geocode",
                        lambda s: geocalls.append(s) or {"lat": 1.0, "lng": 2.0})

    stored = pipeline.run_cta_cycle()
    assert stored[0]["_is_new"] is False
    assert stored[0]["id"] == event_id  # an escalation escalates — never a sibling event

    updated = store.get_event(event_id)
    assert updated["event_type"] == "suspension"
    assert updated["summary"].startswith("Red Line suspended")
    assert updated["detected_at"] == original["detected_at"]  # when it began doesn't change
    assert updated["updated_at"] > original["updated_at"]
    assert updated["verification"] == "confirmed"  # a re-read never downgrades
    assert geocalls == []  # location unchanged: no Nominatim call spent
    assert len(store.get_active_events(min_confidence=0.0)) == 1

    # Third poll, same escalated content: fully deduped, Claude never sees it again.
    calls = []
    monkeypatch.setattr(pipeline, "extract_events", lambda batch: calls.append(batch) or [])
    assert pipeline.run_cta_cycle() == []
    assert calls == [[]]


def test_update_with_new_location_regeocodes(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    event_id = pipeline.run_cta_cycle()[0]["id"]

    moved_alert = dict(CTA_ALERT, short_description="Incident relocated.")
    moved_extract = dict(CTA_EXTRACTED, location_string="Belmont Station, Chicago, IL")
    _wire_cta(monkeypatch, [moved_alert], [moved_extract],
              geo={"lat": 41.94, "lng": -87.653})
    pipeline.run_cta_cycle()

    event = store.get_event(event_id)
    assert event["location_name"] == "Belmont Station, Chicago, IL"
    assert event["lat"] == 41.94 and event["geo_kind"] == "point"


def test_changed_item_yielding_no_event_is_acknowledged(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    event_id = pipeline.run_cta_cycle()[0]["id"]
    original_summary = store.get_event(event_id)["summary"]

    resolved = dict(CTA_ALERT, short_description="Service has resumed.")
    monkeypatch.setattr(pipeline, "fetch_cta_alerts", lambda: [resolved])
    calls = []
    monkeypatch.setattr(pipeline, "extract_events", lambda batch: calls.append(batch) or [])

    assert pipeline.run_cta_cycle() == []
    assert len(calls[0]) == 1  # the changed item reached Claude once…
    assert pipeline.run_cta_cycle() == []
    assert calls[1] == []      # …and only once: content acknowledged, no re-extract loop

    # The event itself is untouched — ending it is clearance's job (step 0.3).
    assert store.get_event(event_id)["summary"] == original_summary


def test_degrading_update_falls_below_display_threshold(tmp_db, monkeypatch):
    # Solo Reddit event at 0.5 (visible, reported); the post gets edited to something
    # vague. The update applies honestly: score drops to 0.2, the default view hides
    # it, nothing is deleted or special-cased.
    monkeypatch.setattr(pipeline, "fetch_reddit_posts", lambda: [REDDIT_POST])
    monkeypatch.setattr(pipeline, "extract_events",
                        lambda batch: [REDDIT_EXTRACTED] if batch else [])
    monkeypatch.setattr(pipeline, "geocode", lambda s: {"lat": 42.019, "lng": -87.673})
    event_id = pipeline.run_reddit_cycle()[0]["id"]

    edited = dict(REDDIT_POST, selftext="nvm, might have been nothing")
    vague = dict(REDDIT_EXTRACTED, extraction_confidence="low")
    monkeypatch.setattr(pipeline, "fetch_reddit_posts", lambda: [edited])
    monkeypatch.setattr(pipeline, "extract_events", lambda batch: [vague] if batch else [])

    stored = pipeline.run_reddit_cycle()
    assert stored[0]["_is_new"] is False
    assert store.get_active_events(min_confidence=0.4) == []  # hidden from the default view
    event = store.get_event(event_id)
    assert event["confidence"] == 0.2  # 0.2 source + 0.0 extraction — archived, not erased


def test_same_source_type_never_corroborates(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    pipeline.run_cta_cycle()

    second = dict(CTA_ALERT, alert_id="1002")
    second_extracted = dict(CTA_EXTRACTED, source_id="1002")
    _wire_cta(monkeypatch, [second], [second_extracted])
    stored = pipeline.run_cta_cycle()

    assert stored[0]["_is_new"] is True  # two CTA alerts are two events, not corroboration
    assert len(store.get_active_events(min_confidence=0.0)) == 2
