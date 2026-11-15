"""LangGraph coordinator: route → specialists → synthesize."""

from __future__ import annotations

import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents import roster_public, run_assignment
from app.citations import normalize_citations
from app.evidence_filter import contains_dump, is_garbage_text, wants_image
from app.graph import GraphStore
from app.knowledge import SCENARIOS, detect_role, detect_scenario
from app import intent as question_intent
from app.intent import resolve_answer_language
from app.memory import apply_memory
from app.neo4j_config import ROOT
from app.prompts import grounded_complete
from app.tools import SPECS, mentioned_from_question

class AgentState(TypedDict, total=False):
    question: str
    scenario: str | None
    role: str | None
    named_ids: list[str]
    messages: list[dict[str, str]]
    ranked: list[dict]
    chunks: list[dict]
    communities: list[dict]
    trace: list[dict]
    answer: str
    focus_id: str | None
    mode: str
    plan: list[str]
    assignments: list[dict]
    compare_ids: list[str]
    wants_graph: bool
    limit: int
    memory_notes: list[str]
    prior_briefs: list[str]
    answer_language: str
    search_query: str
    off_topic: bool
    injection: bool
    meta: bool
    previous_questions: list[str]


def route(question: str, named_ids: list[str] | None = None, scenario: str | None = None) -> list[dict]:
    """Assign work. An explicit scenario is used only when the question names none."""
    scenario = detect_scenario(question) or (scenario if scenario in SCENARIOS else None)
    role = detect_role(question)
    ids = list(named_ids or [])
    for digits in re.findall(r"PSI-(\d+)", question, re.I):
        psi_id = f"PSI-{digits}"
        if psi_id not in ids:
            ids.append(psi_id)
    q = question.lower()
    wants_compare = bool(
        re.search(r"\bcompare|comparison|versus|\bvs\b|differen|so sánh|so sanh|khác nhau|khac nhau", q)
    )
    wants_graph = bool(
        re.search(
            r"\bhardware|neighbou?r|\brelated\b|\bshare|\bfuel\b|family|phần cứng|phan cung|nhiên liệu|nhien lieu",
            q,
        )
    )
    wants_rank = bool(scenario) or bool(
        re.search(
            r"\brank|relevant|priority|\btop\b|\bbest\b|which investigation|xếp hạng|xep hang|nên đọc|nen doc",
            q,
        )
    )

    def step(agent_id: str, goal: str, hints: dict) -> dict:
        return {"agentId": agent_id, "goal": goal, "hints": hints}

    if wants_compare and len(ids) >= 2:
        return [step("comparer", "Compare the named investigations", {"ids": ids[:4]})]
    if wants_compare and not ids and _PHENOMENON_RE.search(question):
        # A flame or mechanism compared across environments is not a catalog rank.
        return [
            step("graphrag", "Local GraphRAG search", {"query": question, "limit": 5}),
            step("graphrag", "Search community summaries", {"query": question, "mode": "global", "limit": 5}),
        ]
    if wants_compare:
        return [
            step("ranker", "Pick the leading investigations", {"scenario": scenario or "iss", "role": role, "limit": 2}),
            step("comparer", "Compare those leaders", {"fromRanker": True}),
        ]
    if len(ids) == 1 and wants_graph:
        return [
            step("reader", "Read the investigation", {"id": ids[0]}),
            step("navigator", "Walk the graph around it", {"id": ids[0]}),
        ]
    if len(ids) == 1 and not wants_rank:
        return [step("reader", "Read the investigation", {"id": ids[0]})]
    if wants_rank:
        chosen = scenario or "iss"
        return [
            step("ranker", "Rank investigations for this environment", {"scenario": chosen, "role": role, "limit": 5}),
            step("navigator", "Attach the atmosphere bound and linked history", {"scenario": chosen}),
        ]
    if _wants_global(q):
        return [step("graphrag", "Search community summaries", {"query": question, "mode": "global", "limit": 5})]
    return [
        step("graphrag", "Local GraphRAG search", {"query": question, "limit": 5}),
        step("graphrag", "Search community summaries", {"query": question, "mode": "global", "limit": 5}),
    ]


_PHENOMENON_RE = re.compile(
    r"flame|ngọn lửa|ngon lua|vi trọng lực|vi trong luc|"
    r"micro-?gravity|micro-g|"
    r"hình dạng|hinh dang|nhiệt độ|nhiet do|"
    r"tốc độ cháy|toc do chay|lan truyền|lan truyen",
    re.IGNORECASE,
)
_COMPARE_WORD_RE = re.compile(
    r"\bcompare|comparison|versus|\bvs\b|differen|so sánh|so sanh|khác nhau|khac nhau",
    re.IGNORECASE,
)
_NO_COMPARISON_SENTENCE = (
    "Dữ liệu truy xuất được không chứa so sánh này, nên không có dữ liệu trong bằng chứng."
)


def _wants_global(q: str) -> bool:
    return bool(
        re.search(
            r"\bcommunit(?:y|ies)\b|\bthemes?\b|\boverview\b|across (?:the )?psi|"
            r"\blandscape\b|global search|fire-safety theme",
            q,
        )
    )


def plan_nodes(
    question: str,
    named_ids: list[str] | None = None,
    scenario: str | None = None,
) -> list[str]:
    plan: list[str] = []
    for step in route(question, named_ids, scenario=scenario):
        agent = step["agentId"]
        hints = step.get("hints") or {}
        if agent == "ranker":
            keys = ["rank"]
        elif agent == "comparer":
            keys = ["compare"]
        elif agent == "reader" and isinstance(hints.get("id"), str):
            keys = ["get_investigation"]
        elif agent == "navigator" and isinstance(hints.get("id"), str):
            keys = ["extract_or_search"]
        elif agent == "navigator":
            keys = ["rank"]
        elif agent == "graphrag" and hints.get("mode") == "global":
            keys = ["global_community"]
        elif agent == "graphrag":
            keys = ["extract_or_search", "global_community"]
        else:
            keys = ["extract_or_search"]
        for key in keys:
            if key not in plan:
                plan.append(key)
    return plan


def _paragraph(lines: list[str]) -> str:
    return "\n\n".join(line for line in lines if line)


def compose(trace: list[dict]) -> dict:
    blocks: list[str] = []
    ranked: list[dict] = []
    focus_id: str | None = None

    for step in trace:
        result = step.get("result") or {}
        if not result.get("ok"):
            blocks.append(result.get("summary") or "")
            continue
        data = result.get("data") or {}
        tool = step.get("tool")
        if tool == "rank_investigations":
            spec = data.get("scenario") or {}
            rows = data.get("ranked") or []
            ranked = rows
            role = data.get("role")
            lines = []
            for index, row in enumerate(rows):
                runs = "run count not in the table" if row.get("runCount") is None else f"{row['runCount']} runs"
                fuels = ", ".join((row.get("fuels") or [])[:6]) or "no fuel column held here"
                note = f" {row['note']}" if row.get("note") else ""
                lines.append(
                    f"{index + 1}. {row['name']} ({row['id']}), relevance {row['score']}. "
                    f"{row.get('reason') or ''} Measured {row['oxygen']}; airflow {row['flow']}; {runs}. "
                    f"Fuels: {fuels}. Hardware family: {row['family']}.{note}"
                )
            blocks.append(
                _paragraph(
                    [
                        f"{spec.get('label', '')}"
                        + (f", limited to {role}" if role else "")
                        + ". Relevance is a fire-safety rank, separate from the oxygen percentage measured in the runs.",
                        "\n".join(lines),
                        spec.get("caveat") or "",
                    ]
                )
            )
        if tool == "get_investigation":
            focus_id = str(data.get("id"))
            peers = data.get("peers") or []
            peer_text = (
                " Same hardware family: "
                + ", ".join(f"{peer['name']} ({peer['id']})" for peer in peers)
                + "."
                if peers
                else ""
            )
            fuels = ", ".join(data.get("fuels") or []) or "none listed"
            runs = data.get("runCount")
            blocks.append(
                _paragraph(
                    [
                        f"{data.get('name')} ({data.get('id')}). {data.get('blurb') or ''}",
                        str(data.get("summary") or ""),
                        f"Measured {data.get('oxygen')}; airflow {data.get('flow')}; "
                        f"{runs if runs is not None else 'no'} runs in the tables held here. "
                        f"Fuels: {fuels}. Hardware family: {data.get('family')}.{peer_text}",
                        str(data["note"]) if data.get("note") else "",
                    ]
                )
            )
        if tool == "compare_investigations":
            same = data.get("same") or []
            different = data.get("different") or []
            rows = data.get("rows") or []
            if not ranked:
                ranked = rows
            blocks.append(
                _paragraph(
                    [
                        "Comparison of "
                        + " and ".join(f"{row['name']} ({row['id']})" for row in rows)
                        + ".",
                        "Shared: " + " ".join(same) if same else "No shared fuel string or hardware family.",
                        " ".join(different),
                        " ".join(f"{row['id']}: {row.get('blurb') or ''}" for row in rows),
                    ]
                )
            )
        if tool == "graph_neighbors":
            focus_id = str(data.get("id"))
            links = data.get("links") or []
            fuels = [link["label"] for link in links if link.get("type") == "fuel"]
            hardware = next((link for link in links if link.get("type") == "hardware"), None)
            roles = [link["label"] for link in links if link.get("type") == "role"]
            peers = data.get("peers") or []
            blocks.append(
                _paragraph(
                    [
                        f"Graph around {data.get('name')} ({data.get('id')}). Family: {data.get('family')}. "
                        f"Metadata hardware node: {(hardware or {}).get('label') or 'none'}. "
                        f"Roles: {', '.join(roles) or 'none'}. Fuel nodes: {', '.join(fuels) or 'none'}.",
                        (
                            "Peers in that family: "
                            + ", ".join(f"{peer['name']} ({peer['id']})" for peer in peers)
                            + "."
                            if peers
                            else ""
                        ),
                    ]
                )
            )
        if tool == "scenario_context":
            spec = data.get("scenario") or {}
            notes = data.get("notes") or []
            note_text = "\n".join(
                f"{note['label']}: {note.get('summary') or ''}" for note in notes
            )
            caveat = spec.get("caveat") or ""
            if any(caveat and caveat in block for block in blocks):
                blocks.append(note_text)
            else:
                blocks.append(_paragraph([caveat, note_text]))
        if tool in {"search_investigations", "local_search"}:
            hits = data.get("hits") or []
            if not hits:
                blocks.append("No investigation matched those words.")
            else:
                lines = []
                for hit in hits:
                    inv = hit.get("investigation") or {}
                    page = hit.get("page")
                    page_bit = f"p.{page}" if page is not None else "page unknown"
                    quote = hit.get("preview") or ""
                    neighbor_bit = ""
                    neighbors = [
                        f"{n.get('label') or n.get('id')} ({n.get('id')})"
                        for n in (hit.get("neighbors") or [])
                        if n.get("id")
                    ]
                    if neighbors:
                        neighbor_bit = " Graph neighbors: " + ", ".join(neighbors[:8]) + "."
                    if inv:
                        fuels = ", ".join((inv.get("fuels") or [])[:6]) or "none listed"
                        lines.append(
                            f"{inv.get('name')} ({inv.get('id')}). Catalog fuels: {fuels}. "
                            f"Measured {inv.get('oxygen')}. Supporting quote ({hit.get('chunkId')}, {page_bit}): "
                            f"“{quote}”{neighbor_bit}"
                        )
                    else:
                        lines.append(
                            f"Chunk {hit.get('chunkId')} ({hit.get('psi')}, {page_bit}): “{quote}”{neighbor_bit}"
                        )
                blocks.append("\n".join(lines))
        if tool == "global_search":
            communities = data.get("communities") or []
            if data.get("degraded") and data.get("reason") == "no_communities":
                blocks.append("No community summaries are in the graph yet.")
            elif not communities:
                blocks.append("No community summaries are in the graph yet.")
            else:
                lines = []
                if data.get("degraded"):
                    lines.append(
                        "Community titles are available; LLM summaries are still being written."
                    )
                for item in communities:
                    psi = ", ".join(item.get("psiIds") or []) or "no PSI ids"
                    catalog_ox = []
                    for inv in item.get("investigations") or []:
                        catalog_ox.append(f"{inv['id']} {inv.get('oxygen')}")
                    ox = (
                        " Catalog oxygen (Investigation properties): " + "; ".join(catalog_ox) + "."
                        if catalog_ox
                        else " Oxygen is not taken from the community paragraph."
                    )
                    summary = item.get("summary") or "Summary not written yet."
                    lines.append(
                        f"{item.get('title')} ({item.get('id')}): {summary} "
                        f"PSI ids: {psi}.{ox}"
                    )
                blocks.append("\n".join(lines))

    return {
        "text": "\n\n".join(block for block in blocks if block),
        "ranked": ranked,
        "focusId": focus_id,
    }


def initial_state(
    store: GraphStore,
    question: str,
    memory: list[dict] | None = None,
    user_language: str | None = None,
) -> AgentState:
    trimmed = question.strip()
    named = mentioned_from_question(store, trimmed) if trimmed else []
    carried = apply_memory(trimmed, named, memory or [])
    named = carried["named_ids"]
    # English search query + off-topic flag (one cached LLM call; falls back to the question).
    analysis = question_intent.analyze(trimmed)
    meta = question_intent.is_meta_question(trimmed)
    return {
        "question": trimmed,
        "answer_language": resolve_answer_language(trimmed, user_language),
        "search_query": analysis["query"],
        "off_topic": bool(analysis["offTopic"]) and not named and not meta,
        "injection": bool(analysis.get("injection")),
        "meta": meta,
        "previous_questions": question_intent.previous_questions(memory),
        "scenario": carried["scenario"],
        "role": detect_role(trimmed) if trimmed else None,
        "named_ids": named,
        "memory_notes": carried["notes"],
        "messages": [{"role": "user", "content": trimmed}] if trimmed else [],
        "ranked": [],
        "chunks": [],
        "communities": [],
        "trace": [],
        "answer": "",
        "focus_id": None,
        "mode": "empty" if not trimmed else "",
        "plan": [],
        "compare_ids": named[:4],
        "wants_graph": False,
        "limit": 5,
    }


def _step_evidence(step: dict) -> dict[str, Any]:
    """Catalog rows, quotes, communities, and scenario notes from one tool result."""
    result = step.get("result") or {}
    data = result.get("data") or {} if result.get("ok") else {}
    tool = step.get("tool")
    ranked: list[dict] = []
    chunks: list[dict] = []
    communities: list[dict] = []
    notes: list[str] = []
    if tool == "rank_investigations":
        ranked = list(data.get("ranked") or [])
        spec = data.get("scenario") or {}
        if spec.get("label"):
            notes.append(str(spec["label"]))
        if spec.get("caveat"):
            notes.append(str(spec["caveat"]))
    elif tool == "compare_investigations":
        ranked = list(data.get("rows") or [])
        for line in (data.get("same") or []) + (data.get("different") or []):
            notes.append(str(line))
    elif tool == "get_investigation":
        if data.get("id"):
            ranked = [data]
        for peer in data.get("peers") or []:
            notes.append(f"{peer.get('name')} ({peer.get('id')})")
    elif tool == "graph_neighbors":
        if data.get("id"):
            notes.append(
                f"{data.get('name')} ({data.get('id')}). Family: {data.get('family')}."
            )
        for link in data.get("links") or []:
            notes.append(f"{link.get('type')}: {link.get('label')} ({link.get('id')})")
    elif tool == "scenario_context":
        spec = data.get("scenario") or {}
        if spec.get("label"):
            notes.append(str(spec["label"]))
        if spec.get("caveat"):
            notes.append(str(spec["caveat"]))
        for note in data.get("notes") or []:
            notes.append(f"{note.get('label')}: {note.get('summary') or ''}")
    elif tool in {"local_search", "search_investigations"}:
        chunks = list(data.get("hits") or [])
    elif tool == "global_search":
        communities = list(data.get("communities") or [])
    return {
        "ranked": ranked,
        "chunks": chunks,
        "communities": communities,
        "notes": [note for note in notes if note.strip()],
    }


def _merge_evidence(parts: list[dict]) -> dict[str, Any]:
    ranked: list[dict] = []
    chunks: list[dict] = []
    communities: list[dict] = []
    notes: list[str] = []
    seen_rank: set[str] = set()
    for part in parts:
        for row in part.get("ranked") or []:
            key = str(row.get("id") or "")
            if key and key in seen_rank:
                continue
            if key:
                seen_rank.add(key)
            ranked.append(row)
        chunks.extend(part.get("chunks") or [])
        communities.extend(part.get("communities") or [])
        notes.extend(part.get("notes") or [])
    return {"ranked": ranked, "chunks": chunks, "communities": communities, "notes": notes}


def ensure_fallback_brief(step: dict) -> None:
    """Fixed text when a tool comes back empty, so the crew does not invent a PSI."""
    if step.get("brief"):
        return
    result = step.get("result") or {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    tool = step.get("tool")
    if tool == "rank_investigations" and result.get("ok") and not (data.get("ranked") or []):
        step["brief"] = "No investigations were ranked for this scenario."
    elif tool == "global_search" and not (data.get("communities") or []):
        step["brief"] = "No community summaries were retrieved."
    elif tool == "local_search" and not (data.get("hits") or []):
        step["brief"] = "No chunks were retrieved."
    elif not result.get("ok"):
        step["brief"] = result.get("summary") or "The tool returned no evidence."


_PSI_ID_RE = re.compile(r"\bPSI-(\d+)\b", re.IGNORECASE)
_NO_PSI_RE = re.compile(
    r"(?:there (?:is|are) no|không có|khong co|chưa có|chua co|without(?: an?)?)"
    r"(?:\s+\w+){0,4}\s+PSI\b"
    r"|\bno\s+PSI\b",
    re.IGNORECASE,
)
_CROWN_RE = re.compile(
    r"most relevant|liên quan nhất|lien quan nhat|highest relevance|highest score|top-ranked|top ranked",
    re.IGNORECASE,
)
_SPECIFIC_FACT_RE = re.compile(
    r"\b(?:helium|argon|nitrogen|halon|carbon dioxide)\b"
    r"|\bCO\s*2\b"
    r"|\bCO₂\b"
    r"|\d+(?:[.,]\d+)?\s*(?:µm|μm|um|microns?|mm|%)",
    re.IGNORECASE,
)
_GRAVITY_RE = re.compile(
    r"micro-?g|microgravity|low-g|zero-g|0-g|vi trọng",
    re.IGNORECASE,
)
_CONTRAST_RE = re.compile(
    r"1-g|normal gravity|terrestrial|earth gravity|trái đất|"
    r"larger|smaller|differ|spherical|spread|temperature|nhiệt",
    re.IGNORECASE,
)
_EMPTY_LOCAL = "No chunks were retrieved."
_EMPTY_GLOBAL = "No community summaries were retrieved."
_NAVIGATOR_NOTE = "Navigator adds scenario notes only."
_MISSING_DETAIL = (
    "Các đoạn trích truy xuất được không chứa chi tiết được hỏi. "
    "The retrieved passages do not contain that detail."
)


def _asks_scientific_comparison(question: str) -> bool:
    text = question or ""
    return bool(_COMPARE_WORD_RE.search(text) and _PHENOMENON_RE.search(text))


def _passage_blob(trace: list[dict], chunks: list[dict] | None) -> str:
    parts: list[str] = []
    for hit in chunks or []:
        parts.append(str(hit.get("preview") or ""))
        parts.append(str(hit.get("text") or ""))
    for step in trace or []:
        if step.get("tool") not in {"local_search", "search_investigations"}:
            continue
        data = (step.get("result") or {}).get("data") or {}
        if not (step.get("result") or {}).get("ok"):
            continue
        for hit in data.get("hits") or []:
            parts.append(str(hit.get("preview") or ""))
            parts.append(str(hit.get("text") or ""))
    return "\n".join(parts)


def _passages_have_comparison(blob: str) -> bool:
    return bool(_GRAVITY_RE.search(blob or "") and _CONTRAST_RE.search(blob or ""))


def _catalog_misses_comparison(
    question: str,
    trace: list[dict],
    chunks: list[dict] | None,
) -> bool:
    """Rank and compare cards are not a flame comparison when the passages lack it."""
    if not _asks_scientific_comparison(question):
        return False
    return not _passages_have_comparison(_passage_blob(trace, chunks))


def _psi_ids(text: str) -> list[str]:
    found: list[str] = []
    for digits in _PSI_ID_RE.findall(text or ""):
        psi_id = f"PSI-{digits}"
        if psi_id not in found:
            found.append(psi_id)
    return found


def _claims_no_psi(text: str) -> bool:
    return bool(_NO_PSI_RE.search(text or ""))


def _as_score(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _format_score(score: float) -> str:
    if float(score).is_integer():
        return str(int(score))
    return str(score)


def _ranked_by_score(ranked: list[dict]) -> list[dict]:
    decorated: list[tuple[int, float, int, dict]] = []
    for index, row in enumerate(ranked or []):
        if not row.get("id"):
            continue
        score = _as_score(row.get("score"))
        missing = 1 if score is None else 0
        decorated.append((missing, -(score if score is not None else 0.0), index, row))
    decorated.sort()
    return [item[3] for item in decorated]


def _rank_rows(trace: list[dict]) -> list[dict]:
    for step in trace:
        if step.get("tool") != "rank_investigations":
            continue
        result = step.get("result") or {}
        if not result.get("ok"):
            continue
        rows = (result.get("data") or {}).get("ranked") or []
        if rows:
            return list(rows)
    return []


def _rank_lock_rows(trace: list[dict]) -> list[dict]:
    """Rank list the final answer must follow. Compare and search keep their own answers."""
    tools = {step.get("tool") for step in trace}
    if "rank_investigations" not in tools:
        return []
    if tools & {"compare_investigations", "get_investigation"}:
        return []
    # A ranking answer may carry supporting passages (intent.refine_route adds a search);
    # the rank still leads it. Search only cancels the lock when the answer is not rank-led.
    rank_led = bool(trace) and trace[0].get("tool") == "rank_investigations"
    if not rank_led and tools & {"local_search", "global_search", "search_investigations"}:
        return []
    return _rank_rows(trace)


def _first_psi(text: str) -> str | None:
    match = _PSI_ID_RE.search(text or "")
    if not match:
        return None
    return f"PSI-{match.group(1)}"


def _crowns_other(text: str, top_id: str) -> bool:
    for match in _CROWN_RE.finditer(text or ""):
        before = text[max(0, match.start() - 160) : match.start()]
        found = list(_PSI_ID_RE.finditer(before))
        if found:
            nearest = f"PSI-{found[-1].group(1)}"
        else:
            after = text[match.end() : match.end() + 40]
            found_after = list(_PSI_ID_RE.finditer(after))
            if not found_after:
                continue
            nearest = f"PSI-{found_after[0].group(1)}"
        if nearest != top_id:
            return True
    return False


def _answer_obeys_rank(text: str, top_id: str) -> bool:
    if not text or _claims_no_psi(text):
        return False
    if _first_psi(text) != top_id:
        return False
    if _crowns_other(text, top_id):
        return False
    return True


def _brief_rejects_rank(brief: str, rows: list[dict]) -> bool:
    ordered = _ranked_by_score(rows)
    if not ordered:
        return False
    top_id = str(ordered[0]["id"])
    if _claims_no_psi(brief):
        return True
    if not _psi_ids(brief):
        return False
    return not _answer_obeys_rank(brief, top_id)


def _scenario_notes_from_step(step: dict) -> str:
    notes = [note for note in _step_evidence(step)["notes"] if note and not _claims_no_psi(note)]
    if notes:
        return "\n".join(notes)
    return _NAVIGATOR_NOTE


def _scenario_notes(trace: list[dict], top_id: str) -> list[str]:
    notes: list[str] = []
    for step in trace:
        if step.get("tool") != "scenario_context":
            continue
        for note in _step_evidence(step)["notes"]:
            if not note or _claims_no_psi(note) or _crowns_other(note, top_id):
                continue
            if note not in notes:
                notes.append(note)
    return notes


def _locked_rank_answer(rows: list[dict], notes: list[str] | None = None) -> str:
    ordered = _ranked_by_score(rows)
    if not ordered:
        return "No investigations were ranked for this scenario."
    top = ordered[0]
    name = str(top.get("name") or top["id"])
    score = _as_score(top.get("score"))
    score_bit = f", relevance {_format_score(score)}" if score is not None else ""
    lines = [f"{name} ({top['id']}) is the most relevant investigation{score_bit}."]
    if len(ordered) > 1:
        bits = []
        for row in ordered[1:4]:
            bit = f"{row.get('name') or row['id']} ({row['id']})"
            row_score = _as_score(row.get("score"))
            if row_score is not None:
                bit += f", relevance {_format_score(row_score)}"
            bits.append(bit)
        lines.append("Lower relevance: " + "; ".join(bits) + ".")
    for note in notes or []:
        if note and note not in lines:
            lines.append(note)
    return "\n".join(lines)


def _apply_rank_lock(text: str, rows: list[dict], trace: list[dict]) -> str:
    ordered = _ranked_by_score(rows)
    if not ordered:
        return text
    top_id = str(ordered[0]["id"])
    if text and _answer_obeys_rank(text, top_id):
        return text
    return _locked_rank_answer(ordered, _scenario_notes(trace, top_id))


def _brief_on_rank(agent_id: str, brief: str, rows: list[dict], step: dict) -> str | None:
    if not rows or agent_id not in {"ranker", "navigator"}:
        return None
    if not _brief_rejects_rank(brief, rows):
        return None
    if agent_id == "navigator":
        return _scenario_notes_from_step(step)
    return _locked_rank_answer(rows, [])


def _strip_dump_markers(text: str) -> str:
    cleaned = str(text or "")
    for marker in (
        "Graph neighbors:",
        "Supporting quote",
        "Catalog fuels:",
        "PSI ids:",
        "[ocr:",
        "[figure:",
    ):
        cleaned = cleaned.replace(marker, " ")
    return " ".join(cleaned.split())


def _short_quote(text: str, limit: int = 320) -> str:
    cleaned = _strip_dump_markers(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _fold_text(text: str) -> str:
    return text.lower().replace("₂", "2").replace("µ", "u").replace("μ", "u").replace(" ", "")


def _hit_corpus(hits: list[dict]) -> str:
    parts: list[str] = []
    for hit in hits:
        parts.append(str(hit.get("preview") or ""))
        parts.append(str(hit.get("text") or ""))
        parts.append(str(hit.get("chunkId") or ""))
    return "\n".join(parts)


def _corpus_has_fact(fact: str, corpus: str) -> bool:
    folded = _fold_text(corpus)
    key = _fold_text(fact)
    if key in {"co2", "carbondioxide"}:
        return "co2" in folded or "carbondioxide" in folded
    return key in folded


def _details_missing(question: str, hits: list[dict]) -> bool:
    facts = _SPECIFIC_FACT_RE.findall(question or "")
    if not facts:
        return False
    corpus = _hit_corpus(hits)
    return any(not _corpus_has_fact(fact, corpus) for fact in facts)


def _compose_graphrag_answer(question: str, hits: list[dict], communities: list[dict]) -> str:
    """Quote retrieved chunks. A missing asked fact is said once, then the quotes stop the story."""
    parts: list[str] = []
    if hits and _details_missing(question, hits):
        parts.append(_MISSING_DETAIL)
    elif _asks_scientific_comparison(question) and not _passages_have_comparison(
        " ".join(str(hit.get("preview") or "") for hit in hits)
    ):
        return _NO_COMPARISON_SENTENCE
    for hit in hits[:8]:
        chunk_id = str(hit.get("chunkId") or "").strip()
        quote = _short_quote(hit.get("preview") or "")
        if chunk_id:
            parts.append(f'{chunk_id}: "{quote}"')
        elif quote:
            parts.append(f'"{quote}"')
    if communities:
        parts.append("This is a community summary, not a measurement.")
        for item in communities[:8]:
            community_id = str(item.get("id") or "").strip()
            summary = _short_quote(item.get("summary") or item.get("title") or "")
            if community_id and summary:
                parts.append(f"{community_id}: {summary}")
            elif community_id:
                parts.append(community_id)
            elif summary:
                parts.append(summary)
    return "\n".join(part for part in parts if part)


def _empty_retrieval_line(step: dict, fallback: str) -> str:
    brief = str(step.get("brief") or "").strip()
    if brief and not _PSI_ID_RE.search(brief) and not _claims_no_psi(brief):
        return brief
    return fallback


def _compose_from_trace(question: str, trace: list[dict]) -> str | None:
    """Deterministic GraphRAG answer. None when this trace did not search."""
    saw = False
    hits: list[dict] = []
    communities: list[dict] = []
    empty_bits: list[str] = []
    seen_chunks: set[str] = set()
    seen_communities: set[str] = set()
    for step in trace:
        tool = step.get("tool")
        if tool not in {"local_search", "global_search"}:
            continue
        result = step.get("result") or {}
        if not result.get("ok"):
            continue
        saw = True
        data = result.get("data") or {}
        if tool == "local_search":
            found = list(data.get("hits") or [])
            if not found:
                line = _empty_retrieval_line(step, _EMPTY_LOCAL)
                if line not in empty_bits:
                    empty_bits.append(line)
                continue
            for hit in found:
                chunk_id = str(hit.get("chunkId") or "")
                if chunk_id and chunk_id in seen_chunks:
                    continue
                if chunk_id:
                    seen_chunks.add(chunk_id)
                hits.append(hit)
        else:
            found = list(data.get("communities") or [])
            if not found:
                line = _empty_retrieval_line(step, _EMPTY_GLOBAL)
                if line not in empty_bits:
                    empty_bits.append(line)
                continue
            for item in found:
                community_id = str(item.get("id") or "")
                if community_id and community_id in seen_communities:
                    continue
                if community_id:
                    seen_communities.add(community_id)
                communities.append(item)
    if not saw:
        return None
    if _asks_scientific_comparison(question) and not _passages_have_comparison(
        " ".join(str(hit.get("preview") or "") for hit in hits)
    ):
        return _NO_COMPARISON_SENTENCE
    parts: list[str] = []
    if hits or communities:
        composed = _compose_graphrag_answer(question, hits, communities)
        if composed:
            parts.append(composed)
    parts.extend(empty_bits)
    if not parts:
        return _EMPTY_LOCAL
    return "\n\n".join(parts)


def write_specialist_briefs(
    question: str,
    trace: list[dict],
    prior_briefs: list[str] | None = None,
    answer_language: str | None = None,
) -> list[str]:
    """Each specialist writes a grounded brief from its own tool payload.

    GraphRAG briefs are the retrieved quotes. A rank brief that crowns a
    lower score, or says no PSI id was found, is replaced without another model call.
    """
    briefs: list[str] = []
    context = list(prior_briefs or [])
    rows = _rank_rows(trace)
    for step in trace:
        agent_id = str(step.get("agentId") or "")
        if agent_id not in {"ranker", "reader", "comparer", "navigator", "graphrag"}:
            continue
        if agent_id == "graphrag":
            evidence = _step_evidence(step)
            if evidence["chunks"] or evidence["communities"]:
                step["brief"] = _compose_graphrag_answer(
                    question,
                    evidence["chunks"],
                    evidence["communities"],
                )
        existing = step.get("brief")
        if isinstance(existing, str) and existing.strip():
            replacement = _brief_on_rank(agent_id, existing, rows, step)
            if replacement is not None:
                step["brief"] = replacement
                existing = replacement
            line = f"{agent_id}: {existing}"
            briefs.append(line)
            if line not in context:
                context.append(line)
            continue
        evidence = _step_evidence(step)
        if not any(evidence.values()):
            continue
        brief = grounded_complete(
            question,
            role=agent_id,
            timeout=45,
            briefs=context,
            answer_language=answer_language,
            **evidence,
        )
        if not brief:
            if agent_id == "navigator" and rows:
                brief = _scenario_notes_from_step(step)
            else:
                continue
        replacement = _brief_on_rank(agent_id, brief, rows, step)
        if replacement is not None:
            brief = replacement
        step["brief"] = brief
        line = f"{agent_id}: {brief}"
        briefs.append(line)
        context.append(line)
    return briefs


def answer_from_agents(
    question: str,
    trace: list[dict],
    *,
    chunks: list[dict],
    communities: list[dict],
    ranked: list[dict],
    fallback: str,
    memory_notes: list[str] | None = None,
    answer_language: str | None = None,
) -> str:
    """Coordinator merges specialist briefs. Template text remains if the LLM is rejected."""
    if _catalog_misses_comparison(question, trace, chunks):
        return _NO_COMPARISON_SENTENCE
    lock_rows = _rank_lock_rows(trace)
    # The quote-only answer is the retrieved (English) evidence text. The answer language is
    # mandatory, so other languages go through the coordinator, which writes in that language.
    quotes_fit = (answer_language or "English") == "English"
    retrieval = None if lock_rows or not quotes_fit else _compose_from_trace(question, trace)
    if retrieval is not None:
        write_specialist_briefs(question, trace, answer_language=answer_language)
        return retrieval
    briefs = list(memory_notes or []) + write_specialist_briefs(question, trace, answer_language=answer_language)
    merged = _merge_evidence([_step_evidence(step) for step in trace])
    if ranked and not merged["ranked"]:
        merged["ranked"] = ranked
    if chunks and not merged["chunks"]:
        merged["chunks"] = chunks
    if communities and not merged["communities"]:
        merged["communities"] = communities
    if not briefs and not merged["chunks"] and not merged["communities"] and not merged["ranked"]:
        if lock_rows:
            return _apply_rank_lock(fallback, lock_rows, trace)
        return fallback
    text = grounded_complete(
        question,
        role="coordinator",
        briefs=briefs,
        timeout=60,
        answer_language=answer_language,
        **merged,
    )
    chosen = text or fallback
    if lock_rows:
        return _apply_rank_lock(chosen, lock_rows, trace)
    return chosen


_GUARD_MESSAGE = (
    "Xin lỗi, hệ thống chưa tổng hợp được câu trả lời đáng tin cậy cho câu hỏi này: "
    "phần bằng chứng truy xuất được chưa đủ sạch để trình bày trực tiếp.\n\n"
    "Bạn thử hỏi cụ thể về một hồ sơ PSI (ví dụ: “PSI-99 nghiên cứu gì?”) "
    "hoặc thu hẹp chủ đề (nhiên liệu, cảm biến khói, nồng độ oxy…) để agent tra cứu lại."
)

_GUARD_MESSAGE_EN = (
    "Sorry, the system could not compose a reliable answer to this question yet: "
    "the retrieved evidence is not clean enough to present directly.\n\n"
    "Try asking about a specific PSI investigation (for example, “What does PSI-99 study?”) "
    "or narrow the topic (fuel, smoke sensors, oxygen concentration…) so the agent can look it up again."
)

_FIGURE_RE = re.compile(r"\[figure:\s*([^\]]+)\]")
_MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_SAFFIRE_POINTER = (
    "Hồ sơ gần nhất về ngọn lửa trong môi trường vi trọng lực mà bạn có thể hỏi tiếp: "
    "Saffire-II (PSI-99) và các hồ sơ cùng họ Saffire (PSI-98, PSI-100)."
)

_SAFFIRE_POINTER_EN = (
    "The closest microgravity-flame investigations you can ask about next: "
    "Saffire-II (PSI-99) and its Saffire siblings (PSI-98, PSI-100)."
)

_FIGURE_MARKER_STRIP_RE = re.compile(r"\[figure:[^\]]*\]")


def _figure_payload_caption(hit: dict) -> str:
    """Caption for a UI figure payload item: document/profile + page."""
    inv = hit.get("investigation") or {}
    title = str(inv.get("name") or "").strip()
    if len(title) < 3:
        title = str(hit.get("documentTitle") or "").strip()
    if len(title) < 3:
        source = str(hit.get("source") or "").strip()
        title = source.rsplit("/", 1)[-1] if source else ""
    if not title:
        title = str(hit.get("psi") or "Tài liệu NASA").strip()
    page = hit.get("page")
    return f"{title} — trang {page}" if page else title


def _servable_figure_paths(hit: dict) -> list[str]:
    """Figure paths (relative to ROOT) a hit carries, from both sources.

    The structured `figures` list joined from the chunk assets is the
    reliable channel: the image files are real even when the chunk's OCR
    text is garbage, so no preview-quality gate applies to it. Paths from
    `[figure: ...]` markers inside the preview text are gated on the
    preview not being failed-OCR garbage, since the marker comes from
    that text.
    """
    rels: list[str] = []
    for raw in hit.get("figures") or []:
        rel = str(raw).split("|")[0].strip().replace("\\", "/")
        if rel:
            rels.append(rel)
    preview = str(hit.get("preview") or "")
    if not is_garbage_text(preview):
        for match in _FIGURE_RE.finditer(preview):
            rel = match.group(1).split("|")[0].strip().replace("\\", "/")
            if rel:
                rels.append(rel)
    return rels


def _payload_figures(
    chunks: list[dict], *, exclude_text: str = "", limit: int = 4
) -> list[dict]:
    """Servable figure images from evidence chunks, as UI payload items.

    A figure ships only when its file really exists under ROOT/data and
    passes the same checks as the /api/media route (inside the data tree,
    image extension). Figures already embedded in the answer text (the
    image-intent markdown image) are excluded to avoid duplicates.
    """
    out: list[dict] = []
    seen: set[str] = set()
    data_root = (ROOT / "data").resolve()
    embedded = set(re.findall(r"/api/media/([^\s)]+)", exclude_text or ""))
    for hit in chunks or []:
        for rel in _servable_figure_paths(hit):
            if not rel.startswith("data/"):
                continue
            media_rel = rel[len("data/"):]
            if media_rel in seen or media_rel in embedded:
                continue
            target = (ROOT / rel).resolve()
            if not target.is_relative_to(data_root):
                continue
            if target.suffix.lower() not in _MEDIA_EXTS or not target.is_file():
                continue
            seen.add(media_rel)
            caption = _figure_payload_caption(hit)
            out.append(
                {"src": f"/api/media/{media_rel}", "alt": caption, "caption": caption}
            )
            if len(out) >= limit:
                return out
    return out


def _figure_caption(preview: str) -> str:
    """Short caption from the text right after a [figure: ...] marker."""
    tail = preview.split("]", 1)[1] if "]" in preview else ""
    tail = " ".join(tail.split()).lstrip("# ").strip()
    heading = tail.split("—")[0].strip().rstrip("([{- ").strip()
    fig = re.search(r"figure\s+\d+", tail, re.IGNORECASE)
    caption = heading
    if fig and fig.group(0).lower() not in heading.lower():
        caption = f"{heading} — {fig.group(0).capitalize()}" if heading else fig.group(0).capitalize()
    caption = re.sub(r"[\[\]*`#]", "", caption)
    caption = " ".join(caption.split())[:140].strip()
    if len(caption) < 8 or is_garbage_text(caption):
        return "Hình ảnh từ tài liệu NASA truy xuất được"
    return caption


def _image_answer(question: str, chunks: list[dict], lang_name: str | None = None) -> str | None:
    """Compact answer for image intent: serve a retrieved figure, or say so.

    Returns None when the question is not an image request (or carries no
    retrieved chunks, e.g. read/rank modes, whose answers stand as-is).
    Canned text follows the resolved answer language (Vietnamese or English).
    """
    if not wants_image(question) or not chunks:
        return None
    vi = lang_name == "Vietnamese"
    pointer = _SAFFIRE_POINTER if vi else _SAFFIRE_POINTER_EN
    data_root = (ROOT / "data").resolve()
    for hit in chunks:
        preview = str(hit.get("preview") or "")
        for rel in _servable_figure_paths(hit):
            if not rel.startswith("data/"):
                continue
            target = (ROOT / rel).resolve()
            if not target.is_relative_to(data_root):
                continue
            if target.suffix.lower() not in _MEDIA_EXTS or not target.is_file():
                continue
            caption = _figure_caption(preview)
            page = hit.get("page")
            if vi:
                page_bit = f", trang {page}" if page else ""
                return (
                    "Dưới đây là hình ảnh liên quan nhất mà thư viện truy xuất được cho yêu cầu của bạn:\n\n"
                    f"![{caption}](/api/media/{rel[len('data/'):]})\n\n"
                    f"*{caption} — trích từ tài liệu truy xuất được{page_bit}.*\n\n"
                    "Lưu ý: đây là hình/biểu đồ trích từ tài liệu nghiên cứu, không nhất thiết là "
                    "ảnh chụp ngọn lửa trong môi trường không trọng lực (zero-g). "
                    + pointer
                )
            page_bit = f", page {page}" if page else ""
            return (
                "Here is the closest matching image the library retrieved for your request:\n\n"
                f"![{caption}](/api/media/{rel[len('data/'):]})\n\n"
                f"*{caption} — from the retrieved document{page_bit}.*\n\n"
                "Note: this is a figure/chart extracted from a research document, not "
                "necessarily a photograph of a flame in zero-g. "
                + pointer
            )
    if vi:
        return (
            "Hiện thư viện chưa có hình ảnh hiển thị trực tiếp cho yêu cầu này: "
            "các tài liệu truy xuất được không kèm file hình có thể phục vụ qua giao diện.\n\n"
            + pointer
        )
    return (
        "The library does not yet have a directly displayable image for this request: "
        "the retrieved documents carry no image file that can be served in the UI.\n\n"
        + pointer
    )


_COMM_CHAIN_RE = re.compile(r"(?:Community\s+)?L\d+:\d+(?:\s*/\s*(?:Community\s+)?L\d+:\d+)+")
_COMM_SINGLE_RE = re.compile(r"Community\s+(L\d+:\d+)")
_COMM_BARE_RE = re.compile(r"\b(L\d+:\d+)\b")
_COMM_RAW_BT_RE = re.compile(r"`community:[^`]*`")
_COMM_RAW_RE = re.compile(r"\bcommunity:[A-Za-z0-9_\-\.]+")
_SCOPED_ID_RE = re.compile(r"\b([A-Za-z0-9_\-]+)@PSI-\d+\b")


def _clean_internal_ids(text: str, communities: list[dict]) -> str:
    """Strip raw internal identifiers from a finalized answer body.

    Community references must read as their topic title, never as
    "Community L2:0"; scoped entity ids ("hibms@PSI-20") lose their
    internal @PSI suffix. A community title that is itself a raw node
    list (contains "@") is never substituted in.
    """
    if not text:
        return text
    titles: dict[str, str] = {}
    for comm in communities or []:
        cid = str(comm.get("id") or "").removeprefix("community:")
        title = str(comm.get("title") or "").strip()
        if cid and title and "@" not in title and len(title) >= 6:
            titles[cid] = title

    def _chain(match: re.Match) -> str:
        names = [titles[p] for p in re.findall(r"L\d+:\d+", match.group(0)) if p in titles]
        return ", ".join(names) if names else "các chủ đề liên quan"

    text = _COMM_CHAIN_RE.sub(_chain, text)
    text = _COMM_SINGLE_RE.sub(lambda m: titles.get(m.group(1), "chủ đề liên quan"), text)
    text = _COMM_BARE_RE.sub(lambda m: titles.get(m.group(1), m.group(0)), text)
    # Degraded community titles are raw member lists ("community:gipc, glenn"):
    # never let that internal form reach the reader either.
    text = _COMM_RAW_BT_RE.sub("một chủ đề trong đồ thị", text)
    text = _COMM_RAW_RE.sub("một chủ đề trong đồ thị", text)
    text = _SCOPED_ID_RE.sub(r"\1", text)
    return text


def image_answer_from_figures(figures: list[dict], lang_name: str | None = None) -> str:
    """Compact answer text when figures were rescued at the endpoint layer:
    the synthesized text refused to show images the library actually has,
    so it is replaced — the figures themselves render below this text."""
    first = (figures or [{}])[0]
    if lang_name == "Vietnamese":
        caption = str(first.get("caption") or "Hình ảnh từ tài liệu NASA truy xuất được")
        return (
            "Dưới đây là hình ảnh liên quan nhất mà thư viện truy xuất được cho yêu cầu của bạn — "
            "ảnh hiển thị ngay bên dưới đáp án này.\n\n"
            f"*{caption}.*\n\n"
            "Lưu ý: đây là hình/biểu đồ trích từ tài liệu nghiên cứu, không nhất thiết là "
            "ảnh chụp ngọn lửa trong môi trường không trọng lực (zero-g). "
            + _SAFFIRE_POINTER
        )
    caption = str(first.get("caption") or "Image from a retrieved NASA document")
    return (
        "Here is the closest matching image the library retrieved for your request — "
        "it is displayed right below this answer.\n\n"
        f"*{caption}.*\n\n"
        "Note: this is a figure/chart extracted from a research document, not "
        "necessarily a photograph of a flame in zero-g. "
        + _SAFFIRE_POINTER_EN
    )


_TABLE_INTENT_RE = re.compile(
    r"bảng|table|csv|experiment(?:al)?\s+(?:data|table)|dữ liệu\s+(?:thí nghiệm|thực nghiệm)|lần chạy|run\s+(?:data|log)",
    re.IGNORECASE,
)


def wants_table(question: str) -> bool:
    return bool(_TABLE_INTENT_RE.search(question or ""))


def _payload_tables(state: AgentState, limit: int = 2) -> list[dict]:
    """Real Experiment Table CSVs for the investigations in focus.

    Parsed verbatim from data/psi/<PSI>_*/Experiment Table/*.csv — the
    same files the catalog was built from — so the UI can render the
    original table next to the answer. Never routed through the LLM.
    """
    import csv as _csv

    ids: list[str] = []
    focus = state.get("focus_id")
    if focus:
        ids.append(str(focus))
    for row in state.get("ranked") or []:
        if row.get("id"):
            ids.append(str(row["id"]))
    for nid in state.get("named_ids") or []:
        ids.append(str(nid))
    out: list[dict] = []
    seen: set[str] = set()
    psi_root = ROOT / "data" / "psi"
    for psi in ids:
        if psi in seen or len(out) >= limit:
            continue
        seen.add(psi)
        try:
            dirs = sorted(psi_root.glob(f"{psi}_*"))
        except Exception:  # noqa: BLE001
            continue
        for folder_root in dirs:
            folder = folder_root / "Experiment Table"
            if not folder.is_dir():
                continue
            done = False
            for path in sorted(folder.glob("*.csv")):
                try:
                    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
                        reader = _csv.DictReader(fh)
                        keys = reader.fieldnames or []
                        columns = [str(c).lstrip("﻿").strip() for c in keys][:10]
                        rows: list[list[str]] = []
                        total = 0
                        for raw in reader:
                            cells = [str(raw.get(k) or "").strip()[:60] for k in keys][:10]
                            if not any(cells):
                                continue
                            total += 1
                            if len(rows) < 12:
                                rows.append(cells)
                except Exception:  # noqa: BLE001
                    continue
                if columns and rows:
                    out.append(
                        {
                            "psi": psi,
                            "file": path.name,
                            "columns": columns,
                            "rows": rows,
                            "totalRows": total,
                            "shownRows": len(rows),
                        }
                    )
                    done = True
                    break
            if done:
                break
    return out


def format_ask(state: AgentState) -> dict:
    chunks = state.get("chunks") or []
    cited: list[dict] = []
    lang_name = state.get("answer_language")
    text = _image_answer(state.get("question") or "", chunks, lang_name)
    image_mode = text is not None
    if text is None:
        text = normalize_citations(
            state.get("answer") or "",
            chunks=chunks,
            communities=state.get("communities") or [],
            ranked=state.get("ranked") or [],
            cited_chunks=cited,
        )
        # Leftover [figure: ...] markers are display noise: the figures
        # themselves ship as a structured payload for the UI to render.
        text = _FIGURE_MARKER_STRIP_RE.sub("", text)
        # Raw internal ids (Community L2:0, hibms@PSI-20) must never reach
        # the reader: communities read as their topic titles. Only the
        # answer body is cleaned — the sources section keeps its format.
        _body, _sep, _tail = text.partition("## Nguồn & bằng chứng")
        text = _clean_internal_ids(_body, state.get("communities") or []) + _sep + _tail
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if contains_dump(text):
        # The raw retrieval template (compose fallback) must never reach the
        # user, whatever made synthesis fail. Drop the sources section too:
        # it was built from the same payload.
        text = _GUARD_MESSAGE if lang_name == "Vietnamese" else _GUARD_MESSAGE_EN
        figures: list[dict] = []
    else:
        # Figures come first from the chunks the answer actually cited; in
        # global mode the synthesis often cites only GraphRAG communities,
        # so fall back to this turn's retrieved evidence chunks (already
        # hygiene-filtered at the tool layer) — still this answer's own
        # retrieval set, never unrelated data.
        figures = _payload_figures(cited, exclude_text=text)
        if not figures:
            figures = _payload_figures(chunks, exclude_text=text)
    return {
        "question": state.get("question") or "",
        "text": text,
        "trace": state.get("trace") or [],
        "ranked": state.get("ranked") or [],
        "chunks": state.get("chunks") or [],
        "communities": state.get("communities") or [],
        "focusId": state.get("focus_id"),
        "mode": state.get("mode") or "empty",
        "figures": figures,
        "answerLanguage": lang_name,
        "tables": _payload_tables(state) if wants_table(state.get("question") or "") else [],
        "retrieval": question_intent.retrieval_from_trace(state.get("trace") or []),
    }


def _mode_from_trace(trace: list[dict]) -> str:
    tools_used = [step.get("tool") for step in trace]
    if "compare_investigations" in tools_used:
        return "compare"
    if "rank_investigations" in tools_used:
        return "rank"
    if "get_investigation" in tools_used:
        return "read"
    if "global_search" in tools_used:
        return "global"
    if "local_search" in tools_used or "search_investigations" in tools_used:
        return "search"
    return "empty"


def _absorb(state: AgentState, steps: list[dict]) -> dict[str, Any]:
    ranked = list(state.get("ranked") or [])
    chunks = list(state.get("chunks") or [])
    communities = list(state.get("communities") or [])
    focus = state.get("focus_id")
    for step in steps:
        result = step.get("result") or {}
        if not result.get("ok"):
            continue
        data = result.get("data") or {}
        tool = step.get("tool")
        if tool == "rank_investigations":
            ranked = data.get("ranked") or []
        elif tool == "compare_investigations" and not ranked:
            ranked = data.get("rows") or []
        elif tool in {"local_search", "search_investigations"}:
            chunks = data.get("hits") or []
        elif tool == "global_search":
            communities = data.get("communities") or []
        elif tool in {"get_investigation", "graph_neighbors"} and data.get("id"):
            focus = str(data["id"])
    return {"ranked": ranked, "chunks": chunks, "communities": communities, "focus_id": focus}


def build_graph(store: GraphStore):
    def route_node(state: AgentState) -> dict[str, Any]:
        question = state.get("question") or ""
        if not question:
            return {
                "plan": [],
                "assignments": [],
                "answer": "Ask about a crew environment (ISS, Moon, Mars, rover, suit, Earth) or name an investigation.",
                "mode": "empty",
            }
        canned = question_intent.canned_lang(state.get("answer_language"))
        if state.get("injection"):
            # Input guardrail: prompt-injection wording never reaches tools or the answer LLM.
            return {
                "plan": [],
                "assignments": [],
                "answer": question_intent.blocked_text(canned),
                "mode": "blocked",
            }
        if state.get("meta"):
            # "What did I just ask?" is answered from session memory, not from the documents.
            return {
                "plan": [],
                "assignments": [],
                "answer": question_intent.history_answer(state.get("previous_questions") or [], canned),
                "mode": "history",
            }
        if state.get("off_topic"):
            return {
                "plan": [],
                "assignments": [],
                "answer": question_intent.off_topic_text(canned),
                "mode": "offtopic",
            }
        named = state.get("named_ids") or []
        remembered = state.get("scenario")
        assignments = question_intent.refine_route(
            question,
            named,
            route(question, named, scenario=remembered),
            state.get("search_query") or question,
        )
        plan = plan_nodes(question, named, scenario=remembered)
        return {
            "plan": plan,
            "assignments": assignments,
            "scenario": detect_scenario(question) or remembered,
            "role": detect_role(question),
            "compare_ids": named[:4],
            "messages": list(state.get("messages") or [])
            + [{"role": "system", "content": "plan:" + ",".join(plan)}],
        }

    def dispatch(state: AgentState) -> str:
        if state.get("assignments"):
            return "act"
        return "synthesize"

    def act_node(state: AgentState) -> dict[str, Any]:
        assignments = list(state.get("assignments") or [])
        current = assignments[0]
        trace = list(state.get("trace") or [])
        prior_briefs = list(state.get("prior_briefs") or [])
        question = state.get("question") or ""
        steps = run_assignment(store, current, trace, question, prior_briefs=prior_briefs)
        for step in steps:
            ensure_fallback_brief(step)
        new_briefs = write_specialist_briefs(question, steps, prior_briefs=prior_briefs)
        absorbed = _absorb(state, steps)
        return {
            "assignments": assignments[1:],
            "trace": trace + steps,
            "prior_briefs": prior_briefs + new_briefs,
            **absorbed,
        }

    def synthesize_node(state: AgentState) -> dict[str, Any]:
        if state.get("mode") in {"offtopic", "history", "blocked"}:
            return {"answer": state.get("answer") or "", "mode": state.get("mode")}
        if state.get("mode") == "empty" and not (state.get("question") or ""):
            return {
                "answer": state.get("answer")
                or "Ask about a crew environment (ISS, Moon, Mars, rover, suit, Earth) or name an investigation.",
                "mode": "empty",
            }
        trace = list(state.get("trace") or [])
        composed = compose(trace)
        mode = _mode_from_trace(trace)
        text = answer_from_agents(
            state.get("question") or "",
            trace,
            chunks=state.get("chunks") or [],
            communities=state.get("communities") or [],
            ranked=composed["ranked"] or state.get("ranked") or [],
            fallback=composed["text"],
            memory_notes=(state.get("memory_notes") or [])
            + question_intent.retrieval_note(question_intent.retrieval_from_trace(trace)),
            answer_language=state.get("answer_language"),
        ) or composed["text"] or "The crew did not retrieve enough evidence to answer."
        return {
            "trace": trace,
            "answer": text,
            "ranked": composed["ranked"] or state.get("ranked") or [],
            "focus_id": composed["focusId"] or state.get("focus_id"),
            "mode": mode,
            "messages": list(state.get("messages") or [])
            + [{"role": "assistant", "content": text or ""}],
        }

    builder = StateGraph(AgentState)
    builder.add_node("route", route_node)
    builder.add_node("act", act_node)
    builder.add_node("synthesize", synthesize_node)
    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", dispatch, {"act": "act", "synthesize": "synthesize"})
    builder.add_conditional_edges("act", dispatch, {"act": "act", "synthesize": "synthesize"})
    builder.add_edge("synthesize", END)
    return builder.compile()


def compiled_graph(store: GraphStore):
    return build_graph(store)


def ask(store: GraphStore, question: str) -> dict:
    graph = compiled_graph(store)
    state = graph.invoke(initial_state(store, question))
    return format_ask(state)


def agent_catalog() -> dict:
    return {"agents": roster_public(), "tools": SPECS}
