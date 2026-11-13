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
import urllib.parse
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


# Before the IFS existed, Sheffield published a CIL Annual Report. Different
# document, different layout each year, CIL only (no S106). Kept separate from
# the IFS series rather than blended into it, because the measures differ.
CIL_PRECURSOR_SOURCES: tuple[tuple[str, str], ...] = (
    (
        "2015/16",
        "https://www.sheffield.gov.uk/sites/default/files/docs/planning-and-development/cil/CIL%20Annual%20Report%202015-16.pdf",
    ),
    (
        "2016/17",
        "https://www.sheffield.gov.uk/sites/default/files/docs/planning-and-development/cil/Annual%20Report%202016-17.pdf",
    ),
    (
        "2017/18",
        "https://www.sheffield.gov.uk/sites/default/files/docs/planning-and-development/cil/CIL%20Annual%20Report%202017-18.pdf",
    ),
    (
        "2018/19",
        "https://www.sheffield.gov.uk/sites/default/files/docs/planning-and-development/cil/CIL%20Annual%20Report%202018-19.pdf",
    ),
)

# The last money literal on an anchored line is the Total (100%) column, because
# every one of these tables runs Strategic / Admin / Local / Total left to right.
PRECURSOR_ANCHORS: dict[str, dict[str, str]] = {
    "2015/16": {
        "received_in_year": "CIL Collected 2015/16",
        "received_to_date": "Total CIL Collected",
        "expenditure_to_date": "CIL Expenditure 2015/16",
        "paid_to_parishes": "CIL paid to Parishes",
        "balance_at_year_end": "CIL receipts carried forward to 2016/17",
    },
    "2016/17": {
        "received_in_year": "CIL Collected 2016/17",
        "received_to_date": "Total CIL Collected",
        "expenditure_to_date": "CIL Expenditure 2016/17",
        "paid_to_parishes": "CIL paid to Parishes",
        "balance_at_year_end": "CIL receipts carried forward to 2017/18",
    },
    "2017/18": {
        "received_in_year": "CIL Receipts 2017/18",
        "received_to_date": "Total CIL Received to 31/3/18",
        "expenditure_to_date": "Total CIL Expenditure to 31/3/18",
        "balance_at_year_end": "Balance - CIL Receipts minus Expenditure 31/3/18",
    },
    "2018/19": {
        "received_in_year": "CIL Receipts in 2018/19",
        "received_to_date": "Total CIL Received to 31/3/19",
        "expenditure_to_date": "Total CIL Expenditure to 31/3/19",
        "balance_at_year_end": "Balance - CIL Receipts minus Expenditure 31/3/19",
    },
}


# A row of table cells: money literals in a row, tolerating the footnote markers
# these reports scatter over their figures (£841,169.00**, £1,143.002).
AMOUNT_RUN = re.compile(
    r"(?:\s*" + AMOUNT + r"\s*(?:\*{1,3}|\d{1,2})?\s*)+", re.I
)


def row_amounts(flat: str, needle: str) -> list[str]:
    """
    The money literals on the table row that starts with `needle`.

    Anchoring on the label alone is not enough: these reports also mention the
    same phrases in narrative prose, and a prose match picks up whatever amounts
    happen to follow. So every occurrence is tried and the one that is followed by
    the longest unbroken run of money literals wins, which is the table row.
    """
    best: list[str] = []
    # A row label can be followed by a short parenthetical before its figures
    # ("Balance - CIL Receipts minus Expenditure 31/3/19 – (CIL Receipts
    # Retained) £6,185,812.72 ..."), so a little preamble is allowed, but only
    # enough of it that a prose mention still loses to the real table row.
    row_pattern = re.compile(r"[^£]{0,40}" + AMOUNT_RUN.pattern, re.I)
    for match in re.finditer(re.escape(needle), flat, re.I):
        window = flat[match.end() : match.end() + 200]
        run = row_pattern.match(window)
        if not run:
            continue
        amounts = re.findall(AMOUNT, run.group(0), re.I)
        if len(amounts) > len(best):
            best = amounts
        if len(amounts) >= 4:
            break
    return best


def extract_cil_precursor(
    year: str, filename: str, pdf_bytes: bytes
) -> Optional[dict[str, Any]]:
    """
    Read one pre-IFS CIL Annual Report.

    Each report is read from its own anchors rather than by one generic parser:
    the four documents lay their tables out differently, and an explicit anchor
    per document is easier to check than clever layout detection. The figure taken
    is always the last money literal on the anchored line, which is the Total
    (100%) column.
    """
    pages = page_texts(pdf_bytes)
    flat = to_words(" ".join(text for _, text in pages))
    anchors = PRECURSOR_ANCHORS[year]
    record: dict[str, Any] = {
        "financial_year": year,
        "source": {
            "file": filename,
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "publisher": "Sheffield City Council",
            "document": "CIL Annual Report (published before Infrastructure Funding Statements existed)",
        },
        "figures": {},
        "checks": {},
    }
    for field, anchor in anchors.items():
        needle = to_words(anchor)
        amounts = row_amounts(flat, needle)
        if not amounts:
            record["checks"][f"anchor_found:{field}"] = False
            continue
        value, note = parse_amount(amounts[-1])
        record["figures"][field] = {
            "printed": amounts[-1],
            "value_gbp": value,
            "columns_printed": amounts,
            "quote": needle,
        }
        if note:
            record["figures"][field]["correction"] = note

    received = record["figures"].get("received_to_date", {}).get("value_gbp")
    spent = record["figures"].get("expenditure_to_date", {}).get("value_gbp")
    parishes = record["figures"].get("paid_to_parishes", {}).get("value_gbp") or 0.0
    balance = record["figures"].get("balance_at_year_end", {}).get("value_gbp")
    if None not in (received, spent, balance):
        # The two earliest reports show money passed to parishes on its own line,
        # outside "expenditure"; the later ones fold it into expenditure instead.
        expected = round(received - spent - parishes, 2)
        record["checks"]["receipts_less_spend_equals_balance"] = {
            "expected": expected,
            "printed_balance": balance,
            "difference_gbp": round(expected - balance, 2),
            "ties": abs(expected - balance) < 0.01,
        }
    return record


def find_restatements(precursor: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Where a report restates a cumulative figure the previous year already printed.

    Sheffield's own CIL time series is not internally consistent: the amount
    reported as "expenditure up to 31/3/<n>" went up between the report that
    first printed it and the one that repeated it. That is the council's own
    published history disagreeing with itself, so it is recorded rather than
    reconciled.
    """
    by_year = {row["financial_year"]: row for row in precursor}
    found: list[dict[str, Any]] = []
    for later, earlier in (("2017/18", "2016/17"), ("2018/19", "2017/18")):
        if later not in by_year or earlier not in by_year:
            continue
        # The restated figure is the "expenditure up to the end of <earlier>"
        # line, which only appears in the later report as a comparative.
        restated = _comparative_expenditure(by_year[later], earlier.split("/")[1])
        original = by_year[earlier]["figures"].get("expenditure_to_date", {}).get("value_gbp")
        if restated is None or original is None:
            continue
        if abs(restated - original) < 0.01:
            continue
        found.append(
            {
                "figure": f"CIL expenditure up to 31/3/{earlier.split('/')[1]}",
                "first_printed_in": earlier,
                "first_printed_value_gbp": original,
                "restated_in": later,
                "restated_value_gbp": restated,
                "uplift_gbp": round(restated - original, 2),
            }
        )
    return found


def _comparative_expenditure(record: dict[str, Any], end_of: str) -> Optional[float]:
    """
    The 'CIL Expenditure up to 31/3/<end_of>' comparative printed by a later report.

    The Total (100%) column is the last money literal on the row, not the first:
    the first is the Strategic CIL column, and reading it as the total understates
    the figure by hundreds of thousands of pounds.
    """
    import pymupdf

    path = PDF_DIR / record["source"]["file"]
    if not path.exists():
        return None
    flat = to_words(" ".join(p.get_text() for p in pymupdf.open(str(path))))
    amounts = row_amounts(flat, to_words(f"CIL Expenditure up to 31/3/{end_of}"))
    if not amounts:
        return None
    value, _ = parse_amount(amounts[-1])
    return value


def fetch_pdf(url: str, offline: bool) -> Optional[bytes]:
    """Cached-first download. A cached copy is used even when the network works."""
    filename = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    local = PDF_DIR / filename
    if local.exists():
        return local.read_bytes()
    if offline:
        return None
    try:
        pdf_bytes = download(url)
    except (urllib.error.URLError, RuntimeError, TimeoutError):
        return None
    local.write_bytes(pdf_bytes)
    return pdf_bytes


# External figures that either corroborate Sheffield's own numbers or explain why
# a place you would expect to find them does not have them. Each one says how it
# was checked. Anything not read out of a primary document by this repository is
# marked, and is not used in any calculation.
CROSS_CHECKS: dict[str, Any] = {
    "corroborating": [
        {
            "id": "s106tracker",
            "claim": "S106 received £1.2m, spent £4.1m, held £15.3m; CIL received £2.6m, spent £1.6m, held £9.2m, all for 2024/25",
            "url": "https://s106tracker.co.uk/council/sheffield",
            "verification": "read directly from the page by this repository",
            "what_it_actually_is": (
                "Not independent. The page attributes every figure to 'Sheffield's "
                "latest Infrastructure Funding Statement (2024/25)', and each one "
                "matches the IFS to the rounding. It is a republication. Its one "
                "useful signal: an independent extraction of the same PDF also read "
                "the misprinted balance as £15.3m rather than £153m."
            ),
        },
        {
            "id": "cambridge_cil_benchmark",
            "claim": "Sheffield has collected £33m of CIL in total since charging began on 15 July 2015 (rank 51); nationally 47% of all CIL ever raised was unspent by 2025 (£2.2bn), against 42% up to 2023-24, implying a spend lag of just under four years",
            "url": "https://www.landecon.cam.ac.uk/sites/default/files/2026-09/cil_research_final_report.pdf",
            "verification": "read directly from the report PDF by this repository",
            "why_it_matters": (
                "Sheffield's unallocated CIL of £9.2m is about 28% of its £33m "
                "lifetime collections, below the 47% national figure, so on CIL at "
                "least Sheffield spends faster than the average authority. The "
                "report also warns its unspent figures come from HBF FOI data of "
                "'doubtful value', so this is a rough benchmark, not a verdict."
            ),
        },
    ],
    "cannot_corroborate": [
        {
            "id": "statement_of_accounts",
            "finding": "Sheffield's audited accounts do not disclose Section 106 or CIL as a separate line, so they can neither confirm nor contradict the IFS balances",
            "url": "https://www.sheffield.gov.uk/sites/default/files/2026-03/sheffield-city-council-audited-statement-of-accounts-2024-2025.pdf",
            "verification": "full-text searched by this repository: 190 pages, zero occurrences of 'Section 106' or 'S106'; 'Community Infrastructure Levy' appears only in two service-variance explanations (£170k, £14k) and the standard accounting-policy note",
            "where_it_is_buried": (
                "Developer contributions sit inside 'Capital grants and contributions "
                "credited to the CI&ES' (Note 8), which for 2024/25 is £94,923k, and "
                "in the Capital Grants Unapplied Reserve, which also holds every "
                "other unapplied capital grant. Both are aggregates over all grants, "
                "so neither is a developer-contribution figure."
            ),
        },
        {
            "id": "symca",
            "finding": "The South Yorkshire Mayoral Combined Authority, the body Sheffield sits inside, publishes no developer-contribution figures at all",
            "url": "https://www.southyorkshire-ca.gov.uk/",
            "verification": "reported by a research pass over SYMCA's audited accounts 2022/23 to 2024/25, auditor's annual reports and capital investment programme reports; not independently re-run by this repository",
            "why": (
                "Combined authorities are not charging or collecting authorities for "
                "CIL, and are not a local planning authority, so they neither sign "
                "S106 deeds nor levy CIL. Sheffield's developer-contribution "
                "position is entirely a Sheffield City Council position."
            ),
        },
    ],
    "unresolved": [
        {
            "id": "clawback_contradiction",
            "finding": "Sheffield told an FOI requester on 31 October 2024 that it held no S106 contributions past their clawback date, then on 30 September 2026 disclosed a spreadsheet of unspent S106 contributions that had passed their expiry dates",
            "url": "https://www.whatdotheyknow.com/request/repayment_of_s106_contributions_250",
            "verification": "NOT VERIFIED. WhatDoTheyKnow is behind Cloudflare and returns 403 to this repository. The attachments (EIR 3286690.xlsx, Book2.xlsx, EIR 2695294.xlsx) could not be downloaded.",
            "why_it_matters": (
                "If the 2026 spreadsheet is not empty then money sat past the point "
                "at which it had to be offered back, which is the single strongest "
                "finding available on Sheffield's S106 practice. It needs a browser "
                "to read, so it is flagged here rather than asserted."
            ),
        },
        {
            "id": "council_answer_2021",
            "finding": "A written answer at Full Council on 8 September 2021 put unspent S106 at £19.4m, of which £14.7m was committed, across 234 wholly or partially unspent agreements",
            "url": "https://democracy.sheffield.gov.uk/documents/b24463/Members%20Questions%20Wednesday%2008-Sep-2021%2014.00%20Council.pdf",
            "verification": "NOT VERIFIED. democracy.sheffield.gov.uk returns 403 to this repository. Reported by a research pass; consistent with the IFS balance of £18,958,520 at 31 March 2021, which is why it is worth chasing.",
        },
    ],
}


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

    precursor: list[dict[str, Any]] = []
    for year, url in CIL_PRECURSOR_SOURCES:
        pdf_bytes = fetch_pdf(url, args.offline)
        if not pdf_bytes:
            print(f"  ! CIL report {year}: unavailable, skipping")
            continue
        try:
            record = extract_cil_precursor(year, urllib.parse.unquote(url.rsplit("/", 1)[-1]), pdf_bytes)
        except AmountError as exc:
            print(f"  ! CIL report {year}: unreadable figure — {exc}")
            continue
        if record:
            precursor.append(record)
            tied = record["checks"].get("receipts_less_spend_equals_balance", {})
            flag = "" if tied.get("ties", True) else " [DOES NOT TIE]"
            print(f"  + CIL annual report {year}: balance {record['figures'].get('balance_at_year_end', {}).get('value_gbp')}{flag}")

    restatements = find_restatements(precursor)
    for item in restatements:
        print(
            f"  ! restatement: {item['figure']} went from "
            f"£{item['first_printed_value_gbp']:,.2f} to £{item['restated_value_gbp']:,.2f} "
            f"(+£{item['uplift_gbp']:,.2f})"
        )

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
        "cil_precursor": precursor,
        "restatements": restatements,
        "restatement_total_gbp": round(sum(r["uplift_gbp"] for r in restatements), 2),
        "cross_checks": CROSS_CHECKS,
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
