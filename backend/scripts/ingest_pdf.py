#!/usr/bin/env python3
"""Convert PSI PDFs to tagged Markdown plus extracted images.

Writes, mirroring the folders under data/psi (Reports, Science Documents, ...):
  data/text/{psi folder}/{section}/{slug}.md
  data/images/{psi folder}/{section}/{slug}/p{page:02d}-f{n:02d}.png
  data/images/{psi folder}/{section}/{slug}/p{page:02d}-f{n:02d}.json
    (includes caption + caption_position when a caption sits above or below the image)
  data/text/{psi folder}/metadata/investigation.md  (from i_Investigation.txt)

Does not dump experiment CSVs. OCR is optional: if tesseract is missing,
image-only pages are still written with [ocr: false] and a sidecar log note.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.catalog import metadata_field, slug as catalog_slug  # noqa: E402

PSI = ROOT / "data" / "psi"
TEXT_ROOT = ROOT / "data" / "text"
IMAGE_ROOT = ROOT / "data" / "images"
LOG_DIR = ROOT / "backend" / "var"
MIN_IMAGE_EDGE = 40
MIN_IMAGE_BYTES = 1500
MIN_TEXT_CHARS = 40
SKIP_DIR_NAMES = {".venv", "node_modules", ".git", "__pycache__"}
TESSERACT_BIN = shutil.which("tesseract")


def psi_id_from_path(path: Path) -> str:
    try:
        parts = path.relative_to(PSI).parts
    except ValueError:
        parts = path.parts
    for part in parts:
        if part.startswith("PSI-"):
            return part.split("_", 1)[0]
        if part == "ACME_common":
            return "ACME_common"
    return "unknown"


def source_rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def document_dir(path: Path) -> Path:
    """Folder of a PSI file relative to data/psi.

    Keeps the investigation directory and the section (Reports, Science
    Documents, Presentations, ...) so a document stays in the same mục.
    """
    try:
        return path.relative_to(PSI).parent
    except ValueError:
        return Path(psi_id_from_path(path))


def drop_flat_copy(psi_id: str, slug: str, dest_dir: Path) -> None:
    """Delete the old data/{text,images}/{PSI-id}/{slug} copy."""
    if dest_dir == Path(psi_id):
        return
    for ext in (".md", ".json"):
        old = TEXT_ROOT / psi_id / f"{slug}{ext}"
        if old.is_file():
            old.unlink()
    old_images = IMAGE_ROOT / psi_id / slug
    new_images = IMAGE_ROOT / dest_dir / slug
    if old_images.is_dir() and old_images.resolve() != new_images.resolve():
        shutil.rmtree(old_images)


def file_slug(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"(?i)^psi-\d+[_-]?", "", stem)
    text = catalog_slug(stem)
    return text[:64] or "document"


def unique_slug(psi_id: str, path: Path, used: dict[str, Path]) -> str:
    base = file_slug(path)
    candidate = base
    n = 2
    key = f"{psi_id}/{candidate}"
    while key in used and used[key] != path:
        candidate = f"{base}-{n}"
        key = f"{psi_id}/{candidate}"
        n += 1
    used[key] = path
    return candidate


def iter_pdfs(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.name.startswith("._"):
            continue
        found.append(path)
    return sorted(found)


def iter_investigations(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("i_Investigation.txt"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        found.append(path)
    return sorted(found)


def sanitize(text: str) -> str:
    return text.encode("utf-8", errors="replace").decode("utf-8")


def yaml_escape(value: str) -> str:
    value = sanitize(value)
    if value == "":
        return '""'
    if any(ch in value for ch in ":#{}[]&*!|>'\"%@`"):
        return json.dumps(value, ensure_ascii=False)
    return value


def page_text_layer(page) -> str:
    text = page.get_text("text") or ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return sanitize(text.strip())


def try_ocr(page) -> tuple[str, bool, str | None]:
    """Return (text, used_ocr, skip_note)."""
    if TESSERACT_BIN is None:
        return "", False, "tesseract not installed; skipped OCR"
    try:
        textpage = page.get_textpage_ocr(flags=0, language="eng", dpi=200, full=True)
        text = page.get_text("text", textpage=textpage) or ""
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        return sanitize(text), True, None
    except Exception as exc:  # noqa: BLE001
        return "", False, f"OCR failed: {exc}"


def native_pixmap(doc, xref: int):
    """Full embedded image for xref — never clipped to a page bbox.

    extract_image() returns the original raster at native width/height.
    Pixmap(doc, xref) can follow a display clip; do not use it here.
    """
    import pymupdf

    extracted = doc.extract_image(xref)
    if not extracted or not extracted.get("image"):
        return None, {}
    width = int(extracted.get("width") or 0)
    height = int(extracted.get("height") or 0)
    if width < MIN_IMAGE_EDGE or height < MIN_IMAGE_EDGE:
        return None, {}
    try:
        pix = pymupdf.Pixmap(extracted["image"])
    except Exception:
        return None, {}
    smask = int(extracted.get("smask") or 0)
    if smask > 0:
        mask_info = doc.extract_image(smask)
        if mask_info and mask_info.get("image"):
            try:
                mask = pymupdf.Pixmap(mask_info["image"])
            except Exception:
                mask = None
            if mask is not None and mask.width == pix.width and mask.height == pix.height:
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                pix = pymupdf.Pixmap(pix, mask)
    if pix.n - pix.alpha > 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    colorspace = extracted.get("colorspace")
    if colorspace is not None:
        colorspace = str(colorspace)
    elif getattr(pix, "colorspace", None):
        colorspace = str(pix.colorspace)
    extra = {
        "xref": xref,
        "colorspace": colorspace,
        "pdf_filter": extracted.get("ext"),
        "smask": smask or None,
    }
    return pix, extra


CAPTION_START_RE = re.compile(
    r"^(?:figure|fig\.?|table|photo|plate)\s*\d+\b",
    re.IGNORECASE,
)
FORMAL_CAPTION_GAP = 48.0   # points from image edge to a "Figure N" caption
LABEL_CAPTION_GAP = 22.0    # points for a short title that is not numbered
CAPTION_LINE_GAP = 9.0      # wrapped lines of the same caption
MAX_CAPTION_LINES = 8
MAX_LABEL_WORDS = 22


def should_keep_image(pix, raw_bytes: int | None = None) -> bool:
    if pix is None:
        return False
    if pix.width < MIN_IMAGE_EDGE or pix.height < MIN_IMAGE_EDGE:
        return False
    size = raw_bytes if raw_bytes is not None else len(pix.tobytes())
    if size < MIN_IMAGE_BYTES and max(pix.width, pix.height) < 120:
        return False
    return True


def _clean_caption(text: str) -> str:
    parts: list[str] = []
    for piece in re.split(r"\s+", text.strip()):
        if not piece:
            continue
        if parts and parts[-1].endswith("-") and piece[:1].islower():
            parts[-1] = parts[-1][:-1] + piece
        else:
            parts.append(piece)
    return " ".join(parts).strip()


def caption_tag(caption: str) -> str:
    text = _clean_caption(caption).replace("[", "(").replace("]", ")")
    return f"[caption: {text}]"


def _union_bbox(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _page_text_lines(page) -> list[dict]:
    """Reading-order lines, with fragments on the same row joined."""
    raw: list[dict] = []
    try:
        blocks = page.get_text("dict").get("blocks") or []
    except Exception:
        return []
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            text = "".join(span.get("text") or "" for span in line.get("spans") or []).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            size = max((float(span.get("size") or 0) for span in line.get("spans") or []), default=0.0)
            if size <= 0:
                size = max(1.0, y1 - y0)
            raw.append({"text": text, "bbox": (x0, y0, x1, y1), "size": size})
    raw.sort(key=lambda row: (round(row["bbox"][1], 1), row["bbox"][0]))
    merged: list[dict] = []
    for row in raw:
        if merged:
            prev = merged[-1]
            py0, py1 = prev["bbox"][1], prev["bbox"][3]
            y0, y1 = row["bbox"][1], row["bbox"][3]
            overlap = min(py1, y1) - max(py0, y0)
            same_row = overlap > 0.5 * min(py1 - py0, y1 - y0)
            gap = row["bbox"][0] - prev["bbox"][2]
            # Join split spans of one line. A wider gap is a column gutter.
            tight = gap <= max(3.5, 0.3 * prev["size"])
            if same_row and row["bbox"][0] >= prev["bbox"][2] - 3 and tight:
                joiner = "" if gap <= 1 else " "
                prev["text"] = _clean_caption(prev["text"] + joiner + row["text"])
                prev["bbox"] = (
                    min(prev["bbox"][0], row["bbox"][0]),
                    min(prev["bbox"][1], row["bbox"][1]),
                    max(prev["bbox"][2], row["bbox"][2]),
                    max(prev["bbox"][3], row["bbox"][3]),
                )
                prev["size"] = max(prev["size"], row["size"])
                continue
        merged.append({"text": row["text"], "bbox": row["bbox"], "size": row["size"]})
    return merged


def _horiz_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return min(a[2], b[2]) - max(a[0], b[0])


def _horiz_aligned(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    overlap = _horiz_overlap(a, b)
    if overlap <= 0:
        return False
    aw = max(1.0, a[2] - a[0])
    bw = max(1.0, b[2] - b[0])
    return overlap >= 24 or overlap >= 0.2 * min(aw, bw)


def _label_aligned(caption: tuple[float, float, float, float], image: tuple[float, float, float, float]) -> bool:
    """A short title must actually sit in the image column, not in the next column."""
    overlap = _horiz_overlap(caption, image)
    if overlap <= 0:
        return False
    caption_width = max(1.0, caption[2] - caption[0])
    image_width = max(1.0, image[2] - image[0])
    return overlap >= 0.5 * caption_width and (overlap >= 24 or overlap >= 0.25 * image_width)


def _center_inside(bbox: tuple[float, float, float, float], images: list[tuple[float, float, float, float]]) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    for ix0, iy0, ix1, iy1 in images:
        if ix0 <= cx <= ix1 and iy0 <= cy <= iy1:
            return True
    return False


def _image_between(
    y0: float,
    y1: float,
    x0: float,
    x1: float,
    placements: list[dict],
    skip: dict,
) -> bool:
    top, bot = (y0, y1) if y0 <= y1 else (y1, y0)
    for place in placements:
        if place is skip:
            continue
        ix0, iy0, ix1, iy1 = place["bbox"]
        if iy1 <= top + 1 or iy0 >= bot - 1:
            continue
        if _horiz_overlap((x0, 0, x1, 0), (ix0, 0, ix1, 0)) > 12:
            return True
    return False


def _line_gap(prev: dict, nxt: dict) -> float:
    return nxt["bbox"][1] - prev["bbox"][3]


def _same_row(a: dict, b: dict) -> bool:
    ay0, ay1 = a["bbox"][1], a["bbox"][3]
    by0, by1 = b["bbox"][1], b["bbox"][3]
    overlap = min(ay1, by1) - max(ay0, by0)
    return overlap > 0.5 * min(ay1 - ay0, by1 - by0)


def _mostly_in_column(line: dict, column: tuple[float, float, float, float]) -> bool:
    overlap = _horiz_overlap(column, line["bbox"])
    width = max(1.0, line["bbox"][2] - line["bbox"][0])
    return overlap >= 0.45 * width


def _expand_caption(lines: list[dict], start: int, image_boxes: list[tuple[float, float, float, float]]) -> list[dict]:
    """Caption lines under or over a figure, staying in that figure's column.

    Two-column pages put body text on the same row as the caption. Those
    body lines are left out because they do not sit in the caption column.
    """
    anchor = lines[start]
    parts = [anchor]
    ax1 = anchor["bbox"][2]
    rights = [
        other
        for other in lines
        if other is not anchor and _same_row(anchor, other) and other["bbox"][0] >= anchor["bbox"][2] - 2
    ]
    rights.sort(key=lambda row: row["bbox"][0])
    for other in rights:
        gap = other["bbox"][0] - ax1
        if gap > 48:
            break
        parts.append(other)
        ax1 = max(ax1, other["bbox"][2])
    column = _union_bbox([part["bbox"] for part in parts])
    last_bottom = column[3]
    used = {id(part) for part in parts}
    rest = []
    for other in lines:
        if id(other) in used:
            continue
        if other["bbox"][1] < column[1] - 1:
            continue
        if not _mostly_in_column(other, column):
            continue
        if _center_inside(other["bbox"], image_boxes):
            continue
        rest.append(other)
    rest.sort(key=lambda row: (row["bbox"][1], row["bbox"][0]))
    for nxt in rest:
        if CAPTION_START_RE.match(nxt["text"].strip()) and not _same_row(nxt, anchor):
            break
        gap = nxt["bbox"][1] - last_bottom
        if gap > max(CAPTION_LINE_GAP, 0.65 * anchor["size"]):
            break
        if gap < -1.5 and not _same_row(nxt, parts[-1]):
            continue
        parts.append(nxt)
        last_bottom = max(last_bottom, nxt["bbox"][3])
        if len(parts) >= MAX_CAPTION_LINES:
            break
        if sum(len(part["text"]) for part in parts) >= 700:
            break
    return parts


def _block_from(parts: list[dict], kind: str) -> dict:
    return {
        "text": _clean_caption(" ".join(p["text"] for p in parts)),
        "bbox": _union_bbox([p["bbox"] for p in parts]),
        "kind": kind,
    }


def _page_placements(page) -> list[dict]:
    by_xref: dict[int, list[tuple[float, float, float, float]]] = {}
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        infos = []
    for info in infos:
        xref = int(info.get("xref") or 0)
        bbox = info.get("bbox")
        if xref <= 0 or not bbox:
            continue
        x0, y0, x1, y1 = bbox
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        by_xref.setdefault(xref, []).append((x0, y0, x1, y1))
    try:
        images = page.get_images(full=True)
    except Exception:
        images = []
    for img in images:
        xref = int(img[0])
        if xref <= 0 or xref in by_xref:
            continue
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        boxes = [(r.x0, r.y0, r.x1, r.y1) for r in rects if r.width >= 8 and r.height >= 8]
        if boxes:
            by_xref[xref] = boxes
    placements = []
    for xref, boxes in by_xref.items():
        for bbox in boxes:
            placements.append({"xref": xref, "bbox": bbox})
    return placements


def _assign_captions(blocks: list[dict], placements: list[dict], max_gap: float) -> dict[int, tuple[str, str, float]]:
    chosen: dict[int, tuple[str, str, float]] = {}
    for block in blocks:
        if not block["text"]:
            continue
        bx0, by0, bx1, by1 = block["bbox"]
        hits: list[tuple[float, str, dict]] = []
        for place in placements:
            ix0, iy0, ix1, iy1 = place["bbox"]
            aligned = (
                _label_aligned(block["bbox"], place["bbox"])
                if block.get("kind") == "label"
                else _horiz_aligned(block["bbox"], place["bbox"])
            )
            if not aligned:
                continue
            if by0 >= iy1 - 3:
                gap = by0 - iy1
                if gap <= max_gap and not _image_between(iy1, by0, ix0, ix1, placements, place):
                    hits.append((gap, "below", place))
            elif by1 <= iy0 + 3:
                gap = iy0 - by1
                if gap <= max_gap and not _image_between(by1, iy0, ix0, ix1, placements, place):
                    hits.append((gap, "above", place))
        if not hits:
            continue
        best = min(hit[0] for hit in hits)
        for gap, position, place in hits:
            # Side-by-side figures often share one caption even when their
            # bottoms differ by a few points after aspect-fit.
            if gap > best + 16:
                continue
            xref = place["xref"]
            prev = chosen.get(xref)
            if prev is None or gap < prev[2] - 0.5:
                chosen[xref] = (block["text"], position, gap)
            elif abs(gap - prev[2]) <= 0.5 and len(block["text"]) > len(prev[0]):
                chosen[xref] = (block["text"], position, gap)
    return chosen


def _useful_label(text: str) -> bool:
    """Short title above or below a figure, not a panel letter or a cut-off line."""
    words = text.split()
    if len(words) < 2 or len(text) < 8:
        return False
    if text[0].islower() or ">" in text:
        return False
    low = text.lower()
    if low.startswith("www.") or "http://" in low or "https://" in low:
        return False
    if re.fullmatch(r"week of .+", low):
        return False
    if words[-1].lower() in {"the", "a", "an", "of", "and", "or", "to", "for", "with"}:
        return False
    return True


def _label_blocks(lines: list[dict], used: set[int], image_boxes: list[tuple[float, float, float, float]], page) -> list[dict]:
    clusters: list[list[dict]] = []
    current: list[dict] = []
    for index, line in enumerate(lines):
        if index in used or _center_inside(line["bbox"], image_boxes):
            if current:
                clusters.append(current)
                current = []
            continue
        if not current:
            current = [line]
            continue
        prev = current[-1]
        gap = _line_gap(prev, line)
        overlap = _horiz_overlap(prev["bbox"], line["bbox"])
        if -1.5 <= gap <= max(CAPTION_LINE_GAP, 0.65 * prev["size"]) and overlap > 10:
            current.append(line)
        else:
            clusters.append(current)
            current = [line]
    if current:
        clusters.append(current)

    page_width = float(page.rect.width)
    blocks = []
    for cluster in clusters:
        block = _block_from(cluster, "label")
        words = block["text"].split()
        if not words or len(words) > MAX_LABEL_WORDS or len(block["text"]) > 220:
            continue
        if re.fullmatch(r"[\d\W]{1,4}", block["text"]):
            continue
        x0, y0, x1, _y1 = block["bbox"]
        if y0 < 36 and (x1 - x0) > 0.6 * page_width:
            continue
        if not _useful_label(block["text"]):
            continue
        blocks.append(block)
    return blocks


def captions_by_xref(page) -> dict[int, tuple[str, str]]:
    """Map an image xref to (caption, position).

    position is "below" or "above". Numbered captions (Figure 1, Fig. 2,
    Table 3) win over a short unnumbered title sitting on the other side.
    """
    placements = _page_placements(page)
    if not placements:
        return {}
    image_boxes = [place["bbox"] for place in placements]
    lines = [line for line in _page_text_lines(page) if not _center_inside(line["bbox"], image_boxes)]
    formal: list[dict] = []
    used: set[int] = set()
    for index, line in enumerate(lines):
        if index in used or not CAPTION_START_RE.match(line["text"].strip()):
            continue
        parts = _expand_caption(lines, index, image_boxes)
        formal.append(_block_from(parts, "formal"))
        part_ids = {id(part) for part in parts}
        for other_index, other in enumerate(lines):
            if id(other) in part_ids:
                used.add(other_index)
    formal_hits = _assign_captions(formal, placements, FORMAL_CAPTION_GAP)
    label_hits = _assign_captions(
        _label_blocks(lines, used, image_boxes, page),
        placements,
        LABEL_CAPTION_GAP,
    )
    merged: dict[int, tuple[str, str]] = {}
    for xref, (text, position, _gap) in label_hits.items():
        merged[xref] = (text, position)
    for xref, (text, position, gap) in formal_hits.items():
        label = label_hits.get(xref)
        if label is not None and gap > label[2] + 12 and gap > 18:
            continue
        merged[xref] = (text, position)
    return merged


TILE_GAP = 0.2  # points; sliced pieces of one figure touch, separate figures do not
MAX_STITCH_ZOOM = 4.0  # 288 dpi cap so a tiled figure is not a huge file


def _rects_touch(a: tuple[float, float, float, float], b: tuple[float, float, float, float], gap: float = TILE_GAP) -> bool:
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])


def _union_rect(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _display_rect(page, bbox: tuple[float, float, float, float]):
    """Image info is in unrotated PDF space. Pixmap clips use the rotated page."""
    import pymupdf

    rect = pymupdf.Rect(bbox)
    if page.rotation:
        rect = rect * page.rotation_matrix
    return pymupdf.Rect(rect)


def _page_tiles(page) -> list[dict]:
    tiles = []
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return tiles
    for info in infos:
        xref = int(info.get("xref") or 0)
        bbox = info.get("bbox")
        if xref <= 0 or not bbox:
            continue
        x0, y0, x1, y1 = bbox
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        px_w = int(info.get("width") or 0)
        px_h = int(info.get("height") or 0)
        if max(px_w, px_h) < 20 and max(x1 - x0, y1 - y0) < 8:
            continue
        tiles.append({"xref": xref, "bbox": (x0, y0, x1, y1), "px_w": px_w, "px_h": px_h})
    return tiles


def _cluster_tiles(tiles: list[dict]) -> list[list[dict]]:
    parent = list(range(len(tiles)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i in range(len(tiles)):
        for j in range(i + 1, len(tiles)):
            if _rects_touch(tiles[i]["bbox"], tiles[j]["bbox"]):
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for index, tile in enumerate(tiles):
        groups.setdefault(find(index), []).append(tile)
    return list(groups.values())


def _is_sliced_figure(members: list[dict]) -> bool:
    """True when several different images sit edge to edge as one figure."""
    if len({member["xref"] for member in members}) < 2:
        return False
    boxes = [member["bbox"] for member in members]
    x0, y0, x1, y1 = _union_rect(boxes)
    union_area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    max_area = max((box[2] - box[0]) * (box[3] - box[1]) for box in boxes)
    if max_area <= 0:
        return False
    # An image plus its soft mask share one rectangle. Slices cover more area.
    return union_area > max_area * 1.35


def _stitch_zoom(members: list[dict]) -> float:
    zooms = []
    for member in members:
        px_w, px_h = member["px_w"], member["px_h"]
        width = member["bbox"][2] - member["bbox"][0]
        height = member["bbox"][3] - member["bbox"][1]
        if width > 1 and px_w:
            zooms.append(px_w / width)
        if height > 1 and px_h:
            zooms.append(px_h / height)
    if not zooms:
        return 2.0
    return min(MAX_STITCH_ZOOM, max(zooms))


def _render_sliced_figure(page, members: list[dict]):
    import pymupdf

    clip = _display_rect(page, _union_rect([member["bbox"] for member in members]))
    zoom = _stitch_zoom(members)
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)


def _substantial_tile(page, member: dict) -> bool:
    rect = _display_rect(page, member["bbox"])
    return rect.width >= 28 and rect.height >= 28


def _seam_whiteness(pix, disp, axis: str, pos: float, span0: float, span1: float) -> float:
    """Fraction of near-white pixels in a band around the shared edge.

    A sliced chart meets on a hairline. Two separate figures leave a wide
    white gap, so the band stays white.
    """
    if disp.width <= 0 or disp.height <= 0 or pix.width <= 0 or pix.height <= 0:
        return 0.0
    sx = pix.width / disp.width
    sy = pix.height / disp.height
    samples = pix.samples
    channels = pix.n
    white = 0
    total = 0
    band = 3.0  # points
    if axis == "x":
        x = int(round((pos - disp.x0) * sx))
        half = max(1, int(round(band * sx)))
        y0 = int(round((min(span0, span1) - disp.y0) * sy))
        y1 = int(round((max(span0, span1) - disp.y0) * sy))
        y0 = max(0, y0)
        y1 = min(pix.height, y1)
        xs = range(max(0, x - half), min(pix.width, x + half + 1))
        coords = ((x, y) for y in range(y0, y1) for x in xs)
    else:
        y = int(round((pos - disp.y0) * sy))
        half = max(1, int(round(band * sy)))
        x0 = int(round((min(span0, span1) - disp.x0) * sx))
        x1 = int(round((max(span0, span1) - disp.x0) * sx))
        x0 = max(0, x0)
        x1 = min(pix.width, x1)
        ys = range(max(0, y - half), min(pix.height, y + half + 1))
        coords = ((x, y) for y in ys for x in range(x0, x1))
    for x, y in coords:
        index = (y * pix.width + x) * channels
        pixel = samples[index : index + min(3, channels)]
        total += 1
        if pixel and min(pixel) >= 245:
            white += 1
    return white / total if total else 0.0


def _tile_seam(page, left: dict, right: dict) -> tuple[str, float, float, float] | None:
    """Return (axis, position, span_start, span_end) where two tiles meet."""
    first = _display_rect(page, left["bbox"])
    second = _display_rect(page, right["bbox"])
    x_overlap = min(first.x1, second.x1) - max(first.x0, second.x0)
    y_overlap = min(first.y1, second.y1) - max(first.y0, second.y0)
    if y_overlap >= 8 and y_overlap >= x_overlap:
        pos = (first.x1 + second.x0) / 2 if first.x0 <= second.x0 else (second.x1 + first.x0) / 2
        return ("x", pos, max(first.y0, second.y0), min(first.y1, second.y1))
    if x_overlap >= 8:
        pos = (first.y1 + second.y0) / 2 if first.y0 <= second.y0 else (second.y1 + first.y0) / 2
        return ("y", pos, max(first.x0, second.x0), min(first.x1, second.x1))
    return None


def _figure_groups(page, members: list[dict]) -> list[tuple[list[dict], object]]:
    """Split a touched cluster when a white gutter separates two real figures.

    Returns (tiles, rendered pixmap or None). The pixmap is reused when the
    whole cluster is one figure.
    """
    if not _is_sliced_figure(members):
        return [(members, None)]
    try:
        pix = _render_sliced_figure(page, members)
    except Exception:
        return [(members, None)]
    disp = _display_rect(page, _union_rect([member["bbox"] for member in members]))
    parent = list(range(len(members)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, left in enumerate(members):
        for j in range(i + 1, len(members)):
            right = members[j]
            if not _rects_touch(left["bbox"], right["bbox"]):
                continue
            seam = _tile_seam(page, left, right)
            gutter = False
            if seam and _substantial_tile(page, left) and _substantial_tile(page, right):
                axis, pos, span0, span1 = seam
                gutter = _seam_whiteness(pix, disp, axis, pos, span0, span1) >= 0.97
            if not gutter:
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for index, member in enumerate(members):
        groups.setdefault(find(index), []).append(member)
    if len(groups) == 1:
        return [(members, pix)]
    return [(group, None) for group in groups.values()]


def _best_caption(page_captions: dict[int, tuple[str, str]], xrefs: set[int]) -> tuple[str, str] | None:
    hits = [page_captions[xref] for xref in xrefs if xref in page_captions]
    if not hits:
        return None

    def rank(hit: tuple[str, str]) -> tuple[int, int]:
        text = hit[0].lower()
        formal = 0 if text.startswith(("figure", "fig", "table", "photo", "plate")) else 1
        return (formal, -len(hit[0]))

    hits.sort(key=rank)
    return hits[0]


PROSE_WORD_MIN = 25  # a scanned text page, not a plot with a short caption
PAGE_COVER_MIN = 0.85


def _prose_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]{4,}", text or ""))


def _covers_page(page, bbox: tuple[float, float, float, float], fraction: float = PAGE_COVER_MIN) -> bool:
    rect = _display_rect(page, bbox)
    page_area = float(page.rect.width * page.rect.height)
    if page_area <= 0:
        return False
    return rect.width * rect.height >= fraction * page_area


def _is_text_page_scan(page, bbox: tuple[float, float, float, float]) -> bool:
    """A full-page raster with a real text layer is the scan of the page, not a figure."""
    if not _covers_page(page, bbox):
        return False
    try:
        text = page.get_text("text") or ""
    except Exception:
        return False
    return _prose_words(text) >= PROSE_WORD_MIN


def _store_figure(
    *,
    image_dir: Path,
    page_no: int,
    figure: int,
    pix,
    psi_id: str,
    source: str,
    ocr: bool,
    found: tuple[str, str] | None,
    extra: dict | None = None,
    stitched: int = 0,
) -> dict | None:
    stem = f"p{page_no:02d}-f{figure:02d}"
    dest = image_dir / f"{stem}.png"
    try:
        dest.write_bytes(pix.tobytes("png"))
    except Exception:
        try:
            pix.save(str(dest))
        except Exception:
            return None
    if not dest.is_file():
        return None
    if dest.stat().st_size < MIN_IMAGE_BYTES and max(pix.width, pix.height) < 160:
        dest.unlink(missing_ok=True)
        return None
    try:
        rel = str(dest.relative_to(ROOT))
    except ValueError:
        rel = str(dest)
    extra = extra or {}
    colorspace = extra.get("colorspace")
    if not colorspace and getattr(pix, "colorspace", None):
        colorspace = str(pix.colorspace)
    sidecar = {
        "psi": psi_id,
        "source": source,
        "page": page_no,
        "figure": figure,
        "file": rel,
        "width": pix.width,
        "height": pix.height,
        "ocr": bool(ocr),
        "cropped": False,
        "xref": extra.get("xref"),
        "colorspace": colorspace,
        "pdf_filter": extra.get("pdf_filter"),
    }
    if stitched > 1:
        sidecar["stitched"] = stitched
    if found:
        sidecar["caption"] = found[0]
        sidecar["caption_position"] = found[1]
    (image_dir / f"{stem}.json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    return {"file": rel, "caption": found[0] if found else None}


def extract_page_images(
    doc,
    page,
    image_dir: Path,
    page_no: int,
    *,
    psi_id: str,
    source: str,
    ocr: bool,
) -> list[dict]:
    image_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    figure = 0
    page_captions = captions_by_xref(page)
    clusters = _cluster_tiles(_page_tiles(page))

    def sort_key(members: list[dict]) -> tuple[float, float]:
        rect = _display_rect(page, _union_rect([member["bbox"] for member in members]))
        return (rect.y0, rect.x0)

    seen_xrefs: set[int] = set()
    for members in sorted(clusters, key=sort_key):
        union = _union_rect([member["bbox"] for member in members])
        if _is_text_page_scan(page, union):
            continue
        for group, rendered in _figure_groups(page, members):
            if _is_sliced_figure(group):
                if not any(
                    member["px_w"] >= MIN_IMAGE_EDGE and member["px_h"] >= MIN_IMAGE_EDGE
                    for member in group
                ):
                    continue
                pix = rendered
                if pix is None:
                    try:
                        pix = _render_sliced_figure(page, group)
                    except Exception:
                        pix = None
                if pix is None or pix.width < MIN_IMAGE_EDGE or pix.height < MIN_IMAGE_EDGE:
                    continue
                figure += 1
                item = _store_figure(
                    image_dir=image_dir,
                    page_no=page_no,
                    figure=figure,
                    pix=pix,
                    psi_id=psi_id,
                    source=source,
                    ocr=ocr,
                    found=_best_caption(page_captions, {member["xref"] for member in group}),
                    stitched=len(group),
                )
                if item is None:
                    figure -= 1
                    continue
                saved.append(item)
                continue
            for member in group:
                xref = member["xref"]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                if _is_text_page_scan(page, member["bbox"]):
                    continue
                px_w, px_h = member["px_w"], member["px_h"]
                if px_w and px_h and (px_w < MIN_IMAGE_EDGE or px_h < MIN_IMAGE_EDGE):
                    continue
                pix, extra = native_pixmap(doc, xref)
                if not should_keep_image(pix):
                    continue
                figure += 1
                item = _store_figure(
                    image_dir=image_dir,
                    page_no=page_no,
                    figure=figure,
                    pix=pix,
                    psi_id=psi_id,
                    source=source,
                    ocr=ocr,
                    found=page_captions.get(xref),
                    extra=extra,
                )
                if item is None:
                    figure -= 1
                    continue
                saved.append(item)
    return saved


def write_text_sidecar(
    *,
    psi_id: str,
    source: str,
    pages: int,
    file: str,
    ocr_pages: list[int],
    figures: int,
    dest: Path,
) -> None:
    payload = {
        "psi": psi_id,
        "source": source,
        "pages": pages,
        "file": file,
        "ocr_pages": ocr_pages,
        "figures": figures,
        "cropped": False,
    }
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def extract_tables(page) -> list[str]:
    # Optional; skipped so the full PDF batch is not blocked by table detection.
    return []


def convert_pdf(
    path: Path,
    psi_id: str,
    slug: str,
    ocr_log: list[str],
    source: str | None = None,
) -> dict:
    import pymupdf

    rel = source or source_rel(path)
    located = path
    if source:
        candidate = Path(source) if Path(source).is_absolute() else ROOT / source
        try:
            candidate.relative_to(PSI)
        except ValueError:
            pass
        else:
            located = candidate
    dest_dir = document_dir(located)
    doc = pymupdf.open(path)
    try:
        page_count = doc.page_count
        lines = [
            "---",
            f"psi: {psi_id}",
            f"source: {yaml_escape(rel)}",
            f"pages: {page_count}",
            "---",
            "",
        ]
        image_dir = IMAGE_ROOT / dest_dir / slug
        if image_dir.exists():
            shutil.rmtree(image_dir)
        image_count = 0
        ocr_pages: list[int] = []
        for index in range(page_count):
            page = doc[index]
            page_no = index + 1
            text = page_text_layer(page)
            used_ocr = False
            if len(re.sub(r"\s+", "", text)) < MIN_TEXT_CHARS:
                ocr_text, used_ocr, note = try_ocr(page)
                if used_ocr and ocr_text:
                    text = ocr_text
                    ocr_pages.append(page_no)
                elif note:
                    ocr_log.append(f"{rel} page {page_no}: {note}")
            figures = extract_page_images(
                doc,
                page,
                image_dir,
                page_no,
                psi_id=psi_id,
                source=rel,
                ocr=used_ocr,
            )
            image_count += len(figures)
            tables = extract_tables(page)
            lines.append(f"[page: {page_no}]")
            lines.append(f"[ocr: {'true' if used_ocr else 'false'}]")
            lines.append("")
            if text:
                lines.append(text)
                lines.append("")
            for n, fig in enumerate(figures, start=1):
                lines.append(f"[figure: {n} | page: {page_no} | file: {fig['file']}]")
                if fig.get("caption"):
                    lines.append(caption_tag(fig["caption"]))
                lines.append("")
            for n, table_md in enumerate(tables, start=1):
                lines.append(f"[table: {n} | page: {page_no}]")
                lines.append("")
                lines.append(table_md)
                lines.append("")
        out_dir = TEXT_ROOT / dest_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{slug}.md"
        drop_flat_copy(psi_id, slug, dest_dir)
        md_rel = str(out_path.relative_to(ROOT))
        out_path.write_text(sanitize("\n".join(lines).rstrip() + "\n"), encoding="utf-8")
        write_text_sidecar(
            psi_id=psi_id,
            source=rel,
            pages=page_count,
            file=md_rel,
            ocr_pages=ocr_pages,
            figures=image_count,
            dest=out_path.with_suffix(".json"),
        )
        return {
            "ok": True,
            "pages": page_count,
            "images": image_count,
            "markdown": md_rel,
        }
    finally:
        doc.close()


def investigation_markdown(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    psi_id = psi_id_from_path(path)
    title = metadata_field(text, "Study Title") or psi_id
    objective = metadata_field(text, "Comment[Objective]")
    hardware = metadata_field(text, "Comment[Experiment Hardware]")
    acronym = metadata_field(text, "Comment[Investigation Acronym]")
    doi = metadata_field(text, "Comment[DOI]")
    approach = metadata_field(text, "Comment[Approach]")
    description = metadata_field(text, "Study Description")
    body = [
        "---",
        f"psi: {psi_id}",
        f"source: {yaml_escape(source_rel(path))}",
        "---",
        "",
        f"# {title}",
        "",
    ]
    if acronym:
        body.append(f"**Acronym:** {acronym}")
    if hardware:
        body.append(f"**Hardware:** {hardware}")
    if doi:
        body.append(f"**DOI:** {doi}")
    if acronym or hardware or doi:
        body.append("")
    if objective:
        body.extend(["## Objective", "", objective, ""])
    if approach:
        body.extend(["## Approach", "", approach, ""])
    if description:
        body.extend(["## Description", "", description, ""])
    return "\n".join(body).rstrip() + "\n"


def write_investigations() -> list[str]:
    written: list[str] = []
    for path in iter_investigations(PSI):
        psi_id = psi_id_from_path(path)
        dest_dir = document_dir(path)
        out_dir = TEXT_ROOT / dest_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "investigation.md"
        drop_flat_copy(psi_id, "investigation", dest_dir)
        dest.write_text(sanitize(investigation_markdown(path)), encoding="utf-8")
        md_rel = str(dest.relative_to(ROOT))
        write_text_sidecar(
            psi_id=psi_id,
            source=source_rel(path),
            pages=1,
            file=md_rel,
            ocr_pages=[],
            figures=0,
            dest=dest.with_suffix(".json"),
        )
        written.append(md_rel)
    return written


FIGURE_LINE_RE = re.compile(
    r"^\[figure:\s*\d+\s*\|\s*page:\s*\d+\s*\|\s*file:\s*([^\]|]+?)\s*\]\s*$"
)
CAPTION_LINE_RE = re.compile(r"^\[caption:.*\]\s*$")


def patch_markdown_captions(md_path: Path, captions: dict[str, str | None]) -> bool:
    """Insert, replace, or drop [caption:] lines for figure files in `captions`."""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    changed = False
    index = 0
    while index < len(lines):
        match = FIGURE_LINE_RE.match(lines[index].strip())
        out.append(lines[index])
        if not match:
            index += 1
            continue
        file_path = match.group(1).strip()
        if file_path not in captions:
            index += 1
            continue
        caption = captions[file_path]
        nxt = lines[index + 1].strip() if index + 1 < len(lines) else ""
        has_caption_line = bool(CAPTION_LINE_RE.match(nxt))
        if caption:
            new_line = caption_tag(caption)
            if has_caption_line:
                if lines[index + 1] != new_line:
                    changed = True
                out.append(new_line)
                index += 2
            else:
                out.append(new_line)
                changed = True
                index += 1
            continue
        if has_caption_line:
            changed = True
            index += 2
            continue
        index += 1
    if changed:
        md_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return changed


def refresh_captions(only: list[str] | None = None) -> dict:
    """Write caption fields onto existing PDF image sidecars and Markdown."""
    import pymupdf

    groups: dict[str, list[tuple[Path, dict]]] = {}
    if not IMAGE_ROOT.exists():
        return {"images": 0, "captions": 0, "markdown": 0}
    for sidecar_path in sorted(IMAGE_ROOT.rglob("*.json")):
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        source = str(data.get("source") or "")
        if not source.lower().endswith(".pdf"):
            continue
        haystack = f"{source} {sidecar_path}"
        if only and not any(token in haystack for token in only):
            continue
        groups.setdefault(source, []).append((sidecar_path, data))

    captions_by_file: dict[str, str | None] = {}
    images = 0
    written = 0
    found = 0
    missing_pdf = 0
    for source, items in groups.items():
        pdf_path = Path(source)
        if not pdf_path.is_file():
            pdf_path = ROOT / source
        if not pdf_path.is_file():
            missing_pdf += len(items)
            continue
        try:
            doc = pymupdf.open(pdf_path)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {source}: {exc}", file=sys.stderr, flush=True)
            continue
        try:
            by_page: dict[int, list[tuple[Path, dict]]] = {}
            for sidecar_path, data in items:
                by_page.setdefault(int(data.get("page") or 0), []).append((sidecar_path, data))
            for page_no, group in by_page.items():
                if page_no < 1 or page_no > doc.page_count:
                    continue
                try:
                    page_captions = captions_by_xref(doc[page_no - 1])
                except Exception as exc:  # noqa: BLE001
                    print(f"FAIL {source} page {page_no}: {exc}", file=sys.stderr, flush=True)
                    continue
                for sidecar_path, data in group:
                    images += 1
                    if data.get("stitched"):
                        file_path = str(data.get("file") or "")
                        if file_path:
                            captions_by_file[file_path] = data.get("caption")
                        continue
                    xref = int(data.get("xref") or 0)
                    hit = page_captions.get(xref)
                    caption = hit[0] if hit else None
                    position = hit[1] if hit else None
                    file_path = str(data.get("file") or "")
                    if file_path:
                        captions_by_file[file_path] = caption
                    changed = data.get("caption") != caption or data.get("caption_position") != position
                    if caption:
                        data["caption"] = caption
                        data["caption_position"] = position
                        found += 1
                    else:
                        data.pop("caption", None)
                        data.pop("caption_position", None)
                    if changed:
                        sidecar_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                        written += 1
        finally:
            doc.close()
        print(f"captions {source} ({found} so far)", flush=True)

    markdown = 0
    if TEXT_ROOT.exists() and captions_by_file:
        for text_sidecar in TEXT_ROOT.rglob("*.json"):
            try:
                meta = json.loads(text_sidecar.read_text(encoding="utf-8"))
            except Exception:
                continue
            source = str(meta.get("source") or "")
            if source not in groups:
                continue
            md_path = text_sidecar.with_suffix(".md")
            if md_path.is_file() and patch_markdown_captions(md_path, captions_by_file):
                markdown += 1
    stats = {
        "images": images,
        "with_caption": found,
        "sidecars_updated": written,
        "markdown_updated": markdown,
        "missing_pdf": missing_pdf,
    }
    print(
        f"images {images} captions {found} sidecars {written} markdown {markdown}",
        flush=True,
    )
    return stats


def _markdown_by_source() -> dict[str, Path]:
    found: dict[str, Path] = {}
    if not TEXT_ROOT.exists():
        return found
    for sidecar in TEXT_ROOT.rglob("*.json"):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        source = data.get("source")
        md_path = sidecar.with_suffix(".md")
        if source and md_path.is_file():
            found[str(source)] = md_path
    return found


def _drop_figure_lines(md_path: Path, removed: set[str]) -> int:
    lines = md_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    index = 0
    dropped = 0
    while index < len(lines):
        match = FIGURE_LINE_RE.match(lines[index].strip())
        if match and match.group(1).strip() in removed:
            dropped += 1
            index += 1
            if index < len(lines) and CAPTION_LINE_RE.match(lines[index].strip()):
                index += 1
            continue
        out.append(lines[index])
        index += 1
    if dropped:
        md_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
        sidecar = md_path.with_suffix(".json")
        if sidecar.is_file():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                meta = None
            if isinstance(meta, dict):
                meta["figures"] = sum(1 for line in out if FIGURE_LINE_RE.match(line.strip()))
                sidecar.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return dropped


def drop_text_page_scans() -> dict:
    """Delete full-page rasters of text pages that were saved as figures."""
    import pymupdf

    if not IMAGE_ROOT.exists():
        return {"removed": 0}
    groups: dict[str, list[tuple[Path, dict]]] = {}
    for sidecar_path in IMAGE_ROOT.rglob("*.json"):
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        source = str(data.get("source") or "")
        if source.lower().endswith(".pdf"):
            groups.setdefault(source, []).append((sidecar_path, data))

    removed_files: set[str] = set()
    for source, items in groups.items():
        pdf_path = Path(source)
        if not pdf_path.is_file():
            pdf_path = ROOT / source
        if not pdf_path.is_file():
            continue
        try:
            doc = pymupdf.open(pdf_path)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {source}: {exc}", file=sys.stderr, flush=True)
            continue
        try:
            by_page: dict[int, list[tuple[Path, dict]]] = {}
            for sidecar_path, data in items:
                by_page.setdefault(int(data.get("page") or 0), []).append((sidecar_path, data))
            for page_no, group in by_page.items():
                if page_no < 1 or page_no > doc.page_count:
                    continue
                page = doc[page_no - 1]
                placements = {tile["xref"]: tile["bbox"] for tile in _page_tiles(page)}
                for sidecar_path, data in group:
                    bbox = placements.get(int(data.get("xref") or 0))
                    if bbox is None or not _is_text_page_scan(page, bbox):
                        continue
                    png = sidecar_path.with_suffix(".png")
                    png.unlink(missing_ok=True)
                    sidecar_path.unlink(missing_ok=True)
                    file_path = str(data.get("file") or "")
                    if file_path:
                        removed_files.add(file_path)
        finally:
            doc.close()

    markdown = 0
    for md_path in _markdown_by_source().values():
        if _drop_figure_lines(md_path, removed_files):
            markdown += 1
    print(f"removed text-page scans {len(removed_files)} markdown {markdown}", flush=True)
    return {"removed": len(removed_files), "markdown": markdown}


def restitch_sliced_pdfs(only: list[str] | None = None) -> dict:
    """Re-extract PDFs where one figure was saved as edge-to-edge slices."""
    import pymupdf

    slugs = _markdown_by_source()
    pdfs = iter_pdfs(PSI)
    if only:
        pdfs = [path for path in pdfs if any(token in str(path) for token in only)]
    ocr_log: list[str] = []
    redone = 0
    for path in pdfs:
        rel = source_rel(path)
        try:
            doc = pymupdf.open(path)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {rel}: {exc}", file=sys.stderr, flush=True)
            continue
        sliced = False
        try:
            for page in doc:
                if any(_is_sliced_figure(cluster) for cluster in _cluster_tiles(_page_tiles(page))):
                    sliced = True
                    break
        finally:
            doc.close()
        if not sliced:
            continue
        md_path = slugs.get(rel)
        psi_id = psi_id_from_path(path)
        slug = md_path.stem if md_path is not None else file_slug(path)
        result = convert_pdf(path, psi_id, slug, ocr_log, source=rel)
        redone += 1
        print(f"restitch {rel} -> {result['markdown']} ({result['images']} img)", flush=True)
    print(f"restitched {redone}", flush=True)
    return {"pdfs": redone}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest PSI PDFs into tagged Markdown.")
    parser.add_argument("--limit", type=int, default=0, help="Convert at most N PDFs (0 = all).")
    parser.add_argument("--only", action="append", default=[], help="Substring filter on PDF path.")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Do not reconvert PDFs that already have a Markdown file.",
    )
    parser.add_argument(
        "--captions-only",
        action="store_true",
        help="Fill caption metadata on existing PDF images. Do not rewrite image files.",
    )
    parser.add_argument(
        "--drop-text-scans",
        action="store_true",
        help="Remove full-page rasters of text pages that were saved as figures.",
    )
    parser.add_argument(
        "--restitch",
        action="store_true",
        help="Re-extract PDFs whose figures were saved as adjacent slices.",
    )
    args = parser.parse_args()
    if args.captions_only:
        refresh_captions(args.only or None)
        return 0
    if args.drop_text_scans:
        drop_text_page_scans()
        return 0
    if args.restitch:
        restitch_sliced_pdfs(args.only or None)
        return 0

    TEXT_ROOT.mkdir(parents=True, exist_ok=True)
    IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    investigations = write_investigations()
    pdfs = iter_pdfs(PSI)
    if args.only:
        pdfs = [path for path in pdfs if any(token in str(path) for token in args.only)]
    if args.limit:
        pdfs = pdfs[: args.limit]

    used_slugs: dict[str, Path] = {}
    succeeded: list[dict] = []
    failed: list[dict] = []
    ocr_log: list[str] = []

    for path in pdfs:
        psi_id = psi_id_from_path(path)
        slug = unique_slug(psi_id, path, used_slugs)
        dest_dir = document_dir(path)
        out_path = TEXT_ROOT / dest_dir / f"{slug}.md"
        if args.skip_existing and out_path.is_file():
            image_dir = IMAGE_ROOT / dest_dir / slug
            image_count = len(list(image_dir.glob("*.png"))) if image_dir.is_dir() else 0
            succeeded.append(
                {
                    "pdf": source_rel(path),
                    "slug": slug,
                    "ok": True,
                    "skipped": True,
                    "images": image_count,
                    "markdown": str(out_path.relative_to(ROOT)),
                }
            )
            print(f"skip {source_rel(path)} -> {out_path.relative_to(ROOT)}", flush=True)
            continue
        try:
            result = convert_pdf(path, psi_id, slug, ocr_log)
            succeeded.append({"pdf": source_rel(path), "slug": slug, **result})
            print(
                f"ok  {source_rel(path)} -> {result['markdown']} ({result['pages']}p, {result['images']} img)",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            failed.append({"pdf": source_rel(path), "error": str(exc), "trace": traceback.format_exc()})
            print(f"FAIL {source_rel(path)}: {exc}", file=sys.stderr, flush=True)

    summary = {
        "pdfs_found": len(iter_pdfs(PSI)),
        "pdfs_attempted": len(pdfs),
        "pdfs_succeeded": len(succeeded),
        "pdfs_failed": len(failed),
        "investigations": len(investigations),
        "tesseract": TESSERACT_BIN or None,
        "ocr_notes": ocr_log,
        "succeeded": succeeded,
        "failed": failed,
    }
    markdown_files = sorted(
        str(path.relative_to(ROOT))
        for path in TEXT_ROOT.rglob("*.md")
        if path.name != "README.md"
    )
    image_files = list(IMAGE_ROOT.rglob("*.png")) if IMAGE_ROOT.exists() else []
    summary["markdown_files"] = len(markdown_files)
    summary["image_files"] = len(image_files)
    (LOG_DIR / "ingest_pdf.json").write_text(json.dumps(summary, indent=2) + "\n")
    log_lines = [
        f"investigations {len(investigations)}",
        f"pdfs ok {len(succeeded)} fail {len(failed)}",
        f"markdown {len(markdown_files)} images {len(image_files)}",
    ]
    log_lines.extend(ocr_log)
    for item in failed:
        log_lines.append(f"FAIL {item['pdf']}: {item['error']}")
    (LOG_DIR / "ingest_pdf.log").write_text("\n".join(log_lines) + "\n")
    print(f"investigations {len(investigations)}")
    print(f"pdfs ok {len(succeeded)} fail {len(failed)}")
    print(f"log {LOG_DIR / 'ingest_pdf.json'}")
    return 1 if failed and not succeeded else 0


if __name__ == "__main__":
    raise SystemExit(main())
