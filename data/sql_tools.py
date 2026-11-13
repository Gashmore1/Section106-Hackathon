"""
Track 1: Deterministic SQL & PostGIS Financial/Spatial Tools.
All inflation indexation and shortfall math is computed deterministically here—never by an LLM.
Automatically uses PostgreSQL 16 when reachable, with seamless fallback to the canonical seed dataset.
"""

from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import insert, select, update

logger = logging.getLogger(__name__)
_WARNED_DB_FALLBACK = False


class AgreementNotFoundError(LookupError):
    """No agreement with that planning reference exists in the ledger."""


def _warn_db_fallback_once(exc: Exception) -> None:
    """Emit a single clear warning if PostgreSQL is unreachable and seed fallback is used."""
    global _WARNED_DB_FALLBACK
    if not _WARNED_DB_FALLBACK:
        _WARNED_DB_FALLBACK = True
        logger.warning(
            "[sql_tools] PostgreSQL unreachable (%s) — falling back to in-memory seed dataset.",
            exc,
        )

from data.seed_db import (
    SEED_INFLATION_INDICES,
    SEED_SHEFFIELD_WARDS,
    InflationIndexRecord,
    S106AgreementRecord,
    S106ObligationRecord,
    SheffieldWardRecord,
    get_engine,
)
from shared.schemas import (
    AuditDiscrepancy,
    AuditReport,
    AuditStatus,
    BoundingBox,
    ClauseEvidence,
    ContributionCategory,
    DeveloperAgreementCSVRow,
    DeveloperAgreementContributionCSVRow,
    DeveloperAgreementTransactionCSVRow,
    ExtractedAgreement,
    IndexationCalculationResult,
    IndexationMethod,
    WardSummary,
)

AUDIT_REFERENCE_DATE = date(2025, 1, 1)


def _lookup_index_value(method: IndexationMethod, target_date: date) -> float:
    """Lookup index value from Postgres or canonical ONS/BCIS table."""
    if method == IndexationMethod.NONE:
        return 1.0

    try:
        engine = get_engine()
        with engine.connect() as conn:
            stmt = (
                select(InflationIndexRecord.index_value)
                .where(InflationIndexRecord.method == method.value)
                .where(InflationIndexRecord.effective_date <= target_date)
                .order_by(InflationIndexRecord.effective_date.desc())
                .limit(1)
            )
            val = conn.execute(stmt).scalar_one_or_none()
            if val is not None:
                return float(val)
    except Exception as exc:
        _warn_db_fallback_once(exc)

    candidates = [
        r["index_value"]
        for r in SEED_INFLATION_INDICES
        if r["method"] == method.value and r["effective_date"] <= target_date
    ]
    if not candidates:
        candidates = [
            r["index_value"]
            for r in SEED_INFLATION_INDICES
            if r["method"] == method.value
        ]
    return float(candidates[-1]) if candidates else 1.0


def compute_indexed_obligation(
    obligation_id: str,
    base_amount_gbp: float,
    indexation_method: IndexationMethod,
    base_date: date,
    audit_date: date = AUDIT_REFERENCE_DATE,
) -> IndexationCalculationResult:
    """
    Deterministic inflation indexation calculation:
    Indexed Sum = Base Sum * (Current Index / Base Index)
    """
    if indexation_method == IndexationMethod.NONE:
        return IndexationCalculationResult(
            obligation_id=obligation_id,
            indexation_method=indexation_method,
            base_date=base_date,
            audit_date=audit_date,
            base_index_value=1.0,
            current_index_value=1.0,
            multiplier=1.0,
            base_amount_gbp=round(base_amount_gbp, 2),
            indexed_amount_gbp=round(base_amount_gbp, 2),
            indexation_uplift_gbp=0.0,
        )

    base_idx = _lookup_index_value(indexation_method, base_date)
    curr_idx = _lookup_index_value(indexation_method, audit_date)
    multiplier = max(1.0, curr_idx / base_idx) if base_idx > 0 else 1.0
    indexed_amount = round(base_amount_gbp * multiplier, 2)
    uplift = round(indexed_amount - base_amount_gbp, 2)

    return IndexationCalculationResult(
        obligation_id=obligation_id,
        indexation_method=indexation_method,
        base_date=base_date,
        audit_date=audit_date,
        base_index_value=base_idx,
        current_index_value=curr_idx,
        multiplier=round(multiplier, 6),
        base_amount_gbp=round(base_amount_gbp, 2),
        indexed_amount_gbp=indexed_amount,
        indexation_uplift_gbp=uplift,
    )


def fetch_all_agreements() -> list[dict]:
    """
    Return every S106 agreement in the PostgreSQL ledger.

    There is deliberately no in-memory fallback: if PostgreSQL is unreachable this
    raises, so the API reports an error rather than silently serving figures that
    were never sourced from a deed.
    """
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(select(S106AgreementRecord)).mappings().all()
    return [dict(r) for r in rows]


def fetch_obligations_for_agreement(planning_reference: str) -> list[dict]:
    """Return the extracted obligations stored for one planning reference."""
    engine = get_engine()
    with engine.connect() as conn:
        stmt = select(S106ObligationRecord).where(
            S106ObligationRecord.planning_reference == planning_reference
        )
        rows = conn.execute(stmt).mappings().all()
    result: list[dict] = []
    for r in rows:
        d = dict(r)
        d["bbox"] = json.loads(d.pop("bbox_json", "{}") or "{}")
        result.append(d)
    return result


def persist_extracted_agreement(
    extracted: ExtractedAgreement,
    dwellings_occupied_current: Optional[int] = None,
    document_url: str = "",
    source_note: str = "",
) -> None:
    """
    Upsert a live Nemotron extraction into the PostgreSQL ledger so subsequent
    audits, ward rollups and CSV exports include it.

    Only agreements the model actually extracted are stored. An ambiguous or
    abstained result persists nothing, so an unreadable deed can never contribute
    invented obligations to the ledger.

    `dwellings_occupied_current` is occupation telemetry: when the caller does not
    supply it we store 0 rather than guessing, which leaves every obligation
    honestly reported as "not yet triggered" instead of fabricating a breach.
    """
    if extracted.extraction_status != "EXTRACTED" or not extracted.obligations:
        return

    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            select(S106AgreementRecord).where(
                S106AgreementRecord.planning_reference == extracted.planning_reference
            )
        ).mappings().first()

        occupied = (
            dwellings_occupied_current
            if dwellings_occupied_current is not None
            else (int(existing["dwellings_occupied_current"]) if existing else 0)
        )
        agreement_values = {
            "planning_reference": extracted.planning_reference,
            "agreement_date": extracted.agreement_date,
            "site_name": extracted.site_name,
            "ward_name": extracted.ward_name,
            "developer_name": extracted.developer_name,
            "total_dwellings_permitted": extracted.total_dwellings_permitted,
            "dwellings_occupied_current": occupied,
            "document_url": document_url or (existing["document_url"] if existing else ""),
            "extraction_status": extracted.extraction_status,
            "source_note": source_note or (existing["source_note"] if existing else ""),
        }
        if existing:
            conn.execute(
                update(S106AgreementRecord)
                .where(
                    S106AgreementRecord.planning_reference == extracted.planning_reference
                )
                .values(**{k: v for k, v in agreement_values.items() if k != "planning_reference"})
            )
        else:
            conn.execute(insert(S106AgreementRecord).values(**agreement_values))

        stored = {
            row[0]
            for row in conn.execute(
                select(S106ObligationRecord.obligation_id).where(
                    S106ObligationRecord.planning_reference == extracted.planning_reference
                )
            ).all()
        }
        for obl in extracted.obligations:
            obligation_values = {
                "planning_reference": extracted.planning_reference,
                "category": obl.category.value,
                "title": obl.title,
                "base_amount_gbp": obl.base_amount_gbp,
                "indexation_method": obl.indexation_method.value,
                "index_base_date": obl.index_base_date or extracted.agreement_date,
                "trigger_type": obl.trigger.trigger_type.value,
                "trigger_description": obl.trigger.description,
                "dwelling_threshold": obl.trigger.dwelling_threshold,
                "clause_reference": obl.evidence.clause_reference,
                "verbatim_quote": obl.evidence.verbatim_quote,
                "page_number": obl.evidence.page_number,
                "bbox_json": json.dumps(
                    obl.evidence.bbox.model_dump() if obl.evidence.bbox else {}
                ),
            }
            if obl.obligation_id in stored:
                # Never overwrite recorded payments with an extraction default.
                conn.execute(
                    update(S106ObligationRecord)
                    .where(
                        S106ObligationRecord.obligation_id == obl.obligation_id
                    )
                    .values(**{k: v for k, v in obligation_values.items() if k != "planning_reference"})
                )
            else:
                conn.execute(
                    insert(S106ObligationRecord).values(
                        obligation_id=obl.obligation_id,
                        amount_received_gbp=0.0,
                        **obligation_values,
                    )
                )


def run_deterministic_audit(
    planning_reference: str,
    extracted_agreement: Optional[ExtractedAgreement] = None,
    dwellings_occupied_override: Optional[int] = None,
) -> AuditReport:
    """
    Evaluate all obligations for a planning reference (or a live `ExtractedAgreement`
    from Nemotron) against site dwelling occupation telemetry and ONS/BCIS inflation indices.
    """
    agreements = fetch_all_agreements()
    matched_agr = next(
        (a for a in agreements if a["planning_reference"] == planning_reference),
        None,
    )

    if (
        extracted_agreement is not None
        and extracted_agreement.extraction_status == "EXTRACTED"
        and extracted_agreement.obligations
    ):
        persist_extracted_agreement(
            extracted_agreement,
            dwellings_occupied_current=dwellings_occupied_override,
        )
        ledger_obls_by_id = {
            o["obligation_id"]: o
            for o in fetch_obligations_for_agreement(
                extracted_agreement.planning_reference
            )
        }
        agr = matched_agr or {
            "planning_reference": extracted_agreement.planning_reference,
            "agreement_date": extracted_agreement.agreement_date,
            "site_name": extracted_agreement.site_name,
            "ward_name": extracted_agreement.ward_name,
            "developer_name": extracted_agreement.developer_name,
            "total_dwellings_permitted": extracted_agreement.total_dwellings_permitted,
            "dwellings_occupied_current": (
                dwellings_occupied_override
                if dwellings_occupied_override is not None
                else max(1, extracted_agreement.total_dwellings_permitted // 2)
            ),
        }
        obligations = []
        for obl in extracted_agreement.obligations:
            ledger_match = ledger_obls_by_id.get(obl.obligation_id, {})
            obligations.append(
                {
                    "obligation_id": obl.obligation_id,
                    "planning_reference": extracted_agreement.planning_reference,
                    "category": obl.category.value,
                    "title": obl.title,
                    "base_amount_gbp": obl.base_amount_gbp,
                    "indexation_method": obl.indexation_method.value,
                    "index_base_date": obl.index_base_date
                    or extracted_agreement.agreement_date,
                    "trigger_type": obl.trigger.trigger_type.value,
                    "trigger_description": obl.trigger.description,
                    "dwelling_threshold": obl.trigger.dwelling_threshold,
                    "amount_received_gbp": float(
                        ledger_match.get("amount_received_gbp", 0.0)
                    ),
                    "clause_reference": obl.evidence.clause_reference,
                    "verbatim_quote": obl.evidence.verbatim_quote,
                    "page_number": obl.evidence.page_number,
                    "bbox": (
                        obl.evidence.bbox.model_dump()
                        if obl.evidence.bbox
                        else ledger_match.get("bbox", {})
                    ),
                }
            )
    else:
        if matched_agr is None:
            raise AgreementNotFoundError(planning_reference)
        agr = matched_agr
        obligations = fetch_obligations_for_agreement(agr["planning_reference"])

    occupied = (
        int(dwellings_occupied_override)
        if dwellings_occupied_override is not None
        else int(agr["dwellings_occupied_current"])
    )

    discrepancies: list[AuditDiscrepancy] = []
    total_base = 0.0
    total_indexed = 0.0
    total_received = 0.0
    total_recoverable_shortfall = 0.0

    for obl in obligations:
        method = IndexationMethod(obl["indexation_method"])
        calc = compute_indexed_obligation(
            obligation_id=obl["obligation_id"],
            base_amount_gbp=float(obl["base_amount_gbp"]),
            indexation_method=method,
            base_date=obl["index_base_date"],
        )

        received = float(obl["amount_received_gbp"])
        threshold: Optional[int] = obl.get("dwelling_threshold")
        is_triggered = threshold is None or occupied >= threshold

        if not is_triggered:
            status = AuditStatus.PENDING_TRIGGER
            shortfall = 0.0
            summary = (
                f"Not yet triggered: {occupied}/{threshold} dwellings currently occupied. "
                f"Projected {method.value}-indexed liability at trigger: £{calc.indexed_amount_gbp:,.2f}."
            )
        else:
            shortfall = max(0.0, round(calc.indexed_amount_gbp - received, 2))
            if shortfall == 0.0:
                status = AuditStatus.COMPLIANT
                summary = "Obligation paid in full including applicable indexation."
            elif received == 0.0:
                status = AuditStatus.TRIGGERED_UNPAID
                summary = (
                    f"BREACH: Trigger threshold ({threshold} dwellings) exceeded "
                    f"(currently {occupied} occupied). £0 received against "
                    f"{method.value}-indexed sum of £{calc.indexed_amount_gbp:,.2f} "
                    f"(Base £{calc.base_amount_gbp:,.2f} + £{calc.indexation_uplift_gbp:,.2f} indexation)."
                )
            else:
                status = AuditStatus.UNDERPAID_INDEXATION
                summary = (
                    f"INDEXATION SHORTFALL: Developer paid nominal base (£{received:,.2f}) "
                    f"but omitted {method.value} inflation uplift "
                    f"(Index {calc.base_index_value} -> {calc.current_index_value}, "
                    f"multiplier {calc.multiplier:.4f}x). Uncollected uplift: £{shortfall:,.2f}."
                )

        total_base += calc.base_amount_gbp
        total_indexed += calc.indexed_amount_gbp
        total_received += received
        total_recoverable_shortfall += shortfall

        bbox_dict = obl.get("bbox")
        bbox_obj = BoundingBox(**bbox_dict) if bbox_dict else None

        discrepancies.append(
            AuditDiscrepancy(
                obligation_id=obl["obligation_id"],
                planning_reference=agr["planning_reference"],
                category=ContributionCategory(obl["category"]),
                title=obl["title"],
                status=status,
                base_amount_gbp=calc.base_amount_gbp,
                indexed_amount_gbp=calc.indexed_amount_gbp,
                amount_received_gbp=received,
                shortfall_gbp=shortfall,
                dwellings_occupied_current=occupied,
                trigger_threshold=threshold,
                finding_summary=summary,
                evidence=ClauseEvidence(
                    clause_reference=obl["clause_reference"],
                    verbatim_quote=obl["verbatim_quote"],
                    page_number=int(obl["page_number"]),
                    bbox=bbox_obj,
                ),
            )
        )

    return AuditReport(
        planning_reference=agr["planning_reference"],
        site_name=agr["site_name"],
        ward_name=agr["ward_name"],
        developer_name=agr["developer_name"],
        agreement_date=agr["agreement_date"],
        audit_timestamp=datetime.now(timezone.utc),
        dwellings_permitted=int(agr["total_dwellings_permitted"]),
        dwellings_occupied=occupied,
        total_base_gbp=round(total_base, 2),
        total_indexed_gbp=round(total_indexed, 2),
        total_received_gbp=round(total_received, 2),
        total_recoverable_shortfall_gbp=round(total_recoverable_shortfall, 2),
        discrepancies=discrepancies,
    )


SHEFFIELD_AUTHORITY = "Sheffield City Council"

DEED_CORPORA: tuple[tuple[str, Path], ...] = (
    (
        "Sheffield planning register",
        Path(__file__).resolve().parent
        / "fixtures" / "pdfs" / "sheffield" / "provenance.json",
    ),
    (
        "curated deeds",
        Path(__file__).resolve().parent / "fixtures" / "pdfs" / "real" / "provenance.json",
    ),
)


def _corpus_deeds_not_in_ledger(ledger_refs: set[str]) -> list[dict[str, Any]]:
    """
    Deeds that were downloaded and read but contributed nothing to the ledger.

    They are deliberately absent from PostgreSQL: persisting nothing is what an
    abstention means. Without this the UI under-reports how many deeds were read,
    which makes the corpus look smaller than the work that was done.
    """
    missing: list[dict[str, Any]] = []
    for corpus, path in DEED_CORPORA:
        if not path.exists():
            continue
        try:
            deeds = json.loads(path.read_text()).get("deeds", [])
        except (json.JSONDecodeError, OSError):
            continue
        for deed in deeds:
            reference = deed.get("planning_reference")
            if not reference or reference in ledger_refs:
                continue
            # Another authority's deed was never ours to read: the ingest skips it
            # by design, so calling it an abstention would be a lie about coverage.
            if (deed.get("authority") or "") != SHEFFIELD_AUTHORITY:
                continue
            missing.append(
                {
                    "planning_reference": reference,
                    "corpus": corpus,
                    "site_name": deed.get("site_name"),
                    "ward_name": deed.get("ward_name"),
                    "document_url": deed.get("document_url"),
                }
            )
    return missing


def get_portfolio_summary() -> dict[str, Any]:
    """
    Whole-ledger rollup: every deed the ledger holds, audited with the same
    deterministic path as a single-deed audit, plus the ward rollup.

    The frontend is deliberately given finished numbers rather than raw rows. It
    cannot add, index or infer a shortfall in JavaScript, so a figure on screen is
    the same figure `/api/audit` produces for that deed.
    """
    agreements = fetch_all_agreements()
    deeds: list[dict[str, Any]] = []
    evidence_pages_resolved = 0
    evidence_total = 0
    for agr in agreements:
        reference = agr["planning_reference"]
        report = run_deterministic_audit(reference)
        obligations = fetch_obligations_for_agreement(reference)
        evidence_total += len(obligations)
        evidence_pages_resolved += sum(
            1 for o in obligations if (o.get("page_number") or 0) > 1
        )
        ward = (agr.get("ward_name") or "").strip()
        deeds.append(
            {
                "planning_reference": reference,
                "site_name": agr.get("site_name"),
                "ward_name": ward or "Unassigned",
                "ward_resolved": bool(ward) and ward.upper() != "UNKNOWN",
                "developer_name": agr.get("developer_name"),
                "agreement_date": agr.get("agreement_date"),
                "extraction_status": agr.get("extraction_status") or "EXTRACTED",
                "source_note": agr.get("source_note") or "",
                "obligation_count": len(obligations),
                "dwellings_permitted": int(agr.get("total_dwellings_permitted") or 0),
                "dwellings_occupied": report.dwellings_occupied,
                "base_total_gbp": report.total_base_gbp,
                "indexed_total_gbp": report.total_indexed_gbp,
                "received_total_gbp": report.total_received_gbp,
                "recoverable_shortfall_gbp": report.total_recoverable_shortfall_gbp,
                "discrepancy_count": len(report.discrepancies),
            }
        )

    def _total(key: str) -> float:
        return round(sum(d[key] for d in deeds), 2)

    by_ward: dict[str, dict[str, Any]] = {}
    for d in deeds:
        row = by_ward.setdefault(
            d["ward_name"],
            {"ward_name": d["ward_name"], "deed_count": 0, "base_total_gbp": 0.0,
             "indexed_total_gbp": 0.0, "recoverable_shortfall_gbp": 0.0},
        )
        row["deed_count"] += 1
        for key in ("base_total_gbp", "indexed_total_gbp", "recoverable_shortfall_gbp"):
            row[key] = round(row[key] + d[key], 2)

    abstained = _corpus_deeds_not_in_ledger(
        {a["planning_reference"] for a in agreements}
    )

    base = _total("base_total_gbp")
    indexed = _total("indexed_total_gbp")
    return {
        "audit_date": date.today().isoformat(),
        "deeds": sorted(deeds, key=lambda d: d["planning_reference"]),
        "totals": {
            "deed_count": len(deeds),
            "deeds_with_obligations": sum(1 for d in deeds if d["obligation_count"]),
            "obligation_count": sum(d["obligation_count"] for d in deeds),
            "base_total_gbp": base,
            "indexed_total_gbp": indexed,
            "indexation_exposure_gbp": round(indexed - base, 2),
            "received_total_gbp": _total("received_total_gbp"),
            "recoverable_shortfall_gbp": _total("recoverable_shortfall_gbp"),
            "deeds_ward_resolved": sum(1 for d in deeds if d["ward_resolved"]),
            "discrepancy_count": sum(d["discrepancy_count"] for d in deeds),
        },
        "wards": sorted(by_ward.values(), key=lambda w: -w["indexed_total_gbp"]),
        "data_quality": {
            "obligations_with_evidence_page": evidence_pages_resolved,
            "obligations_total": evidence_total,
            "abstained_deeds": abstained,
            "occupation_telemetry": (
                "Occupation was recorded as 0 dwellings for every deed, so obligations are "
                "reported as not yet triggered rather than as breaches."
            ),
            "unattributed_deeds": [
                d["planning_reference"] for d in deeds if not d["ward_resolved"]
            ],
        },
    }


def get_sheffield_ward_summaries() -> list[WardSummary]:
    """Aggregate S106 audit metrics by Sheffield Electoral Ward with GeoJSON polygons."""
    wards = SEED_SHEFFIELD_WARDS
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(select(SheffieldWardRecord)).mappings().all()
            if rows:
                wards = [
                    {
                        "ward_code": r["ward_code"],
                        "ward_name": r["ward_name"],
                        "geojson": json.loads(r["geojson_str"]),
                    }
                    for r in rows
                ]
    except Exception as exc:
        _warn_db_fallback_once(exc)

    agreements = fetch_all_agreements()
    reports_by_ward: dict[str, list[AuditReport]] = {}
    for agr in agreements:
        rep = run_deterministic_audit(agr["planning_reference"])
        reports_by_ward.setdefault(agr["ward_name"], []).append(rep)

    summaries: list[WardSummary] = []
    for w in wards:
        w_reports = reports_by_ward.get(w["ward_name"], [])
        summaries.append(
            WardSummary(
                ward_code=w["ward_code"],
                ward_name=w["ward_name"],
                agreements_count=len(w_reports),
                total_agreed_gbp=round(sum(r.total_base_gbp for r in w_reports), 2),
                total_indexed_gbp=round(sum(r.total_indexed_gbp for r in w_reports), 2),
                total_received_gbp=round(sum(r.total_received_gbp for r in w_reports), 2),
                uncollected_shortfall_gbp=round(
                    sum(r.total_recoverable_shortfall_gbp for r in w_reports), 2
                ),
                geojson=w["geojson"],
            )
        )
    return summaries


def generate_statutory_csv_rows() -> dict[str, list[dict]]:
    """
    Generate the 3 statutory UK Government Open Data CSV datasets for `planning.data.gov.uk`:
    1. developer-agreement
    2. developer-agreement-contribution
    3. developer-agreement-transaction
    """
    agreements = fetch_all_agreements()
    agreements_csv: list[dict] = []
    contributions_csv: list[dict] = []
    transactions_csv: list[dict] = []

    for agr in agreements:
        ref_slug = agr["planning_reference"].replace("/", "-")
        agr_ref = f"S106-SHF-{ref_slug}"

        row_agr = DeveloperAgreementCSVRow(
            reference=agr_ref,
            entry_date=agr["agreement_date"],
            start_date=agr["agreement_date"],
            planning_application=agr["planning_reference"],
            document_url=agr["document_url"],
        )
        agreements_csv.append(row_agr.model_dump(by_alias=True, exclude_none=True))

        report = run_deterministic_audit(agr["planning_reference"])
        for disc in report.discrepancies:
            contrib_ref = disc.obligation_id
            row_contrib = DeveloperAgreementContributionCSVRow(
                reference=contrib_ref,
                developer_agreement=agr_ref,
                contribution_purpose=disc.category.value.lower().replace("_", "-"),
                amount=disc.indexed_amount_gbp,
                entry_date=agr["agreement_date"],
                start_date=agr["agreement_date"],
            )
            contributions_csv.append(
                row_contrib.model_dump(by_alias=True, exclude_none=True)
            )

            # Agreed transaction record
            tx_agreed = DeveloperAgreementTransactionCSVRow(
                reference=f"{contrib_ref}-TX-AGREED",
                developer_agreement_contribution=contrib_ref,
                contribution_funding_status="agreed",
                amount=disc.indexed_amount_gbp,
                entry_date=agr["agreement_date"],
                start_date=agr["agreement_date"],
            )
            transactions_csv.append(
                tx_agreed.model_dump(by_alias=True, exclude_none=True)
            )

            # Received transaction record if any payment made
            if disc.amount_received_gbp > 0:
                tx_rec = DeveloperAgreementTransactionCSVRow(
                    reference=f"{contrib_ref}-TX-RECEIVED",
                    developer_agreement_contribution=contrib_ref,
                    contribution_funding_status="received",
                    amount=disc.amount_received_gbp,
                    entry_date=AUDIT_REFERENCE_DATE,
                    start_date=AUDIT_REFERENCE_DATE,
                )
                transactions_csv.append(
                    tx_rec.model_dump(by_alias=True, exclude_none=True)
                )

    return {
        "developer-agreement": agreements_csv,
        "developer-agreement-contribution": contributions_csv,
        "developer-agreement-transaction": transactions_csv,
    }


# ---------------------------------------------------------------------------
# National benchmark: Sheffield's 0/3 statutory endpoints vs peer authorities
# ---------------------------------------------------------------------------
NATIONAL_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "csv" / "national"
SHEFFIELD_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "csv"
SHEFFIELD_ORG_CODE = "local-authority:SHF"
STATUTORY_ENDPOINT_DATASETS: tuple[str, ...] = (
    "developer-agreement",
    "developer-agreement-contribution",
    "developer-agreement-transaction",
)
_BENCHMARK_CACHE: Optional[dict[str, Any]] = None


def _summarise_dataset_csv(path: Path) -> tuple[int, set[str]]:
    """Return (row count, distinct `organisation-entity` values) for a dataset CSV."""
    rows = 0
    entities: set[str] = set()
    if not path.exists():
        return rows, entities
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            entity = (row.get("organisation-entity") or "").strip()
            if entity:
                entities.add(entity)
    return rows, entities


def national_endpoint_benchmark(refresh: bool = False) -> dict[str, Any]:
    """
    Benchmark Sheffield City Council's developer-contribution open data against
    every authority publishing to the national planning.data.gov.uk collection.

    Sheffield currently publishes 0 of the 3 statutory datasets. The published
    national extract identifies authorities only by `organisation-entity` number
    (the `organisation` column is blank throughout the dump), so peers are
    reported as anonymous entity numbers rather than guessed council names.
    """
    global _BENCHMARK_CACHE
    if _BENCHMARK_CACHE is not None and not refresh:
        return _BENCHMARK_CACHE

    national_datasets: dict[str, dict[str, Any]] = {}
    entities_by_dataset: dict[str, set[str]] = {}
    all_entities: set[str] = set()
    for dataset in STATUTORY_ENDPOINT_DATASETS:
        rows, entities = _summarise_dataset_csv(NATIONAL_FIXTURE_DIR / f"{dataset}.csv")
        national_datasets[dataset] = {"rows": rows, "publishing_authorities": len(entities)}
        entities_by_dataset[dataset] = entities
        all_entities |= entities

    # Rows this audit would publish for Sheffield (generated, not yet live).
    generated_for_sheffield = {
        dataset: _summarise_dataset_csv(SHEFFIELD_FIXTURE_DIR / f"sheffield-{dataset}.csv")[0]
        for dataset in STATUTORY_ENDPOINT_DATASETS
    }

    # Largest publishers by agreement volume, for the leaderboard.
    per_entity: Counter = Counter()
    agreement_path = NATIONAL_FIXTURE_DIR / "developer-agreement.csv"
    if agreement_path.exists():
        with agreement_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                entity = (row.get("organisation-entity") or "").strip()
                if entity:
                    per_entity[entity] += 1

    publishing_all_three = set.intersection(*entities_by_dataset.values()) if entities_by_dataset else set()

    benchmark: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sheffield": {
            "organisation_code": SHEFFIELD_ORG_CODE,
            "statutory_datasets_live": 0,
            "statutory_datasets_required": len(STATUTORY_ENDPOINT_DATASETS),
            "rows_produced_by_this_audit": generated_for_sheffield,
        },
        "national": {
            "datasets": national_datasets,
            "distinct_publishing_authorities": len(all_entities),
            "authorities_publishing_all_three": len(publishing_all_three),
            "top_publishers_by_agreement_rows": [
                {"organisation_entity": entity, "agreements": count}
                for entity, count in per_entity.most_common(10)
            ],
        },
    }
    benchmark["summary"] = (
        f"Sheffield publishes 0 of {len(STATUTORY_ENDPOINT_DATASETS)} statutory "
        f"developer-contribution datasets, while "
        f"{benchmark['national']['distinct_publishing_authorities']} authorities publish to the "
        f"national collection and "
        f"{benchmark['national']['authorities_publishing_all_three']} publish all three. "
        f"This audit generates {sum(generated_for_sheffield.values())} rows across the three "
        f"files, closing the gap in one pass."
    )
    _BENCHMARK_CACHE = benchmark
    return benchmark
