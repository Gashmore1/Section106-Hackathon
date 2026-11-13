"""
Track 2: PyMuPDF (`fitz`) S106 Deed Parser & Bounding Box Evidence Extractor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.schemas import BoundingBox


@dataclass
class ParsedTextBlock:
    """A single text block extracted from a PDF page with coordinates."""

    page_number: int
    block_index: int
    text: str
    bbox: BoundingBox


def parse_s106_pdf(pdf_path: str | Path) -> list[ParsedTextBlock]:
    """
    Parse a Section 106 PDF deed using PyMuPDF (`fitz`) and extract all text blocks
    along with their 1-indexed page numbers and bounding boxes (`[x0, y0, x1, y1]`).
    """
    import fitz  # PyMuPDF

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"S106 PDF not found at {path}")

    blocks: list[ParsedTextBlock] = []
    doc = fitz.open(str(path))
    try:
        for page_idx, page in enumerate(doc):
            page_num = page_idx + 1
            raw_blocks = page.get_text("blocks")
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
    finally:
        doc.close()

    return blocks


def find_quote_bbox(
    blocks: list[ParsedTextBlock], quote_snippet: str
) -> Optional[BoundingBox]:
    """Locate the bounding box of a verbatim clause quote across parsed PDF blocks."""
    needle = quote_snippet.strip()[:60].lower()
    for blk in blocks:
        if needle in blk.text.lower():
            return blk.bbox
    return None


def format_blocks_for_llm(
    blocks: list[ParsedTextBlock], max_chars: int = 18000
) -> str:
    """
    Format parsed PyMuPDF blocks with page and coordinate tags for local LLM input.
    Truncates cleanly at `max_chars` (~4,500 tokens) to stay well within the DGX Spark
    8,192-token context window limit (leaving room for system prompt + JSON schema + output).
    """
    lines: list[str] = []
    running_chars = 0
    for blk in blocks:
        b = blk.bbox
        entry = (
            f"[Page {blk.page_number} | BBox ({b.x0}, {b.y0}, {b.x1}, {b.y1})] {blk.text}"
        )
        if running_chars + len(entry) > max_chars:
            break
        lines.append(entry)
        running_chars += len(entry) + 2
    return "\n\n".join(lines)
