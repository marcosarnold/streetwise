"""Nominatim geocoding wrapper."""

import re
import time

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "streetwise/1.0 (mobility intelligence MVP; contact: streetwise@example.com)"

CHICAGO_CENTER = (41.8781, -87.6298)

# Words that make a location string harder for Nominatim to match.
_DIRECTIONAL_RE = re.compile(
    r"\b(north|south|east|west)(bound)?\b", re.IGNORECASE
)
_NEAR_RE = re.compile(r"\bnear\b", re.IGNORECASE)


def geocode(location_string: str) -> dict:
    """Resolve a location string to lat/lng via Nominatim.

    Returns a dict with lat, lng, and geocode_failed.
    """
    result = _query_nominatim(location_string)
    if result is None:
        simplified = _simplify(location_string)
        if simplified != location_string:
            result = _query_nominatim(simplified)

    if result is None:
        lat, lng = CHICAGO_CENTER
        return {"lat": lat, "lng": lng, "geocode_failed": True}

    return {"lat": result["lat"], "lng": result["lng"], "geocode_failed": False}


def _query_nominatim(location_string: str) -> dict | None:
    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={"q": location_string, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    finally:
        time.sleep(1)  # Nominatim rate limit: 1 req/sec

    results = response.json()
    if not results:
        return None

    return {"lat": float(results[0]["lat"]), "lng": float(results[0]["lon"])}


def _simplify(location_string: str) -> str:
    """Strip directional/qualifier words that often confuse Nominatim."""
    simplified = _DIRECTIONAL_RE.sub("", location_string)
    simplified = _NEAR_RE.sub("", simplified)
    simplified = re.sub(r"\s+", " ", simplified).strip(" ,")
    return simplified


if __name__ == "__main__":
    examples = [
        "I-90 westbound near Cicero Ave, Chicago, IL",
        "Clark St between Madison St and Monroe St, Chicago, IL",
        "Some Totally Made Up Place That Does Not Exist, Chicago, IL",
    ]
    for loc in examples:
        print(loc, "->", geocode(loc))
