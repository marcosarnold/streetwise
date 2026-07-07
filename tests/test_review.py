"""/review eval layer (0.7): verdict storage, queue filtering, criterion stats."""

import json

import pytest

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


def _raw(source_id: str, extraction: dict | None = None):
    store.archive_raw_item("cta", source_id, f"hash-{source_id}",
                           json.dumps({"headline": f"alert {source_id}"}),
                           "2026-07-01T12:00:00+00:00")
    if extraction is not None:
        store.update_raw_extraction("cta", source_id, f"hash-{source_id}",
                                    json.dumps(extraction))


def test_verdict_roundtrip_and_queue_filter(tmp_db):
    _raw("a", {"summary": "thing"})
    _raw("b")  # no extraction — the "correctly ignored?" case

    queue = store.get_review_items()
    assert len(queue) == 2
    assert queue[0]["payload"] == {"headline": "alert b"} or queue[1]["payload"] == {"headline": "alert b"}
    no_event = next(i for i in queue if i["source_id"] == "b")
    assert no_event["extraction"] is None  # rendered as "grade the decision to ignore"

    graded = next(i for i in queue if i["source_id"] == "a")
    assert store.set_review(graded["id"], "correct") is True

    remaining = store.get_review_items()
    assert [i["source_id"] for i in remaining] == ["b"]
    everything = store.get_review_items(only_unreviewed=False)
    assert len(everything) == 2

    # Re-judging overwrites — an eval that can't correct itself isn't one.
    assert store.set_review(graded["id"], "wrong_summary") is True
    assert next(i for i in store.get_review_items(only_unreviewed=False)
                if i["id"] == graded["id"])["review"] == "wrong_summary"


def test_verdict_vocabulary_is_enforced(tmp_db):
    _raw("a")
    item = store.get_review_items()[0]
    with pytest.raises(ValueError):
        store.set_review(item["id"], "meh")
    assert store.set_review(999_999, "correct") is False  # unknown id → not found


def test_stats_measure_the_prd_criteria(tmp_db):
    # Three graded items: 2 correct, 1 wrong_event → accuracy 2/3.
    for sid, verdict in [("a", "correct"), ("b", "correct"), ("c", "wrong_event")]:
        _raw(sid)
        store.set_review(store.get_review_items()[0]["id"], verdict)
    _raw("d")  # ungraded

    # Events: station + line resolved (gazetteer), one unresolved → rate 2/3;
    # distinct score components → a discriminating histogram (criterion 6);
    # one real clearance with a 40-minute duration; one unflagged latency row.
    store.upsert_event(_event(id="e1", geo_kind="station", lat=41.9, lng=-87.6,
                              cleared_at="2026-07-01T12:40:00+00:00",
                              official_at="2026-07-01T11:58:00+00:00"))
    store.upsert_event(_event(id="e2", geo_kind="line", score_corrob=0.4,
                              verification="confirmed"))
    store.upsert_event(_event(id="e3", geo_kind="none", score_source=0.2,
                              verification="reported", scope="chronic"))

    stats = store.review_stats()

    assert stats["review"]["reviewed"] == 3
    assert stats["review"]["unreviewed"] == 1
    assert stats["review"]["accuracy"] == pytest.approx(0.667, abs=1e-3)
    assert stats["review"]["verdicts"] == {"correct": 2, "wrong_event": 1}

    assert stats["location"]["gazetteer_rate"] == pytest.approx(0.667, abs=1e-3)
    assert stats["location"]["geo_kinds"] == {"station": 1, "line": 1, "none": 1}

    assert stats["latency"]["measurable_events"] == 1
    assert stats["latency"]["median_seconds"] == 120  # published 11:58, detected 12:00

    assert stats["lifecycle"]["cleared"] == 1
    assert stats["lifecycle"]["expired"] == 0
    assert stats["lifecycle"]["median_duration_minutes"] == 40

    assert len(stats["scores"]["histogram"]) == 3  # 0.5, 0.7, 1.1 — not one value
    assert stats["scopes"] == {"acute": 2, "chronic": 1}


def test_stats_on_empty_db_report_none_not_crash(tmp_db):
    stats = store.review_stats()
    assert stats["review"]["accuracy"] is None
    assert stats["location"]["gazetteer_rate"] is None
    assert stats["latency"]["median_seconds"] is None
    assert stats["lifecycle"]["median_duration_minutes"] is None
