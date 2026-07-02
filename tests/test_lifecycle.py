"""Lifecycle decisions under frozen clocks: the code the product's honesty rests on.

Cleared = evidence (official alert vanished from a successfully polled feed).
Expired = age (reported-only event; no signal exists, so no duration is claimed).
A dead feed must be able to clear NOTHING.
"""

from datetime import datetime, timezone

import pytest

from backend import lifecycle, pipeline, store
from tests.test_pipeline import CTA_ALERT, CTA_EXTRACTED, _wire_cta

T0 = "2026-07-02T10:00:00+00:00"
T1 = "2026-07-02T10:05:00+00:00"
T2 = "2026-07-02T10:10:00+00:00"


# ---------------------------------------------------------------- pure decisions

def test_is_vanished_needs_two_polls_strictly_after_last_seen():
    assert lifecycle.is_vanished(T0, []) is False                 # feed down: no polls
    assert lifecycle.is_vanished(T0, [T1]) is False               # one poll: not yet
    assert lifecycle.is_vanished(T0, [T2, T1]) is True            # two polls: vanished
    assert lifecycle.is_vanished(T0, [T0, T0]) is False           # same-cycle polls don't count


def _evt(sources, updated_at=T0):
    return {"id": "e", "sources": sources, "updated_at": updated_at}


def test_should_clear_anchors_on_official_sources_only():
    polls = {"cta": [T2, T1], "metra": []}
    vanished_cta = {"type": "cta", "id": "1", "last_seen_at": T0}
    lingering_metra = {"type": "metra", "id": "2", "last_seen_at": T0}
    stale_reddit = {"type": "reddit", "id": "r", "last_seen_at": T0}

    assert lifecycle.should_clear(_evt([vanished_cta]), polls) is True
    # A lingering Reddit source can't hold an official clearance open…
    assert lifecycle.should_clear(_evt([vanished_cta, stale_reddit]), polls) is True
    # …and a Reddit-only event can never be "cleared" — there is no signal to detect.
    assert lifecycle.should_clear(_evt([stale_reddit]), polls) is False
    # Metra's feed has no successful polls (down) — its source can't be proven vanished.
    assert lifecycle.should_clear(_evt([vanished_cta, lingering_metra]), polls) is False


def test_should_expire_is_reported_only_and_ttl_gated():
    now = datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc)  # T0 + 4h
    reddit = {"type": "reddit", "id": "r", "last_seen_at": T0}
    official = {"type": "cta", "id": "1", "last_seen_at": T0}

    assert lifecycle.should_expire(_evt([reddit], updated_at=T0), now) is True
    fresh = "2026-07-02T13:00:00+00:00"  # 1h old < 3h TTL
    assert lifecycle.should_expire(_evt([reddit], updated_at=fresh), now) is False
    # An official anchor means vanish-detection owns the ending, never the TTL.
    assert lifecycle.should_expire(_evt([reddit, official], updated_at=T0), now) is False


# ---------------------------------------------------------------- integration

def test_vanished_alert_clears_after_two_successful_polls(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    event_id = pipeline.run_cta_cycle()[0]["id"]

    _wire_cta(monkeypatch, [], [])  # the alert is gone from the feed
    pipeline.run_cta_cycle()
    assert lifecycle.sweep()["cleared"] == []  # one successful poll: not proven yet

    pipeline.run_cta_cycle()
    swept = lifecycle.sweep()
    assert [e["id"] for e in swept["cleared"]] == [event_id]
    assert swept["cleared"][0]["cleared_at"] is not None  # duration data exists now

    # Out of the live view, forever in the archive.
    assert store.get_active_events(min_confidence=0.0) == []
    assert store.get_event(event_id) is not None


def test_dead_feed_clears_nothing(tmp_db, monkeypatch):
    _wire_cta(monkeypatch, [CTA_ALERT], [CTA_EXTRACTED])
    pipeline.run_cta_cycle()

    def boom():
        raise RuntimeError("CloudFront 403")

    monkeypatch.setattr(pipeline, "fetch_cta_alerts", boom)
    for _ in range(5):
        with pytest.raises(RuntimeError):
            pipeline.run_cta_cycle()

    # Five failed cycles logged zero polls — the alert's absence proves nothing.
    assert lifecycle.sweep()["cleared"] == []
    assert len(store.get_active_events(min_confidence=0.0)) == 1


def test_expired_reported_event_leaves_view_without_clearance_claim(tmp_db):
    store.upsert_event({
        "id": "evt-r", "event_type": "delay", "verification": "reported",
        "summary": "Rider report.", "score_source": 0.2, "score_extraction": 0.3,
        "detected_at": T0, "updated_at": T0, "lines": [],
    })
    store.link_source("evt-r", "reddit", "abc", T0, "h")

    swept = lifecycle.sweep(now=datetime(2026, 7, 2, 14, 0, tzinfo=timezone.utc))
    assert [e["id"] for e in swept["expired"]] == ["evt-r"]

    event = store.get_event("evt-r")
    assert event["expired_at"] is not None
    assert event["cleared_at"] is None  # expiry never fabricates a duration
    assert store.get_active_events(min_confidence=0.0) == []
