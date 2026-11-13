"""
Track 3: FastAPI Server with Server-Sent Events (SSE) for Live Agent Telemetry,
Statutory `planning.data.gov.uk` CSV Exporters (3/3 endpoints),
and Automated Legal S106 Demand Notice (.docx) Generation.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Literal

from docx import Document
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from agents.extractor import (
    AVAILABLE_SPARK_MODELS,
    VLLM_BASE_URL,
    VLLM_MODEL_NAME,
    _is_ollama_endpoint,
)
from agents.workflow import stream_audit_workflow
from data.sql_tools import (
    fetch_all_agreements,
    generate_statutory_csv_rows,
    get_sheffield_ward_summaries,
    run_deterministic_audit,
)

FRONTEND_DIR = Path(__file__).parent / "frontend"

app = FastAPI(
    title="Sheffield Section 106 & Infrastructure Levy Autonomous Auditor",
    description=(
        "Air-gapped multi-agent legal & financial auditing pipeline running locally "
        "on NVIDIA DGX Spark (Grace Blackwell GB10, ARM64)."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the Split-Screen S106 Auditor Dashboard."""
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend dashboard not found.")
    return FileResponse(index_file)


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "architecture": "linux/arm64 (NVIDIA Grace Blackwell GB10)",
        "llm_backend": "ollama" if _is_ollama_endpoint(VLLM_BASE_URL) else "vllm",
        "llm_endpoint": VLLM_BASE_URL,
        "active_model": VLLM_MODEL_NAME,
        "available_spark_models": list(AVAILABLE_SPARK_MODELS),
        "statutory_csv_endpoints_ready": 3,
    }


@app.get("/api/agreements")
async def list_agreements():
    """List all Sheffield S106 agreements available in the database."""
    return fetch_all_agreements()


@app.get("/api/wards")
async def list_ward_summaries():
    """Return Sheffield Electoral Ward PostGIS GeoJSON polygons and S106 financial rollups."""
    return [w.model_dump(mode="json") for w in get_sheffield_ward_summaries()]


@app.get("/api/audit/stream")
async def audit_sse_stream(
    planning_reference: str = Query(
        default="21/04512/FUL",
        description="Sheffield planning application reference to audit",
    ),
):
    """
    Server-Sent Events (SSE) stream emitting real-time LangGraph agent telemetry
    during PDF parsing, vLLM extraction, SQL indexation auditing, and PostGIS checks.
    """

    async def event_generator():
        async for event in stream_audit_workflow(planning_reference=planning_reference):
            payload_json = event.model_dump_json()
            yield f"data: {payload_json}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/audit")
async def get_audit_report(
    planning_reference: str = Query(
        default="21/04512/FUL",
        description="Sheffield planning application reference",
    ),
):
    """Run deterministic audit and return the full `AuditReport` JSON."""
    report = run_deterministic_audit(planning_reference)
    return report.model_dump(mode="json")


@app.get("/api/export/csv/{dataset}")
async def export_statutory_csv(
    dataset: Literal[
        "developer-agreement",
        "developer-agreement-contribution",
        "developer-agreement-transaction",
    ],
):
    """
    Export one of the 3 statutory UK Government Open Data CSV files required by
    `planning.data.gov.uk` (Sheffield currently has 0/3 live).
    """
    all_datasets = generate_statutory_csv_rows()
    rows = all_datasets.get(dataset)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Dataset {dataset} not found")

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

    csv_bytes = io.BytesIO(output.getvalue().encode("utf-8"))
    filename = f"sheffield-{dataset}.csv"
    return StreamingResponse(
        csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/demand-notice")
async def export_demand_notice_docx(
    planning_reference: str = Query(default="21/04512/FUL"),
):
    """
    Generate a formal Sheffield City Council Legal Services Section 106 Demand Notice (.docx)
    detailing triggered obligations, ONS/BCIS indexation uplifts, and verbatim deed clauses.
    """
    report = run_deterministic_audit(planning_reference)

    doc = Document()
    doc.add_heading(
        "SHEFFIELD CITY COUNCIL — LEGAL & PLANNING INFRASTRUCTURE SERVICES", level=1
    )
    doc.add_heading(
        f"FORMAL SECTION 106 DEMAND NOTICE: {report.planning_reference}", level=2
    )

    meta = doc.add_paragraph()
    meta.add_run("Site Address: ").bold = True
    meta.add_run(f"{report.site_name} ({report.ward_name} Ward)\n")
    meta.add_run("Obligor / Developer: ").bold = True
    meta.add_run(f"{report.developer_name}\n")
    meta.add_run("Deed Date: ").bold = True
    meta.add_run(f"{report.agreement_date.isoformat()}\n")
    meta.add_run("Verified Site Occupation: ").bold = True
    meta.add_run(
        f"{report.dwellings_occupied} / {report.dwellings_permitted} Permitted Dwellings Occupied\n"
    )
    meta.add_run("Total Uncollected / Overdue Sum Demanded: ").bold = True
    meta.add_run(f"£{report.total_recoverable_shortfall_gbp:,.2f}")

    doc.add_heading("1. Schedule of Audited Obligations & Indexation", level=2)
    table = doc.add_table(rows=1, cols=6)
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "Obligation ID"
    hdr_cells[1].text = "Clause Ref"
    hdr_cells[2].text = "Base Sum (£)"
    hdr_cells[3].text = "Indexed Sum (£)"
    hdr_cells[4].text = "Received (£)"
    hdr_cells[5].text = "Shortfall Due (£)"

    for disc in report.discrepancies:
        row_cells = table.add_row().cells
        row_cells[0].text = disc.obligation_id
        row_cells[1].text = disc.evidence.clause_reference
        row_cells[2].text = f"£{disc.base_amount_gbp:,.2f}"
        row_cells[3].text = f"£{disc.indexed_amount_gbp:,.2f}"
        row_cells[4].text = f"£{disc.amount_received_gbp:,.2f}"
        row_cells[5].text = f"£{disc.shortfall_gbp:,.2f}"

    doc.add_heading("2. Evidentiary Deed Clauses & Findings", level=2)
    for disc in report.discrepancies:
        p = doc.add_paragraph()
        p.add_run(
            f"[{disc.status.value}] {disc.title} ({disc.evidence.clause_reference}, Page {disc.evidence.page_number})\n"
        ).bold = True
        p.add_run(f"Audit Finding: {disc.finding_summary}\n")
        p.add_run(f'Verbatim Covenant: "{disc.evidence.verbatim_quote}"')

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)

    safe_ref = report.planning_reference.replace("/", "-")
    filename = f"Sheffield_S106_Demand_Notice_{safe_ref}.docx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
