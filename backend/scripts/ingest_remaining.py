#!/usr/bin/env python3
"""Convert remaining PSI office/zip/PDF files using the PDF ingest pipeline.

Does not modify data/psi originals. Skips existing successful conversions
matched by sidecar `source`. Leaves prior PDF markdown/images in place.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from ingest_pdf import (  # noqa: E402
    IMAGE_ROOT,
    LOG_DIR,
    MIN_IMAGE_BYTES,
    MIN_IMAGE_EDGE,
    PSI,
    SKIP_DIR_NAMES,
    TEXT_ROOT,
    convert_pdf,
    caption_tag,
    document_dir,
    drop_flat_copy,
    psi_id_from_path,
    sanitize,
    source_rel,
    unique_slug,
    write_text_sidecar,
    yaml_escape,
)

HUGE_XLSX_BYTES = 1_000_000
PREVIEW_ROWS = 20
PREVIEW_COLS = 12
CELL_CHARS = 80
DOC_EXTS = {".pdf", ".ppt", ".pptx", ".doc", ".docx"}
OFFICE_EXTS = {".ppt", ".pptx", ".doc", ".docx"}
PHOTO_EXTS = {".jpg", ".jpeg", ".jpe", ".png"}
SKIP_MEMBER_PREFIX = ("__macosx",)
BINARY_ZIP_EXTS = {".hobj", ".mdipsug", ".next", ".jpg", ".jpe", ".jpeg", ".png", ".db"}
SKIP_LOOSE_EXTS = {".dat", ".pxp", ".csv"}


def skip_name(path: Path) -> bool:
    if path.name.startswith("._") or path.name in {".DS_Store", ".gitkeep"}:
        return True
    return any(part.lower() in SKIP_DIR_NAMES or part.lower() == "__macosx" for part in path.parts)


def walk_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or skip_name(path):
            continue
        found.append(path)
    return sorted(found)


def find_soffice() -> str | None:
    for candidate in (
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/opt/homebrew/bin/soffice",
        "/usr/local/bin/soffice",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def load_existing() -> tuple[dict[str, Path], set[str]]:
    used: dict[str, Path] = {}
    sources: set[str] = set()
    if not TEXT_ROOT.exists():
        return used, sources
    for sidecar in TEXT_ROOT.rglob("*.json"):
        if skip_name(sidecar):
            continue
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        src = data.get("source")
        if src:
            sources.add(src)
        md = sidecar.with_suffix(".md")
        psi_id = str(data.get("psi") or sidecar.parent.name)
        used[f"{psi_id}/{sidecar.stem}"] = md if md.exists() else sidecar
    return used, sources


def blob_to_png(blob: bytes, dest: Path) -> tuple[int, int] | None:
    import pymupdf
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        pix = pymupdf.Pixmap(blob)
        if pix.width < MIN_IMAGE_EDGE or pix.height < MIN_IMAGE_EDGE:
            return None
        if pix.n - pix.alpha > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        dest.write_bytes(pix.tobytes("png"))
        if dest.stat().st_size < MIN_IMAGE_BYTES and max(pix.width, pix.height) < 160:
            dest.unlink(missing_ok=True)
            return None
        return pix.width, pix.height
    except Exception:
        pass
    try:
        image = Image.open(BytesIO(blob))
        image.load()
        width, height = image.size
        if width < MIN_IMAGE_EDGE or height < MIN_IMAGE_EDGE:
            return None
        if image.mode in {"RGBA", "LA", "P"}:
            converted = image.convert("RGBA")
        else:
            converted = image.convert("RGB")
        converted.save(dest, format="PNG")
        if dest.stat().st_size < MIN_IMAGE_BYTES and max(width, height) < 160:
            dest.unlink(missing_ok=True)
            return None
        return width, height
    except Exception:
        dest.unlink(missing_ok=True)
        return None


def write_page_images(
    *,
    blobs: list[bytes],
    image_dir: Path,
    page_no: int,
    psi_id: str,
    source: str,
) -> list[str]:
    if image_dir.exists() and page_no == 1:
        shutil.rmtree(image_dir)
    image_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    figure = 0
    for blob in blobs:
        size = blob_to_png(blob, image_dir / f"p{page_no:02d}-f{figure + 1:02d}.png")
        if not size:
            continue
        figure += 1
        stem = f"p{page_no:02d}-f{figure:02d}"
        dest = image_dir / f"{stem}.png"
        rel = str(dest.relative_to(ROOT))
        sidecar = {
            "psi": psi_id,
            "source": source,
            "page": page_no,
            "figure": figure,
            "file": rel,
            "width": size[0],
            "height": size[1],
            "ocr": False,
            "cropped": False,
        }
        (image_dir / f"{stem}.json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
        saved.append(rel)
    return saved


def finish_markdown(
    *,
    psi_id: str,
    source: str,
    slug: str,
    pages: int,
    lines: list[str],
    image_count: int,
) -> dict:
    src_path = Path(source)
    if not src_path.is_absolute():
        src_path = ROOT / src_path
    dest_dir = document_dir(src_path)
    out_dir = TEXT_ROOT / dest_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slug}.md"
    drop_flat_copy(psi_id, slug, dest_dir)
    md_rel = str(out_path.relative_to(ROOT))
    out_path.write_text(sanitize("\n".join(lines).rstrip() + "\n"), encoding="utf-8")
    write_text_sidecar(
        psi_id=psi_id,
        source=source,
        pages=pages,
        file=md_rel,
        ocr_pages=[],
        figures=image_count,
        dest=out_path.with_suffix(".json"),
    )
    return {"ok": True, "pages": pages, "images": image_count, "markdown": md_rel}


def ingest_loose_photos(used_slugs: dict[str, Path], existing_sources: set[str]) -> list[dict]:
    """Copy standalone experiment photos into the same section folder as the originals."""
    groups: dict[Path, list[Path]] = {}
    for path in walk_files(PSI):
        if path.suffix.lower() not in PHOTO_EXTS:
            continue
        groups.setdefault(path.parent, []).append(path)
    saved: list[dict] = []
    for folder, photos in sorted(groups.items()):
        rel_dir = source_rel(folder)
        if rel_dir in existing_sources:
            print(f"skip {rel_dir}: already converted", flush=True)
            continue
        photos = sorted(photos, key=lambda item: item.name.lower())
        psi_id = psi_id_from_path(folder)
        slug = unique_slug(psi_id, folder, used_slugs)
        src_path = folder if folder.is_absolute() else ROOT / folder
        image_dir = IMAGE_ROOT / document_dir(src_path) / slug
        if image_dir.exists():
            shutil.rmtree(image_dir)
        image_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            f"psi: {psi_id}",
            f"source: {yaml_escape(rel_dir)}",
            "pages: 1",
            "---",
            "",
            "[page: 1]",
            "[ocr: false]",
            "",
        ]
        figure = 0
        for photo in photos:
            figure += 1
            stem = f"p01-f{figure:02d}"
            dest = image_dir / f"{stem}.png"
            size = blob_to_png(photo.read_bytes(), dest)
            if not size:
                figure -= 1
                continue
            rel = str(dest.relative_to(ROOT))
            sidecar = {
                "psi": psi_id,
                "source": source_rel(photo),
                "page": 1,
                "figure": figure,
                "file": rel,
                "width": size[0],
                "height": size[1],
                "ocr": False,
                "cropped": False,
                "caption": photo.stem,
                "caption_position": "filename",
            }
            (image_dir / f"{stem}.json").write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
            lines.append(f"[figure: {figure} | page: 1 | file: {rel}]")
            lines.append(caption_tag(photo.stem))
            lines.append("")
        if figure == 0:
            shutil.rmtree(image_dir, ignore_errors=True)
            continue
        result = finish_markdown(
            psi_id=psi_id,
            source=rel_dir,
            slug=slug,
            pages=1,
            lines=lines,
            image_count=figure,
        )
        existing_sources.add(rel_dir)
        result.update({"kind": "photos", "source": rel_dir, "slug": slug})
        saved.append(result)
        print(f"ok  {rel_dir} -> {result['markdown']} (1p, {figure} img)", flush=True)
    return saved


def convert_pptx_native(path: Path, psi_id: str, slug: str, source: str) -> dict:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    def iter_shapes(shapes):
        for shape in shapes:
            yield shape
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from iter_shapes(shape.shapes)

    prs = Presentation(str(path))
    src_path = Path(source) if Path(source).is_absolute() else ROOT / source
    image_dir = IMAGE_ROOT / document_dir(src_path) / slug
    if image_dir.exists():
        shutil.rmtree(image_dir)
    lines = [
        "---",
        f"psi: {psi_id}",
        f"source: {yaml_escape(source)}",
        f"pages: {len(prs.slides)}",
        "---",
        "",
    ]
    image_count = 0
    for index, slide in enumerate(prs.slides, start=1):
        texts: list[str] = []
        blobs: list[bytes] = []
        for shape in iter_shapes(slide.shapes):
            if getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text.strip()
                if text:
                    texts.append(text)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                blobs.append(shape.image.blob)
        figures = write_page_images(
            blobs=blobs,
            image_dir=image_dir,
            page_no=index,
            psi_id=psi_id,
            source=source,
        )
        image_count += len(figures)
        lines.append(f"[page: {index}]")
        lines.append("[ocr: false]")
        lines.append("")
        if texts:
            lines.append(sanitize("\n\n".join(texts)))
            lines.append("")
        for n, file_path in enumerate(figures, start=1):
            lines.append(f"[figure: {n} | page: {index} | file: {file_path}]")
            lines.append("")
    return finish_markdown(
        psi_id=psi_id,
        source=source,
        slug=slug,
        pages=len(prs.slides),
        lines=lines,
        image_count=image_count,
    )


def convert_docx_native(path: Path, psi_id: str, slug: str, source: str) -> dict:
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(path))
    blobs: list[bytes] = []
    for rel in doc.part.rels.values():
        if "image" in str(rel.reltype):
            blobs.append(rel.target_part.blob)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [sanitize(cell.text.strip())[:CELL_CHARS] for cell in row.cells]
            if any(cells):
                paragraphs.append(" | ".join(cells))
    # Keep document.xml drawings that python-docx missed.
    for blip in doc.element.findall(".//" + qn("a:blip")):
        embed = blip.get(qn("r:embed"))
        if not embed:
            continue
        try:
            part = doc.part.related_parts[embed]
            blob = part.blob
        except Exception:
            continue
        if blob not in blobs:
            blobs.append(blob)

    src_path = Path(source) if Path(source).is_absolute() else ROOT / source
    image_dir = IMAGE_ROOT / document_dir(src_path) / slug
    figures = write_page_images(
        blobs=blobs,
        image_dir=image_dir,
        page_no=1,
        psi_id=psi_id,
        source=source,
    )
    lines = [
        "---",
        f"psi: {psi_id}",
        f"source: {yaml_escape(source)}",
        "pages: 1",
        "---",
        "",
        "[page: 1]",
        "[ocr: false]",
        "",
    ]
    if paragraphs:
        lines.append(sanitize("\n\n".join(paragraphs)))
        lines.append("")
    for n, file_path in enumerate(figures, start=1):
        lines.append(f"[figure: {n} | page: 1 | file: {file_path}]")
        lines.append("")
    return finish_markdown(
        psi_id=psi_id,
        source=source,
        slug=slug,
        pages=1,
        lines=lines,
        image_count=len(figures),
    )


def office_to_pdf(src: Path, outdir: Path, soffice: str) -> Path:
    profile = outdir / "lo-profile"
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        soffice,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nologo",
        f"-env:UserInstallation=file://{profile}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(outdir),
        str(src),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=900, check=False)
    pdfs = sorted(outdir.glob("*.pdf"))
    if result.returncode != 0 or not pdfs:
        err = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"LibreOffice convert failed ({result.returncode}): {err}")
    return pdfs[0]


def convert_office(
    path: Path,
    psi_id: str,
    slug: str,
    source: str,
    ocr_log: list[str],
    soffice: str | None,
) -> dict:
    suffix = path.suffix.lower()
    if soffice:
        with tempfile.TemporaryDirectory(prefix="psi-office-") as tmp:
            tmp_dir = Path(tmp)
            work = tmp_dir / path.name
            shutil.copy2(path, work)
            pdf_path = office_to_pdf(work, tmp_dir, soffice)
            return convert_pdf(pdf_path, psi_id, slug, ocr_log, source=source)
    if suffix == ".pptx":
        return convert_pptx_native(path, psi_id, slug, source)
    if suffix == ".docx":
        return convert_docx_native(path, psi_id, slug, source)
    raise RuntimeError(f"LibreOffice not available; skipped legacy office file ({suffix})")


def md_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    norm = [row + [""] * (width - len(row)) for row in rows]
    header = norm[0]
    body = norm[1:] or [[" "] * width]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def cell_text(value) -> str:
    if value is None:
        return ""
    return sanitize(str(value).replace("\n", " "))[:CELL_CHARS]


def convert_xlsx_summary(path: Path, psi_id: str, slug: str, source: str, huge: bool) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet_names = list(wb.sheetnames)
        lines = [
            "---",
            f"psi: {psi_id}",
            f"source: {yaml_escape(source)}",
            "pages: 1",
            "---",
            "",
            "[page: 1]",
            "[ocr: false]",
            "",
            f"# {path.name}",
            "",
            f"- sheets: {len(sheet_names)}",
            f"- size_bytes: {path.stat().st_size}",
        ]
        if huge:
            lines.extend(
                [
                    "- dump: skipped",
                    "- reason: huge analyzed-data spreadsheet (header-only)",
                    "",
                    "## Sheets",
                    "",
                ]
            )
            for name in sheet_names:
                lines.append(f"- {name}")
            lines.append("")
        else:
            lines.extend(["- dump: compact preview", "", "## Sheets", ""])
            for name in sheet_names:
                ws = wb[name]
                lines.append(f"### {name}")
                lines.append("")
                if not hasattr(ws, "iter_rows"):
                    lines.append("_chart sheet (no cell dump)_")
                    lines.append("")
                    continue
                preview: list[list[str]] = []
                for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    if i > PREVIEW_ROWS:
                        break
                    values = [cell_text(v) for v in (row or ())[:PREVIEW_COLS]]
                    if any(values):
                        preview.append(values)
                if preview:
                    lines.append(md_table(preview))
                    lines.append("")
                else:
                    lines.append("_empty sheet_")
                    lines.append("")
    finally:
        wb.close()
    return finish_markdown(
        psi_id=psi_id,
        source=source,
        slug=slug,
        pages=1,
        lines=lines,
        image_count=0,
    )


def convert_xls_header(path: Path, psi_id: str, slug: str, source: str) -> dict:
    lines = [
        "---",
        f"psi: {psi_id}",
        f"source: {yaml_escape(source)}",
        "pages: 1",
        "---",
        "",
        "[page: 1]",
        "[ocr: false]",
        "",
        f"# {path.name}",
        "",
        f"- size_bytes: {path.stat().st_size}",
        "- dump: skipped",
        "- reason: huge Analyzed Data .xls (SAME/DAFT run tables; header-only)",
        "",
    ]
    return finish_markdown(
        psi_id=psi_id,
        source=source,
        slug=slug,
        pages=1,
        lines=lines,
        image_count=0,
    )


def extract_zip_members(zpath: Path, tmp: Path) -> list[tuple[Path, str]]:
    extracted: list[tuple[Path, str]] = []
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or name.startswith("._"):
                continue
            lowered = name.lower()
            if lowered.startswith(SKIP_MEMBER_PREFIX) or "/._" in lowered:
                continue
            suffix = Path(name).suffix.lower()
            if suffix not in DOC_EXTS | {".xlsx", ".xls"}:
                continue
            dest = tmp / Path(name).name
            n = 2
            while dest.exists():
                dest = tmp / f"{Path(name).stem}-{n}{Path(name).suffix}"
                n += 1
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
            member_source = f"{source_rel(zpath)}/{name}"
            extracted.append((dest, member_source))
    return extracted


def zip_skip_reason(zpath: Path) -> str | None:
    parent = zpath.parent.name
    try:
        with zipfile.ZipFile(zpath) as zf:
            suffixes = []
            for info in zf.infolist():
                if info.is_dir() or Path(info.filename).name.startswith("._"):
                    continue
                if info.filename.lower().startswith(SKIP_MEMBER_PREFIX):
                    continue
                suffixes.append(Path(info.filename).suffix.lower() or "(none)")
    except zipfile.BadZipFile as exc:
        return f"unreadable zip: {exc}"
    if not suffixes:
        return "empty zip"
    if parent == "Analyzed Data" and all(ext in BINARY_ZIP_EXTS | {".hobj", ".mdipsug", ".next"} for ext in suffixes):
        return "Analyzed Data zip (CIR/ACME raw binary .hobj/.next; skipped)"
    if all(ext in BINARY_ZIP_EXTS | {"(none)"} for ext in suffixes):
        return "image/binary zip (jpg/ops photos; skipped document pipeline)"
    return None


def convert_one(
    path: Path,
    source: str,
    used_slugs: dict[str, Path],
    existing_sources: set[str],
    ocr_log: list[str],
    soffice: str | None,
    kind: str,
) -> dict:
    if source in existing_sources:
        return {"ok": True, "skipped": True, "reason": "already converted", "source": source}
    psi_id = psi_id_from_path(Path(source))
    if psi_id == "unknown":
        psi_id = psi_id_from_path(path)
    slug_path = Path(source)
    slug = unique_slug(psi_id, slug_path, used_slugs)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        header = path.read_bytes()[:8]
        if not header.startswith(b"%PDF"):
            return {
                "ok": True,
                "skipped": True,
                "reason": f"not a valid PDF (magic={header!r})",
                "source": source,
            }
        result = convert_pdf(path, psi_id, slug, ocr_log, source=source)
    elif suffix in OFFICE_EXTS:
        if not soffice and suffix in {".ppt", ".doc"}:
            return {
                "ok": True,
                "skipped": True,
                "reason": "LibreOffice missing for legacy .ppt/.doc",
                "source": source,
            }
        result = convert_office(path, psi_id, slug, source, ocr_log, soffice)
    elif suffix == ".xlsx":
        huge = path.stat().st_size >= HUGE_XLSX_BYTES or "MergeResult" in path.name
        result = convert_xlsx_summary(path, psi_id, slug, source, huge=huge)
        result["xlsx_mode"] = "header-only" if huge else "compact"
    elif suffix == ".xls":
        result = convert_xls_header(path, psi_id, slug, source)
        result["xlsx_mode"] = "header-only"
    else:
        raise RuntimeError(f"unsupported {suffix}")
    existing_sources.add(source)
    result.update({"kind": kind, "source": source, "slug": slug})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest remaining PSI office/zip documents.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--photos-only",
        action="store_true",
        help="Ingest standalone JPG/PNG experiment photos and stop.",
    )
    args = parser.parse_args()

    TEXT_ROOT.mkdir(parents=True, exist_ok=True)
    IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    used_slugs, existing_sources = load_existing()
    soffice = find_soffice()
    ocr_log: list[str] = []
    succeeded: list[dict] = []
    failed: list[dict] = []
    skipped: list[dict] = []

    before_md = len(list(TEXT_ROOT.rglob("*.md"))) if TEXT_ROOT.exists() else 0
    before_png = len(list(IMAGE_ROOT.rglob("*.png"))) if IMAGE_ROOT.exists() else 0
    before_json = len(list(TEXT_ROOT.rglob("*.json"))) + (
        len(list(IMAGE_ROOT.rglob("*.json"))) if IMAGE_ROOT.exists() else 0
    )

    def record_skip(source: str, reason: str, extra: dict | None = None) -> None:
        item = {"source": source, "reason": reason}
        if extra:
            item.update(extra)
        skipped.append(item)
        print(f"skip {source}: {reason}", flush=True)

    def maybe_stop() -> bool:
        return bool(args.limit) and (len(succeeded) + len(failed)) >= args.limit

    for result in ingest_loose_photos(used_slugs, existing_sources):
        succeeded.append(result)
    if args.photos_only:
        print(f"photos {len(succeeded)}", flush=True)
        return 0

    # Loose leftover PDFs and office files.
    for path in walk_files(PSI):
        if maybe_stop():
            break
        suffix = path.suffix.lower()
        rel = source_rel(path)
        if suffix in SKIP_LOOSE_EXTS:
            record_skip(rel, f"numeric/table dump ({suffix}); already in catalog or not document-like")
            continue
        if suffix == ".txt":
            if path.name.startswith("s_PSI-"):
                record_skip(rel, 's_PSI-*.txt "Do not use this" metadata')
            elif path.name == "i_Investigation.txt":
                record_skip(rel, "investigation.md already produced")
            else:
                record_skip(rel, "non-document txt")
            continue
        if suffix == ".md":
            record_skip(rel, "psi README, not a study document")
            continue
        if suffix == ".zip":
            continue
        if suffix not in DOC_EXTS | {".xlsx", ".xls"}:
            continue
        if rel in existing_sources:
            record_skip(rel, "already converted")
            continue
        try:
            result = convert_one(path, rel, used_slugs, existing_sources, ocr_log, soffice, kind=suffix.lstrip("."))
            if result.get("skipped"):
                skipped.append(result)
                print(f"skip {rel}: {result.get('reason')}", flush=True)
                continue
            succeeded.append(result)
            print(
                f"ok  {rel} -> {result['markdown']} ({result.get('pages')}p, {result.get('images')} img)",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            failed.append({"source": rel, "error": str(exc), "trace": traceback.format_exc()})
            print(f"FAIL {rel}: {exc}", file=sys.stderr, flush=True)

    # Zips: convert document-like members.
    for path in walk_files(PSI):
        if maybe_stop():
            break
        if path.suffix.lower() != ".zip":
            continue
        rel = source_rel(path)
        reason = zip_skip_reason(path)
        if reason:
            record_skip(rel, reason)
            continue
        with tempfile.TemporaryDirectory(prefix="psi-zip-") as tmp:
            members = extract_zip_members(path, Path(tmp))
            if not members:
                record_skip(rel, "no document-like members")
                continue
            for member_path, member_source in members:
                if maybe_stop():
                    break
                suffix = member_path.suffix.lower()
                if suffix in {".xls", ".xlsx"} and "Analyzed Data" in str(path):
                    # Should not happen often; still treat as spreadsheet.
                    pass
                if member_source in existing_sources:
                    record_skip(member_source, "already converted")
                    continue
                try:
                    result = convert_one(
                        member_path,
                        member_source,
                        used_slugs,
                        existing_sources,
                        ocr_log,
                        soffice,
                        kind=f"zip:{suffix.lstrip('.')}",
                    )
                    if result.get("skipped"):
                        skipped.append(result)
                        continue
                    succeeded.append(result)
                    print(
                        f"ok  {member_source} -> {result['markdown']} "
                        f"({result.get('pages')}p, {result.get('images')} img)",
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    failed.append(
                        {"source": member_source, "error": str(exc), "trace": traceback.format_exc()}
                    )
                    print(f"FAIL {member_source}: {exc}", file=sys.stderr, flush=True)

    after_md = len(list(TEXT_ROOT.rglob("*.md"))) if TEXT_ROOT.exists() else 0
    after_png = len(list(IMAGE_ROOT.rglob("*.png"))) if IMAGE_ROOT.exists() else 0
    after_json = len(list(TEXT_ROOT.rglob("*.json"))) + (
        len(list(IMAGE_ROOT.rglob("*.json"))) if IMAGE_ROOT.exists() else 0
    )
    summary = {
        "soffice": soffice,
        "succeeded": len(succeeded),
        "failed": len(failed),
        "skipped": len(skipped),
        "added_markdown": after_md - before_md,
        "added_png": after_png - before_png,
        "added_json": after_json - before_json,
        "markdown_files": after_md,
        "image_files": after_png,
        "ocr_notes": ocr_log,
        "ok": succeeded,
        "fail": failed,
        "skip": skipped,
    }
    (LOG_DIR / "ingest_remaining.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    log_lines = [
        f"soffice {soffice or 'missing'}",
        f"ok {len(succeeded)} fail {len(failed)} skip {len(skipped)}",
        f"added md {after_md - before_md} png {after_png - before_png} json {after_json - before_json}",
    ]
    for item in failed:
        log_lines.append(f"FAIL {item['source']}: {item['error']}")
    (LOG_DIR / "ingest_remaining.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    print(log_lines[0])
    print(log_lines[1])
    print(log_lines[2])
    print(f"log {LOG_DIR / 'ingest_remaining.json'}")
    return 1 if failed and not succeeded else 0


if __name__ == "__main__":
    raise SystemExit(main())
