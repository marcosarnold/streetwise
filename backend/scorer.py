"""Confidence scoring logic."""

import math
from datetime import datetime, timezone

SOURCE_SCORES = {
    "cta": 0.4,
    "metra": 0.4,
    "reddit": 0.2,
}

EXTRACTION_SCORES = {
    "high": 0.3,
    "medium": 0.15,
    "low": 0.0,
}

CORROBORATION_SCORE = 0.4

CORROBORATION_DISTANCE_METERS = 500
CORROBORATION_WINDOW_MINUTES = 30


def source_score(source_type: str) -> float:
    return SOURCE_SCORES.get(source_type, 0.0)


def extraction_score(extraction_confidence: str) -> float:
    return EXTRACTION_SCORES.get(extraction_confidence, 0.0)


def recency_score(detected_at: str, now: datetime | None = None) -> float:
    """Score based on how recently the signal was detected.

    detected_at: ISO 8601 timestamp string.
    """
    now = now or datetime.now(timezone.utc)
    detected = datetime.fromisoformat(detected_at)
    if detected.tzinfo is None:
        detected = detected.replace(tzinfo=timezone.utc)

    age_minutes = (now - detected).total_seconds() / 60

    if age_minutes < 15:
        return 0.05
    if age_minutes <= 60:
        return 0.02
    return 0.0


def score_event(
    source_type: str,
    extraction_confidence: str,
    detected_at: str,
    corroborated: bool = False,
    now: datetime | None = None,
) -> float:
    """Compute the overall confidence score for an event (0.0 to 1.0)."""
    total = (
        source_score(source_type)
        + extraction_score(extraction_confidence)
        + (CORROBORATION_SCORE if corroborated else 0.0)
        + recency_score(detected_at, now)
    )
    return min(total, 1.0)


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points in meters."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def are_corroborating(event_a: dict, event_b: dict) -> bool:
    """Do two events describe the same incident? (Matcher amended 2026-07-01 review, A6.)

    Both events are dicts with keys: lines, station, lat, lng, detected_at.
    Rule: within the time window AND sharing any anchor — overlapping lines, the same
    station, or points within 500 m. Deliberately NOT required: equal event_type (a
    derailment arrives from CTA as "incident" and from Reddit as "delay" — demanding
    equal labels starves the corroboration and latency datasets) and source-type
    disjointness (the pipeline enforces that from event_sources, the explicit record).
    """
    time_a = datetime.fromisoformat(event_a["detected_at"])
    time_b = datetime.fromisoformat(event_b["detected_at"])
    if time_a.tzinfo is None:
        time_a = time_a.replace(tzinfo=timezone.utc)
    if time_b.tzinfo is None:
        time_b = time_b.replace(tzinfo=timezone.utc)
    if abs((time_a - time_b).total_seconds()) / 60 > CORROBORATION_WINDOW_MINUTES:
        return False

    if set(event_a.get("lines") or []) & set(event_b.get("lines") or []):
        return True
    if event_a.get("station") and event_a.get("station") == event_b.get("station"):
        return True
    if None not in (event_a.get("lat"), event_a.get("lng"),
                    event_b.get("lat"), event_b.get("lng")):
        return haversine_meters(
            event_a["lat"], event_a["lng"], event_b["lat"], event_b["lng"]
        ) <= CORROBORATION_DISTANCE_METERS
    return False


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    recent = now.isoformat()

    cta_score = score_event("cta", "high", recent, corroborated=False, now=now)
    reddit_score = score_event("reddit", "high", recent, corroborated=False, now=now)

    print(f"CTA event (high extraction, no corroboration, recent): {cta_score}")
    print(f"Reddit event (high extraction, no corroboration, recent): {reddit_score}")

    assert cta_score >= 0.6, "CTA event should score >= 0.6"
    assert reddit_score <= 0.55, "Solo Reddit post should score <= 0.55"
    print("OK")
