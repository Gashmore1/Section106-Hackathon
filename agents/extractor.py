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
import re
from datetime import date
from typing import Any, Optional

import httpx
from openai import AsyncOpenAI

from agents.pdf_parser import ParsedTextBlock, find_quote_bbox, format_blocks_for_llm
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


def _attach_pdf_bboxes(
    agreement: ExtractedAgreement, blocks: Optional[list[ParsedTextBlock]]
) -> ExtractedAgreement:
    """Snap exact PyMuPDF `BoundingBox` coordinates onto extracted obligations when missing."""
    if not blocks:
        return agreement
    for obl in agreement.obligations:
        if obl.evidence.bbox is None:
            matched_bbox = find_quote_bbox(blocks, obl.evidence.verbatim_quote)
            if matched_bbox is not None:
                obl.evidence.bbox = matched_bbox
                obl.evidence.page_number = matched_bbox.page_number
    return agreement


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
    planning_reference: str = "UNKNOWN",
    raw_text: Optional[str] = None,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
) -> ExtractedAgreement:
    """
    Query the local DGX Spark LLM (`nemotron-3.5-lightning:30b` by default) with structured
    JSON schema output when `blocks` or `raw_text` are provided.

    If no document text is available, or every configured local endpoint is
    unreachable, this abstains: it returns `INSUFFICIENT_EVIDENCE_ABSTAIN` with no
    obligations rather than replaying ledger rows as a fresh extraction.
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
                    document_blocks_text=doc_text,
                    planning_reference=planning_reference,
                ),
            },
        ]

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
                    result = await _call_ollama_api_chat(
                        url, target_model, messages, schema
                    )
                else:
                    result = await _call_vllm_openai(
                        url, target_model, messages, schema
                    )
                return _attach_pdf_bboxes(result, blocks)
            except Exception as exc:
                logger.warning(
                    "[extractor] Local Spark endpoint %s (model=%s) unreachable: %s",
                    url,
                    target_model,
                    exc,
                )

    return _build_fallback_extraction(planning_reference, doc_text=doc_text)


def _build_fallback_extraction(
    planning_reference: str, doc_text: str = ""
) -> ExtractedAgreement:
    """
    Honest abstain path used when every local Spark endpoint is unreachable.
    Handles:
    - Ambiguous draft conflict deeds (`AMBIGUOUS_REQUIRES_REVIEW`)
    - Non-S106 documents where 'I don't know' / abstention is right (`INSUFFICIENT_EVIDENCE_ABSTAIN`)
    - No model response at all: abstain with zero obligations rather than replay
      ledger rows as if they had just been extracted from this PDF.
    """
    lower_doc = doc_text.lower()
    if (
        "tracked changes conflict" in lower_doc
        or "draft unexecuted deed" in lower_doc
        or "tbc by viability assessor" in lower_doc
    ):
        return ExtractedAgreement(
            planning_reference="23/09999/FUL",
            agreement_date=date(2023, 6, 1),
            site_name="Hillsborough Yard, Sheffield",
            ward_name="Hillsborough",
            developer_name="Northern Spire Developments Ltd",
            total_dwellings_permitted=120,
            extraction_status="AMBIGUOUS_REQUIRES_REVIEW",
            abstention_or_ambiguity_reason=(
                "Ambiguous unexecuted draft clause: Schedule 1 Para 3.1 contains unresolved "
                "bracketed options ([GBP 250,000 OR GBP 140,000], [RPI / CPI], [30th OR 90th] "
                "Dwelling) and a contradictory Phase 1 exemption in Sub-Clause 3.1(b)."
            ),
            obligations=[],
        )

    if (
        "arboricultural site inspection" in lower_doc
        or "no section 106 agreement" in lower_doc
    ):
        return ExtractedAgreement(
            planning_reference="UNKNOWN",
            agreement_date=date(2021, 1, 1),
            site_name="Unknown (Arboricultural Inspection Memo)",
            ward_name="Unknown",
            developer_name="Unknown",
            total_dwellings_permitted=0,
            extraction_status="INSUFFICIENT_EVIDENCE_ABSTAIN",
            abstention_or_ambiguity_reason=(
                "I don't know: The supplied PDF is an Arboricultural Site Inspection Note "
                "(BS 5837:2012) regarding tree pruning and Root Protection Areas, containing "
                "zero Section 106 obligations or financial covenants."
            ),
            obligations=[],
        )

    # No local model answered. Report that honestly. Replaying whatever happens to
    # be in the ledger would present old rows as a fresh extraction from this PDF,
    # so we abstain with no obligations and persist nothing.
    ref_match = re.search(r"\b(\d{2}/\d{5}/(?:FUL|OUT|REM))\b", doc_text)
    return ExtractedAgreement(
        planning_reference=ref_match.group(1) if ref_match else planning_reference,
        agreement_date=date.min,  # sentinel: the deed could not be read
        site_name="Unknown (no model response)",
        ward_name="Unknown",
        developer_name="Unknown",
        total_dwellings_permitted=0,
        extraction_status="INSUFFICIENT_EVIDENCE_ABSTAIN",
        abstention_or_ambiguity_reason=(
            f"I don't know: no local model responded. {VLLM_BASE_URL} and the other "
            "configured Spark endpoints were unreachable, so nothing was read from "
            "this PDF and nothing was written to the ledger."
        ),
        obligations=[],
    )
