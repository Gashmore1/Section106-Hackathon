"""
Ward attribution from real geography, with no database or model dependency.

Kept separate from `scripts.ingest_real_pdfs` so that provenance tooling can
resolve a ward without importing the ingest pipeline, which opens a database
connection and probes the model endpoint at import time.

A ward is never guessed. A site is placed in a ward only when a point falls inside
that ward's published ONS boundary polygon; anything else returns None and the
caller must say so out loud.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
WARDS_BOUNDARY_FILE = (
    REPO_ROOT / "data" / "fixtures" / "spatial" / "sheffield_28_wards_ons_boundaries.geojson"
)


def point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    """Ray-casting test; ring is a list of [lon, lat] pairs."""
    x, y = point
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _rings_of(geom: dict) -> list[list[list[float]]]:
    kind = geom.get("type")
    if kind == "Polygon":
        return list(geom.get("coordinates") or [])
    if kind == "MultiPolygon":
        return [poly[0] for poly in geom.get("coordinates") or [] if poly]
    return []


def resolve_ward(lat: Optional[float], lon: Optional[float]) -> Optional[str]:
    """
    Resolve a ward deterministically from site coordinates against the real ONS
    ward boundary layer, so attribution comes from geography rather than a guess.
    """
    if lat is None or lon is None or not WARDS_BOUNDARY_FILE.exists():
        return None
    point = (lon, lat)
    for feature in json.loads(WARDS_BOUNDARY_FILE.read_text())["features"]:
        geom = feature.get("geometry") or {}
        if any(point_in_ring(point, ring) for ring in _rings_of(geom)):
            props = feature.get("properties") or {}
            return props.get("WD13NM") or props.get("WD22NM")
    return None
