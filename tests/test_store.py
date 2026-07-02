"""Store contracts: migrations, delete-nothing, hydration, source links, raw archive."""

from backend import store


def _event(**overrides) -> dict:
    base = {
        "id": "evt-1",
        "event_type": "delay",
        "verification": "confirmed",
        "summary": "Red Line delays near Howard.",
        "score_source": 0.4,
        "score_extraction": 0.3,
        "score_corrob": 0.0,
        "detected_at": "2026-07-01T12:00:00+00:00",
        "updated_at": "2026-07-01T12:00:00+00:00",
        "lines": ["Red"],
        "geo_kind": "none",
    }
    base.update(overrides)
    return base


def test_migrations_are_idempotent(tmp_db):
    store.init_db()  # second run must be a no-op, not an error
    with store.get_connection() as conn:
        versions = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == ["001_schema_v2.sql"]


def test_event_roundtrip_hydrates_lines_and_sources(tmp_db):
    store.upsert_event(_event())
    store.link_source("evt-1", "cta", "12345", "2026-07-01T12:00:00+00:00", "hash-a")

    event = store.get_event("evt-1")
    assert event["lines"] == ["Red"]
    assert event["confidence"] == 0.7  # sum of components, computed at read
    assert event["sources"] == [{
        "type": "cta", "id": "12345",
        "first_seen_at": "2026-07-01T12:00:00+00:00", "published_at": None,
    }]


def test_relink_preserves_first_seen_and_updates_last_seen(tmp_db):
    store.upsert_event(_event())
    store.link_source("evt-1", "cta", "12345", "2026-07-01T12:00:00+00:00", "hash-a")
    store.link_source("evt-1", "cta", "12345", "2026-07-01T12:05:00+00:00", "hash-b")

    with store.get_connection() as conn:
        row = conn.execute("SELECT * FROM event_sources").fetchone()
    assert row["first_seen_at"] == "2026-07-01T12:00:00+00:00"
    assert row["last_seen_at"] == "2026-07-01T12:05:00+00:00"
    assert row["last_hash"] == "hash-b"


def test_touch_sources_seen_advances_last_seen_only(tmp_db):
    store.upsert_event(_event())
    store.link_source("evt-1", "cta", "12345", "2026-07-01T12:00:00+00:00", "hash-a")
    store.touch_sources_seen("cta", ["12345"], "2026-07-01T12:10:00+00:00")
    store.touch_sources_seen("metra", ["12345"], "2026-07-01T12:20:00+00:00")  # wrong type: no-op

    with store.get_connection() as conn:
        row = conn.execute("SELECT * FROM event_sources").fetchone()
    assert row["last_seen_at"] == "2026-07-01T12:10:00+00:00"


def test_active_excludes_cleared_but_keeps_the_row(tmp_db):
    store.upsert_event(_event())
    store.upsert_event(_event(id="evt-2", cleared_at="2026-07-01T13:00:00+00:00"))

    active = store.get_active_events(min_confidence=0.0)
    assert [e["id"] for e in active] == ["evt-1"]
    # Nothing is deleted: the cleared row is still in the archive.
    assert store.get_event("evt-2") is not None


def test_min_confidence_filters_on_component_sum(tmp_db):
    store.upsert_event(_event())  # 0.7
    store.upsert_event(_event(id="evt-low", score_source=0.2, score_extraction=0.3,
                              verification="reported"))  # 0.5
    assert {e["id"] for e in store.get_active_events(min_confidence=0.6)} == {"evt-1"}
    assert {e["id"] for e in store.get_active_events(min_confidence=0.4)} == {"evt-1", "evt-low"}


def test_raw_archive_dedupes_by_content_and_keeps_history(tmp_db):
    assert store.archive_raw_item("cta", "a1", "hash-a", "{}", "t0") is True
    assert store.archive_raw_item("cta", "a1", "hash-a", "{}", "t1") is False  # same content
    assert store.archive_raw_item("cta", "a1", "hash-b", "{}", "t2") is True   # changed content

    with store.get_connection() as conn:
        count = conn.execute("SELECT count(*) AS n FROM raw_items").fetchone()["n"]
    assert count == 2  # history accumulates, nothing overwritten


def test_known_source_ids_is_per_type(tmp_db):
    store.upsert_event(_event())
    store.link_source("evt-1", "cta", "42", "t0", "h")
    assert store.known_source_ids("cta") == {"42"}
    assert store.known_source_ids("reddit") == set()  # same id, different feed: distinct
