"""OpenUI spec composer. Blocks come from the catalog in docs/AGENT_VIZ_SPEC.md.

Numbers are copied from Investigation fields or chunk quotes. Missing fields become
MissingData instead of a guessed figure.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from typing import Any

from app.graph import GraphStore
from app.knowledge import SCENARIOS, detect_scenario, format_flow
from app.neo4j_config import ROOT
from app.tools import mentioned_from_question

SNAPSHOT_PATH = ROOT / "backend" / "var" / "viz_snapshots.json"
COVERAGE_PATH = ROOT / "backend" / "var" / "viz_coverage.json"
COVERAGE_TTL_SECONDS = 3600
MAX_GRAPH_NODES = 60
MAX_GRAPH_EDGES = 120
MAX_EVIDENCE = 5
MAX_QA_WORDS = 120
MAX_TOPIC = 24
MAX_TOPIC_FETCH = 800
MAX_SENSOR_COLUMNS = 8
MAX_SENSOR_ROWS = 24
MAX_COVERAGE_SOURCES = 43
MAX_HEATMAP_FUELS = 12
MAX_HEATMAP_PHENOMENA = 8
EXTRACT_COLUMNS = (
    "acronym",
    "family",
    "fuels",
    "oxygenMin",
    "oxygenMax",
    "runCount",
    "sampleForm",
    "measurements",
    "documentCount",
    "chunkCount",
)
_NO_FLOW = "no airflow column in the tables held here"
_COVERAGE_CAPTION = (
    "Số chunk theo nguồn là số đếm thô, không phải điểm mô hình. "
    "HeatMap Fuel × Phenomenon là tỉ lệ theo nguồn, không phải số đếm thô. "
    "Bảng trường đếm có mặt hay thiếu trên Investigation "
    "(summary, acronym, blurb, relevance, scores, aliases, doi, sources, runCount, fuels, "
    "measurements, sampleForm, oxygenMin, oxygenMax, oxygenNote), không phải nồng độ oxy."
)
_HEATMAP_CAPTION = (
    "Mỗi ô cộng, theo từng nguồn, số chunk nhắc đồng thời nhiên liệu và hiện tượng "
    "chia cho số chunk của nguồn đó. "
    "Trục lấy tối đa 12 nhiên liệu và 8 hiện tượng theo tổng tỉ lệ đã chuẩn hoá theo nguồn, "
    "không theo tổng MENTIONS thô, để một nguồn lớn không tự chọn trục. "
    "Nhãn gộp biến thể hoa/thường, alias khi node có aliases, và bỏ tiền tố loại cùng hậu tố @PSI; "
    "id gốc nằm ở evidence. "
    "Nguồn không có số chunk thì không được cộng vào ô."
)
# Per source, keep a few fuels/phenomena so one large source cannot fill the candidate list.
_AXIS_PER_SOURCE = 6
_FUEL_AXIS_QUERY = """
MATCH (c:Chunk)-[:MENTIONS]->(f:Fuel)
WITH f, coalesce(c.psi, c.source, 'unknown') AS source, count(DISTINCT c) AS n
ORDER BY source, n DESC
WITH source, collect({id: f.id, label: coalesce(f.label, f.id), aliases: f.aliases, n: n})[..$perSource] AS top
UNWIND top AS row
RETURN row.id AS fuelId, row.label AS fuelLabel, row.aliases AS fuelAliases,
       source AS source, row.n AS n
LIMIT $limit
"""
_PHENOMENON_AXIS_QUERY = """
MATCH (c:Chunk)-[:MENTIONS]->(p:Phenomenon)
WITH p, coalesce(c.psi, c.source, 'unknown') AS source, count(DISTINCT c) AS n
ORDER BY source, n DESC
WITH source, collect({id: p.id, label: coalesce(p.label, p.id), aliases: p.aliases, n: n})[..$perSource] AS top
UNWIND top AS row
RETURN row.id AS phenomenonId, row.label AS phenomenonLabel, row.aliases AS phenomenonAliases,
       source AS source, row.n AS n
LIMIT $limit
"""
_HEATMAP_PAIRS_QUERY = """
UNWIND $fuelIds AS fid
UNWIND $phenIds AS pid
MATCH (f:Fuel {id: fid})
MATCH (p:Phenomenon {id: pid})
MATCH (c:Chunk)-[:MENTIONS]->(f)
MATCH (c)-[:MENTIONS]->(p)
WITH f, p, coalesce(c.psi, c.source, 'unknown') AS source, count(DISTINCT c) AS n
RETURN f.id AS fuelId, coalesce(f.label, f.id) AS fuelLabel, f.aliases AS fuelAliases,
       p.id AS phenomenonId, coalesce(p.label, p.id) AS phenomenonLabel,
       p.aliases AS phenomenonAliases,
       source AS source, n AS n
LIMIT $pairLimit
"""
_COUNT_QUERY = """
MATCH (i:Investigation)
OPTIONAL MATCH (c:Chunk)-[:IN_DOCUMENT]->(i)
OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
RETURN i.psiId AS psiId, count(DISTINCT d) AS documentCount, count(DISTINCT c) AS chunkCount
LIMIT 40
"""
_SENSOR_NOTE = (
    "Dữ liệu chất dập cháy trong PSI còn mỏng. "
    "Ô ma trận chỉ lấy quan hệ MEASURED_WITH đã có; không suy thêm số lượt."
)
_TYPED_ID = re.compile(
    r"^(?:fuel|phenomenon|sensor|hardware|suppressant|location):(.+)$",
    re.IGNORECASE,
)
_PSI_TAIL = re.compile(r"@PSI-\d+$", re.IGNORECASE)
_LEADING_KIND = re.compile(
    r"^(?:fuel|phenomenon|sensor|hardware|suppressant|location)-",
    re.IGNORECASE,
)
_SCOPE_TAIL = re.compile(r"-psi-\d+$", re.IGNORECASE)

_GATEWAY = re.compile(r"lunar gateway|gateway", re.I)
_PROFILE = re.compile(r"hồ sơ|ho so|chi tiết|chi tiet|\bprofile\b|\bdetail", re.I)
_GRAPH_EXPLICIT = re.compile(
    r"đồ thị|do thi|\bgraph\b|hàng xóm|hang xom|\bnodes?\b|neighbou?rs?",
    re.I,
)
_GRAPH_REL = re.compile(r"liên quan|lien quan|thiết bị|thiet bi|\bhardware\b", re.I)
_RANKING = re.compile(r"liên quan nhất|lien quan nhat|\btop\b|xếp hạng|xep hang", re.I)
_SCENARIO_WORD = re.compile(r"briefing|kịch bản|kich ban|\bscenario\b", re.I)
_COMPARE = re.compile(r"so sánh|so sanh|\bcompare\b|\bversus\b|\bvs\b", re.I)
_EVIDENCE = re.compile(r"trang nào|trang nao|nguồn|nguon|\bevidence\b|\bchunk\b|source", re.I)
_EXPLORE = re.compile(r"liệt kê|liet ke|những nghiên cứu|nhung nghien cuu|\bexplore\b|xếp hạng|xep hang|investigation", re.I)
_TOPIC = re.compile(
    r"tổng quan chủ đề|tong quan chu de|bản đồ chủ đề|ban do chu de|\btopic\b|chủ đề|chu de|\bcommunity\b",
    re.I,
)
_COVERAGE = re.compile(
    r"độ phủ|do phu|bao phủ|phủ tới|phu toi|\bcoverage\b|train model|đủ để train|du de train",
    re.I,
)
_EXTRACT = re.compile(r"xuất bảng|xuat bang|xuất csv|xuat csv|\bexport\b|\bcsv\b|trích xuất|trich xuat", re.I)
_SENSOR = re.compile(r"cảm biến|cam bien|\bsensor\b|dập cháy|dap chay|suppressant|extinction", re.I)
_COMMUNITY_ID = re.compile(r"community:L\d+:\d+", re.I)
_OXYGEN_TOKEN = r"(?<![A-Za-z0-9])(?:oxygen|xo2|o2|o₂)(?![A-Za-z0-9])"
_OXYGEN_FIGURE = re.compile(
    rf"(?i){_OXYGEN_TOKEN}.{{0,32}}?(\d+(?:\.\d+)?(?:\s*[–—-]\s*\d+(?:\.\d+)?)?)\s*%"
    rf"|(\d+(?:\.\d+)?(?:\s*[–—-]\s*\d+(?:\.\d+)?)?)\s*%.{{0,32}}?{_OXYGEN_TOKEN}"
)
_OXYGEN_CHUNK_QUERY = """
MATCH (c:Chunk)-[:IN_DOCUMENT]->(i:Investigation {psiId: $psiId})
WHERE c.text IS NOT NULL AND (
  toLower(c.text) CONTAINS 'oxygen' OR c.text CONTAINS 'O2' OR c.text CONTAINS 'O₂' OR toLower(c.text) CONTAINS 'xo2'
)
RETURN c.id AS chunkId, c.text AS text, c.page AS page, c.pages_span AS pages_span, c.source AS source
LIMIT 8
"""
_TESTED_FUEL_QUERY = """
MATCH (i:Investigation {psiId: $psiId})-[:TESTED_FUEL]->(f:Fuel)
RETURN f.id AS id, coalesce(f.label, f.id) AS label, f.aliases AS aliases
LIMIT 24
"""
_USED_HARDWARE_QUERY = """
MATCH (i:Investigation {psiId: $psiId})-[:USED_HARDWARE]-(h:Hardware)
RETURN h.id AS id, coalesce(h.label, h.id) AS label
ORDER BY label
LIMIT 8
"""
_STUDIED_QUERY = """
MATCH (i:Investigation {psiId: $psiId})-[:STUDIED]-(p:Phenomenon)
RETURN p.id AS id, coalesce(p.label, p.id) AS label
ORDER BY label
LIMIT 8
"""
_LOCATION_QUERY = """
MATCH (i:Investigation)-[:USED_IN|RELATED_TO|STUDIED]-(l:Location)
WHERE ($label <> '' AND toLower(coalesce(l.label, '')) = $label)
   OR ($locId <> '' AND l.id = $locId)
RETURN DISTINCT i.psiId AS psiId
LIMIT 40
"""
_SENSOR_INV_QUERY = """
MATCH (s:Sensor)-[:MEASURED_WITH]-(i:Investigation)
WHERE toLower(coalesce(s.label, s.id, '')) CONTAINS $sensor
   OR toLower(coalesce(s.id, '')) CONTAINS $sensor
RETURN DISTINCT i.psiId AS psiId, s.id AS sensorId, coalesce(s.label, s.id) AS label
LIMIT 40
"""
_MENTION_WEIGHT_QUERY = """
MATCH (c:Chunk)-[:IN_DOCUMENT]->(:Investigation {psiId: $psiId})
MATCH (c)-[:MENTIONS]->(e)
WHERE e.id IN $ids
RETURN e.id AS id, count(*) AS n
LIMIT 60
"""
_FUEL_ALIAS_QUERY = """
MATCH (f:Fuel)
WHERE f.aliases IS NOT NULL AND size(f.aliases) > 0
RETURN f.id AS id, coalesce(f.label, f.id) AS label, f.aliases AS aliases
LIMIT 400
"""


def _spec(
    intent: str,
    title: str,
    blocks: list[dict],
    *,
    filters: list[dict] | None = None,
    followups: list[str] | None = None,
    text: str | None = None,
) -> dict:
    body: dict[str, Any] = {
        "version": "1.0",
        "intent": intent,
        "title": title,
        "filters": filters or [],
        "blocks": blocks,
        "followups": (followups or [])[:3],
    }
    if text:
        words = text.split()
        body["text"] = " ".join(words[:MAX_QA_WORDS])
    return body


def missing(field: str) -> dict:
    return {
        "component": "MissingData",
        "data": {"field": field, "action": "search_chunks"},
    }


def _filters(state: dict | None) -> list[dict]:
    raw = (state or {}).get("filters") or []
    return [item for item in raw if isinstance(item, dict) and item.get("kind")]


def _family_filter(filters: list[dict]) -> str | None:
    for item in filters:
        if item.get("kind") == "family" and item.get("id"):
            return str(item["id"])
    return None


def _fuel_filter(filters: list[dict]) -> str | None:
    for item in filters:
        if item.get("kind") == "fuel" and (item.get("label") or item.get("id")):
            return str(item.get("label") or item.get("id")).lower()
    return None


def _sensor_filter(filters: list[dict]) -> str | None:
    for item in filters:
        if item.get("kind") == "sensor" and (item.get("label") or item.get("id")):
            return str(item.get("label") or item.get("id")).lower()
    return None


def _investigation_filter(filters: list[dict]) -> str | None:
    for item in filters:
        if item.get("kind") == "investigation" and item.get("id"):
            return str(item["id"]).removeprefix("inv:")
    return None


def _location_filter(filters: list[dict]) -> tuple[str, str]:
    for item in filters:
        if item.get("kind") != "location":
            continue
        label = str(item.get("label") or "").strip().lower()
        raw_id = str(item.get("id") or "").strip()
        if label or raw_id:
            return label, raw_id
    return "", ""


def _narrowing(filters: list[dict]) -> bool:
    kinds = {"family", "fuel", "investigation", "sensor", "location"}
    return any(item.get("kind") in kinds for item in filters)


def _card_matches(card: dict, filters: list[dict]) -> bool:
    family = _family_filter(filters)
    if family and card.get("familyId") != family and card.get("family") != family:
        return False
    fuel = _fuel_filter(filters)
    if fuel and not any(fuel in str(item).lower() for item in (card.get("fuels") or [])):
        return False
    focus = _investigation_filter(filters)
    if focus and str(card.get("id") or "").removeprefix("inv:") != focus:
        return False
    return True


def _psi_set(rows: list[dict] | None) -> set[str]:
    found: set[str] = set()
    for row in rows or []:
        psi = str(row.get("psiId") or "").removeprefix("inv:").strip()
        if psi:
            found.add(psi)
    return found


def _relation_ids(store: GraphStore, filters: list[dict]) -> set[str] | None:
    """Investigation ids linked by a real sensor or location relationship. None if no such filter."""
    sets: list[set[str]] = []
    label, loc_id = _location_filter(filters)
    if label or loc_id:
        try:
            rows = _run(store, _LOCATION_QUERY, label=label, locId=loc_id)
        except Exception:  # noqa: BLE001
            rows = []
        sets.append(_psi_set(rows))
    sensor = _sensor_filter(filters)
    if sensor:
        try:
            rows = _run(store, _SENSOR_INV_QUERY, sensor=sensor)
        except Exception:  # noqa: BLE001
            rows = []
        needle = sensor.casefold()
        matched = []
        for row in rows or []:
            hay = f"{row.get('label') or ''} {row.get('sensorId') or ''} {row.get('id') or ''}".casefold()
            if needle in hay:
                matched.append(row)
        sets.append(_psi_set(matched))
    if not sets:
        return None
    allowed = sets[0]
    for other in sets[1:]:
        allowed &= other
    return allowed


def _restrict_cards(store: GraphStore, cards: list[dict], filters: list[dict]) -> list[dict]:
    kept = [card for card in cards if _card_matches(card, filters)]
    allowed = _relation_ids(store, filters)
    if allowed is None:
        return kept
    return [card for card in kept if str(card.get("id") or "").removeprefix("inv:") in allowed]


def _graph_depth(state: dict | None) -> int:
    if not isinstance(state, dict) or "depth" not in state:
        return 1
    try:
        value = int(state.get("depth"))
    except (TypeError, ValueError):
        return 1
    return min(2, max(1, value))


def _keep_filters(spec: dict, filters: list[dict]) -> dict:
    if filters:
        spec["filters"] = _merge_filters(filters, list(spec.get("filters") or []))
    return spec


def _active_scenario(question: str, state: dict | None) -> str | None:
    for item in _filters(state):
        if item.get("kind") == "scenario" and item.get("id"):
            raw = str(item["id"]).removeprefix("scenario:").strip().lower()
            if raw in SCENARIOS:
                return raw
    found = _scenario_of(question)
    if found in SCENARIOS:
        return found
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _merge_filters(primary: list[dict], extra: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple] = set()
    for item in [*primary, *extra]:
        key = (item.get("kind"), str(item.get("id") or ""), str(item.get("label") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _scenario_relevance(question: str, named: list[str], scenario: str | None) -> bool:
    """Ranking for a named environment stays scenario even when the words include 'liên quan'."""
    if not scenario:
        return False
    if _RANKING.search(question):
        return True
    if named:
        return False
    return re.search(r"liên quan|lien quan", question, re.I) is not None


def classify(question: str, named: list[str], scenario: str | None) -> str:
    """Intent table in the viz spec. Structured views win over free text."""
    if _EVIDENCE.search(question):
        return "evidence"
    # Coverage screen also carries the export table when the question asks for both.
    if _COVERAGE.search(question) and _EXTRACT.search(question):
        return "coverage"
    if _EXTRACT.search(question):
        return "extract"
    if _COVERAGE.search(question):
        return "coverage"
    if _SENSOR.search(question):
        return "sensor"
    if _TOPIC.search(question):
        return "topic"
    if _COMPARE.search(question) and len(named) >= 2:
        return "compare"
    if _GRAPH_EXPLICIT.search(question):
        return "graph"
    relevance = _scenario_relevance(question, named, scenario)
    if named and _GRAPH_REL.search(question) and not _RANKING.search(question):
        return "graph"
    if not scenario and _GRAPH_REL.search(question):
        return "graph"
    if relevance:
        return "scenario"
    if _PROFILE.search(question) or (len(named) == 1 and not scenario):
        return "profile"
    if scenario or _SCENARIO_WORD.search(question):
        return "scenario"
    if _EXPLORE.search(question) or not question.strip():
        return "explore"
    if len(named) == 1:
        return "profile"
    return "qa"


def _scenario_of(question: str) -> str | None:
    if _GATEWAY.search(question):
        return "moon"
    return detect_scenario(question)


def _card_block(card: dict, alias_index: _AliasIndex | None = None) -> dict:
    fuels = [str(item) for item in (card.get("fuels") or []) if str(item).strip()]
    if alias_index is not None:
        fuels = _merge_fuel_labels(fuels, alias_index)
    return {
        "component": "InvestigationCard",
        "data": {
            "id": card.get("id"),
            "name": card.get("name"),
            "acronym": card.get("acronym") or None,
            "family": card.get("family"),
            "relevance": card.get("score"),
            "fuels": fuels,
            "runCount": card.get("runCount"),
            "doi": card.get("doi") or None,
        },
    }


def _document_title(hit: dict) -> str | None:
    for key in ("documentTitle", "title", "document"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    source = hit.get("source")
    if isinstance(source, str) and source.strip():
        return source.strip()
    return None


def _evidence_block(hits: list[dict], limit: int = MAX_EVIDENCE) -> dict:
    chunks = []
    refs = []
    for hit in hits[:limit]:
        chunk_id = hit.get("chunkId") or hit.get("id")
        chunk = {
            "id": chunk_id,
            "text": hit.get("preview") or hit.get("text") or "",
            "page": hit.get("page"),
            "documentTitle": _document_title(hit),
            "source": hit.get("source"),
            "figures": [],
        }
        span = hit.get("pages_span")
        if span not in (None, "", []):
            chunk["pages_span"] = span
        chunks.append(chunk)
        if chunk_id:
            refs.append({"kind": "chunk", "ref": chunk_id, "chunkId": chunk_id})
    block: dict[str, Any] = {"component": "EvidencePanel", "data": {"chunks": chunks}}
    if refs:
        block["evidence"] = refs
    return block


def _chunk_badge(chunk_id: str, page: Any = None) -> dict:
    data: dict[str, Any] = {"kind": "chunk", "ref": chunk_id, "chunkId": chunk_id}
    if page is not None:
        data["page"] = page
    return {"component": "SourceBadge", "data": data}


def _safe_hits(store: GraphStore, query: str, limit: int = 3) -> list[dict]:
    try:
        data = store.local_search(query, limit)
    except Exception:  # noqa: BLE001
        return []
    return list(data.get("hits") or [])


def _rank_scores(store: GraphStore, scenario: str | None) -> dict[str, float]:
    if not scenario or scenario not in SCENARIOS:
        return {}
    ranker = getattr(store, "rank_investigations", None)
    if not callable(ranker):
        return {}
    try:
        ranked = ranker(scenario, None, 25) or {}
    except Exception:  # noqa: BLE001
        return {}
    scores: dict[str, float] = {}
    for card in ranked.get("ranked") or []:
        psi_id = str(card.get("id") or "").removeprefix("inv:")
        score = _number(card.get("score"))
        if psi_id and score is not None:
            scores[psi_id] = score
    return scores


def _order_by_relevance(cards: list[dict], scores: dict[str, float]) -> list[dict]:
    if not scores:
        return cards
    ordered = []
    for index, card in enumerate(cards):
        copied = dict(card)
        psi_id = str(copied.get("id") or "")
        if psi_id in scores:
            copied["score"] = scores[psi_id]
        ordered.append((index, copied))

    def sort_key(item: tuple[int, dict]) -> tuple:
        index, card = item
        score = scores.get(str(card.get("id") or ""))
        if score is None:
            return (1, index, "")
        return (0, -score, index)

    ordered.sort(key=sort_key)
    return [card for _index, card in ordered]


def _corpus_count(store: GraphStore, query: str, key: str) -> int | None:
    try:
        rows = _run(store, query)
    except Exception:  # noqa: BLE001
        return None
    for row in rows or []:
        if key not in row or row.get(key) is None:
            continue
        try:
            return int(row[key])
        except (TypeError, ValueError):
            return None
    return None


def compose_explore(store: GraphStore, filters: list[dict], scenario: str | None = None) -> dict:
    cards = _restrict_cards(store, list(store.list_investigations() or []), filters)
    shown = list(filters)
    if scenario and not any(item.get("kind") == "scenario" for item in shown):
        shown.append({"kind": "scenario", "id": scenario, "label": scenario})
    cards = _order_by_relevance(cards, _rank_scores(store, scenario))
    alias_index = _fuel_alias_index(store)
    blocks: list[dict] = [
        {
            "component": "StatCard",
            "data": {"label": "Hồ sơ", "value": len(cards), "tone": "blue"},
            "evidence": [{"kind": "field", "ref": "Investigation"}],
        }
    ]
    blocks.extend(_overview_stats(store, cards, filters))
    blocks.extend(_card_block(card, alias_index) for card in cards[:25])
    if not cards:
        blocks.append(missing("investigations"))
    return _spec(
        "explore",
        "Khám phá investigation",
        blocks,
        filters=shown,
        followups=["Briefing cho ISS", "Hồ sơ SAME (PSI-102)", "Đồ thị quanh PSI-159"],
    )


def _text_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        if text[:1] == "[":
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                return [text]
        else:
            return [text]
    if isinstance(value, list):
        return [str(item).strip() for item in value if item not in (None, "") and str(item).strip()]
    return [str(value)]


def _profile_sources(card: dict) -> list[str] | None:
    sources = _text_list(card.get("sources"))
    return sources or None


def _profile_counts(store: GraphStore, psi_id: str) -> dict | None:
    try:
        rows = _run(
            store,
            """
            MATCH (i:Investigation {psiId: $psiId})
            OPTIONAL MATCH (c:Chunk)-[:IN_DOCUMENT]->(i)
            OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
            RETURN i.psiId AS psiId, count(DISTINCT d) AS documentCount, count(DISTINCT c) AS chunkCount
            LIMIT 1
            """,
            psiId=psi_id,
        )
    except Exception:  # noqa: BLE001
        return None
    for row in rows or []:
        row_psi = str(row.get("psiId") or "").removeprefix("inv:")
        if row_psi and row_psi != psi_id:
            continue
        if "documentCount" in row or "chunkCount" in row:
            return row
    return None


def _stat(label: str, value: Any, ref: str) -> dict:
    return {
        "component": "StatCard",
        "data": {"label": label, "value": value},
        "evidence": [{"kind": "field", "ref": ref}],
    }


def _hardware_items(card: dict) -> list[dict]:
    hardware = card.get("hardware")
    labels = _text_list(hardware)
    return [{"label": label} for label in labels]


def _relation_labels(store: GraphStore, query: str, psi_id: str) -> list[dict]:
    """Labels from one relationship. Ignore rows that are not {id, label}."""
    try:
        rows = _run(store, query, psiId=psi_id)
    except Exception:  # noqa: BLE001
        return []
    items = []
    seen: set[str] = set()
    for row in rows or []:
        if "label" not in row:
            continue
        label = str(row.get("label") or "").strip()
        raw_id = str(row.get("id") or "").strip()
        if not label and not raw_id:
            continue
        key = raw_id or label
        if key in seen:
            continue
        seen.add(key)
        items.append({"id": key, "label": label or raw_id})
    return items


def _same_clause(text: str) -> bool:
    stripped = re.sub(r"\d\.\d", "0", text)
    return "." not in stripped and ";" not in stripped and "!" not in stripped and "?" not in stripped


def _oxygen_bounds(raw: str) -> tuple[float, float] | None:
    text = raw.strip()
    ranged = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)", text)
    if ranged:
        lo = float(ranged.group(1))
        hi = float(ranged.group(2))
        return (lo, hi) if lo <= hi else (hi, lo)
    single = re.fullmatch(r"(\d+(?:\.\d+)?)", text)
    if single:
        value = float(single.group(1))
        return value, value
    return None


def _oxygen_figure(text: str) -> dict | None:
    """A percent that sits in the same clause as an oxygen word. No invented range."""
    if not text:
        return None
    for match in _OXYGEN_FIGURE.finditer(text):
        verbatim = match.group(0)
        if not _same_clause(verbatim):
            continue
        raw = match.group(1) or match.group(2)
        bounds = _oxygen_bounds(raw or "")
        if bounds is None:
            continue
        return {"min": bounds[0], "max": bounds[1], "note": verbatim}
    return None


def _oxygen_from_hits(hits: list[dict]) -> tuple[dict, dict] | None:
    for hit in hits:
        text = str(hit.get("text") or hit.get("preview") or "")
        figure = _oxygen_figure(text)
        chunk_id = str(hit.get("chunkId") or hit.get("id") or "").strip()
        if figure and chunk_id:
            return figure, hit
    return None


def _profile_oxygen_hits(store: GraphStore, psi_id: str) -> list[dict]:
    try:
        rows = _run(store, _OXYGEN_CHUNK_QUERY, psiId=psi_id)
    except Exception:  # noqa: BLE001
        return []
    hits = []
    for row in rows or []:
        if not row.get("text") and not row.get("preview"):
            continue
        hits.append(row)
    return hits


def _tested_fuels(store: GraphStore, psi_id: str) -> list[dict]:
    try:
        rows = _run(store, _TESTED_FUEL_QUERY, psiId=psi_id)
    except Exception:  # noqa: BLE001
        return []
    items = []
    for row in rows or []:
        label = str(row.get("label") or "").strip()
        raw_id = str(row.get("id") or "").strip()
        if not label and not raw_id:
            continue
        if "fuelId" in row and "label" not in row:
            continue
        items.append({"id": raw_id or label, "label": label or raw_id, "aliases": row.get("aliases")})
    return items


def _fuel_chip_items(labels: list[str], rows: list[dict] | None = None) -> list[dict]:
    index = _index_aliases(rows or [])
    merged = _merge_fuel_labels(labels, index)
    return [{"label": label} for label in merged]


def compose_profile(store: GraphStore, psi_id: str, filters: list[dict] | None = None) -> dict:
    card = store.get_investigation(psi_id)
    applied = list(filters or [])
    if not card:
        return _spec(
            "profile",
            f"Không có {psi_id}",
            [missing("investigation")],
            filters=applied,
            followups=["Liệt kê investigation"],
        )
    if not _card_matches(card, applied):
        return _spec("profile", f"Hồ sơ {psi_id}", [missing("investigation")], filters=applied)
    allowed = _relation_ids(store, applied)
    if allowed is not None and str(card.get("id") or "").removeprefix("inv:") not in allowed:
        return _spec("profile", f"Hồ sơ {psi_id}", [missing("investigation")], filters=applied)
    blocks: list[dict] = [
        {
            "component": "ProfileHeader",
            "data": {
                "id": card["id"],
                "name": card.get("name"),
                "acronym": card.get("acronym") or None,
                "summary": card.get("summary") or card.get("blurb") or "",
                "family": card.get("family"),
                "doi": card.get("doi") or None,
                "sources": _profile_sources(card),
            },
        }
    ]
    if card.get("runCount") is None:
        blocks.append(missing("runCount"))
    else:
        blocks.append(_stat("Số lần chạy", card["runCount"], "Investigation.runCount"))
    counts = _profile_counts(store, card["id"])
    if counts is not None:
        if counts.get("documentCount") is not None:
            blocks.append(_stat("Số tài liệu", counts["documentCount"], "IN_DOCUMENT"))
        if counts.get("chunkCount") is not None:
            blocks.append(_stat("Số chunk", counts["chunkCount"], "Chunk"))
    doi = str(card.get("doi") or "").strip()
    if doi:
        blocks.append(_stat("DOI", doi, "Investigation.doi"))
    else:
        blocks.append(missing("doi"))
    if card.get("oxygenMin") is None or card.get("oxygenMax") is None:
        quoted = _oxygen_from_hits(_profile_oxygen_hits(store, card["id"]))
        if quoted is None:
            quoted = _oxygen_from_hits(_safe_hits(store, f"oxygen O2 {card.get('name')} {card['id']}", 5))
        if quoted is None:
            blocks.append(missing("oxygen"))
        else:
            figure, hit = quoted
            chunk_id = str(hit.get("chunkId") or hit.get("id"))
            cited = {
                "kind": "chunk",
                "ref": chunk_id,
                "chunkId": chunk_id,
                "page": hit.get("page"),
            }
            if hit.get("pages_span") not in (None, "", []):
                cited["pages_span"] = hit.get("pages_span")
            blocks.append(
                {
                    "component": "GaugeO2",
                    "data": {
                        "min": figure["min"],
                        "max": figure["max"],
                        "note": figure["note"],
                        "missing": False,
                    },
                    "evidence": [cited],
                }
            )
            blocks.append(_chunk_badge(chunk_id, hit.get("page")))
    else:
        blocks.append(
            {
                "component": "GaugeO2",
                "data": {
                    "min": card["oxygenMin"],
                    "max": card["oxygenMax"],
                    "note": card.get("oxygenNote") or None,
                    "missing": False,
                },
                "evidence": [{"kind": "field", "ref": "Investigation.oxygenMin"}],
            }
        )
    fuel_labels = [str(fuel) for fuel in (card.get("fuels") or []) if str(fuel).strip()]
    fuel_rows: list[dict] = []
    if not fuel_labels:
        fuel_rows = _tested_fuels(store, card["id"])
        fuel_labels = [item["label"] for item in fuel_rows]
    fuel_items = _fuel_chip_items(fuel_labels, fuel_rows)
    if fuel_items:
        fuel_block: dict[str, Any] = {
            "component": "ChipList",
            "data": {"title": "Nhiên liệu / vật liệu", "items": fuel_items},
        }
        if fuel_rows:
            fuel_block["evidence"] = [
                {"id": item["id"], "kind": "graph", "ref": "TESTED_FUEL"} for item in fuel_rows
            ]
        blocks.append(fuel_block)
    else:
        blocks.append(missing("fuels"))
    flow_block = _flow_block(card)
    if flow_block:
        blocks.append(flow_block)
    hardware = _hardware_items(card)
    hardware_evidence: list[dict] = [{"kind": "field", "ref": "Investigation.hardware"}] if hardware else []
    if not hardware:
        linked_hardware = _relation_labels(store, _USED_HARDWARE_QUERY, card["id"])
        hardware = [{"label": item["label"]} for item in linked_hardware]
        hardware_evidence = [
            {"id": item["id"], "kind": "graph", "ref": "USED_HARDWARE"} for item in linked_hardware
        ]
    if hardware:
        blocks.append(
            {
                "component": "ChipList",
                "data": {"title": "Thiết bị", "items": hardware},
                "evidence": hardware_evidence,
            }
        )
    phenomena = _relation_labels(store, _STUDIED_QUERY, card["id"])
    if phenomena:
        blocks.append(
            {
                "component": "ChipList",
                "data": {"title": "Hiện tượng", "items": [{"label": item["label"]} for item in phenomena]},
                "evidence": [{"id": item["id"], "kind": "graph", "ref": "STUDIED"} for item in phenomena],
            }
        )
    hits = _safe_hits(store, f"{card.get('name')} {card['id']}", 3)
    blocks.append(_evidence_block(hits, 3))
    return _spec(
        "profile",
        f"Hồ sơ thí nghiệm — {card.get('name')} ({card['id']})",
        blocks,
        filters=_merge_filters(
            applied,
            [{"kind": "investigation", "id": card["id"], "label": card.get("name")}],
        ),
        followups=[
            f"Đồ thị quan hệ của {card['id']}",
            "So sánh với FLEX (PSI-69)",
            "Trang nào nói về oxy?",
        ],
    )


def _slug_body(raw_id: str, label: str) -> str:
    for candidate in (str(raw_id or "").strip(), str(label or "").strip()):
        match = _TYPED_ID.fullmatch(candidate)
        if not match:
            continue
        body = _PSI_TAIL.sub("", match.group(1)).strip()
        body = _LEADING_KIND.sub("", body, count=1)
        return _SCOPE_TAIL.sub("", body).strip()
    text = _PSI_TAIL.sub("", str(label or raw_id or "").strip()).strip()
    if ":" in text:
        text = _PSI_TAIL.sub("", text.split(":", 1)[-1]).strip()
    text = _LEADING_KIND.sub("", text, count=1)
    return _SCOPE_TAIL.sub("", text).strip()


def _rollup_key(raw_id: str, label: str) -> str:
    return _slug_body(raw_id, label).casefold()


class _AliasIndex:
    """Union slug keys with alias slugs. Ids that share neither stay apart."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, key: str) -> str:
        self.parent.setdefault(key, key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while key != root:
            nxt = self.parent[key]
            self.parent[key] = root
            key = nxt
        return root

    def union(self, keys: list[str]) -> None:
        cleaned = [key for key in keys if key]
        if not cleaned:
            return
        root = self.find(cleaned[0])
        for key in cleaned[1:]:
            other = self.find(key)
            if other != root:
                self.parent[other] = root


def _alias_slugs(raw_id: str, label: str, aliases: Any) -> list[str]:
    keys: list[str] = []
    primary = _rollup_key(raw_id, label)
    if primary:
        keys.append(primary)
    for alias in _text_list(aliases):
        folded = _rollup_key(str(alias), str(alias))
        if folded and folded not in keys:
            keys.append(folded)
    return keys


def _index_aliases(rows: list[dict], *, id_key: str = "id", label_key: str = "label", alias_key: str = "aliases") -> _AliasIndex:
    index = _AliasIndex()
    for row in rows or []:
        raw_id = str(row.get(id_key) or "")
        label = str(row.get(label_key) or raw_id)
        keys = _alias_slugs(raw_id, label, row.get(alias_key))
        if keys:
            index.union(keys)
    return index


def _fuel_alias_index(store: GraphStore) -> _AliasIndex:
    try:
        rows = _run(store, _FUEL_ALIAS_QUERY)
    except Exception:  # noqa: BLE001
        return _AliasIndex()
    usable = [row for row in rows or [] if row.get("aliases")]
    return _index_aliases(usable)


def _merge_fuel_labels(labels: list[str], index: _AliasIndex) -> list[str]:
    grouped: dict[str, str] = {}
    order: list[str] = []
    for label in labels:
        key = index.find(_rollup_key(label, label) or label.casefold())
        if key not in grouped:
            grouped[key] = label
            order.append(key)
    return [grouped[key] for key in order]


def _overview_stats(store: GraphStore, cards: list[dict], filters: list[dict]) -> list[dict]:
    """Family, document, and chunk totals from counts. No hardcoded family number."""
    stats: list[dict] = []
    families = {
        str(card.get("familyId") or card.get("family"))
        for card in cards
        if card.get("familyId") or card.get("family")
    }
    if _narrowing(filters):
        if families:
            stats.append(_stat("Số family", len(families), "Investigation.family"))
        linked = _linked_counts(store)
        shown = [str(card.get("id") or "").removeprefix("inv:") for card in cards]
        if linked and any(psi in linked for psi in shown):
            documents = 0
            chunks = 0
            for psi in shown:
                row = linked.get(psi) or {}
                documents += int(row.get("documentCount") or 0) if row else 0
                chunks += int(row.get("chunkCount") or 0) if row else 0
            stats.append(_stat("Số tài liệu", documents, "Document"))
            stats.append(_stat("Số chunk", chunks, "Chunk"))
        return stats
    family_count = _corpus_count(store, "MATCH (f:Family) RETURN count(f) AS familyCount LIMIT 1", "familyCount")
    if family_count is None and families:
        family_count = len(families)
    if family_count is not None:
        stats.append(_stat("Số family", family_count, "Family"))
    documents = _corpus_count(store, "MATCH (d:Document) RETURN count(d) AS documentTotal LIMIT 1", "documentTotal")
    chunks = _corpus_count(store, "MATCH (c:Chunk) RETURN count(c) AS chunkTotal LIMIT 1", "chunkTotal")
    if documents is not None:
        stats.append(_stat("Số tài liệu", documents, "Document"))
    if chunks is not None:
        stats.append(_stat("Số chunk", chunks, "Chunk"))
    return stats


def _candidate_display(raw_id: str, label: str) -> str:
    lab = str(label or "").strip()
    if lab and _TYPED_ID.fullmatch(lab) is None and _PSI_TAIL.search(lab) is None and ":" not in lab:
        return lab
    body = _slug_body(raw_id, label).replace("-", " ").strip()
    return body or lab or str(raw_id or "")


def _clean_label(label: str) -> str:
    text = str(label or "")
    return _candidate_display(text, text)


def _touch_group(groups: dict[str, dict], key: str, raw_id: str, label: str, weight: int) -> None:
    group = groups.setdefault(key, {"ids": {}, "labels": {}})
    group["ids"][raw_id] = group["ids"].get(raw_id, 0) + weight
    shown = _candidate_display(raw_id, label)
    group["labels"][shown] = group["labels"].get(shown, 0) + weight


def _materialize_group(key: str, group: dict) -> dict:
    primary = max(group["ids"], key=lambda item: (group["ids"][item], item))
    label = max(group["labels"], key=lambda item: (group["labels"][item], item))
    raw_ids = sorted(group["ids"], key=lambda item: (-group["ids"][item], item))
    return {
        "key": key,
        "id": primary,
        "label": label,
        "rawIds": raw_ids,
        "weight": sum(group["ids"].values()),
        "collidesWith": [],
    }


def _disambiguate(items: list[dict]) -> list[dict]:
    """Same cleaned label with different slugs stays two rows. Higher count keeps the label."""
    items.sort(key=lambda item: (-item["weight"], item["id"]))
    claimed: dict[str, dict] = {}
    for item in items:
        token = item["label"].casefold()
        prior = claimed.get(token)
        if prior is None:
            claimed[token] = item
            continue
        item["label"] = f"{item['label']} · {item['id']}"
        item["collidesWith"] = list(prior["rawIds"])
        prior["collidesWith"] = list(dict.fromkeys([*prior["collidesWith"], *item["rawIds"]]))
    return items


def _load_neighbors(store: GraphStore, psi_id: str, depth: int) -> dict | None:
    fetch = getattr(store, "graph_neighbors", None)
    if not callable(fetch):
        return None
    try:
        return fetch(psi_id, depth=depth)
    except TypeError:
        return fetch(psi_id)


def _mention_count(link: dict) -> int | None:
    for key in ("mentionCount", "mentions"):
        value = link.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number
    return None


def _mention_weights(store: GraphStore, psi_id: str, node_ids: list[str]) -> dict[str, int]:
    if not node_ids:
        return {}
    try:
        rows = _run(store, _MENTION_WEIGHT_QUERY, psiId=psi_id, ids=node_ids[:MAX_GRAPH_NODES])
    except Exception:  # noqa: BLE001
        return {}
    weights: dict[str, int] = {}
    for row in rows or []:
        node_id = str(row.get("id") or "").strip()
        if not node_id:
            continue
        try:
            weights[node_id] = int(row.get("n"))
        except (TypeError, ValueError):
            continue
    return weights


def _edge_explanation(relation: str, from_label: str, to_label: str) -> str | None:
    kind = str(relation or "").strip()
    left = str(from_label or "").strip()
    right = str(to_label or "").strip()
    if not kind or not left or not right:
        return None
    return f"Quan hệ {kind}: {left} — {right}."


def _node_matches_filters(node: dict, filters: list[dict]) -> bool:
    fuel = _fuel_filter(filters)
    sensor = _sensor_filter(filters)
    if fuel is None and sensor is None:
        return True
    hay = f"{node.get('label') or ''} {node.get('id') or ''}".casefold()
    if fuel is not None and fuel.casefold() not in hay:
        return False
    if sensor is not None and sensor.casefold() not in hay:
        return False
    return True


def _narrow_graph(nodes: list[dict], edges: list[dict], filters: list[dict], center_id: str) -> tuple[list[dict], list[dict]]:
    if _fuel_filter(filters) is None and _sensor_filter(filters) is None:
        return nodes, edges
    matched = {node["id"] for node in nodes if node.get("id") != center_id and _node_matches_filters(node, filters)}
    kept = {center_id, *matched}
    for edge in edges:
        if edge.get("to") in matched:
            kept.add(edge.get("from"))
        if edge.get("from") in matched and edge.get("to") == center_id:
            kept.add(edge.get("from"))
    kept.discard(None)
    return (
        [node for node in nodes if node.get("id") in kept],
        [edge for edge in edges if edge.get("from") in kept and edge.get("to") in kept],
    )


def compose_graph(store: GraphStore, psi_id: str, depth: int = 1, filters: list[dict] | None = None) -> dict:
    data = _load_neighbors(store, psi_id, depth)
    if not data:
        return _spec("graph", f"Không có đồ thị {psi_id}", [missing("graph")])
    center_id = data["id"]
    labels = {center_id: data.get("name") or center_id}
    nodes = [{"id": center_id, "label": labels[center_id], "type": "investigation", "weight": 1}]
    edges: list[dict] = []
    seen = {center_id}
    seen_edges: set[tuple] = set()
    for link in data.get("links") or []:
        if str(link.get("relation") or "").upper() == "MENTIONS":
            continue
        node_id = link.get("id")
        relation = str(link.get("relation") or "").strip()
        if not node_id or not relation or node_id == center_id:
            continue
        from_id = link.get("fromId") or center_id
        if from_id not in seen:
            continue
        if node_id not in seen:
            if len(nodes) >= MAX_GRAPH_NODES:
                continue
            label = _clean_label(link.get("label") or node_id)
            labels[node_id] = label
            seen.add(node_id)
            counted = _mention_count(link)
            nodes.append(
                {
                    "id": node_id,
                    "label": label,
                    "type": link.get("type") or "unknown",
                    "weight": counted if counted is not None else 1,
                    "_counted": counted is not None,
                }
            )
        if len(edges) >= MAX_GRAPH_EDGES:
            break
        key = tuple(sorted((str(from_id), str(node_id)))) + (relation,)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        edge = {"from": from_id, "to": node_id, "type": relation}
        explanation = _edge_explanation(relation, labels.get(from_id, ""), labels.get(node_id, ""))
        if explanation:
            edge["explanation"] = explanation
        edges.append(edge)
    pending = [node["id"] for node in nodes if node.get("id") != center_id and not node.get("_counted")]
    counted = _mention_weights(store, str(center_id).removeprefix("inv:"), pending)
    for node in nodes:
        node_id = node.get("id")
        if node_id in counted and not node.get("_counted"):
            node["weight"] = counted[node_id]
        node.pop("_counted", None)
    applied = list(filters or [])
    nodes, edges = _narrow_graph(nodes, edges, applied, center_id)
    chips = _merge_filters(
        applied,
        [{"kind": "investigation", "id": center_id, "label": data.get("name")}],
    )
    return _spec(
        "graph",
        f"Đồ thị quanh {data.get('name')} ({center_id})",
        [{"component": "NodeGraph", "data": {"nodes": nodes[:MAX_GRAPH_NODES], "edges": edges[:MAX_GRAPH_EDGES]}}],
        filters=chips,
        followups=[f"Hồ sơ {center_id}", "Xem bằng chứng", "Briefing cho ISS"],
    )


def _compare_cell(value: Any) -> tuple[Any, bool]:
    if value is None or value == "" or value == []:
        return "—", True
    if isinstance(value, str) and not value.strip():
        return "—", True
    if isinstance(value, list):
        text = ", ".join(str(item) for item in value if item not in (None, "") and str(item).strip())
        if not text:
            return "—", True
        return text, False
    return value, False


def _compare_row(field: str, raw_values: list[Any]) -> dict:
    values = []
    flags = []
    for value in raw_values:
        shown, absent = _compare_cell(value)
        values.append(shown)
        flags.append(absent)
    highlight = len({str(value) for value in values}) > 1
    return {"field": field, "values": values, "missing": flags, "highlightDiff": highlight}


def compose_compare(store: GraphStore, ids: list[str], filters: list[dict] | None = None) -> dict:
    chosen = ids[:3]
    cards = []
    for psi_id in chosen:
        card = store.get_investigation(psi_id)
        if card:
            cards.append(card)
    cards = _restrict_cards(store, cards, list(filters or []))
    if len(cards) < 2:
        return _spec("compare", "Chưa đủ hồ sơ để so sánh", [missing("compare")], filters=list(filters or []))
    columns = [card["id"] for card in cards]

    oxygen = []
    for card in cards:
        if card.get("oxygenMin") is None or card.get("oxygenMax") is None:
            oxygen.append(None)
        else:
            oxygen.append(card.get("oxygen"))

    def optional_row(field: str, values: list[Any]) -> dict | None:
        if not any(not _compare_cell(value)[1] for value in values):
            return None
        return _compare_row(field, values)

    rows = [
        _compare_row("family", [card.get("family") for card in cards]),
        _compare_row("fuels", [card.get("fuels") or None for card in cards]),
        _compare_row("oxygen", oxygen),
        _compare_row("runCount", [card.get("runCount") for card in cards]),
    ]

    def phenomenon_of(card: dict) -> tuple[Any, list[dict]]:
        value = card.get("phenomenon")
        if value not in (None, "", []):
            return value, [{"kind": "field", "ref": "Investigation.phenomenon"}]
        if card.get("phenomena") not in (None, "", []):
            return card.get("phenomena"), [{"kind": "field", "ref": "Investigation.phenomenon"}]
        linked = _relation_labels(store, _STUDIED_QUERY, card["id"])
        if not linked:
            return None, []
        return [item["label"] for item in linked], [
            {"id": item["id"], "kind": "graph", "ref": "STUDIED"} for item in linked
        ]

    def hardware_of(card: dict) -> tuple[Any, list[dict]]:
        if card.get("hardware") not in (None, "", []):
            return card.get("hardware"), [{"kind": "field", "ref": "Investigation.hardware"}]
        linked = _relation_labels(store, _USED_HARDWARE_QUERY, card["id"])
        if not linked:
            return None, []
        return [item["label"] for item in linked], [
            {"id": item["id"], "kind": "graph", "ref": "USED_HARDWARE"} for item in linked
        ]

    def chunk_count_of(card: dict) -> tuple[Any, list[dict]]:
        current = card.get("chunkCount")
        if current is not None and current != "":
            return current, [{"kind": "field", "ref": "Investigation.chunkCount"}]
        counted = _profile_counts(store, card["id"])
        if counted and counted.get("chunkCount") is not None:
            return counted.get("chunkCount"), [{"kind": "field", "ref": "Chunk"}]
        return None, []

    for field, pairs in (
        ("facility", [(card.get("facility"), []) for card in cards]),
        ("hardware", [hardware_of(card) for card in cards]),
        ("phenomenon", [phenomenon_of(card) for card in cards]),
        ("chunkCount", [chunk_count_of(card) for card in cards]),
    ):
        extra = optional_row(field, [pair[0] for pair in pairs])
        if extra:
            evidence = [item for _value, items in pairs for item in items]
            if evidence:
                extra["evidence"] = evidence
            rows.append(extra)
    return _spec(
        "compare",
        "So sánh " + " và ".join(columns),
        [
            {
                "component": "CompareTable",
                "data": {"columns": columns, "rows": rows},
            }
        ],
        filters=list(filters or []),
        followups=[f"Hồ sơ {columns[0]}", f"Đồ thị quanh {columns[1]}"],
    )


def _scenario_briefing_extras(store: GraphStore, scenario: str, spec: dict) -> tuple[dict, dict]:
    """Copy grounded scenario_context fields. Do not invent risks, sensors, or gaps."""
    getter = getattr(store, "scenario_context", None)
    extras: dict[str, Any] = {}
    if not callable(getter):
        return spec, extras
    try:
        ctx = getter(scenario) or {}
    except Exception:  # noqa: BLE001
        return spec, extras
    remote = ctx.get("scenario") or {}
    merged = dict(spec)
    if not merged.get("label") and remote.get("label"):
        merged["label"] = remote["label"]
    if not merged.get("caveat") and remote.get("caveat"):
        merged["caveat"] = remote["caveat"]
    notes = []
    for note in ctx.get("notes") or []:
        if isinstance(note, dict) and str(note.get("summary") or "").strip():
            notes.append(
                {
                    "label": note.get("label"),
                    "summary": note.get("summary"),
                    "url": note.get("url"),
                }
            )
    if notes:
        extras["context"] = notes
    for source_key, target_key in (("phenomena", "risks"), ("sensors", "sensors"), ("gaps", "gaps")):
        value = ctx.get(source_key)
        if isinstance(value, list) and value:
            extras[target_key] = value
    return merged, extras


def compose_scenario(store: GraphStore, scenario: str, filters: list[dict] | None = None) -> dict:
    ranked = store.rank_investigations(scenario, None, 5)
    spec, extras = _scenario_briefing_extras(store, scenario, ranked.get("scenario") or {})
    cards = ranked.get("ranked") or []
    if filters:
        cards = _restrict_cards(store, list(cards), filters)
    investigations = [
        {
            "id": card["id"],
            "name": card.get("name"),
            "relevance": card.get("score"),
            "reason": card.get("reason") or "",
            "evidence": [{"kind": "graph", "ref": "RELEVANT_TO", "id": card["id"]}],
        }
        for card in cards
    ]
    briefing = {
        "id": scenario,
        "label": spec.get("label"),
        "caveat": spec.get("caveat"),
        "investigations": investigations,
    }
    briefing.update(extras)
    blocks = [
        {
            "component": "ScenarioBriefing",
            "data": briefing,
        },
        {
            "component": "SourceBadge",
            "data": {"kind": "field", "ref": "RELEVANT_TO"},
        },
    ]
    blocks.extend(_card_block(card) for card in cards)
    if not cards:
        blocks.append(missing("ranking"))
    return _spec(
        "scenario",
        spec.get("label") or scenario,
        blocks,
        filters=_merge_filters(
            list(filters or []),
            [{"kind": "scenario", "id": scenario, "label": spec.get("label") or scenario}],
        ),
        followups=["Hồ sơ PSI đứng đầu", "Đồ thị quanh PSI đứng đầu", "Liệt kê investigation"],
    )


def compose_evidence(store: GraphStore, question: str, named: list[str]) -> dict:
    query = question.strip() or (named[0] if named else "fire safety")
    hits = _safe_hits(store, query, MAX_EVIDENCE)
    blocks = [_evidence_block(hits)]
    if not hits:
        blocks.append(missing("chunks"))
    title = f"Bằng chứng cho {named[0]}" if named else "Bằng chứng"
    return _spec("evidence", title, blocks, followups=["Hồ sơ investigation", "Đồ thị quan hệ"])


def compose_qa(store: GraphStore, question: str) -> dict:
    hits = _safe_hits(store, question or "microgravity fire", MAX_EVIDENCE)
    quotes = [str(hit.get("preview") or "").strip() for hit in hits if hit.get("preview")]
    text = " ".join(quotes) if quotes else "Chưa có đoạn trích nào khớp câu hỏi trong đồ thị."
    blocks = [_evidence_block(hits)]
    if not hits:
        blocks.append(missing("chunks"))
    return _spec(
        "qa",
        "Hỏi đáp",
        blocks,
        text=text,
        followups=["Xem hồ sơ SAME (PSI-102)", "Briefing cho ISS", "Đồ thị quanh PSI-159"],
    )


def _flow_text(card: dict) -> str | None:
    raw = card.get("flowRaw")
    if raw is not None and str(raw).strip():
        text = format_flow(str(raw).strip())
    else:
        flow = card.get("flow")
        if flow is None:
            return None
        text = str(flow).strip()
    if not text or text == _NO_FLOW:
        return None
    return text


def _flow_block(card: dict) -> dict | None:
    text = _flow_text(card)
    if not text:
        return None
    return {
        "component": "FlowSteps",
        "data": {"steps": [{"title": "Dòng khí", "text": text}]},
        "evidence": [{"kind": "field", "ref": "Investigation.flow"}],
    }


def _run(store: GraphStore, query: str, **params: Any) -> list[dict]:
    runner = getattr(store, "run", None)
    if not callable(runner):
        return []
    return list(runner(query, **params) or [])


def _blank(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _oxygen_cell(value: Any) -> str:
    """Missing oxygen stays blank. Never coerce an empty catalog value to 0."""
    if value is None or value == "":
        return ""
    if isinstance(value, str) and "no oxygen" in value.lower():
        return ""
    return _num_cell(value)


def _num_cell(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    return str(value)


def _fuels_cell(fuels: Any) -> str:
    if fuels is None or fuels == "":
        return ""
    if isinstance(fuels, str):
        text = fuels.strip()
        if text[:1] == "[":
            try:
                fuels = json.loads(text)
            except json.JSONDecodeError:
                return text
        else:
            return text
    if isinstance(fuels, list):
        return "; ".join(str(item) for item in fuels if item not in (None, ""))
    return str(fuels)


def _filled(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _coverage_fields(cards: list[dict]) -> list[list[Any]]:
    total = len(cards)
    checks = (
        ("summary", lambda card: _filled(card.get("summary"))),
        ("acronym", lambda card: _filled(card.get("acronym"))),
        ("blurb", lambda card: _filled(card.get("blurb"))),
        ("relevance", lambda card: _filled(card.get("relevance"))),
        ("scores", lambda card: _filled(card.get("scores"))),
        ("aliases", lambda card: _filled(card.get("aliases"))),
        ("doi", lambda card: _filled(card.get("doi"))),
        ("sources", lambda card: _filled(card.get("sources"))),
        ("runCount", lambda card: card.get("runCount") is not None),
        ("fuels", lambda card: _filled(card.get("fuels"))),
        ("measurements", lambda card: _filled(card.get("measurements"))),
        ("sampleForm", lambda card: _filled(card.get("sampleForm"))),
        ("oxygenMin", lambda card: card.get("oxygenMin") is not None),
        ("oxygenMax", lambda card: card.get("oxygenMax") is not None),
        ("oxygenNote", lambda card: _filled(card.get("oxygenNote"))),
        ("oxygen", lambda card: card.get("oxygenMin") is not None and card.get("oxygenMax") is not None),
    )
    rows = []
    for name, present_of in checks:
        present = sum(1 for card in cards if present_of(card))
        rows.append([name, present, total - present, total])
    return rows


def _read_coverage_cache() -> dict | None:
    if not COVERAGE_PATH.is_file():
        return None
    try:
        payload = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    cached_at = payload.get("cachedAt")
    data = payload.get("data")
    if not isinstance(cached_at, (int, float)) or not isinstance(data, dict):
        return None
    if time.time() - float(cached_at) > COVERAGE_TTL_SECONDS:
        return None
    if "heatmap" not in data or data.get("rollup") != 4:
        return None
    return data


def _write_coverage_cache(data: dict) -> None:
    try:
        COVERAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = COVERAGE_PATH.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"cachedAt": time.time(), "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(COVERAGE_PATH)
    except OSError:
        return


def _chunk_series(rows: list[dict]) -> list[dict]:
    series = []
    for row in rows:
        label = row.get("source")
        count = row.get("n")
        if label is None or str(label).strip() == "" or count is None:
            continue
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        series.append({"label": str(label), "value": value})
        if len(series) >= MAX_COVERAGE_SOURCES:
            break
    return series


def _empty_heatmap(*, failed: bool = False) -> dict:
    caption = _HEATMAP_CAPTION
    if failed:
        caption += " Truy vấn có giới hạn bị lỗi; không điền số thay thế."
    return {"x": [], "y": [], "cells": [], "caption": caption, "evidence": []}


def _shape_heatmap(rows: list[dict], source_chunks: dict[str, int]) -> dict:
    """Normalize co-mentions by source chunk count. Axes follow that ratio, not raw totals."""
    fuel_index = _AliasIndex()
    phen_index = _AliasIndex()
    parsed: list[tuple] = []
    for row in rows:
        fuel_id = str(row.get("fuelId") or row.get("fuel") or "").strip()
        phen_id = str(row.get("phenomenonId") or row.get("phenomenon") or "").strip()
        source = str(row.get("source") or "").strip()
        if not fuel_id or not phen_id or not source:
            continue
        try:
            count = float(row.get("n"))
        except (TypeError, ValueError):
            continue
        if count < 0:
            continue
        fuel_label = str(row.get("fuelLabel") or fuel_id)
        phen_label = str(row.get("phenomenonLabel") or phen_id)
        fuel_keys = _alias_slugs(fuel_id, fuel_label, row.get("fuelAliases")) or [fuel_id.casefold()]
        phen_keys = _alias_slugs(phen_id, phen_label, row.get("phenomenonAliases")) or [phen_id.casefold()]
        fuel_index.union(fuel_keys)
        phen_index.union(phen_keys)
        parsed.append((fuel_id, fuel_label, fuel_keys[0], phen_id, phen_label, phen_keys[0], source, count))
    groups_fuel: dict[str, dict] = {}
    groups_phen: dict[str, dict] = {}
    raw: dict[tuple[str, str, str], float] = {}
    for fuel_id, fuel_label, fuel_primary, phen_id, phen_label, phen_primary, source, count in parsed:
        fuel_key = fuel_index.find(fuel_primary)
        phen_key = phen_index.find(phen_primary)
        _touch_group(groups_fuel, fuel_key, fuel_id, fuel_label, int(count) or 1)
        _touch_group(groups_phen, phen_key, phen_id, phen_label, int(count) or 1)
        slot = (fuel_key, phen_key, source)
        raw[slot] = raw.get(slot, 0.0) + count
    shares: dict[tuple[str, str], float] = {}
    axis_fuel: dict[str, float] = {}
    axis_phen: dict[str, float] = {}
    for (fuel_key, phen_key, source), count in raw.items():
        denom = source_chunks.get(source)
        if denom is None or denom <= 0:
            continue
        share = count / float(denom)
        shares[(fuel_key, phen_key)] = shares.get((fuel_key, phen_key), 0.0) + share
        axis_fuel[fuel_key] = axis_fuel.get(fuel_key, 0.0) + share
        axis_phen[phen_key] = axis_phen.get(phen_key, 0.0) + share
    fuels = _disambiguate([_materialize_group(key, group) for key, group in groups_fuel.items()])
    phens = _disambiguate([_materialize_group(key, group) for key, group in groups_phen.items()])
    for item in fuels:
        item["axis"] = axis_fuel.get(item["key"], 0.0)
    for item in phens:
        item["axis"] = axis_phen.get(item["key"], 0.0)
    fuels.sort(key=lambda item: (-item["axis"], item["label"]))
    phens.sort(key=lambda item: (-item["axis"], item["label"]))
    fuels = fuels[:MAX_HEATMAP_FUELS]
    phens = phens[:MAX_HEATMAP_PHENOMENA]
    fuel_by_key = {item["key"]: item for item in fuels}
    phen_by_key = {item["key"]: item for item in phens}
    cells = []
    for (fuel_key, phen_key), value in shares.items():
        fuel = fuel_by_key.get(fuel_key)
        phen = phen_by_key.get(phen_key)
        if fuel is None or phen is None:
            continue
        shown = round(value, 4)
        if shown == 0:
            continue
        cells.append({"x": phen["label"], "y": fuel["label"], "v": shown})
    cells.sort(key=lambda cell: (cell["y"], cell["x"]))
    evidence = []
    for item in [*fuels, *phens]:
        evidence.append({"id": item["id"], "rawIds": item["rawIds"], "kind": "graph", "ref": "MENTIONS"})
        if item["collidesWith"]:
            evidence.append(
                {
                    "ids": list(dict.fromkeys([*item["rawIds"], *item["collidesWith"]])),
                    "kind": "graph",
                    "ref": "MENTIONS",
                }
            )
    return {
        "x": [item["label"] for item in phens],
        "y": [item["label"] for item in fuels],
        "cells": cells,
        "caption": _HEATMAP_CAPTION,
        "evidence": evidence,
    }


def _filter_series(series: list[dict], filters: list[dict]) -> list[dict]:
    focus = _investigation_filter(filters)
    if not focus:
        return series
    needle = focus.casefold()
    return [item for item in series if needle in str(item.get("label") or "").casefold()]


def _filter_heatmap(heatmap: dict, filters: list[dict]) -> dict:
    fuel = _fuel_filter(filters)
    if not fuel:
        return heatmap
    copied = dict(heatmap)
    copied["y"] = [label for label in heatmap.get("y") or [] if fuel in str(label).casefold()]
    copied["cells"] = [
        cell for cell in heatmap.get("cells") or [] if fuel in str(cell.get("y") or "").casefold()
    ]
    return copied


def _is_pair_row(row: dict) -> bool:
    return bool((row.get("fuelId") or row.get("fuel")) and (row.get("phenomenonId") or row.get("phenomenon")))


def _axis_pick(rows: list[dict], source_chunks: dict[str, int], id_key: str, label_key: str, alias_key: str, limit: int) -> list[dict]:
    index = _AliasIndex()
    parsed = []
    for row in rows or []:
        raw_id = str(row.get(id_key) or "").strip()
        label = str(row.get(label_key) or raw_id)
        source = str(row.get("source") or "").strip()
        if not raw_id or not source:
            continue
        try:
            count = float(row.get("n"))
        except (TypeError, ValueError):
            continue
        denom = source_chunks.get(source)
        if denom is None or denom <= 0 or count < 0:
            continue
        keys = _alias_slugs(raw_id, label, row.get(alias_key)) or [raw_id.casefold()]
        index.union(keys)
        parsed.append((raw_id, label, keys[0], count / float(denom), int(count) or 1))
    groups: dict[str, dict] = {}
    scores: dict[str, float] = {}
    for raw_id, label, primary, share, count in parsed:
        key = index.find(primary)
        _touch_group(groups, key, raw_id, label, count)
        scores[key] = scores.get(key, 0.0) + share
    items = []
    for key, group in groups.items():
        item = _materialize_group(key, group)
        item["axis"] = scores.get(key, 0.0)
        items.append(item)
    items = _disambiguate(items)
    items.sort(key=lambda item: (-item.get("axis", 0.0), item["label"]))
    return items[:limit]


def _heatmap_rows(store: GraphStore, source_chunks: dict[str, int]) -> list[dict]:
    """Pair rows. Axis candidates are source-capped, then ranked by normalized share."""
    fuel_rows = _run(
        store,
        _FUEL_AXIS_QUERY,
        perSource=_AXIS_PER_SOURCE,
        limit=MAX_COVERAGE_SOURCES * _AXIS_PER_SOURCE,
    )
    if fuel_rows and _is_pair_row(fuel_rows[0]):
        return list(fuel_rows)
    phen_rows = _run(
        store,
        _PHENOMENON_AXIS_QUERY,
        perSource=_AXIS_PER_SOURCE,
        limit=MAX_COVERAGE_SOURCES * _AXIS_PER_SOURCE,
    )
    if not fuel_rows or not phen_rows:
        return []
    if _is_pair_row(phen_rows[0]):
        return list(phen_rows)
    fuels = _axis_pick(fuel_rows, source_chunks, "fuelId", "fuelLabel", "fuelAliases", MAX_HEATMAP_FUELS)
    phens = _axis_pick(
        phen_rows,
        source_chunks,
        "phenomenonId",
        "phenomenonLabel",
        "phenomenonAliases",
        MAX_HEATMAP_PHENOMENA,
    )
    if not fuels or not phens:
        return []
    return _run(
        store,
        _HEATMAP_PAIRS_QUERY,
        fuelIds=[item["id"] for item in fuels],
        phenIds=[item["id"] for item in phens],
        pairLimit=MAX_HEATMAP_FUELS * MAX_HEATMAP_PHENOMENA * 64,
    )


def _coverage_spec(
    series: list[dict],
    field_rows: list[list[Any]],
    heatmap: dict,
    *,
    chunk_error: bool,
    filters: list[dict] | None = None,
) -> dict:
    caption = _COVERAGE_CAPTION
    if chunk_error:
        caption += " Chưa đếm được chunk theo nguồn vì truy vấn đồ thị lỗi."
    heat = heatmap or _empty_heatmap()
    blocks = [
        {
            "component": "BarChart",
            "data": {"series": series, "unit": "chunk", "caption": caption},
            "evidence": [{"kind": "graph", "ref": "Chunk.psi"}],
        },
        {
            "component": "HeatMap",
            "data": {
                "x": list(heat.get("x") or []),
                "y": list(heat.get("y") or []),
                "cells": list(heat.get("cells") or []),
                "caption": heat.get("caption") or _HEATMAP_CAPTION,
            },
            "evidence": list(heat.get("evidence") or []),
        },
        {
            "component": "DataTable",
            "data": {
                "columns": ["trường", "có mặt", "thiếu", "tổng"],
                "rows": field_rows,
                "warnings": [],
                "caption": caption,
            },
            "evidence": [{"kind": "field", "ref": "Investigation"}],
        },
        {
            "component": "SourceBadge",
            "data": {"kind": "field", "ref": "Investigation.oxygenMin"},
        },
        {
            "component": "ExportCSV",
            "data": {"href": "/api/viz/extract.csv", "filename": "extract.csv"},
        },
    ]
    return _spec(
        "coverage",
        "Độ phủ dữ liệu",
        blocks,
        filters=list(filters or []),
        followups=["Xuất bảng dữ liệu", "Ma trận cảm biến", "Liệt kê investigation"],
    )


def compose_coverage(store: GraphStore, filters: list[dict] | None = None) -> dict:
    """Chunk counts, field presence, and a cached fuel × phenomenon heatmap."""
    applied = list(filters or [])
    cached = _read_coverage_cache()
    if cached is not None and not _narrowing(applied):
        return _coverage_spec(
            list(cached.get("series") or []),
            list(cached.get("fields") or []),
            dict(cached.get("heatmap") or {}),
            chunk_error=bool(cached.get("chunkError")),
        )
    try:
        cards = list(store.list_investigations() or [])
    except Exception:  # noqa: BLE001
        return _spec("coverage", "Không đọc được độ phủ", [missing("coverage")], filters=applied)
    if cached is not None:
        shown = _restrict_cards(store, cards, applied)
        return _coverage_spec(
            _filter_series(list(cached.get("series") or []), applied),
            _coverage_fields(shown),
            _filter_heatmap(dict(cached.get("heatmap") or {}), applied),
            chunk_error=bool(cached.get("chunkError")),
            filters=applied,
        )
    chunk_error = False
    try:
        chunk_rows = _run(
            store,
            """
            MATCH (c:Chunk)
            WITH coalesce(c.psi, c.source, 'unknown') AS source
            RETURN source AS source, count(*) AS n
            ORDER BY n DESC
            LIMIT $limit
            """,
            limit=MAX_COVERAGE_SOURCES,
        )
    except Exception:  # noqa: BLE001
        chunk_rows = []
        chunk_error = True
    series = _chunk_series(chunk_rows)
    fields = _coverage_fields(cards)
    heatmap_error = False
    if chunk_error:
        heatmap = _empty_heatmap(failed=True)
    else:
        try:
            source_chunks = {item["label"]: int(item["value"]) for item in series}
            heat_rows = _heatmap_rows(store, source_chunks)
            heatmap = _shape_heatmap(heat_rows, source_chunks)
        except Exception:  # noqa: BLE001
            heatmap_error = True
            heatmap = _empty_heatmap(failed=True)
    if not chunk_error:
        _write_coverage_cache(
            {
                "series": series,
                "fields": fields,
                "chunkError": False,
                "heatmap": heatmap,
                "heatmapError": heatmap_error,
                "rollup": 4,
            }
        )
    shown = _restrict_cards(store, cards, applied) if _narrowing(applied) else cards
    return _coverage_spec(
        _filter_series(series, applied),
        _coverage_fields(shown) if _narrowing(applied) else fields,
        _filter_heatmap(heatmap, applied),
        chunk_error=chunk_error,
        filters=applied,
    )


def _measurements_cell(value: Any) -> str:
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "[]":
            return ""
        if text[:1] == "[":
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                return text
        else:
            return text
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if item not in (None, "") and str(item).strip()]
        return "; ".join(parts)
    return str(value)


def _count_cell(value: Any) -> str:
    if value is None or value == "" or isinstance(value, bool):
        return ""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    if number < 0:
        return ""
    return str(number)


def _linked_counts(store: GraphStore) -> dict[str, dict]:
    try:
        rows = _run(store, _COUNT_QUERY)
    except Exception:  # noqa: BLE001
        return {}
    found: dict[str, dict] = {}
    for row in rows or []:
        psi = str(row.get("psiId") or "").removeprefix("inv:").strip()
        if psi:
            found[psi] = row
    return found


def _extract_rows(store: GraphStore, filters: list[dict] | None = None) -> list[dict]:
    cards = _restrict_cards(store, list(store.list_investigations() or []), list(filters or []))
    counts = _linked_counts(store)
    rows = []
    for card in cards:
        psi_id = str(card.get("id") or "")
        linked = counts.get(psi_id)
        rows.append(
            {
                "id": psi_id,
                "acronym": _blank(card.get("acronym")),
                "family": _blank(card.get("family")),
                "fuels": _fuels_cell(card.get("fuels")),
                "oxygenMin": _oxygen_cell(card.get("oxygenMin")),
                "oxygenMax": _oxygen_cell(card.get("oxygenMax")),
                "runCount": _num_cell(card.get("runCount")),
                "sampleForm": _blank(card.get("sampleForm")),
                "measurements": _measurements_cell(card.get("measurements")),
                "documentCount": _count_cell(linked.get("documentCount")) if linked else "",
                "chunkCount": _count_cell(linked.get("chunkCount")) if linked else "",
            }
        )
    rows.sort(key=lambda row: row["id"])
    return rows


def _extract_warnings(rows: list[dict]) -> list[str]:
    total = len(rows)
    if total == 0:
        return ["Không có investigation để xuất."]
    warnings = []
    for field in EXTRACT_COLUMNS:
        present = sum(1 for row in rows if row.get(field) not in ("", None))
        if present * 2 < total:
            warnings.append(f"{field} covered {present}/{total} (<50%)")
    return warnings


def extract_csv(store: GraphStore) -> str:
    rows = _extract_rows(store, None)
    warnings = _extract_warnings(rows)
    buffer = io.StringIO()
    if warnings:
        buffer.write("# warning: " + "; ".join(warnings) + "\n")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXTRACT_COLUMNS)
    for row in rows:
        writer.writerow([row[column] for column in EXTRACT_COLUMNS])
    return buffer.getvalue()


def compose_extract(store: GraphStore, filters: list[dict] | None = None) -> dict:
    try:
        rows = _extract_rows(store, filters)
    except Exception:  # noqa: BLE001
        return _spec("extract", "Không xuất được bảng", [missing("extract")])
    warnings = _extract_warnings(rows)
    table_rows = [[row[column] for column in EXTRACT_COLUMNS] for row in rows]
    return _spec(
        "extract",
        "Trích xuất dataset",
        [
            {
                "component": "DataTable",
                "data": {
                    "columns": list(EXTRACT_COLUMNS),
                    "rows": table_rows,
                    "warnings": warnings,
                },
                "evidence": [{"kind": "field", "ref": "Investigation"}],
            },
            {
                "component": "ExportCSV",
                "data": {"href": "/api/viz/extract.csv", "filename": "extract.csv"},
            },
        ],
        followups=["Độ phủ dữ liệu", "Hồ sơ investigation", "Ma trận cảm biến"],
    )


def _normalize_community_id(value: str) -> str:
    match = re.fullmatch(r"community:L(\d+):(\d+)", value.strip(), re.I)
    if not match:
        return value.strip()
    return f"community:L{match.group(1)}:{match.group(2)}"


def _community_focus(question: str, state: dict | None) -> str | None:
    for item in _filters(state):
        if item.get("kind") == "community" and item.get("id"):
            return _normalize_community_id(str(item["id"]))
    extra = (state or {}).get("communityId")
    if isinstance(extra, str) and extra.strip():
        return _normalize_community_id(extra)
    match = _COMMUNITY_ID.search(question or "")
    if match:
        return _normalize_community_id(match.group(0))
    return None


def _community_node(row: dict) -> dict | None:
    raw_id = row.get("id")
    if not raw_id:
        return None
    level = row.get("level")
    if isinstance(level, str) and level.isdigit():
        level = int(level)
    parent = row.get("parent") or row.get("parentId") or row.get("parent_id")
    node = {
        "id": _normalize_community_id(str(raw_id)),
        "title": str(row.get("title") or raw_id),
        "summary": str(row.get("summary") or ""),
        "level": level,
        "parent": _normalize_community_id(str(parent)) if parent else None,
        "size": row.get("size"),
    }
    psi_ids = _text_list(row.get("psiIds"))
    if psi_ids:
        node["psiIds"] = [item.removeprefix("inv:") for item in psi_ids]
    chunk_ids = _text_list(row.get("chunkIds"))
    if chunk_ids:
        node["chunkIds"] = chunk_ids
    return node


def _breadcrumb(by_id: dict[str, dict], focus: str) -> list[dict]:
    chain: list[dict] = []
    seen: set[str] = set()
    current: str | None = focus
    while current and current not in seen:
        seen.add(current)
        node = by_id.get(current)
        if node is None:
            chain.append({"id": current, "title": current, "level": None})
            break
        chain.append({"id": node["id"], "title": node["title"], "level": node.get("level")})
        current = node.get("parent")
    chain.reverse()
    return chain


def _topic_view(nodes: list[dict], focus: str | None) -> tuple[list[dict], list[dict]]:
    by_id = {node["id"]: node for node in nodes}
    if focus:
        children = [node for node in nodes if node.get("parent") == focus]
        children.sort(key=lambda node: (-(node.get("size") or 0), node["id"]))
        shown = []
        focus_node = by_id.get(focus)
        if focus_node:
            shown.append(focus_node)
        for child in children:
            if child["id"] == focus:
                continue
            shown.append(child)
            if len(shown) >= MAX_TOPIC:
                break
        if not shown:
            shown = [{"id": focus, "title": focus, "summary": "", "level": None, "parent": None, "size": None}]
        return shown[:MAX_TOPIC], _breadcrumb(by_id, focus)
    levels = [node.get("level") for node in nodes if isinstance(node.get("level"), int)]
    if not levels:
        return nodes[:MAX_TOPIC], []
    top = max(levels)
    shown = [node for node in nodes if node.get("level") == top]
    shown.sort(key=lambda node: (-(node.get("size") or 0), node["id"]))
    breadcrumb = [{"id": None, "title": f"Cấp {top}", "level": top}]
    return shown[:MAX_TOPIC], breadcrumb


def compose_topic(store: GraphStore, question: str = "", state: dict | None = None) -> dict:
    focus = _community_focus(question, state)
    try:
        rows = _run(
            store,
            """
            MATCH (c:Community)
            OPTIONAL MATCH (c)-[:PARENT_COMMUNITY]->(p:Community)
            RETURN c.id AS id, c.title AS title, c.summary AS summary,
                   c.level AS level, c.size AS size, c.psiIds AS psiIds,
                   coalesce(p.id, c.parent_id) AS parent
            LIMIT $limit
            """,
            limit=MAX_TOPIC_FETCH,
        )
    except Exception:  # noqa: BLE001
        return _spec("topic", "Không đọc được chủ đề", [missing("communities")])
    nodes = [node for row in rows if (node := _community_node(row))]
    if not nodes and not focus:
        return _spec("topic", "Bản đồ chủ đề", [missing("communities")])
    shown, breadcrumb = _topic_view(nodes, focus)
    levels = sorted({node["level"] for node in nodes if isinstance(node.get("level"), int)})
    public = []
    for node in shown:
        item = {
            "id": node["id"],
            "title": node["title"],
            "summary": node["summary"],
            "level": node.get("level"),
            "parent": node.get("parent"),
            "size": node.get("size"),
        }
        if node.get("psiIds"):
            item["psiIds"] = list(node["psiIds"])
        if node.get("chunkIds"):
            item["chunkIds"] = list(node["chunkIds"])
        public.append(item)
    filters = []
    if focus:
        filters.append({"kind": "community", "id": focus, "label": focus})
    blocks = [
        {
            "component": "TopicDrilldown",
            "data": {"levels": levels, "communities": public, "breadcrumb": breadcrumb},
            "evidence": [{"kind": "community", "ref": focus or "Community"}],
        }
    ]
    seen_psi: set[str] = set()
    lookup = getattr(store, "get_investigation", None)
    for node in public:
        for psi_id in node.get("psiIds") or []:
            cleaned = str(psi_id).removeprefix("inv:")
            if not cleaned or cleaned in seen_psi or not callable(lookup):
                continue
            seen_psi.add(cleaned)
            if len(seen_psi) > 25:
                break
            try:
                card = lookup(cleaned)
            except Exception:  # noqa: BLE001
                card = None
            if card:
                blocks.append(_card_block(card))
    return _spec(
        "topic",
        "Bản đồ chủ đề",
        blocks,
        filters=filters,
        followups=["Độ phủ dữ liệu", "Liệt kê investigation", "Xuất bảng dữ liệu"],
    )


def _sensor_count(row: dict) -> int | None:
    value = row.get("n")
    if value is None:
        value = row.get("count")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _suppressant_note(store: GraphStore) -> str:
    prefix = "Dữ liệu chất dập cháy trong PSI còn mỏng"
    suffix = ". Ô ma trận chỉ lấy quan hệ MEASURED_WITH đã có; không suy thêm số lượt."
    try:
        rows = _run(store, "MATCH (n:Suppressant) RETURN count(n) AS n LIMIT 1")
    except Exception:  # noqa: BLE001
        return prefix + suffix
    if not rows:
        return prefix + suffix
    raw = rows[0].get("n")
    if raw is None:
        return prefix + suffix
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return prefix + suffix
    return f"{prefix} ({count} node){suffix}"


def _labeled_entities(rows: list[dict]) -> list[dict]:
    items = []
    seen: set[str] = set()
    for row in rows or []:
        raw_id = str(row.get("id") or "").strip()
        label = str(row.get("label") or "").strip()
        if not raw_id and not label:
            continue
        if raw_id and raw_id in seen:
            continue
        if raw_id:
            seen.add(raw_id)
        items.append({"id": raw_id or label, "label": _clean_label(label or raw_id)})
    return items


def _extinction_blocks(store: GraphStore) -> list[dict]:
    """Suppressant, extinction phenomenon, and chunks only when the graph returns them."""
    try:
        suppressants = _run(
            store,
            """
            MATCH (s:Suppressant)
            RETURN s.id AS id, coalesce(s.label, s.id) AS label
            ORDER BY label
            LIMIT 24
            """,
        )
        phenomena = _run(
            store,
            """
            MATCH (p:Phenomenon)
            WHERE toLower(coalesce(p.label, p.id, '')) CONTAINS 'extinction'
            RETURN p.id AS id, coalesce(p.label, p.id) AS label
            ORDER BY label
            LIMIT 12
            """,
        )
    except Exception:  # noqa: BLE001
        return []
    try:
        chunks = _run(
            store,
            """
            MATCH (c:Chunk)-[:MENTIONS]->(p:Phenomenon)
            WHERE toLower(coalesce(p.label, p.id, '')) CONTAINS 'extinction'
              AND c.text IS NOT NULL AND trim(c.text) <> ''
            RETURN c.id AS chunkId, c.text AS preview, c.page AS page,
                   c.pages_span AS pages_span, c.source AS source
            LIMIT 5
            """,
        )
    except Exception:  # noqa: BLE001
        chunks = []
    blocks: list[dict] = []
    suppressant_items = _labeled_entities(suppressants)
    if suppressant_items:
        blocks.append(
            {
                "component": "ChipList",
                "data": {
                    "title": "Chất dập cháy",
                    "items": [{"label": item["label"]} for item in suppressant_items],
                },
                "evidence": [{"id": item["id"], "kind": "graph", "ref": "Suppressant"} for item in suppressant_items],
            }
        )
    phenomenon_items = _labeled_entities(phenomena)
    if phenomenon_items:
        blocks.append(
            {
                "component": "ChipList",
                "data": {
                    "title": "Hiện tượng extinction",
                    "items": [{"label": item["label"]} for item in phenomenon_items],
                },
                "evidence": [{"id": item["id"], "kind": "graph", "ref": "Phenomenon"} for item in phenomenon_items],
            }
        )
    chunk_hits = [
        row
        for row in chunks or []
        if row.get("chunkId") and (row.get("preview") or row.get("text"))
    ]
    if chunk_hits:
        blocks.append(_evidence_block(chunk_hits, MAX_EVIDENCE))
    return blocks


def compose_sensor(store: GraphStore, filters: list[dict] | None = None) -> dict:
    applied = list(filters or [])
    try:
        rows = _run(
            store,
            """
            MATCH (s:Sensor)-[r:MEASURED_WITH]-(i:Investigation)
            RETURN s.id AS sensorId, coalesce(s.label, s.id) AS label, s.aliases AS aliases,
                   i.psiId AS psiId, count(r) AS n
            ORDER BY n DESC
            LIMIT 400
            """,
        )
    except Exception:  # noqa: BLE001
        return _spec(
            "sensor",
            "Không đọc được cảm biến",
            [missing("sensors")],
            text=_SENSOR_NOTE,
        )
    prepared = []
    index = _AliasIndex()
    for row in rows:
        sensor_id = str(row.get("sensorId") or row.get("id") or "").strip()
        psi_id = row.get("psiId") or row.get("investigation")
        count = _sensor_count(row)
        if not sensor_id or not psi_id or count is None:
            continue
        label = str(row.get("label") or sensor_id)
        keys = _alias_slugs(sensor_id, label, row.get("aliases")) or [sensor_id.casefold()]
        index.union(keys)
        prepared.append((sensor_id, label, keys[0], str(psi_id).removeprefix("inv:"), count))
    groups: dict[str, dict] = {}
    pair: dict[tuple[str, str], int] = {}
    weight: dict[str, int] = {}
    for sensor_id, label, primary, investigation, count in prepared:
        sensor_key = index.find(primary)
        _touch_group(groups, sensor_key, sensor_id, label, count)
        pair[(sensor_key, investigation)] = pair.get((sensor_key, investigation), 0) + count
        weight[investigation] = weight.get(investigation, 0) + count
    allowed = _relation_ids(store, [item for item in applied if item.get("kind") != "sensor"])
    family_fuel = [item for item in applied if item.get("kind") in {"family", "fuel", "investigation"}]
    if family_fuel or allowed is not None:
        try:
            cards = list(store.list_investigations() or [])
        except Exception:  # noqa: BLE001
            cards = []
        kept_ids = {str(card.get("id") or "") for card in cards if _card_matches(card, family_fuel or applied)}
        if allowed is not None:
            kept_ids &= allowed
        if family_fuel or allowed is not None:
            weight = {psi: count for psi, count in weight.items() if psi in kept_ids}
            pair = {key: count for key, count in pair.items() if key[1] in kept_ids}
    sensor_needle = _sensor_filter(applied)
    ranked = sorted(weight, key=lambda psi: (-weight[psi], psi))
    capped = len(ranked) > MAX_SENSOR_COLUMNS
    chosen = ranked[:MAX_SENSOR_COLUMNS]
    chosen_set = set(chosen)
    sensor_weight: dict[str, int] = {}
    for (sensor_key, investigation), count in pair.items():
        if investigation in chosen_set:
            sensor_weight[sensor_key] = sensor_weight.get(sensor_key, 0) + count
    items = []
    for sensor_key, sensor_total in sensor_weight.items():
        item = _materialize_group(sensor_key, groups[sensor_key])
        item["weight"] = sensor_total
        items.append(item)
    items = _disambiguate(items)
    items.sort(key=lambda item: (-item["weight"], item["id"]))
    items = items[:MAX_SENSOR_ROWS]
    if sensor_needle:
        needle = sensor_needle.casefold()
        items = [
            item
            for item in items
            if needle in f"{item['label']} {item['id']} {item['key']}".casefold()
            or any(needle in str(raw).casefold() for raw in item["rawIds"])
        ]
    chosen_keys = {item["key"] for item in items}
    id_of = {item["key"]: item["id"] for item in items}
    cells = [
        {"sensor": id_of[sensor_key], "investigation": investigation, "value": count}
        for (sensor_key, investigation), count in pair.items()
        if sensor_key in chosen_keys and investigation in chosen_set
    ]
    cells.sort(key=lambda cell: (cell["sensor"], cell["investigation"]))
    note = _suppressant_note(store)
    if capped:
        note += " Cột chỉ gồm investigation có cạnh cảm biến, tối đa 8 cột."
    if not cells:
        return _spec(
            "sensor",
            "Ma trận cảm biến",
            [missing("sensors")],
            text=note,
            followups=["Độ phủ dữ liệu", "Hồ sơ PSI-102", "Đồ thị quanh PSI-102"],
        )
    evidence = []
    for item in items:
        evidence.append({"id": item["id"], "rawIds": item["rawIds"], "kind": "graph", "ref": "MEASURED_WITH"})
        if item["collidesWith"]:
            evidence.append(
                {
                    "ids": list(dict.fromkeys([*item["rawIds"], *item["collidesWith"]])),
                    "kind": "graph",
                    "ref": "MEASURED_WITH",
                }
            )
    blocks = [
        {
            "component": "SensorMatrix",
            "data": {
                "sensors": [
                    {"id": item["id"], "label": item["label"], "rawIds": item["rawIds"]} for item in items
                ],
                "investigations": chosen,
                "cells": cells,
                "note": note,
            },
            "evidence": evidence,
        }
    ]
    blocks.extend(_extinction_blocks(store))
    return _spec(
        "sensor",
        "Ma trận cảm biến",
        blocks,
        filters=applied,
        followups=["Hồ sơ PSI-102", "Đồ thị quanh PSI-102", "Độ phủ dữ liệu"],
    )


def _focus(question: str, named: list[str], state: dict | None) -> str | None:
    focus = (state or {}).get("focusId")
    if isinstance(focus, str) and focus:
        return focus
    if named:
        return named[0]
    return None


def compose(store: GraphStore, question: str, state: dict | None = None) -> dict:
    """Build one UISpec. A saved demo snapshot wins when the file has this question."""
    cached = load_snapshot(question)
    if cached is not None:
        return cached
    try:
        return _compose_live(store, question, state)
    except Exception:  # noqa: BLE001
        return _spec("qa", "Không đọc được đồ thị", [missing("graph")], text="Neo4j không trả lời kịp.")


def _with_extract(spec: dict, store: GraphStore, filters: list[dict]) -> dict:
    extracted = compose_extract(store, filters)
    extra = [
        block
        for block in extracted.get("blocks") or []
        if block.get("component") == "DataTable"
    ]
    spec["blocks"] = list(spec.get("blocks") or []) + extra
    if not any(block.get("component") == "ExportCSV" for block in spec["blocks"]):
        spec["blocks"].append(
            {
                "component": "ExportCSV",
                "data": {"href": "/api/viz/extract.csv", "filename": "extract.csv"},
            }
        )
    return spec


def _compose_live(store: GraphStore, question: str, state: dict | None) -> dict:
    filters = _filters(state)
    named = mentioned_from_question(store, question) if question.strip() else []
    scenario = _scenario_of(question)
    intent = classify(question, named, scenario)
    focus = _focus(question, named, state)
    active = _active_scenario(question, state)
    depth = _graph_depth(state)
    if intent == "topic":
        return _keep_filters(compose_topic(store, question, state), filters)
    if intent == "coverage":
        spec = compose_coverage(store, filters)
        if _EXTRACT.search(question):
            spec = _with_extract(spec, store, filters)
        return _keep_filters(spec, filters)
    if intent == "extract":
        return _keep_filters(compose_extract(store, filters), filters)
    if intent == "sensor":
        return _keep_filters(compose_sensor(store, filters), filters)
    if intent == "explore":
        return compose_explore(store, filters, scenario=active)
    if intent == "profile" and focus:
        return compose_profile(store, focus, filters)
    if intent == "graph" and focus:
        return compose_graph(store, focus, depth=depth, filters=filters)
    if intent == "compare":
        return compose_compare(store, named, filters)
    if intent == "scenario" and scenario:
        return compose_scenario(store, scenario, filters)
    if intent == "evidence":
        return _keep_filters(compose_evidence(store, question, named), filters)
    if intent == "profile":
        return compose_explore(store, filters, scenario=active)
    if intent == "graph":
        return compose_qa(store, question)
    return compose_qa(store, question)


def load_snapshot(question: str) -> dict | None:
    if not question.strip() or not SNAPSHOT_PATH.is_file():
        return None
    try:
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    found = payload.get(_snapshot_key(question))
    return found if isinstance(found, dict) else None


def _snapshot_key(question: str) -> str:
    return " ".join(question.lower().split())
