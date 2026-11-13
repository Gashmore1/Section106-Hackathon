"""
Track 1: PostgreSQL 16 (PostGIS + pgvector) schema definition and seed script
for Sheffield City Council Section 106 agreements, inflation indices (RPI/CPI/BCIS),
electoral wards (PostGIS polygons), and council payment ledger.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://spark:sparkpassword@localhost:5432/sheffield_s106",
)


class Base(DeclarativeBase):
    pass


class InflationIndexRecord(Base):
    """Official ONS (RPI, CPI) and BCIS All-in Tender Price Index series."""

    __tablename__ = "inflation_indices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    method: Mapped[str] = mapped_column(String(16), index=True)  # RPI, CPI, BCIS
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    index_value: Mapped[float] = mapped_column(Float, nullable=False)


class SheffieldWardRecord(Base):
    """Sheffield Electoral Wards with PostGIS GeoJSON boundary geometry."""

    __tablename__ = "sheffield_wards"

    ward_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    ward_name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    geojson_str: Mapped[str] = mapped_column(Text, nullable=False)


class S106AgreementRecord(Base):
    """Section 106 Agreement header & site occupation telemetry."""

    __tablename__ = "s106_agreements"

    planning_reference: Mapped[str] = mapped_column(String(64), primary_key=True)
    agreement_date: Mapped[date] = mapped_column(Date, nullable=False)
    site_name: Mapped[str] = mapped_column(String(256), nullable=False)
    ward_name: Mapped[str] = mapped_column(String(128), index=True)
    developer_name: Mapped[str] = mapped_column(String(256), nullable=False)
    total_dwellings_permitted: Mapped[int] = mapped_column(Integer, default=0)
    dwellings_occupied_current: Mapped[int] = mapped_column(Integer, default=0)
    document_url: Mapped[str] = mapped_column(String(512), default="")


class S106ObligationRecord(Base):
    """Individual S106 financial obligation with PDF evidence coordinates."""

    __tablename__ = "s106_obligations"

    obligation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    planning_reference: Mapped[str] = mapped_column(
        String(64), ForeignKey("s106_agreements.planning_reference"), index=True
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    base_amount_gbp: Mapped[float] = mapped_column(Float, nullable=False)
    indexation_method: Mapped[str] = mapped_column(String(16), default="NONE")
    index_base_date: Mapped[date] = mapped_column(Date, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_description: Mapped[str] = mapped_column(Text, nullable=False)
    dwelling_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_received_gbp: Mapped[float] = mapped_column(Float, default=0.0)
    clause_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    verbatim_quote: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    bbox_json: Mapped[str] = mapped_column(Text, default="{}")


# ---------------------------------------------------------------------------
# Placeholder seed data — used only when the real open-data fixtures below are
# absent (fresh clone without scripts/fetch_real_datasets.py having been run).
# ---------------------------------------------------------------------------

_PLACEHOLDER_INFLATION_INDICES: list[dict[str, Any]] = [
    # BCIS All-in Tender Price Index
    {"method": "BCIS", "effective_date": date(2020, 1, 1), "index_value": 332.0},
    {"method": "BCIS", "effective_date": date(2021, 1, 1), "index_value": 340.0},
    {"method": "BCIS", "effective_date": date(2022, 1, 1), "index_value": 368.0},
    {"method": "BCIS", "effective_date": date(2023, 1, 1), "index_value": 391.0},
    {"method": "BCIS", "effective_date": date(2024, 1, 1), "index_value": 402.0},
    {"method": "BCIS", "effective_date": date(2025, 1, 1), "index_value": 414.5},
    # RPI All Items Index (Jan 1987 = 100)
    {"method": "RPI", "effective_date": date(2020, 1, 1), "index_value": 290.6},
    {"method": "RPI", "effective_date": date(2021, 1, 1), "index_value": 294.6},
    {"method": "RPI", "effective_date": date(2022, 1, 1), "index_value": 317.7},
    {"method": "RPI", "effective_date": date(2023, 1, 1), "index_value": 360.3},
    {"method": "RPI", "effective_date": date(2024, 1, 1), "index_value": 378.0},
    {"method": "RPI", "effective_date": date(2025, 1, 1), "index_value": 391.2},
    # CPI All Items Index (2015 = 100)
    {"method": "CPI", "effective_date": date(2020, 1, 1), "index_value": 108.2},
    {"method": "CPI", "effective_date": date(2021, 1, 1), "index_value": 109.0},
    {"method": "CPI", "effective_date": date(2022, 1, 1), "index_value": 114.9},
    {"method": "CPI", "effective_date": date(2023, 1, 1), "index_value": 126.4},
    {"method": "CPI", "effective_date": date(2024, 1, 1), "index_value": 131.5},
    {"method": "CPI", "effective_date": date(2025, 1, 1), "index_value": 135.4},
]

_PLACEHOLDER_WARDS: list[dict[str, Any]] = [
    {
        "ward_code": "E05010861",
        "ward_name": "City",
        "geojson": {
            "type": "Feature",
            "properties": {"ward_code": "E05010861", "ward_name": "City"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-1.485, 53.372],
                    [-1.455, 53.372],
                    [-1.455, 53.390],
                    [-1.485, 53.390],
                    [-1.485, 53.372],
                ]],
            },
        },
    },
    {
        "ward_code": "E05010879",
        "ward_name": "Walkley",
        "geojson": {
            "type": "Feature",
            "properties": {"ward_code": "E05010879", "ward_name": "Walkley"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-1.510, 53.385],
                    [-1.475, 53.385],
                    [-1.475, 53.405],
                    [-1.510, 53.405],
                    [-1.510, 53.385],
                ]],
            },
        },
    },
    {
        "ward_code": "E05010866",
        "ward_name": "Ecclesall",
        "geojson": {
            "type": "Feature",
            "properties": {"ward_code": "E05010866", "ward_name": "Ecclesall"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-1.535, 53.345],
                    [-1.490, 53.345],
                    [-1.490, 53.370],
                    [-1.535, 53.370],
                    [-1.535, 53.345],
                ]],
            },
        },
    },
    {
        "ward_code": "E05010870",
        "ward_name": "Hillsborough",
        "geojson": {
            "type": "Feature",
            "properties": {"ward_code": "E05010870", "ward_name": "Hillsborough"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-1.525, 53.400],
                    [-1.485, 53.400],
                    [-1.485, 53.422],
                    [-1.525, 53.422],
                    [-1.525, 53.400],
                ]],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Real open-data fixtures (fetched by scripts/fetch_real_datasets.py)
# ---------------------------------------------------------------------------
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
WARDS_BOUNDARY_FIXTURE = FIXTURES_DIR / "spatial" / "sheffield_28_wards_ons_boundaries.geojson"
WARDS_CIL_FIXTURE = FIXTURES_DIR / "spatial" / "sheffield_wards_cil_indicators.geojson"
INFLATION_FIXTURES: tuple[Path, ...] = (
    FIXTURES_DIR / "inflation" / "ons_live_rpi_cpi.csv",
    FIXTURES_DIR / "inflation" / "ons_rpi_cpi_bcis_series.csv",
)

# The CIL indicators layer carries 2022 ward names while the boundary layer uses
# 2013 codes. The 2022 "City" ward is the same authority area as the 2013
# "Central" ward, so indicators can be joined across the rename.
WARD_NAME_ALIASES: dict[str, str] = {"City": "Central"}


def load_sheffield_wards() -> list[dict[str, Any]]:
    """
    Load all 28 real Sheffield electoral ward boundaries from the ONS GeoJSON,
    enriched with Sheffield Open Data Hub CIL / deprivation indicators
    (IMD decile, green-space access, affordable-housing need) where the ward can
    be matched by name.

    Falls back to the hand-drawn placeholder rectangles if the fixture is absent,
    so a fresh clone still renders something rather than an empty map.
    """
    if not WARDS_BOUNDARY_FIXTURE.exists():
        return list(_PLACEHOLDER_WARDS)

    cil_by_name: dict[str, dict[str, Any]] = {}
    if WARDS_CIL_FIXTURE.exists():
        for feat in json.loads(WARDS_CIL_FIXTURE.read_text())["features"]:
            props = dict(feat.get("properties") or {})
            raw_name = props.get("ward_name")
            name = WARD_NAME_ALIASES.get(raw_name, raw_name)
            props.pop("ward_code", None)
            props.pop("ward_name", None)
            if name:
                cil_by_name[name] = props

    wards: list[dict[str, Any]] = []
    for feat in json.loads(WARDS_BOUNDARY_FIXTURE.read_text())["features"]:
        props = dict(feat.get("properties") or {})
        code = props.get("WD13CD") or props.get("WD22CD")
        name = props.get("WD13NM") or props.get("WD22NM")
        if not code or not name:
            continue
        merged = dict(props)
        merged.update(cil_by_name.get(name, {}))
        merged["ward_code"], merged["ward_name"] = code, name
        wards.append(
            {
                "ward_code": code,
                "ward_name": name,
                "geojson": {**feat, "properties": merged},
            }
        )

    return wards or list(_PLACEHOLDER_WARDS)


def load_inflation_indices() -> list[dict[str, Any]]:
    """
    Load the full monthly ONS RPI / CPI series plus the BCIS All-in Tender Price
    Index series from the fetched CSVs.

    The two files overlap on a few annual dates and disagree on one value
    (RPI 2025-01: 391.2 in the static series vs 391.7 from the live ONS API), so
    the live API file is applied last and takes precedence.
    """
    merged: dict[tuple[str, date], float] = {}
    for path in reversed(INFLATION_FIXTURES):  # first entry wins
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    merged[(row["method"], date.fromisoformat(row["effective_date"]))] = float(
                        row["index_value"]
                    )
                except (KeyError, ValueError):
                    continue

    if not merged:
        return list(_PLACEHOLDER_INFLATION_INDICES)

    return [
        {"method": method, "effective_date": effective, "index_value": value}
        for (method, effective), value in sorted(merged.items())
    ]


SEED_INFLATION_INDICES: list[dict[str, Any]] = load_inflation_indices()
SEED_SHEFFIELD_WARDS: list[dict[str, Any]] = load_sheffield_wards()

SEED_AGREEMENTS: list[dict[str, Any]] = [
    {
        "planning_reference": "21/04512/FUL",
        "agreement_date": date(2021, 1, 1),
        "site_name": "Kelham Riverside Foundry Quarter, Green Lane, Sheffield S3 8SJ",
        "ward_name": "Walkley",
        "developer_name": "Hallamshire Urban Regeneration Developments Ltd",
        "total_dwellings_permitted": 240,
        "dwellings_occupied_current": 112,
        "document_url": "https://planningapps.sheffield.gov.uk/online-applications/21-04512-FUL-S106.pdf",
    },
    {
        "planning_reference": "20/01984/OUT",
        "agreement_date": date(2020, 1, 1),
        "site_name": "West Bar Gateway Tower Phase 2, Corporation Street, Sheffield S3 8RB",
        "ward_name": "City",
        "developer_name": "Pennine Commercial Estates PLC",
        "total_dwellings_permitted": 360,
        "dwellings_occupied_current": 195,
        "document_url": "https://planningapps.sheffield.gov.uk/online-applications/20-01984-OUT-S106.pdf",
    },
    {
        "planning_reference": "22/03110/FUL",
        "agreement_date": date(2022, 1, 1),
        "site_name": "Banner Cross Parklands, Ecclesall Road South, Sheffield S11 9PA",
        "ward_name": "Ecclesall",
        "developer_name": "Peak District Homes (Yorkshire) Ltd",
        "total_dwellings_permitted": 85,
        "dwellings_occupied_current": 64,
        "document_url": "https://planningapps.sheffield.gov.uk/online-applications/22-03110-FUL-S106.pdf",
    },
]

SEED_OBLIGATIONS: list[dict[str, Any]] = [
    {
        "obligation_id": "OBL-21-04512-01",
        "planning_reference": "21/04512/FUL",
        "category": "AFFORDABLE_HOUSING",
        "title": "Off-Site Affordable Housing Commuted Sum (First Tranche)",
        "base_amount_gbp": 450000.0,
        "indexation_method": "BCIS",
        "index_base_date": date(2021, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 50th Market Dwelling on the Land",
        "dwelling_threshold": 50,
        "amount_received_gbp": 450000.0,  # Paid nominal base only, missing BCIS indexation uplift!
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
        "title": "Off-Site Affordable Housing Commuted Sum (Second Tranche)",
        "base_amount_gbp": 450000.0,
        "indexation_method": "BCIS",
        "index_base_date": date(2021, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 100th Market Dwelling on the Land",
        "dwelling_threshold": 100,
        "amount_received_gbp": 0.0,  # Trigger breached (112 occupied >= 100), completely unpaid!
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
        "title": "Secondary School Places Expansion Contribution",
        "base_amount_gbp": 285000.0,
        "indexation_method": "RPI",
        "index_base_date": date(2021, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 75th Dwelling",
        "dwelling_threshold": 75,
        "amount_received_gbp": 0.0,  # Trigger breached (112 >= 75), completely unpaid!
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
        "title": "River Don Corridor Biodiversity Net Gain & Pocket Park Sum",
        "base_amount_gbp": 120000.0,
        "indexation_method": "CPI",
        "index_base_date": date(2021, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 180th Dwelling",
        "dwelling_threshold": 180,
        "amount_received_gbp": 0.0,  # Pending trigger (112 < 180)
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
        "title": "West Bar Active Travel & Bus Rapid Transit Junction Works",
        "base_amount_gbp": 390000.0,
        "indexation_method": "BCIS",
        "index_base_date": date(2020, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 150th Residential Unit",
        "dwelling_threshold": 150,
        "amount_received_gbp": 390000.0,  # Paid base, missing BCIS uplift
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
        "title": "Sheffield NHS ICB Primary Care Consulting Rooms Expansion",
        "base_amount_gbp": 165000.0,
        "indexation_method": "RPI",
        "index_base_date": date(2022, 1, 1),
        "trigger_type": "DWELLING_OCCUPATION_THRESHOLD",
        "trigger_description": "Prior to Occupation of the 50th Dwelling",
        "dwelling_threshold": 50,
        "amount_received_gbp": 0.0,  # Trigger breached (64 >= 50)
        "clause_reference": "Schedule 4, Paragraph 1.1",
        "verbatim_quote": (
            "The Owner shall pay the Healthcare Infrastructure Contribution of £165,000 "
            "(RPI Index Linked) prior to Occupation of the 50th Dwelling."
        ),
        "page_number": 29,
        "bbox": {"page_number": 29, "x0": 72.0, "y0": 260.0, "x1": 523.0, "y1": 330.0},
    },
]


def get_engine():
    return create_engine(DATABASE_URL, pool_pre_ping=True)


def seed_database() -> None:
    """Create tables, enable PostGIS + pgvector extensions, and insert Sheffield seed data."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
        except Exception as exc:
            print(f"[seed_db] PostGIS extension warning (continuing with GeoJSON): {exc}")

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        for idx in SEED_INFLATION_INDICES:
            session.add(InflationIndexRecord(**idx))

        for ward in SEED_SHEFFIELD_WARDS:
            session.add(
                SheffieldWardRecord(
                    ward_code=ward["ward_code"],
                    ward_name=ward["ward_name"],
                    geojson_str=json.dumps(ward["geojson"]),
                )
            )

        for agr in SEED_AGREEMENTS:
            session.add(S106AgreementRecord(**agr))

        for obl in SEED_OBLIGATIONS:
            obl_copy = dict(obl)
            bbox = obl_copy.pop("bbox", {})
            obl_copy["bbox_json"] = json.dumps(bbox)
            session.add(S106ObligationRecord(**obl_copy))

        session.commit()

    print(
        f"[seed_db] Successfully seeded {len(SEED_AGREEMENTS)} Sheffield S106 agreements, "
        f"{len(SEED_OBLIGATIONS)} obligations, {len(SEED_SHEFFIELD_WARDS)} ward boundaries "
        f"and {len(SEED_INFLATION_INDICES)} inflation index points "
        f"at {datetime.now(timezone.utc).isoformat()}."
    )


if __name__ == "__main__":
    seed_database()
