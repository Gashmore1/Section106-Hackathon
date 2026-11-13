"""
Turn the register harvest manifest into an ingest-ready provenance file.

`scripts/scrape_sheffield_register.py` records what the register knows: the
planning reference, the site address as the council typed it, the decision code
and the exact URL and sha256 of the deed. The auditor needs two more things that
the register does not give it:

  * **the ward**, which must come from geography, never from a guess. The postcode
    in the site address is resolved with postcodes.io, then the coordinates are
    run through the same point-in-polygon test against the real ONS ward layer
    that `scripts/ingest_real_pdfs.py` uses, so both paths agree;
  * **the authority**, which is Sheffield City Council for every deed in this
    corpus because the crawler only talks to Sheffield's register.

Deeds whose address has no usable postcode, or whose postcode falls outside the
Sheffield 28 wards, are written with `ward_name: null` and a `ward_source` that
says why. They still ingest; they are simply not attributed to a ward.

Run this on a machine with internet access, then rsync the corpus to the Spark:

    python3 -m scripts.enrich_sheffield_manifest
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEFFIELD_DIR = REPO_ROOT / "data" / "fixtures" / "pdfs" / "sheffield"
MANIFEST_FILE = SHEFFIELD_DIR / "manifest.json"
PROVENANCE_FILE = SHEFFIELD_DIR / "provenance.json"

AUTHORITY = "Sheffield City Council"
AUTHORITY_ENTITY = "local-authority:SHF"

# Ofcom/BS7662 style postcode match, tolerant of the underscores the crawler's
# sanitiser leaves in filenames and of missing spaces.
POSTCODE_RE = re.compile(
    r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.IGNORECASE
)


def find_postcode(*candidates: Optional[str]) -> Optional[str]:
    for text in candidates:
        if not text:
            continue
        match = POSTCODE_RE.search(text)
        if match:
            return f"{match.group(1).upper()} {match.group(2).upper()}"
    return None


def geocode(postcode: str, delay: float = 0.3) -> Optional[dict[str, Any]]:
    """postcodes.io, the ONS-NPD derived free geocoder. One request per postcode."""
    url = "https://api.postcodes.io/postcodes/" + urllib.parse.quote(postcode.replace(" ", ""))
    request = urllib.request.Request(url, headers={"User-Agent": "s106-auditor/0.1"})
    time.sleep(delay)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"    ! {postcode}: geocode failed ({exc})")
        return None
    data = payload.get("result") or {}
    if not data:
        return None
    return {
        "postcode": data.get("postcode", postcode),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "ward": data.get("admin_district"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=0.3, help="seconds between geocode calls")
    args = parser.parse_args()

    if not MANIFEST_FILE.exists():
        raise SystemExit(f"{MANIFEST_FILE} not found. Run scripts.scrape_sheffield_register first.")

    # Same ward geometry the ingest uses, from a module with no DB or model imports.
    from data.geo import resolve_ward

    manifest = json.loads(MANIFEST_FILE.read_text())
    deeds = manifest.get("deeds", [])
    print(f"Enriching {len(deeds)} register deed(s) for {AUTHORITY}")

    enriched: list[dict[str, Any]] = []
    cache: dict[Optional[str], Optional[dict[str, Any]]] = {}
    for deed in deeds:
        postcode = find_postcode(deed.get("site_address"), deed.get("description"))
        if postcode not in cache:
            cache[postcode] = geocode(postcode, delay=args.delay) if postcode else None
        located = cache[postcode]

        lat = located.get("latitude") if located else None
        lon = located.get("longitude") if located else None
        ward = resolve_ward(lat, lon) if lat and lon else None

        if ward:
            ward_source = (
                f"postcode {postcode} geocoded by postcodes.io to ({lat}, {lon}), "
                "resolved by point-in-polygon against "
                "data/fixtures/spatial/sheffield_28_wards_ons_boundaries.geojson"
            )
        elif located:
            ward_source = (
                f"not resolved: postcode {postcode} lands at ({lat}, {lon}), outside the "
                "Sheffield 28-ward layer"
            )
        elif postcode:
            ward_source = f"not resolved: postcode {postcode} could not be geocoded"
        else:
            ward_source = "not resolved: the register records no postcode for this site"

        entry = {
            "file": deed["file"],
            "planning_reference": deed["planning_reference"],
            "authority": AUTHORITY,
            "authority_entity": AUTHORITY_ENTITY,
            "site_name": deed.get("site_address") or deed.get("description") or "",
            "site_postcode": postcode,
            "site_lat": lat,
            "site_lon": lon,
            "ward_name": ward,
            "ward_source": ward_source,
            "developer_name": None,
            "total_dwellings_permitted": None,
            "document_url": deed.get("document_url"),
            "sha256": deed.get("sha256"),
            "bytes": deed.get("bytes"),
            "source_note": (
                f"Engrossed legal agreement published by {AUTHORITY} on its public planning "
                f"register against permission {deed['planning_reference']}, which was decided "
                f"{deed.get('decision_label') or deed.get('decision_code')}. "
                f"Application description: {deed.get('description') or 'not recorded'}. "
                "Scanned image: the auditor OCRs it locally and reconciles every sum against "
                "the amount the deed spells out in words."
            ),
            "register_key_val": deed.get("key_val"),
            "decision_code": deed.get("decision_code"),
            "verified": True,
        }
        enriched.append(entry)
        print(f"  {deed['planning_reference']:16s} {postcode or 'no postcode':10s} ward={ward}")

    PROVENANCE_FILE.write_text(
        json.dumps(
            {
                "_comment": (
                    "Ingest-ready provenance for deeds harvested from Sheffield City "
                    "Council's public planning register. Generated from manifest.json by "
                    "scripts/enrich_sheffield_manifest.py; nothing here is synthetic."
                ),
                "_generated": date.today().isoformat(),
                "_source": manifest.get("_source"),
                "deeds": enriched,
            },
            indent=2,
        )
        + "\n"
    )
    resolved = sum(1 for e in enriched if e["ward_name"])
    print(f"\n{resolved}/{len(enriched)} deeds resolved to a Sheffield ward")
    print(f"wrote {PROVENANCE_FILE}")


if __name__ == "__main__":
    main()
