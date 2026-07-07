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


def _fake_resolve(geo):
    """Stand-in for locate.resolve_location: a fixed point, or nothing when geo=None."""
    def resolve(station, lines, location_string):
        if geo is None:
            return {"geo_kind": "none", "lat": None, "lng": None, "station": None,
                    "location_name": location_string}
        return {"geo_kind": "point", "lat": geo["lat"], "lng": geo["lng"],
                "station": None, "location_name": location_string}
    return resolve


def _wire_cta(monkeypatch, alerts, extracted, geo={"lat": 42.019, "lng": -87.673}):
    monkeypatch.setattr(pipeline, "fetch_cta_alerts", lambda: alerts)
    monkeypatch.setattr(pipeline, "extract_events", lambda batch, *a: extracted if batch else [])
    monkeypatch.setattr(pipeline, "resolve_location", _fake_resolve(geo))


def test_cta_cycle_writes_v2_rows(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    stored = pipeline.run_cta_cycle()

    assert len(stored) == 1
    event = store.get_event(stored[0]["id"])
    assert event["verification"] == "confirmed"  # official source: born confirmed
    assert event["geo_kind"] == "point"
    assert event["scope"] == "acute"
    assert event["confidence"] == 0.7  # 0.4 source + 0.3 extraction; no dead recency bonus
    # CTA's XML carries no publish time (checked 2026-07-02): official_at falls back
    # to fetch time and the observation is flagged out of headline latency stats.
    assert event["official_at"] is not None and event["first_social_at"] is None
    assert event["latency_flagged"] == 1
    assert isinstance(event["age_minutes"], int)  # read-time freshness, never stored
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
                        lambda batch, *a: calls.append(batch) or [])
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
                        lambda batch, *a: [REDDIT_EXTRACTED] if batch else [])
    monkeypatch.setattr(pipeline, "resolve_location",
                        _fake_resolve({"lat": 42.019, "lng": -87.673}))

    stored = pipeline.run_reddit_cycle()
    event = store.get_event(stored[0]["id"])
    assert event["verification"] == "reported"  # 0.2 + 0.3 = 0.5: visible, unverified
    assert event["confidence"] == 0.5


def test_corroboration_merges_promotes_and_keeps_earlier_id(tmp_db, monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_reddit_posts", lambda: [REDDIT_POST])
    monkeypatch.setattr(pipeline, "extract_events",
                        lambda batch, *a: [REDDIT_EXTRACTED] if batch else [])
    monkeypatch.setattr(pipeline, "resolve_location",
                        _fake_resolve({"lat": 42.019, "lng": -87.673}))
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
                        lambda batch, *a: [escalated_extract] if batch else [])
    monkeypatch.setattr(
        pipeline, "resolve_location",
        lambda st, ln, s: geocalls.append(s) or _fake_resolve({"lat": 1.0, "lng": 2.0})(st, ln, s),
    )

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
    monkeypatch.setattr(pipeline, "extract_events", lambda batch, *a: calls.append(batch) or [])
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
    monkeypatch.setattr(pipeline, "extract_events", lambda batch, *a: calls.append(batch) or [])

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
                        lambda batch, *a: [REDDIT_EXTRACTED] if batch else [])
    monkeypatch.setattr(pipeline, "resolve_location",
                        _fake_resolve({"lat": 42.019, "lng": -87.673}))
    event_id = pipeline.run_reddit_cycle()[0]["id"]

    edited = dict(REDDIT_POST, selftext="nvm, might have been nothing")
    vague = dict(REDDIT_EXTRACTED, extraction_confidence="low")
    monkeypatch.setattr(pipeline, "fetch_reddit_posts", lambda: [edited])
    monkeypatch.setattr(pipeline, "extract_events", lambda batch, *a: [vague] if batch else [])

    stored = pipeline.run_reddit_cycle()
    assert stored[0]["_is_new"] is False
    assert store.get_active_events(min_confidence=0.4) == []  # hidden from the default view
    event = store.get_event(event_id)
    assert event["confidence"] == 0.2  # 0.2 source + 0.0 extraction — archived, not erased


def test_lines_overlap_corroborates_without_coordinates(tmp_db, monkeypatch):
    # The A6 payoff: no points anywhere, different event_type labels — lines overlap
    # is the anchor. v1 would have missed this entirely.
    reddit_x = dict(REDDIT_EXTRACTED, event_type="delay", lines=["Red"],
                    location_string=None)
    monkeypatch.setattr(pipeline, "fetch_reddit_posts", lambda: [REDDIT_POST])
    monkeypatch.setattr(pipeline, "extract_events",
                        lambda batch, *a: [reddit_x] if batch else [])
    monkeypatch.setattr(pipeline, "resolve_location", _fake_resolve(None))
    reddit_id = pipeline.run_reddit_cycle()[0]["id"]

    cta_x = dict(CTA_EXTRACTED, event_type="incident", lines=["Red"],
                 location_string=None)
    _wire_cta(monkeypatch, [CTA_ALERT], [cta_x], geo=None)
    stored = pipeline.run_cta_cycle()

    assert stored[0]["id"] == reddit_id
    event = store.get_event(reddit_id)
    assert event["verification"] == "confirmed"
    assert event["lines"] == ["Red"]
    assert len(store.get_active_events(min_confidence=0.0)) == 1


def test_reddit_then_metra_yields_unflagged_lead_observation(tmp_db, monkeypatch):
    from datetime import datetime, timezone

    social_published = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    post = dict(REDDIT_POST, created_utc=social_published.timestamp())
    reddit_x = dict(REDDIT_EXTRACTED, lines=["UP-N"], location_string=None)
    monkeypatch.setattr(pipeline, "fetch_reddit_posts", lambda: [post])
    monkeypatch.setattr(pipeline, "extract_events",
                        lambda batch, *a: [reddit_x] if batch else [])
    monkeypatch.setattr(pipeline, "resolve_location", _fake_resolve(None))
    event_id = pipeline.run_reddit_cycle()[0]["id"]

    metra_alert = {"guid": "DevAPI-77", "title": "UP-N delays",
                   "description": "Trains delayed.", "line": "UP-N",
                   "pubDate": "2026-07-02T10:20:00+00:00", "link": "x"}
    metra_x = dict(CTA_EXTRACTED, source_id="DevAPI-77", event_type="delay",
                   lines=["UP-N"], location_string=None)
    monkeypatch.setattr(pipeline, "fetch_metra_alerts", lambda: [metra_alert])
    monkeypatch.setattr(pipeline, "extract_events",
                        lambda batch, *a: [metra_x] if batch else [])
    pipeline.run_metra_cycle()

    event = store.get_event(event_id)
    assert event["first_social_at"] == social_published.isoformat()  # Reddit created_utc
    assert event["official_at"] == "2026-07-02T10:20:00+00:00"       # Metra pubDate
    assert event["latency_flagged"] == 0  # both sides source-published: headline-grade
    lead = (datetime.fromisoformat(event["official_at"])
            - datetime.fromisoformat(event["first_social_at"])).total_seconds()
    assert lead == 1200.0  # the street knew 20 minutes first — the moat, measured


def test_v2_fields_flow_into_the_record(tmp_db, monkeypatch):
    v2_extract = dict(
        CTA_EXTRACTED,
        event_type="accessibility",
        mode="cta_rail",
        lines=["Red"],
        station="Howard",
        severity="minor",
        scope="chronic",
        is_clearance=False,
    )
    _wire_cta(monkeypatch, [CTA_ALERT], [v2_extract])
    stored = pipeline.run_cta_cycle()

    event = store.get_event(stored[0]["id"])
    assert event["mode"] == "cta_rail"
    assert event["lines"] == ["Red"]
    assert event["severity"] == "minor"
    assert event["scope"] == "chronic"  # the verdict board will ignore this one (1.2)


def test_clearance_items_never_create_events(tmp_db, monkeypatch):
    resumed = dict(
        CTA_EXTRACTED,
        summary="Red Line service has resumed near Howard.",
        is_clearance=True,
    )
    _wire_cta(monkeypatch, [CTA_ALERT], [resumed])
    assert pipeline.run_cta_cycle() == []
    assert store.get_active_events(min_confidence=0.0) == []

    # Capture-only: the extraction is archived for /review even though no event exists.
    with store.get_connection() as conn:
        raw = conn.execute("SELECT extraction FROM raw_items").fetchone()
    assert json.loads(raw["extraction"])["is_clearance"] is True


def test_update_can_escalate_severity(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [dict(CTA_EXTRACTED, severity="minor")])
    event_id = pipeline.run_cta_cycle()[0]["id"]

    worse_alert = dict(CTA_ALERT, short_description="Red Line suspended.")
    worse = dict(CTA_EXTRACTED, event_type="suspension", severity="severe",
                 summary="Red Line suspended near Howard.")
    _wire_cta(monkeypatch, [worse_alert], [worse])
    pipeline.run_cta_cycle()

    event = store.get_event(event_id)
    assert event["severity"] == "severe"  # an escalation is often exactly this field


def test_reddit_cycle_is_credential_gated(tmp_db, monkeypatch):
    # Social signal is deferred (decision log 2026-07-02): without credentials the
    # cycle must never run; with them it self-activates. Deferred, not deleted.
    _wire_cta(monkeypatch, [], [])
    monkeypatch.setattr(pipeline, "fetch_metra_alerts", lambda: [])

    def boom():
        raise AssertionError("reddit fetched without credentials")
    monkeypatch.setattr(pipeline, "fetch_reddit_posts", boom)
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    assert pipeline.run_full_cycle() == []

    monkeypatch.setenv("REDDIT_CLIENT_ID", "x")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "y")
    fetched = []
    monkeypatch.setattr(pipeline, "fetch_reddit_posts", lambda: fetched.append(1) or [])
    pipeline.run_full_cycle()
    assert fetched == [1]  # credentials present: the cycle lights up


def test_same_source_type_never_corroborates(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    pipeline.run_cta_cycle()

    second = dict(CTA_ALERT, alert_id="1002")
    second_extracted = dict(CTA_EXTRACTED, source_id="1002")
    _wire_cta(monkeypatch, [second], [second_extracted])
    stored = pipeline.run_cta_cycle()

    assert stored[0]["_is_new"] is True  # two CTA alerts are two events, not corroboration
    assert len(store.get_active_events(min_confidence=0.0)) == 2
