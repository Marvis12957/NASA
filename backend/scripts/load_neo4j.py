#!/usr/bin/env python3
"""MERGE catalog + GraphRAG graph into Neo4j.

Oxygen is stored only as Investigation.oxygenMin / oxygenMax (never a node).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.neo4j_config import connect, require_aura  # noqa: E402

GRAPH_PATH = ROOT / "backend" / "var" / "graphrag.json"
CATALOG_PATH = ROOT / "backend" / "var" / "catalog.json"

LABELS = {
    "investigation": "Investigation",
    "fuel": "Fuel",
    "hardware": "Hardware",
    "family": "Family",
    "scenario": "Scenario",
    "role": "Role",
    "context": "Context",
    "document": "Document",
    "chunk": "Chunk",
    "sensor": "Sensor",
    "phenomenon": "Phenomenon",
    "suppressant": "Suppressant",
    "location": "Location",
    "community": "Community",
}

CATALOG_JSON_FIELDS = (
    "fuels",
    "sampleSize",
    "measurements",
    "varied",
    "constant",
    "reports",
    "scores",
    "relevance",
    "roles",
    "sources",
)
CATALOG_SCALAR_FIELDS = (
    "acronym",
    "blurb",
    "oxygenNote",
    "sampleForm",
    "familyId",
    "doi",
    "role",
)

SKIP_NODE_KEYS = {"type"}


def json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        if all(isinstance(item, (str, int, float, bool)) or item is None for item in value):
            return [item for item in value if item is not None]
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def node_props(node: dict, catalog_by_psi: dict[str, dict]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key, value in node.items():
        if key in SKIP_NODE_KEYS:
            continue
        converted = json_safe(value)
        if converted is not None:
            props[key] = converted
    if node.get("type") == "investigation":
        rec = catalog_by_psi.get(str(node.get("psiId") or ""))
        if rec:
            for key in CATALOG_SCALAR_FIELDS:
                converted = json_safe(rec.get(key))
                if converted is not None and converted != "":
                    props[key] = converted
            for key in CATALOG_JSON_FIELDS:
                converted = json_safe(rec.get(key))
                if converted is not None and converted != [] and converted != "{}":
                    props[key] = converted
            # Catalog oxygen stays on Investigation only.
            if rec.get("oxygenMin") is not None:
                props["oxygenMin"] = rec["oxygenMin"]
            if rec.get("oxygenMax") is not None:
                props["oxygenMax"] = rec["oxygenMax"]
    return props


def edge_props(edge: dict) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key, value in edge.items():
        if key in {"source", "target", "type"}:
            continue
        converted = json_safe(value)
        if converted is not None:
            props[key] = converted
    return props


def batches(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def wipe_graph(session) -> None:
    session.run("MATCH (n) DETACH DELETE n")


def ensure_schema(session) -> None:
    for label in LABELS.values():
        session.run(
            f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        )
    session.run(
        """
        CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
        FOR (c:Chunk) ON (c.embedding)
        OPTIONS {
          indexConfig: {
            `vector.dimensions`: 384,
            `vector.similarity_function`: 'cosine'
          }
        }
        """
    )


def merge_nodes(session, label: str, rows: list[dict], batch_size: int) -> None:
    query = (
        f"UNWIND $rows AS row "
        f"MERGE (n:{label} {{id: row.id}}) "
        f"SET n += row.props"
    )
    for chunk in batches(rows, batch_size):
        session.run(query, rows=chunk)


def merge_edges(session, rel: str, rows: list[dict], batch_size: int) -> None:
    query = (
        f"UNWIND $rows AS row "
        f"MATCH (a {{id: row.source}}) "
        f"MATCH (b {{id: row.target}}) "
        f"MERGE (a)-[r:{rel}]->(b) "
        f"SET r += row.props"
    )
    for chunk in batches(rows, batch_size):
        session.run(query, rows=chunk)


def counts_by_label(session) -> list[tuple[str, int]]:
    result = session.run(
        """
        MATCH (n)
        WITH labels(n)[0] AS label, count(*) AS n
        RETURN label, n
        ORDER BY n DESC, label
        """
    )
    return [(record["label"], record["n"]) for record in result]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wipe", action="store_true", help="Delete all nodes and relationships first")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog_by_psi = {item["id"]: item for item in catalog.get("investigations", [])}

    nodes_by_label: dict[str, list[dict]] = defaultdict(list)
    skipped = 0
    for node in graph["nodes"]:
        ntype = node.get("type")
        label = LABELS.get(ntype)
        if not label:
            skipped += 1
            continue
        if ntype == "fuel" and str(node.get("label") or "").casefold() in {"oxygen", "o2", "o₂"}:
            continue
        props = node_props(node, catalog_by_psi)
        nodes_by_label[label].append({"id": node["id"], "props": props})

    edges_by_type: dict[str, list[dict]] = defaultdict(list)
    for edge in graph["edges"]:
        rel = edge["type"]
        if not rel.isidentifier() or not rel.isupper():
            raise SystemExit(f"unexpected relationship type {rel!r}")
        edges_by_type[rel].append(
            {
                "source": edge["source"],
                "target": edge["target"],
                "props": edge_props(edge),
            }
        )

    uri, user, password, database = require_aura()
    print(f"connecting {uri} database={database}")
    try:
        driver = connect(uri, user, password)
    except Exception as exc:  # noqa: BLE001
        print(f"Aura unavailable: {exc}")
        print("scripts are complete; check NEO4J_URI / NEO4J_PASSWORD and re-run")
        return 2

    with driver:
        with driver.session(database=database) as session:
            if args.wipe:
                print("wiping graph")
                wipe_graph(session)
            ensure_schema(session)
            for label, rows in nodes_by_label.items():
                print(f"MERGE {len(rows)} :{label}")
                merge_nodes(session, label, rows, args.batch_size)
            for rel, rows in edges_by_type.items():
                print(f"MERGE {len(rows)} -[:{rel}]->")
                merge_edges(session, rel, rows, args.batch_size)
            print("node counts by label:")
            for label, count in counts_by_label(session):
                print(f"  {label}\t{count}")
            rels = session.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n ORDER BY n DESC")
            print("relationship counts:")
            for record in rels:
                print(f"  {record['t']}\t{record['n']}")
    if skipped:
        print(f"skipped unlabeled nodes {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
