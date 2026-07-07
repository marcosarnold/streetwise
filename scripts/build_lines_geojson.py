#!/usr/bin/env python3
"""Build data/lines.geojson — one MultiLineString per transit line, for the map's
network base layer (dev-plan 1.3b).

Sources (probed live 2026-07-07):
- CTA: the city's "CTA - 'L' (Rail) Lines" dataset (xbyr-jnvx), keyless GeoJSON of
  153 track segments. Segments are shared (the Loop carries up to five lines), so we
  emit each line's full route by parsing the free-text `lines` property — a segment
  appears in every line that rides it, which is how the railroad actually works.
- Metra: shapes.txt inside the public static GTFS zip (same source as the gazetteer).
  Inbound shapes only — outbound traces the same track; branches (ME has three)
  become parts of the line's MultiLineString.

Feature properties are {id, agency} only: names and official colors already live in
the gazetteer and are served by /lines — one source of truth for line identity.

Run: python3 scripts/build_lines_geojson.py   (regenerate on service changes)
"""

import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "lines.geojson"

CTA_LINES_GEOJSON = "https://data.cityofchicago.org/api/geospatial/xbyr-jnvx?method=export&format=GeoJSON"
METRA_GTFS_ZIP = "https://schedules.metrarail.com/gtfs/schedule.zip"

CTA_LINE_IDS = ["Red", "Blue", "Brown", "Green", "Orange", "Pink", "Purple", "Yellow"]
METRA_LINE_IDS = ["UP-N", "UP-NW", "UP-W", "MD-N", "MD-W", "NCS", "BNSF", "HC", "RI", "ME", "SWS"]

COORD_PRECISION = 5  # ~1 m — plenty for a city-scale overview layer


def _round_coords(coords):
    return [[round(lng, COORD_PRECISION), round(lat, COORD_PRECISION)] for lng, lat in coords]


def _segment_lines(prop: str) -> set[str]:
    """'Brown, Green, Orange, Pink, Purple (Exp)' -> {Brown, Green, Orange, Pink, Purple}."""
    cleaned = re.sub(r"\(.*?\)", "", prop)
    found = set()
    for token in cleaned.split(","):
        word = token.strip().split(" ")[0]
        if word in CTA_LINE_IDS:
            found.add(word)
    return found


def build_cta() -> list[dict]:
    src = httpx.get(CTA_LINES_GEOJSON, timeout=60).raise_for_status().json()
    parts: dict[str, list] = {line: [] for line in CTA_LINE_IDS}
    for feature in src["features"]:
        rides = _segment_lines(feature["properties"].get("lines", ""))
        for part in feature["geometry"]["coordinates"]:  # segments are MultiLineString
            for line in rides:
                parts[line].append(_round_coords(part))
    return [
        {"type": "Feature", "properties": {"id": line, "agency": "cta"},
         "geometry": {"type": "MultiLineString", "coordinates": parts[line]}}
        for line in CTA_LINE_IDS if parts[line]
    ]


def build_metra() -> list[dict]:
    r = httpx.get(METRA_GTFS_ZIP, timeout=60,
                  headers={"User-Agent": "Mozilla/5.0"}).raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    by_shape: dict[str, list] = {}
    with zf.open("shapes.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            row = {k.strip(): (v or "").strip() for k, v in row.items()}
            by_shape.setdefault(row["shape_id"], []).append(
                (int(row["shape_pt_sequence"]), float(row["shape_pt_lon"]), float(row["shape_pt_lat"]))
            )
    features = []
    for line in METRA_LINE_IDS:
        branches = []
        for shape_id, pts in sorted(by_shape.items()):
            if shape_id.startswith(f"{line}_IB"):  # inbound only; outbound is the same track
                pts.sort()
                branches.append(_round_coords([[lng, lat] for _, lng, lat in pts]))
        if branches:
            features.append(
                {"type": "Feature", "properties": {"id": line, "agency": "metra"},
                 "geometry": {"type": "MultiLineString", "coordinates": branches}}
            )
    return features


def main():
    cta = build_cta()
    metra = build_metra()
    collection = {
        "type": "FeatureCollection",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "features": cta + metra,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(collection, separators=(",", ":")))
    size_kb = OUT.stat().st_size // 1024
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(cta)} CTA + {len(metra)} Metra lines, {size_kb} KB.")


if __name__ == "__main__":
    main()
