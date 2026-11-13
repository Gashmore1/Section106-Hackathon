"""
Hackathon Evaluation Suite: Test `nemotron-3.5-lightning:30b` on the 5 PDF Fixtures.

Per the hackathon rules:
"Pick a model and test it on five inputs, including an ambiguous one and one where 'I don't know' is right."

Each test case runs the full Track 2 & Track 1 pipeline on an actual `.pdf` file in `data/fixtures/pdfs/`:
1. `parse_s106_pdf(pdf_path)` — PyMuPDF (`fitz`) text block & bounding box extraction
2. `filter_relevant_s106_blocks(blocks)` — Smart schedule/covenant filtering to fit the 8,192-token window
3. `extract_s106_obligations_vllm(blocks=filtered_blocks)` — Local `nemotron-3.5-lightning:30b` structured extraction
4. `run_deterministic_audit(planning_reference, extracted_agreement=result)` — Deterministic ONS/BCIS SQL math

Usage (with organiser SSH tunnel open `ssh -N -L 127.0.0.1:11435:127.0.0.1:11434 USER@SPARK_ADDRESS`):
    python -m scripts.test_five_inputs
    python -m scripts.test_five_inputs --include-real-deeds
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from agents.extractor import VLLM_BASE_URL, VLLM_MODEL_NAME, extract_s106_obligations_vllm
from agents.pdf_parser import filter_relevant_s106_blocks, parse_s106_pdf
from data.sql_tools import run_deterministic_audit

PDF_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "pdfs"

FIVE_PDF_TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "INPUT-1-CLEAR-KELHAM-BCIS",
        "title": "1. Clear Multi-Schedule Deed (Kelham Riverside — BCIS / RPI / CPI)",
        "pdf_filename": "21-04512-FUL-S106.pdf",
        "planning_reference": "21/04512/FUL",
        "expected_status": "EXTRACTED",
        "expected_min_obligations": 1,
    },
    {
        "id": "INPUT-2-CLEAR-WEST-BAR-HIGHWAYS",
        "title": "2. Clear Highways & Active Travel Deed (West Bar Gateway — BCIS)",
        "pdf_filename": "20-01984-OUT-S106.pdf",
        "planning_reference": "20/01984/OUT",
        "expected_status": "EXTRACTED",
        "expected_min_obligations": 1,
    },
    {
        "id": "INPUT-3-CLEAR-BANNER-CROSS-NHS",
        "title": "3. Clear NHS Primary Healthcare Deed (Banner Cross Parklands — RPI)",
        "pdf_filename": "22-03110-FUL-S106.pdf",
        "planning_reference": "22/03110/FUL",
        "expected_status": "EXTRACTED",
        "expected_min_obligations": 1,
    },
    {
        "id": "INPUT-4-AMBIGUOUS-DRAFT",
        "title": "4. AMBIGUOUS Input: Unexecuted Draft Deed with Conflicting Sums & Triggers",
        "pdf_filename": "23-09999-FUL-DRAFT-AMBIGUOUS.pdf",
        "planning_reference": "23/09999/FUL",
        "expected_status": "AMBIGUOUS_REQUIRES_REVIEW",
        "expected_min_obligations": 0,
    },
    {
        "id": "INPUT-5-IDK-ARBORICULTURAL",
        "title": "5. 'I DON'T KNOW' Input: Arboricultural Tree Survey PDF (No S106 Obligations)",
        "pdf_filename": "21-04512-FUL-ARBORICULTURAL-SURVEY.pdf",
        "planning_reference": "UNKNOWN",
        "expected_status": "INSUFFICIENT_EVIDENCE_ABSTAIN",
        "expected_min_obligations": 0,
    },
]


async def run_five_input_benchmark(include_real_deeds: bool = False) -> None:
    print("=" * 92)
    print(
        f"DGX Spark 5-Input PDF Benchmark | Model: {VLLM_MODEL_NAME} | Endpoint: {VLLM_BASE_URL}"
    )
    print("=" * 92)

    passed = 0
    for item in FIVE_PDF_TEST_CASES:
        pdf_path = PDF_FIXTURES_DIR / item["pdf_filename"]
        all_blocks = parse_s106_pdf(pdf_path)
        filtered_blocks = filter_relevant_s106_blocks(all_blocks)

        print(f"\n>>> {item['title']}")
        print(
            f"    PDF File:        {pdf_path.name} "
            f"({len(all_blocks)} total blocks -> {len(filtered_blocks)} high-signal blocks)"
        )
        print(f"    Expected status: {item['expected_status']}")

        result = await extract_s106_obligations_vllm(
            blocks=filtered_blocks,
            planning_reference=item["planning_reference"],
        )
        status_ok = result.extraction_status == item["expected_status"]
        if status_ok:
            passed += 1

        print(
            f"    Actual status:   {result.extraction_status} "
            f"[{'PASS' if status_ok else 'MISMATCH'}]"
        )
        print(f"    Obligations:     {len(result.obligations)}")
        if result.abstention_or_ambiguity_reason:
            print(f"    Reason/Note:     {result.abstention_or_ambiguity_reason}")

        for obl in result.obligations:
            bbox_str = (
                f"Page {obl.evidence.page_number} BBox({obl.evidence.bbox.x0}, {obl.evidence.bbox.y0})"
                if obl.evidence.bbox
                else f"Page {obl.evidence.page_number}"
            )
            print(
                f"      - [{obl.obligation_id}] {obl.category.value}: "
                f"£{obl.base_amount_gbp:,.2f} ({obl.indexation_method.value}) "
                f"@ threshold={obl.trigger.dwelling_threshold} | {bbox_str}"
            )

        if result.extraction_status == "EXTRACTED" and result.obligations:
            audit = run_deterministic_audit(
                item["planning_reference"], extracted_agreement=result
            )
            print(
                f"    Deterministic Audit: Base £{audit.total_base_gbp:,.2f} -> "
                f"Indexed £{audit.total_indexed_gbp:,.2f} | "
                f"Recoverable Shortfall: £{audit.total_recoverable_shortfall_gbp:,.2f}"
            )

    print("\n" + "=" * 92)
    print(f"Summary: {passed}/{len(FIVE_PDF_TEST_CASES)} required benchmark inputs matched expected status.")
    print("=" * 92)

    if include_real_deeds:
        real_dir = PDF_FIXTURES_DIR / "real"
        real_pdfs = sorted(real_dir.glob("*.pdf"))
        print(f"\nScanning {len(real_pdfs)} real UK S106 deeds in {real_dir}...")
        for rpdf in real_pdfs:
            r_blocks = parse_s106_pdf(rpdf)
            r_filtered = filter_relevant_s106_blocks(r_blocks)
            raw_chars = sum(len(b.text) for b in r_blocks)
            filt_chars = sum(len(b.text) for b in r_filtered)
            print(
                f"  • {rpdf.name}: {len(r_blocks)} blocks ({raw_chars:,} chars) "
                f"-> {len(r_filtered)} schedule/covenant blocks ({filt_chars:,} chars)"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the 5-input DGX Spark S106 PDF evaluation suite."
    )
    parser.add_argument(
        "--include-real-deeds",
        action="store_true",
        help="Also parse and filter the 5 real UK S106 PDFs in data/fixtures/pdfs/real/",
    )
    args = parser.parse_args()
    asyncio.run(run_five_input_benchmark(include_real_deeds=args.include_real_deeds))
