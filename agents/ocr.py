"""
Local OCR for scanned section 106 deeds.

Why this exists
---------------
Nine of the deeds published on Sheffield's own planning register are flat scans:
PyMuPDF returns zero characters for every page, so the text pipeline sees an
empty deed. OCR is done with **RapidOCR (ONNX Runtime)** rather than tesseract
because it installs with `uv pip install rapidocr-onnxruntime` and needs no root
- the Spark's apt is behind a sudo password we do not hold. It is fully local and
air-gap safe, and it returns word boxes, which the evidence viewer needs.

OCR is not trusted blindly
--------------------------
Scanned legal deeds defeat the recogniser in predictable ways:

    means the sum of565.000.00 (five hundred and sixty-five thousand)   -> £565,000.00
    means the sum of f5,0oo.00 (five thousand)                          -> £5,000.00
    15.To pay188,333.33 one hundred and eighty eight thousand ...        -> £88,333.33
    disbursementsof2,0oo.0o(two thousandpounds)                          -> £2,000.00

  * `£` is read as `f`, `E`, `1` or swallowed into the number;
  * `0` is read as the letter `o`;
  * thousands separators appear as `.` as often as `,`;
  * spaces between words disappear.

So every amount recovered from OCR is **reconciled against the words the deed
itself prints in brackets**, which English deeds always do. Digits and words must
agree. If they do not, the amount is reported as unverified rather than guessed,
and the auditor abstains instead of inventing a figure. No arithmetic is done by
the model anywhere in this path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

from shared.schemas import BoundingBox

# 300 DPI is the sweet spot measured on Sheffield scans: 220 DPI lost word spaces,
# 400 DPI was slower without fixing the digit confusions.
OCR_DPI = 300

# Characters the recogniser swaps inside what should be a pure numeric token.
_OCR_DIGIT_FIX = str.maketrans(
    {"o": "0", "O": "0", "o": "0", "l": "1", "I": "1", "i": "1", "S": "5", "B": "8", "g": "9"}
)
# A money-ish token: digits with . or , separators and an optional tail.
_AMOUNT_TOKEN_RE = re.compile(r"(?:(?:£|gbp)\s*)?(\d[\d.,]{2,15}\d)", re.IGNORECASE)
# A number only counts as money when the line says so. Without this the parser
# reports clause numbers, years and paragraph marks as sums.
_MONEY_CONTEXT_RE = re.compile(
    r"(£|gbp|\bpounds?\b|sum of|sums of|to pay|pay\b|commuted|contribution|consideration"
    r"|interest at|£\s?\d)",
    re.IGNORECASE,
)
_ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}

# Longest alternative first, everywhere. With "six" listed before "sixty" the
# recogniser's output "sixty five thousand" parses as 6 + 5 and returns 511,000
# for a £565,000 obligation.
_WORD_ALT = "|".join(
    sorted(
        list(_ONES) + list(_TENS) + ["hundred", "thousand", "million"],
        key=len,
        reverse=True,
    )
)
_NUMBER_WORD_RE = re.compile(_WORD_ALT)
# The words English deeds print next to a sum. Sheffield's scans come back with
# full-width parentheses, so ASCII brackets alone silently match nothing.
# The alternation must be grouped: written bare, the trailing `[\s-]*` binds only
# to the last alternative and the phrase never matches at all.
_NUMBER_PHRASE = rf"(?:{_WORD_ALT}|and|pounds|sterling)"
# Deeds write "(one hundred and eighty eight thousand ... pounds)". Reading up to
# the first "pounds" stops the phrase from running into the next obligation.
_WORDS_TO_POUNDS_RE = re.compile(rf"(({_NUMBER_PHRASE}[\s,-]*?)+)pounds", re.IGNORECASE)
_WORDS_RE = re.compile(rf"(({_NUMBER_PHRASE}[\s,-]*){{3,}})", re.IGNORECASE)
# Opening bracket variants seen after OCR, including the full-width form.
_LEADING_BRACKET_RE = re.compile(r"^[\(\[（{]\s*")


@dataclass
class OcrLine:
    """One recognised line with its box, in PDF page points."""

    text: str
    bbox: BoundingBox
    confidence: float


@dataclass
class OcrAmount:
    """
    A sum read from a scan, with the evidence needed to decide whether to trust it.

    `digits` is the repaired numeric token. `words` is the amount spelled out by the
    deed next to it. `agrees` is only True when both parse and match, and only an
    agreeing amount should ever reach the ledger.
    """

    raw: str
    digits: Optional[float]
    words: Optional[float]
    agrees: bool
    page_number: int
    bbox: BoundingBox

    @property
    def usable(self) -> bool:
        return self.agrees and self.digits is not None


@lru_cache(maxsize=1)
def ocr_backend() -> Optional[Any]:
    """The RapidOCR engine, or None when it is not installed."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    return RapidOCR()


def ocr_available() -> bool:
    return ocr_backend() is not None


def needs_ocr(page: Any) -> bool:
    """True when a page has no text layer but does have page images."""
    if page.get_text().strip():
        return False
    return bool(page.get_images(full=True))


def ocr_page(page: Any, dpi: int = OCR_DPI) -> list[OcrLine]:
    """
    OCR one PDF page into lines with bounding boxes scaled back to page points, so
    the evidence viewer can still highlight the legal source of an OCR'd figure.
    """
    engine = ocr_backend()
    if engine is None:
        return []
    import pymupdf as fitz

    zoom = dpi / 72.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
    result, _ = engine(pixmap.tobytes("png"))
    lines: list[OcrLine] = []
    for item in result or []:
        box, text, confidence = item[0], item[1], float(item[2])
        text = str(text).strip()
        if not text:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        lines.append(
            OcrLine(
                text=text,
                bbox=BoundingBox(
                    page_number=page.number + 1,
                    x0=round(min(xs) / zoom, 2),
                    y0=round(min(ys) / zoom, 2),
                    x1=round(max(xs) / zoom, 2),
                    y1=round(max(ys) / zoom, 2),
                ),
                confidence=confidence,
            )
        )
    return lines


def repair_amount(token: str) -> Optional[float]:
    """
    Turn a mangled OCR money token into a number.

    Separators are decided from the shape of the token, not from locale: a final
    group of exactly two digits is pence, and any group of three before it is a
    thousands separator, which is what fixes `565.000.00` -> 565000.00 and
    `2,0oo.0o` -> 2000.00.
    """
    cleaned = token.translate(_OCR_DIGIT_FIX)
    cleaned = re.sub(r"[^0-9.,]", "", cleaned)
    if not cleaned:
        return None
    parts = re.split(r"[.,]", cleaned)
    if len(parts) == 1:
        return float(parts[0]) if parts[0] else None
    head, tail = parts[:-1], parts[-1]
    # A two digit tail is pence; a three digit tail is a thousands group.
    if len(tail) == 2:
        integer_part = "".join(head)
        return float(f"{integer_part}.{tail}") if integer_part else None
    return float("".join(parts))


def words_to_pounds(text: str) -> Optional[float]:
    """
    Parse the amount spelled out in the deed, e.g. "five hundred and sixty five
    thousand pounds" -> 565000.00. Returns None when the phrase is not readable,
    which is common because OCR glues the words together.
    """
    words = _NUMBER_WORD_RE.findall(text.lower())
    if not words:
        return None
    total = 0
    current = 0
    for word in words:
        if word in _ONES:
            current += _ONES[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word in ("thousand", "million"):
            scale = 1_000 if word == "thousand" else 1_000_000
            total += (current or 1) * scale
            current = 0
    return float(total + current) or None


def extract_amounts(page: Any, lines: Optional[list[OcrLine]] = None) -> list[OcrAmount]:
    """
    Every money candidate on an OCR'd page, each reconciled against the words the
    deed prints beside it. `agrees` is False unless digits and words both parse
    and match to the nearest pound, since deeds spell the pounds and write the
    pence as digits.
    """
    lines = lines if lines is not None else ocr_page(page)
    amounts: list[OcrAmount] = []
    for index, line in enumerate(lines):
        if not _MONEY_CONTEXT_RE.search(line.text):
            continue
        # The deed spells the sum out in words, but the recogniser breaks the line
        # mid-phrase, so the words have to be read from a window of lines.
        context = " ".join(item.text for item in lines[index:index + 3])
        for match in _AMOUNT_TOKEN_RE.finditer(context):
            # A window overlaps the next lines; only claim sums that start on this line.
            if match.start() >= len(line.text):
                continue
            digits = repair_amount(match.group(1))
            if digits is None or digits <= 0:
                continue
            words = None
            tail = _LEADING_BRACKET_RE.sub("", context[match.end():].strip())
            phrase = _WORDS_TO_POUNDS_RE.match(tail) or _WORDS_RE.match(tail)
            if phrase:
                words = words_to_pounds(phrase.group(1))
            agrees = (
                words is not None
                and digits is not None
                # Deeds spell the pounds and write the pence as digits, so the
                # words are only expected to agree to the nearest pound.
                and abs(digits - words) < 1.0
            )
            amounts.append(
                OcrAmount(
                    raw=match.group(0),
                    digits=digits,
                    words=words,
                    agrees=agrees,
                    page_number=line.bbox.page_number,
                    bbox=line.bbox,
                )
            )
    # Overlapping line windows report the same sum more than once.
    unique: dict[tuple, OcrAmount] = {}
    for amount in amounts:
        unique.setdefault(
            (amount.page_number, amount.digits, amount.bbox.y0, amount.bbox.x0), amount
        )
    return list(unique.values())
