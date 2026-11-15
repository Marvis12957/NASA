"""Shared evidence hygiene for the ask pipeline.

Three jobs, all deterministic:

1. Failed-OCR detection — chunk previews produced by a failed OCR pass
   (``[ocr: false]`` plus symbol soup) must never reach synthesis or the
   citation list.
2. A final-answer dump guard — the template composer in orchestrator.py
   renders raw retrieval payloads (``Chunk ...``, ``Graph neighbors:``,
   ``Supporting quote ...``, community lines with ``PSI ids:``). That text
   is a fallback for the LLM, never a user-facing answer; if it ever
   reaches the finalized answer, the caller replaces the whole answer.
3. Image-intent detection for questions that ask to SEE a flame picture.
"""

from __future__ import annotations

import re

_OCR_FAIL_RE = re.compile(r"\[\s*ocr\s*:\s*false\s*\]", re.IGNORECASE)


def is_garbage_text(text: str | None) -> bool:
    """True for failed-OCR / symbol-soup text.

    Calibrated on retrieved previews: failed STDCE OCR pages score a
    letter ratio of 0.18-0.21, healthy previews 0.54+. The explicit
    ``[ocr: false]`` marker is definitive on its own.
    """
    if not text:
        return False
    # "[ocr: false]" only means the page had a text layer and needed no OCR: it is on
    # 99.5% of PDF chunks (5029 of 5054, 96% of them clean prose), so it is not a garbage
    # signal. Judge the text that follows it.
    stripped = _OCR_FAIL_RE.sub("", text).strip()
    if len(stripped) >= 60:
        letters = sum(1 for ch in stripped if ch.isalpha())
        if letters / len(stripped) < 0.35:
            return True
    return False


def filter_hits(hits: list[dict] | None) -> list[dict]:
    """Drop search hits whose preview is failed-OCR garbage."""
    return [
        hit
        for hit in hits or []
        if not is_garbage_text(str(hit.get("preview") or ""))
    ]


def is_citable_community(community: dict | None) -> bool:
    """A community may be cited only when it names at least one PSI id."""
    return bool((community or {}).get("psiIds"))


# --- dump guard ------------------------------------------------------------

_DUMP_LITERALS = (
    "Graph neighbors:",
    "Supporting quote",
    "[ocr:",
    "Catalog fuels:",
    "PSI ids:",
    "[figure: data/",
)
# Composer chunk lines start at a line start ("Chunk web/... (web:luci,
# p.40): ..."). The inline pattern catches the same shape mid-line. A bare
# "Chunk " word inside prose (e.g. an LLM table header "| Chunk /
# Community |") is NOT a dump and must not trip the guard.
_DUMP_CHUNK_LINE_RE = re.compile(r"(?m)^Chunk \S")
_DUMP_CHUNK_INLINE_RE = re.compile(r"Chunk \S+ \(\S+, p\.")


def contains_dump(text: str | None) -> bool:
    """True when a finalized answer body leaks raw retrieval payload."""
    if not text:
        return False
    if any(marker in text for marker in _DUMP_LITERALS):
        return True
    return bool(
        _DUMP_CHUNK_LINE_RE.search(text) or _DUMP_CHUNK_INLINE_RE.search(text)
    )


# --- image intent ----------------------------------------------------------

_IMAGE_INTENT_RE = re.compile(
    r"hình ảnh|hinh anh|bức ảnh|tấm ảnh|ảnh chụp|hình chụp|bức hình|tấm hình|"
    r"xem ảnh|xem hình|muốn xem|cho .{0,20}?(?:ảnh|hình)|"
    r"ảnh (?:về|của|ngọn|nào)|hình (?:về|nào)|có ảnh|"
    r"\bimage\b|\bpicture\b|\bphoto\b|\bvideo\b|"
    r"show me .{0,30}?(?:image|picture|photo)",
    re.IGNORECASE,
)


def wants_image(question: str | None) -> bool:
    return bool(_IMAGE_INTENT_RE.search(question or ""))


# --- false image-refusal claims ----------------------------------------------

_IMAGE_REFUSAL_RE = re.compile(
    r"không thể (?:cung cấp|xuất|hiển thị|gửi|tạo|đưa|trình bày)[^.\n]{0,40}(?:ảnh|hình|media)|"
    r"không có khả năng (?:sinh|gửi|hiển thị|tạo)[^.\n]{0,40}(?:ảnh|hình|media)|"
    r"không có (?:bất kỳ )?(?:hình ảnh|ảnh|chunk|dữ liệu media|media)\b|"
    r"không trả về chunk|trợ lý (?:graphrag )?văn bản|"
    r"không hỗ trợ[^.\n]{0,60}hình ảnh|"
    r"chưa có[^.\n]{0,50}(?:hình ảnh|ảnh|hình|media)|"
    r"không kèm[^.\n]{0,30}(?:file )?(?:hình|ảnh|media)|"
    r"không có file (?:hình|ảnh)|"
    r"cannot provide (?:an? )?image|no chunks? (?:were|was) (?:retrieved|returned)|"
    r"no (?:images?|media) (?:were|was) returned|"
    r"does not include (?:any )?(?:image|media) files?|"
    r"no (?:displayable|viewable) (?:images?|pictures?|photos?)",
    re.IGNORECASE,
)


def contains_image_refusal(text: str | None) -> bool:
    """True when an answer falsely claims it cannot show images, or that the
    graph holds no media/chunks — claims that must never stand when the
    library actually has figures for the question."""
    return bool(text and _IMAGE_REFUSAL_RE.search(text))


__all__ = [
    "contains_dump",
    "contains_image_refusal",
    "filter_hits",
    "is_citable_community",
    "is_garbage_text",
    "wants_image",
]
