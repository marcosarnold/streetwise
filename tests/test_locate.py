"""Gazetteer name matching + resolution order (dev-plan 0.4).

The trust rule under test: an ambiguous name resolves to NOTHING, never to a guess —
CTA has four "Western" stations, two of them on the Blue Line alone.
"""

import pytest

from backend import locate

FIXTURE = {
    "lines": {},
    "stations": [
        {"name": "Howard", "agency": "cta", "lat": 42.019, "lng": -87.6729,
         "routes": ["Red", "Purple", "Yellow"]},
        {"name": "Western", "agency": "cta", "lat": 41.9661, "lng": -87.6885,
         "routes": ["Brown"]},
        {"name": "Western", "agency": "cta", "lat": 41.9161, "lng": -87.6873,
         "routes": ["Blue"]},
        {"name": "Western", "agency": "cta", "lat": 41.8754, "lng": -87.6884,
         "routes": ["Blue"]},
        {"name": "Kenosha", "agency": "metra", "lat": 42.5847, "lng": -87.8212,
         "routes": ["UP-N"]},
    ],
}


@pytest.fixture(autouse=True)
def fixture_gazetteer():
    locate.set_gazetteer(FIXTURE)
    yield
    locate._gaz = None  # next production access lazy-loads the real file again
    locate._index = None


def test_exact_and_normalized_names_match():
    assert locate.resolve_station("Howard")["lat"] == 42.019
    # Extractor-style strings: comma tails, suffix noise, parens, case, punctuation.
    assert locate.resolve_station("Howard Station, Chicago, IL")["name"] == "Howard"
    assert locate.resolve_station("Kenosha Metra Station, Kenosha, WI")["name"] == "Kenosha"
    assert locate.resolve_station("HOWARD (Red Line)")["name"] == "Howard"
    assert locate.resolve_station("Nonexistent Stop") is None


def test_ambiguous_names_resolve_to_nothing():
    assert locate.resolve_station("Western") is None                    # four candidates
    assert locate.resolve_station("Western", ["Brown"])["lat"] == 41.9661  # lines disambiguate
    # Two Westerns on the Blue Line — still ambiguous, still nothing. No wrong pins.
    assert locate.resolve_station("Western", ["Blue"]) is None


def test_resolution_order_station_first(monkeypatch):
    def no_nominatim(_):
        raise AssertionError("gazetteer hit must never reach Nominatim")
    monkeypatch.setattr(locate, "geocode", no_nominatim)

    r = locate.resolve_location(None, [], "Howard Station, Chicago, IL")
    assert r["geo_kind"] == "station"
    assert r["station"] == "Howard"                       # canonical gazetteer name
    assert r["location_name"] == "Howard Station, Chicago, IL"  # rider-facing text kept

    # station field (prompt v2) wins over the free-text string.
    r = locate.resolve_location("Kenosha", [], "somewhere vague")
    assert r["station"] == "Kenosha"


def test_line_anchor_skips_nominatim(monkeypatch):
    def no_nominatim(_):
        raise AssertionError("line-anchored events must not spend a Nominatim call")
    monkeypatch.setattr(locate, "geocode", no_nominatim)

    r = locate.resolve_location(None, ["Blue"], None)
    assert r["geo_kind"] == "line"
    assert r["lat"] is None  # a line has no point — the line coloring carries it


def test_point_fallback_and_none(monkeypatch):
    monkeypatch.setattr(locate, "geocode", lambda s: {"lat": 41.9, "lng": -87.7})
    r = locate.resolve_location(None, [], "I-90 near Cicero Ave, Chicago, IL")
    assert r["geo_kind"] == "point" and r["lat"] == 41.9

    monkeypatch.setattr(locate, "geocode", lambda s: None)
    r = locate.resolve_location(None, [], "Some Unresolvable Place")
    assert r["geo_kind"] == "none" and r["lat"] is None  # never a fabricated pin

    assert locate.resolve_location(None, [], None)["geo_kind"] == "none"
