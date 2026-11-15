#!/usr/bin/env python3
"""LLM entity + relationship extraction for GraphRAG.

Writes MERGE-only Neo4j nodes/rels. Does not overwrite RELEVANT_TO or
Investigation oxygen properties. Skips short and drawing-like chunks.

Extracted node ids are `{type}:{slug}@{psi}` (see app/graphrag.py). Catalog
Investigation/Scenario/Role ids stay global. `--with-communities` runs
hierarchical Leiden/Louvain after extract. Chunk extract resumes from
`backend/var/graphrag_extract.jsonl`. Community LLM summaries resume from
`backend/var/graphrag_community.jsonl` (separate file; do not reuse --checkpoint).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.graphrag import (  # noqa: E402
    CHILDREN_PATH,
    EXTRACT_SYSTEM,
    chunk_first_source,
    chunk_pages,
    eligible_chunks,
    extraction_prompt,
    load_gazetteer,
    load_jsonl,
    normalize_psi_token,
    normalize_type,
    parse_items,
    resolve_entity,
    scope_psi,
)
from app.ontology import map_relation, schema_version  # noqa: E402
from app.llm import complete_json, provider_label, quota_wait_seconds  # noqa: E402
from app.neo4j_config import connect, require_aura  # noqa: E402

CHECKPOINT = ROOT / "backend" / "var" / "graphrag_extract.jsonl"
STATS_PATH = ROOT / "backend" / "var" / "graphrag_extract_stats.json"

APPEND_LISTS = (
    "n.aliases = coalesce(n.aliases, []) + "
    "[x IN coalesce(row.aliases, []) WHERE NOT x IN coalesce(n.aliases, [])], "
    "n.source_chunks = coalesce(n.source_chunks, []) + "
    "[x IN coalesce(row.source_chunks, []) WHERE NOT x IN coalesce(n.source_chunks, [])], "
    "n.pages = coalesce(n.pages, []) + "
    "[x IN coalesce(row.pages, []) WHERE NOT x IN coalesce(n.pages, [])]"
)


def batches(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def load_done(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.is_file():
        return done
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = row.get("chunk_id")
            if cid:
                done.add(cid)
    return done


def _unique(values: list) -> list:
    out = []
    seen = set()
    for value in values:
        key = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def ensure_scoped_id(entity: dict, psi: str) -> dict:
    """Catalog inv/scenario/role stay global; every other extracted id must include @{psi}."""
    prefix = str(entity.get("type") or "")
    if prefix in {"inv", "scenario", "role"}:
        return entity
    nid = str(entity.get("id") or "")
    if "@" in nid:
        entity["psi"] = entity.get("psi") or psi
        return entity
    name_part = nid.split(":", 1)[-1] if ":" in nid else nid
    entity["id"] = f"{prefix or 'entity'}:{name_part}@{psi}"
    entity["psi"] = psi
    entity["extracted"] = True
    return entity


def attach_chunk_meta(entity: dict, child: dict, chunk_id: str) -> dict:
    pages = chunk_pages(child)
    source = chunk_first_source(child)
    aliases = list(entity.get("aliases") or [])
    if entity.get("label") and entity["label"] not in aliases:
        aliases.insert(0, entity["label"])
    entity = dict(entity)
    entity["aliases"] = _unique(aliases)
    entity["source_chunks"] = [chunk_id]
    entity["pages"] = pages
    entity["first_source"] = source
    if entity.get("extracted") is None:
        entity["extracted"] = entity.get("type") not in {"inv", "scenario", "role"}
    return entity


def inv_psi_from_text(text: str, mapping: dict[str, str]) -> str | None:
    token = normalize_psi_token(text)
    if token:
        return token
    folded = (text or "").casefold()
    best_id = None
    best_len = 0
    for alias, nid in mapping.items():
        if not isinstance(nid, str) or not nid.startswith("inv:"):
            continue
        if not alias or alias.startswith("__") or len(alias) < 4:
            continue
        if alias in folded and len(alias) > best_len:
            best_id = nid.removeprefix("inv:")
            best_len = len(alias)
    return best_id


def chunk_psi_hint(child: dict, raw_entities: list, mapping, known) -> str:
    psi = scope_psi(child)
    if normalize_psi_token(psi):
        return psi
    for ent in raw_entities:
        if not isinstance(ent, dict):
            continue
        etype = normalize_type(str(ent.get("type") or ""), str(ent.get("name") or ""))
        name = str(ent.get("name") or ent.get("normalized") or "")
        if etype != "Investigation":
            continue
        resolved = resolve_entity(name, etype, mapping, known, chunk=child)
        if resolved and str(resolved["id"]).startswith("inv:"):
            return resolved["id"].removeprefix("inv:")
    hinted = inv_psi_from_text(str(child.get("text") or ""), mapping)
    return hinted or psi


def normalize_extraction(
    raw_items: list[dict], mapping, known, children_by_id: dict[str, dict]
) -> list[dict]:
    out: list[dict] = []
    for item in raw_items:
        cid = str(item.get("chunk_id") or item.get("id") or "")
        child = children_by_id.get(cid) or {"id": cid}
        raw_ents = [ent for ent in (item.get("entities") or []) if isinstance(ent, dict)]
        psi = chunk_psi_hint(child, raw_ents, mapping, known)
        entities = []
        resolved_by_name: dict[str, dict] = {}
        for ent in raw_ents:
            name = str(ent.get("name") or ent.get("normalized") or "")
            etype = normalize_type(str(ent.get("type") or ""), name)
            if not etype:
                continue
            resolved = resolve_entity(name, etype, mapping, known, psi=psi, chunk=child)
            if not resolved:
                continue
            resolved = ensure_scoped_id(resolved, psi)
            if resolved.get("type") not in {"inv", "scenario", "role"}:
                if "@" not in str(resolved.get("id") or ""):
                    continue
            resolved = attach_chunk_meta(resolved, child, cid)
            entities.append(resolved)
            resolved_by_name[name.casefold()] = resolved
            resolved_by_name[resolved["label"].casefold()] = resolved
            resolved_by_name[resolved["id"].casefold()] = resolved
        rels = []
        for rel in item.get("relationships") or []:
            if not isinstance(rel, dict):
                continue
            rtype = map_relation(str(rel.get("type") or ""))
            if not rtype:
                continue
            src = resolved_by_name.get(str(rel.get("source") or "").casefold())
            dst = resolved_by_name.get(str(rel.get("target") or "").casefold())
            if not src or not dst or src["id"] == dst["id"]:
                continue
            rels.append(
                {
                    "source": src["id"],
                    "target": dst["id"],
                    "type": rtype,
                    "source_chunk": cid,
                }
            )
        out.append({"chunk_id": cid, "entities": entities, "relationships": rels})
    return out


def ensure_extra_schema(session) -> None:
    for label in ("Sensor", "Phenomenon", "Suppressant", "Location", "Community"):
        session.run(
            f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        )


def pack_entities(entities: list[dict]) -> list[dict]:
    packed: dict[str, dict] = {}
    for ent in entities:
        nid = ent["id"]
        cur = packed.get(nid)
        if not cur:
            packed[nid] = {
                "id": nid,
                "label": ent["label"],
                "type": ent.get("type"),
                "psi": ent.get("psi"),
                "psiId": nid.removeprefix("inv:") if str(nid).startswith("inv:") else None,
                "aliases": list(ent.get("aliases") or ([ent["label"]] if ent.get("label") else [])),
                "source_chunks": list(ent.get("source_chunks") or []),
                "pages": list(ent.get("pages") or []),
                "first_source": ent.get("first_source") or "",
                "canonical": ent.get("canonical"),
                "extracted": bool(ent.get("extracted", True)),
                "neo4j": ent["neo4j"],
                "created": bool(ent.get("created")),
            }
            continue
        cur["aliases"] = _unique(cur["aliases"] + list(ent.get("aliases") or []))
        cur["source_chunks"] = _unique(cur["source_chunks"] + list(ent.get("source_chunks") or []))
        cur["pages"] = _unique(cur["pages"] + list(ent.get("pages") or []))
        if not cur.get("first_source"):
            cur["first_source"] = ent.get("first_source") or ""
        if not cur.get("canonical"):
            cur["canonical"] = ent.get("canonical")
        cur["created"] = cur["created"] or bool(ent.get("created"))
    return list(packed.values())


def merge_graph(session, entities: list[dict], rels: list[dict], batch_size: int) -> tuple[int, int]:
    rows = pack_entities(entities)
    by_label: dict[str, list[dict]] = defaultdict(list)
    created = 0
    for ent in rows:
        if ent.get("created"):
            created += 1
        by_label[ent["neo4j"]].append(ent)

    for label, group in by_label.items():
        if label == "Investigation":
            query = (
                "UNWIND $rows AS row "
                "MERGE (n:Investigation {id: row.id}) "
                "ON CREATE SET n.psiId = row.psiId, n.label = row.label "
                "ON MATCH SET " + APPEND_LISTS
            )
        else:
            query = (
                f"UNWIND $rows AS row "
                f"MERGE (n:{label} {{id: row.id}}) "
                f"ON CREATE SET n.label = row.label, n.type = row.type, n.psi = row.psi, "
                f"n.aliases = row.aliases, n.source_chunks = row.source_chunks, "
                f"n.pages = row.pages, n.first_source = row.first_source, "
                f"n.extracted = true, n.canonical = row.canonical "
                f"ON MATCH SET n.label = coalesce(n.label, row.label), "
                f"n.type = coalesce(n.type, row.type), "
                f"n.psi = coalesce(n.psi, row.psi), "
                f"n.first_source = coalesce(n.first_source, row.first_source), "
                f"n.canonical = coalesce(n.canonical, row.canonical), "
                f"n.extracted = true, {APPEND_LISTS}"
            )
        for chunk in batches(group, batch_size):
            session.run(query, rows=chunk)

    canonical_rows = [
        {
            "chunk_id": cid,
            "canonical": ent.get("canonical"),
            "label": ent.get("label"),
        }
        for ent in entities
        for cid in (ent.get("source_chunks") or [])
        if ent.get("canonical")
    ]
    if canonical_rows:
        session.run(
            "UNWIND $rows AS row "
            "MATCH (c:Chunk {id: row.chunk_id}) "
            "MATCH (g {id: row.canonical}) "
            "MERGE (c)-[:MENTIONS {text: row.label, extracted: true}]->(g)",
            rows=canonical_rows,
        )

    rel_n = 0
    by_type: dict[str, list[dict]] = defaultdict(list)
    for rel in rels:
        by_type[rel["type"]].append(rel)
    for rtype, group in by_type.items():
        query = (
            f"UNWIND $rows AS row "
            f"MATCH (a {{id: row.source}}) "
            f"MATCH (b {{id: row.target}}) "
            f"MERGE (a)-[r:{rtype} {{source_chunk: row.source_chunk}}]->(b)"
        )
        for chunk in batches(group, batch_size):
            session.run(query, rows=chunk)
            rel_n += len(chunk)
        mention = (
            f"UNWIND $rows AS row "
            f"MATCH (c:Chunk {{id: row.source_chunk}}) "
            f"MATCH (b {{id: row.target}}) "
            f"MERGE (c)-[:MENTIONS {{text: b.label, extracted: true}}]->(b)"
        )
        for chunk in batches(group, batch_size):
            session.run(mention, rows=chunk)
    return created, rel_n


def wipe_extracted(session) -> dict[str, int]:
    """Remove LLM extract overlay. Keep catalog Investigation cores, chunks, documents."""
    rels = session.run(
        """
        MATCH ()-[r]->()
        WHERE (r.source_chunk IS NOT NULL OR r.extracted = true)
          AND NOT type(r) IN ['RELEVANT_TO', 'IN_DOCUMENT', 'PART_OF']
        WITH collect(r) AS rels
        FOREACH (r IN rels | DELETE r)
        RETURN size(rels) AS n
        """
    ).single()["n"]
    nodes = session.run(
        """
        MATCH (n)
        WHERE n.extracted = true
          AND NOT n:Investigation
          AND NOT n:Chunk
          AND NOT n:Document
          AND NOT n:Family
          AND NOT n:Role
          AND NOT n:Scenario
        WITH collect(n) AS nodes
        FOREACH (n IN nodes | DETACH DELETE n)
        RETURN size(nodes) AS n
        """
    ).single()["n"]
    communities = session.run(
        """
        MATCH (c:Community)
        WITH collect(c) AS nodes
        FOREACH (c IN nodes | DETACH DELETE c)
        RETURN size(nodes) AS n
        """
    ).single()["n"]
    session.run(
        """
        MATCH (i:Investigation)
        WHERE i.extracted IS NOT NULL
        REMOVE i.extracted
        """
    )
    return {"rels": int(rels or 0), "nodes": int(nodes or 0), "communities": int(communities or 0)}


EXTRACT_LLM_TIMEOUT = 120


def extract_batch(batch: list[dict], mapping, known) -> list[dict]:
    payload = complete_json(
        EXTRACT_SYSTEM, extraction_prompt(batch), timeout=EXTRACT_LLM_TIMEOUT
    )
    items = parse_items(payload)
    by_id = {str(item.get("chunk_id") or item.get("id") or ""): item for item in items}
    aligned = []
    children_by_id = {child["id"]: child for child in batch}
    for child in batch:
        aligned.append(by_id.get(child["id"], {"chunk_id": child["id"], "entities": [], "relationships": []}))
    return normalize_extraction(aligned, mapping, known, children_by_id)


def _is_quota_error(exc: BaseException) -> bool:
    text = str(exc)
    return "HTTP 429" in text or "rate_limit_error" in text or "Token quota exceeded" in text


def extract_batch_wait_429(batch: list[dict], mapping, known) -> list[dict]:
    """Retry the same batch after sleeping on 429; do not skip or checkpoint."""
    while True:
        try:
            return extract_batch(batch, mapping, known)
        except Exception as exc:  # noqa: BLE001
            if not _is_quota_error(exc):
                raise
            wait = quota_wait_seconds(str(exc))
            print(f"HTTP 429 quota; sleeping {int(wait)}s until reset (not skipping)", flush=True)
            time.sleep(wait)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--neo4j-batch", type=int, default=200)
    parser.add_argument("--with-communities", action="store_true")
    parser.add_argument("--reset-extracted", action="store_true")
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    args, _unknown = parser.parse_known_args()
    checkpoint: Path = args.checkpoint

    children = load_jsonl(CHILDREN_PATH)
    eligible = eligible_chunks(children)
    print(f"children {len(children)} eligible {len(eligible)} skipped {len(children) - len(eligible)}")

    api = provider_label()
    if not api:
        print(
            "NO LLM API KEY (checked ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, "
            "OPENAI_API_KEY, GROK_API_KEY, XAI_API_KEY, GEMINI_API_KEY)"
        )
        print("API used: none")
        print("entities added 0")
        print("rels added 0")
        STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATS_PATH.write_text(
            json.dumps(
                {
                    "api": None,
                    "eligible": len(eligible),
                    "entities_added": 0,
                    "rels_added": 0,
                    "extracted_chunks": 0,
                    "skipped_no_llm": True,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if args.with_communities:
            sys.path.insert(0, str(ROOT / "backend" / "scripts"))
            from community import main as community_main  # type: ignore

            return community_main()
        return 0

    print(f"API used: {api}")
    print(f"ontology {schema_version()}")

    done = load_done(checkpoint)
    todo = [row for row in eligible if row["id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"todo {len(todo)} already {len(done)}")

    def _run_communities() -> int:
        sys.path.insert(0, str(ROOT / "backend" / "scripts"))
        from community import main as community_main  # type: ignore

        return community_main()

    if not args.reset_extracted and not todo:
        print("extract checkpoint already complete; skipping Aura extract")
        print("entities added 0")
        print("rels added 0")
        if args.with_communities:
            return _run_communities()
        return 0

    _pattern, mapping, known = load_gazetteer()
    del _pattern

    uri, user, password, database = require_aura()
    print(f"connecting {uri} database={database}")
    driver = connect(uri, user, password)

    if args.reset_extracted:
        with driver.session(database=database) as session:
            wipe_stats = wipe_extracted(session)
        print(
            f"reset extracted overlay nodes={wipe_stats['nodes']} rels={wipe_stats['rels']} "
            f"communities={wipe_stats['communities']}"
        )

    entities_added = 0
    rels_added = 0
    extracted_chunks = 0
    checkpoint.parent.mkdir(parents=True, exist_ok=True)

    with driver:
        with driver.session(database=database) as session:
            ensure_extra_schema(session)
            for batch in batches(todo, args.batch_size):
                try:
                    rows = extract_batch_wait_429(batch, mapping, known)
                except Exception as exc:  # noqa: BLE001
                    print(f"batch failed ({exc}); retrying one chunk at a time")
                    rows = []
                    for child in batch:
                        try:
                            rows.extend(extract_batch_wait_429([child], mapping, known))
                        except Exception as inner:  # noqa: BLE001
                            print(f"chunk {child['id']} failed ({inner}); skipping")
                if not rows:
                    continue
                ents = [ent for row in rows for ent in row["entities"]]
                rels = [rel for row in rows for rel in row["relationships"]]
                created, rel_n = merge_graph(session, ents, rels, args.neo4j_batch)
                entities_added += created
                rels_added += rel_n
                extracted_chunks += len(rows)
                with checkpoint.open("a", encoding="utf-8") as handle:
                    for row in rows:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                sample = next((ent["id"] for ent in ents if "@" in str(ent.get("id"))), "")
                print(
                    f"extracted {extracted_chunks} new_nodes {entities_added} "
                    f"rels {rels_added} sample {sample or '-'}"
                )

    stats = {
        "api": api,
        "eligible": len(eligible),
        "entities_added": entities_added,
        "rels_added": rels_added,
        "extracted_chunks": extracted_chunks,
        "skipped_no_llm": False,
        "checkpoint": str(checkpoint),
    }
    STATS_PATH.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(f"entities added {entities_added}")
    print(f"rels added {rels_added}")
    if args.with_communities:
        return _run_communities()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
