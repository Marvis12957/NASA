"""Resolve a figure path to a local file or a Cloudinary delivery URL.

Upload public ids are the path relative to ``data/`` (image ids drop the
extension; JSON sidecars keep ``.json``). The API process only needs
``CLOUDINARY_CLOUD_NAME``. The API secret stays with the upload script.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from app.neo4j_config import ROOT

DATA_ROOT = (ROOT / "data").resolve()
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_CLOUD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def cloud_name() -> str:
    name = (os.environ.get("CLOUDINARY_CLOUD_NAME") or "").strip()
    return name if _CLOUD_NAME_RE.fullmatch(name) else ""


def sign_params(params: dict[str, str], api_secret: str, algorithm: str = "sha1") -> str:
    """Cloudinary signature: alphabetical ``key=value`` pairs, then the secret."""
    payload = "&".join(f"{key}={params[key]}" for key in sorted(params))
    digest = hashlib.sha256 if algorithm == "sha256" else hashlib.sha1
    return digest((payload + api_secret).encode("utf-8")).hexdigest()


def _parts(rel: str) -> list[str] | None:
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return parts


def normalize_rel(path: str) -> str | None:
    """Path under ``data/``, or None when it escapes that tree."""
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        return None
    if raw.startswith("data/"):
        raw = raw[len("data/") :]
    parts = _parts(raw)
    if parts is None:
        return None
    rel = "/".join(parts)
    target = (DATA_ROOT / rel).resolve()
    try:
        resolved = target.relative_to(DATA_ROOT).as_posix()
    except ValueError:
        return None
    if _parts(resolved) is None:
        return None
    return resolved


def _kind(rel: str) -> str | None:
    ext = Path(rel).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext == ".json" and rel.startswith("images/"):
        return "raw"
    return None


def delivery_url(rel: str) -> str | None:
    """Public CDN URL for a ``data/``-relative path. None when Cloudinary is off."""
    cloud = cloud_name()
    if not cloud or _parts(rel) is None or _kind(rel) is None:
        return None
    if _kind(rel) == "image":
        public = str(Path(rel).with_suffix(""))
        ext = Path(rel).suffix.lower()
        encoded = "/".join(quote(part, safe="") for part in public.split("/"))
        return f"https://res.cloudinary.com/{cloud}/image/upload/{encoded}{ext}"
    encoded = "/".join(quote(part, safe="") for part in rel.split("/"))
    return f"https://res.cloudinary.com/{cloud}/raw/upload/{encoded}"


def image_is_servable(target: Path) -> bool:
    """True when the figure file is on disk or Cloudinary can serve that path."""
    if target.suffix.lower() not in IMAGE_EXTS:
        return False
    if target.is_file():
        return True
    try:
        rel = target.resolve().relative_to(DATA_ROOT).as_posix()
    except ValueError:
        return False
    return delivery_url(rel) is not None


@dataclass(frozen=True)
class MediaTarget:
    path: Path | None = None
    redirect_url: str | None = None


def resolve_media(path: str) -> MediaTarget | None:
    """Local file when it exists, otherwise a Cloudinary redirect for that path."""
    rel = normalize_rel(path)
    if rel is None or _kind(rel) is None:
        return None
    target = (DATA_ROOT / rel).resolve()
    try:
        target.relative_to(DATA_ROOT)
    except ValueError:
        return None
    if target.is_file():
        return MediaTarget(path=target)
    url = delivery_url(rel)
    if url:
        return MediaTarget(redirect_url=url)
    return None
