#!/usr/bin/env python3
"""Attach child chunks to the catalog graph and gazetteer-link mentions.

No LLM. No embeddings. Text stays in data/chunks/children.jsonl.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.catalog import add_edge  # noqa: E402

GRAPH_PATH = ROOT / "backend" / "var" / "graph.json"
CATALOG_PATH = ROOT / "backend" / "var" / "catalog.json"
PROFILES_PATH = ROOT / "data" / "profiles.json"
CHILDREN_PATH = ROOT / "data" / "chunks" / "children.jsonl"
PARENTS_PATH = ROOT / "data" / "chunks" / "parents.jsonl"
OUT_PATH = ROOT / "backend" / "var" / "graphrag.json"

MAX_GRAPH_BYTES = 50 * 1024 * 1024
GENERIC_FUELS = {
    "ramped",
    "ramp",
    "2 cm sphere",
    "1 cm sphere",
}
# Lowercase English that must not fire short investigation/hardware acronyms.
AMBIGUOUS = {"same", "slice", "spice", "bass", "flex", "bre", "cir", "msg", "cfi", "daft"}
OXYGEN_NEAR = re.compile(
    r"(?:oxygen|\bo2\b|o₂|mole fraction of o2|o2 mole)",
    re.I,
)
PSI_ID_RE = re.compile(r"\bPSI[-\s]?\d+\b", re.I)
HYPHENS = str.maketrans({"–": "-", "—": "-", "−": "-", "‐": "-"})


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def normalize_surface(text: str) -> str:
    return text.translate(HYPHENS)


def add_alias(aliases: dict[str, str], alias: str, node_id: str, *, overwrite: bool = False) -> None:
    alias = collapse_alias(alias)
    if not alias:
        return
    key = alias.casefold()
    if len(key) < 2:
        return
    if key in {"o2", "o₂", "oxygen", "%", "n2", "he"}:
        return
    if re.fullmatch(r"\d+(?:\.\d+)?%?", alias):
        return
    if key in aliases and not overwrite:
        return
    aliases[key] = node_id
    aliases[f"__surf__:{key}"] = alias


def collapse_alias(text: str) -> str:
    text = normalize_surface(text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def accept_match(span: str, key: str) -> bool:
    if key not in AMBIGUOUS:
        return True
    letters = [c for c in span if c.isalpha()]
    if not letters:
        return False
    if key == "same":
        return span.isupper() or span.upper().startswith("SAME")
    return not span.islower()


def looks_like_oxygen_percent(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 12) : min(len(text), end + 24)]
    if not re.search(r"\d", window):
        return False
    return bool(OXYGEN_NEAR.search(window)) and not re.search(
        r"\b(?:C\d?H\d|CH4|ethylene|methane|propane|butane|heptane|pmma|fuel)\b",
        window,
        re.I,
    )


def paren_acronyms(label: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"\(([A-Z][A-Z0-9\-/]{1,12})\)", label)]


def build_gazetteer(graph: dict, catalog: dict, profiles: dict) -> dict[str, str]:
    aliases: dict[str, str] = {}
    nodes_by_id = {node["id"]: node for node in graph["nodes"]}
    inv_hardware: dict[str, str] = {}
    for edge in graph["edges"]:
        if edge["type"] == "USED_HARDWARE":
            inv_hardware[edge["source"]] = edge["target"]

    for role in ("prevention", "detection", "suppression"):
        add_alias(aliases, role, f"role:{role}")

    for node in graph["nodes"]:
        ntype = node["type"]
        nid = node["id"]
        label = str(node.get("label") or "")
        if ntype == "family":
            add_alias(aliases, label, nid)
            if nid == "family:cir-acme":
                add_alias(aliases, "CIR + ACME", nid, overwrite=True)
            elif nid == "family:cir-mdca":
                add_alias(aliases, "CIR + MDCA", nid, overwrite=True)
            elif nid == "family:msg-duct":
                add_alias(aliases, "MSG glovebox", nid)
                add_alias(aliases, "SPICE fan duct", nid)
            elif nid == "family:cygnus":
                add_alias(aliases, "Cygnus", nid)
                add_alias(aliases, "Saffire", nid)
                add_alias(aliases, "SAFFIRE", nid)
            elif nid == "family:aerosol":
                add_alias(aliases, "ISS aerosol instrument", nid)
        elif ntype == "hardware":
            add_alias(aliases, label, nid)
            for acro in paren_acronyms(label):
                if acro in {"ACME"}:
                    continue
                add_alias(aliases, acro, nid)
        elif ntype == "fuel":
            if label.casefold() in GENERIC_FUELS:
                continue
            add_alias(aliases, label, nid)
            stripped = re.sub(r"\s*\(pure\)\s*$", "", label, flags=re.I).strip()
            if stripped != label:
                add_alias(aliases, stripped, nid)
            stripped = re.sub(r"\s*\(mixture\)\s*$", "", stripped, flags=re.I).strip()
            if stripped and stripped.casefold() not in GENERIC_FUELS:
                add_alias(aliases, stripped, nid)

    # Short material names: prefer the exact catalog fuel node when it exists.
    for fuel_id, extra in (
        ("fuel:pmma", ("PMMA", "poly(methyl methacrylate)", "polymethyl methacrylate", "acrylic")),
        ("fuel:teflon", ("Teflon", "PTFE")),
        ("fuel:kapton", ("Kapton",)),
        ("fuel:silicone", ("Silicone",)),
        ("fuel:silicone-rubber", ("Silicone Rubber",)),
        ("fuel:lampwick", ("Lampwick", "lamp wick")),
        ("fuel:dibutyl-phthalate", ("Dibutyl Phthalate", "DBP")),
        ("fuel:sibal-fabric", ("SIBAL", "SIBAL fabric", "cotton-fiberglass")),
        ("fuel:heptane", ("Heptane", "n-heptane", "n-Heptane")),
        ("fuel:methanol", ("Methanol",)),
        ("fuel:ethylene", ("Ethylene",)),
        ("fuel:propane", ("Propane",)),
        ("fuel:propylene", ("Propylene",)),
        ("fuel:methane", ("Methane",)),
        ("fuel:ethane", ("ethane", "Ethane")),
        ("fuel:butane", ("butane", "Butane")),
        ("fuel:c2h4", ("C2H4",)),
        ("fuel:c3h8", ("C3H8",)),
        ("fuel:c12h26", ("C12H26", "n-dodecane")),
        ("fuel:isooctane-pure", ("IsoOctane", "iso-octane", "isooctane")),
        ("fuel:n-octane-pure", ("n-Octane", "n-octane")),
        ("fuel:decane-pure", ("Decane", "n-decane")),
        ("fuel:ethanol-pure", ("Ethanol",)),
        ("fuel:nomex-iii-natural", ("Nomex", "Nomex III")),
        ("fuel:arizona-road-dust-ard-fine-in-combination-with-i", ("Arizona Road Dust", "ARD Fine")),
    ):
        if fuel_id in nodes_by_id:
            for alias in extra:
                add_alias(aliases, alias, fuel_id, overwrite=True)

    add_alias(aliases, "ACME", "family:cir-acme", overwrite=True)
    add_alias(aliases, "Advanced Combustion via Microgravity Experiments", "family:cir-acme")
    add_alias(aliases, "MDCA", "hw:multi-user-droplet-combustion-apparatus-mdca-com", overwrite=True)
    add_alias(aliases, "Multi-User Droplet Combustion Apparatus", "hw:multi-user-droplet-combustion-apparatus-mdca-com")
    add_alias(aliases, "Multi-user Droplet Combustion Apparatus", "hw:multi-user-droplet-combustion-apparatus-mdca-com")
    add_alias(aliases, "CIR", "hw:combustion-integrated-rack-cir-acme", overwrite=True)
    add_alias(aliases, "Combustion Integrated Rack", "hw:combustion-integrated-rack-cir-acme")
    add_alias(aliases, "MSG", "hw:microgravity-science-glovebox-msg", overwrite=True)
    add_alias(aliases, "Microgravity Science Glovebox", "hw:microgravity-science-glovebox-msg")
    add_alias(aliases, "P-Trak", "hw:tsi-p-trak")
    add_alias(aliases, "TSI P-Trak", "hw:tsi-p-trak", overwrite=True)

    by_id = {item["id"]: item for item in catalog["investigations"]}
    for psi_id, rec in by_id.items():
        inv = f"inv:{psi_id}"
        add_alias(aliases, psi_id, inv, overwrite=True)
        add_alias(aliases, psi_id.replace("-", " "), inv)
        add_alias(aliases, rec["name"], inv, overwrite=True)
        acronym = rec.get("acronym") or ""
        if acronym and acronym.casefold() != rec["name"].casefold():
            add_alias(aliases, acronym, inv, overwrite=True)
            if "/" in acronym:
                for part in acronym.split("/"):
                    add_alias(aliases, part.strip(), inv, overwrite=True)

    # Longer investigation phrases overwrite short collisions (BASS-II vs BASS).
    named = [
        ("Burning and Suppression of Solids II", "inv:PSI-25"),
        ("BASS-II", "inv:PSI-25"),
        ("BASS II", "inv:PSI-25"),
        ("Burning and Suppression of Solids", "inv:PSI-26"),
        ("BASS", "inv:PSI-26"),
        ("Flame Extinguishment Experiment-2", "inv:PSI-68"),
        ("Flame Extinguishment Experiment 2", "inv:PSI-68"),
        ("Second Flame Extinguishment Experiment", "inv:PSI-68"),
        ("FLEX-2", "inv:PSI-68"),
        ("FLEX 2", "inv:PSI-68"),
        ("Flame Extinguishment Experiment", "inv:PSI-69"),
        ("Flame Extinguishment", "inv:PSI-69"),
        ("FLEX", "inv:PSI-69"),
        ("Cool Flames Investigation with Gases", "inv:PSI-159"),
        ("ACME CFI-G", "inv:PSI-159"),
        ("CFI-G", "inv:PSI-159"),
        ("CFI G", "inv:PSI-159"),
        ("Cool Flames Investigation", "inv:PSI-39"),
        ("CFI", "inv:PSI-39"),
        ("ACME Flame Design", "inv:PSI-10"),
        ("Flame Design", "inv:PSI-10"),
        ("ACME Burning Rate Emulator", "inv:PSI-20"),
        ("Burning Rate Emulator", "inv:PSI-20"),
        ("ACME BRE", "inv:PSI-20"),
        ("BRE", "inv:PSI-20"),
        ("Coflow Laminar Diffusion Flame", "inv:PSI-21"),
        ("CLD Flame", "inv:PSI-21"),
        ("ACME CLD Flame", "inv:PSI-21"),
        ("E-FIELD Flames", "inv:PSI-22"),
        ("E-FIELD", "inv:PSI-22"),
        ("EFIELD", "inv:PSI-22"),
        ("s-Flame", "inv:PSI-23"),
        ("s Flame", "inv:PSI-23"),
        ("Dust and Aerosol Measurement Feasibility Test", "inv:PSI-47"),
        ("DAFT-2", "inv:PSI-47"),
        ("DAFT 2", "inv:PSI-47"),
        ("DAFT", "inv:PSI-47"),
        ("SPICE data analysis", "inv:PSI-60"),
        ("Concurrent flame spread vs BASS", "inv:PSI-62"),
        ("Saffire-III", "inv:PSI-100"),
        ("Saffire III", "inv:PSI-100"),
        ("SAFFIRE-III", "inv:PSI-100"),
        ("Saffire-II", "inv:PSI-99"),
        ("Saffire II", "inv:PSI-99"),
        ("SAFFIRE-II", "inv:PSI-99"),
        ("Saffire-I", "inv:PSI-98"),
        ("Saffire I", "inv:PSI-98"),
        ("SAFFIRE-I", "inv:PSI-98"),
        ("Smoke Aerosol Measurement Experiment", "inv:PSI-102"),
        ("SAME-R", "inv:PSI-101"),
        ("SAME R", "inv:PSI-101"),
        ("SAME smoke modeling", "inv:PSI-115"),
        ("SAME", "inv:PSI-102"),
        ("Structure and Liftoff in Combustion Experiment", "inv:PSI-106"),
        ("SLICE", "inv:PSI-106"),
        ("Smoke Point In Co-flow Experiment", "inv:PSI-107"),
        ("Smoke Point In Co-flow", "inv:PSI-107"),
        ("SPICE", "inv:PSI-107"),
        ("STDCE-1", "inv:PSI-108"),
        ("STDCE", "inv:PSI-108"),
        ("Cool-flame perturbation", "inv:PSI-117"),
        ("Cool-flame counterflow", "inv:PSI-142"),
    ]
    for alias, nid in named:
        add_alias(aliases, alias, nid, overwrite=True)

    for psi_id, profile in profiles.items():
        inv = f"inv:{psi_id}"
        add_alias(aliases, profile["name"], inv, overwrite=True)
        hw_label = profile.get("hardware") or ""
        hw_node = inv_hardware.get(inv)
        if hw_label and hw_node:
            add_alias(aliases, hw_label, hw_node)

    return aliases


def compile_gazetteer(aliases: dict[str, str]) -> tuple[re.Pattern[str], dict[str, str]]:
    surfaces: dict[str, str] = {}
    mapping: dict[str, str] = {}
    for key, value in aliases.items():
        if key.startswith("__surf__:"):
            surfaces[key.removeprefix("__surf__:")] = value
            continue
        mapping[key] = value
    phrases = sorted(mapping, key=lambda k: (-len(k), k))
    parts = [re.escape(surfaces.get(k, k)) for k in phrases]
    pattern = re.compile(
        r"(?<![A-Za-z0-9])(?:" + "|".join(parts) + r")(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return pattern, mapping


def extract_mentions(text: str, pattern: re.Pattern[str], mapping: dict[str, str], known_inv: set[str]) -> list[tuple[str, str]]:
    if not text:
        return []
    text = normalize_surface(text)
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for match in PSI_ID_RE.finditer(text):
        raw = re.sub(r"\s+", "", match.group(0).upper().replace("PSI", "PSI-", 1))
        raw = raw.replace("PSI--", "PSI-")
        if not raw.startswith("PSI-"):
            raw = "PSI-" + re.sub(r"\D", "", raw)
        nid = f"inv:{raw}"
        if nid in known_inv and not looks_like_oxygen_percent(text, match.start(), match.end()):
            item = (nid, match.group(0))
            if item not in seen:
                seen.add(item)
                found.append(item)

    for match in pattern.finditer(text):
        span = match.group(0)
        key = collapse_alias(span).casefold()
        nid = mapping.get(key)
        if not nid:
            continue
        if not accept_match(span, key):
            continue
        if looks_like_oxygen_percent(text, match.start(), match.end()) and nid.startswith("fuel:"):
            # Fuel mix strings like "40% CH4" are kept; bare oxygen context is dropped.
            if re.search(r"\b(?:O2|oxygen)\b", text[match.start() : match.end() + 16], re.I):
                continue
        item = (nid, span)
        if item not in seen:
            seen.add(item)
            found.append(item)
    return found


def chunk_node(child: dict) -> dict:
    return {
        "id": child["id"],
        "type": "chunk",
        "psi": child.get("psi"),
        "source": child.get("source"),
        "page": child.get("page"),
        "pages_span": child.get("pages_span"),
        "figures": child.get("figures") or [],
        "token_estimate": child.get("token_estimate"),
    }


def document_node(parent: dict) -> dict:
    return {
        "id": parent["id"],
        "type": "document",
        "psi": parent.get("psi"),
        "source": parent.get("source"),
        "pages": parent.get("pages"),
        "title": parent.get("title"),
    }


def maybe_alias_node(nid: str, mention: str, nodes_by_id: dict[str, dict], new_nodes: list[dict]) -> None:
    if nid in nodes_by_id:
        return
    prefix, _, rest = nid.partition(":")
    ntype = {"inv": "investigation", "fuel": "fuel", "hw": "hardware", "family": "family", "role": "role"}.get(prefix)
    if not ntype:
        return
    node = {"id": nid, "type": ntype, "label": mention, "alias": True}
    nodes_by_id[nid] = node
    new_nodes.append(node)


def main() -> int:
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    profiles = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    parents = load_jsonl(PARENTS_PATH)
    children = load_jsonl(CHILDREN_PATH)

    nodes: list[dict] = list(graph["nodes"])
    edges: list[dict] = list(graph["edges"])
    nodes_by_id = {node["id"]: node for node in nodes}
    known_inv = {node["id"] for node in nodes if node["type"] == "investigation"}

    aliases = build_gazetteer(graph, catalog, profiles)
    pattern, mapping = compile_gazetteer(aliases)
    pending_nodes: list[dict] = []

    parent_ids = {row["id"] for row in parents}
    for parent in parents:
        node = document_node(parent)
        if node["id"] not in nodes_by_id:
            nodes.append(node)
            nodes_by_id[node["id"]] = node

    in_document = 0
    mentions = 0
    chunk_count = 0
    samples: list[tuple[str, str, str]] = []

    for child in children:
        cid = child["id"]
        node = chunk_node(child)
        if cid not in nodes_by_id:
            nodes.append(node)
            nodes_by_id[cid] = node
        chunk_count += 1

        parent_id = child.get("parent_id")
        if parent_id and parent_id in parent_ids:
            add_edge(edges, cid, parent_id, "PART_OF")

        psi = str(child.get("psi") or "")
        inv = f"inv:{psi}"
        if inv in known_inv:
            add_edge(edges, cid, inv, "IN_DOCUMENT")
            in_document += 1

        for nid, surface in extract_mentions(child.get("text") or "", pattern, mapping, known_inv):
            maybe_alias_node(nid, surface, nodes_by_id, pending_nodes)
            if nid not in nodes_by_id:
                continue
            before = len(edges)
            add_edge(edges, cid, nid, "MENTIONS", text=surface)
            if len(edges) > before:
                mentions += 1
                if len(samples) < 3 and psi == "PSI-69":
                    samples.append((cid, nid, surface))

    for extra in pending_nodes:
        if extra["id"] not in {n["id"] for n in nodes}:
            nodes.append(extra)

    payload = {"nodes": nodes, "edges": edges}
    raw = json.dumps(payload, indent=2) + "\n"
    if len(raw.encode("utf-8")) > MAX_GRAPH_BYTES:
        # Safety: never embed child text; payload already omits it.
        raw = json.dumps(payload, separators=(",", ":")) + "\n"

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(raw, encoding="utf-8")

    print(f"chunk nodes {chunk_count}")
    print(f"IN_DOCUMENT {in_document}")
    print(f"MENTIONS {mentions}")
    print("sample MENTIONS PSI-69/FLEX:")
    if not samples:
        print("  (none)")
    for cid, nid, surface in samples:
        print(f"  {cid} MENTIONS {nid} ({surface})")
    print(f"wrote {OUT_PATH}")
    print(f"graph bytes {OUT_PATH.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
