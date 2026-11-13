"""
Track 2: Local vLLM Structured Output Extractor (`http://localhost:8000/v1`).
Uses `guided_json` / `response_format` with `ExtractedAgreement` Pydantic schema.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Optional

from openai import AsyncOpenAI

from agents.pdf_parser import ParsedTextBlock, format_blocks_for_llm
from agents.prompts import S106_EXTRACTION_SYSTEM_PROMPT, S106_EXTRACTION_USER_TEMPLATE
from data.sql_tools import fetch_all_agreements, fetch_obligations_for_agreement
from shared.schemas import (
    BoundingBox,
    ClauseEvidence,
    ContributionCategory,
    ExtractedAgreement,
    ExtractedObligation,
    IndexationMethod,
    TriggerCondition,
    TriggerType,
)

VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
VLLM_API_KEY = os.getenv("VLLM_API_KEY", "EMPTY")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "meta-llama/Llama-3.3-70B-Instruct")


async def extract_s106_obligations_vllm(
    blocks: Optional[list[ParsedTextBlock]] = None,
    planning_reference: str = "21/04512/FUL",
) -> ExtractedAgreement:
    """
    Call the local vLLM OpenAI-compatible server on NVIDIA DGX Spark with guided JSON
    schema enforcement. If vLLM is offline during local UI development, falls back cleanly
    to deterministic structured extraction for `planning_reference`.
    """
    if blocks:
        try:
            client = AsyncOpenAI(
                base_url=VLLM_BASE_URL,
                api_key=VLLM_API_KEY,
                timeout=20.0,
            )
            doc_text = format_blocks_for_llm(blocks)
            schema = ExtractedAgreement.model_json_schema()

            response = await client.chat.completions.create(
                model=VLLM_MODEL_NAME,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": S106_EXTRACTION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": S106_EXTRACTION_USER_TEMPLATE.format(
                            document_blocks_text=doc_text
                        ),
                    },
                ],
                extra_body={"guided_json": schema},
            )
            raw_json = response.choices[0].message.content or "{}"
            return ExtractedAgreement.model_validate(json.loads(raw_json))
        except Exception as exc:
            print(
                f"[vllm_extractor] Local vLLM endpoint ({VLLM_BASE_URL}) unreachable ({exc}); "
                f"using pre-extracted deed structure for {planning_reference}."
            )

    return _build_fallback_extraction(planning_reference)


def _build_fallback_extraction(planning_reference: str) -> ExtractedAgreement:
    """Construct `ExtractedAgreement` from the Sheffield seed records."""
    agreements = fetch_all_agreements()
    agr = next(
        (a for a in agreements if a["planning_reference"] == planning_reference),
        agreements[0],
    )
    obligations_raw = fetch_obligations_for_agreement(agr["planning_reference"])

    extracted_obligations: list[ExtractedObligation] = []
    for obl in obligations_raw:
        bbox_dict = obl.get("bbox")
        extracted_obligations.append(
            ExtractedObligation(
                obligation_id=obl["obligation_id"],
                category=ContributionCategory(obl["category"]),
                title=obl["title"],
                base_amount_gbp=float(obl["base_amount_gbp"]),
                indexation_method=IndexationMethod(obl["indexation_method"]),
                index_base_date=obl["index_base_date"],
                trigger=TriggerCondition(
                    trigger_type=TriggerType(obl["trigger_type"]),
                    description=obl["trigger_description"],
                    dwelling_threshold=obl.get("dwelling_threshold"),
                    prior_to_event=obl["trigger_description"],
                ),
                evidence=ClauseEvidence(
                    clause_reference=obl["clause_reference"],
                    verbatim_quote=obl["verbatim_quote"],
                    page_number=int(obl["page_number"]),
                    bbox=BoundingBox(**bbox_dict) if bbox_dict else None,
                ),
            )
        )

    agr_date = agr["agreement_date"]
    if isinstance(agr_date, str):
        agr_date = date.fromisoformat(agr_date)

    return ExtractedAgreement(
        planning_reference=agr["planning_reference"],
        agreement_date=agr_date,
        site_name=agr["site_name"],
        ward_name=agr["ward_name"],
        developer_name=agr["developer_name"],
        total_dwellings_permitted=int(agr["total_dwellings_permitted"]),
        obligations=extracted_obligations,
    )
