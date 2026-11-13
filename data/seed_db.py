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
    extraction_status: Mapped[str] = mapped_column(String(32), default="EXTRACTED")
    source_note: Mapped[str] = mapped_column(Text, default="")


def ensure_schema_upgrades(engine) -> list[str]:
    """
    Add columns that later revisions of the ledger need, without dropping anything.

    `init_database` drops and recreates, which is right for a seed but would destroy
    a ledger built from real deeds. This is the additive path: idempotent, and safe
    to run before every ingest. Returns the columns it added.
    """
    added: list[str] = []
    statements = [
        ("s106_agreements", "extraction_status", "VARCHAR(32) NOT NULL DEFAULT 'EXTRACTED'"),
        ("s106_agreements", "source_note", "TEXT NOT NULL DEFAULT ''"),
    ]
    with engine.begin() as conn:
        for table, column, definition in statements:
            before = {
                row[0]
                for row in conn.execute(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = 'public' and table_name = :t"
                    ),
                    {"t": table},
                ).all()
            }
            if column in before:
                continue
            conn.execute(
                text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')  # noqa: S608
            )
            added.append(f"{table}.{column}")
    return added


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

# ---------------------------------------------------------------------------
# No synthetic agreements or obligations are seeded.
#
# The ledger is populated only by `scripts/ingest_real_pdfs.py`, which runs the
# Nemotron extractor over the real Section 106 deeds in data/fixtures/pdfs/real/
# and persists the result to PostgreSQL. Run seeding first, then ingestion.
# ---------------------------------------------------------------------------



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

        session.commit()

    print(
        f"[seed_db] Seeded {len(SEED_SHEFFIELD_WARDS)} ward boundaries and "
        f"{len(SEED_INFLATION_INDICES)} inflation index points "
        f"at {datetime.now(timezone.utc).isoformat()}. "
        "The agreement ledger is intentionally empty: populate it with "
        "`python -m scripts.ingest_real_pdfs`."
    )


if __name__ == "__main__":
    seed_database()
