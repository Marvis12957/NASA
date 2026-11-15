#!/usr/bin/env python3
"""Crawl NASA combustion-science sources into Markdown.

Input: a list of Markdown links ``[Title](url)``, one per line
(default: backend/scripts/crawl_sources.txt).

For every source:
  HTML page -> data/crawl/{slug}/index.md   (main article only, nav stripped)
  PDF       -> data/crawl/{slug}/index.md   (text per page) + source.pdf
  Images    -> data/crawl/{slug}/images/img-NNN.{ext}
               data/crawl/{slug}/images/img-NNN.md   <- image converted to Markdown
               (metadata, embed, caption/alt, OCR text; optional LLM transcription)
  PDF pages with no text layer are rendered to an image and handled the same way.

A run summary goes to data/crawl/manifest.json. Sources whose index.md already
exists are skipped unless --force.

Usage (on the server):
  ~/venvs/nasa-crawl/bin/python backend/scripts/crawl_sources.py
  ~/venvs/nasa-crawl/bin/python backend/scripts/crawl_sources.py --only sofie flex --force
  ~/venvs/nasa-crawl/bin/python backend/scripts/crawl_sources.py --vision   # LLM image->Markdown

Requires: requests beautifulsoup4 lxml markdownify pymupdf pymupdf4llm pillow,
and the `tesseract` binary for OCR (optional; skipped with a note if missing).
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCES = Path(__file__).with_name("crawl_sources.txt")
DEFAULT_OUT = ROOT / "data" / "crawl"
USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) FlameInFreefall-crawler/1.0"
MIN_IMAGE_EDGE = 80          # skip icons / spacers smaller than this (px)
MIN_PAGE_TEXT_CHARS = 40     # PDF page below this is treated as an image page
OCR_MIN_CONF = 75            # tesseract word confidence (0-100) to keep a word
TESSERACT = shutil.which("tesseract")
LINK_RE = re.compile(r"\[([^\]]+)\]\((\S+?)\)")
DROP_SELECTORS = [
    "script", "style", "noscript", "svg", "form", "button", "iframe",
    "nav", ".usa-breadcrumb", "[class*=secondary-navigation]",
    "[class*=social]", "[class*=share]", ".hds-a11y-heading-22",
]


# ---------------------------------------------------------------- helpers

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_url(url: str) -> str:
    """Drop utm_* tracking params, keep everything else."""
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunparse(parts._replace(query=urlencode(query)))


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text[:60] or "source"


def source_slug(title: str) -> str:
    """'Flame Extinguishment Experiment (FLEX)' -> 'flex'."""
    m = re.search(r"\(([^)]+)\)\s*$", title)
    return slugify(m.group(1) if m else title)


def yaml_value(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def frontmatter(fields: dict) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        lines.append(f"{key}: {yaml_value(value)}")
    lines.append("---\n")
    return "\n".join(lines)


def tidy_markdown(md: str) -> str:
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


def parse_sources(path: Path) -> list[dict]:
    sources, seen = [], set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = LINK_RE.search(line)
        if not m:
            continue
        title, url = m.group(1).strip(), clean_url(m.group(2).strip())
        slug = source_slug(title)
        base, n = slug, 2
        while slug in seen:
            slug, n = f"{base}-{n}", n + 1
        seen.add(slug)
        sources.append({"title": title, "url": url, "slug": slug})
    return sources


class Fetcher:
    def __init__(self, delay: float, timeout: int = 60):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.delay = delay
        self.timeout = timeout
        self._last = 0.0

    def get(self, url: str, attempts: int = 3) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < attempts:
                    time.sleep(2 * attempt)
        raise RuntimeError(f"GET {url} failed: {last_exc}")


# ---------------------------------------------------------------- image -> markdown

def ocr_image(data: bytes) -> tuple[str, str | None]:
    """Return (text, note). Converts any format to PNG for tesseract."""
    if not TESSERACT:
        return "", "tesseract not installed; OCR skipped"
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        if max(img.size) < 1000:  # upscale small images: tesseract likes ~300 dpi text
            scale = 1000 / max(img.size)
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            img.save(tmp.name)
            out = subprocess.run(
                [TESSERACT, tmp.name, "stdout", "-l", "eng", "--psm", "3", "tsv"],
                capture_output=True, text=True, timeout=120,
            )
        # Rebuild text from confident words only; photos/logos otherwise yield junk.
        lines: dict[tuple, list[str]] = {}
        confident = 0
        for row in out.stdout.splitlines()[1:]:
            cols = row.split("\t")
            if len(cols) < 12 or not cols[11].strip():
                continue
            try:
                conf = float(cols[10])
            except ValueError:
                continue
            if conf < OCR_MIN_CONF:
                continue
            word = cols[11].strip()
            if re.search(r"[A-Za-z0-9]{2,}", word):
                confident += 1
            lines.setdefault((cols[1], cols[2], cols[3], cols[4]), []).append(word)
        if confident < 3:
            return "", "no readable text detected"
        return "\n".join(" ".join(ws) for ws in lines.values()), None
    except Exception as exc:  # noqa: BLE001
        return "", f"OCR failed: {exc}"


def _env_from_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def vision_markdown(data: bytes, mime: str, context: str) -> tuple[str, str | None]:
    """Ask the project's configured Anthropic endpoint to transcribe the image."""
    _env_from_dotenv()
    key = (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    if not key:
        return "", "no ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN; vision skipped"
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    url = base if base.endswith("/messages") else (f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages")
    model = (os.environ.get("CRAWL_VISION_MODEL") or os.environ.get("ANTHROPIC_MODEL") or "claude-opus-5-5").strip()
    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        buf = io.BytesIO()
        Image.open(io.BytesIO(data)).convert("RGB").save(buf, "PNG")
        data, mime = buf.getvalue(), "image/png"
    payload = {
        "model": model,
        "max_tokens": 2048,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime,
                                             "data": base64.b64encode(data).decode()}},
                {"type": "text", "text": (
                    "Convert this image from a NASA combustion-science document into Markdown. "
                    "Transcribe all visible text exactly. Render tables as Markdown tables. "
                    "For charts/plots give axes, units, series and the key trend. For photos or "
                    "diagrams give a short factual description of what is shown. Do not invent "
                    f"numbers. Output Markdown only, no preamble.\nContext: {context}"
                )},
            ],
        }],
    }
    headers = {"anthropic-version": "2023-06-01", "content-type": "application/json",
               "x-api-key": key, "authorization": f"Bearer {key}"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=180)
        if resp.status_code >= 400:
            return "", f"vision HTTP {resp.status_code}"
        body = resp.json()
        text = "".join(p.get("text", "") for p in body.get("content", []) if isinstance(p, dict))
        return text.strip(), None if text.strip() else "vision returned empty text"
    except Exception as exc:  # noqa: BLE001
        return "", f"vision failed: {type(exc).__name__}"


class ImageWriter:
    """Saves images of one source and writes a Markdown file per image."""

    def __init__(self, folder: Path, source: dict, use_ocr: bool, use_vision: bool):
        self.folder = folder
        self.source = source
        self.use_ocr = use_ocr
        self.use_vision = use_vision
        self.count = 0
        self.skipped = 0
        self.hashes: dict[str, str] = {}
        folder.mkdir(parents=True, exist_ok=True)

    def add(self, data: bytes, *, ext: str, origin_url: str | None = None, alt: str = "",
            caption: str = "", page: int | None = None, kind: str = "figure") -> str | None:
        """Return the image filename (relative to images/) or None if skipped."""
        digest = hashlib.sha1(data).hexdigest()
        if digest in self.hashes:
            return self.hashes[digest]
        try:
            with Image.open(io.BytesIO(data)) as im:
                width, height = im.size
        except Exception:  # noqa: BLE001
            self.skipped += 1
            return None
        if min(width, height) < MIN_IMAGE_EDGE:
            self.skipped += 1
            return None

        self.count += 1
        stem = f"img-{self.count:03d}"
        ext = (ext or "png").lower().lstrip(".")
        ext = {"jpeg": "jpg", "jpe": "jpg"}.get(ext, ext)
        name = f"{stem}.{ext}"
        (self.folder / name).write_bytes(data)
        self.hashes[digest] = name

        mime = mimetypes.types_map.get(f".{ext}", "image/png")
        context = f"{self.source['title']}" + (f", page {page}" if page else "") + (f", alt: {alt}" if alt else "")
        ocr_text, ocr_note = ocr_image(data) if self.use_ocr else ("", "OCR disabled")
        vis_text, vis_note = vision_markdown(data, mime, context) if self.use_vision else ("", None)

        title = alt or caption or f"{self.source['title']} — {kind} {self.count}"
        meta = frontmatter({
            "type": "image",
            "kind": kind,
            "source_title": self.source["title"],
            "source_url": self.source["url"],
            "image_url": origin_url,
            "page": page,
            "file": name,
            "width": width,
            "height": height,
            "sha1": digest,
            "ocr": bool(ocr_text),
            "ocr_note": ocr_note,
            "vision": bool(vis_text),
            "vision_note": vis_note,
            "crawled_at": now_iso(),
        })
        parts = [meta, f"# {title}\n", f"![{alt or title}]({name})\n"]
        if caption or alt:
            parts.append("## Caption\n")
            if caption:
                parts.append(caption + "\n")
            if alt and alt != caption:
                parts.append(f"Alt text: {alt}\n")
        if vis_text:
            parts.append("## Content (LLM transcription)\n")
            parts.append(vis_text + "\n")
        parts.append("## Text in image (OCR)\n")
        parts.append(f"```text\n{ocr_text}\n```\n" if ocr_text else f"_{ocr_note or 'no text'}_\n")
        (self.folder / f"{stem}.md").write_text(tidy_markdown("\n".join(parts)), encoding="utf-8")
        return name


# ---------------------------------------------------------------- HTML

def best_img_url(img, page_url: str) -> str | None:
    src = img.get("src") or img.get("data-src") or ""
    srcset = img.get("srcset") or img.get("data-srcset") or ""
    if srcset:
        best, best_w = None, -1
        for item in srcset.split(","):
            bits = item.strip().split()
            if not bits:
                continue
            w = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit() else 0
            if w > best_w:
                best, best_w = bits[0], w
        src = best or src
    if not src or src.startswith("data:"):
        return None
    return urljoin(page_url, src)


def image_caption(img) -> str:
    fig = img.find_parent("figure")
    if fig and fig.find("figcaption"):
        return fig.find("figcaption").get_text(" ", strip=True)
    for cls in ("caption", "credit"):
        holder = img.find_parent(class_=re.compile(cls))
        if holder:
            return holder.get_text(" ", strip=True)[:500]
    return ""


def crawl_html(src: dict, resp: requests.Response, out: Path, fetcher: Fetcher, args) -> dict:
    soup = BeautifulSoup(resp.content, "lxml")
    page_title = soup.title.get_text(strip=True) if soup.title else src["title"]
    main = soup.select_one("div.entry-content") or soup.select_one("main") or soup.select_one("article") or soup.body
    for sel in DROP_SELECTORS:
        for el in main.select(sel):
            el.decompose()

    images = ImageWriter(out / "images", src, args.ocr, args.vision)
    for img in main.find_all("img"):
        url = best_img_url(img, resp.url)
        if not url:
            img.decompose()
            continue
        alt = (img.get("alt") or "").strip()
        caption = image_caption(img)
        try:
            r = fetcher.get(url)
            ext = Path(urlparse(r.url).path).suffix or mimetypes.guess_extension(
                r.headers.get("content-type", "").split(";")[0]) or ".png"
            name = images.add(r.content, ext=ext, origin_url=url, alt=alt, caption=caption)
        except Exception as exc:  # noqa: BLE001
            log(f"   image failed {url[:80]}: {exc}")
            name = None
        # An <a> wrapping only the image would produce a broken nested link; replace the <a>.
        target = img
        parent = img.parent
        if parent is not None and parent.name == "a" and not parent.get_text(strip=True):
            target = parent
        if name:
            target.replace_with(soup.new_string(
                f"\n\n![{alt}](images/{name}) — [image notes](images/{Path(name).stem}.md)\n\n"))
        else:
            target.decompose()

    hrefs = [urljoin(resp.url, a["href"]) for a in main.find_all("a", href=True)]
    pdf_links = sorted({h for h in hrefs if ".pdf" in h.lower()})
    page_links = sorted({norm_page_url(h) for h in hrefs
                         if urlparse(h).scheme in ("http", "https") and ".pdf" not in h.lower()
                         and not re.search(r"\.(jpe?g|png|gif|webp|svg|zip|mp4|mov)(\?|$)", h, re.I)})
    body = markdownify(str(main), heading_style="ATX", bullets="-", strip=["span"])
    body = re.sub(r"\[\s*\]\([^)]*\)", "", body)  # empty links left by removed icons
    meta = frontmatter({
        "type": "html",
        "title": src["title"],
        "page_title": page_title,
        "source_url": src["url"],
        "final_url": resp.url,
        "images": images.count,
        "crawled_at": now_iso(),
    })
    links = "".join(f"- {u}\n" for u in pdf_links)
    text = meta + f"# {src['title']}\n\nSource: <{src['url']}>\n\n" + body
    if links:
        text += "\n\n## Linked PDFs\n\n" + links
    (out / "index.md").write_text(tidy_markdown(text), encoding="utf-8")
    return {"kind": "html", "images": images.count, "images_skipped": images.skipped,
            "pdf_links": pdf_links, "page_links": page_links, "chars": len(body)}


def norm_page_url(url: str) -> str:
    parts = urlparse(clean_url(url))
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return urlunparse(parts._replace(fragment="", path=path, query=parts.query))


def seed_prefix(url: str) -> str:
    """Links are followed only below the seed page's own path (same section)."""
    parts = urlparse(url)
    if parts.path.lower().endswith(".pdf"):
        return ""
    path = parts.path if parts.path.endswith("/") else parts.path.rsplit("/", 1)[0] + "/"
    return f"{parts.scheme}://{parts.netloc}{path}"


def page_slug(url: str, parent_slug: str) -> str:
    segs = [s for s in urlparse(url).path.split("/") if s]
    tail = segs[-1] if segs else "page"
    tail = re.sub(r"\.pdf$", "", tail, flags=re.I)
    return f"{parent_slug}--{slugify(tail)}"[:90]


# ---------------------------------------------------------------- PDF

def crawl_pdf(src: dict, resp: requests.Response, out: Path, args) -> dict:
    import pymupdf
    import pymupdf4llm

    pdf_path = out / "source.pdf"
    pdf_path.write_bytes(resp.content)
    doc = pymupdf.open(pdf_path)
    images = ImageWriter(out / "images", src, args.ocr, args.vision)
    sections, image_pages = [], 0

    for pno in range(doc.page_count):
        page = doc[pno]
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                page_md = pymupdf4llm.to_markdown(doc, pages=[pno], write_images=False,
                                                  show_progress=False)
        except Exception:  # noqa: BLE001  - fall back to plain text layer
            page_md = page.get_text("text")
        page_md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", page_md or "")
        refs: list[str] = []

        if len(re.sub(r"\s+", "", page_md)) < MIN_PAGE_TEXT_CHARS:
            # image-only (scanned) page: render it and convert the picture to Markdown
            image_pages += 1
            pix = page.get_pixmap(dpi=200)
            name = images.add(pix.tobytes("png"), ext="png", page=pno + 1, kind="page-render",
                              caption=f"Rendered page {pno + 1} (no text layer)")
            if name:
                refs.append(name)
                ocr_md = (out / "images" / f"{Path(name).stem}.md").read_text(encoding="utf-8")
                m = re.search(r"```text\n(.*?)\n```", ocr_md, re.S)
                page_md = m.group(1) if m else page_md
        else:
            for info in page.get_images(full=True):
                try:
                    data = doc.extract_image(info[0])
                except Exception:  # noqa: BLE001
                    continue
                if not data or not data.get("image"):
                    continue
                img_bytes, ext = data["image"], data.get("ext", "png")
                if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
                    try:
                        pix = pymupdf.Pixmap(doc, info[0])
                        if pix.n - pix.alpha >= 4:
                            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                        img_bytes, ext = pix.tobytes("png"), "png"
                    except Exception:  # noqa: BLE001
                        continue
                name = images.add(img_bytes, ext=ext, page=pno + 1)
                if name and name not in refs:
                    refs.append(name)

        block = [f"## Page {pno + 1}\n", page_md.strip() + "\n"]
        for name in refs:
            block.append(f"![page {pno + 1}](images/{name}) — [image notes](images/{Path(name).stem}.md)\n")
        sections.append("\n".join(block))

    meta = frontmatter({
        "type": "pdf",
        "title": src["title"],
        "source_url": src["url"],
        "pdf_title": (doc.metadata or {}).get("title") or None,
        "pages": doc.page_count,
        "image_only_pages": image_pages,
        "images": images.count,
        "crawled_at": now_iso(),
    })
    text = meta + f"# {src['title']}\n\nSource: <{src['url']}> · local copy: [source.pdf](source.pdf)\n\n" + "\n\n".join(sections)
    (out / "index.md").write_text(tidy_markdown(text), encoding="utf-8")
    pages = doc.page_count
    doc.close()
    return {"kind": "pdf", "pages": pages, "image_only_pages": image_pages,
            "images": images.count, "images_skipped": images.skipped}


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--only", nargs="*", help="crawl only these slugs (e.g. flex sofie)")
    ap.add_argument("--force", action="store_true", help="re-crawl sources that already have index.md")
    ap.add_argument("--no-ocr", dest="ocr", action="store_false", help="skip tesseract OCR")
    ap.add_argument("--vision", action="store_true",
                    help="also transcribe each image to Markdown with the LLM in .env (costs tokens)")
    ap.add_argument("--follow-pdfs", action="store_true", help="also crawl PDFs linked from HTML pages")
    ap.add_argument("--depth", type=int, default=0,
                    help="follow HTML links below each seed's own URL path this many levels deep")
    ap.add_argument("--max-pages", type=int, default=300, help="hard cap on pages per run")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests (politeness)")
    args = ap.parse_args()

    sources = parse_sources(args.sources)
    if args.only:
        wanted = {s.lower() for s in args.only}
        sources = [s for s in sources if s["slug"] in wanted]
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"sources": {}}
    fetcher = Fetcher(args.delay)
    log(f"{len(sources)} sources -> {args.out}  (ocr={'on' if args.ocr and TESSERACT else 'off'}, vision={args.vision})")

    for s in sources:
        s.setdefault("depth", 0)
        s.setdefault("prefix", seed_prefix(s["url"]))
    queue = list(sources)
    done_urls: set[str] = set()
    used_slugs = {s["slug"] for s in sources}
    failures = 0

    def enqueue(url: str, parent: dict, title: str, depth: int) -> None:
        key = norm_page_url(url) if ".pdf" not in url.lower() else clean_url(url)
        if key in done_urls or any(q["url"] == key for q in queue):
            return
        slug, base, n = page_slug(key, parent["slug"].split("--")[0]), None, 2
        base = slug
        while slug in used_slugs:
            slug, n = f"{base}-{n}", n + 1
        used_slugs.add(slug)
        queue.append({"title": title, "url": key, "slug": slug, "depth": depth,
                      "prefix": parent["prefix"], "parent": parent["slug"]})

    def follow_links(entry: dict, src: dict) -> None:
        if args.follow_pdfs:
            for link in entry.get("pdf_links", []):
                enqueue(link, src, f"{src['title']} — linked PDF", src["depth"] + 1)
        if src["depth"] < args.depth and src.get("prefix"):
            for link in entry.get("page_links", []):
                if link.startswith(src["prefix"]) and link != norm_page_url(src["url"]):
                    enqueue(link, src, f"{src['title']} — subpage", src["depth"] + 1)

    while queue:
        src = queue.pop(0)
        key = norm_page_url(src["url"]) if ".pdf" not in src["url"].lower() else src["url"]
        if key in done_urls:
            continue
        if len(done_urls) >= args.max_pages:
            log(f"max-pages {args.max_pages} reached; {len(queue) + 1} left in queue")
            break
        done_urls.add(key)
        out = args.out / src["slug"]
        old = manifest["sources"].get(src["slug"], {})
        if (out / "index.md").exists() and not args.force and old.get("status") == "ok":
            log(f"skip {src['slug']} (exists; use --force)")
            follow_links(old, src)
            continue
        log(f"crawl {src['slug']}: {src['url'][:100]}")
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        entry = {"title": src["title"], "url": src["url"], "depth": src.get("depth", 0),
                 "parent": src.get("parent"), "crawled_at": now_iso()}
        try:
            resp = fetcher.get(src["url"])
            ctype = resp.headers.get("content-type", "").lower()
            if "pdf" in ctype or resp.content[:4] == b"%PDF":
                entry.update(crawl_pdf(src, resp, out, args))
            elif "html" in ctype:
                entry.update(crawl_html(src, resp, out, fetcher, args))
                follow_links(entry, src)
            else:
                raise RuntimeError(f"unsupported content-type {ctype!r}")
            entry["status"] = "ok"
            log(f"   ok: " + ", ".join(f"{k}={v}" for k, v in entry.items()
                                    if k in ("kind", "pages", "images", "image_only_pages", "chars")))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            entry.update(status="error", error=str(exc)[:500])
            (out / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
            log(f"   ERROR: {exc}")
        manifest["sources"][src["slug"]] = entry
        manifest["updated_at"] = now_iso()
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    log(f"done: {len(done_urls)} processed, {failures} failed; manifest {manifest_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
