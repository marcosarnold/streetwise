#!/usr/bin/env python3
"""Build data/gazetteer.json — the offline station + line lookup for location resolution.

Sources (probed live 2026-07-02; see docs/architecture.md):
- CTA rail stations: the City of Chicago "List of 'L' Stops" dataset (8pix-ypme) —
  station names, parent ids, per-line booleans, exact coordinates, ~300 rows, no key.
  Chosen over raw CTA GTFS (98 MB zip; the station→route join needs stop_times.txt).
- CTA rail line colors: official brand constants (stable for decades).
- Metra stations: Metra's GTFS API requires developer credentials. When
  METRA_GTFS_ACCESS_KEY / METRA_GTFS_SECRET_KEY are set (register at
  metra.com/developers), stations are fetched and included; otherwise the build warns
  and ships CTA-only — Metra alerts fall back to Nominatim until then.
- Metra line ids/names are public constants and are always included (color deliberately
  null until verified against Metra brand assets — honesty over completeness).

Run: python3 scripts/build_gazetteer.py   (regenerate quarterly / on service changes)
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "gazetteer.json"

CTA_L_STOPS = "https://data.cityofchicago.org/resource/8pix-ypme.json?$limit=2000"

# Official CTA brand colors — the design system's semantic primitives (dev-plan 1.1).
CTA_RAIL_LINES = [
    {"id": "Red", "name": "Red Line", "color": "#C60C30"},
    {"id": "Blue", "name": "Blue Line", "color": "#00A1DE"},
    {"id": "Brown", "name": "Brown Line", "color": "#62361B"},
    {"id": "Green", "name": "Green Line", "color": "#009B3A"},
    {"id": "Orange", "name": "Orange Line", "color": "#F9461C"},
    {"id": "Pink", "name": "Pink Line", "color": "#E27EA6"},
    {"id": "Purple", "name": "Purple Line", "color": "#522398"},
    {"id": "Yellow", "name": "Yellow Line", "color": "#F9E300"},
]

METRA_LINES = [
    {"id": "UP-N", "name": "Union Pacific North", "color": None},
    {"id": "UP-NW", "name": "Union Pacific Northwest", "color": None},
    {"id": "UP-W", "name": "Union Pacific West", "color": None},
    {"id": "MD-N", "name": "Milwaukee District North", "color": None},
    {"id": "MD-W", "name": "Milwaukee District West", "color": None},
    {"id": "NCS", "name": "North Central Service", "color": None},
    {"id": "BNSF", "name": "BNSF Railway", "color": None},
    {"id": "HC", "name": "Heritage Corridor", "color": None},
    {"id": "RI", "name": "Rock Island District", "color": None},
    {"id": "ME", "name": "Metra Electric", "color": None},
    {"id": "SWS", "name": "SouthWest Service", "color": None},
]

# L-stops dataset boolean field -> route id.
_LINE_FIELDS = {"red": "Red", "blue": "Blue", "g": "Green", "brn": "Brown",
                "p": "Purple", "y": "Yellow", "pnk": "Pink", "o": "Orange"}


def build_cta_stations() -> list[dict]:
    rows = httpx.get(CTA_L_STOPS, timeout=30).raise_for_status().json()
    stations: dict[str, dict] = {}  # keyed by map_id = the parent station
    for row in rows:
        map_id = row.get("map_id")
        loc = row.get("location") or {}
        if not map_id or not loc.get("latitude"):
            continue
        st = stations.setdefault(map_id, {
            "name": row["station_name"],
            "agency": "cta",
            "lat": float(loc["latitude"]),
            "lng": float(loc["longitude"]),
            "routes": [],
        })
        for field, route in _LINE_FIELDS.items():
            if row.get(field) and route not in st["routes"]:
                st["routes"].append(route)
    return sorted(stations.values(), key=lambda s: s["name"])


def build_metra_stations() -> list[dict]:
    key = os.environ.get("METRA_GTFS_ACCESS_KEY", "").strip()
    secret = os.environ.get("METRA_GTFS_SECRET_KEY", "").strip()
    if not key or not secret:
        print("WARN: METRA_GTFS_ACCESS_KEY/SECRET_KEY not set — building without Metra "
              "stations (Metra alerts fall back to Nominatim). Register at "
              "metra.com/developers, add the keys to .env, and rebuild.",
              file=sys.stderr)
        return []
    r = httpx.get("https://gtfsapi.metra.com/gtfs/schedule/stops",
                  auth=(key, secret), timeout=30).raise_for_status()
    out = []
    for stop in r.json():
        out.append({
            "name": stop["stop_name"],
            "agency": "metra",
            "lat": float(stop["stop_lat"]),
            "lng": float(stop["stop_lon"]),
            # Per-station route mapping needs the stop_times join; empty routes just
            # means Metra names skip line-based disambiguation (rarely needed — Metra
            # station names are far less duplicated than CTA's).
            "routes": [],
        })
    return sorted(out, key=lambda s: s["name"])


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    cta = build_cta_stations()
    metra = build_metra_stations()

    gazetteer = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "lines": {"cta_rail": CTA_RAIL_LINES, "metra": METRA_LINES},
        "stations": cta + metra,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(gazetteer, indent=1))
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(cta)} CTA stations, "
          f"{len(metra)} Metra stations, "
          f"{len(CTA_RAIL_LINES) + len(METRA_LINES)} lines.")


if __name__ == "__main__":
    main()
