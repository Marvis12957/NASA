#!/usr/bin/env python3
"""Embed child chunk TEXT and SET c.embedding on Chunk nodes.

Uses sentence-transformers all-MiniLM-L6-v2 (384-d). Does not use Qdrant.
Writes to Neo4j Aura via NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.neo4j_config import connect, require_aura  # noqa: E402

CHILDREN_PATH = ROOT / "data" / "chunks" / "children.jsonl"
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def batches(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--model", default=MODEL_NAME)
    args = parser.parse_args()

    uri, user, password, database = require_aura()
    children = load_jsonl(CHILDREN_PATH)
    ids = [row["id"] for row in children]
    texts = [str(row.get("text") or "") for row in children]
    print(f"chunks {len(children)} model {args.model}")

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(args.model)
    vectors = model.encode(
        texts,
        batch_size=args.batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    if vectors.shape[1] != DIM:
        raise SystemExit(f"expected {DIM}-d embeddings, got {vectors.shape[1]}")

    print(f"connecting {uri} database={database}")
    try:
        driver = connect(uri, user, password)
    except Exception as exc:  # noqa: BLE001
        print(f"Aura unavailable: {exc}")
        print("embeddings were computed in-process but not written")
        return 2

    query = """
    UNWIND $rows AS row
    MERGE (c:Chunk {id: row.id})
    SET c.embedding = row.embedding
    """
    written = 0
    with driver:
        with driver.session(database=database) as session:
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
            payload = [
                {"id": cid, "embedding": vec.tolist()}
                for cid, vec in zip(ids, vectors)
            ]
            for chunk in batches(payload, 200):
                session.run(query, rows=chunk)
                written += len(chunk)
                print(f"upserted {written}/{len(payload)}")
            rec = session.run(
                "MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(c) AS n"
            ).single()
            print(f"chunks with embedding {rec['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
