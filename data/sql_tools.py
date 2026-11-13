"""
Track 1: Deterministic SQL & PostGIS Financial/Spatial Tools.
All inflation indexation and shortfall math is computed deterministically here—never by an LLM.
Automatically uses PostgreSQL 16 when reachable, with seamless fallback to the canonical seed dataset.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)
_WARNED_DB_FALLBACK = False


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
    SEED_AGREEMENTS,
    SEED_INFLATION_INDICES,
    SEED_OBLIGATIONS,
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
    """Return all S106 agreements from PostgreSQL or canonical seed fallback."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(select(S106AgreementRecord)).mappings().all()
            if rows:
                return [dict(r) for r in rows]
    except Exception as exc:
        _warn_db_fallback_once(exc)
    return [dict(a) for a in SEED_AGREEMENTS]


def fetch_obligations_for_agreement(planning_reference: str) -> list[dict]:
    """Return obligations for a given planning reference."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            stmt = select(S106ObligationRecord).where(
                S106ObligationRecord.planning_reference == planning_reference
            )
            rows = conn.execute(stmt).mappings().all()
            if rows:
                result = []
                for r in rows:
                    d = dict(r)
                    d["bbox"] = json.loads(d.pop("bbox_json", "{}") or "{}")
                    result.append(d)
                return result
    except Exception as exc:
        _warn_db_fallback_once(exc)
    return [
        dict(o)
        for o in SEED_OBLIGATIONS
        if o["planning_reference"] == planning_reference
    ]


def persist_extracted_agreement(
    extracted: ExtractedAgreement,
    dwellings_occupied_current: Optional[int] = None,
    document_url: str = "",
) -> None:
    """
    Persist a newly extracted `ExtractedAgreement` (from live Nemotron PDF extraction)
    into the canonical runtime ledger (and PostgreSQL when connected) so subsequent
    audits, ward rollups, and CSV exports include it.
    """
    if extracted.extraction_status != "EXTRACTED" or not extracted.obligations:
        return

    existing_agr = next(
        (
            a
            for a in SEED_AGREEMENTS
            if a["planning_reference"] == extracted.planning_reference
        ),
        None,
    )
    occupied = (
        dwellings_occupied_current
        if dwellings_occupied_current is not None
        else (
            int(existing_agr["dwellings_occupied_current"])
            if existing_agr
            else max(1, extracted.total_dwellings_permitted // 2)
        )
    )

    agr_dict = {
        "planning_reference": extracted.planning_reference,
        "agreement_date": extracted.agreement_date,
        "site_name": extracted.site_name,
        "ward_name": extracted.ward_name,
        "developer_name": extracted.developer_name,
        "total_dwellings_permitted": extracted.total_dwellings_permitted,
        "dwellings_occupied_current": occupied,
        "document_url": document_url
        or (existing_agr["document_url"] if existing_agr else ""),
    }
    if existing_agr is None:
        SEED_AGREEMENTS.append(agr_dict)

    existing_obl_ids = {o["obligation_id"] for o in SEED_OBLIGATIONS}
    for obl in extracted.obligations:
        if obl.obligation_id in existing_obl_ids:
            continue
        SEED_OBLIGATIONS.append(
            {
                "obligation_id": obl.obligation_id,
                "planning_reference": extracted.planning_reference,
                "category": obl.category.value,
                "title": obl.title,
                "base_amount_gbp": obl.base_amount_gbp,
                "indexation_method": obl.indexation_method.value,
                "index_base_date": obl.index_base_date or extracted.agreement_date,
                "trigger_type": obl.trigger.trigger_type.value,
                "trigger_description": obl.trigger.description,
                "dwelling_threshold": obl.trigger.dwelling_threshold,
                "amount_received_gbp": 0.0,
                "clause_reference": obl.evidence.clause_reference,
                "verbatim_quote": obl.evidence.verbatim_quote,
                "page_number": obl.evidence.page_number,
                "bbox": obl.evidence.bbox.model_dump() if obl.evidence.bbox else {},
            }
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
        agr = matched_agr or agreements[0]
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
