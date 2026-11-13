"""
Ingest the real Section 106 deeds in `data/fixtures/pdfs/real/` into the ledger.

`data.seed_db` creates the schema, the 28 real ward boundaries and the ONS/BCIS
index series, and deliberately leaves the agreement ledger empty. This script is
the only supported way to fill it: each deed is parsed, passed through the local
Nemotron extractor, and the result is upserted into PostgreSQL.

Nothing is invented:
  * a deed the model cannot read persists nothing (see `_build_fallback_extraction`);
  * occupation telemetry defaults to 0 dwellings, so obligations are reported as
    "not yet triggered" instead of a fabricated breach;
  * the ward is resolved by point-in-polygon against the real ONS boundary layer
    from the coordinates recorded in `provenance.json`, and cross-checked against
    the ward name recorded there.

Usage (on the DGX Spark, after `python -m data.seed_db`):
    .venv/bin/python -m scripts.ingest_real_pdfs
    .venv/bin/python -m scripts.ingest_real_pdfs --all-authorities
    .venv/bin/python -m scripts.ingest_real_pdfs --only 17/04673 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any, Optional

import httpx

from agents.extractor import (
    LLM_KEEP_ALIVE,
    LLM_NUM_CTX,
    LLM_NUM_PREDICT,
    VLLM_BASE_URL,
    VLLM_MODEL_NAME,
    _is_ollama_endpoint,
    _ollama_root_url,
)
from agents.pdf_parser import format_blocks_for_llm, parse_s106_pdf
from agents.prompts import S106_EXTRACTION_SYSTEM_PROMPT, S106_EXTRACTION_USER_TEMPLATE
from data.geo import point_in_ring, resolve_ward
from data.sql_tools import compute_indexed_obligation, persist_extracted_agreement
from shared.schemas import (
    ExtractedAgreement,
    IndexationMethod,
    TriggerType,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIRS = {
    "real": REPO_ROOT / "data" / "fixtures" / "pdfs" / "real",
    "sheffield": REPO_ROOT / "data" / "fixtures" / "pdfs" / "sheffield",
}
REAL_PDF_DIR = CORPUS_DIRS["real"]
PROVENANCE_FILE = REAL_PDF_DIR / "provenance.json"


def use_corpus(name: str) -> None:
    """
    Point the ingest at one of the deed corpora before its manifest is read. Both
    corpora use the same provenance schema: `real` is the hand-curated set,
    `sheffield` is the register harvest enriched by
    scripts/enrich_sheffield_manifest.py.
    """
    global REAL_PDF_DIR, PROVENANCE_FILE
    if name not in CORPUS_DIRS:
        raise SystemExit(f"unknown corpus {name!r}; choose from {sorted(CORPUS_DIRS)}")
    REAL_PDF_DIR = CORPUS_DIRS[name]
    PROVENANCE_FILE = REAL_PDF_DIR / "provenance.json"
    if not PROVENANCE_FILE.exists():
        raise SystemExit(
            f"{PROVENANCE_FILE} does not exist. For the register corpus, run "
            f"`python -m scripts.enrich_sheffield_manifest` first."
        )
WARDS_BOUNDARY_FILE = REPO_ROOT / "data" / "fixtures" / "spatial" / "sheffield_28_wards_ons_boundaries.geojson"

DEFAULT_AUTHORITY = "Sheffield City Council"

# Blocks worth sending for a 30-60 page deed: the definitions that name a sum, the
# covenants that impose payment, the indexation clause, and the deed header. The
# generic relevance filter keeps too much narrative for a 30B model to read at
# temperature 0, and it abstains.
FINANCIAL_BLOCK_RE = re.compile(
    r"(means the sum of|means the following sum|To pay\b|INDEXATION|Index Linked|"
    r"Monitoring Fee|prior to the Occupation|prior to the Commencement|"
    r"THIS DEED is made|relating to land at|Dwellings.+means|Application.+means)",
    re.IGNORECASE,
)
MAX_PAYLOAD_CHARS = 9000

# Deterministic category resolution from the defined contribution name. The model
# routinely mislabels these on real deeds (it called a bus-stop sum EDUCATION).
CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("MONITORING_FEE", ("monitoring",)),
    ("AFFORDABLE_HOUSING", ("affordable housing", "first home", "intermediate rent")),
    ("EDUCATION", ("education", "school", "early years", "pediatric")),
    ("HEALTHCARE", ("health", "gp surgery", "medical", "nhs")),
    (
        "HIGHWAYS_AND_TRANSPORT",
        ("bus stop", "mova", "traffic", "tro", "highway", "transport", "tram", "parking", "junction", "car club"),
    ),
    (
        "OPEN_SPACE_AND_BIODIVERSITY",
        ("open space", "play", "park", "biodiversity", "ecology", "drainage", "suds", "green infrastructure", "grassland"),
    ),
    ("COMMUNITY_INFRASTRUCTURE", ("community", "centre", "library", "hub", "employment", "skills")),
]


def categorise(contribution_name: str) -> str:
    lowered = contribution_name.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return category
    return "COMMUNITY_INFRASTRUCTURE"


def load_provenance() -> list[dict[str, Any]]:
    if not PROVENANCE_FILE.exists():
        raise FileNotFoundError(
            f"Missing {PROVENANCE_FILE}. Every deed needs a recorded source before it "
            "can enter the ledger."
        )
    return json.loads(PROVENANCE_FILE.read_text())["deeds"]


def _point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    """Kept as an alias: ward geometry lives in `data.geo` so provenance tooling can
    resolve a ward without importing this pipeline."""
    return point_in_ring(point, ring)


DEFINED_SUM_RE = re.compile(
    r'["\u201c]([A-Z][^"\u201d]{3,80}?)["\u201d]\s*(?:\n\s*)?means (?:if applicable )?the sum of\s*£?\s*([\d,]+(?:\.\d{1,2})?)',
)
# Some deeds define a sum with prose in between, e.g.
#   "On-Site ... Maintenance Sum" means if the Council are to adopt the ... a sum of £6,000.00
DEFINED_SUM_PROSE_RE = re.compile(
    r'["\u201c]([A-Z][^"\u201d]{3,80}?)["\u201d]\s*(?:\n\s*)?means\b[^";]{0,160}?\ba sum of\s*£?\s*([\d,]+(?:\.\d{1,2})?)',
)


def parse_defined_sums(deed_text: str) -> dict[str, float]:
    """
    Parse `"X Contribution" means the sum of £27,000.00` definitions straight out of
    the deed. These are authoritative: the model reads the same page but routinely
    pairs a name with a neighbouring sum, so the deterministic parse wins.
    """
    sums: dict[str, float] = {}
    for pattern in (DEFINED_SUM_RE, DEFINED_SUM_PROSE_RE):
        for name, amount in pattern.findall(deed_text):
            clean = " ".join(name.split()).strip(" .")
            if not clean or clean.lower().startswith(("this", "the deed")):
                continue
            try:
                sums.setdefault(clean, float(amount.replace(",", "")))
            except ValueError:
                continue
    return sums


GENERIC_TOKENS = {
    "contribution", "contributions", "sum", "sums", "fee", "fees", "the", "of",
    "and", "to", "for", "payment", "council", "sums", "sum", "costs", "amount",
}


def _distinctive_tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]{4,}", name.lower()) if t not in GENERIC_TOKENS}


def match_defined_sum(title: str, defined: dict[str, float]) -> Optional[tuple[str, float]]:
    """
    Match an extracted obligation back to a defined contribution name. Matching is
    strict on purpose: an earlier, looser version paired 'Bus Stop Upgrades
    Contribution' with 'TRO Contribution' because both end in 'Contribution'.
    """
    lowered = " ".join(title.lower().split())
    best: Optional[tuple[float, str, float]] = None
    for name, amount in defined.items():
        key = " ".join(name.lower().split())
        score = 0.0
        if key == lowered:
            score = 1000.0
        elif key in lowered or lowered in key:
            score = 500.0 + min(len(key), len(lowered))
        else:
            tokens = _distinctive_tokens(name)
            if tokens and all(t in lowered for t in tokens):
                score = 100.0 + len(tokens)
        if score and (best is None or score > best[0]):
            best = (score, name, amount)
    return (best[1], best[2]) if best else None


def pdf_page_text(pdf_path: Path) -> str:
    """
    Whole-document page text. The block view splits quoted defined terms away from
    the words that define them, so definition and indexation parsing must run on the
    continuous page text instead. Scanned pages are OCR'd locally; a document with
    no text layer and no OCR engine available yields nothing, and the deed is
    reported as unreadable rather than silently audited as empty.
    """
    from agents.ocr import ocr_available

    if ocr_available():
        from agents.ocr import ocr_document_text

        return ocr_document_text(pdf_path)
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        return "\n".join(doc[i].get_text() for i in range(doc.page_count))


def detect_deed_indexation(full_text: str) -> str:
    """
    Resolve the deed's indexation from the whole document, not the trimmed payload:
    the clause that imposes indexation and the clause that defines the Index are
    usually pages apart.
    """
    if not re.search(r"INDEXATION OF CONTRIBUTIONS", full_text, re.IGNORECASE):
        return "NONE"
    if re.search(r"Retail Prices Index|RPI|item reference CHAW", full_text, re.IGNORECASE):
        return "RPI"
    if re.search(r"Consumer Prices Index|\bCPI\b", full_text, re.IGNORECASE):
        return "CPI"
    if re.search(r"BCIS|Tender Price", full_text, re.IGNORECASE):
        return "BCIS"
    return "NONE"


def find_covenant(blocks: list, name: str) -> Optional[dict[str, Any]]:
    """
    Find the covenant in the Landowners' obligations schedule that imposes payment
    of a defined contribution, and read its trigger from the covenant's own wording.
    """
    pattern = re.compile(
        r"To pay the\s+" + r"\s+".join(re.escape(word) for word in name.split()),
        re.IGNORECASE,
    )
    for block in blocks:
        flat = " ".join(block.text.split())
        match = pattern.search(flat) or pattern.search(block.text)
        if not match:
            continue
        source = flat if pattern.search(flat) else block.text
        quote = " ".join(source[max(0, match.start() - 8): match.start() + 480].split())
        lowered = source.lower()
        threshold: Optional[int] = 1
        if "prior to the commencement" in lowered:
            trigger, threshold = "PRIOR_TO_COMMENCEMENT", None
        elif "prior to the occupation of any" in lowered:
            trigger = "PRIOR_TO_OCCUPATION"
        else:
            nth = re.search(r"occupation of the (\d+)(?:st|nd|rd|th)", lowered)
            if nth:
                trigger, threshold = "DWELLING_OCCUPATION_THRESHOLD", int(nth.group(1))
            else:
                trigger = "PRIOR_TO_OCCUPATION"
        clause = re.search(r"\b(\d{1,2}\.\d{1,2})\b", source)
        return {
            "clause": f"Schedule 2, paragraph {clause.group(1)}" if clause else "Landowners' obligations schedule",
            "trigger_type": trigger,
            "dwelling_threshold": threshold,
            "quote": quote[:900],
            "page_number": block.page_number,
        }
    return None


def select_financial_blocks(blocks: list) -> list:
    """Keep only blocks that carry money, triggers, indexation or the deed header."""
    scored = [(len(FINANCIAL_BLOCK_RE.findall(b.text)), b) for b in blocks]
    picked: list = []
    total = 0
    for score, block in sorted(
        (s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1].page_number)
    ):
        cost = len(block.text) + 48
        if total + cost > MAX_PAYLOAD_CHARS:
            continue
        picked.append(block)
        total += cost
    picked.sort(key=lambda b: (b.page_number, getattr(b, "block_index", 0)))
    return picked


async def call_local_llm_raw(
    text: str, planning_reference: str
) -> tuple[Optional[dict[str, Any]], str]:
    """
    Ask the local model for the extraction and return the raw JSON object, without
    validating it. Validation happens after the deterministic repair pass, because
    the model frequently omits enum fields on long real deeds and strict validation
    would throw the whole (partly good) answer away.
    """
    from shared.schemas import ExtractedAgreement as _EA

    messages = [
        {"role": "system", "content": S106_EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": S106_EXTRACTION_USER_TEMPLATE.format(
                document_blocks_text=text, planning_reference=planning_reference
            ),
        },
    ]
    schema = _EA.model_json_schema()
    payload = {
        "model": VLLM_MODEL_NAME,
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
    endpoint = (
        f"{_ollama_root_url(VLLM_BASE_URL)}/api/chat"
        if _is_ollama_endpoint(VLLM_BASE_URL)
        else f"{VLLM_BASE_URL.rstrip('/')}/chat/completions"
    )
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(endpoint, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if _is_ollama_endpoint(VLLM_BASE_URL):
            content = data.get("message", {}).get("content", "{}")
        else:
            content = data["choices"][0]["message"]["content"]
        return json.loads(content), ""
    except Exception as exc:  # unreachable server, bad JSON, etc.
        return None, f"{type(exc).__name__}: {exc}"


def repair_payload(
    raw: dict[str, Any],
    deed_text: str,
    planning_reference: str,
    full_text: str,
    blocks: Optional[list] = None,
) -> tuple[Optional[dict[str, Any]], list[str]]:
    """
    Fill the fields the model leaves out using the deed text itself, then drop
    anything incomplete. Deterministic code decides; the model only proposes.

    Returns the repaired payload (or None if nothing usable survived) and a list of
    human-readable notes about what was repaired or discarded.
    """
    notes: list[str] = []
    if not isinstance(raw, dict):
        return None, ["model returned a non-object answer"]

    status = raw.get("extraction_status")
    if status not in ("EXTRACTED", "AMBIGUOUS_REQUIRES_REVIEW", "INSUFFICIENT_EVIDENCE_ABSTAIN"):
        status = "EXTRACTED" if raw.get("obligations") else "INSUFFICIENT_EVIDENCE_ABSTAIN"
        notes.append(f"extraction_status was '{raw.get('extraction_status')}'; read as {status}")
    if status != "EXTRACTED":
        return {**raw, "extraction_status": status}, notes

    # The deed's own indexation clause governs every contribution in it, and the
    # interpretations clause is the authority on what each named sum actually is.
    indexation = detect_deed_indexation(full_text)
    if indexation != "NONE":
        notes.append(
            f"deed contains a general indexation clause -> applied {indexation} to every contribution"
        )
    defined_sums = parse_defined_sums(full_text)
    if defined_sums:
        preview = ", ".join(f"{n} £{a:,.0f}" for n, a in sorted(defined_sums.items())[:5])
        notes.append(f"parsed {len(defined_sums)} defined sums from the deed: {preview}")

    deed_date_raw = raw.get("agreement_date")
    deed_date = (
        deed_date_raw
        if isinstance(deed_date_raw, str) and re.match(r"\d{4}-\d{2}-\d{2}", deed_date_raw)
        else None
    )
    if not deed_date:
        # "THIS DEED is made on 2021" / "DATED 2019" - a year on its own is enough
        # to anchor indexation, and indexation from the wrong date is the failure
        # that actually costs the Council money.
        year = re.search(r"(?:made on|DATED)\s+(\d{4})", deed_text)
        if year:
            deed_date = f"{year.group(1)}-01-01"
            notes.append(f"deed date read from the deed text as {deed_date}")

    repaired: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    claimed_definitions: set[str] = set()
    for index, obl in enumerate(raw.get("obligations") or [], start=1):
        if not isinstance(obl, dict):
            continue
        title = str(obl.get("title") or f"Obligation {index}").strip()
        try:
            amount = float(obl.get("base_amount_gbp") or 0)
        except (TypeError, ValueError):
            amount = 0.0

        matched = match_defined_sum(title, defined_sums)
        if matched and matched[0] not in claimed_definitions:
            defined_name, defined_amount = matched
            if abs(defined_amount - amount) > 0.005:
                notes.append(
                    f"'{title}': sum £{amount:,.2f} -> £{defined_amount:,.2f}, the deed's own "
                    f"definition of '{defined_name}'"
                )
            amount = defined_amount
            claimed_definitions.add(defined_name)

        if amount <= 0:
            notes.append(
                f"dropped '{title}': no sum in the deed attaches to it (model returned {amount})"
            )
            continue

        name_for_category = f"{title} {obl.get('clause_reference') or ''}"
        category = categorise(name_for_category)
        if obl.get("category") != category:
            notes.append(f"'{title}': category {obl.get('category')} -> {category} from the clause wording")

        # The covenant text is the authority on the trigger and the evidence, not
        # the model: it is a literal sentence in the deed we can locate exactly.
        covenant = find_covenant(blocks or [], matched[0] if matched else title)
        if covenant:
            trigger = covenant["trigger_type"]
            threshold = covenant["dwelling_threshold"]
            if trigger != str(obl.get("trigger_type") or ""):
                notes.append(f"'{title}': trigger read from the covenant as {trigger}")
        else:
            trigger = str(obl.get("trigger_type") or "")
            threshold = obl.get("dwelling_threshold")
            if trigger not in [t.value for t in TriggerType]:
                trigger, threshold = "PRIOR_TO_OCCUPATION", 1
                notes.append(f"'{title}': trigger defaulted to PRIOR_TO_OCCUPATION at the first dwelling")
            if trigger == "PRIOR_TO_OCCUPATION" and not threshold:
                threshold = 1

        obligation_id = str(obl.get("obligation_id") or "").strip()
        if not obligation_id or obligation_id in seen_ids:
            obligation_id = f"OBL-{re.sub(r'[^0-9A-Za-z]+', '-', planning_reference)}-{index:02d}"
        seen_ids.add(obligation_id)

        repaired.append(
            {
                "obligation_id": obligation_id,
                "category": category,
                "title": title[:255],
                "base_amount_gbp": round(amount, 2),
                "indexation_method": obl.get("indexation_method") or indexation,
                "index_base_date": obl.get("index_base_date") or deed_date,
                "trigger": {
                    "trigger_type": trigger,
                    "description": (covenant or {}).get("quote")
                    or str(obl.get("trigger_description") or "")[:500]
                    or "Payment due under the Landowners' obligations schedule",
                    "dwelling_threshold": threshold,
                    "prior_to_event": str(obl.get("prior_to_event") or title)[:255],
                },
                "evidence": {
                    "clause_reference": (covenant or {}).get("clause")
                    or str(obl.get("clause_reference") or "Landowners' obligations schedule")[:127],
                    "verbatim_quote": (covenant or {}).get("quote")
                    or str(obl.get("verbatim_quote") or title)[:4000],
                    "page_number": int((covenant or {}).get("page_number") or obl.get("page_number") or 1),
                    "bbox": obl.get("bbox") or None,
                },
            }
        )

    # Completeness: a defined sum the model never mentioned is still a real
    # obligation if the deed imposes it. Find the covenant and record it.
    if blocks:
        for name, amount in sorted(defined_sums.items()):
            if name in claimed_definitions:
                continue
            covenant = find_covenant(blocks, name)
            if not covenant:
                notes.append(
                    f"'{name}' is defined at £{amount:,.2f} but no payment covenant was found; not recorded"
                )
                continue
            obligation_id = f"OBL-{re.sub(r'[^0-9A-Za-z]+', '-', planning_reference)}-{len(repaired) + 1:02d}"
            if obligation_id in seen_ids:
                continue
            seen_ids.add(obligation_id)
            repaired.append(
                {
                    "obligation_id": obligation_id,
                    "category": categorise(name),
                    "title": name[:255],
                    "base_amount_gbp": round(amount, 2),
                    "indexation_method": indexation,
                    "index_base_date": deed_date,
                    "trigger": {
                        "trigger_type": covenant["trigger_type"],
                        "description": covenant["quote"][:500],
                        "dwelling_threshold": covenant["dwelling_threshold"],
                        "prior_to_event": name[:255],
                    },
                    "evidence": {
                        "clause_reference": covenant["clause"][:127],
                        "verbatim_quote": covenant["quote"][:4000],
                        "page_number": covenant["page_number"],
                        "bbox": None,
                    },
                }
            )
            notes.append(
                f"added '{name}' £{amount:,.2f} from {covenant['clause']} (page {covenant['page_number']})"
                " - defined in the deed and imposed by a covenant, but the model missed it"
            )

    if not repaired:
        return None, notes + ["no obligation survived validation"]

    payload = {
        "planning_reference": str(raw.get("planning_reference") or planning_reference),
        "agreement_date": deed_date,
        "site_name": str(raw.get("site_name") or "UNKNOWN")[:255],
        "ward_name": str(raw.get("ward_name") or "UNKNOWN")[:127],
        "developer_name": str(raw.get("developer_name") or "UNKNOWN")[:255],
        "total_dwellings_permitted": int(raw.get("total_dwellings_permitted") or 0),
        "extraction_status": "EXTRACTED",
        "abstention_or_ambiguity_reason": None,
        "obligations": repaired,
    }
    return payload, notes


async def ingest_deed(entry: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    pdf_path = REAL_PDF_DIR / entry["file"]
    reference = entry["planning_reference"]
    result: dict[str, Any] = {
        "planning_reference": reference,
        "authority": entry.get("authority", "unknown"),
        "file": entry["file"],
        "status": "NOT_FOUND",
        "obligations": 0,
        "base_total_gbp": 0.0,
        "indexed_total_gbp": 0.0,
        "ward": None,
        "ward_note": "",
        "notes": [],
    }
    if not pdf_path.exists():
        result["status"] = "MISSING_FILE"
        return result

    blocks = parse_s106_pdf(pdf_path)
    focused = select_financial_blocks(blocks)
    deed_text = format_blocks_for_llm(focused)
    full_text = pdf_page_text(pdf_path)
    raw, error = await call_local_llm_raw(deed_text, reference)
    if raw is None:
        result["status"] = "LLM_UNREACHABLE"
        result["reason"] = error
        return result

    payload, notes = repair_payload(raw, deed_text, reference, full_text, blocks)
    result["notes"] = notes
    if payload is None:
        result["status"] = str(raw.get("extraction_status") or "NO_OBLIGATIONS")
        if raw.get("abstention_or_ambiguity_reason"):
            result["reason"] = str(raw["abstention_or_ambiguity_reason"])
        return result

    extracted = ExtractedAgreement.model_validate(payload)

    # Header facts come from the provenance manifest when the model left them blank
    # or echoed a fragment of the title page. The manifest records what the deed says.
    def _usable(value: str) -> bool:
        lowered = (value or "").strip().lower()
        return len(lowered) > 4 and lowered not in {
            "unknown", "land at", "site", "ward", "landowners", "the developer",
        }

    if not _usable(extracted.site_name) and entry.get("site_name"):
        extracted.site_name = entry["site_name"]
    if not _usable(extracted.developer_name) and entry.get("developer_name"):
        extracted.developer_name = entry["developer_name"]
    if not extracted.total_dwellings_permitted and entry.get("total_dwellings_permitted"):
        extracted.total_dwellings_permitted = int(entry["total_dwellings_permitted"])

    # Ward: trust the geometry, not the model. The deed rarely names an electoral ward.
    resolved = resolve_ward(entry.get("site_lat"), entry.get("site_lon"))
    expected = entry.get("ward_name")
    if resolved:
        result["ward"] = resolved
        result["ward_note"] = (
            "resolved from site coordinates"
            if expected in (None, resolved)
            else f"resolved from coordinates; manifest said '{expected}'"
        )
        extracted.ward_name = resolved
    else:
        result["ward"] = expected or "Unassigned"
        result["ward_note"] = entry.get("ward_source", "no coordinates recorded")

    base_total = sum(o.base_amount_gbp for o in extracted.obligations)
    indexed_total = 0.0
    for obl in extracted.obligations:
        calc = compute_indexed_obligation(
            obligation_id=obl.obligation_id,
            base_amount_gbp=obl.base_amount_gbp,
            indexation_method=IndexationMethod(obl.indexation_method.value),
            base_date=obl.index_base_date or extracted.agreement_date,
        )
        indexed_total += calc.indexed_amount_gbp
    result.update(
        status="EXTRACTED",
        obligations=len(extracted.obligations),
        base_total_gbp=round(base_total, 2),
        indexed_total_gbp=round(indexed_total, 2),
    )

    if not dry_run:
        persist_extracted_agreement(
            extracted,
            dwellings_occupied_current=0,
            document_url=entry.get("document_url", ""),
        )
    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authority",
        default=DEFAULT_AUTHORITY,
        help=f"only ingest deeds published by this authority (default {DEFAULT_AUTHORITY})",
    )
    parser.add_argument(
        "--all-authorities",
        action="store_true",
        help="ingest every deed in the manifest, including other councils' deeds",
    )
    parser.add_argument("--only", default=None, help="substring filter on planning reference")
    parser.add_argument("--corpus", default="real", choices=sorted(CORPUS_DIRS),
                        help="which deed corpus to ingest (default: real)")
    parser.add_argument("--limit", type=int, default=None, help="stop after N deeds")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="extract and report, but write nothing to PostgreSQL",
    )
    args = parser.parse_args()
    use_corpus(args.corpus)

    entries = load_provenance()
    if not args.all_authorities:
        entries = [e for e in entries if e.get("authority") == args.authority]
    if args.only:
        entries = [e for e in entries if args.only in e["planning_reference"]]
    if args.limit:
        entries = entries[: args.limit]

    if not entries:
        print("[ingest] No deeds matched. Nothing to do.")
        return

    print(
        f"[ingest] {len(entries)} deed(s) via {VLLM_MODEL_NAME}; "
        f"{'dry run, nothing will be written' if args.dry_run else 'writing to PostgreSQL'}"
    )
    rows = []
    for entry in entries:
        row = await ingest_deed(entry, dry_run=args.dry_run)
        rows.append(row)
        print(
            f"  {row['planning_reference']:14s} {row['status']:30s} "
            f"{row['obligations']:2d} obligations  "
            f"base £{row['base_total_gbp']:>12,.2f}  indexed £{row['indexed_total_gbp']:>12,.2f}  "
            f"ward={row['ward']}"
        )
        if row.get("reason"):
            print(f"    note: {row['reason'][:200]}")
        for note in row.get("notes", []):
            print(f"    repair: {note[:160]}")

    total_base = sum(r["base_total_gbp"] for r in rows)
    total_indexed = sum(r["indexed_total_gbp"] for r in rows)
    extracted = [r for r in rows if r["status"] == "EXTRACTED"]
    print(
        f"\n[ingest] {len(extracted)}/{len(rows)} deeds extracted; "
        f"£{total_base:,.2f} agreed -> £{total_indexed:,.2f} indexed today "
        f"(£{total_indexed - total_base:,.2f} of indexation exposure)."
    )
    print(
        "[ingest] Occupation telemetry was recorded as 0 dwellings for every deed: "
        "obligations therefore show as not yet triggered rather than as assumed breaches."
    )


if __name__ == "__main__":
    asyncio.run(main())
