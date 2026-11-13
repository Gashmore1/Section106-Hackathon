"""
Generate realistic multi-page Sheffield Section 106 PDF deeds in `data/fixtures/pdfs/`,
statutory ground-truth CSVs in `data/fixtures/csv/`, ONS/BCIS inflation CSVs in
`data/fixtures/inflation/`, and Sheffield Ward / CIL / Neighbourhood allocation GeoJSONs
in `data/fixtures/spatial/`.

Uses PyMuPDF (`fitz`) when installed, with an automatic pure-Python PDF 1.4 writer fallback
so fixtures can be generated in any environment with zero external dependencies.

Usage:
    python3 -m scripts.generate_s106_pdf_fixtures
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT_DIR / "data" / "fixtures"
PDF_FIXTURES_DIR = FIXTURES_DIR / "pdfs"
CSV_FIXTURES_DIR = FIXTURES_DIR / "csv"
INFLATION_FIXTURES_DIR = FIXTURES_DIR / "inflation"
SPATIAL_FIXTURES_DIR = FIXTURES_DIR / "spatial"


# ---------------------------------------------------------------------------
# Canonical Seed Fixtures (mirrors data/seed_db.py without requiring SQLAlchemy)
# ---------------------------------------------------------------------------

AGREEMENTS: list[dict[str, Any]] = [
    {
        "planning_reference": "21/04512/FUL",
        "agreement_date": dt.date(2021, 1, 1),
        "site_name": "Kelham Riverside Foundry Quarter, Green Lane, Sheffield S3 8SJ",
        "ward_name": "Walkley",
        "developer_name": "Hallamshire Urban Regeneration Developments Ltd",
        "total_dwellings_permitted": 240,
        "dwellings_occupied_current": 112,
        "document_url": "https://planningapps.sheffield.gov.uk/online-applications/21-04512-FUL-S106.pdf",
    },
    {
        "planning_reference": "20/01984/OUT",
        "agreement_date": dt.date(2020, 1, 1),
        "site_name": "West Bar Gateway Tower Phase 2, Corporation Street, Sheffield S3 8RB",
        "ward_name": "City",
        "developer_name": "Pennine Commercial Estates PLC",
        "total_dwellings_permitted": 360,
        "dwellings_occupied_current": 195,
        "document_url": "https://planningapps.sheffield.gov.uk/online-applications/20-01984-OUT-S106.pdf",
    },
    {
        "planning_reference": "22/03110/FUL",
        "agreement_date": dt.date(2022, 1, 1),
        "site_name": "Banner Cross Parklands, Ecclesall Road South, Sheffield S11 9PA",
        "ward_name": "Ecclesall",
        "developer_name": "Peak District Homes (Yorkshire) Ltd",
        "total_dwellings_permitted": 85,
        "dwellings_occupied_current": 64,
        "document_url": "https://planningapps.sheffield.gov.uk/online-applications/22-03110-FUL-S106.pdf",
    },
]

OBLIGATIONS: list[dict[str, Any]] = [
    {
        "obligation_id": "OBL-21-04512-01",
        "planning_reference": "21/04512/FUL",
        "category": "AFFORDABLE_HOUSING",
        "contribution_purpose": "affordable-housing",
        "title": "Off-Site Affordable Housing Commuted Sum (First Tranche)",
        "base_amount_gbp": 450000.0,
        "indexed_amount_gbp": 548602.94,
        "indexation_method": "BCIS",
        "index_base_date": dt.date(2021, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 50th Market Dwelling on the Land",
        "dwelling_threshold": 50,
        "amount_received_gbp": 450000.0,
        "clause_reference": "Schedule 1, Part 2, Paragraph 4.1",
        "verbatim_quote": (
            "The Owner covenants with the Council to pay 50% of the Affordable Housing "
            "Commuted Sum (£450,000 Index Linked in accordance with the BCIS All-in Tender "
            "Price Index from the date of this Deed) prior to the Occupation of the 50th "
            "Market Dwelling constructed on the Site."
        ),
        "page_number": 18,
        "bbox": {"page_number": 18, "x0": 72.0, "y0": 215.0, "x1": 523.0, "y1": 295.0},
    },
    {
        "obligation_id": "OBL-21-04512-02",
        "planning_reference": "21/04512/FUL",
        "category": "AFFORDABLE_HOUSING",
        "contribution_purpose": "affordable-housing",
        "title": "Off-Site Affordable Housing Commuted Sum (Second Tranche)",
        "base_amount_gbp": 450000.0,
        "indexed_amount_gbp": 548602.94,
        "indexation_method": "BCIS",
        "index_base_date": dt.date(2021, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 100th Market Dwelling on the Land",
        "dwelling_threshold": 100,
        "amount_received_gbp": 0.0,
        "clause_reference": "Schedule 1, Part 2, Paragraph 4.2",
        "verbatim_quote": (
            "The Owner covenants with the Council to pay the remaining 50% balance of the "
            "Affordable Housing Commuted Sum (£450,000 Index Linked in accordance with the "
            "BCIS Index) prior to the Occupation of the 100th Market Dwelling."
        ),
        "page_number": 18,
        "bbox": {"page_number": 18, "x0": 72.0, "y0": 310.0, "x1": 523.0, "y1": 388.0},
    },
    {
        "obligation_id": "OBL-21-04512-03",
        "planning_reference": "21/04512/FUL",
        "category": "EDUCATION",
        "contribution_purpose": "education",
        "title": "Secondary School Places Expansion Contribution",
        "base_amount_gbp": 285000.0,
        "indexed_amount_gbp": 378452.14,
        "indexation_method": "RPI",
        "index_base_date": dt.date(2021, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 75th Dwelling",
        "dwelling_threshold": 75,
        "amount_received_gbp": 0.0,
        "clause_reference": "Schedule 2, Paragraph 2.1",
        "verbatim_quote": (
            "Not to Occupy or permit the Occupation of more than 74 Dwellings until the "
            "Education Contribution of £285,000 (Index Linked by RPI from January 2021) "
            "has been paid in full to the Council."
        ),
        "page_number": 24,
        "bbox": {"page_number": 24, "x0": 72.0, "y0": 140.0, "x1": 523.0, "y1": 210.0},
    },
    {
        "obligation_id": "OBL-21-04512-04",
        "planning_reference": "21/04512/FUL",
        "category": "OPEN_SPACE_AND_BIODIVERSITY",
        "contribution_purpose": "open-space",
        "title": "River Don Corridor Biodiversity Net Gain & Pocket Park Sum",
        "base_amount_gbp": 120000.0,
        "indexed_amount_gbp": 149064.22,
        "indexation_method": "CPI",
        "index_base_date": dt.date(2021, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 180th Dwelling",
        "dwelling_threshold": 180,
        "amount_received_gbp": 0.0,
        "clause_reference": "Schedule 3, Paragraph 1.3",
        "verbatim_quote": (
            "To pay the Open Space and River Don Corridor Enhancement Contribution of "
            "£120,000 (CPI Index Linked) prior to Occupation of the 180th Dwelling."
        ),
        "page_number": 31,
        "bbox": {"page_number": 31, "x0": 72.0, "y0": 410.0, "x1": 523.0, "y1": 475.0},
    },
    {
        "obligation_id": "OBL-20-01984-01",
        "planning_reference": "20/01984/OUT",
        "category": "HIGHWAYS_AND_TRANSPORT",
        "contribution_purpose": "transport-and-travel",
        "title": "West Bar Active Travel & Bus Rapid Transit Junction Works",
        "base_amount_gbp": 390000.0,
        "indexed_amount_gbp": 486912.65,
        "indexation_method": "BCIS",
        "index_base_date": dt.date(2020, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 150th Residential Unit",
        "dwelling_threshold": 150,
        "amount_received_gbp": 390000.0,
        "clause_reference": "Schedule 2, Paragraph 5.1",
        "verbatim_quote": (
            "The Developer shall pay the Highways & Active Travel Contribution of £390,000 "
            "(BCIS Index Linked from January 2020) prior to Occupation of the 150th Unit."
        ),
        "page_number": 22,
        "bbox": {"page_number": 22, "x0": 72.0, "y0": 180.0, "x1": 523.0, "y1": 248.0},
    },
    {
        "obligation_id": "OBL-22-03110-01",
        "planning_reference": "22/03110/FUL",
        "category": "HEALTHCARE",
        "contribution_purpose": "health",
        "title": "Sheffield NHS ICB Primary Care Consulting Rooms Expansion",
        "base_amount_gbp": 165000.0,
        "indexed_amount_gbp": 203172.80,
        "indexation_method": "RPI",
        "index_base_date": dt.date(2022, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 50th Dwelling",
        "dwelling_threshold": 50,
        "amount_received_gbp": 0.0,
        "clause_reference": "Schedule 4, Paragraph 1.1",
        "verbatim_quote": (
            "The Owner shall pay the Healthcare Infrastructure Contribution of £165,000 "
            "(RPI Index Linked) prior to Occupation of the 50th Dwelling."
        ),
        "page_number": 29,
        "bbox": {"page_number": 29, "x0": 72.0, "y0": 260.0, "x1": 523.0, "y1": 330.0},
    },
]


# ---------------------------------------------------------------------------
# Pure-Python Minimal PDF 1.4 Writer (Zero External Dependencies)
# ---------------------------------------------------------------------------


def _escape_pdf_text(s: str) -> str:
    return (
        s.replace("£", "GBP ")
        .replace("—", "-")
        .replace("–", "-")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _write_simple_pdf(pages_lines: list[list[tuple[float, float, float, str]]], out_path: Path) -> None:
    """
    Write a valid multi-page PDF 1.4 file where each page is a list of
    `(x, y_top_down, font_size, text_line)` tuples.
    Compatible with PyMuPDF (`fitz`), pdfplumber, and browser PDF viewers.
    """
    objects: list[bytes] = []

    def add_obj(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    # 1: Catalog, 2: Pages, 3: Font (Helvetica)
    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(b"")  # placeholder for Pages
    font_obj_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_obj_ids: list[int] = []
    page_height = 842.0

    for lines in pages_lines:
        stream_parts: list[str] = ["BT"]
        for x, y_top, size, text_line in lines:
            y_pdf = round(page_height - y_top, 2)
            escaped = _escape_pdf_text(text_line)
            stream_parts.append(f"/F1 {size:.1f} Tf 1 0 0 1 {x:.2f} {y_pdf:.2f} Tm ({escaped}) Tj")
        stream_parts.append("ET")
        stream_bytes = "\n".join(stream_parts).encode("latin-1", errors="replace")
        content_id = add_obj(
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream"
        )
        page_id = add_obj(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_obj_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode("ascii")
        )
        page_obj_ids.append(page_id)

    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_obj_ids)} >>".encode("ascii")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for idx, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")

    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(out))


def _wrap_text(text: str, width: int = 82) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    curr_len = 0
    for w in words:
        if curr_len + len(w) + (1 if current else 0) > width:
            lines.append(" ".join(current))
            current = [w]
            curr_len = len(w)
        else:
            current.append(w)
            curr_len += len(w) + (1 if len(current) > 1 else 0)
    if current:
        lines.append(" ".join(current))
    return lines


def generate_pdf_fixtures() -> list[Path]:
    """Generate all S106 agreement PDFs + Sheffield Standard S106 Template + test PDFs."""
    PDF_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    for agr in AGREEMENTS:
        ref = agr["planning_reference"]
        slug = ref.replace("/", "-")
        out_path = PDF_FIXTURES_DIR / f"{slug}-S106.pdf"
        obls = [o for o in OBLIGATIONS if o["planning_reference"] == ref]
        max_page = max((int(o["page_number"]) for o in obls), default=10)
        total_pages = max(max_page + 2, 20)

        obls_by_page: dict[int, list[dict[str, Any]]] = {}
        for o in obls:
            obls_by_page.setdefault(int(o["page_number"]), []).append(o)

        pages_lines: list[list[tuple[float, float, float, str]]] = []
        for pno in range(1, total_pages + 1):
            lines: list[tuple[float, float, float, str]] = [
                (72.0, 42.0, 8.5, f"SHEFFIELD CITY COUNCIL - SECTION 106 DEED OF AGREEMENT | REF: {ref}"),
                (72.0, 805.0, 8.5, f"Page {pno} of {total_pages} | Deed Date: {agr['agreement_date'].isoformat()} | Ward: {agr['ward_name']}"),
            ]
            if pno == 1:
                cover = [
                    f"DATED: {agr['agreement_date'].isoformat()}",
                    "SHEFFIELD CITY COUNCIL (1) and " + agr["developer_name"].upper() + " (2)",
                    "PLANNING OBLIGATION BY DEED OF AGREEMENT PURSUANT TO SECTION 106 TCPA 1990",
                    f"Planning Application Reference: {ref}",
                    f"Development Site: {agr['site_name']}",
                    f"Electoral Ward: {agr['ward_name']} | Permitted Dwellings: {agr['total_dwellings_permitted']}",
                    "1. DEFINITIONS AND INTERPRETATION",
                    "'BCIS Index' means the All-in Tender Price Index published by BCIS / RICS.",
                    "'RPI' means the Retail Prices Index (All Items, ONS Series CHAW).",
                    "'CPI' means the Consumer Prices Index (All Items, ONS Series D7BT).",
                    f"'Index Linked' means increased in proportion to the Index from {agr['agreement_date'].isoformat()}.",
                ]
                y = 95.0
                for c in cover:
                    lines.append((72.0, y, 10.0, c))
                    y += 24.0
            elif pno in obls_by_page:
                lines.append((72.0, 80.0, 11.0, f"SCHEDULE OF DEVELOPER COVENANTS ({ref})"))
                for obl in obls_by_page[pno]:
                    bbox = obl.get("bbox") or {}
                    y = float(bbox.get("y0", 200.0))
                    lines.append(
                        (
                            72.0,
                            y,
                            10.0,
                            f"{obl['clause_reference']} - {obl['title']} [{obl['obligation_id']}]",
                        )
                    )
                    y += 16.0
                    lines.append(
                        (
                            72.0,
                            y,
                            9.5,
                            f"Category: {obl['category']} | Base Sum: GBP {obl['base_amount_gbp']:,.2f} | Indexation: {obl['indexation_method']} ({obl['index_base_date'].isoformat()})",
                        )
                    )
                    y += 16.0
                    for wline in _wrap_text(obl["verbatim_quote"], width=78):
                        lines.append((72.0, y, 10.0, wline))
                        y += 14.0
            else:
                lines.append(
                    (72.0, 100.0, 10.5, f"STANDARD SHEFFIELD S106 MODEL PROVISIONS (PAGE {pno})")
                )
                lines.append(
                    (72.0, 130.0, 9.5, f"Clause {pno}.1: Late payment interest accrues at 4% above Bank of England base rate.")
                )
                lines.append(
                    (72.0, 152.0, 9.5, f"Clause {pno}.2: Written notice required 14 days prior to Occupation thresholds.")
                )
            pages_lines.append(lines)

        _write_simple_pdf(pages_lines, out_path)
        generated.append(out_path)

    # Sheffield Standard S106 Model Template PDF
    template_path = PDF_FIXTURES_DIR / "Sheffield-Standard-S106-Model-Template.pdf"
    _write_simple_pdf(
        [
            [
                (72.0, 60.0, 12.0, "SHEFFIELD CITY COUNCIL - STANDARD SECTION 106 MODEL AGREEMENT TEMPLATE"),
                (72.0, 95.0, 10.0, "Model Bilateral Planning Obligation Deed (Town and Country Planning Act 1990 s.106)"),
                (72.0, 130.0, 9.5, "Schedule 1: Affordable Housing Commuted Sum (BCIS All-in Tender Price Index Linked)"),
                (72.0, 155.0, 9.5, "Schedule 2: Education Infrastructure Contribution (RPI All Items Index Linked)"),
                (72.0, 180.0, 9.5, "Schedule 3: Open Space, Recreation & Biodiversity Net Gain (CPI Index Linked)"),
                (72.0, 205.0, 9.5, "Schedule 4: Highways, Active Travel & Public Transport Works (BCIS Index Linked)"),
                (72.0, 230.0, 9.5, "Schedule 5: NHS Integrated Care Board (ICB) Primary Healthcare Sum (RPI Index Linked)"),
            ]
        ],
        template_path,
    )
    generated.append(template_path)

    # Ambiguous Draft Deed PDF (Test Input 4)
    amb_path = PDF_FIXTURES_DIR / "23-09999-FUL-DRAFT-AMBIGUOUS.pdf"
    _write_simple_pdf(
        [
            [
                (72.0, 80.0, 11.0, "DRAFT UNEXECUTED DEED (2023-06-01) - Application 23/09999/FUL, Hillsborough Yard"),
                (72.0, 110.0, 10.0, "Schedule 1, Paragraph 3.1 [TRACKED CHANGES CONFLICT]:"),
                (72.0, 130.0, 9.5, "The Owner shall pay the Open Space Sum of [GBP 250,000 OR GBP 140,000 - TBC BY VIABILITY ASSESSOR]"),
                (72.0, 150.0, 9.5, "indexed by [RPI / CPI - DELETE AS APPLICABLE] prior to Occupation of either the [30th OR 90th]"),
                (72.0, 170.0, 9.5, "Dwelling, whereas Sub-Clause 3.1(b) states no Open Space Sum is payable if Phase 1 is occupied first."),
            ]
        ],
        amb_path,
    )
    generated.append(amb_path)

    # Non-S106 Arboricultural Survey PDF (Test Input 5 - I Don't Know)
    idk_path = PDF_FIXTURES_DIR / "21-04512-FUL-ARBORICULTURAL-SURVEY.pdf"
    _write_simple_pdf(
        [
            [
                (72.0, 80.0, 11.0, "SHEFFIELD CITY COUNCIL - ARBORICULTURAL SITE INSPECTION NOTE (BS 5837:2012)"),
                (72.0, 110.0, 9.5, "Three mature English Oak specimens (T1, T2, T3) along the northern boundary fence"),
                (72.0, 130.0, 9.5, "exhibit moderate crown dieback and require 15% deadwood pruning prior to protective"),
                (72.0, 150.0, 9.5, "heras fencing installation. No Section 106 agreement, developer contribution, or"),
                (72.0, 170.0, 9.5, "financial covenant is mentioned or contained in this document."),
            ]
        ],
        idk_path,
    )
    generated.append(idk_path)

    return generated


def generate_statutory_csv_fixtures() -> list[Path]:
    """Generate the 3 statutory UK Gov (`planning.data.gov.uk`) ground-truth CSV files for Sheffield."""
    CSV_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    agreements_rows: list[dict[str, Any]] = []
    contributions_rows: list[dict[str, Any]] = []
    transactions_rows: list[dict[str, Any]] = []

    for agr in AGREEMENTS:
        slug = agr["planning_reference"].replace("/", "-")
        agr_ref = f"S106-SHF-{slug}"
        d_str = agr["agreement_date"].isoformat()
        agreements_rows.append(
            {
                "reference": agr_ref,
                "organisation": "local-authority:SHF",
                "entry-date": d_str,
                "start-date": d_str,
                "end-date": "",
                "planning-application": agr["planning_reference"],
                "document-url": agr["document_url"],
                "developer-agreement-type": "section-106",
            }
        )

    for obl in OBLIGATIONS:
        slug = obl["planning_reference"].replace("/", "-")
        agr_ref = f"S106-SHF-{slug}"
        d_str = obl["index_base_date"].isoformat()
        contributions_rows.append(
            {
                "reference": obl["obligation_id"],
                "developer-agreement": agr_ref,
                "contribution-purpose": obl["contribution_purpose"],
                "amount": f"{obl['indexed_amount_gbp']:.2f}",
                "base-amount": f"{obl['base_amount_gbp']:.2f}",
                "indexation-method": obl["indexation_method"],
                "trigger-dwelling-threshold": obl["dwelling_threshold"] or "",
                "entry-date": d_str,
                "start-date": d_str,
            }
        )
        transactions_rows.append(
            {
                "reference": f"{obl['obligation_id']}-TX-AGREED",
                "developer-agreement-contribution": obl["obligation_id"],
                "contribution-funding-status": "agreed",
                "amount": f"{obl['indexed_amount_gbp']:.2f}",
                "entry-date": d_str,
                "start-date": d_str,
            }
        )
        if obl["amount_received_gbp"] > 0:
            transactions_rows.append(
                {
                    "reference": f"{obl['obligation_id']}-TX-RECEIVED",
                    "developer-agreement-contribution": obl["obligation_id"],
                    "contribution-funding-status": "received",
                    "amount": f"{obl['amount_received_gbp']:.2f}",
                    "entry-date": "2025-01-01",
                    "start-date": "2025-01-01",
                }
            )

    datasets = {
        "sheffield-developer-agreement.csv": agreements_rows,
        "sheffield-developer-agreement-contribution.csv": contributions_rows,
        "sheffield-developer-agreement-transaction.csv": transactions_rows,
    }
    for filename, rows in datasets.items():
        path = CSV_FIXTURES_DIR / filename
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        generated.append(path)

    return generated


def generate_inflation_and_spatial_fixtures() -> list[Path]:
    """Generate ONS RPI/CPI + BCIS CSV fixtures and Sheffield 28-Ward / CIL / Deprivation GeoJSONs."""
    INFLATION_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    SPATIAL_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    # 1. ONS RPI (CHAW), CPI (D7BT), and BCIS All-in TPI monthly/annual series CSV
    inflation_csv = INFLATION_FIXTURES_DIR / "ons_rpi_cpi_bcis_series.csv"
    rows = [
        {"method": "BCIS", "series_id": "BCIS-TPI", "effective_date": "2020-01-01", "index_value": 332.0},
        {"method": "BCIS", "series_id": "BCIS-TPI", "effective_date": "2021-01-01", "index_value": 340.0},
        {"method": "BCIS", "series_id": "BCIS-TPI", "effective_date": "2022-01-01", "index_value": 368.0},
        {"method": "BCIS", "series_id": "BCIS-TPI", "effective_date": "2023-01-01", "index_value": 391.0},
        {"method": "BCIS", "series_id": "BCIS-TPI", "effective_date": "2024-01-01", "index_value": 402.0},
        {"method": "BCIS", "series_id": "BCIS-TPI", "effective_date": "2025-01-01", "index_value": 414.5},
        {"method": "RPI", "series_id": "CHAW", "effective_date": "2020-01-01", "index_value": 290.6},
        {"method": "RPI", "series_id": "CHAW", "effective_date": "2021-01-01", "index_value": 294.6},
        {"method": "RPI", "series_id": "CHAW", "effective_date": "2022-01-01", "index_value": 317.7},
        {"method": "RPI", "series_id": "CHAW", "effective_date": "2023-01-01", "index_value": 360.3},
        {"method": "RPI", "series_id": "CHAW", "effective_date": "2024-01-01", "index_value": 378.0},
        {"method": "RPI", "series_id": "CHAW", "effective_date": "2025-01-01", "index_value": 391.2},
        {"method": "CPI", "series_id": "D7BT", "effective_date": "2020-01-01", "index_value": 108.2},
        {"method": "CPI", "series_id": "D7BT", "effective_date": "2021-01-01", "index_value": 109.0},
        {"method": "CPI", "series_id": "D7BT", "effective_date": "2022-01-01", "index_value": 114.9},
        {"method": "CPI", "series_id": "D7BT", "effective_date": "2023-01-01", "index_value": 126.4},
        {"method": "CPI", "series_id": "D7BT", "effective_date": "2024-01-01", "index_value": 131.5},
        {"method": "CPI", "series_id": "D7BT", "effective_date": "2025-01-01", "index_value": 135.4},
    ]
    with inflation_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["method", "series_id", "effective_date", "index_value"])
        writer.writeheader()
        writer.writerows(rows)
    generated.append(inflation_csv)

    # 2. Sheffield Electoral Wards + Local Insight Deprivation & CIL Zone Indicators GeoJSON
    wards_geojson_path = SPATIAL_FIXTURES_DIR / "sheffield_wards_cil_indicators.geojson"
    ward_features = [
        {
            "type": "Feature",
            "properties": {
                "ward_code": "E05010861",
                "ward_name": "City",
                "lac_area": "Central Local Area Committee",
                "cil_charging_zone": "Zone 3 (City Centre Residential £50/sqm)",
                "imd_decile": 3,
                "green_space_access_pct": 41.2,
                "affordable_housing_need_score": 88.5,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-1.485, 53.372], [-1.455, 53.372], [-1.455, 53.390], [-1.485, 53.390], [-1.485, 53.372]]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "ward_code": "E05010879",
                "ward_name": "Walkley",
                "lac_area": "Central Local Area Committee",
                "cil_charging_zone": "Zone 3 (Inner Northwest / Kelham £50/sqm)",
                "imd_decile": 5,
                "green_space_access_pct": 62.4,
                "affordable_housing_need_score": 79.0,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-1.510, 53.385], [-1.475, 53.385], [-1.475, 53.405], [-1.510, 53.405], [-1.510, 53.385]]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "ward_code": "E05010866",
                "ward_name": "Ecclesall",
                "lac_area": "South West Local Area Committee",
                "cil_charging_zone": "Zone 5 (South West High Value £80/sqm)",
                "imd_decile": 10,
                "green_space_access_pct": 89.1,
                "affordable_housing_need_score": 64.2,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-1.535, 53.345], [-1.490, 53.345], [-1.490, 53.370], [-1.535, 53.370], [-1.535, 53.345]]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "ward_code": "E05010870",
                "ward_name": "Hillsborough",
                "lac_area": "North West Local Area Committee",
                "cil_charging_zone": "Zone 2 (North / Hillsborough £30/sqm)",
                "imd_decile": 4,
                "green_space_access_pct": 71.8,
                "affordable_housing_need_score": 74.6,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-1.525, 53.400], [-1.485, 53.400], [-1.485, 53.422], [-1.525, 53.422], [-1.525, 53.400]]],
            },
        },
    ]
    wards_geojson_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": ward_features}, indent=2),
        encoding="utf-8",
    )
    generated.append(wards_geojson_path)
    return generated


if __name__ == "__main__":
    pdfs = generate_pdf_fixtures()
    csvs = generate_statutory_csv_fixtures()
    others = generate_inflation_and_spatial_fixtures()
    print(f"[generate_s106_pdf_fixtures] Generated {len(pdfs)} PDFs, {len(csvs)} CSVs, {len(others)} spatial/inflation files:")
    for p in [*pdfs, *csvs, *others]:
        print(f"  - {p.relative_to(ROOT_DIR)} ({p.stat().st_size:,} bytes)")
