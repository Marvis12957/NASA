"""Neo4j Aura access for ranking, neighbors, and MiniLM vector search."""

from __future__ import annotations

import json
import logging
import re
import threading as _threading
import time as _time
from typing import Any

from app.knowledge import SCENARIOS, family_of, format_flow, format_oxygen, oxygen_note
from app.neo4j_config import ROOT, connect, neo4j_settings
from app.rerank import BM25Index, reciprocal_rank_fusion, rerank_records

CHUNK_GRAPH_CYPHER = """
UNWIND $ids AS cid
MATCH (node:Chunk {id: cid})
OPTIONAL MATCH (node)-[:IN_DOCUMENT]->(i:Investigation)
OPTIONAL MATCH (i)-[:IN_FAMILY]->(f:Family)
OPTIONAL MATCH (node)-[:PART_OF]->(doc:Document)
OPTIONAL MATCH (node)-[rel]-(ent)
WHERE NOT ent:Document AND NOT ent:Chunk AND NOT ent:Community
  AND NOT type(rel) IN ['PART_OF', 'IN_DOCUMENT', 'IN_COMMUNITY', 'PARENT_COMMUNITY']
OPTIONAL MATCH (ent)-[:IN_COMMUNITY]->(fine:Community)
WHERE coalesce(fine.level, 0) = 0
OPTIONAL MATCH (fine)-[:PARENT_COMMUNITY]->(pillar:Community)
WITH node, i, f,
     head(collect(DISTINCT doc.title)) AS documentTitle,
     collect(DISTINCT {
       id: ent.id,
       label: coalesce(ent.label, ent.id),
       type: labels(ent)[0],
       rel: type(rel)
     }) AS neighbors,
     collect(DISTINCT {
       id: fine.id,
       title: fine.title,
       level: fine.level,
       parentId: pillar.id
     }) AS communities
RETURN node.id AS chunkId, node.psi AS psi, node.page AS page,
       node.pages_span AS pages_span,
       node.source AS source, documentTitle, node.text AS text, neighbors, communities,
       i AS inv, f.label AS family
LIMIT 40
"""

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
QUERY_TIMEOUT_SECONDS = 10.0
CATALOG_READ_LIMIT = 40
GRAPH_READ_LIMIT = 120
COMMUNITY_READ_LIMIT = 800
# Spec F4 walks these types. MENTIONS is a weight source only and is not pulled.
GRAPH_REL_TYPES = (
    "USED_IN",
    "STUDIED",
    "MEASURED_WITH",
    "TESTED_FUEL",
    "RELATED_TO",
    "USED_HARDWARE",
)
CHILDREN_PATH = ROOT / "data" / "chunks" / "children.jsonl"
PREVIEW_CHARS = 420


def parse_prop(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def node_dict(node: Any) -> dict[str, Any]:
    if node is None:
        return {}
    if hasattr(node, "items"):
        return {key: parse_prop(value) for key, value in dict(node).items()}
    return dict(node)


_OCR_MARKER_RE = re.compile(r"\[\s*ocr\s*:\s*(?:true|false)\s*\]", re.IGNORECASE)


def preview_text(text: str | None, limit: int = PREVIEW_CHARS) -> str:
    if not text:
        return ""
    # The ingest marker "[ocr: false]" (page had a text layer) means nothing to a reader.
    collapsed = " ".join(_OCR_MARKER_RE.sub(" ", str(text)).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rsplit(" ", 1)[0] + "…"


class GraphStore:
    def __init__(self, driver: Any, database: str):
        self.driver = driver
        self.database = database
        self._model = None
        self._chunk_text: dict[str, str] | None = None
        self._chunk_figures: dict[str, list[str]] | None = None
        self._bm25: BM25Index | None = None
        self._names: dict[str, str] | None = None

    def close(self) -> None:
        self.driver.close()

    def session(self):
        return self.driver.session(database=self.database)

    def run(self, query: str, **params: Any) -> list[dict]:
        from neo4j import Query

        statement = Query(query, timeout=QUERY_TIMEOUT_SECONDS)
        with self.session() as session:
            return [dict(record) for record in session.run(statement, params)]

    def ping(self) -> bool:
        try:
            return bool(self.run("RETURN 1 AS ok LIMIT 1"))
        except Exception:  # noqa: BLE001
            return False

    def investigation_names(self) -> dict[str, str]:
        if self._names is None:
            rows = self.run(
                """
                MATCH (i:Investigation)
                RETURN i.psiId AS id, i.label AS name
                LIMIT $limit
                """,
                limit=CATALOG_READ_LIMIT,
            )
            self._names = {row["id"]: row["name"] for row in rows if row.get("id")}
        return self._names

    def _load_chunk_assets(self) -> None:
        if self._chunk_text is not None:
            if getattr(self, "_chunk_figures", None) is None:
                self._chunk_figures = {}
            return
        texts: dict[str, str] = {}
        figures: dict[str, list[str]] = {}
        if CHILDREN_PATH.is_file():
            with CHILDREN_PATH.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    cid = row.get("id")
                    if not cid:
                        continue
                    texts[cid] = str(row.get("text") or "")
                    paths: list[str] = []
                    for raw in row.get("figures") or []:
                        path = str(raw).strip().replace("\\", "/")
                        if path.startswith("data/") and path not in paths:
                            paths.append(path)
                    figures[cid] = paths
        self._chunk_text = texts
        self._chunk_figures = figures

    def _chunk_lookup(self) -> dict[str, str]:
        self._load_chunk_assets()
        return self._chunk_text or {}

    def _figure_lookup(self) -> dict[str, list[str]]:
        self._load_chunk_assets()
        return self._chunk_figures or {}

    def _bm25_index(self) -> BM25Index:
        if getattr(self, "_bm25", None) is None:
            lookup = self._chunk_lookup()
            self._bm25 = BM25Index(list(lookup.items()))
        return self._bm25

    def embed_query(self, text: str) -> list[float]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(MODEL_NAME)
        vector = self._model.encode([text], normalize_embeddings=True)[0]
        return [float(value) for value in vector]

    def card(self, record: dict, scenario: str = "iss") -> dict[str, Any]:
        spec = SCENARIOS[scenario]
        psi_id = record.get("id") or record.get("psiId")
        fuels = record.get("fuels") or []
        if isinstance(fuels, str):
            fuels = parse_prop(fuels) or []
        roles = record.get("roles") or []
        if isinstance(roles, str):
            roles = parse_prop(roles) or []
        family_id, family_label = family_of(psi_id)
        family = record.get("family") or family_label
        return {
            "id": psi_id,
            "name": record.get("name") or record.get("label") or psi_id,
            "score": record.get("score"),
            "reason": record.get("reason") or record.get("blurb") or "",
            "blurb": record.get("blurb") or "",
            "summary": record.get("summary") or "",
            "roles": list(roles),
            "hardware": record.get("hardware") or "",
            "family": family,
            "familyId": record.get("familyId") or family_id,
            "fuels": list(fuels),
            "oxygenMin": record.get("oxygenMin"),
            "oxygenMax": record.get("oxygenMax"),
            "oxygen": format_oxygen(record.get("oxygenMin"), record.get("oxygenMax")),
            "flowRaw": record.get("flow"),
            "flow": format_flow(record.get("flow")),
            "sampleForm": record.get("sampleForm") or None,
            "runCount": record.get("runCount"),
            "measurements": record.get("measurements"),
            "note": oxygen_note({"id": psi_id, **record}),
            "oxygenNote": record.get("oxygenNote") or None,
            "doi": record.get("doi") or "",
            "acronym": record.get("acronym") or "",
            "sources": _as_list(record.get("sources")),
            "aliases": _as_list(record.get("aliases")),
            "relevance": _as_dict(record.get("relevance")),
            "scores": _as_dict(record.get("scores")),
            "facility": record.get("facility") or None,
            "phenomenon": record.get("phenomenon") if record.get("phenomenon") not in (None, "", []) else record.get("phenomena"),
            "chunkCount": record.get("chunkCount"),
            "documentCount": record.get("documentCount"),
            "scoreKey": spec["score_key"],
        }

    def list_scenarios(self) -> list[dict]:
        rows = self.run(
            """
            MATCH (s:Scenario)
            OPTIONAL MATCH (s)-[:USES_ATMOSPHERE_BOUND]->(bound:Scenario)
            RETURN s.id AS id, s.label AS label, s.caveat AS caveat, bound.id AS boundId
            ORDER BY s.id
            LIMIT $limit
            """,
            limit=CATALOG_READ_LIMIT,
        )
        out = []
        for row in rows:
            raw = str(row["id"] or "").removeprefix("scenario:")
            spec = SCENARIOS.get(raw, {})
            caveat = row.get("caveat") or spec.get("caveat")
            out.append(
                {
                    "id": raw,
                    "label": row.get("label") or spec.get("label") or raw,
                    "caveat": caveat,
                    "scoreKey": spec.get("score_key", raw if raw in ("iss", "moon", "suit", "earth") else "moon"),
                    "bound": (row.get("boundId") or "").removeprefix("scenario:") or None,
                }
            )
        return out

    def list_investigations(self) -> list[dict]:
        rows = self.run(
            """
            MATCH (i:Investigation)
            OPTIONAL MATCH (i)-[:IN_FAMILY]->(f:Family)
            RETURN i AS inv, f.label AS family
            ORDER BY i.psiId
            LIMIT $limit
            """,
            limit=CATALOG_READ_LIMIT,
        )
        cards = []
        for row in rows:
            rec = _investigation_from_node(node_dict(row["inv"]), row.get("family"))
            cards.append(self.card(rec))
        return cards

    def get_investigation(self, psi_id: str) -> dict | None:
        rows = self.run(
            """
            MATCH (i:Investigation {psiId: $psiId})
            OPTIONAL MATCH (i)-[:IN_FAMILY]->(f:Family)
            OPTIONAL MATCH (peer:Investigation)-[:IN_FAMILY]->(f)
            WHERE peer.psiId <> i.psiId
            RETURN i AS inv, f.label AS family, f.id AS familyId,
                   collect(DISTINCT {id: peer.psiId, name: peer.label}) AS peers
            LIMIT 1
            """,
            psiId=psi_id,
        )
        if not rows:
            return None
        row = rows[0]
        rec = _investigation_from_node(node_dict(row["inv"]), row.get("family"))
        if row.get("familyId"):
            rec["familyId"] = str(row["familyId"]).removeprefix("family:")
        card = self.card(rec)
        peers = [peer for peer in row.get("peers") or [] if peer and peer.get("id")]
        card["peers"] = peers
        return card

    def rank_investigations(self, scenario: str, role: str | None = None, limit: int = 5) -> dict:
        spec = SCENARIOS[scenario]
        score_id = f"scenario:{spec['score_key']}"
        rows = self.run(
            """
            MATCH (i:Investigation)-[rel:RELEVANT_TO]->(s:Scenario {id: $scenarioId})
            OPTIONAL MATCH (i)-[:IN_FAMILY]->(f:Family)
            OPTIONAL MATCH (i)-[:HAS_ROLE]->(ro:Role)
            WITH i, rel, f, collect(DISTINCT replace(ro.id, 'role:', '')) AS roleIds
            WHERE $role IS NULL OR $role IN roleIds
            RETURN i AS inv, rel.score AS score, rel.reason AS reason, f.label AS family, roleIds
            ORDER BY rel.score DESC, i.psiId
            LIMIT $limit
            """,
            scenarioId=score_id,
            role=role,
            limit=int(limit),
        )
        ranked = []
        for row in rows:
            rec = _investigation_from_node(node_dict(row["inv"]), row.get("family"))
            rec["score"] = row.get("score")
            rec["reason"] = row.get("reason")
            rec["roles"] = row.get("roleIds") or rec.get("roles") or []
            ranked.append(self.card(rec, scenario))
        scenario_row = self.run(
            """
            MATCH (s:Scenario {id: $id})
            RETURN s.caveat AS caveat, s.label AS label
            LIMIT 1
            """,
            id=f"scenario:{scenario}",
        )
        caveat = spec["caveat"]
        label = spec["label"]
        if scenario_row:
            caveat = scenario_row[0].get("caveat") or caveat
            label = scenario_row[0].get("label") or label
        return {
            "scenario": {**spec, "label": label, "caveat": caveat},
            "role": role,
            "ranked": ranked,
        }

    def search_investigations(self, query: str, limit: int = 5) -> dict:
        embedding = self.embed_query(query)
        k = max(1, min(int(limit), 8))
        rows = self.run(
            """
            CALL db.index.vector.queryNodes('chunk_embedding', $k, $embedding)
            YIELD node, score
            OPTIONAL MATCH (node)-[:IN_DOCUMENT]->(i:Investigation)
            OPTIONAL MATCH (i)-[:IN_FAMILY]->(f:Family)
            OPTIONAL MATCH (node)-[:PART_OF]->(doc:Document)
            RETURN node.id AS chunkId, node.psi AS psi, node.page AS page,
                   node.pages_span AS pages_span,
                   node.source AS source, doc.title AS documentTitle, node.text AS text, score,
                   i AS inv, f.label AS family
            LIMIT $k
            """,
            k=k,
            embedding=embedding,
        )
        lookup = self._chunk_lookup()
        hits = []
        for row in rows:
            chunk_id = row.get("chunkId")
            text = row.get("text") or lookup.get(chunk_id, "")
            inv_node = node_dict(row.get("inv")) if row.get("inv") is not None else {}
            investigation = None
            if inv_node:
                rec = _investigation_from_node(inv_node, row.get("family"))
                investigation = self.card(rec)
            hits.append(
                {
                    "chunkId": chunk_id,
                    "psi": row.get("psi") or (investigation or {}).get("id"),
                    "page": row.get("page"),
                    "pages_span": row.get("pages_span"),
                    "source": row.get("source"),
                    "documentTitle": row.get("documentTitle") or None,
                    "preview": preview_text(text),
                    "figures": list(self._figure_lookup().get(chunk_id) or []),
                    "score": row.get("score"),
                    "investigation": investigation,
                }
            )
        return {"hits": hits}

    def compare_investigations(self, ids: list[str]) -> dict | None:
        unique: list[dict] = []
        seen: set[str] = set()
        for psi_id in ids:
            if psi_id in seen:
                continue
            card = self.get_investigation(psi_id)
            if not card:
                continue
            seen.add(psi_id)
            unique.append(card)
            if len(unique) == 4:
                break
        if len(unique) < 2:
            return None
        families = {item["familyId"] for item in unique}
        role_sets = [set(item.get("roles") or []) for item in unique]
        shared_roles = sorted(role_sets[0].intersection(*role_sets[1:])) if role_sets else []
        fuel_sets = [set(str(fuel).lower() for fuel in item.get("fuels") or []) for item in unique]
        shared_fuels = sorted(fuel_sets[0].intersection(*fuel_sets[1:])) if fuel_sets else []
        same = [
            text
            for text in (
                f"Same hardware family: {unique[0]['family']}." if len(families) == 1 else "",
                f"Shared safety role: {', '.join(shared_roles)}." if shared_roles else "",
                f"Shared fuel strings: {', '.join(shared_fuels)}." if shared_fuels else "",
            )
            if text
        ]
        different = [
            "Oxygen: "
            + "; ".join(f"{item['id']} {item['oxygen']}" for item in unique)
            + ".",
            "Airflow: "
            + "; ".join(f"{item['id']} {item['flow']}" for item in unique)
            + ".",
            "Runs: "
            + "; ".join(
                f"{item['id']} {item['runCount'] if item['runCount'] is not None else 'none recorded'}"
                for item in unique
            )
            + ".",
        ]
        if len(families) > 1:
            different.append(
                "Hardware: " + "; ".join(f"{item['id']} {item['family']}" for item in unique) + "."
            )
        return {"rows": unique, "same": same, "different": different}

    def graph_neighbors(self, psi_id: str, depth: int = 1) -> dict | None:
        card = self.get_investigation(psi_id)
        if not card:
            return None
        hops = 2 if int(depth or 1) >= 2 else 1
        rows = self.run(
            """
            MATCH (i:Investigation {psiId: $psiId})-[rel]-(n)
            WHERE NOT n:Chunk AND NOT n:Document
              AND type(rel) IN $rels
            RETURN type(rel) AS relation, n.id AS id, labels(n)[0] AS type,
                   coalesce(n.label, n.id) AS label,
                   i.psiId AS fromId,
                   rel.score AS score, rel.reason AS reason
            ORDER BY relation, label
            LIMIT $limit
            """,
            psiId=psi_id,
            rels=list(GRAPH_REL_TYPES),
            limit=GRAPH_READ_LIMIT,
        )
        if hops >= 2:
            hop_ids = [row.get("id") for row in rows if row.get("id")]
            if hop_ids:
                rows.extend(
                    self.run(
                        """
                        MATCH (n)-[rel]-(m)
                        WHERE n.id IN $ids
                          AND NOT m:Chunk AND NOT m:Document
                          AND type(rel) IN $rels
                          AND NOT m.id IN $centerIds
                          AND coalesce(m.psiId, '') <> $psiId
                        RETURN type(rel) AS relation, m.id AS id, labels(m)[0] AS type,
                               coalesce(m.label, m.id) AS label,
                               n.id AS fromId,
                               rel.score AS score, rel.reason AS reason
                        ORDER BY relation, label
                        LIMIT $limit
                        """,
                        ids=hop_ids[:GRAPH_READ_LIMIT],
                        rels=list(GRAPH_REL_TYPES),
                        psiId=psi_id,
                        centerIds=[psi_id, f"inv:{psi_id}"],
                        limit=GRAPH_READ_LIMIT,
                    )
                )
        links = [_neighbor_link(row) for row in rows if row.get("id")]
        return {
            "id": card["id"],
            "name": card["name"],
            "family": card["family"],
            "links": links,
            "peers": card.get("peers") or [],
        }

    def scenario_context(self, scenario: str) -> dict:
        spec = SCENARIOS[scenario]
        link_key = None if scenario == "mars" else "moon" if scenario == "rover" else scenario
        notes = []
        if link_key:
            notes = self.run(
                """
                MATCH (c:Context)-[:INFORMS]->(s:Scenario {id: $id})
                RETURN c.label AS label, c.summary AS summary, c.url AS url
                ORDER BY c.label
                LIMIT 20
                """,
                id=f"scenario:{link_key}",
            )
        scenario_row = self.run(
            """
            MATCH (s:Scenario {id: $id})
            RETURN s.caveat AS caveat, s.label AS label
            LIMIT 1
            """,
            id=f"scenario:{scenario}",
        )
        caveat = spec["caveat"]
        label = spec["label"]
        if scenario_row:
            caveat = scenario_row[0].get("caveat") or caveat
            label = scenario_row[0].get("label") or label
        body: dict[str, Any] = {
            "scenario": {**spec, "label": label, "caveat": caveat},
            "notes": notes,
        }
        # Phenomena and sensors only. Scenario nodes have no gaps property.
        score_id = f"scenario:{spec['score_key']}"
        phenomena = _scenario_entities(
            self,
            """
            MATCH (i:Investigation)-[:RELEVANT_TO]->(:Scenario {id: $id})
            MATCH (i)-[:STUDIED]-(p:Phenomenon)
            WITH p, count(DISTINCT i) AS n
            ORDER BY n DESC, coalesce(p.label, p.id)
            LIMIT 8
            RETURN p.id AS id, coalesce(p.label, p.id) AS label
            """,
            score_id,
        )
        sensors = _scenario_entities(
            self,
            """
            MATCH (i:Investigation)-[:RELEVANT_TO]->(:Scenario {id: $id})
            MATCH (i)-[:MEASURED_WITH]-(s:Sensor)
            WITH s, count(DISTINCT i) AS n
            ORDER BY n DESC, coalesce(s.label, s.id)
            LIMIT 8
            RETURN s.id AS id, coalesce(s.label, s.id) AS label
            """,
            score_id,
        )
        if phenomena:
            body["phenomena"] = phenomena
        if sensors:
            body["sensors"] = sensors
        return body

    def local_search(self, query: str, limit: int = 5) -> dict:
        """Hybrid retrieve (cosine + BM25 RRF), then cross-encoder rerank."""
        k = max(1, min(int(limit), 8))
        retrieve_k = max(k, min(32, max(k * 4, 12)))
        vector_ids: list[str] = []
        vector_score: dict[str, float] = {}
        try:
            embedding = self.embed_query(query)
            for row in self.run(
                """
                CALL db.index.vector.queryNodes('chunk_embedding', $k, $embedding)
                YIELD node, score
                RETURN node.id AS chunkId, score
                LIMIT $k
                """,
                k=retrieve_k,
                embedding=embedding,
            ):
                cid = row.get("chunkId")
                if cid:
                    vector_ids.append(str(cid))
                    vector_score[str(cid)] = float(row.get("score") or 0)
        except Exception:  # noqa: BLE001
            pass
        bm25_ids = [doc_id for doc_id, _score in self._bm25_index().search(query, retrieve_k)]
        fused = reciprocal_rank_fusion([vector_ids, bm25_ids])
        fused_ids = [doc_id for doc_id, _score in fused[:retrieve_k]]
        if not fused_ids and vector_ids:
            fused_ids = vector_ids[:retrieve_k]
        if not fused_ids:
            reason = "vector_search_unavailable" if not vector_ids else "no_hits"
            return {"hits": [], "degraded": True, "reason": reason, "retrieve": "hybrid"}
        try:
            rows = self.run(CHUNK_GRAPH_CYPHER, ids=fused_ids)
        except Exception:  # noqa: BLE001
            return {
                "hits": [],
                "degraded": True,
                "reason": "vector_search_unavailable",
                "retrieve": "hybrid",
            }
        lookup = self._chunk_lookup()
        by_id = {row.get("chunkId"): row for row in rows if row.get("chunkId")}
        hits = []
        for chunk_id in fused_ids:
            row = by_id.get(chunk_id) or {"chunkId": chunk_id}
            text = row.get("text") or lookup.get(chunk_id, "")
            inv_node = node_dict(row.get("inv")) if row.get("inv") is not None else {}
            investigation = None
            if inv_node:
                rec = _investigation_from_node(inv_node, row.get("family"))
                investigation = self.card(rec)
            neighbors = [
                neighbor
                for neighbor in (row.get("neighbors") or [])
                if neighbor and neighbor.get("id")
            ]
            communities = [
                community
                for community in (row.get("communities") or [])
                if community and community.get("id")
            ]
            hits.append(
                {
                    "chunkId": chunk_id,
                    "psi": row.get("psi") or (investigation or {}).get("id"),
                    "page": row.get("page"),
                    "pages_span": row.get("pages_span"),
                    "source": row.get("source"),
                    "documentTitle": row.get("documentTitle") or None,
                    "preview": preview_text(text),
                    "figures": list(self._figure_lookup().get(chunk_id) or []),
                    "score": vector_score.get(chunk_id),
                    "investigation": investigation,
                    "neighbors": neighbors,
                    "communities": communities,
                    "_rerank_text": text or preview_text(text),
                }
            )
        ranked, method = rerank_records(query, hits, text_key="_rerank_text", limit=k)
        for hit in ranked:
            hit.pop("_rerank_text", None)
        return {
            "hits": ranked,
            "degraded": False,
            "reason": None,
            "rerank": method,
            "retrieve": "hybrid",
            "retrieved": len(hits),
            "vectorHits": len(vector_ids),
            "bm25Hits": len(bm25_ids),
        }

    def global_search(self, query: str, limit: int = 5) -> dict:
        """Rank Community nodes. Prefer LLM summaries; degrade to titles/members if pending."""
        k = max(1, min(int(limit), 8))
        try:
            rows = self.run(
                """
                MATCH (c:Community)
                RETURN c.id AS id, c.summary AS summary, c.title AS title,
                       c.psiIds AS psiIds, c.level AS level, c.size AS size,
                       c.parent_id AS parentId
                ORDER BY coalesce(c.size, 0) DESC, c.id
                LIMIT $limit
                """,
                limit=COMMUNITY_READ_LIMIT,
            )
        except Exception:  # noqa: BLE001
            return {"communities": [], "degraded": True, "reason": "community_query_failed"}
        if not rows:
            return {"communities": [], "degraded": True, "reason": "no_communities"}

        def _has_summary(row: dict) -> bool:
            return bool(str(row.get("summary") or "").strip())

        summarized = [row for row in rows if _has_summary(row)]
        degraded = not summarized
        reason = "summaries_pending" if degraded else None
        pool = summarized if summarized else rows

        scored: list[dict] = []
        for row in pool:
            psi_ids = [psi for psi in (row.get("psiIds") or []) if psi]
            investigations = []
            for psi_id in psi_ids[:8]:
                card = self.get_investigation(str(psi_id).removeprefix("inv:"))
                if card:
                    investigations.append(
                        {
                            "id": card["id"],
                            "name": card["name"],
                            "oxygen": card["oxygen"],
                            "fuels": card.get("fuels") or [],
                        }
                    )
            blob = " ".join(
                str(part or "")
                for part in (
                    row.get("title"),
                    row.get("summary"),
                    " ".join(psi_ids),
                )
            )
            scored.append(
                {
                    "id": row.get("id"),
                    "title": row.get("title") or row.get("id"),
                    "summary": row.get("summary") or "",
                    "level": row.get("level") or 0,
                    "size": row.get("size"),
                    "parentId": row.get("parentId"),
                    "psiIds": [str(psi).removeprefix("inv:") for psi in psi_ids],
                    "investigations": investigations,
                    "_rerank_text": blob,
                }
            )
        ranked, method = rerank_records(query, scored, text_key="_rerank_text", limit=k)
        for item in ranked:
            item["score"] = item.get("rerankScore")
            item.pop("_rerank_text", None)
        return {
            "communities": ranked,
            "degraded": degraded,
            "reason": reason,
            "rerank": method,
        }


def _investigation_from_node(inv: dict, family: str | None) -> dict:
    psi_id = inv.get("psiId") or str(inv.get("id") or "").removeprefix("inv:")
    return {
        "id": psi_id,
        "psiId": psi_id,
        "name": inv.get("label") or inv.get("name") or psi_id,
        "label": inv.get("label"),
        "blurb": inv.get("blurb") or "",
        "summary": inv.get("summary") or "",
        "fuels": inv.get("fuels") or [],
        "roles": inv.get("roles") or [],
        "hardware": inv.get("hardware") or "",
        "family": family or inv.get("family"),
        "familyId": inv.get("familyId"),
        "oxygenMin": inv.get("oxygenMin"),
        "oxygenMax": inv.get("oxygenMax"),
        "oxygenNote": inv.get("oxygenNote"),
        "flow": inv.get("flow"),
        "sampleForm": inv.get("sampleForm"),
        "runCount": inv.get("runCount"),
        "measurements": inv.get("measurements"),
        "doi": inv.get("doi") or "",
        "acronym": inv.get("acronym") or "",
        "relevance": inv.get("relevance") or {},
        "scores": inv.get("scores") or {},
        "sources": inv.get("sources") or [],
        "aliases": inv.get("aliases") or [],
        "facility": inv.get("facility"),
        "phenomenon": inv.get("phenomenon") or inv.get("phenomena"),
        "chunkCount": inv.get("chunkCount"),
        "documentCount": inv.get("documentCount"),
    }


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    parsed = parse_prop(value)
    return parsed if isinstance(parsed, dict) else {}


def _as_list(value: Any) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    parsed = parse_prop(value)
    if isinstance(parsed, list):
        return [item for item in parsed if item not in (None, "")]
    return [value]


def _scenario_entities(store: GraphStore, query: str, scenario_id: str) -> list[dict]:
    try:
        rows = store.run(query, id=scenario_id)
    except Exception:  # noqa: BLE001
        return []
    found = []
    seen: set[str] = set()
    for row in rows or []:
        raw_id = str(row.get("id") or "").strip()
        label = str(row.get("label") or "").strip()
        if not raw_id and not label:
            continue
        if raw_id in seen:
            continue
        if raw_id:
            seen.add(raw_id)
        found.append({"id": raw_id or label, "label": label or raw_id})
    return found


def _neighbor_link(row: dict) -> dict:
    return {
        "relation": row.get("relation"),
        "id": row.get("id"),
        "type": (row.get("type") or "unknown").lower(),
        "label": row.get("label"),
        "fromId": row.get("fromId"),
        "score": row.get("score"),
        "reason": row.get("reason"),
    }


_log = logging.getLogger(__name__)
_STORE_LOCK = _threading.Lock()
_STORE: GraphStore | None = None
_LAST_FAILURE = 0.0
_STARTED = False
STORE_RETRY_SECONDS = 30.0


def open_store() -> GraphStore | None:
    """Shared Aura store. Never raises: returns None while Aura is unreachable.

    The first call (app startup) tries a few times. Later calls make one quick attempt
    at most every STORE_RETRY_SECONDS, so a paused Aura gives fast 503s instead of a
    ~36 s stall on every request. A working connection is cached for the process.
    """
    global _STORE, _LAST_FAILURE, _STARTED
    if _STORE is not None:
        return _STORE
    uri, user, password, database = neo4j_settings()
    if not uri or not password:
        return None
    with _STORE_LOCK:
        if _STORE is not None:
            return _STORE
        if _LAST_FAILURE and _time.monotonic() - _LAST_FAILURE < STORE_RETRY_SECONDS:
            return None
        attempts = 1 if _STARTED else 3
        _STARTED = True
        try:
            driver = connect(uri, user, password, attempts=attempts)
        except Exception as exc:  # noqa: BLE001
            _LAST_FAILURE = _time.monotonic()
            _log.warning(
                "Neo4j Aura unreachable (%s); next try in %ss",
                type(exc).__name__,
                int(STORE_RETRY_SECONDS),
            )
            return None
        _LAST_FAILURE = 0.0
        _STORE = GraphStore(driver, database)
        return _STORE


def _reset_store() -> None:
    """Forget the cached store (app shutdown, tests)."""
    global _STORE, _LAST_FAILURE, _STARTED
    with _STORE_LOCK:
        _STORE = None
        _LAST_FAILURE = 0.0
        _STARTED = False


# Keeps the old lru_cache API that app.main.lifespan calls on shutdown.
open_store.cache_clear = _reset_store  # type: ignore[attr-defined]


def require_store() -> GraphStore:
    store = open_store()
    if store is None:
        raise RuntimeError("Neo4j Aura is not configured. Copy .env.example to .env.")
    return store
