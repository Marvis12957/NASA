#!/usr/bin/env python3
"""Page-based parent/child chunking for PSI Markdown (GraphRAG corpus).

Writes:
  data/chunks/children.jsonl
  data/chunks/parents.jsonl

Skips engineering drawing ingest. Does not chunk CSV tables or catalog.json.
Does not build embeddings.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEXT_ROOT = ROOT / "data" / "text"
CHUNK_DIR = ROOT / "data" / "chunks"

TOKEN_MULT = 1.3
TINY_TOKENS = 200
MERGE_TARGET = 400
CHILD_TARGET_MAX = 800
VERY_LONG = 1000
MIN_SPLIT_PART = 200

PAGE_RE = re.compile(r"^\[page:\s*(\d+)\]\s*$", re.MULTILINE)
OCR_RE = re.compile(r"\[ocr:\s*(true|false)\]", re.IGNORECASE)
FIGURE_RE = re.compile(
    r"\[figure:\s*\d+\s*\|\s*page:\s*\d+\s*\|\s*file:\s*([^\]]+?)\]",
    re.IGNORECASE,
)
HEADING_RE = re.compile(
    r"(?im)^(?:#{1,3}\s+)?(?:\d+[\.\):]\s+)?"
    r"(objectives?|results?(?:\s+and\s+discussion)?|conclusions?)\s*$"
)
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
KEEP_ENGINEERING_RE = re.compile(
    r"(?i)(requirements?|requirments?|implementation|vpred|\berd\b|\bsrd\b|"
    r"manual|software\s+interface|verification\s+plan|anemometry|velocity\s+measurement)"
)
CAD_STEM_RE = re.compile(
    r"(?i)^(?:s\d{4}[a-z]{2,4}\d|"  # s1101mfa1506, s1183mfd1022
    r"\d{5}[a-z]{2,4}\d|"  # 60089MFHA151, 67235MFAC17146
    r"\d{5}mdfh\d)"  # 60054mdfh001
)
DRAWING_TREE_RE = re.compile(r"(?i)drawing[\s_-]*tree")
DRAWING_ZIP_RE = re.compile(r"(?i)acme_drawings[^/]*\.zip")


def token_estimate(text: str) -> int:
    words = [w for w in text.split() if w]
    return int(round(len(words) * TOKEN_MULT))


def parse_front_matter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, parts[2].lstrip("\n")


def load_sidecar(md_path: Path) -> dict:
    sidecar = md_path.with_suffix(".json")
    if sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def split_pages(body: str) -> list[dict]:
    matches = list(PAGE_RE.finditer(body))
    if not matches:
        text = body.strip()
        return [
            {
                "page": None,
                "text": text,
                "ocr": False,
                "figures": extract_figures(text),
            }
        ]
    pages: list[dict] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[start:end].strip()
        ocr_hits = [m.group(1).lower() == "true" for m in OCR_RE.finditer(text)]
        pages.append(
            {
                "page": int(match.group(1)),
                "text": text,
                "ocr": any(ocr_hits),
                "figures": extract_figures(text),
            }
        )
    return pages


def extract_figures(text: str) -> list[str]:
    seen: list[str] = []
    for path in FIGURE_RE.findall(text):
        cleaned = path.strip()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return seen


def source_norm(source: str) -> str:
    return source.replace("\\", "/")


def is_drawing_doc(*, source: str, pages: int, body: str, filename: str) -> bool:
    """Skip CAD drawing ingest; keep requirement/report engineering PDFs."""
    src = source_norm(source)
    low = src.lower()
    if "acme_drawings" in low or DRAWING_ZIP_RE.search(src):
        return True
    name = Path(src).name or filename
    if KEEP_ENGINEERING_RE.search(name) or KEEP_ENGINEERING_RE.search(src):
        return False
    cad = bool(CAD_STEM_RE.match(name)) or bool(DRAWING_TREE_RE.search(name))
    if not cad:
        # Loose drawing-number files that include a title after the stem.
        cad = bool(re.match(r"(?i)^(?:s\d{4}[a-z]{2,4}\d|\d{5}[a-z]{2,4}\d)", name))
    if not cad:
        return False
    # CAD stems are drawing ingest even when OCR fills a title block / notes page.
    return True


def is_spreadsheet(source: str, body: str) -> bool:
    low = source_norm(source).lower()
    if low.endswith(".xlsx") or low.endswith(".xls"):
        return True
    return bool(re.search(r"(?m)^- dump: (compact preview|skipped|header-only)\s*$", body))


def title_for(meta: dict, body: str, source: str, doc_id: str) -> str:
    if meta.get("title"):
        return meta["title"]
    match = H1_RE.search(body)
    if match:
        return match.group(1).strip()
    name = Path(source_norm(source)).stem if source else doc_id
    name = re.sub(r"[_-]+", " ", name).strip()
    return name or doc_id


def split_long_page(page: dict) -> list[dict]:
    text = page["text"]
    tokens = token_estimate(text)
    if tokens < VERY_LONG:
        return [page]
    if page["figures"]:
        return [page]
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return [page]
    cuts = [0]
    for match in matches:
        pos = match.start()
        if pos <= 0:
            continue
        if all(abs(pos - c) > 40 for c in cuts):
            cuts.append(pos)
    cuts.append(len(text))
    parts: list[dict] = []
    for i in range(len(cuts) - 1):
        chunk = text[cuts[i] : cuts[i + 1]].strip()
        if not chunk:
            continue
        parts.append(
            {
                "page": page["page"],
                "text": chunk,
                "ocr": page["ocr"],
                "figures": extract_figures(chunk),
            }
        )
    if len(parts) < 2:
        return [page]
    # Drop splits that leave a fragment too small unless the original is huge.
    if any(token_estimate(p["text"]) < MIN_SPLIT_PART for p in parts):
        return [page]
    return parts


def merge_units(units: list[dict]) -> list[dict]:
    if not units:
        return []
    merged: list[list[dict]] = []
    buf: list[dict] = []
    buf_tokens = 0

    def flush() -> None:
        nonlocal buf, buf_tokens
        if buf:
            merged.append(buf)
        buf = []
        buf_tokens = 0

    for unit in units:
        tok = token_estimate(unit["text"])
        if not buf:
            buf = [unit]
            buf_tokens = tok
            continue
        can_fill_tiny = buf_tokens < TINY_TOKENS and buf_tokens + tok <= CHILD_TARGET_MAX
        can_pack_tinies = (
            buf_tokens < MERGE_TARGET
            and tok < TINY_TOKENS
            and buf_tokens + tok <= MERGE_TARGET
        )
        if can_fill_tiny or can_pack_tinies:
            buf.append(unit)
            buf_tokens += tok
            continue
        flush()
        buf = [unit]
        buf_tokens = tok
    flush()

    out: list[dict] = []
    for group in merged:
        texts = [u["text"] for u in group if u["text"]]
        pages = [u["page"] for u in group if u["page"] is not None]
        figures: list[str] = []
        for unit in group:
            for path in unit["figures"]:
                if path not in figures:
                    figures.append(path)
        out.append(
            {
                "page": pages[0] if len(set(pages)) == 1 else (pages[0] if pages else None),
                "pages": pages,
                "text": "\n\n".join(texts).strip(),
                "ocr": any(u["ocr"] for u in group),
                "figures": figures,
            }
        )
    return out


def pages_span(pages: list[int], fallback: int | None) -> list[int | None]:
    if pages:
        return [min(pages), max(pages)]
    if fallback is not None:
        return [fallback, fallback]
    return [None, None]


def child_id(psi: str, doc_id: str, span: list, part: int | None = None) -> str:
    a, b = span
    if a is None and b is None:
        suffix = "all"
    elif a == b:
        suffix = f"p{int(a):03d}"
    else:
        suffix = f"p{int(a):03d}-{int(b):03d}"
    ident = f"{psi}/{doc_id}/{suffix}"
    if part is not None:
        ident = f"{ident}/s{part}"
    return ident


def iter_markdown() -> list[Path]:
    if not TEXT_ROOT.exists():
        return []
    found: list[Path] = []
    for path in sorted(TEXT_ROOT.rglob("*.md")):
        if path.name == "README.md" or path.name.startswith("._"):
            continue
        found.append(path)
    return found


def chunk_document(md_path: Path) -> tuple[dict | None, list[dict], str | None]:
    raw = md_path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_front_matter(raw)
    sidecar = load_sidecar(md_path)
    psi = str(sidecar.get("psi") or meta.get("psi") or md_path.parent.name)
    source = str(sidecar.get("source") or meta.get("source") or "")
    doc_id = md_path.stem
    try:
        pages_n = int(sidecar.get("pages") or meta.get("pages") or 0)
    except (TypeError, ValueError):
        pages_n = 0

    if is_drawing_doc(source=source, pages=pages_n or 1, body=body, filename=md_path.name):
        return None, [], "drawing"

    investigation = doc_id == "investigation"
    spreadsheet = is_spreadsheet(source, body)
    title = title_for(meta, body, source, doc_id)
    parent_id = f"{psi}/{doc_id}"

    if investigation or spreadsheet:
        text = body.strip()
        span = pages_span([], 1 if pages_n else None)
        if investigation:
            page_val = None
        else:
            page_val = 1 if pages_n else None
        cid = f"{psi}/{doc_id}/all" if investigation else child_id(psi, doc_id, [page_val or 1, page_val or 1])
        child = {
            "id": cid,
            "parent_id": parent_id,
            "psi": psi,
            "source": source,
            "doc_id": doc_id,
            "page": page_val,
            "pages_span": [page_val, page_val] if investigation else span,
            "figures": extract_figures(text),
            "ocr": any(m.group(1).lower() == "true" for m in OCR_RE.finditer(text)),
            "text": text,
            "token_estimate": token_estimate(text),
        }
        parent = {
            "id": parent_id,
            "psi": psi,
            "source": source,
            "doc_id": doc_id,
            "pages": pages_n or 1,
            "child_ids": [cid],
            "title": title,
        }
        return parent, [child], None

    page_blocks = split_pages(body)
    units: list[dict] = []
    for page in page_blocks:
        units.extend(split_long_page(page))
    chunks = merge_units(units)

    children: list[dict] = []
    used_ids: dict[str, int] = {}
    for chunk in chunks:
        pages = chunk["pages"]
        span = pages_span(pages, None)
        base = child_id(psi, doc_id, span)
        used_ids[base] = used_ids.get(base, 0) + 1
        cid = base if used_ids[base] == 1 else f"{base}/s{used_ids[base]}"
        page_val = chunk["page"]
        if page_val is None and len(set(pages)) == 1:
            page_val = pages[0]
        elif pages and min(pages) != max(pages):
            page_val = min(pages)
        children.append(
            {
                "id": cid,
                "parent_id": parent_id,
                "psi": psi,
                "source": source,
                "doc_id": doc_id,
                "page": page_val,
                "pages_span": span,
                "figures": chunk["figures"],
                "ocr": chunk["ocr"],
                "text": chunk["text"],
                "token_estimate": token_estimate(chunk["text"]),
            }
        )

    # Repair duplicate ids if a later split reused a span.
    seen: dict[str, int] = {}
    for child in children:
        ident = child["id"]
        seen[ident] = seen.get(ident, 0) + 1
        if seen[ident] > 1:
            child["id"] = f"{ident}/s{seen[ident]}"

    parent = {
        "id": parent_id,
        "psi": psi,
        "source": source,
        "doc_id": doc_id,
        "pages": pages_n or len(page_blocks),
        "child_ids": [c["id"] for c in children],
        "title": title,
    }
    return parent, children, None


def jsonl_line(row: dict) -> str:
    # JSON allows U+2028/U+2029; they still break one-record-per-line readers.
    payload = json.dumps(row, ensure_ascii=False)
    return (
        payload.replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
        .replace("\x85", "\\u0085")
        .replace("\r", "\\r")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Chunk PSI Markdown into parent/child JSONL.")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N markdown files (0 = all).")
    args = parser.parse_args()

    files = iter_markdown()
    if args.limit:
        files = files[: args.limit]

    parents: list[dict] = []
    children: list[dict] = []
    skipped_drawings = 0
    skipped_empty = 0

    for path in files:
        parent, kids, reason = chunk_document(path)
        if reason == "drawing":
            skipped_drawings += 1
            continue
        if parent is None or not kids:
            skipped_empty += 1
            continue
        parents.append(parent)
        children.extend(kids)

    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    parents_path = CHUNK_DIR / "parents.jsonl"
    children_path = CHUNK_DIR / "children.jsonl"
    parents_path.write_text("".join(jsonl_line(row) + "\n" for row in parents), encoding="utf-8")
    children_path.write_text("".join(jsonl_line(row) + "\n" for row in children), encoding="utf-8")

    estimates = [c["token_estimate"] for c in children]
    estimates_sorted = sorted(estimates)
    median = int(statistics.median(estimates_sorted)) if estimates_sorted else 0
    examples = [c["id"] for c in children[:2]]
    print(f"parents {len(parents)}")
    print(f"children {len(children)}")
    print(f"skipped_drawing_docs {skipped_drawings}")
    if estimates_sorted:
        print(
            f"child_token_estimate min {estimates_sorted[0]} "
            f"median {median} max {estimates_sorted[-1]}"
        )
    else:
        print("child_token_estimate min 0 median 0 max 0")
    print("example_child_ids " + (", ".join(examples) if examples else "(none)"))
    print(f"wrote {CHUNK_DIR / 'children.jsonl'}")
    print(f"wrote {CHUNK_DIR / 'parents.jsonl'}")
    if skipped_empty:
        print(f"skipped_empty {skipped_empty}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
