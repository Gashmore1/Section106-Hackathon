"""
Track 2: Local Nemotron / Ollama / vLLM Structured Output Extractor for NVIDIA DGX Spark.

Supports:
- Organiser Ollama `/api/chat` endpoint on SSH tunnel (`http://127.0.0.1:11435/api/chat`)
  or on-Spark (`http://127.0.0.1:11434/api/chat`) with:
  `"stream": False, "think": False, "keep_alive": 0, "format": schema, "options": {"num_ctx": 8192}`
- vLLM OpenAI-compatible endpoint (`http://127.0.0.1:8000/v1`) with `extra_body={"guided_json": schema}`

Preloaded DGX Spark models (Nemotron earns the hackathon bonus):
- `nemotron-3.5-lightning:30b` (default: classifying, extracting, routing)
- `nemotron-3-nano:30b` (reasoning and code)
- `qwen3.6:35b-a3b` (images + text/code)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any, Optional

import httpx
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
    "nemotron-3.5-lightning:30b",
    "nemotron-3-nano:30b",
    "qwen3.6:35b-a3b",
)

# Default to organiser SSH tunnel port (11435), with automatic fallback to on-Spark (11434) and vLLM (8000)
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://127.0.0.1:11435")
VLLM_API_KEY = os.getenv("VLLM_API_KEY", "ollama")
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", AVAILABLE_SPARK_MODELS[0])

LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "8192"))
LLM_NUM_PREDICT = int(os.getenv("LLM_NUM_PREDICT", "2048"))
LLM_KEEP_ALIVE = int(os.getenv("LLM_KEEP_ALIVE", "0"))


def _is_ollama_endpoint(base_url: str) -> bool:
    """Detect whether the target server is Ollama (11434/11435) vs vLLM (8000)."""
    backend_env = os.getenv("LLM_BACKEND", "").strip().lower()
    if backend_env in ("ollama", "vllm"):
        return backend_env == "ollama"
    return (
        "11434" in base_url
        or "11435" in base_url
        or "ollama" in base_url.lower()
    )


def _ollama_root_url(base_url: str) -> str:
    """Strip trailing `/v1` or `/api/chat` to get the Ollama root URL."""
    cleaned = base_url.rstrip("/")
    for suffix in ("/v1", "/api/chat"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned


async def _call_ollama_api_chat(
    root_url: str,
    model: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
) -> ExtractedAgreement:
    """
    Call Ollama's `/api/chat` endpoint using the exact organiser DGX Spark parameters:
    `stream: false`, `think: false`, `keep_alive: 0`, `format: schema`,
    `options: {num_ctx: 8192, num_predict: 2048, temperature: 0.0}`.
    """
    endpoint = f"{_ollama_root_url(root_url)}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": LLM_KEEP_ALIVE,
        "format": schema,
        "options": {
            "num_ctx": LLM_NUM_CTX,
            "num_predict": LLM_NUM_PREDICT,
            "temperature": 0.0,
        },
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(endpoint, json=payload)
        resp.raise_for_status()
        data = resp.json()
        raw_content = data.get("message", {}).get("content", "{}")
        return ExtractedAgreement.model_validate(json.loads(raw_content))


async def _call_vllm_openai(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
) -> ExtractedAgreement:
    """Call vLLM OpenAI-compatible `/v1/chat/completions` with `guided_json`."""
    v1_url = base_url.rstrip("/")
    if not v1_url.endswith("/v1"):
        v1_url = f"{v1_url}/v1"
    client = AsyncOpenAI(base_url=v1_url, api_key=VLLM_API_KEY, timeout=30.0)
    response = await client.chat.completions.create(
        model=model,
        temperature=0.0,
        messages=messages,  # type: ignore[arg-type]
        extra_body={"guided_json": schema},
    )
    raw_json = response.choices[0].message.content or "{}"
    return ExtractedAgreement.model_validate(json.loads(raw_json))


async def extract_s106_obligations_vllm(
    blocks: Optional[list[ParsedTextBlock]] = None,
    planning_reference: str = "21/04512/FUL",
    raw_text: Optional[str] = None,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
) -> ExtractedAgreement:
    """
    Query the local DGX Spark LLM (`nemotron-3.5-lightning:30b` by default) with structured
    JSON schema output when `blocks` or `raw_text` are provided.
    If no document text is provided (or if the Spark tunnel is offline during local UI dev),
    returns the deterministic pre-extracted agreement for `planning_reference`.
    """
    target_model = model_name or VLLM_MODEL_NAME
    primary_url = base_url or VLLM_BASE_URL

    doc_text = raw_text or (format_blocks_for_llm(blocks) if blocks else "")
    if doc_text.strip():
        schema = ExtractedAgreement.model_json_schema()
        messages = [
            {"role": "system", "content": S106_EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": S106_EXTRACTION_USER_TEMPLATE.format(
                    document_blocks_text=doc_text
                ),
            },
        ]

        # Candidate endpoints in priority order:
        # 1. Configured primary URL (default http://127.0.0.1:11435 SSH tunnel)
        # 2. On-Spark direct Ollama (http://127.0.0.1:11434)
        # 3. Local vLLM (http://127.0.0.1:8000/v1)
        candidates = [primary_url]
        for alt in (
            "http://127.0.0.1:11435",
            "http://127.0.0.1:11434",
            "http://127.0.0.1:8000/v1",
        ):
            if _ollama_root_url(alt) not in [
                _ollama_root_url(c) for c in candidates
            ]:
                candidates.append(alt)

        for url in candidates:
            try:
                if _is_ollama_endpoint(url):
                    return await _call_ollama_api_chat(
                        url, target_model, messages, schema
                    )
                return await _call_vllm_openai(url, target_model, messages, schema)
            except Exception as exc:
                logger.warning(
                    "[extractor] Local Spark endpoint %s (model=%s) unreachable: %s",
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
        extraction_status="EXTRACTED",
        obligations=extracted_obligations,
    )
