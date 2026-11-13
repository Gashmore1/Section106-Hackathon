"""
Track 2: PyMuPDF (`fitz`) S106 Deed Parser, Smart Schedule/Covenant Block Filter,
and Bounding Box Evidence Extractor.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.schemas import BoundingBox

# High-signal keywords for prioritizing Schedule & Covenant blocks within the 8,192-token window
_HIGH_SIGNAL_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"schedule\s+\d+|schedule\s+of\s+developer\s+covenants", re.I), 12),
    (re.compile(r"(?:£|gbp)\s*\d[\d,]*(?:\.\d{2})?", re.I), 15),
    (re.compile(r"\b(?:bcis|rpi|cpi|index\s+linked|tender\s+price\s+index|retail\s+prices)\b", re.I), 10),
    (re.compile(r"\b(?:prior\s+to\s+(?:the\s+)?occupation|commencement\s+of\s+development)\b", re.I), 10),
    (re.compile(r"\b\d+(?:st|nd|rd|th)\s+(?:market\s+)?(?:dwelling|residential\s+unit|unit)\b", re.I), 12),
    (re.compile(r"\b(?:commuted\s+sum|affordable\s+housing|education\s+contribution|highways|healthcare|open\s+space)\b", re.I), 8),
    (re.compile(r"\b(?:tracked\s+changes\s+conflict|draft\s+unexecuted|tbc\s+by\s+viability)\b", re.I), 18),
    (re.compile(r"\b(?:arboricultural|no\s+section\s+106\s+agreement)\b", re.I), 18),
)

_BOILERPLATE_PENALTY_RE = re.compile(
    r"standard\s+sheffield\s+s106\s+model\s+provisions|late\s+payment\s+interest\s+accrues",
    re.I,
)


@dataclass
class ParsedTextBlock:
    """A single text block extracted from a PDF page with coordinates."""

    page_number: int
    block_index: int
    text: str
    bbox: BoundingBox


def _ocr_blocks_for_page(page) -> list[ParsedTextBlock]:
    """
    OCR one image-only page into text blocks with bounding boxes. Returns nothing
    when no local OCR engine is installed, so a machine without RapidOCR behaves
    exactly as it did before rather than failing.
    """
    try:
        from agents.ocr import needs_ocr, ocr_page
    except ImportError:
        return []
    if not needs_ocr(page):
        return []
    try:
        lines = ocr_page(page)
    except Exception:  # an OCR engine that cannot run must not break the parse
        return []
    return [
        ParsedTextBlock(
            page_number=page.number + 1,
            block_index=index,
            text=line.text,
            bbox=line.bbox,
        )
        for index, line in enumerate(lines)
    ]


def parse_s106_pdf(pdf_path: str | Path) -> list[ParsedTextBlock]:
    """
    Parse a Section 106 PDF deed using PyMuPDF (`fitz`) and extract all text blocks
    along with their 1-indexed page numbers and bounding boxes (`[x0, y0, x1, y1]`).
    Includes a lightweight pure-Python PDF stream fallback if `fitz` is not installed.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"S106 PDF not found at {path}")

    try:
        try:
            import pymupdf as fitz  # PyMuPDF >= 1.24
        except ImportError:
            import fitz  # Legacy PyMuPDF

        blocks: list[ParsedTextBlock] = []
        doc = fitz.open(str(path))
        try:
            for page_idx, page in enumerate(doc):
                page_num = page_idx + 1
                raw_blocks = page.get_text("blocks")
                blocks_before = len(blocks)
                for b_idx, b in enumerate(raw_blocks):
                    x0, y0, x1, y1, text_content, *_ = b
                    cleaned = (text_content or "").strip()
                    if not cleaned:
                        continue
                    blocks.append(
                        ParsedTextBlock(
                            page_number=page_num,
                            block_index=b_idx,
                            text=cleaned,
                            bbox=BoundingBox(
                                page_number=page_num,
                                x0=round(float(x0), 2),
                                y0=round(float(y0), 2),
                                x1=round(float(x1), 2),
                                y1=round(float(y1), 2),
                            ),
                        )
                    )
                # Scanned deeds (most of Sheffield's register publications) have no
                # text layer at all. OCR them locally rather than parsing an empty page.
                if len(blocks) == blocks_before and page.get_images(full=True):
                    blocks.extend(_ocr_blocks_for_page(page))
        finally:
            doc.close()
        return blocks
    except ImportError:
        return _parse_pdf_stream_fallback(path)


def _parse_pdf_stream_fallback(path: Path) -> list[ParsedTextBlock]:
    """
    Pure-Python fallback parser for PDF 1.4 content streams (supports uncompressed
    and FlateDecode streams) when PyMuPDF (`fitz`) is not installed in the active venv.
    """
    raw_bytes = path.read_bytes()
    stream_re = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream", re.S)
    tj_line_re = re.compile(
        r"1\s+0\s+0\s+1\s+([\d.]+)\s+([\d.]+)\s+Tm\s*\((.*?)\)\s*Tj"
    )
    generic_tj_re = re.compile(r"\(([^()]*)\)\s*Tj")

    blocks: list[ParsedTextBlock] = []
    page_num = 0

    for match in stream_re.finditer(raw_bytes):
        header, body = match.group(1), match.group(2)
        if b"/FlateDecode" in header:
            try:
                body = zlib.decompress(body)
            except Exception:
                continue
        text_stream = body.decode("latin-1", errors="ignore")
        if "BT" not in text_stream:
            continue

        page_num += 1
        page_lines: list[tuple[float, float, str]] = []
        for m in tj_line_re.finditer(text_stream):
            x = float(m.group(1))
            y_pdf = float(m.group(2))
            y_top = round(842.0 - y_pdf, 2)
            txt = (
                m.group(3)
                .replace("\\(", "(")
                .replace("\\)", ")")
                .replace("\\\\", "\\")
                .strip()
            )
            if txt:
                page_lines.append((x, y_top, txt))

        if not page_lines:
            fragments = [
                m.group(1).strip()
                for m in generic_tj_re.finditer(text_stream)
                if m.group(1).strip()
            ]
            if fragments:
                joined = " ".join(fragments)
                blocks.append(
                    ParsedTextBlock(
                        page_number=page_num,
                        block_index=0,
                        text=joined,
                        bbox=BoundingBox(
                            page_number=page_num,
                            x0=72.0,
                            y0=72.0,
                            x1=523.0,
                            y1=770.0,
                        ),
                    )
                )
            continue

        # Group contiguous lines on the page into logical blocks
        current_lines: list[str] = []
        curr_x0 = 72.0
        curr_y0 = page_lines[0][1]
        last_y = curr_y0
        b_idx = 0

        for x, y_top, txt in page_lines:
            if current_lines and abs(y_top - last_y) > 26.0:
                blocks.append(
                    ParsedTextBlock(
                        page_number=page_num,
                        block_index=b_idx,
                        text=" ".join(current_lines),
                        bbox=BoundingBox(
                            page_number=page_num,
                            x0=round(curr_x0, 2),
                            y0=round(curr_y0, 2),
                            x1=523.0,
                            y1=round(last_y + 14.0, 2),
                        ),
                    )
                )
                b_idx += 1
                current_lines = [txt]
                curr_x0 = x
                curr_y0 = y_top
            else:
                current_lines.append(txt)
            last_y = y_top

        if current_lines:
            blocks.append(
                ParsedTextBlock(
                    page_number=page_num,
                    block_index=b_idx,
                    text=" ".join(current_lines),
                    bbox=BoundingBox(
                        page_number=page_num,
                        x0=round(curr_x0, 2),
                        y0=round(curr_y0, 2),
                        x1=523.0,
                        y1=round(last_y + 14.0, 2),
                    ),
                )
            )

    return blocks


def score_block_relevance(blk: ParsedTextBlock) -> int:
    """
    Score a `ParsedTextBlock` by S106 legal/financial relevance so covenant schedules,
    £ contributions, triggers, and indexation clauses are prioritized over boilerplate.
    """
    score = 0
    # Always preserve Page 1 cover/definitions (application ref, date, site, developer, ward)
    if blk.page_number == 1:
        score += 25
    elif blk.page_number == 2:
        score += 8

    for pattern, weight in _HIGH_SIGNAL_PATTERNS:
        if pattern.search(blk.text):
            score += weight

    if _BOILERPLATE_PENALTY_RE.search(blk.text):
        score -= 15

    return score


def filter_relevant_s106_blocks(
    blocks: list[ParsedTextBlock], max_chars: int = 18000
) -> list[ParsedTextBlock]:
    """
    Filter and select the highest-signal S106 covenant, schedule, and header blocks
    from multi-page deeds (30–60 pages) so they fit cleanly within `max_chars`
    (~4,500 tokens, well inside the DGX Spark 8,192-token limit) while preserving
    original `(page_number, block_index)` reading order.
    """
    if not blocks:
        return []

    # Check if all blocks (formatted) already fit inside max_chars
    total_raw_len = sum(len(b.text) + 48 for b in blocks)
    if total_raw_len <= max_chars:
        return blocks

    # Score every block and include neighboring context on high-scoring schedule pages
    scored_indices: list[tuple[int, int]] = []
    high_signal_pages: set[int] = set()
    for idx, blk in enumerate(blocks):
        s = score_block_relevance(blk)
        if s >= 12 and blk.page_number > 1:
            high_signal_pages.add(blk.page_number)
        scored_indices.append((s, idx))

    # Boost adjacent blocks on pages that contain a high-signal schedule/obligation
    boosted: list[tuple[int, int]] = []
    for s, idx in scored_indices:
        blk = blocks[idx]
        if blk.page_number in high_signal_pages and s >= 0:
            s += 10
        boosted.append((s, idx))

    # Sort by descending relevance score, breaking ties by earlier document position
    boosted.sort(key=lambda item: (-item[0], item[1]))

    selected_indices: list[int] = []
    running_chars = 0
    for _score, idx in boosted:
        blk = blocks[idx]
        entry_len = len(blk.text) + 48
        if running_chars + entry_len > max_chars:
            continue
        selected_indices.append(idx)
        running_chars += entry_len

    # Restore natural document reading order (page_number, block_index)
    selected_indices.sort()
    return [blocks[i] for i in selected_indices]


def find_quote_bbox(
    blocks: list[ParsedTextBlock], quote_snippet: str
) -> Optional[BoundingBox]:
    """Locate the bounding box of a verbatim clause quote across parsed PDF blocks."""
    cleaned_quote = quote_snippet.replace("£", "GBP ").strip()
    for needle in (quote_snippet.strip()[:55].lower(), cleaned_quote[:55].lower()):
        if not needle:
            continue
        for blk in blocks:
            if needle in blk.text.lower():
                return blk.bbox
    return None


def format_blocks_for_llm(
    blocks: list[ParsedTextBlock], max_chars: int = 18000
) -> str:
    """
    Filter to the highest-signal Schedule/Covenant blocks and format with page and
    coordinate tags for local Nemotron LLM input on the DGX Spark (8,192-token limit).
    """
    filtered = filter_relevant_s106_blocks(blocks, max_chars=max_chars)
    lines: list[str] = []
    running_chars = 0
    for blk in filtered:
        b = blk.bbox
        entry = (
            f"[Page {blk.page_number} | BBox ({b.x0}, {b.y0}, {b.x1}, {b.y1})] {blk.text}"
        )
        if running_chars + len(entry) > max_chars:
            break
        lines.append(entry)
        running_chars += len(entry) + 2
    return "\n\n".join(lines)
