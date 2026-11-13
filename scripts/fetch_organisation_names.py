"""
Fill in the council names behind the national dataset's entity ids.

The three developer-contribution datasets identify a publisher only by a numeric
`organisation-entity` (345, 122, 402...). The names live in a separate dataset,
`organisation.csv`, which planning.data.gov.uk has been returning 503 for. Until
this script succeeds, `scripts.analyse_national_unpaid` shows those publishers as
"Entity 345, name unverified" rather than guessing, because a confident wrong
council name next to a large pound figure is precisely the failure this auditor
exists to avoid.

Run it once the service answers; then re-run the analysis and the names appear:

    python3 -m scripts.fetch_organisation_names
    python3 -m scripts.analyse_national_unpaid

A partial or failed download is refused rather than written: a truncated register
would silently turn verified names into missing ones.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
NATIONAL_DIR = REPO_ROOT / "data" / "fixtures" / "csv" / "national"
ORGANISATION_URL = "https://www.planning.data.gov.uk/dataset/organisation.csv"
NAMES_FILE = NATIONAL_DIR / "organisation_names.json"
ANALYSIS_FILE = NATIONAL_DIR / "unpaid_analysis.json"

# A real organisation register is tens of thousands of rows. Anything smaller is a
# truncated transfer or an error page that happens to parse as CSV.
MIN_ROWS = 1000


def download(url: str = ORGANISATION_URL, timeout: int = 120) -> Optional[str]:
    request = urllib.request.Request(url, headers={"User-Agent": "s106-auditor/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        code = getattr(exc, "code", None)
        print(f"  ! {url} failed ({code or exc}). The register is not answering.")
        return None
    if status != 200:
        print(f"  ! {url} returned HTTP {status}.")
        return None
    if body.lstrip().startswith("<"):
        print(f"  ! {url} returned HTML, not CSV. Usually a service-problem page.")
        return None
    return body


def publisher_entities() -> set[str]:
    """Entity ids that actually publish developer contributions."""
    if ANALYSIS_FILE.exists():
        try:
            analysis = json.loads(ANALYSIS_FILE.read_text())
            return {a["entity"] for a in analysis.get("authorities", [])}
        except (json.JSONDecodeError, KeyError):
            pass
    contributions = NATIONAL_DIR / "developer-agreement-contribution.csv"
    if not contributions.exists():
        return set()
    with contributions.open(newline="", encoding="utf-8") as handle:
        return {(row.get("organisation-entity") or "").strip() for row in csv.DictReader(handle)} - {
            ""
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=ORGANISATION_URL)
    args = parser.parse_args()

    wanted = publisher_entities()
    print(f"Looking up {len(wanted)} publishing entity ids in the organisation register")

    body = download(args.url)
    if body is None:
        raise SystemExit(
            "organisation.csv unavailable. Nothing written: existing names are left as they "
            "are. Retry when planning.data.gov.uk is answering."
        )

    rows = list(csv.DictReader(io.StringIO(body)))
    if len(rows) < MIN_ROWS:
        raise SystemExit(
            f"organisation.csv returned only {len(rows)} rows, expected at least "
            f"{MIN_ROWS}. Refusing to write a partial register."
        )

    found: dict[str, str] = {}
    for row in rows:
        entity = (row.get("entity") or "").strip()
        name = (row.get("name") or "").strip()
        if entity and name and (not wanted or entity in wanted):
            found.setdefault(entity, name)

    if not found:
        raise SystemExit(
            "Downloaded the register but matched none of the publishing entity ids. "
            "Check that organisation.csv still has `entity` and `name` columns."
        )

    previous: dict[str, str] = {}
    if NAMES_FILE.exists():
        try:
            previous = json.loads(NAMES_FILE.read_text()).get("names", {})
        except (json.JSONDecodeError, OSError):
            previous = {}
    # Never lose a name that was verified earlier just because today's register
    # happened not to include it.
    merged = {**previous, **found}

    NAMES_FILE.write_text(
        json.dumps(
            {
                "_comment": (
                    "entity id -> authority name, read from the live organisation register. "
                    "Entities absent from this file are shown by entity id and marked "
                    "unverified rather than guessed."
                ),
                "_source": args.url,
                "_generated": date.today().isoformat(),
                "_rows_in_register": len(rows),
                "names": dict(sorted(merged.items())),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Resolved {len(found)} of {len(wanted)} publishers; wrote {NAMES_FILE}")
    missing = sorted(wanted - set(merged))
    if missing:
        print(f"  still unnamed: {len(missing)} entity ids")


if __name__ == "__main__":
    main()
