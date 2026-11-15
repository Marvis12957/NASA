"""GraphRAG extract/resolve helpers. Types come from the concept schema."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from app.neo4j_config import ROOT
from app.ontology import (
    EXTRACT_SYSTEM,
    GLOBAL_CATALOG_PREFIXES,
    NEO4J_LABEL,
    TYPE_PREFIX,
    collapse,
    is_forbidden_entity,
    map_extract_type,
    phenomenon_slug,
    scoped_id,
    slug,
)

CHILDREN_PATH = ROOT / "data" / "chunks" / "children.jsonl"
GRAPH_PATH = ROOT / "backend" / "var" / "graph.json"
CATALOG_PATH = ROOT / "backend" / "var" / "catalog.json"
PROFILES_PATH = ROOT / "data" / "profiles.json"

CATALOG_PREFIX_TO_EXTRACT = {
    "inv": "inv",
    "fuel": "fuel",
    "hw": "hardware",
    "hardware": "hardware",
    "sensor": "sensor",
    "phen": "phenomenon",
    "phenomenon": "phenomenon",
    "suppressant": "suppressant",
    "loc": "location",
    "location": "location",
    "scenario": "scenario",
    "role": "role",
    "family": "family",
}

PSI_TOKEN = re.compile(r"PSI[-\s]?\d+", re.I)
CAD_STEM = re.compile(
    r"(?i)(?:s\d{4}[a-z]{2,4}\d|\d{5}[a-z]{2,4}\d|\d{5}mdfh\d)"
)


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def normalize_type(raw: str, name: str = "") -> str | None:
    return map_extract_type(raw, name)


def is_drawing_leftover(child: dict) -> bool:
    source = str(child.get("source") or "").replace("\\", "/").lower()
    if "acme_drawings" in source or "drawing_tree" in source or "drawing-tree" in source:
        return True
    name = Path(source).name
    if CAD_STEM.match(name) and "requirement" not in name:
        return True
    text = str(child.get("text") or "")
    letters = len(re.findall(r"[A-Za-z]", text))
    digits = len(re.findall(r"\d", text))
    if letters < 40 and digits > letters:
        return True
    words = [w for w in text.split() if re.search(r"[A-Za-z]{3,}", w)]
    if words and len(words) < 12 and (child.get("token_estimate") or 0) >= 80:
        if re.search(r"(?i)third angle|rev\s*[A-Z]\b|sheet \d+ of \d+", text):
            return True
    return False


def should_skip_chunk(child: dict, min_tokens: int = 80) -> bool:
    if int(child.get("token_estimate") or 0) < min_tokens:
        return True
    return is_drawing_leftover(child)


def eligible_chunks(children: Iterable[dict], min_tokens: int = 80) -> list[dict]:
    return [row for row in children if not should_skip_chunk(row, min_tokens=min_tokens)]


def load_gazetteer() -> tuple[re.Pattern[str], dict[str, str], set[str]]:
    import sys

    scripts = str(ROOT / "backend" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from link_chunks import build_gazetteer, compile_gazetteer  # type: ignore  # noqa: E402

    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    profiles = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    aliases = build_gazetteer(graph, catalog, profiles)
    pattern, mapping = compile_gazetteer(aliases)
    known = {node["id"] for node in graph["nodes"]}
    return pattern, mapping, known


def normalize_psi_token(raw: str | None) -> str | None:
    match = re.search(r"PSI[-\s]?(\d+)", raw or "", re.I)
    if not match:
        return None
    return f"PSI-{int(match.group(1))}"


def scope_psi(child: dict | None) -> str:
    child = child or {}
    for candidate in (child.get("psi"), child.get("id"), child.get("parent_id"), child.get("source")):
        psi = normalize_psi_token(str(candidate or ""))
        if psi:
            return psi
    found = PSI_TOKEN.findall(str(child.get("text") or ""))
    if found:
        psi = normalize_psi_token(found[0])
        if psi:
            return psi
    folder = collapse(str(child.get("psi") or ""))
    if folder:
        return folder.replace(" ", "-")
    return "unknown"


def chunk_pages(child: dict | None) -> list[int]:
    pages: list[int] = []
    if not child:
        return pages
    span = child.get("pages_span")
    values: list = list(span) if isinstance(span, list) else []
    if child.get("page") is not None:
        values.append(child.get("page"))
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            number = int(value)
            if number not in pages:
                pages.append(number)
    return pages


def chunk_first_source(child: dict | None) -> str:
    child = child or {}
    psi = str(child.get("psi") or "")
    doc_id = str(child.get("doc_id") or "")
    if psi and doc_id:
        md = ROOT / "data" / "text" / psi / f"{doc_id}.md"
        if md.is_file():
            try:
                return str(md.relative_to(ROOT))
            except ValueError:
                return str(md)
    return str(child.get("source") or "")


def _catalog_record(nid: str, surface: str, created: bool) -> dict[str, Any]:
    prefix = nid.split(":", 1)[0]
    return {
        "id": nid,
        "label": surface,
        "type": prefix,
        "neo4j": NEO4J_LABEL.get(prefix, "Entity"),
        "matched": True,
        "created": created,
        "extracted": False,
        "canonical": None,
        "psi": nid.removeprefix("inv:") if prefix == "inv" else None,
    }


def resolve_entity(
    name: str,
    etype: str,
    mapping: dict[str, str],
    known: set[str],
    *,
    psi: str | None = None,
    chunk: dict | None = None,
) -> dict[str, Any] | None:
    """Resolve an LLM entity. Catalog Investigation/Scenario/Role stay global ids.

    Other extracted mentions use `{type}:{slug}@{psi}` so the same surface
    (PMMA, cool flame, CIR) is not collapsed across investigations or types.
    """
    mapped = map_extract_type(etype, name)
    if not mapped:
        return None
    surface = collapse(name)
    if not surface:
        return None
    key = surface.casefold()
    prefix = TYPE_PREFIX.get(mapped)
    gazetteer = mapping.get(key)
    gazetteer_prefix = gazetteer.split(":", 1)[0] if gazetteer else ""

    if mapped == "Investigation" or prefix == "inv":
        if gazetteer_prefix == "inv":
            return _catalog_record(gazetteer, surface, gazetteer not in known)
        token = normalize_psi_token(surface)
        if token:
            nid = f"inv:{token}"
            if nid in known or gazetteer_prefix == "inv":
                return _catalog_record(nid, surface, nid not in known)
        return None

    if gazetteer_prefix in GLOBAL_CATALOG_PREFIXES and gazetteer_prefix != "inv":
        return _catalog_record(gazetteer, surface, gazetteer not in known)

    if prefix in GLOBAL_CATALOG_PREFIXES:
        return None

    if not prefix:
        return None

    scoped_psi = psi or scope_psi(chunk)
    name_slug = phenomenon_slug(surface) if prefix == "phenomenon" else slug(surface)
    if not name_slug:
        return None
    nid = scoped_id(prefix, name_slug, scoped_psi)
    canonical = None
    if (
        gazetteer
        and gazetteer in known
        and CATALOG_PREFIX_TO_EXTRACT.get(gazetteer_prefix) == prefix
        and prefix == "fuel"
    ):
        canonical = gazetteer
    return {
        "id": nid,
        "label": surface,
        "type": prefix,
        "neo4j": NEO4J_LABEL.get(prefix, "Entity"),
        "matched": bool(canonical),
        "created": nid not in known,
        "extracted": True,
        "canonical": canonical,
        "psi": scoped_psi,
        "aliases": [surface],
    }


def extraction_prompt(batch: list[dict], max_chars: int = 2200) -> str:
    parts = [
        "Extract entities and relationships for each passage using only ontology types and relations.",
        "Do not drop investigation scope: extracted concepts belong to the PSI in the passage.",
        'JSON shape: {"items":[{"chunk_id":"...","entities":[{"name":"...","type":"Phenomenon"}],'
        '"relationships":[{"source":"...","target":"...","type":"STUDIED"}]}]}',
        "",
    ]
    for child in batch:
        text = collapse(str(child.get("text") or ""))[:max_chars]
        parts.append(f"### {child['id']}\n{text}\n")
    return "\n".join(parts)


def parse_items(payload: dict[str, Any]) -> list[dict]:
    items = payload.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    if "entities" in payload:
        return [payload]
    return []
