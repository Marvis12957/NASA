"""Context-aware UI planner for FlameViz (see docs/AGENT_GENUI.md).

One LLM call reads the question plus the session context and picks up to three
views from the closed catalog. The LLM returns view names, PSI ids, a scenario id
and a search query, nothing else. Every number in the UISpec still comes from the
app.viz composers, so the planner cannot invent data. Any failure (LLM off, slow,
invalid plan) falls back to the rule router app.viz.compose.

This module only adds. It does not change app.viz, app.main or the frontend.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from typing import Any

from app import viz
from app.graph import GraphStore
from app.knowledge import SCENARIOS
from app.neo4j_config import ROOT

FIELD_EVIDENCE_PATH = ROOT / "backend" / "var" / "viz_field_evidence.json"
MAX_VIEWS = 3
MAX_FOLLOWUPS = 3
MAX_EVIDENCE = 5
MAX_QUERY_CHARS = 200
DEFAULT_TIMEOUT_SECONDS = 12
MAX_LLM_ATTEMPTS = 3

CATALOG = frozenset(
    {
        "StatCard", "InvestigationCard", "ProfileHeader", "GaugeO2", "ChipList", "FlowSteps",
        "NodeGraph", "HeatMap", "BarChart", "CompareTable", "SensorMatrix", "TopicDrilldown",
        "EvidencePanel", "DataTable", "ExportCSV", "ScenarioBriefing", "SourceBadge", "MissingData",
    }
)

# view -> what the LLM is told. Order is the order shown in the prompt.
VIEWS: dict[str, str] = {
    "explore": "grid of investigations, ranked by relevance. For 'which / list / most relevant' questions.",
    "profile": "one investigation in detail (needs exactly one id).",
    "compare": "side-by-side table of 2-3 investigations (needs 2-3 ids).",
    "graph": "relation graph around one investigation: hardware, fuels, sensors, phenomena (needs one id).",
    "scenario": "briefing for one crew environment (needs scenario).",
    "sensor": "sensor x investigation matrix plus suppression notes. Optional ids narrow the columns.",
    "topic": "community/theme drill-down for overview questions.",
    "coverage": "what data exists and where it is thin (for modelling readiness).",
    "extract": "flat dataset table with CSV export.",
    "evidence": "source chunks with page numbers (needs query). For 'which page / what is the source'.",
    "qa": "short text answer with source chunks (needs query). Last resort.",
}

SYSTEM = (
    "You plan a dashboard over NASA PSI microgravity-combustion data. "
    "Reply with a single JSON object and nothing else.\n"
    "Pick 1 to 3 views, most important first, from this closed list:\n"
    + "\n".join(f"- {name}: {text}" for name, text in VIEWS.items())
    + "\nRules:\n"
    "- Use only PSI ids that appear in the investigation list. Never invent ids, view names or numbers.\n"
    "- Words like it, that one, nó, cái đó, cái kia refer to the focus given in the context.\n"
    "- scenario is one of: iss, moon, mars, rover, suit, earth. Lunar Gateway counts as moon.\n"
    "- Add a second or third view only when the question asks for more than one thing "
    "(for example compare plus sensor, or profile plus graph).\n"
    "- Sensors, detectors, smoke detection, suppression or extinguishing: use sensor, "
    "with ids when investigations are named.\n"
    "- 'Tell me about', 'hồ sơ', 'chi tiết' for one investigation: use profile.\n"
    "- A fact, a number or a page inside the documents: use evidence with a short English query.\n"
    "- Use qa only when no other view fits.\n"
    "- If the question has nothing to do with fire, combustion, spacecraft safety or this dataset, "
    'reply {"offTopic": true, "views": []}.\n'
    'Format: {"offTopic": false, "views": [{"view": "compare", "ids": ["PSI-69", "PSI-25"]}, '
    '{"view": "evidence", "query": "sensor used in FLEX"}]}'
)

OFF_TOPIC_TEXT = (
    "Câu hỏi này nằm ngoài dữ liệu cháy vi trọng lực của NASA PSI nên không có bằng chứng để trả lời."
)
OFF_TOPIC_FOLLOWUPS = ["Liệt kê investigation", "Briefing cho ISS", "Độ phủ dữ liệu"]


def enabled() -> bool:
    flag = (os.environ.get("VIZ_PLANNER") or "1").strip().lower()
    return flag not in {"0", "false", "no", "off", "rules"}


def timeout_seconds() -> int:
    raw = (os.environ.get("VIZ_PLANNER_TIMEOUT") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_TIMEOUT_SECONDS


# ---------- context ----------


def _clean_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("inv:"):
        text = text[4:]
    return text.upper()


def _known_ids(store: GraphStore) -> dict[str, str]:
    """PSI id -> display name. Empty when the graph cannot be read."""
    try:
        names = store.investigation_names() or {}
    except Exception:  # noqa: BLE001
        return {}
    known: dict[str, str] = {}
    for key, value in dict(names).items():
        psi = _clean_id(key)
        if psi.startswith("PSI-"):
            known[psi] = str(value or psi)
    return known


def _state_focus(state: dict | None) -> str | None:
    focus = (state or {}).get("focusId")
    if isinstance(focus, str) and focus.strip():
        return _clean_id(focus)
    for item in (state or {}).get("filters") or []:
        if isinstance(item, dict) and item.get("kind") == "investigation" and item.get("id"):
            return _clean_id(item["id"])
    return None


def _turn_focus(turns: list[dict]) -> str | None:
    for turn in reversed(turns or []):
        focus = turn.get("focusId")
        if isinstance(focus, str) and focus:
            return _clean_id(focus)
        ranked = turn.get("rankedIds") or []
        if ranked:
            return _clean_id(ranked[0])
    return None


def build_context(store: GraphStore, question: str, state: dict | None, turns: list[dict] | None) -> dict:
    known = _known_ids(store)
    try:
        named = [_clean_id(item) for item in viz.mentioned_from_question(store, question)]
    except Exception:  # noqa: BLE001
        named = []
    named = [psi for psi in named if psi in known]
    focus = _state_focus(state) or (named[0] if named else None) or _turn_focus(turns or [])
    if focus not in known:
        focus = None
    filters = [item for item in (state or {}).get("filters") or [] if isinstance(item, dict) and item.get("kind")]
    return {"known": known, "named": named, "focus": focus, "filters": filters, "turns": list(turns or [])[-4:]}


def user_prompt(question: str, context: dict) -> str:
    lines = [f"Question: {question}", "Context:"]
    focus = context.get("focus")
    lines.append(f"- focus: {focus} ({context['known'].get(focus)})" if focus else "- focus: none")
    named = context.get("named") or []
    lines.append("- ids named in the question: " + (", ".join(named) or "none"))
    shown = [f"{item.get('kind')}={item.get('label') or item.get('id')}" for item in context.get("filters") or []]
    lines.append("- active filters: " + (", ".join(shown) or "none"))
    turns = context.get("turns") or []
    if turns:
        lines.append("- earlier turns (oldest first):")
        for turn in turns:
            lines.append(
                f"  - asked “{turn.get('question') or ''}”; view {turn.get('mode') or 'none'}; "
                f"focus {turn.get('focusId') or 'none'}; scenario {turn.get('scenario') or 'none'}"
            )
    else:
        lines.append("- earlier turns: none")
    lines.append("Investigations (id name):")
    for psi, name in sorted(context["known"].items(), key=lambda item: (len(item[0]), item[0])):
        lines.append(f"{psi} {name}")
    return "\n".join(lines)


# ---------- LLM ----------


def _ask_llm(system: str, user: str, timeout: int) -> dict | None:
    """Planning call with a hard wall-clock limit. None means: use the rules.

    The proxy sometimes answers with empty text in well under a second. A failed call is
    retried (three attempts in total) as long as the whole call stays inside the time limit.
    """
    from app.llm import complete_text, parse_json_object

    def work(budget: int) -> dict:
        return parse_json_object(complete_text(system, user, json_mode=True, timeout=budget))

    deadline = time.time() + timeout
    for _attempt in range(MAX_LLM_ATTEMPTS):
        budget = int(deadline - time.time())
        if budget < 1:
            return None
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(work, budget)
            try:
                data = future.result(timeout=budget + 1)
            except Exception:  # noqa: BLE001  (timeout, HTTP error, empty or bad JSON)
                continue
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return data if isinstance(data, dict) else None
    return None


# ---------- plan validation ----------


def validate_plan(raw: dict | None, context: dict, question: str) -> list[dict]:
    """Keep only views, ids and scenarios that exist. Drop anything the composer cannot run."""
    if not isinstance(raw, dict):
        return []
    known = context.get("known") or {}
    focus = context.get("focus")
    steps: list[dict] = []
    seen: set[tuple] = set()
    for item in raw.get("views") or []:
        if not isinstance(item, dict):
            continue
        view = str(item.get("view") or "").strip().lower()
        if view not in VIEWS:
            continue
        ids: list[str] = []
        for value in item.get("ids") if isinstance(item.get("ids"), list) else []:
            psi = _clean_id(value)
            if psi in known and psi not in ids:
                ids.append(psi)
        scenario = str(item.get("scenario") or "").strip().lower() or None
        query = item.get("query")
        query = " ".join(query.split())[:MAX_QUERY_CHARS] if isinstance(query, str) else ""
        step: dict[str, Any] = {"view": view}
        if view in {"profile", "graph"}:
            chosen = ids[0] if ids else focus
            if not chosen:
                continue
            step["ids"] = [chosen]
        elif view == "compare":
            if len(ids) == 1 and focus and focus != ids[0]:
                ids = [focus, ids[0]]
            if len(ids) < 2:
                continue
            step["ids"] = ids[:3]
        elif view == "scenario":
            if scenario not in SCENARIOS:
                continue
            step["scenario"] = scenario
        elif view in {"evidence", "qa"}:
            step["query"] = query or question
            step["ids"] = ids[:3]
        elif view == "sensor":
            step["ids"] = ids[:8]
        elif view == "explore":
            if scenario in SCENARIOS:
                step["scenario"] = scenario
        key = (view, tuple(step.get("ids") or []), step.get("scenario"), step.get("query"))
        if key in seen:
            continue
        seen.add(key)
        steps.append(step)
        if len(steps) >= MAX_VIEWS:
            break
    return steps


# ---------- running views ----------


def _run_step(store: GraphStore, step: dict, question: str, state: dict | None, context: dict) -> dict | None:
    view = step["view"]
    ids = list(step.get("ids") or [])
    filters = context.get("filters") or []
    try:
        if view == "explore":
            try:
                return viz.compose_explore(store, filters, scenario=step.get("scenario"))
            except TypeError:
                return viz.compose_explore(store, filters)
        if view == "profile":
            return viz.compose_profile(store, ids[0])
        if view == "compare":
            return viz.compose_compare(store, ids)
        if view == "graph":
            return viz.compose_graph(store, ids[0])
        if view == "scenario":
            return viz.compose_scenario(store, step["scenario"])
        if view == "sensor":
            return _narrow_sensor(viz.compose_sensor(store), ids)
        if view == "topic":
            return viz.compose_topic(store, question, state)
        if view == "coverage":
            return viz.compose_coverage(store)
        if view == "extract":
            return viz.compose_extract(store)
        if view == "evidence":
            return viz.compose_evidence(store, step.get("query") or question, ids)
        if view == "qa":
            return viz.compose_qa(store, step.get("query") or question)
    except Exception:  # noqa: BLE001  (one broken view must not break the page)
        return {
            "version": "1.0",
            "intent": view,
            "title": "",
            "filters": [],
            "blocks": [viz.missing(view)],
            "followups": [],
        }
    return None


def _narrow_sensor(spec: dict, ids: list[str]) -> dict:
    """Keep only the asked investigations in the matrix. Counts are copied, never recomputed."""
    if not ids or not isinstance(spec, dict):
        return spec
    blocks = []
    for block in spec.get("blocks") or []:
        if block.get("component") != "SensorMatrix":
            blocks.append(block)
            continue
        data = dict(block.get("data") or {})
        columns = [psi for psi in data.get("investigations") or [] if psi in ids]
        if not columns:
            blocks.append(block)
            continue
        cells = [cell for cell in data.get("cells") or [] if cell.get("investigation") in columns]
        used = {cell.get("sensor") for cell in cells}
        data["investigations"] = columns
        data["cells"] = cells
        data["sensors"] = [item for item in data.get("sensors") or [] if item.get("id") in used]
        note = str(data.get("note") or "").strip()
        data["note"] = (note + " " if note else "") + "Đang lọc theo " + ", ".join(columns) + "."
        blocks.append({**block, "data": data})
    return {**spec, "blocks": blocks}


# ---------- field evidence (built by backend/scripts/build_field_evidence.py) ----------


def load_field_evidence() -> dict:
    try:
        payload = json.loads(FIELD_EVIDENCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("investigations") if isinstance(payload, dict) else None
    return rows if isinstance(rows, dict) else {}


def _oxygen_refs(entry: dict) -> list[dict]:
    refs = []
    for table in entry.get("tables") or []:
        for column in table.get("oxygenColumns") or []:
            refs.append(
                {
                    "kind": "field",
                    "ref": "Investigation.oxygenMin/oxygenMax",
                    "source": table.get("file"),
                    "column": column.get("column"),
                    "rows": column.get("values"),
                    "min": column.get("min"),
                    "max": column.get("max"),
                }
            )
    return refs


def attach_field_evidence(blocks: list[dict], index: dict | None = None) -> list[dict]:
    """Point a GaugeO2 at the experiment-table column its bounds came from.

    Added only when no evidence item names a source file yet, and only when the table
    bounds equal the gauge bounds. Existing evidence is kept. Otherwise the gauge is left as is.
    """
    index = load_field_evidence() if index is None else index
    if not index:
        return blocks
    current: str | None = None
    out = []
    for block in blocks:
        component = block.get("component")
        data = block.get("data") or {}
        if component == "ProfileHeader":
            current = _clean_id(data.get("id"))
        existing = [item for item in block.get("evidence") or [] if isinstance(item, dict)]
        sourced = any(item.get("source") for item in existing)
        if component == "GaugeO2" and current and not sourced and not data.get("missing"):
            entry = index.get(current) or {}
            same = entry.get("oxygenMin") == data.get("min") and entry.get("oxygenMax") == data.get("max")
            refs = _oxygen_refs(entry) if same else []
            if refs:
                block = {**block, "evidence": [*existing, *refs]}
        out.append(block)
    return out


# ---------- merge ----------


def merge_specs(specs: list[dict]) -> dict:
    first = specs[0]
    titles = [str(spec.get("title") or "").strip() for spec in specs if str(spec.get("title") or "").strip()]
    filters: list[dict] = []
    seen_filter: set[tuple] = set()
    blocks: list[dict] = []
    panels = 0
    for spec in specs:
        for item in spec.get("filters") or []:
            if not isinstance(item, dict):
                continue
            key = (item.get("kind"), item.get("id"))
            if key not in seen_filter:
                seen_filter.add(key)
                filters.append(item)
        for block in spec.get("blocks") or []:
            if not isinstance(block, dict) or block.get("component") not in CATALOG:
                continue
            if block.get("component") == "EvidencePanel":
                panels += 1
            blocks.append(block)
    if panels > 1:
        blocks = _single_evidence_panel(blocks)
    followups: list[str] = []
    longest = max((len(spec.get("followups") or []) for spec in specs), default=0)
    for position in range(longest):
        for spec in specs:
            chips = spec.get("followups") or []
            if position < len(chips) and chips[position] not in followups:
                followups.append(chips[position])
    merged: dict[str, Any] = {
        "version": "1.0",
        "intent": first.get("intent") or "qa",
        "title": " · ".join(titles[:2]) or "FlameViz",
        "filters": filters,
        "blocks": attach_field_evidence(blocks),
        "followups": followups[:MAX_FOLLOWUPS],
    }
    text = next((spec.get("text") for spec in specs if spec.get("text")), None)
    if text:
        merged["text"] = text
    return merged


def _single_evidence_panel(blocks: list[dict]) -> list[dict]:
    """Several views each bring a panel. Keep one, at the end, capped at five chunks."""
    chunks: list[dict] = []
    seen: set[str] = set()
    kept = []
    for block in blocks:
        if block.get("component") != "EvidencePanel":
            kept.append(block)
            continue
        for chunk in (block.get("data") or {}).get("chunks") or []:
            key = str(chunk.get("id"))
            if key in seen or len(chunks) >= MAX_EVIDENCE:
                continue
            seen.add(key)
            chunks.append(chunk)
    kept.append({"component": "EvidencePanel", "data": {"chunks": chunks}})
    return kept


# ---------- entry point ----------


def _tag(spec: dict, mode: str, started: float, **extra: Any) -> dict:
    tagged = dict(spec)
    tagged["planner"] = {"mode": mode, "ms": int((time.time() - started) * 1000), **extra}
    return tagged


def _rules(store: GraphStore, question: str, state: dict | None, started: float, reason: str) -> dict:
    return _tag(viz.compose(store, question, state), "rules", started, reason=reason)


def _off_topic(started: float) -> dict:
    spec = {
        "version": "1.0",
        "intent": "qa",
        "title": "Ngoài phạm vi dữ liệu",
        "filters": [],
        "blocks": [viz.missing("scope")],
        "followups": list(OFF_TOPIC_FOLLOWUPS),
        "text": OFF_TOPIC_TEXT,
    }
    return _tag(spec, "llm", started, views=[], offTopic=True)


def compose_planned(
    store: GraphStore,
    question: str,
    state: dict | None = None,
    turns: list[dict] | None = None,
) -> dict:
    """UISpec chosen from the question and the session context. Never raises."""
    started = time.time()
    question = (question or "").strip()
    try:
        if not question:
            return _rules(store, question, state, started, "empty question")
        snapshot = viz.load_snapshot(question)
        if snapshot is not None:
            return _tag(snapshot, "snapshot", started)
        if not enabled():
            return _rules(store, question, state, started, "planner off")
        if turns is None:
            turns = load_turns(store, state)
        context = build_context(store, question, state, turns)
        if not context["known"]:
            return _rules(store, question, state, started, "no investigation list")
        raw = _ask_llm(SYSTEM, user_prompt(question, context), timeout_seconds())
        if raw is None:
            return _rules(store, question, state, started, "llm failed or timed out")
        if raw.get("offTopic") is True and not raw.get("views"):
            return _off_topic(started)
        steps = validate_plan(raw, context, question)
        if not steps:
            return _rules(store, question, state, started, "plan had no valid view")
        specs = [spec for step in steps if (spec := _run_step(store, step, question, state, context))]
        if not specs:
            return _rules(store, question, state, started, "no view could be built")
        return _tag(merge_specs(specs), "llm", started, views=steps)
    except Exception:  # noqa: BLE001
        try:
            return _rules(store, question, state, started, "planner error")
        except Exception:  # noqa: BLE001
            return _tag(
                {
                    "version": "1.0",
                    "intent": "qa",
                    "title": "Không đọc được đồ thị",
                    "filters": [],
                    "blocks": [viz.missing("graph")],
                    "followups": [],
                },
                "rules",
                started,
                reason="graph unavailable",
            )


# ---------- session memory (reuses app.memory, stores ids only) ----------


def _session_id(state: dict | None) -> str:
    value = (state or {}).get("sessionId")
    return value.strip() if isinstance(value, str) else ""


def load_turns(store: GraphStore, state: dict | None) -> list[dict]:
    session_id = _session_id(state)
    if not session_id:
        return []
    from app.memory import load_turns as load

    try:
        return list(load(store, session_id)[0])
    except Exception:  # noqa: BLE001
        return []


def turn_of(question: str, spec: dict) -> dict:
    """What a later turn may reuse: ids and the view, never answer prose or numbers."""
    focus = None
    scenario = None
    for item in spec.get("filters") or []:
        if item.get("kind") == "investigation" and not focus:
            focus = _clean_id(item.get("id"))
        if item.get("kind") == "scenario" and not scenario:
            scenario = str(item.get("id") or "") or None
    ranked = []
    for block in spec.get("blocks") or []:
        data = block.get("data") or {}
        if block.get("component") == "ProfileHeader" and not focus:
            focus = _clean_id(data.get("id"))
        if block.get("component") == "InvestigationCard" and data.get("id"):
            ranked.append(_clean_id(data["id"]))
        if block.get("component") == "CompareTable":
            ranked.extend(_clean_id(item) for item in data.get("columns") or [])
    return {
        "question": question,
        "scenario": scenario,
        "focusId": focus,
        "rankedIds": list(dict.fromkeys(ranked))[:5],
        "mode": spec.get("intent") or "",
    }


def remember(store: GraphStore, state: dict | None, question: str, spec: dict) -> str | None:
    """Save one turn for follow-ups. Call this from the endpoint, after compose_planned."""
    session_id = _session_id(state)
    if not session_id or not (question or "").strip():
        return None
    from app.memory import save_turn

    return save_turn(store, session_id, turn_of(question, spec))
