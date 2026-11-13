"""Build Sheffield's own reported developer-contribution time series.

Sheffield City Council publishes an annual Infrastructure Funding Statement
(IFS) under the Community Infrastructure Levy (Amendment) (England) (No.2)
Regulations 2019. Every figure in the output fixture comes from one of those
PDFs: nothing here is inferred from the national dataset, because Sheffield
publishes nothing to it.

The script is deliberately literal. Each figure is pulled by a regex anchored on
the sentence the council actually wrote, the sentence is kept as a verbatim
quote, and the arithmetic the council claims for itself is re-checked. Where a
published figure does not reconcile, the script says so instead of fixing it
quietly.

    python -m scripts.fetch_sheffield_ifs_series
    python -m scripts.fetch_sheffield_ifs_series --check   # do not write
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = REPO_ROOT / "data" / "fixtures" / "pdfs" / "sheffield" / "ifs"
OUT_FILE = REPO_ROOT / "data" / "fixtures" / "pdfs" / "sheffield" / "ifs_series.json"

# Financial year -> published URL. Verified against the council's own CIL page:
# https://www.sheffield.gov.uk/planning-and-development/planning-application-requirements/community-infrastructure-levy
SOURCES: tuple[tuple[str, str], ...] = (
    (
        "2019/20",
        "https://www.sheffield.gov.uk/sites/default/files/docs/planning-and-development/cil/ifs-report-2019-2020.pdf",
    ),
    (
        "2020/21",
        "https://www.sheffield.gov.uk/sites/default/files/docs/planning-and-development/cil/ifs-report-2020-2021.pdf",
    ),
    (
        "2021/22",
        "https://www.sheffield.gov.uk/sites/default/files/2023-03/infrastructure_funding_statement_2021-2022.pdf",
    ),
    (
        "2022/23",
        "https://www.sheffield.gov.uk/sites/default/files/2024-01/infrastructure_funding_statement_2022-2023.pdf",
    ),
    (
        "2023/24",
        "https://www.sheffield.gov.uk/sites/default/files/2025-03/ifs_report_sheffieldcitycouncil_2023_2024.pdf",
    ),
    (
        "2024/25",
        "https://www.sheffield.gov.uk/sites/default/files/2026-01/infrastructure_funding_statement_2024_to_2025.pdf",
    ),
)

USER_AGENT = "s106-auditor/0.1"

# A money literal as printed, including the malformed ones (see parse_amount).
# A money literal as printed. The integer part has to end on a digit, otherwise
# "was £3,504,340, which is" gets read as "£3,504,340,". Deliberately permissive
# about bad comma grouping so genuinely misprinted figures are caught and
# reported rather than silently missed (see parse_amount).
AMOUNT = r"£\s*(?:\d[\d,]*\d|\d)(?:\.\d{1,2})?"

# Every pattern names its captures, because "the last group" silently changes
# meaning when an optional clause is added. `amount` is always the money literal.
PATTERNS: dict[str, re.Pattern[str]] = {
    "s106_receipts_in_year": re.compile(
        r"The total amount of Section 106 receipts in the reporting year\s*"
        r"(?P<year>\d{4}/\d{2})\s+was\s+(?P<amount>" + AMOUNT + r")"
        r"(?:,?\s*(?:which is\s+)?(?:an?\s+)?(?P<trend>increase|decrease)\s+of\s+"
        r"(?P<trend_pct>[\d.]+)%\s+compared to the previous year)?",
        re.I,
    ),
    "s106_agreements_signed": re.compile(
        r"(?P<count>[A-Za-z][A-Za-z-]+|\d+)\s+S106 agreements?\s+were\s+signed\s+in\s+"
        r"(?P<year>\d{4}/\d{2})\s+with\s+potential\s+future\s+receipts\s+of\s+(?P<amount>"
        + AMOUNT + r")",
        re.I,
    ),
    # From 2023/24 the statement changes wording and starts counting deeds of
    # variation and supplemental deeds in the same total, so the counts either
    # side of that line are not the same measure. Recorded, not smoothed over.
    "s106_agreements_signed_including_variations": re.compile(
        r"There have been\s+(?P<count>\d+)\s+S106 agreements\s+\(including deed of "
        r"variations and supplemental deeds\)\s+signed\s+in\s+(?P<year>\d{4}/\d{2})\s+"
        r"with\s+potential\s+future\s+receipts\s+of\s+(?P<amount>" + AMOUNT + r")",
        re.I,
    ),
    "s106_balance_retained": re.compile(
        r"the amount retained at the end of\s*(?P<year>\d{4}/\d{2})\s+was\s+(?P<amount>"
        + AMOUNT + r")",
        re.I,
    ),
    "s106_committed_next_5_years": re.compile(
        r"(?P<amount>£[\d.]+m)\s+is\s+committed\s+to\s+schemes\s+over\s+the\s+next\s+5\s+years",
        re.I,
    ),
    "s106_available_to_allocate": re.compile(
        r"remaining\s+(?P<amount>£[\d.]+m)\s+available\s+to\s+be\s+allocated", re.I
    ),
    "s106_monitoring_cost": re.compile(
        r"monitoring(?:\s+and\s+reporting)?\s+of\s+Section\s+106\s+agreements[^£]{0,60}?"
        r"(?:in\s+\d{4}/\d{2}\s+)?is\s+(?P<amount>" + AMOUNT + r")",
        re.I,
    ),
    "cil_received_in_year": re.compile(
        r"The total amount of CIL received in the reporting year\s*(?P<year>\d{4}/\d{2})\s+was\s+"
        r"(?P<amount>" + AMOUNT + r")",
        re.I,
    ),
    "cil_demand_notices_issued": re.compile(
        r"value of demand notices issued in\s*(?P<year>\d{4}/\d{2})\s+is\s+(?P<amount>"
        + AMOUNT + r")",
        re.I,
    ),
    "cil_demand_liability_notices": re.compile(
        r"demand notices issued in\s*\d{4}/\d{2}.*?"
        r"liability notices?[^£]{0,20}(?:\(\s*)?(?P<amount>" + AMOUNT + r")",
        re.I | re.S,
    ),
    "cil_demand_surcharges": re.compile(
        r"demand notices issued in\s*\d{4}/\d{2}.*?surcharges[^£]{0,30}(?:\(\s*)?(?P<amount>"
        + AMOUNT + r")",
        re.I | re.S,
    ),
    "cil_demand_interest": re.compile(
        r"demand notices issued in\s*\d{4}/\d{2}.*?interest accrued[^£]{0,20}(?:\(\s*)?"
        r"(?P<amount>" + AMOUNT + r")",
        re.I | re.S,
    ),
}

WORD_NUMBERS = {
    "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "twentyone": 21, "twentytwo": 22, "twentythree": 23, "twentyfour": 24,
}

VALID_GROUPING = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?")


class AmountError(ValueError):
    """A printed money literal that cannot be read without guessing."""


def _group(digits: str) -> str:
    """Insert thousands separators into '1234567.89'."""
    if "." in digits:
        whole, frac = digits.split(".", 1)
        frac = "." + frac
    else:
        whole, frac = digits, ""
    if not whole:
        return frac
    out = []
    while whole:
        out.insert(0, whole[-3:])
        whole = whole[:-3]
    return ",".join(out) + frac


def parse_amount(printed: str) -> tuple[Optional[float], Optional[dict[str, Any]]]:
    """
    Read a printed money literal.

    Returns (value, note). A well-formed number has no note.

    The 2024/25 statement prints its closing S106 balance as £15,3251,204.79,
    which is not a number: one stray digit inside a comma group. Rather than pick
    a reading, every single-digit deletion that leaves all the printed comma
    groups well-formed is kept, and no value is returned. The difference between
    the surviving readings is £100,000 on a £15.3m balance, and nothing in this
    repository turns on it, so the honest output is the range and a note saying
    the published document contains a typo.
    """
    raw = printed.replace("£", "").replace(" ", "").strip().rstrip(".")
    if VALID_GROUPING.fullmatch(raw):
        return float(raw.replace(",", "")), None

    groups = raw.split(",")
    candidates: list[float] = []
    for position, group in enumerate(groups):
        digits = re.sub(r"[^\d]", "", group)
        decimals = group.split(".")[1] if "." in group else None
        for index in range(len(digits)):
            trial_digits = digits[:index] + digits[index + 1 :]
            whole_ok = (
                len(trial_digits) == 3
                if position > 0
                else 1 <= len(trial_digits) <= 3
            )
            if not whole_ok:
                continue
            rebuilt = list(groups)
            rebuilt[position] = trial_digits + ("." + decimals if decimals else "")
            trial = ",".join(rebuilt)
            if VALID_GROUPING.fullmatch(trial):
                candidates.append(float(trial.replace(",", "")))
    candidates = sorted(set(candidates))
    if not candidates:
        raise AmountError(f"cannot read printed amount {printed!r}")
    return None, {
        "printed_verbatim": printed.strip(),
        "reason": (
            "the published figure is not a well-formed number: one stray digit "
            "inside a comma group"
        ),
        "candidates": candidates,
        "range_gbp": [candidates[0], candidates[-1]],
        "eliminated": (
            "reading the digits without the stray character as £153,251,204.79 is "
            "ruled out by the same paragraph, which splits the balance as £10.7m "
            "committed plus £4.7m available"
        ),
    }


def to_words(text: str) -> str:
    """Join soft line breaks so sentence-spanning regexes work."""
    return re.sub(r"\s+", " ", text)


def download(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        status = getattr(response, "status", 200)
        if status != 200:
            raise RuntimeError(f"{url} returned HTTP {status}")
        body = response.read()
    if not body.startswith(b"%PDF"):
        # A 404 page can be served with HTTP 200 by the council's CMS.
        raise RuntimeError(f"{url} did not return a PDF (starts with {body[:8]!r})")
    return body


def page_texts(pdf_bytes: bytes) -> list[tuple[int, str]]:
    import pymupdf

    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return [(number + 1, page.get_text()) for number, page in enumerate(document)]


def find_first(pages: list[tuple[int, str]], pattern: re.Pattern[str]) -> Optional[dict[str, Any]]:
    """First match, with the page it appeared on and the sentence around it."""
    for page_number, raw in pages:
        flat = to_words(raw)
        for match in pattern.finditer(flat):
            named = {k: v.strip() for k, v in match.groupdict().items() if v is not None}
            if not named:
                continue
            start = max(match.start() - 90, 0)
            return {
                "page": page_number,
                "quote": flat[start : match.end() + 90].strip(),
                "named": named,
                "match": match.group(0).strip(),
            }
    return None


def extract_cil_table1(pages: list[tuple[int, str]]) -> Optional[dict[str, Any]]:
    """
    Table 1: CIL received to date, allocated, and unallocated, by category.

    The table is laid out one cell per line, so the amounts are read in order
    after the Strategic and Neighbourhood labels.
    """
    for page_number, raw in pages:
        if "Table 1" not in raw or "Strategic CIL" not in raw:
            continue
        block = raw[raw.index("Strategic CIL") :]
        block = block[: block.index("3.9")] if "3.9" in block else block[:1200]
        amounts = re.findall(AMOUNT, block)
        if len(amounts) < 5:
            continue
        rows: dict[str, Any] = {"_page": page_number}
        labels = ["strategic", "neighbourhood"]
        cursor = 0
        for label in labels:
            triple = amounts[cursor : cursor + 3]
            if not triple:
                break
            while len(triple) < 3:
                triple.append("£0")
            cursor += 3
            values = []
            for cell in triple:
                value, note = parse_amount(cell)
                values.append((value, note))
            received, allocated, unallocated = values
            rows[label] = {
                "received_to_date_gbp": received[0],
                "allocated_gbp": allocated[0],
                "unallocated_gbp": unallocated[0],
                "checks": {
                    "received_minus_allocated_equals_unallocated": (
                        None
                        if None in (received[0], allocated[0], unallocated[0])
                        else abs((received[0] - allocated[0]) - unallocated[0]) < 0.01
                    )
                },
            }
            for key, (_, note) in (
                ("received_to_date", received),
                ("allocated", allocated),
                ("unallocated", unallocated),
            ):
                if note:
                    rows[label][f"{key}_correction"] = note
        return rows
    return None


def as_count(word: str) -> Optional[int]:
    if word.isdigit():
        return int(word)
    return WORD_NUMBERS.get(word.lower().replace("-", "").replace(" ", ""))


def extract_year(year: str, filename: str, pdf_bytes: bytes) -> dict[str, Any]:
    pages = page_texts(pdf_bytes)
    record: dict[str, Any] = {
        "financial_year": year,
        "source": {
            "file": filename,
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "pages": len(pages),
            "publisher": "Sheffield City Council",
            "document": "Annual Infrastructure Funding Statement (CIL and Section 106)",
        },
        "figures": {},
        "corrections": [],
        "checks": {},
    }

    for name, pattern in PATTERNS.items():
        hit = find_first(pages, pattern)
        if not hit:
            continue
        named = hit["named"]
        entry: dict[str, Any] = {"page": hit["page"], "quote": hit["quote"]}
        if name.startswith("s106_agreements_signed"):
            count = as_count(named["count"])
            if count is None:
                continue
            entry["count"] = count
            entry["stated_year"] = named.get("year")
            entry["basis"] = (
                "new agreements plus deeds of variation and supplemental deeds"
                if name.endswith("including_variations")
                else "agreements signed in the year, as worded that year"
            )
        elif name.endswith("_next_5_years") or name.endswith("_available_to_allocate"):
            entry["value_gbp"] = round(float(named["amount"].strip("£m").replace(",", "")) * 1e6, 2)
            entry["precision"] = "the statement rounds this to the nearest £0.1m"
            record["figures"][name] = entry
            continue
        else:
            entry["stated_year"] = named.get("year")
            if named.get("trend"):
                entry["stated_change"] = f"{named['trend']} of {named['trend_pct']}%"
        value, note = parse_amount(named["amount"])
        entry["printed"] = named["amount"]
        entry["value_gbp"] = value
        if note:
            entry["correction"] = note
            record["corrections"].append({f"figures.{name}": note})
        record["figures"][name] = entry

    table1 = extract_cil_table1(pages)
    if table1:
        record["cil_cumulative_table"] = table1

    # --- reconciliation the council asserts for itself -------------------------
    balance = record["figures"].get("s106_balance_retained")
    if balance and balance.get("value_gbp") is not None:
        committed = record["figures"].get("s106_committed_next_5_years")
        available = record["figures"].get("s106_available_to_allocate")
        if committed and available:
            parts = committed["value_gbp"] + available["value_gbp"]
            record["checks"]["balance_equals_committed_plus_available"] = {
                "balance": balance["value_gbp"],
                "committed_plus_available": parts,
                "difference_gbp": round(balance["value_gbp"] - parts, 2),
                "note": (
                    "the committed and available figures are each rounded to "
                    "£0.1m, so a difference of up to £100,000 is expected"
                ),
                "within_rounding": abs(balance["value_gbp"] - parts) <= 100_000,
            }
    demand = record["figures"].get("cil_demand_notices_issued")
    parts = [
        record["figures"].get(key)
        for key in ("cil_demand_liability_notices", "cil_demand_surcharges", "cil_demand_interest")
    ]
    if demand and demand.get("value_gbp") is not None and all(parts):
        total = sum(p["value_gbp"] for p in parts)
        record["checks"]["demand_notice_components"] = {
            "stated_total": demand["value_gbp"],
            "sum_of_components": round(total, 2),
            "difference_gbp": round(demand["value_gbp"] - total, 2),
            "ties": abs(demand["value_gbp"] - total) < 0.01,
        }
    if table1:
        checks = [
            row.get("checks", {}).get("received_minus_allocated_equals_unallocated")
            for row in table1.values()
            if isinstance(row, dict)
        ]
        if any(c is None for c in checks):
            record["checks"]["cil_table1_rows_tie"] = None
        else:
            record["checks"]["cil_table1_rows_tie"] = all(checks)
    return record


def build_series(years: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Flatten the per-year records into rows ready for display.

    Two derived figures are computed here rather than in the browser, so the
    arithmetic is done once, in one place, and is reviewable:

    `developer_contributions_received_gbp`
        S106 receipts plus CIL receipts for the same financial year. These are
        the two statutory streams; nothing else counts as a developer
        contribution in an IFS.
    `unspent_at_year_end_gbp`
        the S106 balance the council retained, plus CIL received-to-date that has
        not been allocated to a project. This is the money secured and collected
        but not yet delivered as infrastructure. Where a figure is a range, the
        derived figure is a range too.
    """
    rows: list[dict[str, Any]] = []
    for year in sorted(years):
        record = years[year]
        figures = record["figures"]
        table = record.get("cil_cumulative_table", {}) or {}

        def value(key: str) -> Optional[float]:
            figure = figures.get(key)
            return figure.get("value_gbp") if figure else None

        def span(key: str) -> Optional[tuple[float, float]]:
            """(low, high) for a figure that may be a range because of a typo."""
            figure = figures.get(key)
            if not figure:
                return None
            if figure.get("value_gbp") is not None:
                return (figure["value_gbp"], figure["value_gbp"])
            found = (figure.get("correction") or {}).get("range_gbp")
            return (found[0], found[1]) if found else None

        cil_unallocated = 0.0
        cil_unallocated_known = True
        cil_received_to_date = 0.0
        for row in table.values():
            if not isinstance(row, dict):
                continue
            if row.get("unallocated_gbp") is None:
                cil_unallocated_known = False
            else:
                cil_unallocated += row["unallocated_gbp"]
            if row.get("received_to_date_gbp") is None:
                cil_received_to_date = None
            else:
                cil_received_to_date += row["received_to_date_gbp"]

        s106_receipts = value("s106_receipts_in_year")
        cil_receipts = value("cil_received_in_year")
        received = (
            round(s106_receipts + cil_receipts, 2)
            if s106_receipts is not None and cil_receipts is not None
            else None
        )
        s106_balance = span("s106_balance_retained")
        held = None
        if s106_balance and cil_unallocated_known:
            held = [
                round(s106_balance[0] + cil_unallocated, 2),
                round(s106_balance[1] + cil_unallocated, 2),
            ]

        agreed = (
            figures.get("s106_agreements_signed")
            or figures.get("s106_agreements_signed_including_variations")
            or {}
        )
        rows.append(
            {
                "financial_year": year,
                "s106_receipts_gbp": s106_receipts,
                "s106_balance_retained_gbp": (
                    s106_balance[0]
                    if s106_balance and s106_balance[0] == s106_balance[1]
                    else None
                ),
                # Only a real range. Emitting [x, x] for clean years made every
                # year look like the one with the misprinted figure.
                "s106_balance_retained_range_gbp": (
                    list(s106_balance) if s106_balance and s106_balance[0] != s106_balance[1] else None
                ),
                "s106_agreements_signed": agreed.get("count"),
                "s106_agreements_basis": agreed.get("basis"),
                "s106_future_receipts_gbp": agreed.get("value_gbp"),
                "s106_monitoring_cost_gbp": value("s106_monitoring_cost"),
                "cil_received_gbp": cil_receipts,
                "cil_received_to_date_gbp": (
                    round(cil_received_to_date, 2) if cil_received_to_date else None
                ),
                "cil_unallocated_gbp": round(cil_unallocated, 2) if cil_unallocated_known else None,
                "developer_contributions_received_gbp": received,
                "unspent_at_year_end_gbp": held[0] if held and held[0] == held[1] else None,
                "unspent_at_year_end_range_gbp": held if held and held[0] != held[1] else None,
                "source_page_balance": figures.get("s106_balance_retained", {}).get("page"),
                "source_file": record["source"]["file"],
                "source_sha256": record["source"]["sha256"],
            }
        )
    return rows


def build_trend(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    First-year versus last-year movement, computed once here rather than in the
    browser. Both ends are reported as ranges where the underlying published
    figure is a range.
    """
    usable = [r for r in rows if r.get("developer_contributions_received_gbp") is not None]
    if len(usable) < 2:
        return {}
    first, last = usable[0], usable[-1]

    def pct_change(a: float, b: float) -> float:
        return round((b - a) / a * 100, 1) if a else None

    out: dict[str, Any] = {
        "from_year": first["financial_year"],
        "to_year": last["financial_year"],
        "received_from_gbp": first["developer_contributions_received_gbp"],
        "received_to_gbp": last["developer_contributions_received_gbp"],
        "received_change_pct": pct_change(
            first["developer_contributions_received_gbp"], last["developer_contributions_received_gbp"]
        ),
    }
    held_from = first.get("unspent_at_year_end_gbp") or (
        first.get("unspent_at_year_end_range_gbp") or [None]
    )[0]
    held_to = last.get("unspent_at_year_end_gbp") or (
        last.get("unspent_at_year_end_range_gbp") or [None]
    )[0]
    if held_from and held_to:
        out["unspent_from_gbp"] = held_from
        out["unspent_to_gbp"] = held_to
        out["unspent_change_pct"] = pct_change(held_from, held_to)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="print instead of writing")
    parser.add_argument("--offline", action="store_true", help="use cached PDFs only")
    parser.add_argument(
        "--verify", action="store_true", help="print every quote the figures came from"
    )
    args = parser.parse_args()

    import json

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    years: dict[str, Any] = {}
    for year, url in SOURCES:
        filename = url.rsplit("/", 1)[-1]
        local = PDF_DIR / filename
        if local.exists():
            pdf_bytes = local.read_bytes()
            origin = "cache"
        elif args.offline:
            print(f"  ! {year}: no cached copy, skipping")
            continue
        else:
            try:
                pdf_bytes = download(url)
            except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
                print(f"  ! {year}: {exc}")
                if local.exists():
                    pdf_bytes = local.read_bytes()
                    origin = "cache after failure"
                else:
                    continue
            else:
                local.write_bytes(pdf_bytes)
                origin = "downloaded"
        try:
            record = extract_year(year, filename, pdf_bytes)
        except AmountError as exc:
            print(f"  ! {year}: unreadable figure — {exc}")
            continue
        years[year] = record
        flags = ""
        if record["checks"].get("cil_table1_rows_tie") is False:
            flags += " [CIL TABLE 1 DOES NOT TIE]"
        if record["corrections"]:
            flags += f" [{len(record['corrections'])} correction(s)]"
        print(f"  + {year} ({origin}) {len(record['figures'])} figures{flags}")

    series_rows = build_series(years)
    out = {
        "_comment": (
            "Every figure in this file is quoted from a Sheffield City Council "
            "Infrastructure Funding Statement, which the council must publish each "
            "year under the CIL (Amendment) (England) (No.2) Regulations 2019. "
            "Sheffield publishes nothing to the national planning-development-data "
            "dataset, so these statements are the only published account of what "
            "the council has received and what it is holding."
        ),
        "_generated": date.today().isoformat(),
        "_source_urls": {year: url for year, url in SOURCES},
        "_definitions": {
            "s106_receipts_in_year": "paragraph 4.10: money actually received in the year",
            "s106_balance_retained": "the council's own 'Current S106 Balance' paragraph",
            "s106_agreements_signed": (
                "paragraph 4.8. Not one measure across the six years: up to 2022/23 "
                "the wording is 'N S106 agreements were signed', from 2023/24 it is "
                "'N S106 agreements (including deed of variations and supplemental "
                "deeds)'. The counts either side of that change are not comparable."
            ),
            "cil_unallocated": (
                "Table 1: CIL received up to and including the reported year, less "
                "the amount allocated to projects"
            ),
            "unspent_at_year_end": (
                "derived: S106 balance retained plus unallocated CIL"
            ),
        },
        "authority": "Sheffield City Council",
        "years": years,
        "series": series_rows,
        "trend": build_trend(series_rows),
    }

    print()
    header = (
        f"{'year':8} {'S106 receipts':>15} {'agmts':>6} {'future receipts':>16} "
        f"{'S106 held':>16} {'CIL received':>14} {'CIL unalloc':>13}"
    )
    print(header)
    print("-" * len(header))

    def agreements(record: dict[str, Any]) -> dict[str, Any]:
        figures = record.get("figures", {})
        return (
            figures.get("s106_agreements_signed")
            or figures.get("s106_agreements_signed_including_variations")
            or {}
        )

    def cell(figure: Optional[dict[str, Any]], width: int) -> str:
        if not figure:
            return "—".rjust(width)
        value = figure.get("value_gbp")
        if value is not None:
            return f"{value:,.2f}".rjust(width)
        span = (figure.get("correction") or {}).get("range_gbp")
        if span:
            return f"{span[0] / 1e6:.2f}-{span[1] / 1e6:.2f}m".rjust(width)
        return "unreadable".rjust(width)

    for year in sorted(years):
        figures = years[year]["figures"]
        t1 = years[year].get("cil_cumulative_table", {})
        parts = [
            row.get("unallocated_gbp")
            for row in t1.values()
            if isinstance(row, dict) and row.get("unallocated_gbp") is not None
        ]
        unalloc = f"{sum(parts):,.2f}".rjust(13) if parts else "—".rjust(13)
        agreed = agreements(years[year])
        print(
            f"{year:8} {cell(figures.get('s106_receipts_in_year'), 15)} "
            f"{str(agreed.get('count', '—')):>6} {cell(agreed, 16)} "
            f"{cell(figures.get('s106_balance_retained'), 16)} "
            f"{cell(figures.get('cil_received_in_year'), 14)} {unalloc}"
        )

    if args.verify:
        for year in sorted(years):
            print(f"\n=== {year} ===")
            for name, figure in sorted(years[year]["figures"].items()):
                print(f"  {name:34} {figure.get('printed', figure.get('count', '')):>12}  p{figure['page']}")
                print(f"      {figure['quote'][:190]}")
            checks = years[year]["checks"]
            if checks:
                print(f"  checks: {json.dumps(checks)}")

    if args.check:
        print("\n--check: nothing written")
        return 0
    OUT_FILE.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwrote {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
