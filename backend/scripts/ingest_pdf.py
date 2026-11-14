#!/usr/bin/env python3
"""Convert PSI PDFs to tagged Markdown plus extracted images.

Writes:
  data/text/{PSI-id}/{slug}.md
  data/images/{PSI-id}/{slug}/p{page:02d}-f{n:02d}.png
  data/text/{PSI-id}/investigation.md  (from i_Investigation.txt)

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


def should_keep_image(pix, raw_bytes: int | None = None) -> bool:
    if pix is None:
        return False
    if pix.width < MIN_IMAGE_EDGE or pix.height < MIN_IMAGE_EDGE:
        return False
    size = raw_bytes if raw_bytes is not None else len(pix.tobytes())
    if size < MIN_IMAGE_BYTES and max(pix.width, pix.height) < 120:
        return False
    return True


def extract_page_images(
    doc,
    page,
    image_dir: Path,
    page_no: int,
    *,
    psi_id: str,
    source: str,
    ocr: bool,
) -> list[str]:
    image_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    seen: set[int] = set()
    figure = 0
    for img in page.get_images(full=True):
        xref = img[0]
        if xref in seen or xref <= 0:
            continue
        seen.add(xref)
        width, height = img[2], img[3]
        if width and height and (width < MIN_IMAGE_EDGE or height < MIN_IMAGE_EDGE):
            continue
        pix, extra = native_pixmap(doc, xref)
        if not should_keep_image(pix):
            continue
        figure += 1
        stem = f"p{page_no:02d}-f{figure:02d}"
        dest = image_dir / f"{stem}.png"
        try:
            dest.write_bytes(pix.tobytes("png"))
        except Exception:
            try:
                pix.save(str(dest))
            except Exception:
                figure -= 1
                continue
        if not dest.is_file():
            figure -= 1
            continue
        if dest.stat().st_size < MIN_IMAGE_BYTES and max(pix.width, pix.height) < 160:
            dest.unlink(missing_ok=True)
            figure -= 1
            continue
        rel = str(dest.relative_to(ROOT))
        colorspace = extra.get("colorspace")
        if not colorspace:
            colorspace = str(pix.colorspace) if getattr(pix, "colorspace", None) else None
        pdf_filter = extra.get("pdf_filter") or (img[8] if len(img) > 8 else None)
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
            "xref": extra.get("xref", xref),
            "colorspace": colorspace,
            "pdf_filter": pdf_filter,
        }
        (image_dir / f"{stem}.json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
        saved.append(rel)
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
        image_dir = IMAGE_ROOT / psi_id / slug
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
            for n, file_path in enumerate(figures, start=1):
                lines.append(f"[figure: {n} | page: {page_no} | file: {file_path}]")
                lines.append("")
            for n, table_md in enumerate(tables, start=1):
                lines.append(f"[table: {n} | page: {page_no}]")
                lines.append("")
                lines.append(table_md)
                lines.append("")
        out_dir = TEXT_ROOT / psi_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{slug}.md"
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
        out_dir = TEXT_ROOT / psi_id
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "investigation.md"
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest PSI PDFs into tagged Markdown.")
    parser.add_argument("--limit", type=int, default=0, help="Convert at most N PDFs (0 = all).")
    parser.add_argument("--only", action="append", default=[], help="Substring filter on PDF path.")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Do not reconvert PDFs that already have a Markdown file.",
    )
    args = parser.parse_args()

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
        out_path = TEXT_ROOT / psi_id / f"{slug}.md"
        if args.skip_existing and out_path.is_file():
            image_dir = IMAGE_ROOT / psi_id / slug
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
