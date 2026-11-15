"""Short session memory for follow-up questions.

Stores scenario, focus, and ranked PSI ids. It does not store answer prose,
so a later turn cannot reuse a percent that was never in the catalog.
"""

from __future__ import annotations

import re
from typing import Any

from app.knowledge import SCENARIOS, detect_scenario

_FOLLOW = re.compile(
    r"\b(it|its|that|those|them|previous|above)\b|"
    r"cái đó|cai do|cái này|cai nay|\bnó\b|\bthế\b|\bvậy\b|\bcòn\b|vừa rồi|vua roi",
    re.I,
)
_ANAPHORA = re.compile(
    r"\b(it|its|that|those|them)\b|cái đó|cai do|cái này|cai nay|\bnó\b",
    re.I,
)
_ABOUT_CARD = re.compile(r"oxy|oxygen|fuel|nhiên|nhien|hardware|phần cứng|phan cung|airflow|\bflow\b", re.I)
_RANKISH = re.compile(
    r"\brank|relevant|priority|\btop\b|\bbest\b|which investigation|xếp hạng|xep hang|nên đọc|nen doc",
    re.I,
)
MAX_TURNS = 8


class SessionMemory:
    def __init__(self) -> None:
        self._sessions: dict[str, list[dict[str, Any]]] = {}

    def recent(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._sessions.get(session_id) or [])

    def remember(self, session_id: str, turn: dict[str, Any]) -> None:
        if not session_id:
            return
        rows = self._sessions.setdefault(session_id, [])
        rows.append(
            {
                "question": turn.get("question") or "",
                "scenario": turn.get("scenario"),
                "focusId": turn.get("focusId"),
                "rankedIds": list(turn.get("rankedIds") or []),
                "mode": turn.get("mode") or "",
            }
        )
        del rows[:-MAX_TURNS]

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


SESSIONS = SessionMemory()


def _turn_row(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": turn.get("question") or "",
        "scenario": turn.get("scenario"),
        "focusId": turn.get("focusId"),
        "rankedIds": [str(item) for item in (turn.get("rankedIds") or []) if item],
        "mode": turn.get("mode") or "",
    }


def recent_cloud(store: Any, session_id: str) -> list[dict[str, Any]]:
    """Load follow-up state from Neo4j Aura. Raises if the database is unreachable."""
    rows = store.run(
        """
        MATCH (s:ChatSession {id: $id})-[:HAS_TURN]->(t:ChatTurn)
        RETURN t.question AS question, t.scenario AS scenario, t.focusId AS focusId,
               t.rankedIds AS rankedIds, t.mode AS mode
        ORDER BY t.at
        """,
        id=session_id,
    )
    loaded = []
    for row in rows:
        loaded.append(
            {
                "question": row.get("question") or "",
                "scenario": row.get("scenario"),
                "focusId": row.get("focusId"),
                "rankedIds": list(row.get("rankedIds") or []),
                "mode": row.get("mode") or "",
            }
        )
    return loaded


def remember_cloud(store: Any, session_id: str, turn: dict[str, Any]) -> None:
    """Append one turn in Aura and keep only the newest MAX_TURNS. No answer prose."""
    if not session_id:
        return
    row = _turn_row(turn)
    store.run(
        """
        MERGE (s:ChatSession {id: $id})
        CREATE (t:ChatTurn {
            question: $question,
            scenario: $scenario,
            focusId: $focusId,
            rankedIds: $rankedIds,
            mode: $mode,
            at: timestamp()
        })
        MERGE (s)-[:HAS_TURN]->(t)
        """,
        id=session_id,
        **row,
    )
    store.run(
        """
        MATCH (s:ChatSession {id: $id})-[:HAS_TURN]->(t:ChatTurn)
        WITH t ORDER BY t.at DESC
        SKIP $keep
        DETACH DELETE t
        """,
        id=session_id,
        keep=MAX_TURNS,
    )


def load_turns(store: Any, session_id: str) -> tuple[list[dict[str, Any]], str]:
    try:
        return recent_cloud(store, session_id), "aura"
    except Exception:  # noqa: BLE001
        return SESSIONS.recent(session_id), "local"


def save_turn(store: Any, session_id: str, turn: dict[str, Any]) -> str:
    try:
        remember_cloud(store, session_id, turn)
        return "aura"
    except Exception:  # noqa: BLE001
        SESSIONS.remember(session_id, turn)
        return "local"


def is_followup(question: str) -> bool:
    return bool(_FOLLOW.search(question or ""))


def _focus_of(turn: dict[str, Any]) -> str | None:
    focus = turn.get("focusId")
    if isinstance(focus, str) and focus:
        return focus
    ranked = turn.get("rankedIds") or []
    if ranked:
        return str(ranked[0])
    return None


def apply_memory(question: str, named_ids: list[str], turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve 'it' / 'nó' from the last turn. A new scenario name wins."""
    detected = detect_scenario(question)
    ids = list(named_ids)
    scenario = detected if detected in SCENARIOS else None
    last = turns[-1] if turns else None
    follow = bool(last) and is_followup(question) and detected is None
    if follow and last:
        focus = _focus_of(last)
        rankish = bool(_RANKISH.search(question or ""))
        remembered = last.get("scenario")
        about_card = bool(_ANAPHORA.search(question or "")) or bool(_ABOUT_CARD.search(question or ""))
        if focus and about_card and not rankish and focus not in ids:
            ids = [focus, *ids]
        elif rankish and remembered in SCENARIOS:
            scenario = remembered
        elif not ids and remembered in SCENARIOS:
            scenario = remembered
    notes = []
    for turn in turns[-4:]:
        ranked = ", ".join(turn.get("rankedIds") or []) or "none"
        notes.append(
            f"memory: earlier question “{turn.get('question') or ''}”; "
            f"scenario {turn.get('scenario') or 'none'}; "
            f"focus {turn.get('focusId') or 'none'}; ranked {ranked}."
        )
    return {"scenario": scenario, "named_ids": ids, "notes": notes, "follow": follow}
