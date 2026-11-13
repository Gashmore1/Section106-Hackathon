"""
Online Dataset Downloader & Scraper for the Sheffield Section 106 Autonomous Auditor.

Downloads and normalizes real public datasets across all 3 required categories:
1. Real Unstructured Section 106 Legal PDFs (`data/fixtures/pdfs/real/`):
   - Queries UK `planning.data.gov.uk` developer-agreement entities with live `document-url` PDFs
   - Scrapes Sheffield City Council's Idox Planning Portal (`planningapps.sheffield.gov.uk`)
     and `sheffield.gov.uk/planning-development/planning-obligations` for S106 templates & deeds
2. Structured Ground-Truth CSVs (`data/fixtures/csv/national/`):
   - `https://files.planning.data.gov.uk/dataset/developer-agreement.csv`
   - `https://files.planning.data.gov.uk/dataset/developer-agreement-contribution.csv`
   - `https://files.planning.data.gov.uk/dataset/developer-agreement-transaction.csv`
3. Spatial & Neighbourhood Allocation + ONS Inflation Datasets (`data/fixtures/spatial/` & `data/fixtures/inflation/`):
   - Official ONS RPI (`CHAW`) and CPI (`D7BT`) time series from `ons.gov.uk`
   - Official Sheffield 28 Electoral Wards GeoJSON from ONS Open Geography ArcGIS FeatureServer
   - Sheffield City Council Open Data ArcGIS Hub catalog search (CIL Charging Zones, HELAA, Wards)

Usage:
    python3 -m scripts.fetch_real_datasets --all
    python3 -m scripts.fetch_real_datasets --csv --ons --spatial --pdfs
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import json
import re
import ssl
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT_DIR / "data" / "fixtures"
REAL_PDF_DIR = FIXTURES_DIR / "pdfs" / "real"
NATIONAL_CSV_DIR = FIXTURES_DIR / "csv" / "national"
INFLATION_DIR = FIXTURES_DIR / "inflation"
SPATIAL_DIR = FIXTURES_DIR / "spatial"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 SheffieldS106Auditor/1.0"
)


def _http_get(url: str, timeout: int = 30) -> bytes:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# 1. Structured Ground-Truth CSVs (planning.data.gov.uk)
# ---------------------------------------------------------------------------

PLANNING_DATA_CSV_URLS: dict[str, str] = {
    "developer-agreement.csv": "https://files.planning.data.gov.uk/dataset/developer-agreement.csv",
    "developer-agreement-contribution.csv": "https://files.planning.data.gov.uk/dataset/developer-agreement-contribution.csv",
    "developer-agreement-transaction.csv": "https://files.planning.data.gov.uk/dataset/developer-agreement-transaction.csv",
}


def download_planning_data_csvs() -> list[Path]:
    """Download the 3 national statutory Developer Contribution CSVs from planning.data.gov.uk."""
    NATIONAL_CSV_DIR.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []

    for filename, url in PLANNING_DATA_CSV_URLS.items():
        out_path = NATIONAL_CSV_DIR / filename
        print(f"[csv] Downloading {url} -> {out_path.relative_to(ROOT_DIR)} ...")
        try:
            raw = _http_get(url, timeout=45)
            out_path.write_bytes(raw)
            line_count = raw.count(b"\n")
            print(f"  [ok] Saved {filename} ({len(raw):,} bytes, ~{line_count:,} rows)")
            downloaded.append(out_path)
        except Exception as exc:
            print(f"  [warn] Failed to download {filename}: {exc}")

    return downloaded


# ---------------------------------------------------------------------------
# 2. Real Unstructured S106 PDFs (Sheffield Portal + National S106 PDF Links)
# ---------------------------------------------------------------------------


def download_real_s106_pdfs(max_pdfs: int = 5) -> list[Path]:
    """
    1) Check `sheffield.gov.uk/planning-development/planning-obligations` for template/guidance PDFs.
    2) Query `planning.data.gov.uk/entity.json?dataset=developer-agreement` for direct `.pdf` links
       to signed council Section 106 agreements and download up to `max_pdfs` real S106 PDFs.
    3) Provide a ready-to-run helper for Sheffield's Idox portal (`planningapps.sheffield.gov.uk`).
    """
    REAL_PDF_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    # Step A: Check Sheffield Planning Obligations page for PDF links
    sheffield_obligations_url = "https://www.sheffield.gov.uk/planning-development/planning-obligations"
    print(f"[pdfs] Scanning {sheffield_obligations_url} for Sheffield S106 templates/guidance...")
    try:
        html = _http_get(sheffield_obligations_url, timeout=20).decode("utf-8", errors="ignore")
        pdf_links = re.findall(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', html, flags=re.IGNORECASE)
        for href in pdf_links[:5]:
            full_url = urllib.parse.urljoin(sheffield_obligations_url, href)
            fname = Path(urllib.parse.urlparse(full_url).path).name or "sheffield_s106_doc.pdf"
            target = REAL_PDF_DIR / f"sheffield_{fname}"
            try:
                data = _http_get(full_url, timeout=25)
                if data.startswith(b"%PDF"):
                    target.write_bytes(data)
                    print(f"  [ok] Downloaded Sheffield PDF: {target.name} ({len(data):,} bytes)")
                    saved.append(target)
            except Exception as e:
                print(f"  [skip] {full_url}: {e}")
    except Exception as exc:
        print(f"  [info] Could not scrape sheffield.gov.uk directly ({exc})")

    # Step B: Parse local `developer-agreement.csv` for direct `.pdf` document-urls and download real S106 PDFs
    local_agr_csv = NATIONAL_CSV_DIR / "developer-agreement.csv"
    if local_agr_csv.exists():
        print(f"[pdfs] Scanning {local_agr_csv.relative_to(ROOT_DIR)} for live signed S106 PDF agreements...")
        with local_agr_csv.open("r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if len(saved) >= max_pdfs:
                    break
                doc_url = (row.get("document-url") or "").strip()
                if not doc_url.startswith("http") or ".pdf" not in doc_url.lower():
                    continue
                # Handle concatenated URLs if present
                if "http" in doc_url[8:]:
                    doc_url = doc_url[: doc_url.find("http", 8)]
                ref = re.sub(r"[^A-Za-z0-9_-]+", "_", str(row.get("reference") or row.get("entity") or "s106"))
                out_file = REAL_PDF_DIR / f"real_s106_{ref[:40]}.pdf"
                if out_file.exists() and out_file.stat().st_size > 5_000:
                    saved.append(out_file)
                    continue
                try:
                    pdf_bytes = _http_get(doc_url, timeout=15)
                    if pdf_bytes.startswith(b"%PDF") and len(pdf_bytes) > 5_000:
                        out_file.write_bytes(pdf_bytes)
                        print(
                            f"  [ok] Downloaded real S106 deed: {out_file.name} "
                            f"({len(pdf_bytes):,} bytes) from {doc_url}"
                        )
                        saved.append(out_file)
                except Exception:
                    continue
    return saved


def search_sheffield_idox_major_applications(reference_query: str = "17/04673/OUT") -> dict[str, Any]:
    """
    Helper to query Sheffield City Council's Idox Public Access Planning Portal
    (`https://planningapps.sheffield.gov.uk/online-applications/`) using a cookie session.
    Note: Idox portals enforce session CSRF tokens (`_csrf`) on POST search forms.
    """
    jar = http.cookiejar.CookieJar()
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=ctx),
    )
    opener.addheaders = [("User-Agent", USER_AGENT)]

    search_url = (
        "https://planningapps.sheffield.gov.uk/online-applications/"
        f"simpleSearchResults.do?action=firstPage&searchType=Application"
        f"&searchCriteria.simpleSearchString={urllib.parse.quote(reference_query)}"
    )
    try:
        with opener.open(search_url, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            key_vals = re.findall(r"keyVal=([A-Z0-9]+)", body)
            return {
                "query": reference_query,
                "status": resp.status,
                "idox_key_vals": list(dict.fromkeys(key_vals)),
                "documents_tab_urls": [
                    f"https://planningapps.sheffield.gov.uk/online-applications/applicationDetails.do?activeTab=documents&keyVal={kv}"
                    for kv in dict.fromkeys(key_vals)
                ],
            }
    except Exception as exc:
        return {"query": reference_query, "error": str(exc)}


# ---------------------------------------------------------------------------
# 3. ONS Inflation Indices (RPI CHAW & CPI D7BT)
# ---------------------------------------------------------------------------

ONS_SERIES_ENDPOINTS: dict[str, str] = {
    "RPI": "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/chaw/mm23/data",
    "CPI": "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7bt/mm23/data",
}


def download_ons_inflation_series() -> Path | None:
    """
    Download official ONS MM23 time series JSON for RPI (`CHAW`) and CPI (`D7BT`)
    and write a unified `data/fixtures/inflation/ons_live_rpi_cpi.csv` table.
    """
    INFLATION_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = INFLATION_DIR / "ons_live_rpi_cpi.csv"
    records: list[dict[str, Any]] = []

    month_map = {
        "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
        "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
        "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
    }

    for method, url in ONS_SERIES_ENDPOINTS.items():
        print(f"[ons] Fetching ONS {method} time series from {url} ...")
        try:
            data = json.loads(_http_get(url, timeout=30).decode("utf-8"))
            series_id = data.get("description", {}).get("cdid", method)
            for item in data.get("months", []):
                year = str(item.get("year", ""))
                mon_name = str(item.get("month", "")).upper()[:3]
                val_str = str(item.get("value", "")).strip()
                if not (year.isdigit() and int(year) >= 2015 and mon_name in month_map and val_str):
                    continue
                records.append(
                    {
                        "method": method,
                        "series_id": series_id,
                        "effective_date": f"{year}-{month_map[mon_name]}-01",
                        "index_value": float(val_str),
                    }
                )
            print(f"  [ok] Parsed {method} ({series_id}) monthly observations since 2015")
        except Exception as exc:
            print(f"  [warn] ONS {method} fetch failed: {exc}")

    if records:
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["method", "series_id", "effective_date", "index_value"])
            writer.writeheader()
            writer.writerows(records)
        print(f"  [ok] Saved {len(records)} official ONS rows to {out_csv.relative_to(ROOT_DIR)}")
        return out_csv
    return None


# ---------------------------------------------------------------------------
# 4. Spatial & Neighbourhood Datasets (Sheffield 28 Wards + ArcGIS Hub)
# ---------------------------------------------------------------------------

# Official UK GeoJSON Electoral Wards for Sheffield (E08000019)
ONS_SHEFFIELD_WARDS_GEOJSON_URL = (
    "https://raw.githubusercontent.com/martinjc/UK-GeoJSON/master/json/electoral/eng/wards_by_lad/E08000019.json"
)

SHEFFIELD_ARCGIS_HUB_SEARCH_URL = (
    "https://sheffield-city-council-open-data-sheffieldcc.hub.arcgis.com/api/v3/datasets?"
    "q=Sheffield&page[size]=25"
)


def download_sheffield_spatial_datasets() -> list[Path]:
    """Download official Sheffield 28 Electoral Wards GeoJSON and Sheffield ArcGIS Open Data catalog."""
    SPATIAL_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    wards_out = SPATIAL_DIR / "sheffield_28_wards_ons_boundaries.geojson"
    print(f"[spatial] Downloading Sheffield 28 Electoral Wards GeoJSON from ONS ArcGIS FeatureServer...")
    try:
        raw = _http_get(ONS_SHEFFIELD_WARDS_GEOJSON_URL, timeout=35)
        parsed = json.loads(raw.decode("utf-8"))
        feats = parsed.get("features", [])
        wards_out.write_bytes(raw)
        print(f"  [ok] Saved {wards_out.name} ({len(raw):,} bytes, {len(feats)} ward polygons)")
        saved.append(wards_out)
    except Exception as exc:
        print(f"  [warn] ONS ArcGIS Ward fetch failed: {exc}")

    hub_out = SPATIAL_DIR / "sheffield_open_data_hub_catalog.json"
    print(f"[spatial] Querying Sheffield City Council ArcGIS Open Data Hub API...")
    try:
        raw_hub = _http_get(SHEFFIELD_ARCGIS_HUB_SEARCH_URL, timeout=25)
        hub_data = json.loads(raw_hub.decode("utf-8"))
        items = [
            {
                "id": d.get("id"),
                "title": d.get("attributes", {}).get("name"),
                "type": d.get("attributes", {}).get("type"),
                "url": d.get("attributes", {}).get("url"),
                "slug": d.get("attributes", {}).get("slug"),
            }
            for d in hub_data.get("data", [])
        ]
        hub_out.write_text(json.dumps(items, indent=2), encoding="utf-8")
        print(f"  [ok] Saved {len(items)} Sheffield Open Data Hub layer descriptors to {hub_out.name}")
        saved.append(hub_out)
    except Exception as exc:
        print(f"  [warn] Sheffield Open Data Hub catalog query failed: {exc}")

    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Download real S106, CSV, ONS, and Spatial datasets.")
    parser.add_argument("--all", action="store_true", help="Download all dataset categories")
    parser.add_argument("--csv", action="store_true", help="Download national planning.data.gov.uk CSVs")
    parser.add_argument("--pdfs", action="store_true", help="Download real S106 PDFs")
    parser.add_argument("--ons", action="store_true", help="Download live ONS RPI & CPI inflation series")
    parser.add_argument("--spatial", action="store_true", help="Download Sheffield Ward & ArcGIS spatial data")
    args = parser.parse_args()

    run_all = args.all or not (args.csv or args.pdfs or args.ons or args.spatial)

    if run_all or args.csv:
        download_planning_data_csvs()
    if run_all or args.ons:
        download_ons_inflation_series()
    if run_all or args.spatial:
        download_sheffield_spatial_datasets()
    if run_all or args.pdfs:
        download_real_s106_pdfs(max_pdfs=5)


if __name__ == "__main__":
    main()
