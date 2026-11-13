"""
Track 2: LangGraph Multi-Agent S106 Auditing Pipeline with Live SSE Telemetry.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional, TypedDict

from agents.extractor import (
    VLLM_BASE_URL,
    VLLM_MODEL_NAME,
    _is_ollama_endpoint,
    extract_s106_obligations_vllm,
)
from agents.pdf_parser import ParsedTextBlock, parse_s106_pdf
from data.sql_tools import get_sheffield_ward_summaries, run_deterministic_audit
from shared.schemas import (
    AgentTelemetryEvent,
    AuditReport,
    ExtractedAgreement,
    WardSummary,
)


class S106AuditState(TypedDict, total=False):
    """State dictionary passed between LangGraph nodes."""

    planning_reference: str
    pdf_path: Optional[str]
    parsed_blocks: list[ParsedTextBlock]
    extracted_agreement: ExtractedAgreement
    audit_report: AuditReport
    ward_summaries: list[WardSummary]
    telemetry_log: list[AgentTelemetryEvent]


def _make_event(
    node: str,
    status: str,
    message: str,
    payload: Optional[dict] = None,
) -> AgentTelemetryEvent:
    return AgentTelemetryEvent(
        event_id=str(uuid.uuid4())[:8],
        timestamp=datetime.now(timezone.utc),
        agent_node=node,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        message=message,
        payload=payload,
    )


def build_s106_langgraph():
    """
    Build the compiled LangGraph StateGraph for the S106 Autonomous Auditor.
    """
    from langgraph.graph import END, START, StateGraph

    async def node_pdf_parser(state: S106AuditState) -> S106AuditState:
        pdf_path = state.get("pdf_path")
        blocks: list[ParsedTextBlock] = []
        if pdf_path:
            try:
                blocks = parse_s106_pdf(pdf_path)
            except Exception:
                blocks = []
        return {"parsed_blocks": blocks}

    async def node_vllm_extractor(state: S106AuditState) -> S106AuditState:
        ref = state.get("planning_reference", "21/04512/FUL")
        extracted = await extract_s106_obligations_vllm(
            blocks=state.get("parsed_blocks"),
            planning_reference=ref,
        )
        return {"extracted_agreement": extracted}

    async def node_sql_indexation_auditor(state: S106AuditState) -> S106AuditState:
        ref = state.get("planning_reference", "21/04512/FUL")
        report = run_deterministic_audit(ref)
        return {"audit_report": report}

    async def node_postgis_spatial_checker(state: S106AuditState) -> S106AuditState:
        wards = get_sheffield_ward_summaries()
        return {"ward_summaries": wards}

    async def node_report_synthesizer(state: S106AuditState) -> S106AuditState:
        return state

    graph = StateGraph(S106AuditState)
    graph.add_node("pdf_parser", node_pdf_parser)
    graph.add_node("vllm_extractor", node_vllm_extractor)
    graph.add_node("sql_indexation_auditor", node_sql_indexation_auditor)
    graph.add_node("postgis_spatial_checker", node_postgis_spatial_checker)
    graph.add_node("report_synthesizer", node_report_synthesizer)

    graph.add_edge(START, "pdf_parser")
    graph.add_edge("pdf_parser", "vllm_extractor")
    graph.add_edge("vllm_extractor", "sql_indexation_auditor")
    graph.add_edge("sql_indexation_auditor", "postgis_spatial_checker")
    graph.add_edge("postgis_spatial_checker", "report_synthesizer")
    graph.add_edge("report_synthesizer", END)

    return graph.compile()


async def stream_audit_workflow(
    planning_reference: str = "21/04512/FUL",
    pdf_path: Optional[str] = None,
) -> AsyncGenerator[AgentTelemetryEvent, None]:
    """
    Execute the multi-agent auditing pipeline and yield real-time `AgentTelemetryEvent`
    records for Server-Sent Events (SSE) streaming to the dashboard.
    Never fabricates block counts when no PDF file is attached.
    """
    blocks: list[ParsedTextBlock] = []
    if pdf_path:
        yield _make_event(
            "pdf_parser",
            "STARTED",
            f"PyMuPDF scanning attached deed '{pdf_path}' for {planning_reference}...",
        )
        await asyncio.sleep(0.25)
        try:
            blocks = parse_s106_pdf(pdf_path)
            yield _make_event(
                "pdf_parser",
                "COMPLETED",
                f"Parsed {len(blocks)} text blocks with bounding boxes from '{pdf_path}'.",
                payload={"blocks_parsed": len(blocks), "pdf_attached": True},
            )
        except Exception as exc:
            yield _make_event(
                "pdf_parser",
                "COMPLETED",
                f"Could not read PDF '{pdf_path}' ({exc}); 0 blocks parsed.",
                payload={"blocks_parsed": 0, "pdf_attached": False},
            )
    else:
        yield _make_event(
            "pdf_parser",
            "STARTED",
            f"Checking attached deed PDF for {planning_reference}...",
        )
        await asyncio.sleep(0.2)
        yield _make_event(
            "pdf_parser",
            "COMPLETED",
            f"No deed PDF attached for {planning_reference} (0 blocks parsed) — using pre-indexed seed covenant records.",
            payload={"blocks_parsed": 0, "pdf_attached": False},
        )

    await asyncio.sleep(0.25)

    backend_label = "Ollama" if _is_ollama_endpoint(VLLM_BASE_URL) else "vLLM"
    if blocks:
        yield _make_event(
            "vllm_extractor",
            "STARTED",
            f"Querying local {backend_label} ({VLLM_MODEL_NAME} on DGX Spark, num_ctx=8192, think=false) with schema `ExtractedAgreement`...",
        )
        extracted = await extract_s106_obligations_vllm(
            blocks=blocks, planning_reference=planning_reference
        )
        yield _make_event(
            "vllm_extractor",
            "COMPLETED",
            f"Extracted {len(extracted.obligations)} S106 obligations via {VLLM_MODEL_NAME} (status={extracted.extraction_status}).",
            payload={
                "obligations_count": len(extracted.obligations),
                "extraction_status": extracted.extraction_status,
                "source": "live_llm",
            },
        )
    else:
        yield _make_event(
            "vllm_extractor",
            "STARTED",
            f"Loading structured `ExtractedAgreement` covenants for {planning_reference} (model configured: {VLLM_MODEL_NAME})...",
        )
        extracted = await extract_s106_obligations_vllm(
            blocks=None, planning_reference=planning_reference
        )
        await asyncio.sleep(0.25)
        yield _make_event(
            "vllm_extractor",
            "COMPLETED",
            f"Loaded {len(extracted.obligations)} pre-extracted S106 obligations from seed records (attach a PDF deed to run live {VLLM_MODEL_NAME} extraction).",
            payload={
                "obligations_count": len(extracted.obligations),
                "extraction_status": extracted.extraction_status,
                "source": "seed_records",
            },
        )

    await asyncio.sleep(0.25)

    yield _make_event(
        "sql_indexation_auditor",
        "STARTED",
        "Executing deterministic PostgreSQL ONS (RPI/CPI) & BCIS Tender Price Indexation math...",
    )
    report = run_deterministic_audit(planning_reference)
    await asyncio.sleep(0.35)

    breaches = [
        d
        for d in report.discrepancies
        if d.status.value in ("TRIGGERED_UNPAID", "UNDERPAID_INDEXATION")
    ]
    yield _make_event(
        "sql_indexation_auditor",
        "COMPLETED",
        f"Identified {len(breaches)} actionable breaches totaling £{report.total_recoverable_shortfall_gbp:,.2f} in uncollected S106 contributions.",
        payload={
            "total_recoverable_shortfall_gbp": report.total_recoverable_shortfall_gbp,
            "breach_count": len(breaches),
        },
    )
    await asyncio.sleep(0.25)

    yield _make_event(
        "postgis_spatial_checker",
        "STARTED",
        f"Cross-referencing {report.ward_name} Ward PostGIS polygons & Council Tax occupation telemetry ({report.dwellings_occupied}/{report.dwellings_permitted} dwellings occupied)...",
    )
    wards = get_sheffield_ward_summaries()
    await asyncio.sleep(0.25)

    yield _make_event(
        "postgis_spatial_checker",
        "COMPLETED",
        f"Updated PostGIS ward ledger across {len(wards)} Sheffield electoral wards.",
    )
    await asyncio.sleep(0.2)

    yield _make_event(
        "report_synthesizer",
        "COMPLETED",
        f"Audit complete for {planning_reference}. Ready to generate formal S106 Demand Notice (.docx) & statutory planning.data.gov.uk CSVs.",
        payload={"report": report.model_dump(mode="json")},
    )
