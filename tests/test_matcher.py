"""Corroboration matcher (A6): time window + any anchor — lines, station, proximity.

Deliberately absent: event_type equality (different sources label the same incident
differently) and source-type checks (the pipeline owns those via event_sources).
"""

from backend.scorer import are_corroborating

T = "2026-07-02T10:00:00+00:00"
T_25MIN = "2026-07-02T10:25:00+00:00"
T_31MIN = "2026-07-02T10:31:00+00:00"


def _e(**kw):
    base = {"lines": [], "station": None, "lat": None, "lng": None, "detected_at": T}
    base.update(kw)
    return base


def test_lines_overlap_is_an_anchor_regardless_of_labels():
    # A derailment: CTA says "incident", Reddit says "delay" — labels don't matter.
    assert are_corroborating(_e(lines=["Red"]), _e(lines=["Red", "Purple"],
                                                   detected_at=T_25MIN)) is True
    assert are_corroborating(_e(lines=["Red"]), _e(lines=["Blue"])) is False


def test_station_and_proximity_anchors():
    assert are_corroborating(_e(station="Howard"), _e(station="Howard")) is True
    assert are_corroborating(_e(station="Howard"), _e(station="Belmont")) is False
    # ~444 m apart (0.004° lat) corroborates; ~666 m (0.006°) does not.
    a = _e(lat=41.9000, lng=-87.6500)
    assert are_corroborating(a, _e(lat=41.9040, lng=-87.6500)) is True
    assert are_corroborating(a, _e(lat=41.9060, lng=-87.6500)) is False


def test_time_gate_and_no_anchor():
    assert are_corroborating(_e(lines=["Red"]),
                             _e(lines=["Red"], detected_at=T_31MIN)) is False
    assert are_corroborating(_e(), _e()) is False          # nothing shared, nothing claimed
    assert are_corroborating(_e(lat=41.9, lng=-87.65), _e()) is False  # one-sided point
