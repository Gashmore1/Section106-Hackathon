"""
Hackathon Evaluation Suite: Test `nemotron-3.5-lightning:30b` on 5 S106 Inputs.

Per the hackathon rules:
"Pick a model and test it on five inputs, including an ambiguous one and one where 'I don't know' is right."

Run with the organiser SSH tunnel open (`ssh -N -L 127.0.0.1:11435:127.0.0.1:11434 USER@SPARK_ADDRESS`):
    python -m scripts.test_five_inputs
"""

from __future__ import annotations

import asyncio
from typing import Any

from agents.extractor import VLLM_BASE_URL, VLLM_MODEL_NAME, extract_s106_obligations_vllm

FIVE_TEST_INPUTS: list[dict[str, Any]] = [
    {
        "id": "INPUT-1-CLEAR-BCIS",
        "title": "1. Clear BCIS-Indexed Affordable Housing Tranche (Kelham Riverside)",
        "expected_status": "EXTRACTED",
        "raw_text": (
            "[Page 18 | BBox (72.0, 215.0, 523.0, 295.0)] "
            "THIS DEED is dated 1 January 2021 regarding Planning Application 21/04512/FUL at "
            "Kelham Riverside Foundry Quarter, Walkley Ward, Sheffield (240 Permitted Dwellings) "
            "by Hallamshire Urban Regeneration Developments Ltd. "
            "Schedule 1, Part 2, Paragraph 4.1: The Owner covenants with the Council to pay "
            "50% of the Affordable Housing Commuted Sum (£450,000 Index Linked in accordance "
            "with the BCIS All-in Tender Price Index from the date of this Deed, 2021-01-01) "
            "prior to the Occupation of the 50th Market Dwelling constructed on the Site."
        ),
    },
    {
        "id": "INPUT-2-CLEAR-RPI",
        "title": "2. Clear RPI-Indexed Secondary Education Contribution",
        "expected_status": "EXTRACTED",
        "raw_text": (
            "[Page 24 | BBox (72.0, 140.0, 523.0, 210.0)] "
            "Deed dated 2021-01-01 for Application 21/04512/FUL (Kelham Riverside, Walkley Ward, "
            "Developer: Hallamshire Urban Regeneration Developments Ltd, 240 Dwellings). "
            "Schedule 2, Paragraph 2.1: Not to Occupy or permit the Occupation of more than "
            "74 Dwellings (trigger at 75th Dwelling) until the Education Contribution of "
            "£285,000 (Index Linked by RPI from 2021-01-01) has been paid in full to the Council."
        ),
    },
    {
        "id": "INPUT-3-CLEAR-HIGHWAYS",
        "title": "3. Clear West Bar Highways & Active Travel Contribution",
        "expected_status": "EXTRACTED",
        "raw_text": (
            "[Page 22 | BBox (72.0, 180.0, 523.0, 248.0)] "
            "Section 106 Agreement dated 2020-01-01 for Application 20/01984/OUT at West Bar "
            "Gateway Tower Phase 2, City Ward, Sheffield by Pennine Commercial Estates PLC "
            "(360 Permitted Residential Units). "
            "Schedule 2, Paragraph 5.1: The Developer shall pay the Highways & Active Travel "
            "Contribution of £390,000 (BCIS Index Linked from 2020-01-01) prior to Occupation "
            "of the 150th Residential Unit."
        ),
    },
    {
        "id": "INPUT-4-AMBIGUOUS-DRAFT",
        "title": "4. AMBIGUOUS Input: Unresolved Draft Deed with Conflicting Sums & Triggers",
        "expected_status": "AMBIGUOUS_REQUIRES_REVIEW",
        "raw_text": (
            "[Page 14 | BBox (72.0, 110.0, 523.0, 260.0)] "
            "DRAFT UNEXECUTED DEED (2023-06-01) — Application 23/09999/FUL, Hillsborough Yard, "
            "Hillsborough Ward, Sheffield (Developer: Northern Spire Developments Ltd, 120 units). "
            "Schedule 1, Paragraph 3.1 [TRACKED CHANGES CONFLICT]: The Owner shall pay the "
            "Open Space Sum of [£250,000 OR £140,000 — TBC BY VIABILITY ASSESSOR] indexed by "
            "[RPI / CPI — DELETE AS APPLICABLE] prior to Occupation of either the [30th OR 90th] "
            "Dwelling, whereas Sub-Clause 3.1(b) states no Open Space Sum is payable if Phase 1 "
            "is occupied first."
        ),
    },
    {
        "id": "INPUT-5-IDK-NO-S106",
        "title": "5. 'I DON'T KNOW' Input: Arboricultural Tree Survey Memo (No S106 Obligations)",
        "expected_status": "INSUFFICIENT_EVIDENCE_ABSTAIN",
        "raw_text": (
            "[Page 1 | BBox (72.0, 90.0, 523.0, 195.0)] "
            "SHEFFIELD CITY COUNCIL — ARBORICULTURAL SITE INSPECTION NOTE (BS 5837:2012). "
            "Three mature English Oak specimens (T1, T2, T3) along the northern boundary fence "
            "exhibit moderate crown dieback and require 15% deadwood pruning prior to protective "
            "heras fencing installation. Root Protection Areas (RPAs) must not be compacted by "
            "plant machinery. No Section 106 agreement, developer contribution, or financial "
            "covenant is mentioned or contained in this document."
        ),
    },
]


async def run_five_input_benchmark() -> None:
    print("=" * 88)
    print(f"DGX Spark 5-Input S106 Benchmark | Model: {VLLM_MODEL_NAME} | Endpoint: {VLLM_BASE_URL}")
    print("=" * 88)

    for item in FIVE_TEST_INPUTS:
        print(f"\n>>> {item['title']}")
        print(f"    Expected status: {item['expected_status']}")
        result = await extract_s106_obligations_vllm(
            raw_text=item["raw_text"],
            planning_reference="21/04512/FUL",
        )
        print(f"    Actual status:   {result.extraction_status}")
        print(f"    Obligations:     {len(result.obligations)}")
        if result.abstention_or_ambiguity_reason:
            print(f"    Reason/Note:     {result.abstention_or_ambiguity_reason}")
        for obl in result.obligations:
            print(
                f"      - [{obl.obligation_id}] {obl.category.value}: "
                f"£{obl.base_amount_gbp:,.2f} ({obl.indexation_method.value}) "
                f"@ threshold={obl.trigger.dwelling_threshold}"
            )


if __name__ == "__main__":
    asyncio.run(run_five_input_benchmark())
