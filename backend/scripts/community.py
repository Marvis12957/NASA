#!/usr/bin/env python3
"""Hierarchical Louvain/Leiden communities over the Aura entity graph (no GDS).

C0 = fine clusters (tight themes). C1 = coarser pillars. C2 if the graph is large.
Writes Community nodes {id, level, parent_id, size, summary}, IN_COMMUNITY from
entities to C0, PARENT_COMMUNITY between levels.
Does not touch RELEVANT_TO scores, Chunk embeddings, or Investigation oxygen.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from app.llm import complete_text, provider_label  # noqa: E402
from app.prompts import COMMUNITY_SUMMARY_SYSTEM  # noqa: E402
from app.neo4j_config import connect, require_aura  # noqa: E402

COMMUNITY_CHECKPOINT = ROOT / "backend" / "var" / "graphrag_community.jsonl"
STATS_PATH = ROOT / "backend" / "var" / "graphrag_community_stats.json"

ENTITY_LABELS = (
    "Investigation",
    "Fuel",
    "Hardware",
    "Family",
    "Sensor",
    "Phenomenon",
    "Suppressant",
    "Location",
    "Role",
    "Scenario",
)
SKIP_REL = {
    "RELEVANT_TO",
    "INFORMS",
    "USES_ATMOSPHERE_BOUND",
    "PART_OF",
    "IN_DOCUMENT",
    "IN_COMMUNITY",
    "PARENT_COMMUNITY",
    "HAS_ROLE",
}
SUMMARY_SYSTEM = COMMUNITY_SUMMARY_SYSTEM


def fetch_edges(session) -> list[dict]:
    rows = session.run(
        """
        MATCH (a)-[r]->(b)
        WHERE any(l IN labels(a) WHERE l IN $entityLabels)
          AND any(l IN labels(b) WHERE l IN $entityLabels)
          AND NOT type(r) IN $skip
        RETURN a.id AS src, labels(a)[0] AS srcLabel, coalesce(a.label, a.id) AS srcName,
               coalesce(a.psiId, a.psi) AS srcPsi,
               b.id AS dst, labels(b)[0] AS dstLabel, coalesce(b.label, b.id) AS dstName,
               coalesce(b.psiId, b.psi) AS dstPsi, type(r) AS rel
        """,
        entityLabels=list(ENTITY_LABELS),
        skip=list(SKIP_REL),
    )
    return [dict(row) for row in rows]


def _graph_and_meta(rows: list[dict]):
    import networkx as nx

    graph = nx.Graph()
    meta: dict[str, dict] = {}
    for row in rows:
        for nid, label, name, psi in (
            (row["src"], row["srcLabel"], row["srcName"], row["srcPsi"]),
            (row["dst"], row["dstLabel"], row["dstName"], row["dstPsi"]),
        ):
            if not nid:
                continue
            graph.add_node(nid)
            rec = meta.setdefault(nid, {"id": nid, "label": label, "name": name, "psi": psi})
            if psi:
                rec["psi"] = psi
        if row["src"] and row["dst"] and row["src"] != row["dst"]:
            weight = 2.0 if row["rel"] in {"IN_FAMILY", "USED_HARDWARE", "TESTED_FUEL"} else 1.0
            if graph.has_edge(row["src"], row["dst"]):
                graph[row["src"]][row["dst"]]["weight"] += weight
            else:
                graph.add_edge(row["src"], row["dst"], weight=weight)
    return graph, meta


def _partition(graph, resolution: float, seed: int = 42) -> list[set]:
    import networkx as nx
    from networkx.algorithms.community import louvain_communities

    if graph.number_of_nodes() == 0:
        return []
    try:
        import igraph as ig  # type: ignore
        import leidenalg  # type: ignore

        mapping = {node: index for index, node in enumerate(graph.nodes())}
        inverse = {index: node for node, index in mapping.items()}
        edges = [(mapping[u], mapping[v]) for u, v in graph.edges()]
        weights = [float(graph[u][v].get("weight", 1.0)) for u, v in graph.edges()]
        igraph = ig.Graph(n=len(mapping), edges=edges, directed=False)
        if weights:
            igraph.es["weight"] = weights
        part = leidenalg.find_partition(
            igraph,
            leidenalg.RBConfigurationVertexPartition,
            weights="weight" if weights else None,
            resolution_parameter=resolution,
            seed=seed,
        )
        return [{inverse[i] for i in community} for community in part]
    except Exception:  # noqa: BLE001
        parts = louvain_communities(graph, weight="weight", resolution=resolution, seed=seed)
        return [set(part) for part in parts]


def _community_record(cid: str, level: int, member_ids: list[str], meta: dict, parent_id: str | None) -> dict:
    nodes = [meta[nid] for nid in member_ids if nid in meta]
    rank = {"Investigation": 0, "Family": 1, "Phenomenon": 2, "Hardware": 3, "Fuel": 4}
    nodes.sort(key=lambda node: (rank.get(str(node["label"]), 9), str(node["name"])))
    psi_ids = sorted(
        {
            str(node["psi"] or node["id"].removeprefix("inv:"))
            for node in nodes
            if node["label"] == "Investigation" or str(node["id"]).startswith("inv:")
        }
    )
    names = [str(node["name"]) for node in nodes[:12]]
    title = ", ".join(names[:4]) or cid
    return {
        "id": cid,
        "level": level,
        "parent_id": parent_id,
        "title": title[:180],
        "psiIds": psi_ids,
        "members": [node["id"] for node in nodes],
        "memberNames": names,
        "size": len(member_ids),
        "summary": "",
    }


def _contract(graph, assignment: dict[str, str]):
    import networkx as nx

    super_g = nx.Graph()
    for cid in set(assignment.values()):
        super_g.add_node(cid)
    for u, v, data in graph.edges(data=True):
        cu, cv = assignment.get(u), assignment.get(v)
        if not cu or not cv or cu == cv:
            continue
        weight = float(data.get("weight", 1.0))
        if super_g.has_edge(cu, cv):
            super_g[cu][cv]["weight"] += weight
        else:
            super_g.add_edge(cu, cv, weight=weight)
    return super_g


def communities_from_edges(rows: list[dict]) -> list[dict]:
    """Nested hierarchy: C0 fine, C1 coarser, optional C2 when the graph is large."""
    graph, meta = _graph_and_meta(rows)
    if graph.number_of_nodes() == 0:
        return []

    parts0 = _partition(graph, 1.6)
    if len(parts0) < 4 and graph.number_of_nodes() >= 16:
        parts0 = _partition(graph, 2.4)
    parts0 = sorted(parts0, key=lambda s: (-len(s), min(str(x) for x in s)))

    c0 = []
    node_to_c0: dict[str, str] = {}
    for index, members in enumerate(parts0):
        cid = f"community:L0:{index}"
        for nid in members:
            node_to_c0[nid] = cid
        c0.append(_community_record(cid, 0, sorted(members), meta, None))

    super1 = _contract(graph, node_to_c0)
    parts1 = _partition(super1, 1.0) if super1.number_of_nodes() > 1 else [set(super1.nodes())]
    parts1 = sorted(parts1, key=lambda s: (-len(s), min(str(x) for x in s)))
    c0_by_id = {item["id"]: item for item in c0}
    c1 = []
    c0_to_c1: dict[str, str] = {}
    for index, members in enumerate(parts1):
        cid = f"community:L1:{index}"
        entity_ids = []
        for child_id in members:
            c0_to_c1[child_id] = cid
            entity_ids.extend(c0_by_id.get(child_id, {}).get("members") or [])
        c1.append(_community_record(cid, 1, entity_ids, meta, None))
    for item in c0:
        item["parent_id"] = c0_to_c1.get(item["id"])

    levels = c0 + c1
    if graph.number_of_nodes() >= 80 and len(c1) >= 4:
        c1_to_entities = {item["id"]: item["members"] for item in c1}
        super2 = _contract(super1, c0_to_c1)
        parts2 = _partition(super2, 0.7) if super2.number_of_nodes() > 1 else [set(super2.nodes())]
        parts2 = sorted(parts2, key=lambda s: (-len(s), min(str(x) for x in s)))
        c1_to_c2: dict[str, str] = {}
        c2 = []
        for index, members in enumerate(parts2):
            cid = f"community:L2:{index}"
            entity_ids = []
            for child_id in members:
                c1_to_c2[child_id] = cid
                entity_ids.extend(c1_to_entities.get(child_id) or [])
            c2.append(_community_record(cid, 2, entity_ids, meta, None))
        for item in c1:
            item["parent_id"] = c1_to_c2.get(item["id"])
        levels.extend(c2)
    return levels


def summarize(community: dict) -> str:
    grain = {0: "fine thematic cluster", 1: "coarser fire-safety pillar", 2: "broad hardware/theme family"}.get(
        int(community.get("level") or 0), "cluster"
    )
    user = (
        f"This is a {grain} (level {community.get('level')}). "
        f"Theme candidates from member names: {', '.join(community['memberNames'])}. "
        f"PSI ids: {', '.join(community['psiIds']) or 'none'}. "
        f"Size: {community['size']} nodes."
    )
    text = complete_text(SUMMARY_SYSTEM, user, json_mode=False).strip()
    return " ".join(text.split())[:800]


def fallback_summary(community: dict) -> str:
    psi = ", ".join(community["psiIds"]) or "no PSI ids"
    names = ", ".join(community["memberNames"][:8])
    grain = "pillar" if int(community.get("level") or 0) else "cluster"
    return f"Fire-safety {grain} covering {names}. Investigations: {psi}."


def load_summary_checkpoint(path: Path) -> dict[str, dict]:
    """Last summary per community id. Crash-safe: jsonl is append-only."""
    saved: dict[str, dict] = {}
    if not path.is_file():
        return saved
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = row.get("id")
            summary = str(row.get("summary") or "").strip()
            if cid and summary:
                saved[str(cid)] = row
    return saved


def apply_summary_checkpoint(communities: list[dict], saved: dict[str, dict]) -> int:
    applied = 0
    for item in communities:
        prev = saved.get(item["id"])
        if not prev:
            continue
        prev_members = prev.get("members")
        if prev_members and set(prev_members) != set(item.get("members") or []):
            continue
        item["summary"] = str(prev.get("summary") or "").strip()
        item["source"] = prev.get("source") or "checkpoint"
        applied += 1
    return applied


def append_summary_checkpoint(path: Path, community: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": community["id"],
        "level": community.get("level"),
        "title": community.get("title"),
        "summary": community.get("summary") or "",
        "parent_id": community.get("parent_id"),
        "size": community.get("size"),
        "psiIds": community.get("psiIds") or [],
        "members": community.get("members") or [],
        "source": community.get("source") or "llm",
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def persist_community_summary(session, community: dict) -> None:
    if session is None:
        return
    session.run(
        """
        MATCH (c:Community {id: $id})
        SET c.summary = $summary
        """,
        id=community["id"],
        summary=community.get("summary") or "",
    )


def fill_summaries(
    communities: list[dict],
    *,
    checkpoint: Path,
    skip_summaries: bool = False,
    api: str | None = None,
    session=None,
    persist: bool = True,
) -> dict:
    """Resume LLM summaries from jsonl. Prints community summary i/N."""
    saved = load_summary_checkpoint(checkpoint)
    resumed = apply_summary_checkpoint(communities, saved)
    total = len(communities)
    new_llm = 0
    new_fallback = 0
    for index, item in enumerate(communities, 1):
        cid = item["id"]
        if str(item.get("summary") or "").strip():
            print(f"community summary {index}/{total} {cid} resume", flush=True)
            continue
        if skip_summaries or not api:
            item["summary"] = fallback_summary(item)
            item["source"] = "fallback"
            print(f"community summary {index}/{total} {cid} fallback", flush=True)
            continue
        try:
            item["summary"] = summarize(item)
            item["source"] = "llm"
            new_llm += 1
        except Exception as exc:  # noqa: BLE001
            print(f"summary failed ({exc}); using member list", flush=True)
            item["summary"] = fallback_summary(item)
            item["source"] = "fallback"
            new_fallback += 1
        if persist:
            append_summary_checkpoint(checkpoint, item)
            persist_community_summary(session, item)
        print(f"community summary {index}/{total} {cid} {item['source']}", flush=True)
    return {
        "resumed": resumed,
        "summaries_written": new_llm,
        "fallbacks_written": new_fallback,
        "total": total,
    }


def write_communities(session, communities: list[dict]) -> None:
    session.run("MATCH (c:Community) DETACH DELETE c")
    session.run(
        """
        CREATE CONSTRAINT community_id IF NOT EXISTS
        FOR (n:Community) REQUIRE n.id IS UNIQUE
        """
    )
    rows = [
        {
            "id": item["id"],
            "parent_id": item.get("parent_id"),
            "props": {
                "id": item["id"],
                "title": item["title"],
                "summary": item["summary"],
                "level": item["level"],
                "parent_id": item.get("parent_id"),
                "size": item["size"],
                "psiIds": item["psiIds"],
            },
            "members": item["members"] if int(item.get("level") or 0) == 0 else [],
        }
        for item in communities
    ]
    session.run(
        """
        UNWIND $rows AS row
        MERGE (c:Community {id: row.id})
        SET c += row.props
        WITH c, row
        UNWIND row.members AS mid
        WITH c, mid
        WHERE mid IS NOT NULL
        MATCH (n {id: mid})
        MERGE (n)-[:IN_COMMUNITY]->(c)
        """,
        rows=rows,
    )
    parents = [row for row in rows if row.get("parent_id")]
    if parents:
        session.run(
            """
            UNWIND $rows AS row
            MATCH (child:Community {id: row.id})
            MATCH (parent:Community {id: row.parent_id})
            MERGE (child)-[:PARENT_COMMUNITY]->(parent)
            """,
            rows=parents,
        )


def _write_stats(payload: dict) -> None:
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-summaries", action="store_true")
    parser.add_argument(
        "--community-checkpoint",
        type=Path,
        default=COMMUNITY_CHECKPOINT,
        help="Append-only JSONL of community summaries (not the extract chunk checkpoint)",
    )
    args, _unknown = parser.parse_known_args()
    checkpoint: Path = args.community_checkpoint

    uri, user, password, database = require_aura()
    print(f"connecting {uri} database={database}")
    driver = connect(uri, user, password)
    api = provider_label()

    with driver:
        with driver.session(database=database) as session:
            edges = fetch_edges(session)
            communities = communities_from_edges(edges)
            levels = sorted({int(item.get("level") or 0) for item in communities})
            print(
                f"entity edges {len(edges)} communities {len(communities)} "
                f"levels {levels}"
            )
            saved = load_summary_checkpoint(checkpoint)
            resumed = apply_summary_checkpoint(communities, saved)
            print(
                f"community checkpoint {checkpoint} resumed {resumed}/{len(communities)}",
                flush=True,
            )
            if args.skip_summaries or not api:
                if not api:
                    print("NO LLM API KEY; community summaries use member labels only")
                    print("API used: none")
                else:
                    print(f"API used: {api} (summaries skipped)")
                for item in communities:
                    if not str(item.get("summary") or "").strip():
                        item["summary"] = fallback_summary(item)
                        item["source"] = "fallback"
                fill = {
                    "resumed": resumed,
                    "summaries_written": 0,
                    "fallbacks_written": 0,
                    "total": len(communities),
                }
            else:
                print(f"API used: {api}")
                fill = {"resumed": resumed, "summaries_written": 0, "fallbacks_written": 0, "total": len(communities)}
            write_communities(session, communities)
            if not args.skip_summaries and api:
                fill = fill_summaries(
                    communities,
                    checkpoint=checkpoint,
                    skip_summaries=False,
                    api=api,
                    session=session,
                )
            count = session.run("MATCH (c:Community) RETURN count(c) AS n").single()["n"]
            level_rows = session.run(
                "MATCH (c:Community) RETURN c.level AS level, count(*) AS n ORDER BY level"
            )
            level_counts = {int(row["level"] or 0): int(row["n"]) for row in level_rows}

    _write_stats(
        {
            "community_count": int(count),
            "levels": level_counts,
            "summaries_written": int(fill["summaries_written"]),
            "resumed": int(fill["resumed"]),
            "checkpoint": str(checkpoint),
            "api": api,
            "llm_summaries": bool(api) and not args.skip_summaries,
            "hierarchical": True,
        }
    )
    print(f"community count {count}")
    print(f"level counts {level_counts}")
    print(f"summaries written {fill['summaries_written']}")
    print(f"summaries resumed {fill['resumed']}")
    print("wrote Community nodes + IN_COMMUNITY + PARENT_COMMUNITY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
