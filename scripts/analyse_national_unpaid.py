"""
Build the national "what councils are owed" ledger from the published dataset.

Sheffield publishes nothing to `planning.data.gov.uk`, so the auditor's own ledger
is a floor, not a picture of the whole problem. This script reads the three
statutory developer-contribution datasets that *other* authorities publish and
works out, contribution by contribution, how much was committed and how much of it
ever arrived. The result is what the overview page leads with.

Definitions, stated in full because a wrong number here is worse than no number:

  * **committed** — the `amount` on a `developer-agreement-contribution` row. Rows
    with no amount, or a non-numeric amount, are counted separately and excluded
    from money totals rather than treated as zero.
  * **received** — the sum of `developer-agreement-transaction` amounts whose
    `contribution-funding-status` is one of received / allocated / spent /
    delivered / transferred. The last four are downstream of money actually
    arriving, so counting them as received cannot overstate what was collected.
  * **outstanding** — `committed - received`, floored at zero. A contribution paid
    twice, or overpaid, is not allowed to reduce another authority's total.
  * **never received anything** — a contribution with a committed amount and no
    received transaction at all. This is the headline: money a developer promised
    under a signed legal agreement that the council has not collected.

Authority names come from `organisation_names.json` next to the output. That file
is only written from a live `organisation.csv`; when planning.data.gov.uk is
unreachable the analysis still runs and the authority is shown by entity id, marked
unverified, rather than guessed.

    python3 -m scripts.analyse_national_unpaid
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
NATIONAL_DIR = REPO_ROOT / "data" / "fixtures" / "csv" / "national"
CONTRIBUTIONS_CSV = NATIONAL_DIR / "developer-agreement-contribution.csv"
TRANSACTIONS_CSV = NATIONAL_DIR / "developer-agreement-transaction.csv"
AGREEMENTS_CSV = NATIONAL_DIR / "developer-agreement.csv"
OUTPUT_FILE = NATIONAL_DIR / "unpaid_analysis.json"
NAMES_FILE = NATIONAL_DIR / "organisation_names.json"

# Statuses that only exist once money has actually reached the council's account.
RECEIVED_STATUSES = {"received", "allocated", "spent", "delivered", "transferred"}

AMOUNT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_amount(raw: str) -> Optional[float]:
    """Strict: '1,234.00' and '' and '12k' are all rejected rather than guessed."""
    text = (raw or "").strip()
    if not text or not AMOUNT_RE.match(text):
        return None
    return float(text)


def load_names() -> dict[str, str]:
    """entity id -> council name, only from a verified mapping file."""
    if not NAMES_FILE.exists():
        return {}
    try:
        payload = json.loads(NAMES_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k): str(v) for k, v in (payload.get("names") or {}).items()}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def analyse() -> dict[str, Any]:
    contributions = read_rows(CONTRIBUTIONS_CSV)
    transactions = read_rows(TRANSACTIONS_CSV)
    agreements = read_rows(AGREEMENTS_CSV)
    names = load_names()

    # --- transactions rolled up onto the contribution they belong to -----------
    # received -> allocated -> spent is the same money moving through stages, so the
    # stages are NOT summed. The amount received is the largest single stage, which
    # also copes with a contribution paid in instalments at each stage.
    stage_sums: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    secured_by_ref: dict[str, float] = defaultdict(float)
    malformed_transactions = 0
    for tx in transactions:
        status = (tx.get("contribution-funding-status") or "").strip().lower()
        if status not in RECEIVED_STATUSES and status != "secured":
            continue
        ref = (tx.get("developer-agreement-contribution") or "").strip()
        if not ref:
            continue
        amount = parse_amount(tx.get("amount", ""))
        if amount is None:
            amount = parse_amount(tx.get("units", ""))
            if amount is None:
                malformed_transactions += 1
                continue
        if status == "secured":
            secured_by_ref[ref] += amount
        else:
            stage_sums[ref][status] += amount

    received_by_ref = {ref: max(stages.values()) for ref, stages in stage_sums.items()}

    # A single bulk payment is often linked to every contribution it covers, so the
    # same large `received` figure appears against several small obligations
    # (COV-07332/3333/334 each show £477,382 received against commitments of
    # £13,745, £16,364 and £21,818). A contribution cannot receive more than it
    # committed, so receipts are capped at the commitment. Without this, two
    # authorities came out with a negative amount owed.
    over_attributed = 0

    # --- contributions --------------------------------------------------------
    per_entity: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "contributions": 0,
            "committed_gbp": 0.0,
            "received_gbp": 0.0,
            "shortfall_gbp": 0.0,
            "never_received": 0,
            "never_received_gbp": 0.0,
            "secured_no_receipt": 0,
            "secured_no_receipt_gbp": 0.0,
        }
    )
    per_purpose: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"contributions": 0, "committed_gbp": 0.0, "shortfall_gbp": 0.0}
    )

    totals = {
        "contribution_rows": len(contributions),
        "rows_with_amount": 0,
        "rows_without_amount": 0,
        "committed_gbp": 0.0,
        "received_gbp": 0.0,
        "received_uncapped_gbp": 0.0,
        "shortfall_gbp": 0.0,
        "never_received_contributions": 0,
        "never_received_gbp": 0.0,
        "secured_no_receipt_contributions": 0,
        "secured_no_receipt_gbp": 0.0,
    }

    for row in contributions:
        entity = (row.get("organisation-entity") or "").strip() or "unknown"
        ref = (row.get("reference") or "").strip()
        amount = parse_amount(row.get("amount", ""))
        purpose = (row.get("contribution-purpose") or "unspecified").strip() or "unspecified"
        raw_received = received_by_ref.get(ref, 0.0)

        entity_row = per_entity[entity]
        entity_row["contributions"] += 1

        if amount is None or amount <= 0.0:
            # No committed amount means this row cannot take part in a
            # committed-versus-received total. Treating a zero as a real
            # commitment would also make every receipt against it look unpaid.
            totals["rows_without_amount"] += 1
            continue
        totals["rows_with_amount"] += 1
        totals["received_uncapped_gbp"] += raw_received

        # Cap receipts at the commitment: a bulk payment linked to several
        # obligations would otherwise be counted in full against each of them.
        received = min(raw_received, amount)
        if raw_received > amount + 0.005:
            over_attributed += 1
        totals["committed_gbp"] += amount
        totals["received_gbp"] += received
        entity_row["committed_gbp"] += amount
        entity_row["received_gbp"] += received

        # Floored per contribution: a contribution paid twice is not allowed to
        # reduce the same authority's total. `not_collected` below is the aggregate
        # difference, which is what ties to committed minus received.
        shortfall = max(0.0, amount - received)
        totals["shortfall_gbp"] += shortfall
        entity_row["shortfall_gbp"] += shortfall

        purpose_row = per_purpose[purpose]
        purpose_row["contributions"] += 1
        purpose_row["committed_gbp"] += amount
        purpose_row["shortfall_gbp"] += shortfall

        if received <= 0.0 and amount > 0.0:
            totals["never_received_contributions"] += 1
            totals["never_received_gbp"] += amount
            entity_row["never_received"] += 1
            entity_row["never_received_gbp"] += amount

        # The tightest claim available: the council itself recorded the money as
        # secured under a signed agreement, and no payment has ever arrived.
        secured = secured_by_ref.get(ref, 0.0)
        if secured > 0.0 and received <= 0.0:
            totals["secured_no_receipt_contributions"] += 1
            totals["secured_no_receipt_gbp"] += secured
            entity_row["secured_no_receipt"] += 1
            entity_row["secured_no_receipt_gbp"] += secured

    authorities = []
    for entity, row in per_entity.items():
        committed = round(row["committed_gbp"], 2)
        received = round(row["received_gbp"], 2)
        authorities.append(
            {
                "entity": entity,
                "name": names.get(entity),
                "name_verified": entity in names,
                "contributions": row["contributions"],
                "committed_gbp": committed,
                "received_gbp": received,
                # The figure that ties: committed minus received, in the aggregate.
                "not_collected_gbp": round(committed - received, 2),
                # Per-contribution, floored at zero, so a double payment on one
                # obligation cannot hide a shortfall on another.
                "shortfall_gbp": round(row["shortfall_gbp"], 2),
                "never_received_count": row["never_received"],
                "never_received_gbp": round(row["never_received_gbp"], 2),
                "secured_no_receipt_count": row["secured_no_receipt"],
                "secured_no_receipt_gbp": round(row["secured_no_receipt_gbp"], 2),
            }
        )
    authorities.sort(key=lambda a: -a["not_collected_gbp"])

    committed_total = round(totals["committed_gbp"], 2)
    received_total = round(totals["received_gbp"], 2)
    received_uncapped = round(totals["received_uncapped_gbp"], 2)
    headline = {
        "publishing_authorities": len(per_entity),
        "priced_contributions": totals["rows_with_amount"],
        "committed_gbp": committed_total,
        "received_gbp": received_total,
        # Two defensible readings of the same messy data, reported as a range
        # rather than quietly picking the bigger one. A council that publishes one
        # bulk payment linked to every obligation it covers makes the upper bound
        # too high; counting that payment in full against each obligation makes the
        # lower bound too low. The truth is between them.
        "not_collected_gbp": round(committed_total - received_total, 2),
        "received_uncapped_gbp": received_uncapped,
        "not_collected_uncapped_gbp": round(committed_total - received_uncapped, 2),
        "secured_no_receipt_count": totals["secured_no_receipt_contributions"],
        "secured_no_receipt_gbp": round(totals["secured_no_receipt_gbp"], 2),
        "never_received_count": totals["never_received_contributions"],
        "never_received_gbp": round(totals["never_received_gbp"], 2),
    }

    sheffield_rows = sum(
        1 for row in contributions if (row.get("organisation") or "") == "local-authority:SHF"
    )

    return {
        "_comment": (
            "National developer-contribution position, computed from the published "
            "developer-agreement datasets on disk. Every figure is a sum of published "
            "rows; nothing here is modelled or estimated."
        ),
        "_generated": date.today().isoformat(),
        "_definitions": {
            "committed": "developer-agreement-contribution.amount",
            "received": (
                "the largest single stage among "
                + ", ".join(sorted(RECEIVED_STATUSES))
                + " transaction amounts for that contribution. These are stages of the same "
                "money, so summing them would count one payment up to four times"
            ),
            "outstanding": "max(0, committed - received) per contribution",
            "never_received": "a contribution with a committed amount and no received transaction",
            "secured_no_receipt": (
                "a contribution with a `secured` transaction and no received transaction: the "
                "council has recorded the money as owed under a signed agreement and has not "
                "collected a penny of it. Not all of it is yet legally due - many obligations "
                "trigger on occupation of a later dwelling - but none of it is money the "
                "council has seen."
            ),
            "tie_out": (
                "committed - received equals outstanding except where a contribution was paid "
                "more than once, which is floored to zero rather than netted against other "
                "contributions"
            ),
        },
        "sources": {
            "developer-agreement-contribution.csv": {
                "rows": len(contributions),
                "sha256": sha256(CONTRIBUTIONS_CSV),
            },
            "developer-agreement-transaction.csv": {
                "rows": len(transactions),
                "sha256": sha256(TRANSACTIONS_CSV),
            },
            "developer-agreement.csv": {"rows": len(agreements), "sha256": sha256(AGREEMENTS_CSV)},
            "malformed_transactions_skipped": malformed_transactions,
            "receipts_capped_at_commitment": over_attributed,
            "authority_names": NAMES_FILE.name if NAMES_FILE.exists() else "not available",
        },
        "headline": headline,
        "totals": {
            **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in totals.items()},
            "publishing_authorities": len(per_entity),
            "sheffield_contribution_rows": sheffield_rows,
        },
        "authorities": authorities,
        "purposes": sorted(
            (
                {
                    "purpose": purpose,
                    "contributions": row["contributions"],
                    "committed_gbp": round(row["committed_gbp"], 2),
                    "shortfall_gbp": round(row["shortfall_gbp"], 2),
                }
                for purpose, row in per_purpose.items()
            ),
            key=lambda p: -p["shortfall_gbp"],
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="print totals, write nothing")
    args = parser.parse_args()

    for path in (CONTRIBUTIONS_CSV, TRANSACTIONS_CSV, AGREEMENTS_CSV):
        if not path.exists():
            raise SystemExit(f"{path} is missing. Run scripts.fetch_real_datasets first.")

    result = analyse()
    h = result["headline"]
    print(
        f"{h['priced_contributions']:,} priced contributions from "
        f"{h['publishing_authorities']} authorities: £{h['committed_gbp']:,.2f} committed, "
        f"£{h['received_gbp']:,.2f} received, £{h['not_collected_gbp']:,.2f} not collected"
    )
    print(
        f"  secured with no payment recorded: {h['secured_no_receipt_count']:,} contributions, "
        f"£{h['secured_no_receipt_gbp']:,.2f}"
    )
    print(
        f"  never received anything: {h['never_received_count']:,} contributions, "
        f"£{h['never_received_gbp']:,.2f}"
    )
    print("  top 5 by not collected:")
    for a in result["authorities"][:5]:
        label = a["name"] or f"entity {a['entity']} (name unverified)"
        print(f"    {label:44s} £{a['not_collected_gbp']:>14,.2f}")
    if args.check:
        return
    OUTPUT_FILE.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
