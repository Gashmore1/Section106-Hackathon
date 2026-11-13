"""
SINGLE SOURCE OF TRUTH: Shared Pydantic v2 Schemas for Sheffield Section 106 Auditor.
Do not modify field names without team sync across Track 1 (Data), Track 2 (Agents), and Track 3 (App).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class IndexationMethod(str, Enum):
    """Inflation indexation method specified in the S106 agreement."""

    NONE = "NONE"
    RPI = "RPI"
    CPI = "CPI"
    BCIS = "BCIS"


class TriggerType(str, Enum):
    """Legal/physical trigger type for a developer contribution."""

    PRIOR_TO_COMMENCEMENT = "PRIOR_TO_COMMENCEMENT"
    PRIOR_TO_OCCUPATION = "PRIOR_TO_OCCUPATION"
    DWELLING_OCCUPATION_THRESHOLD = "DWELLING_OCCUPATION_THRESHOLD"
    PHASE_COMPLETION = "PHASE_COMPLETION"
    ON_SIGNING = "ON_SIGNING"
    CUSTOM_DATE = "CUSTOM_DATE"


class ContributionCategory(str, Enum):
    """Standardized UK Developer Contribution purpose categories."""

    AFFORDABLE_HOUSING = "AFFORDABLE_HOUSING"
    EDUCATION = "EDUCATION"
    HIGHWAYS_AND_TRANSPORT = "HIGHWAYS_AND_TRANSPORT"
    OPEN_SPACE_AND_BIODIVERSITY = "OPEN_SPACE_AND_BIODIVERSITY"
    HEALTHCARE = "HEALTHCARE"
    COMMUNITY_INFRASTRUCTURE = "COMMUNITY_INFRASTRUCTURE"
    MONITORING_FEE = "MONITORING_FEE"


class AuditStatus(str, Enum):
    """Lifecycle audit status of an obligation."""

    COMPLIANT = "COMPLIANT"
    TRIGGERED_UNPAID = "TRIGGERED_UNPAID"
    UNDERPAID_INDEXATION = "UNDERPAID_INDEXATION"
    PENDING_TRIGGER = "PENDING_TRIGGER"
    OVERDUE = "OVERDUE"


class BoundingBox(BaseModel):
    """PDF page coordinates (in points, [x0, y0, x1, y1]) from PyMuPDF."""

    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(..., ge=1, description="1-indexed PDF page number")
    x0: float = Field(..., description="Left X coordinate in points")
    y0: float = Field(..., description="Top Y coordinate in points")
    x1: float = Field(..., description="Right X coordinate in points")
    y1: float = Field(..., description="Bottom Y coordinate in points")


class ClauseEvidence(BaseModel):
    """Traceable legal quote and PDF location for an extracted obligation."""

    model_config = ConfigDict(extra="forbid")

    clause_reference: str = Field(
        ..., description="Schedule and paragraph ref, e.g. 'Schedule 2, Para 3.1'"
    )
    verbatim_quote: str = Field(
        ..., description="Exact substring extracted from the deed PDF"
    )
    page_number: int = Field(..., ge=1, description="1-indexed PDF page number")
    bbox: Optional[BoundingBox] = Field(
        default=None, description="Bounding box coordinates on the PDF page"
    )


class TriggerCondition(BaseModel):
    """Structured representation of when an obligation becomes payable."""

    model_config = ConfigDict(extra="forbid")

    trigger_type: TriggerType = Field(..., description="Category of trigger event")
    description: str = Field(
        ..., description="Human-readable trigger condition from the deed"
    )
    dwelling_threshold: Optional[int] = Field(
        default=None,
        description="If triggered by occupation of Nth dwelling, the integer N (e.g. 50)",
    )
    prior_to_event: Optional[str] = Field(
        default=None,
        description="Event description such as 'Commencement of Development' or 'Occupation of 50th Dwelling'",
    )


class ExtractedObligation(BaseModel):
    """Single S106 financial or non-financial obligation extracted by vLLM."""

    model_config = ConfigDict(extra="forbid")

    obligation_id: str = Field(
        ..., description="Unique obligation identifier within the agreement"
    )
    category: ContributionCategory = Field(
        ..., description="Purpose category of the developer contribution"
    )
    title: str = Field(..., description="Short descriptive title of the contribution")
    base_amount_gbp: float = Field(
        ..., ge=0.0, description="Nominal base contribution in GBP stated in the deed"
    )
    indexation_method: IndexationMethod = Field(
        default=IndexationMethod.NONE,
        description="Inflation indexation basis (RPI, CPI, BCIS, or NONE)",
    )
    index_base_date: Optional[date] = Field(
        default=None,
        description="Base date for indexation calculation (typically deed date or valuation date)",
    )
    trigger: TriggerCondition = Field(
        ..., description="Structured trigger condition for payment"
    )
    evidence: ClauseEvidence = Field(
        ..., description="Verbatim legal clause quote and PDF coordinates"
    )


class ExtractedAgreement(BaseModel):
    """Complete structured output from an S106 PDF deed."""

    model_config = ConfigDict(extra="forbid")

    planning_reference: str = Field(
        ..., description="Sheffield planning application reference, e.g. '21/04512/FUL' or 'UNKNOWN'"
    )
    agreement_date: date = Field(..., description="Date the S106 deed was executed")
    site_name: str = Field(..., description="Development site name / address")
    ward_name: str = Field(..., description="Sheffield electoral ward name")
    developer_name: str = Field(..., description="Lead developer / obligor legal entity")
    total_dwellings_permitted: int = Field(
        default=0, ge=0, description="Total residential units permitted on site"
    )
    extraction_status: Literal[
        "EXTRACTED",
        "AMBIGUOUS_REQUIRES_REVIEW",
        "INSUFFICIENT_EVIDENCE_ABSTAIN",
    ] = Field(
        default="EXTRACTED",
        description=(
            "EXTRACTED when obligations are clear; AMBIGUOUS_REQUIRES_REVIEW when clauses "
            "contain conflicting sums/triggers; INSUFFICIENT_EVIDENCE_ABSTAIN ('I don't know') "
            "when the input is not an S106 obligation clause or lacks sufficient information."
        ),
    )
    abstention_or_ambiguity_reason: Optional[str] = Field(
        default=None,
        description="Explanation when extraction_status is AMBIGUOUS_REQUIRES_REVIEW or INSUFFICIENT_EVIDENCE_ABSTAIN",
    )
    obligations: list[ExtractedObligation] = Field(
        default_factory=list, description="List of extracted developer obligations"
    )


class IndexationCalculationResult(BaseModel):
    """Deterministic SQL calculation result for inflation-adjusted S106 sums."""

    model_config = ConfigDict(extra="forbid")

    obligation_id: str
    indexation_method: IndexationMethod
    base_date: date
    audit_date: date
    base_index_value: float
    current_index_value: float
    multiplier: float
    base_amount_gbp: float
    indexed_amount_gbp: float
    indexation_uplift_gbp: float


class AuditDiscrepancy(BaseModel):
    """Audit finding comparing deed obligations, site telemetry, and council ledger."""

    model_config = ConfigDict(extra="forbid")

    obligation_id: str
    planning_reference: str
    category: ContributionCategory
    title: str
    status: AuditStatus
    base_amount_gbp: float
    indexed_amount_gbp: float
    amount_received_gbp: float
    shortfall_gbp: float
    dwellings_occupied_current: int
    trigger_threshold: Optional[int] = None
    finding_summary: str
    evidence: ClauseEvidence


class AuditReport(BaseModel):
    """Complete multi-agent audit output for a single S106 agreement."""

    model_config = ConfigDict(extra="forbid")

    planning_reference: str
    site_name: str
    ward_name: str
    developer_name: str
    agreement_date: date
    audit_timestamp: datetime
    dwellings_permitted: int
    dwellings_occupied: int
    total_base_gbp: float
    total_indexed_gbp: float
    total_received_gbp: float
    total_recoverable_shortfall_gbp: float
    discrepancies: list[AuditDiscrepancy]


class WardSummary(BaseModel):
    """PostGIS ward-level aggregation for the Sheffield spatial dashboard."""

    model_config = ConfigDict(extra="forbid")

    ward_code: str
    ward_name: str
    agreements_count: int
    total_agreed_gbp: float
    total_indexed_gbp: float
    total_received_gbp: float
    uncollected_shortfall_gbp: float
    geojson: dict[str, Any]


class AgentTelemetryEvent(BaseModel):
    """Server-Sent Event (SSE) payload streamed to the frontend during LangGraph runs."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    timestamp: datetime
    agent_node: Literal[
        "pdf_parser",
        "vllm_extractor",
        "sql_indexation_auditor",
        "postgis_spatial_checker",
        "report_synthesizer",
    ]
    status: Literal["STARTED", "IN_PROGRESS", "COMPLETED", "ERROR"]
    message: str
    payload: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Statutory UK Government Open Data Schemas (planning.data.gov.uk)
# Sheffield City Council currently has 0/3 endpoints live.
# ---------------------------------------------------------------------------


class DeveloperAgreementCSVRow(BaseModel):
    """Statutory schema 1/3: developer-agreement.csv"""

    reference: str = Field(..., description="Unique developer agreement reference")
    Organisation: str = Field(
        default="local-authority:SHF", description="Sheffield City Council OGL code"
    )
    entry_date: date = Field(..., serialization_alias="entry-date")
    start_date: date = Field(..., serialization_alias="start-date")
    end_date: Optional[date] = Field(default=None, serialization_alias="end-date")
    planning_application: str = Field(..., serialization_alias="planning-application")
    document_url: str = Field(..., serialization_alias="document-url")
    developer_agreement_type: str = Field(
        default="section-106", serialization_alias="developer-agreement-type"
    )


class DeveloperAgreementContributionCSVRow(BaseModel):
    """Statutory schema 2/3: developer-agreement-contribution.csv"""

    reference: str
    developer_agreement: str = Field(..., serialization_alias="developer-agreement")
    contribution_purpose: str = Field(..., serialization_alias="contribution-purpose")
    amount: float
    units: Optional[int] = None
    entry_date: date = Field(..., serialization_alias="entry-date")
    start_date: date = Field(..., serialization_alias="start-date")


class DeveloperAgreementTransactionCSVRow(BaseModel):
    """Statutory schema 3/3: developer-agreement-transaction.csv"""

    reference: str
    developer_agreement_contribution: str = Field(
        ..., serialization_alias="developer-agreement-contribution"
    )
    contribution_funding_status: Literal[
        "agreed", "received", "allocated", "spent", "transferred", "returned"
    ] = Field(..., serialization_alias="contribution-funding-status")
    amount: float
    units: Optional[int] = None
    entry_date: date = Field(..., serialization_alias="entry-date")
    start_date: date = Field(..., serialization_alias="start-date")


# Backward-compatible aliases matching early design docs
ExtractedCovenant = ExtractedObligation
ExtractionOutput = ExtractedAgreement
FullAuditReport = AuditReport
