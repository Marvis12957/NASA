#!/usr/bin/env python3
"""Upload figure images and their JSON sidecars to Cloudinary.

Public id is the path relative to ``data/`` so the deployed API can rebuild
the delivery URL from that same path. Image ids drop the extension; sidecar
JSON is uploaded as a raw file and keeps ``.json``.

Credentials come from ``.env`` (never from the command line, so they do not
land in the shell history):

  CLOUDINARY_CLOUD_NAME
  CLOUDINARY_API_KEY
  CLOUDINARY_API_SECRET

The run checkpoints ``backend/var/cloudinary_upload.jsonl`` and skips paths
already uploaded. Files larger than the Cloudinary image limit are recompressed
before upload; the public id stays the original path.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.cloudinary_media import IMAGE_EXTS, sign_params  # noqa: E402
from app.neo4j_config import load_env_file  # noqa: E402

DATA = ROOT / "data"
CHECKPOINT = ROOT / "backend" / "var" / "cloudinary_upload.jsonl"
MAX_IMAGE_BYTES = 9_500_000
IMAGE_ROOTS = ("images",)


def _env(name: str) -> str:
    import os

    return (os.environ.get(name) or "").strip()


def missing_credentials() -> list[str]:
    return [
        name
        for name in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")
        if not _env(name)
    ]


def iter_uploads() -> list[Path]:
    found: list[Path] = []
    for root_name in IMAGE_ROOTS:
        base = DATA / root_name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            if ext in IMAGE_EXTS:
                found.append(path)
            elif ext == ".json" and root_name == "images":
                found.append(path)
    found.sort()
    return found


def rel_of(path: Path) -> str:
    return path.relative_to(DATA).as_posix()


def content_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".json": "application/json",
    }.get(path.suffix.lower(), "application/octet-stream")


def _context_value(value: object) -> str:
    raw = str(value).replace("|", "/").replace("=", " ").replace("&", " ")
    text = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in raw)
    return " ".join(text.split())[:400]


def image_context(path: Path) -> str:
    sidecar = path.with_suffix(".json")
    if not sidecar.is_file():
        return ""
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    for key in ("psi", "page", "figure", "caption", "caption_position", "source"):
        if data.get(key) in (None, ""):
            continue
        parts.append(f"{key}={_context_value(data[key])}")
    context = "|".join(parts)
    return context[:1000]


def _shrink_pillow(path: Path) -> bytes | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        for dim, quality in ((3200, 80), (2400, 70), (1800, 60), (1400, 50), (1000, 40)):
            copy = rgb.copy()
            copy.thumbnail((dim, dim))
            import io

            buf = io.BytesIO()
            copy.save(buf, format="JPEG", quality=quality, optimize=True)
            blob = buf.getvalue()
            if len(blob) <= MAX_IMAGE_BYTES:
                return blob
    return None


def _shrink_sips(path: Path) -> bytes | None:
    if shutil.which("sips") is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.jpg"
        for dim, quality in ((3200, 80), (2400, 70), (1800, 60), (1400, 50), (1000, 40)):
            subprocess.run(
                [
                    "sips",
                    "-Z",
                    str(dim),
                    "-s",
                    "format",
                    "jpeg",
                    "-s",
                    "formatOptions",
                    str(quality),
                    str(path),
                    "--out",
                    str(out),
                ],
                check=False,
                capture_output=True,
            )
            if out.is_file() and out.stat().st_size <= MAX_IMAGE_BYTES:
                return out.read_bytes()
    return None


def prepare_body(path: Path) -> tuple[bytes, str, str]:
    """Return bytes, content type, and upload filename."""
    ext = path.suffix.lower()
    if ext == ".json" or path.stat().st_size <= MAX_IMAGE_BYTES:
        return path.read_bytes(), content_type(path), path.name.replace('"', "")
    shrunk = _shrink_pillow(path) or _shrink_sips(path)
    if not shrunk:
        raise RuntimeError(
            f"{rel_of(path)} is {path.stat().st_size} bytes and could not be recompressed under 10 MB"
        )
    return shrunk, "image/jpeg", "figure.jpg"


def _multipart(fields: dict[str, str], filename: str, content_type: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----nasaCloudinary" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    header = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    chunks.append(header + content + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


class UploadError(Exception):
    def __init__(self, message: str, *, retry: bool = False, fatal: bool = False, resign: bool = False):
        super().__init__(message)
        self.retry = retry
        self.fatal = fatal
        self.resign = resign


def _post(
    url: str,
    fields: dict[str, str],
    filename: str,
    mime: str,
    content: bytes,
) -> dict:
    body, boundary = _multipart(fields, filename, mime, content)
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        message = detail
        try:
            parsed = json.loads(detail)
            message = str((parsed.get("error") or {}).get("message") or detail)
        except json.JSONDecodeError:
            pass
        lowered = message.lower()
        if exc.code in {420, 429, 500, 502, 503, 504}:
            raise UploadError(message, retry=True) from exc
        if "invalid signature" in lowered:
            raise UploadError(message, resign=True) from exc
        if exc.code in {401, 403} or "api_key" in lowered or "cloud_name" in lowered or "api_secret" in lowered:
            raise UploadError(message, fatal=True) from exc
        raise UploadError(message) from exc
    except urllib.error.URLError as exc:
        raise UploadError(str(exc.reason), retry=True) from exc
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise UploadError("Cloudinary returned a non-JSON body", retry=True) from exc
    if not isinstance(parsed, dict) or not parsed.get("secure_url"):
        raise UploadError("Cloudinary response had no secure_url", retry=True)
    return parsed


def upload_one(
    path: Path,
    *,
    cloud: str,
    api_key: str,
    api_secret: str,
    algorithm: str,
) -> str:
    rel = rel_of(path)
    resource = "raw" if path.suffix.lower() == ".json" else "image"
    public_id = rel if resource == "raw" else str(Path(rel).with_suffix(""))
    content, mime, filename = prepare_body(path)
    params = {
        "overwrite": "true",
        "public_id": public_id,
        "timestamp": str(int(time.time())),
        "unique_filename": "false",
    }
    if resource == "image":
        context = image_context(path)
        if context:
            params["context"] = context
        tags = ""
        if context.startswith("psi="):
            tags = context.split("|", 1)[0].split("=", 1)[1]
        if tags:
            params["tags"] = tags
    fields = {
        **params,
        "api_key": api_key,
        "signature": sign_params(params, api_secret, algorithm),
    }
    url = f"https://api.cloudinary.com/v1_1/{cloud}/{resource}/upload"
    parsed = _post(url, fields, filename, mime, content)
    return str(parsed["secure_url"])


def load_done() -> set[str]:
    done: set[str] = set()
    if not CHECKPOINT.is_file():
        return done
    for line in CHECKPOINT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rel = row.get("rel")
        if isinstance(rel, str):
            done.add(rel)
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload data/images figures and JSON sidecars to Cloudinary")
    parser.add_argument("--limit", type=int, default=0, help="Upload at most N pending files (0 = all)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-upload paths already in the checkpoint")
    args = parser.parse_args()

    load_env_file()
    files = iter_uploads()
    done = set() if args.force else load_done()
    pending = [path for path in files if rel_of(path) not in done]
    if args.limit > 0:
        pending = pending[: args.limit]
    oversized = sum(1 for path in pending if path.suffix.lower() in IMAGE_EXTS and path.stat().st_size > MAX_IMAGE_BYTES)
    print(
        f"files {len(files)} already uploaded {len(done)} pending {len(pending)} oversize {oversized}"
    )
    missing = missing_credentials()
    if args.dry_run:
        if missing:
            print("dry-run: missing " + ", ".join(missing))
        return 0
    if missing:
        print("missing " + ", ".join(missing) + ". Set them in .env and re-run.", file=sys.stderr)
        return 2
    if not pending:
        print("nothing to upload")
        return 0

    cloud = _env("CLOUDINARY_CLOUD_NAME")
    api_key = _env("CLOUDINARY_API_KEY")
    api_secret = _env("CLOUDINARY_API_SECRET")
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    state = {"algorithm": "sha1", "fatal": "", "ok": 0, "failed": 0}

    def record(rel: str, url: str) -> None:
        line = json.dumps({"rel": rel, "url": url}, ensure_ascii=False)
        with lock:
            with CHECKPOINT.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            state["ok"] += 1
            count = state["ok"]
        if count == 1 or count % 25 == 0 or count == len(pending):
            print(f"[{count}/{len(pending)}] {rel}")

    def worker(path: Path) -> None:
        rel = rel_of(path)
        delay = 2.0
        for attempt in range(1, 6):
            with lock:
                if state["fatal"]:
                    return
                algorithm = state["algorithm"]
            try:
                url = upload_one(
                    path,
                    cloud=cloud,
                    api_key=api_key,
                    api_secret=api_secret,
                    algorithm=algorithm,
                )
            except UploadError as exc:
                if exc.resign and algorithm == "sha1":
                    with lock:
                        state["algorithm"] = "sha256"
                    continue
                if exc.fatal or exc.resign:
                    with lock:
                        state["fatal"] = str(exc)
                    print(f"fatal {rel}: {exc}", file=sys.stderr)
                    return
                if exc.retry and attempt < 5:
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                with lock:
                    state["failed"] += 1
                print(f"fail {rel}: {exc}", file=sys.stderr)
                return
            record(rel, url)
            return

    workers = max(1, args.workers)
    if workers == 1:
        for path in pending:
            worker(path)
            if state["fatal"]:
                break
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker, path) for path in pending]
            for future in as_completed(futures):
                future.result()
                if state["fatal"]:
                    break
    print(f"uploaded {state['ok']} failed {state['failed']}")
    if state["fatal"]:
        print(state["fatal"], file=sys.stderr)
        return 1
    return 1 if state["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
