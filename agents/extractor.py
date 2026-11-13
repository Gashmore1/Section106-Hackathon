"""
Track 2: Local LLM Structured Output Extractor for NVIDIA DGX Spark.
Supports both:
- Ollama OpenAI-compatible endpoint (`http://localhost:11434/v1`) using `response_format` / `format=schema`
- vLLM OpenAI-compatible endpoint (`http://localhost:8000/v1`) using `extra_body={"guided_json": schema}`

Preloaded DGX Spark models:
- `qwen3.6:35b-a3b` (default)
- `nemotron-3.5-lightning:30b`
- `nemotron-3-nano:30b`
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any, Optional

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

logger = logging.getLogger(__name__)

AVAILABLE_SPARK_MODELS: tuple[str, ...] = (
    "qwen3.6:35b-a3b",
    "nemotron-3.5-lightning:30b",
    "nemotron-3-nano:30b",
)

# Default to Ollama on the DGX Spark (:11434/v1), with automatic fallback to vLLM (:8000/v1)
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:11434/v1")
VLLM_API_KEY = os.getenv("VLLM_API_KEY", "ollama")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", AVAILABLE_SPARK_MODELS[0])


def _is_ollama_endpoint(base_url: str) -> bool:
    """Detect whether the target OpenAI-compatible server is Ollama vs vLLM."""
    backend_env = os.getenv("LLM_BACKEND", "").strip().lower()
    if backend_env in ("ollama", "vllm"):
        return backend_env == "ollama"
    return "11434" in base_url or "ollama" in base_url.lower()


def _normalize_openai_base_url(base_url: str) -> str:
    """Ensure base_url ends with `/v1` when using the OpenAI client."""
    cleaned = base_url.rstrip("/")
    if not cleaned.endswith("/v1"):
        cleaned = f"{cleaned}/v1"
    return cleaned


def _build_structured_output_kwargs(
    base_url: str, schema: dict[str, Any]
) -> dict[str, Any]:
    """
    Build the structured-output request kwargs depending on whether we are targeting
    Ollama (`:11434`) or vLLM (`:8000`).
    """
    if _is_ollama_endpoint(base_url):
        # Ollama's OpenAI compatibility layer supports `response_format` (json_schema)
        # and native `format: schema` via extra_body.
        return {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ExtractedAgreement",
                    "schema": schema,
                },
            },
            "extra_body": {"format": schema},
        }
    # vLLM structured outputs via Outlines / XGrammar guided_json
    return {
        "extra_body": {"guided_json": schema},
    }


async def extract_s106_obligations_vllm(
    blocks: Optional[list[ParsedTextBlock]] = None,
    planning_reference: str = "21/04512/FUL",
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
) -> ExtractedAgreement:
    """
    Query the local DGX Spark LLM server (Ollama or vLLM) with JSON schema enforcement.
    Tries the configured endpoint first; if unreachable during local UI development,
    falls back cleanly to deterministic structured extraction for `planning_reference`.
    """
    target_model = model_name or VLLM_MODEL_NAME
    primary_url = _normalize_openai_base_url(base_url or VLLM_BASE_URL)

    if blocks:
        # Try configured endpoint first, then alternate local Spark port (11434 <-> 8000)
        fallback_url = (
            "http://localhost:8000/v1"
            if "11434" in primary_url
            else "http://localhost:11434/v1"
        )
        candidate_urls = [primary_url]
        if fallback_url not in candidate_urls:
            candidate_urls.append(fallback_url)

        doc_text = format_blocks_for_llm(blocks)
        schema = ExtractedAgreement.model_json_schema()

        for url in candidate_urls:
            try:
                client = AsyncOpenAI(
                    base_url=url,
                    api_key=VLLM_API_KEY,
                    timeout=30.0,
                )
                structured_kwargs = _build_structured_output_kwargs(url, schema)

                response = await client.chat.completions.create(
                    model=target_model,
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
                    **structured_kwargs,
                )
                raw_json = response.choices[0].message.content or "{}"
                return ExtractedAgreement.model_validate(json.loads(raw_json))
            except Exception as exc:
                logger.warning(
                    "[extractor] Local LLM endpoint %s (model=%s) failed: %s",
                    url,
                    target_model,
                    exc,
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
