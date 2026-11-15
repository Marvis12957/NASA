"""Specialists. Each agent may call only the tools listed on it."""

from __future__ import annotations

import os
from typing import Any, Callable

from app.graph import GraphStore
from app.knowledge import SCENARIOS
from app.tools import execute

AgentHandle = Callable[[dict[str, Any], GraphStore, list[dict]], list[dict]]


def _step(agent: dict, tool: str, args: dict, store: GraphStore) -> dict:
    if tool not in agent["tools"]:
        result = {
            "ok": False,
            "summary": f"{agent['id']} is not allowed to call {tool}",
            "data": None,
        }
    else:
        result = execute(tool, args, store)
    return {
        "agentId": agent["id"],
        "agentRole": agent["role"],
        "tool": tool,
        "args": args,
        "result": result,
    }


def ids_from_ranker(prior: list[dict]) -> list[str]:
    for step in prior:
        if step.get("tool") != "rank_investigations" or not step.get("result", {}).get("ok"):
            continue
        ranked = (step["result"].get("data") or {}).get("ranked") or []
        return [row["id"] for row in ranked]
    return []


def handle_ranker(task: dict, store: GraphStore, prior: list[dict]) -> list[dict]:
    hints = task.get("hints") or {}
    return [
        _step(
            RANKER,
            "rank_investigations",
            {
                "scenario": hints.get("scenario"),
                "role": hints.get("role"),
                "limit": hints.get("limit", 5),
            },
            store,
        )
    ]


def handle_reader(task: dict, store: GraphStore, prior: list[dict]) -> list[dict]:
    hints = task.get("hints") or {}
    if isinstance(hints.get("id"), str) and hints["id"]:
        return [_step(READER, "get_investigation", {"id": hints["id"]}, store)]
    return []


def handle_comparer(task: dict, store: GraphStore, prior: list[dict]) -> list[dict]:
    hints = task.get("hints") or {}
    if isinstance(hints.get("ids"), list):
        ids = [str(item) for item in hints["ids"] if item]
    elif hints.get("fromRanker"):
        ids = ids_from_ranker(prior)
    else:
        ids = []
    if len(ids) < 2:
        for psi_id in ids_from_ranker(prior):
            if psi_id not in ids:
                ids.append(psi_id)
            if len(ids) >= 2:
                break
    return [_step(COMPARER, "compare_investigations", {"ids": ids[:4]}, store)]


def handle_navigator(task: dict, store: GraphStore, prior: list[dict]) -> list[dict]:
    hints = task.get("hints") or {}
    if isinstance(hints.get("id"), str):
        return [_step(NAVIGATOR, "graph_neighbors", {"id": hints["id"]}, store)]
    return [_step(NAVIGATOR, "scenario_context", {"scenario": hints.get("scenario")}, store)]


def handle_graphrag(task: dict, store: GraphStore, prior: list[dict]) -> list[dict]:
    hints = task.get("hints") or {}
    query = hints.get("query") or task.get("question")
    limit = hints.get("limit", 5)
    args = {"query": query, "limit": limit}
    if hints.get("mode") == "global":
        return [_step(GRAPHRAG, "global_search", args, store)]
    return [_step(GRAPHRAG, "local_search", args, store)]


def _tool_choice_enabled() -> bool:
    flag = (os.environ.get("AGENT_TOOL_CHOICE") or "1").strip().lower()
    return flag not in {"0", "false", "no", "rules"}


def _allowed_ids(task: dict, prior: list[dict]) -> set[str]:
    hints = task.get("hints") or {}
    found: set[str] = set()
    if isinstance(hints.get("id"), str) and hints["id"]:
        found.add(hints["id"])
    if isinstance(hints.get("ids"), list):
        found.update(str(item) for item in hints["ids"] if item)
    found.update(ids_from_ranker(prior))
    for step in prior:
        data = (step.get("result") or {}).get("data") or {}
        if data.get("id"):
            found.add(str(data["id"]))
        for row in (data.get("ranked") or []) + (data.get("rows") or []):
            if row.get("id"):
                found.add(str(row["id"]))
    return found


def _limit(value: Any, default: int = 5) -> int | None:
    try:
        return max(1, min(8, int(value)))
    except (TypeError, ValueError):
        if value is None:
            return default
        return None


def _validate_choice(agent: dict, tool: str, args: dict, task: dict, prior: list[dict]) -> dict | None:
    """Keep the router's scenario and PSI ids. Reject tools outside this agent."""
    if tool not in agent["tools"]:
        return None
    hints = task.get("hints") or {}
    args = args if isinstance(args, dict) else {}
    if tool == "rank_investigations":
        scenario = args.get("scenario") or hints.get("scenario")
        if hints.get("scenario") and scenario != hints.get("scenario"):
            return None
        if scenario not in SCENARIOS:
            return None
        role = args.get("role") if args.get("role") in {"prevention", "detection", "suppression"} else hints.get("role")
        limit = _limit(args.get("limit", hints.get("limit", 5)))
        if limit is None:
            return None
        return {"scenario": scenario, "role": role, "limit": limit}
    if tool == "get_investigation":
        psi_id = str(args.get("id") or hints.get("id") or "")
        allowed = _allowed_ids(task, prior)
        if not psi_id or (allowed and psi_id not in allowed):
            return None
        return {"id": psi_id}
    if tool == "compare_investigations":
        raw = args.get("ids") if isinstance(args.get("ids"), list) else list(hints.get("ids") or [])
        ids = [str(item) for item in raw if item]
        allowed = _allowed_ids(task, prior)
        if len(ids) < 2 or (allowed and any(psi_id not in allowed for psi_id in ids)):
            return None
        return {"ids": ids[:4]}
    if tool == "graph_neighbors":
        psi_id = str(args.get("id") or hints.get("id") or "")
        allowed = _allowed_ids(task, prior)
        if not psi_id or (allowed and psi_id not in allowed):
            return None
        return {"id": psi_id}
    if tool == "scenario_context":
        scenario = args.get("scenario") or hints.get("scenario")
        if hints.get("scenario") and scenario != hints.get("scenario"):
            return None
        if scenario not in SCENARIOS:
            return None
        return {"scenario": scenario}
    if tool in {"local_search", "global_search"}:
        expected = "global_search" if hints.get("mode") == "global" else "local_search"
        if tool != expected:
            return None
        query = args.get("query") or hints.get("query") or task.get("question")
        if not isinstance(query, str) or not query.strip():
            return None
        limit = _limit(args.get("limit", hints.get("limit", 5)))
        if limit is None:
            return None
        return {"query": query.strip(), "limit": limit}
    return None


def choose_tool(agent: dict, task: dict, prior: list[dict]) -> tuple[str, dict] | None:
    """Ask for one allowed tool. None means the caller must use the rule handler."""
    if not _tool_choice_enabled():
        return None
    from app.llm import complete_text, parse_json_object

    hints = task.get("hints") or {}
    prior_briefs = task.get("prior_briefs") or []
    user = "\n".join(
        [
            f"Agent: {agent['id']}",
            f"Allowed tools: {', '.join(agent['tools'])}",
            f"Question: {task.get('question') or ''}",
            f"Hints: {hints}",
            "Prior briefs:",
            *([f"- {brief}" for brief in prior_briefs] or ["- none"]),
            'Reply JSON only: {"tool": "<allowed tool>", "args": { ... }}',
        ]
    )
    try:
        text = complete_text(
            "Choose exactly one allowed tool. Do not invent PSI ids or scenarios.",
            user,
            json_mode=True,
            timeout=30,
        )
        data = parse_json_object(text)
    except Exception:  # noqa: BLE001
        return None
    tool = data.get("tool") if isinstance(data, dict) else None
    args = data.get("args") if isinstance(data, dict) and isinstance(data.get("args"), dict) else {}
    if not isinstance(tool, str):
        return None
    cleaned = _validate_choice(agent, tool, args, task, prior)
    if cleaned is None:
        return None
    return tool, cleaned


def _result_empty(steps: list[dict]) -> bool:
    if not steps:
        return True
    for step in steps:
        result = step.get("result") or {}
        if not result.get("ok"):
            return True
        data = result.get("data") or {}
        tool = step.get("tool")
        if tool == "rank_investigations" and not (data.get("ranked") or []):
            return True
        if tool == "compare_investigations" and len(data.get("rows") or []) < 2:
            return True
        if tool in {"local_search", "search_investigations"} and not (data.get("hits") or []):
            return True
        if tool == "global_search" and not (data.get("communities") or []):
            return True
        if tool in {"get_investigation", "graph_neighbors"} and not data.get("id"):
            return True
    return False


def _tag(steps: list[dict], choice: str, goal: str | None) -> list[dict]:
    for step in steps:
        step["toolChoice"] = choice
        if goal and not step.get("goal"):
            step["goal"] = goal
    return steps


def _graphrag_fallback(task: dict, store: GraphStore, prior: list[dict]) -> list[dict]:
    """Text search belongs to GraphRAG, not the reader."""
    hints = dict(task.get("hints") or {})
    hints["query"] = hints.get("query") or task.get("question")
    local = handle_graphrag({**task, "hints": {**hints, "mode": "local"}}, store, prior)
    globe = handle_graphrag({**task, "hints": {**hints, "mode": "global"}}, store, prior)
    return local + globe


def run_assignment(
    store: GraphStore,
    assignment: dict,
    prior: list[dict],
    question: str,
    prior_briefs: list[str] | None = None,
) -> list[dict]:
    """Run one routed specialist. Invalid tool choice falls back to the rule handler."""
    agent = ROSTER_BY_ID.get(str(assignment.get("agentId") or ""))
    if agent is None:
        return []
    task = {**assignment, "question": question, "prior_briefs": list(prior_briefs or [])}
    goal = assignment.get("goal")
    hints = task.get("hints") or {}
    if agent["id"] == "reader" and not (isinstance(hints.get("id"), str) and hints.get("id")):
        return _tag(
            _graphrag_fallback(task, store, prior),
            "rules",
            goal or "GraphRAG fallback because no investigation id",
        )
    chosen = choose_tool(agent, task, prior)
    if chosen:
        tool, args = chosen
        steps = _tag([_step(agent, tool, args, store)], "llm", goal)
        if not _result_empty(steps):
            return steps
    steps = agent["handle"](task, store, prior)
    if agent["id"] == "reader" and not steps:
        steps = _graphrag_fallback(task, store, prior)
    return _tag(steps, "rules", goal)


RANKER = {
    "id": "ranker",
    "role": "Ranks investigations for one habitable environment",
    "description": "Uses RELEVANT_TO scores. Oxygen percentage is reported beside the rank, and is not the rank.",
    "tools": ["rank_investigations"],
    "handle": handle_ranker,
}
READER = {
    "id": "reader",
    "role": "Reads one investigation card",
    "description": "Opens one PSI record. Fuels and oxygen stay on Investigation. Chunk search belongs to GraphRAG.",
    "tools": ["get_investigation"],
    "handle": handle_reader,
}
COMPARER = {
    "id": "comparer",
    "role": "Compares investigations on the measured conditions",
    "description": "States what is shared and what changes: oxygen, airflow, fuel, role, hardware family.",
    "tools": ["compare_investigations"],
    "handle": handle_comparer,
}
NAVIGATOR = {
    "id": "navigator",
    "role": "Walks the knowledge graph",
    "description": "Follows hardware, fuel, and role edges, and attaches history for a scenario.",
    "tools": ["graph_neighbors", "scenario_context"],
    "handle": handle_navigator,
}
GRAPHRAG = {
    "id": "graphrag",
    "role": "Local and global GraphRAG search",
    "description": (
        "local_search uses MiniLM cosine plus BM25 (RRF), then reranks, plus one-hop neighbors. "
        "global_search reads Community summaries. Oxygen stays on Investigation nodes."
    ),
    "tools": ["local_search", "global_search"],
    "handle": handle_graphrag,
}

ROSTER = [RANKER, READER, COMPARER, NAVIGATOR, GRAPHRAG]
ROSTER_BY_ID = {agent["id"]: agent for agent in ROSTER}


def roster_public() -> list[dict]:
    return [
        {
            "id": agent["id"],
            "role": agent["role"],
            "description": agent["description"],
            "tools": agent["tools"],
        }
        for agent in ROSTER
    ]
