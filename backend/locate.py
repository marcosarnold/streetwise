"""Location resolution: gazetteer first, Nominatim fallback, or nothing (dev-plan 0.4).

Resolution order (docs/architecture.md — certainty over precision):
  1. station — the name joins the gazetteer: exact coordinates, instant, offline.
  2. line   — no station but the event names known route(s): anchors to the line,
              deliberately no point (the verdict board / line coloring carries it).
  3. point  — free text via Nominatim (rare post-gazetteer; rate-limited, weak at
              intersections — verified in the 0.0 preflight).
  4. none   — list-only. Never a fabricated pin.

An ambiguous name resolves to NOTHING, not to a guess: CTA has four "Western" stations
(and two on the Blue Line alone). A name that stays ambiguous after filtering by the
event's lines falls through to the next tier — the no-fake-pins rule applied to names.
"""

import json
import re
from pathlib import Path

from backend.geocoder import geocode

GAZETTEER_PATH = Path(__file__).resolve().parent.parent / "data" / "gazetteer.json"

_gaz: dict | None = None
_index: dict[str, list[dict]] | None = None

# Noise tokens stripped during normalization — "Howard Station", "Kenosha Metra
# Station", "Clybourn stop" all reduce to the bare gazetteer name.
_STRIP_TOKENS = {"station", "metra", "cta", "stop", "the"}


def set_gazetteer(data: dict) -> None:
    """Install a gazetteer (tests inject fixtures; production lazy-loads the file)."""
    global _gaz, _index
    _gaz = data
    _index = {}
    for st in data.get("stations", []):
        _index.setdefault(_norm(st["name"]), []).append(st)


def get_lines() -> dict:
    """The gazetteer's line metadata (ids, names, official colors), for /lines."""
    _ensure_loaded()
    return _gaz.get("lines", {})


def _ensure_loaded() -> None:
    if _gaz is not None:
        return
    try:
        set_gazetteer(json.loads(GAZETTEER_PATH.read_text()))
    except (OSError, json.JSONDecodeError):
        # Missing/corrupt gazetteer degrades to the Nominatim path — never a crash.
        set_gazetteer({"stations": [], "lines": {}})


def _norm(name: str) -> str:
    """'Kenosha Metra Station, Kenosha, WI' -> 'kenosha'; '18th (Pink Line)' -> '18th'."""
    s = name.split(",")[0].lower()
    s = re.sub(r"\(.*?\)", " ", s)         # parenthetical qualifiers
    s = re.sub(r"[^a-z0-9]+", " ", s)      # all punctuation to spaces
    return " ".join(t for t in s.split() if t not in _STRIP_TOKENS)


def resolve_station(name: str, lines: list[str] | None = None) -> dict | None:
    """Match a name to exactly one gazetteer station, or None. `lines` (the event's
    route ids) disambiguates duplicates; still-ambiguous names return None."""
    _ensure_loaded()
    candidates = _index.get(_norm(name), []) if name else []
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1 and lines:
        filtered = [c for c in candidates if set(c["routes"]) & set(lines)]
        if len(filtered) == 1:
            return filtered[0]
    return None


def resolve_location(station: str | None, lines: list[str] | None,
                     location_string: str | None) -> dict:
    """Resolve an extraction's location fields to {geo_kind, lat, lng, station,
    location_name}. The location_string is also tried against the gazetteer — the
    extractor often writes 'Howard Station, Chicago, IL' rather than a station field
    (always, until prompt v2 lands in 0.5)."""
    lines = lines or []

    for name in (station, location_string):
        if name:
            hit = resolve_station(name, lines)
            if hit:
                return {"geo_kind": "station", "lat": hit["lat"], "lng": hit["lng"],
                        "station": hit["name"],
                        "location_name": location_string or hit["name"]}

    if lines:
        return {"geo_kind": "line", "lat": None, "lng": None, "station": None,
                "location_name": location_string}

    if location_string:
        geo = geocode(location_string)
        if geo:
            return {"geo_kind": "point", "lat": geo["lat"], "lng": geo["lng"],
                    "station": None, "location_name": location_string}

    return {"geo_kind": "none", "lat": None, "lng": None, "station": None,
            "location_name": location_string}
