#!/usr/bin/env python3
"""Partition crawled NASA web/PDF Markdown (data/crawl) for the graph pipeline.

Reads  data/crawl/manifest.json + data/crawl/*/index.md + images/*.md
Writes
  data/crawl/partition.json         groups -> docs, PSI ids, counts; per-doc metadata
  data/crawl/PARTITION.md           human-readable summary
  data/chunks/web_parents.jsonl     one record per crawled document
  data/chunks/web_children.jsonl    retrieval units (sections / pages / image OCR)

Chunk schema matches data/chunks/{parents,children}.jsonl (see data/chunks/README.md)
plus web fields: url, kind, doc_type, group, psi_ids, mentions, chunk_type.
`psi` is "web:<group>" so web chunks never collide with PSI archive chunks;
link to Investigation nodes through `psi_ids`.

Stdlib only. Safe to re-run: all outputs are overwritten.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CRAWL = ROOT / "data" / "crawl"
CHUNKS = ROOT / "data" / "chunks"

TOKEN_MULT = 1.3
MERGE_TARGET = 400
TINY = 200
MAX_TOKENS = 800

# group key -> label, PSI ids in data/psi, hardware/facility, mention regex (case-sensitive)
GROUPS: dict[str, dict] = {
    "acme":    {"label": "Advanced Combustion via Microgravity Experiments (ACME)",
                "psi_ids": ["PSI-10", "PSI-20", "PSI-21", "PSI-22", "PSI-23", "PSI-159", "ACME_common"],
                "hardware": "CIR", "pattern": r"\bACME\b"},
    "bass":    {"label": "Burning and Suppression of Solids (BASS / BASS-II)",
                "psi_ids": ["PSI-26", "PSI-25", "PSI-62"], "hardware": "MSG", "pattern": r"\bBASS(?:-II)?\b"},
    "daft":    {"label": "Dust and Aerosol Measurement Feasibility Test (DAFT)",
                "psi_ids": ["PSI-47"], "hardware": "ISS cabin", "pattern": r"\bDAFT\b"},
    "flex":    {"label": "Flame Extinguishment Experiment (FLEX / FLEX-2)",
                "psi_ids": ["PSI-69", "PSI-68"], "hardware": "CIR + MDCA", "pattern": r"\bFLEX(?:-2)?\b"},
    "mdca":    {"label": "Multi-user Droplet Combustion Apparatus (MDCA)",
                "psi_ids": ["PSI-39", "PSI-68", "PSI-69"], "hardware": "CIR",
                "pattern": r"\bMDCA\b|Multi-[Uu]ser Droplet Combustion"},
    "saffire": {"label": "Spacecraft Fire Safety Demonstration (Saffire)",
                "psi_ids": ["PSI-98", "PSI-99", "PSI-100"], "hardware": "Cygnus", "pattern": r"\bS[Aa][Ff][Ff][Ii][Rr][Ee]\b"},
    "same":    {"label": "Smoke Aerosol Measurement Experiment (SAME)",
                "psi_ids": ["PSI-102", "PSI-101", "PSI-115"], "hardware": "MSG", "pattern": r"\bSAME(?:-R)?\b"},
    "slice":   {"label": "Structure & Liftoff in Combustion Experiment (SLICE)",
                "psi_ids": ["PSI-106"], "hardware": "MSG", "pattern": r"\bSLICE\b"},
    "spice":   {"label": "Smoke Point in Co-flow Experiment (SPICE)",
                "psi_ids": ["PSI-107", "PSI-60"], "hardware": "MSG", "pattern": r"\bSPICE\b"},
    "cfi":     {"label": "Cool Flames Investigation (CFI)",
                "psi_ids": ["PSI-39", "PSI-159"], "hardware": "CIR", "pattern": r"\bCFI(?:-G)?\b|[Cc]ool [Ff]lames? [Ii]nvestigation"},
    "sofie":   {"label": "Solid Fuel Ignition and Extinction (SoFIE)",
                "psi_ids": [], "hardware": "CIR", "pattern": r"\bSo[Ff]IE\b|\bSOFIE\b"},
    "fm2":     {"label": "Flammability of Materials on the Moon (FM2)",
                "psi_ids": [], "hardware": "Lunar lander", "pattern": r"\bFM2\b"},
    "flare":   {"label": "Flammability Limits at Reduced-g Experiment (FLARE)",
                "psi_ids": [], "hardware": "Lunar / reduced g", "pattern": r"\bFLARE\b"},
    "cir":     {"label": "Combustion Integrated Rack / Fluids & Combustion Facility (CIR / FCF)",
                "psi_ids": [], "hardware": "CIR", "pattern": r"\bCIR\b|Combustion Integrated Rack|\bFCF\b"},
    "program": {"label": "Combustion science program, operations and news",
                "psi_ids": [], "hardware": None, "pattern": None},
}

# seed slug -> (group, doc_type). Subpages ("seed--tail") inherit the seed's group.
SEEDS = {
    "combustion-science": ("program", "program_overview"),
    "combustion-focus": ("program", "program_overview"),
    "gipoc": ("program", "operations"),
    "news-combustion-arrives": ("program", "news"),
    "news-lab-demolition": ("program", "news"),
    "fm2": ("fm2", "investigation_page"),
    "sofie": ("sofie", "investigation_page"),
    "sofie-mission": ("sofie", "mission_page"),
    "sofie-stories": ("sofie", "stories"),
    "flare": ("flare", "investigation_page"),
    "cir": ("cir", "facility"),
    "mdca": ("mdca", "facility"),
    "mdca-image": ("mdca", "image_article"),
    "acme": ("acme", "factsheet"), "bass": ("bass", "factsheet"), "daft": ("daft", "factsheet"),
    "flex": ("flex", "factsheet"), "saffire": ("saffire", "factsheet"), "same": ("same", "factsheet"),
    "slice": ("slice", "factsheet"), "spice": ("spice", "factsheet"),
}
MENTION_RES = {k: re.compile(v["pattern"]) for k, v in GROUPS.items() if v["pattern"]}
IMG_LINE = re.compile(r"!\[([^\]]*)\]\((images/[^)]+)\)(?:\s*—\s*\[image notes\]\([^)]*\))?")
EMPTY_LINK = re.compile(r"\[\s*\]\([^)]*\)")


def tokens(text: str) -> int:
    return int(len(text.split()) * TOKEN_MULT)


def front_matter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---", 4)
    meta = {}
    for line in raw[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            try:
                meta[k.strip()] = json.loads(v.strip())
            except json.JSONDecodeError:
                meta[k.strip()] = v.strip()
    return meta, raw[end + 4:].lstrip("\n")


def mentions(text: str) -> dict[str, int]:
    out = {}
    for key, rx in MENTION_RES.items():
        n = len(rx.findall(text))
        if n:
            out[key] = n
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def clean_text(text: str, slug: str) -> tuple[str, list[str]]:
    """Replace image lines with [figure: file] tags; return text + figure paths."""
    figures: list[str] = []

    def repl(m: re.Match) -> str:
        path = f"data/crawl/{slug}/{m.group(2)}"
        figures.append(path)
        alt = m.group(1).strip()
        return f"[figure: {path}" + (f" | alt: {alt}" if alt else "") + "]"

    text = IMG_LINE.sub(repl, text)
    text = EMPTY_LINK.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, figures


def split_units(body: str, kind: str) -> list[dict]:
    """PDF -> one unit per '## Page N'; HTML -> one unit per heading section."""
    units: list[dict] = []
    if kind == "pdf":
        parts = re.split(r"(?m)^## Page (\d+)\s*$", body)
        for i in range(1, len(parts), 2):
            units.append({"page": int(parts[i]), "text": parts[i + 1].strip()})
    else:
        parts = re.split(r"(?m)^(?=#{1,5} )", body)
        for part in parts:
            if part.strip():
                units.append({"page": None, "text": part.strip()})
    return [u for u in units if u["text"]]


def split_long(unit: dict) -> list[dict]:
    if tokens(unit["text"]) <= MAX_TOKENS:
        return [unit]
    out, buf = [], []
    for para in re.split(r"\n\s*\n", unit["text"]):
        if buf and tokens("\n\n".join(buf + [para])) > MAX_TOKENS:
            out.append({**unit, "text": "\n\n".join(buf)})
            buf = []
        buf.append(para)
    if buf:
        out.append({**unit, "text": "\n\n".join(buf)})
    return out


def merge_units(units: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for u in units:
        u = {**u, "pages": [u["page"]] if u["page"] else []}
        if merged:
            last = merged[-1]
            lt, ut = tokens(last["text"]), tokens(u["text"])
            if (lt < TINY or ut < TINY) and lt + ut <= MERGE_TARGET:
                last["text"] += "\n\n" + u["text"]
                last["pages"] += u["pages"]
                continue
        merged.append(u)
    out = []
    for u in merged:
        out.extend(split_long(u))
    return out


def image_chunks(slug: str, doc: dict, parent_id: str) -> list[dict]:
    rows = []
    for md in sorted((CRAWL / slug / "images").glob("img-*.md")):
        meta, body = front_matter(md.read_text(encoding="utf-8"))
        ocr = re.search(r"```text\n(.*?)\n```", body, re.S)
        llm = re.search(r"## Content \(LLM transcription\)\n(.*?)(?=\n## |\Z)", body, re.S)
        cap = re.search(r"## Caption\n(.*?)(?=\n## |\Z)", body, re.S)
        content = "\n\n".join(x.group(1).strip() for x in (cap, llm, ocr) if x)
        if not (ocr or llm):  # nothing beyond a caption: the figure tag in the page chunk is enough
            continue
        fig = f"data/crawl/{slug}/images/{meta.get('file')}"
        title = body.splitlines()[0].lstrip("# ").strip() if body else meta.get("file")
        text = f"[figure: {fig}]\n\n# {title}\n\n{content}"
        rows.append({"chunk_type": "image", "page": meta.get("page"), "pages": [meta["page"]] if meta.get("page") else [],
                     "text": text, "figures": [fig], "ocr": bool(ocr), "id_suffix": md.stem})
    return rows


def main() -> int:
    manifest = json.loads((CRAWL / "manifest.json").read_text(encoding="utf-8"))["sources"]
    docs, parents, children = {}, [], []
    seen_bodies: dict[str, str] = {}

    for slug, entry in sorted(manifest.items()):
        if entry.get("status") != "ok" or not (CRAWL / slug / "index.md").exists():
            docs[slug] = {"status": entry.get("status"), "error": entry.get("error"), "url": entry.get("url")}
            continue
        seed = slug.split("--")[0]
        group, doc_type = SEEDS.get(seed, ("program", "page"))
        if seed != slug:
            doc_type = "linked_pdf" if entry.get("kind") == "pdf" else "subpage"
        meta, body = front_matter((CRAWL / slug / "index.md").read_text(encoding="utf-8"))
        body_hash = sha1(re.sub(r"\s+", " ", body).encode()).hexdigest()
        duplicate_of = seen_bodies.get(body_hash)
        seen_bodies.setdefault(body_hash, slug)

        text, figures = clean_text(body, slug)
        found = mentions(text)
        related = [g for g, n in found.items() if g != group and n >= 2]
        psi_ids = list(GROUPS[group]["psi_ids"])
        parent_id = f"web/{slug}"
        docs[slug] = {
            "status": "ok", "title": entry.get("title"), "url": entry.get("url"),
            "kind": entry.get("kind"), "doc_type": doc_type, "group": group,
            "psi_ids": psi_ids, "related_groups": related, "mentions": found,
            "pages": entry.get("pages"), "images": entry.get("images", 0),
            "tokens": tokens(text), "duplicate_of": duplicate_of,
        }
        if duplicate_of:
            continue

        units = merge_units(split_units(text, entry.get("kind")))
        rows = [{**u, "chunk_type": "page" if entry.get("kind") == "pdf" else "section",
                 "figures": re.findall(r"\[figure: ([^\]|]+)", u["text"]), "ocr": False}
                for u in units]
        rows += image_chunks(slug, docs[slug], parent_id)
        child_ids = []
        for i, row in enumerate(rows, 1):
            cid = f"{parent_id}/{row.pop('id_suffix', None) or f'c{i:03d}'}"
            pages = row.pop("pages") or []
            child_ids.append(cid)
            children.append({
                "id": cid, "parent_id": parent_id, "psi": f"web:{group}", "source": f"data/crawl/{slug}/index.md",
                "doc_id": slug, "page": row["page"],
                "pages_span": [min(pages), max(pages)] if pages else [None, None],
                "figures": row["figures"], "ocr": row["ocr"], "text": row["text"],
                "token_estimate": tokens(row["text"]),
                "url": entry.get("url"), "kind": entry.get("kind"), "doc_type": doc_type,
                "group": group, "psi_ids": psi_ids, "mentions": mentions(row["text"]),
                "chunk_type": row["chunk_type"],
            })
        parents.append({
            "id": parent_id, "psi": f"web:{group}", "source": f"data/crawl/{slug}/index.md",
            "doc_id": slug, "pages": entry.get("pages") or 1, "child_ids": child_ids,
            "title": entry.get("title"), "url": entry.get("url"), "kind": entry.get("kind"),
            "doc_type": doc_type, "group": group, "psi_ids": psi_ids,
            "related_groups": related, "figures": figures,
        })
        docs[slug]["chunks"] = len(child_ids)

    groups = {}
    for key, g in GROUPS.items():
        members = [s for s, d in docs.items() if d.get("group") == key]
        mentioned_in = [s for s, d in docs.items() if key in d.get("related_groups", [])]
        groups[key] = {
            "label": g["label"], "psi_ids": g["psi_ids"], "hardware": g["hardware"],
            "docs": members, "mentioned_in": mentioned_in,
            "chunks": sum(docs[s].get("chunks", 0) for s in members),
            "images": sum(docs[s].get("images", 0) for s in members),
            "tokens": sum(docs[s].get("tokens", 0) for s in members),
        }
    stats = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "docs_ok": sum(1 for d in docs.values() if d.get("status") == "ok"),
        "docs_failed": [s for s, d in docs.items() if d.get("status") != "ok"],
        "duplicates": {s: d["duplicate_of"] for s, d in docs.items() if d.get("duplicate_of")},
        "parents": len(parents), "children": len(children),
        "chunk_types": dict(Counter(c["chunk_type"] for c in children)),
        "tokens": sum(c["token_estimate"] for c in children),
    }

    CHUNKS.mkdir(parents=True, exist_ok=True)
    with (CHUNKS / "web_parents.jsonl").open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(p, ensure_ascii=False) + "\n" for p in parents)
    with (CHUNKS / "web_children.jsonl").open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(c, ensure_ascii=False) + "\n" for c in children)
    (CRAWL / "partition.json").write_text(
        json.dumps({"stats": stats, "groups": groups, "docs": docs}, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = ["# Crawl partition", "", f"Generated {stats['generated_at']} by `backend/scripts/partition_crawl.py`.", "",
             f"Docs ok: {stats['docs_ok']} · parents: {stats['parents']} · children: {stats['children']} "
             f"({', '.join(f'{k}={v}' for k, v in stats['chunk_types'].items())}) · ~{stats['tokens']} tokens", "",
             "| Group | PSI ids | Docs | Chunks | Images | Also mentioned in |", "|---|---|---|---|---|---|"]
    for key, g in groups.items():
        if not g["docs"] and not g["mentioned_in"]:
            continue
        lines.append(f"| **{key}** — {g['label']} | {', '.join(g['psi_ids']) or '—'} | {', '.join(g['docs']) or '—'} "
                     f"| {g['chunks']} | {g['images']} | {', '.join(g['mentioned_in']) or '—'} |")
    if stats["docs_failed"]:
        lines += ["", "Failed: " + ", ".join(stats["docs_failed"])]
    if stats["duplicates"]:
        lines += ["", "Duplicates (not chunked): " + ", ".join(f"{a} = {b}" for a, b in stats["duplicates"].items())]
    (CRAWL / "PARTITION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
