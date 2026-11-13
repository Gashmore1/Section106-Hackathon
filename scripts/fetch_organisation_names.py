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
NAMES_FILE = NATIONAL_DIR / "organisation_names.json"
ANALYSIS_FILE = NATIONAL_DIR / "unpaid_analysis.json"

# (label, url, minimum plausible rows). Tried in order.
#
# planning.data.gov.uk is the canonical publisher but has been returning 503.
# The Digital Land project is the pipeline that *builds* that register, and it
# keeps the organisation collection in a public repository, which is a live
# upstream source rather than a snapshot of the down one. Both use `entity` and
# `name` columns.
SOURCES: tuple[tuple[str, str, int], ...] = (
    (
        "planning.data.gov.uk organisation register",
        "https://www.planning.data.gov.uk/dataset/organisation.csv",
        1000,
    ),
    (
        "Digital Land organisation collection (GitHub)",
        "https://raw.githubusercontent.com/digital-land/organisation-dataset/main/collection/organisation.csv",
        200,
    ),
)


def download(url: str, timeout: int = 120) -> Optional[str]:
    request = urllib.request.Request(url, headers={"User-Agent": "s106-auditor/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        code = getattr(exc, "code", None)
        print(f"  ! {url} failed ({code or exc}).")
        return None
    if status != 200:
        print(f"  ! {url} returned HTTP {status}.")
        return None
    if body.lstrip().startswith("<"):
        print(f"  ! {url} returned HTML, not CSV. Usually a service-problem page.")
        return None
    return body


def fetch_register(url: Optional[str] = None) -> tuple[str, str, list[dict[str, str]]]:
    """
    Try each configured source in order and return (label, url, rows).

    A source that answers with too few rows is skipped rather than accepted: a
    truncated register would silently turn verified names into missing ones.
    """
    sources = [("--url override", url, 1)] if url else list(SOURCES)
    for label, source_url, min_rows in sources:
        print(f"  trying {label}")
        body = download(source_url)
        if body is None:
            continue
        rows = list(csv.DictReader(io.StringIO(body)))
        if len(rows) < min_rows:
            print(f"  ! only {len(rows)} rows, expected at least {min_rows}; skipping")
            continue
        print(f"  + {label}: {len(rows)} rows")
        return label, source_url, rows
    raise SystemExit(
        "No organisation register source answered. Nothing written: existing names are "
        "left as they are. Retry later."
    )


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
    parser.add_argument("--url", default=None, help="override the configured register sources")
    args = parser.parse_args()

    wanted = publisher_entities()
    print(f"Looking up {len(wanted)} publishing entity ids in the organisation register")

    label, source_url, rows = fetch_register(args.url)

    found: dict[str, str] = {}
    for row in rows:
        entity = (row.get("entity") or "").strip()
        name = (row.get("name") or "").strip()
        if entity and name and (not wanted or entity in wanted):
            found.setdefault(entity, name)

    if not found:
        raise SystemExit(
            "Downloaded the register but matched none of the publishing entity ids. "
            "Check that it still has `entity` and `name` columns."
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
                "_source": label,
                "_source_url": source_url,
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
