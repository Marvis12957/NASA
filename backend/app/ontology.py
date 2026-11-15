"""Versioned GraphRAG concept schema. Constrains extract types and ids."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("concept_schema.json")
HYPHENS = str.maketrans({"–": "-", "—": "-", "−": "-", "‐": "-"})
OXYGEN_ENTITY = re.compile(
    r"(?i)^\s*(?:about\s+|~|≈)?\s*\d+(?:\.\d+)?\s*(?:%|percent)?\s*"
    r"(?:o2|o₂|oxygen|xo2|x_o2)?\s*$"
    r"|^\s*(?:xo2|x_o2|loi|limiting oxygen(?: index)?|mole fraction(?: of o2)?|oxygen index)\s*$"
    r"|^\s*\d+(?:\.\d+)?\s*(?:psi|atm|kpa|mm|cm/s|s|mg/s)\s*$"
)


def collapse(text: str) -> str:
    text = (text or "").translate(HYPHENS).strip()
    return re.sub(r"\s+", " ", text)


def slug(text: str) -> str:
    text = collapse(text).casefold()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:80]


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_version() -> str:
    return str(load_schema().get("version") or "0")


def node_types() -> list[dict[str, Any]]:
    return list(load_schema().get("node_types") or [])


def _by_extract_label() -> dict[str, dict[str, Any]]:
    return {str(item["extract_label"]): item for item in node_types()}


def allowed_type_aliases() -> dict[str, str]:
    mapping: dict[str, str] = {}
    catalog_only: dict[str, str] = {}
    for item in node_types():
        label = str(item["extract_label"])
        dest = catalog_only if item.get("prefix") in {"scenario", "role"} else mapping
        dest[collapse(label).casefold()] = label
        dest[str(item["id"]).casefold()] = label
        dest[str(item["prefix"]).casefold()] = label
        for alias in item.get("aliases") or []:
            dest[collapse(str(alias)).casefold()] = label
    for key, value in catalog_only.items():
        mapping.setdefault(key, value)
    return mapping


def type_prefix_map() -> dict[str, str]:
    return {str(item["extract_label"]): str(item["prefix"]) for item in node_types()}


def neo4j_labels() -> dict[str, str]:
    labels = {
        "hw": "Hardware",
        "phen": "Phenomenon",
        "loc": "Location",
        "family": "Family",
    }
    for item in node_types():
        labels[str(item["prefix"])] = str(item["neo4j"])
        labels[str(item["id"])] = str(item["neo4j"])
    return labels


def global_prefixes() -> set[str]:
    return {str(item["prefix"]) for item in node_types() if item.get("scope") == "global"}


def allowed_relations() -> set[str]:
    return {str(item["id"]) for item in load_schema().get("relation_types") or []}


def relation_aliases() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in load_schema().get("relation_types") or []:
        canonical = str(item["id"])
        mapping[canonical] = canonical
        for alias in item.get("aliases") or []:
            mapping[str(alias).upper().replace(" ", "_")] = canonical
    return mapping


def phenomena() -> tuple[str, ...]:
    rows = [collapse(str(item)).casefold() for item in load_schema().get("phenomena") or []]
    rows.sort(key=len, reverse=True)
    return tuple(rows)


def name_type_overrides() -> dict[str, str]:
    raw = load_schema().get("name_type_overrides") or {}
    return {collapse(str(key)).casefold(): str(value) for key, value in raw.items()}


def forbidden_types() -> set[str]:
    return {collapse(str(item)).casefold() for item in load_schema().get("forbidden_types") or []}


def is_forbidden_entity(name: str, etype: str | None = None) -> bool:
    text = collapse(name)
    if not text:
        return True
    if OXYGEN_ENTITY.search(text):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?%?", text):
        return True
    if re.search(r"(?i)\bmole fraction\b|\blimiting oxygen\b|\boxygen index\b|\bxo2\b", text):
        return True
    if re.search(r"(?i)\d+(?:\.\d+)?\s*%\s*(?:o2|o₂|oxygen)\b", text) and len(text) < 48:
        return True
    if re.search(r"(?i)\b(?:mg/s|flow rate|m_?fuel|m_?o2)\b", text) and len(text) < 48:
        return True
    folded = text.casefold()
    if folded in {"oxygen", "o2", "o₂", "%"}:
        return True
    type_key = collapse(etype or "").casefold()
    if type_key in forbidden_types() or type_key in {"oxygen", "condition", "test condition"}:
        return True
    return False


def map_extract_type(raw: str, name: str = "") -> str | None:
    """Map an LLM type onto the ontology, or reject it."""
    type_key = collapse(raw).casefold()
    if not type_key or type_key in forbidden_types():
        return None
    if is_forbidden_entity(name, raw):
        return None
    aliases = allowed_type_aliases()
    mapped = aliases.get(type_key)
    override = name_type_overrides().get(collapse(name).casefold())
    if override:
        if mapped in {None, "Material/Fuel", "Phenomenon"} or type_key in {"diluent", "inert"}:
            return override
    return mapped


def map_relation(raw: str) -> str | None:
    key = collapse(raw).upper().replace(" ", "_").replace("-", "_")
    if not key:
        return None
    return relation_aliases().get(key)


def phenomenon_slug(surface: str) -> str:
    key = collapse(surface).casefold()
    for phrase in phenomena():
        if phrase == key or phrase in key:
            return slug(phrase)
    return slug(surface)


def scoped_id(prefix: str, name_slug: str, psi: str) -> str:
    """Catalog-global prefixes omit @psi; extracted concepts never drop it."""
    if prefix in global_prefixes():
        return f"{prefix}:{name_slug}"
    token = psi or "unknown"
    return f"{prefix}:{name_slug}@{token}"


def extract_system_prompt() -> str:
    schema = load_schema()
    labels = [str(item["extract_label"]) for item in node_types() if item["prefix"] not in {"scenario", "role"}]
    rels = load_schema().get("relation_types") or []
    rel_lines = [f"{item['id']}: {item.get('meaning', '')}" for item in rels]
    rules = schema.get("disambiguation") or []
    return "\n".join(
        [
            f"You extract a knowledge graph from NASA PSI combustion-safety reports (ontology {schema_version()}).",
            "Return JSON only. Inventing types or relations outside this schema is forbidden.",
            "Allowed entity types: " + ", ".join(labels) + ".",
            "Phenomenon examples: cool flame, soot, extinction, radiative extinction, flame spread.",
            "Location examples: ISS, CIR, MSG, Cygnus, lunar habitat — not numeric conditions.",
            "Same surface name + different type or PSI => different nodes "
            "(fuel:pmma@PSI-25 vs fuel:pmma@PSI-99; sensor:cir@PSI-69 vs hardware:cir@PSI-69).",
            "Catalog Investigation ids stay global (inv:PSI-69). Do not emit oxygen/flow/XO2 entities.",
            "FORBIDDEN entities: oxygen percentages, mole fractions, XO2, limiting oxygen index, "
            "pressures, droplet diameters, flow rates (mg/s), or any numeric test condition. "
            "Those stay Investigation catalog properties.",
            "Relationship types: " + ", ".join(item["id"] for item in rels) + ".",
            *rel_lines,
            *rules,
            "If a mention does not fit an allowed type, omit it. Do not invent oxygen numbers.",
        ]
    )


# Back-compat names used by extract/resolve.
ALLOWED_TYPES = allowed_type_aliases()
TYPE_PREFIX = type_prefix_map()
NEO4J_LABEL = neo4j_labels()
GLOBAL_CATALOG_PREFIXES = global_prefixes()
ALLOWED_RELS = allowed_relations()
EXTRACT_SYSTEM = extract_system_prompt()
PHENOMENA = phenomena()
