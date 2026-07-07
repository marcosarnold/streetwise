#!/usr/bin/env python3
"""Build data/gazetteer.json — the offline station + line lookup for location resolution.

Sources (probed live 2026-07-02; see docs/architecture.md):
- CTA rail stations: the City of Chicago "List of 'L' Stops" dataset (8pix-ypme) —
  station names, parent ids, per-line booleans, exact coordinates, ~300 rows, no key.
  Chosen over raw CTA GTFS (98 MB zip; the station→route join needs stop_times.txt).
- CTA rail line colors: official brand constants (stable for decades).
- Metra stations + lines: the static GTFS zip at schedules.metrarail.com/gtfs/schedule.zip
  (public, no key — verified 2026-07-06; the METRA_GTFS_API_KEY bearer token is only for
  the realtime feeds at gtfspublic.metrarr.com). ~470 KB with all join tables, so the
  station→line mapping is computed properly (stop_times → trips → routes), and line
  colors come from Metra's own published route_color values.

Run: python3 scripts/build_gazetteer.py   (regenerate quarterly / on service changes)
"""

import csv
import io
import json
import zipfile
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

METRA_GTFS_ZIP = "https://schedules.metrarail.com/gtfs/schedule.zip"

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


def _read_gtfs_csv(zf: zipfile.ZipFile, name: str) -> list[dict]:
    # Metra's GTFS pads fields with spaces after every comma; strip keys and values.
    with zf.open(name) as f:
        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()}
                for row in reader]


def build_metra() -> tuple[list[dict], list[dict]]:
    """Return (lines, stations) from Metra's public static GTFS zip."""
    r = httpx.get(METRA_GTFS_ZIP, timeout=60,
                  headers={"User-Agent": "Mozilla/5.0"}).raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))

    lines = sorted(
        ({"id": rt["route_id"], "name": rt["route_long_name"],
          "color": f"#{rt['route_color']}" if rt.get("route_color") else None}
         for rt in _read_gtfs_csv(zf, "routes.txt")),
        key=lambda ln: ln["id"])

    trip_route = {t["trip_id"]: t["route_id"] for t in _read_gtfs_csv(zf, "trips.txt")}
    stop_routes: dict[str, set[str]] = {}
    for st in _read_gtfs_csv(zf, "stop_times.txt"):
        route = trip_route.get(st["trip_id"])
        if route:
            stop_routes.setdefault(st["stop_id"], set()).add(route)

    stations = sorted(
        ({"name": stop["stop_name"], "agency": "metra",
          "lat": float(stop["stop_lat"]), "lng": float(stop["stop_lon"]),
          "routes": sorted(stop_routes.get(stop["stop_id"], set()))}
         for stop in _read_gtfs_csv(zf, "stops.txt")),
        key=lambda s: s["name"])
    return lines, stations


def main():
    cta = build_cta_stations()
    metra_lines, metra = build_metra()

    gazetteer = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "lines": {"cta_rail": CTA_RAIL_LINES, "metra": metra_lines},
        "stations": cta + metra,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(gazetteer, indent=1))
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(cta)} CTA stations, "
          f"{len(metra)} Metra stations, "
          f"{len(CTA_RAIL_LINES) + len(metra_lines)} lines.")


if __name__ == "__main__":
    main()
