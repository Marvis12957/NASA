"""Live Aura ranking checks. Skip when .env is missing or Aura is unreachable."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.graph import open_store  # noqa: E402
from app.knowledge import detect_scenario  # noqa: E402
from app.neo4j_config import neo4j_settings  # noqa: E402
from app.agents import run_assignment  # noqa: E402
from app.orchestrator import answer_from_agents, ask, ensure_fallback_brief, route  # noqa: E402


def aura_ready() -> bool:
    uri, _user, password, _database = neo4j_settings()
    if not uri or not password:
        return False
    try:
        store = open_store()
        return bool(store and store.ping())
    except Exception:  # noqa: BLE001
        return False


live = pytest.mark.skipif(not aura_ready(), reason="Aura not configured or unreachable")


def test_coordinator_keeps_template_when_llm_invents_psi(monkeypatch) -> None:
    def fake_complete(*_args, **_kwargs):
        return "BASS (PSI-999) burns at 21%."

    monkeypatch.setattr("app.llm.complete_text", fake_complete)
    trace = [
        {
            "agentId": "ranker",
            "tool": "rank_investigations",
            "result": {
                "ok": True,
                "data": {
                    "scenario": {"label": "ISS cabin (21% O2, 14.7 psi)", "caveat": ""},
                    "ranked": [
                        {
                            "id": "PSI-102",
                            "name": "SAME",
                            "score": 96,
                            "oxygen": "no oxygen column in the tables held here",
                            "fuels": ["Teflon"],
                        }
                    ],
                },
            },
        }
    ]
    text = answer_from_agents(
        "What should a crew read for the ISS?",
        trace,
        chunks=[],
        communities=[],
        ranked=trace[0]["result"]["data"]["ranked"],
        fallback="TEMPLATE SAME (PSI-102)",
    )
    assert text == "TEMPLATE SAME (PSI-102)"
    assert "brief" not in trace[0]


def test_coordinator_uses_specialist_brief(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_complete(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Ranker brief: SAME (PSI-102), relevance 96. Catalog has no oxygen column."
        return "Crew should read SAME (PSI-102). Relevance 96 is not an oxygen percent."

    monkeypatch.setattr("app.llm.complete_text", fake_complete)
    trace = [
        {
            "agentId": "ranker",
            "tool": "rank_investigations",
            "result": {
                "ok": True,
                "data": {
                    "scenario": {"label": "ISS cabin (21% O2, 14.7 psi)"},
                    "ranked": [
                        {
                            "id": "PSI-102",
                            "name": "SAME",
                            "score": 96,
                            "oxygen": "no oxygen column in the tables held here",
                            "fuels": ["Teflon"],
                        }
                    ],
                },
            },
        }
    ]
    text = answer_from_agents(
        "What should a crew read for the ISS?",
        trace,
        chunks=[],
        communities=[],
        ranked=[],
        fallback="TEMPLATE",
    )
    assert "PSI-102" in text
    assert "999" not in text
    assert trace[0]["brief"].startswith("Ranker brief")
    assert calls["n"] == 2


def test_roster_runs_only_its_own_tools(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute(tool, args, store):
        calls.append(tool)
        return {"ok": True, "summary": tool, "data": {"hits": [], "communities": [], "ranked": []}}

    monkeypatch.setattr("app.agents.execute", fake_execute)
    from app.agents import run_assignment

    local = run_assignment(
        None,  # type: ignore[arg-type]
        {"agentId": "graphrag", "goal": "Local GraphRAG search", "hints": {"query": "soot"}},
        [],
        "soot particle size",
    )
    global_step = run_assignment(
        None,  # type: ignore[arg-type]
        {
            "agentId": "graphrag",
            "goal": "Search community summaries",
            "hints": {"query": "soot", "mode": "global"},
        },
        [],
        "soot particle size",
    )
    assert [step["tool"] for step in local] == ["local_search"]
    assert [step["tool"] for step in global_step] == ["global_search"]
    assert local[0]["goal"] == "Local GraphRAG search"
    assert calls == ["local_search", "global_search"]


def test_graph_dispatches_roster(monkeypatch) -> None:
    def fake_execute(tool, args, store):
        if tool == "rank_investigations":
            data = {
                "scenario": {"label": "ISS cabin (21% O2, 14.7 psi)", "caveat": ""},
                "ranked": [
                    {
                        "id": "PSI-102",
                        "name": "SAME",
                        "score": 96,
                        "oxygen": "no oxygen column",
                        "flow": "5–10 cm/s",
                        "family": "ISS aerosol",
                        "fuels": ["Teflon"],
                        "reason": "Primary detection dataset.",
                        "runCount": 8,
                    }
                ],
            }
        elif tool == "scenario_context":
            data = {"scenario": {"label": "ISS cabin (21% O2, 14.7 psi)", "caveat": "Station air."}, "notes": []}
        else:
            data = {}
        return {"ok": True, "summary": tool, "data": data}

    monkeypatch.setattr("app.agents.execute", fake_execute)
    monkeypatch.setattr("app.orchestrator.grounded_complete", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.orchestrator.mentioned_from_question", lambda _store, _question: [])
    answer = ask(object(), "What should a crew read for the ISS?")  # type: ignore[arg-type]
    tools = [step["tool"] for step in answer["trace"]]
    agents = [step["agentId"] for step in answer["trace"]]
    # Ranking answers also search the documents for passages to cite (intent.refine_route).
    assert tools == ["rank_investigations", "scenario_context", "local_search"]
    assert agents == ["ranker", "navigator", "graphrag"]
    assert answer["ranked"][0]["id"] == "PSI-102"
    assert answer["mode"] == "rank"


def _ok(tool: str, data: dict) -> dict:
    return {"ok": True, "summary": tool, "data": data}


def test_llm_tool_choice_used_when_valid(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TOOL_CHOICE", "1")
    monkeypatch.setattr(
        "app.llm.complete_text",
        lambda *_args, **_kwargs: '{"tool":"rank_investigations","args":{"scenario":"iss","limit":3}}',
    )
    seen: dict = {}

    def fake_execute(tool, args, store):
        seen["tool"] = tool
        seen["args"] = args
        return _ok(tool, {"ranked": [{"id": "PSI-102"}], "scenario": {"label": "ISS"}})

    monkeypatch.setattr("app.agents.execute", fake_execute)
    steps = run_assignment(
        None,  # type: ignore[arg-type]
        {"agentId": "ranker", "hints": {"scenario": "iss", "limit": 5}},
        [],
        "What should a crew read for the ISS?",
    )
    assert steps[0]["toolChoice"] == "llm"
    assert seen["tool"] == "rank_investigations"
    assert seen["args"]["scenario"] == "iss"
    assert seen["args"]["limit"] == 3


def test_bad_tool_choice_falls_back_to_rules(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TOOL_CHOICE", "1")
    monkeypatch.setattr("app.llm.complete_text", lambda *_args, **_kwargs: "use PSI-999 somehow")

    def fake_execute(tool, args, store):
        return _ok(tool, {"ranked": [{"id": "PSI-102"}]})

    monkeypatch.setattr("app.agents.execute", fake_execute)
    steps = run_assignment(
        None,  # type: ignore[arg-type]
        {"agentId": "ranker", "goal": "rank", "hints": {"scenario": "iss"}},
        [],
        "What should a crew read for the ISS?",
    )
    assert steps[0]["tool"] == "rank_investigations"
    assert steps[0]["toolChoice"] == "rules"
    assert steps[0]["args"]["scenario"] == "iss"


def test_tool_choice_off_skips_llm(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TOOL_CHOICE", "0")

    def boom(*_args, **_kwargs):
        raise AssertionError("tool choice should stay off")

    monkeypatch.setattr("app.llm.complete_text", boom)
    monkeypatch.setattr(
        "app.agents.execute",
        lambda tool, args, store: _ok(tool, {"ranked": [{"id": "PSI-102"}]}),
    )
    steps = run_assignment(
        None,  # type: ignore[arg-type]
        {"agentId": "ranker", "hints": {"scenario": "iss"}},
        [],
        "What should a crew read for the ISS?",
    )
    assert steps[0]["toolChoice"] == "rules"


def test_reader_without_id_uses_graphrag(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TOOL_CHOICE", "0")
    calls: list[str] = []

    def fake_execute(tool, args, store):
        calls.append(tool)
        if tool == "local_search":
            return _ok(tool, {"hits": [{"chunkId": "ch-1"}]})
        return _ok(tool, {"communities": [{"id": "community:L0:1"}]})

    monkeypatch.setattr("app.agents.execute", fake_execute)
    steps = run_assignment(
        None,  # type: ignore[arg-type]
        {"agentId": "reader", "hints": {}},
        [],
        "soot particle size in microgravity smoke",
    )
    assert calls == ["local_search", "global_search"]
    assert {step["agentId"] for step in steps} == {"graphrag"}
    assert all(step["toolChoice"] == "rules" for step in steps)


def test_comparer_fills_ids_from_ranker(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TOOL_CHOICE", "0")
    seen: dict = {}

    def fake_execute(tool, args, store):
        seen["args"] = args
        return _ok(tool, {"rows": [{"id": "PSI-102"}, {"id": "PSI-69"}]})

    monkeypatch.setattr("app.agents.execute", fake_execute)
    prior = [
        {
            "tool": "rank_investigations",
            "result": {"ok": True, "data": {"ranked": [{"id": "PSI-102"}, {"id": "PSI-69"}, {"id": "PSI-25"}]}},
        }
    ]
    steps = run_assignment(
        None,  # type: ignore[arg-type]
        {"agentId": "comparer", "hints": {"ids": ["PSI-25"]}},
        prior,
        "compare them",
    )
    assert seen["args"]["ids"] == ["PSI-25", "PSI-102"]
    assert steps[0]["toolChoice"] == "rules"


def test_second_assignment_sees_prior_brief(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TOOL_CHOICE", "1")
    seen: dict = {}

    def fake_complete(_system, user, **_kwargs):
        seen["user"] = user
        return '{"tool":"scenario_context","args":{"scenario":"iss"}}'

    monkeypatch.setattr("app.llm.complete_text", fake_complete)
    monkeypatch.setattr(
        "app.agents.execute",
        lambda tool, args, store: _ok(tool, {"scenario": {"label": "ISS cabin"}, "notes": []}),
    )
    run_assignment(
        None,  # type: ignore[arg-type]
        {"agentId": "navigator", "hints": {"scenario": "iss"}},
        [],
        "What should a crew read for the ISS?",
        prior_briefs=["ranker: SAME (PSI-102), relevance 96."],
    )
    assert "ranker: SAME (PSI-102)" in seen["user"]


def test_empty_rank_brief_does_not_invent() -> None:
    step = {
        "agentId": "ranker",
        "tool": "rank_investigations",
        "result": {"ok": True, "data": {"ranked": [], "scenario": {"label": "ISS"}}},
    }
    ensure_fallback_brief(step)
    assert "PSI-" not in step["brief"]
    assert "no investigations" in step["brief"].lower()


def test_act_passes_prior_brief_to_next_agent(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TOOL_CHOICE", "0")
    seen: list[list[str]] = []

    def fake_execute(tool, args, store):
        if tool == "rank_investigations":
            return _ok(
                tool,
                {
                    "scenario": {"label": "ISS cabin (21% O2, 14.7 psi)"},
                    "ranked": [
                        {
                            "id": "PSI-102",
                            "name": "SAME",
                            "score": 96,
                            "oxygen": "no oxygen column",
                            "flow": "5–10 cm/s",
                            "family": "ISS aerosol",
                            "fuels": ["Teflon"],
                            "reason": "Primary detection dataset.",
                            "runCount": 8,
                        }
                    ],
                },
            )
        return _ok(tool, {"scenario": {"label": "ISS cabin (21% O2, 14.7 psi)", "caveat": "Station air."}, "notes": []})

    def fake_grounded(_question, **kwargs):
        seen.append(list(kwargs.get("briefs") or []))
        return "SAME (PSI-102) stays first."

    monkeypatch.setattr("app.agents.execute", fake_execute)
    monkeypatch.setattr("app.orchestrator.grounded_complete", fake_grounded)
    monkeypatch.setattr("app.orchestrator.mentioned_from_question", lambda _store, _question: [])
    answer = ask(object(), "What should a crew read for the ISS?")  # type: ignore[arg-type]
    assert answer["ranked"][0]["id"] == "PSI-102"
    assert any("PSI-102" in " ".join(briefs) for briefs in seen)


def test_routes_stay_the_same() -> None:
    iss = route("What should a crew read for the ISS?", [])
    assert [step["agentId"] for step in iss] == ["ranker", "navigator"]
    compared = route("Compare FLEX vs BASS-II", ["PSI-69", "PSI-25"])
    assert compared[0]["agentId"] == "comparer"
    assert compared[0]["hints"]["ids"][:2] == ["PSI-69", "PSI-25"]
    soot = route("soot particle size in microgravity smoke", [])
    assert soot[0]["agentId"] == "graphrag"
    assert soot[0]["hints"].get("mode") != "global"
    assert soot[1]["hints"].get("mode") == "global"


def test_cloud_memory_writes_session_not_answer_prose() -> None:
    from app.memory import remember_cloud

    calls: list[tuple[str, dict]] = []

    class Fake:
        def run(self, query, **params):
            calls.append((query, params))
            return []

    remember_cloud(
        Fake(),
        "sess-1",
        {
            "question": "ISS?",
            "scenario": "iss",
            "focusId": "PSI-102",
            "rankedIds": ["PSI-102"],
            "mode": "rank",
            "answer": "should not be stored",
        },
    )
    assert "ChatSession" in calls[0][0]
    assert "answer" not in calls[0][1]
    assert calls[0][1]["rankedIds"] == ["PSI-102"]
    assert "SKIP $keep" in calls[1][0]


def test_memory_followup_reads_last_focus() -> None:
    from app.memory import apply_memory

    carried = apply_memory(
        "oxy của nó là bao nhiêu?",
        [],
        [{"question": "ISS", "scenario": "iss", "focusId": "PSI-102", "rankedIds": ["PSI-102", "PSI-69"]}],
    )
    assert carried["named_ids"] == ["PSI-102"]
    assert carried["scenario"] is None


def test_memory_rank_followup_keeps_scenario() -> None:
    from app.memory import apply_memory

    carried = apply_memory(
        "thế top thì sao?",
        [],
        [{"question": "ISS", "scenario": "iss", "focusId": None, "rankedIds": ["PSI-102"]}],
    )
    assert carried["scenario"] == "iss"
    assert carried["named_ids"] == []


def test_memory_new_scenario_ignores_focus() -> None:
    from app.memory import apply_memory

    carried = apply_memory(
        "Which investigations matter most for a lunar habitat?",
        [],
        [{"question": "ISS", "scenario": "iss", "focusId": "PSI-102", "rankedIds": ["PSI-102"]}],
    )
    assert carried["scenario"] == "moon"
    assert carried["named_ids"] == []


def test_route_compare_named_ids() -> None:
    steps = route("Compare FLEX vs BASS-II", ["PSI-69", "PSI-25"])
    assert [step["agentId"] for step in steps] == ["comparer"]
    assert steps[0]["hints"]["ids"][:2] == ["PSI-69", "PSI-25"]


def test_route_rank_then_compare() -> None:
    steps = route("Compare the top investigations for the Moon", [])
    assert [step["agentId"] for step in steps] == ["ranker", "comparer"]


def test_route_scenario_rank_and_context() -> None:
    assert detect_scenario("lunar habitat") == "moon"
    steps = route("Which investigations matter most for a lunar habitat?", [])
    assert [step["agentId"] for step in steps] == ["ranker", "navigator"]
    assert steps[0]["hints"]["scenario"] == "moon"


def test_route_else_search() -> None:
    steps = route("soot particle size in microgravity smoke", [])
    assert [step["agentId"] for step in steps] == ["graphrag", "graphrag"]
    assert "query" in steps[0]["hints"]
    assert steps[1]["hints"].get("mode") == "global"


def test_graphrag_local_answer_cites_top_chunk_without_invented_number(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("GraphRAG answer must not call the LLM")

    monkeypatch.setattr("app.llm.complete_text", boom)
    first = {
        "chunkId": "urban-ices-talk/p002",
        "psi": "PSI-102",
        "preview": "Soot particle size distributions are larger in micro-g flames.",
        "investigation": {"id": "PSI-102", "name": "SAME", "oxygen": "21% oxygen"},
    }
    second = {
        "chunkId": "same-eird/p009",
        "psi": "PSI-102",
        "preview": "solid-particulate smoke-aerosols in low-g have larger average dimensions than those produced in 1-g",
    }
    step = {
        "agentId": "graphrag",
        "tool": "local_search",
        "brief": "Particles are 50 µm across in PSI-25.",
        "result": {"ok": True, "data": {"hits": [first, second]}},
    }
    text = answer_from_agents(
        "soot or smoke particle size in microgravity versus 1-g",
        [step],
        chunks=[first, second],
        communities=[],
        ranked=[],
        fallback="TEMPLATE 50 µm PSI-25",
    )
    assert "urban-ices-talk/p002" in text
    assert "Soot particle size distributions are larger" in text
    assert "50" not in text
    assert "21%" not in text
    assert "PSI-25" not in text
    assert "50" not in step["brief"]
    assert "PSI-25" not in step["brief"]


def test_graphrag_local_missing_detail_still_cites_passages(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("missing detail must not trigger another LLM call")

    monkeypatch.setattr("app.llm.complete_text", boom)
    hits = [
        {
            "chunkId": "PSI-117/p001",
            "psi": "PSI-117",
            "preview": "FLEX droplet extinguishment was observed in the chamber.",
        },
        {
            "chunkId": "ACME_common/p003",
            "preview": "Droplet burning continued in the CIR insert.",
        },
    ]
    step = {
        "agentId": "graphrag",
        "tool": "local_search",
        "result": {"ok": True, "data": {"hits": hits}},
    }
    text = answer_from_agents(
        "FLEX droplet extinguishment with helium or CO2 suppression",
        [step],
        chunks=hits,
        communities=[],
        ranked=[],
        fallback="Helium at 30% extinguished PSI-25.",
    )
    assert "do not contain" in text.lower()
    assert "PSI-117/p001" in text
    assert "ACME_common/p003" in text
    assert "30%" not in text
    assert "PSI-25" not in text


def test_graphrag_global_answer_is_community_summary(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("global GraphRAG answer must not call the LLM")

    monkeypatch.setattr("app.llm.complete_text", boom)
    step = {
        "agentId": "graphrag",
        "tool": "global_search",
        "result": {
            "ok": True,
            "data": {
                "communities": [
                    {
                        "id": "community:L0:4",
                        "title": "soot and smoke",
                        "summary": "Soot and smoke aerosols group the cabin studies.",
                        "psiIds": ["PSI-102"],
                        "investigations": [{"id": "PSI-102", "oxygen": "21% oxygen"}],
                    }
                ]
            },
        },
    }
    text = answer_from_agents(
        "soot community themes",
        [step],
        chunks=[],
        communities=[],
        ranked=[],
        fallback="Particles are 50 µm.",
    )
    assert "community:L0:4" in text
    assert "community summary" in text.lower()
    assert "not a measurement" in text.lower()
    assert "21%" not in text
    assert "50" not in text


def test_empty_local_brief_does_not_invent_psi(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("empty local search must not call the LLM")

    monkeypatch.setattr("app.llm.complete_text", boom)
    step = {
        "agentId": "graphrag",
        "tool": "local_search",
        "result": {"ok": True, "data": {"hits": []}},
    }
    ensure_fallback_brief(step)
    assert step["brief"] == "No chunks were retrieved."
    assert "PSI-" not in step["brief"]
    text = answer_from_agents(
        "soot particle size",
        [step],
        chunks=[],
        communities=[],
        ranked=[],
        fallback="Use PSI-25.",
    )
    assert text == "No chunks were retrieved."
    assert "PSI-" not in text


def test_rank_answer_keeps_highest_score_over_later_brief(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.llm.complete_text",
        lambda *_args, **_kwargs: "There is no PSI id. PSI-25 is the most relevant.",
    )
    trace = [
        {
            "agentId": "ranker",
            "tool": "rank_investigations",
            "brief": "SAME (PSI-102), relevance 96. BASS-II (PSI-25), relevance 86.",
            "result": {
                "ok": True,
                "data": {
                    "scenario": {"label": "ISS cabin", "caveat": "Station air."},
                    "ranked": [
                        {
                            "id": "PSI-102",
                            "name": "SAME",
                            "score": 96,
                            "oxygen": "no oxygen column in the tables held here",
                            "fuels": ["Teflon"],
                        },
                        {
                            "id": "PSI-25",
                            "name": "BASS-II",
                            "score": 86,
                            "oxygen": "no oxygen column in the tables held here",
                            "fuels": ["PMMA"],
                        },
                    ],
                },
            },
        },
        {
            "agentId": "navigator",
            "tool": "scenario_context",
            "brief": "There is no PSI id. PSI-25 is the most relevant.",
            "result": {
                "ok": True,
                "data": {
                    "scenario": {"label": "ISS cabin", "caveat": "Station air."},
                    "notes": [],
                },
            },
        },
    ]
    text = answer_from_agents(
        "Thí nghiệm nào liên quan nhất cho trạm ISS?",
        trace,
        chunks=[],
        communities=[],
        ranked=trace[0]["result"]["data"]["ranked"],
        fallback="PSI-25 is the most relevant.",
    )
    assert "PSI-102" in text
    most = text.lower().find("most relevant")
    assert most != -1
    window = text[max(0, most - 80) : most + 20]
    assert "PSI-102" in window
    assert "PSI-25" not in window
    assert "no psi" not in text.lower()
    assert "no psi" not in trace[1]["brief"].lower()
    assert "most relevant" not in trace[1]["brief"].lower()


def test_ask_rank_names_top_score_when_later_brief_denies_psi(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TOOL_CHOICE", "0")
    monkeypatch.setattr(
        "app.llm.complete_text",
        lambda *_args, **_kwargs: "There is no PSI id. PSI-25 is the most relevant.",
    )
    monkeypatch.setattr("app.orchestrator.mentioned_from_question", lambda _store, _question: [])

    def fake_execute(tool, args, store):
        if tool == "rank_investigations":
            return _ok(
                tool,
                {
                    "scenario": {"label": "ISS cabin", "caveat": "Station air."},
                    "role": None,
                    "ranked": [
                        {
                            "id": "PSI-102",
                            "name": "SAME",
                            "score": 96,
                            "oxygen": "no oxygen column in the tables held here",
                            "flow": "5–10 cm/s",
                            "family": "ISS aerosol",
                            "fuels": ["Teflon"],
                            "reason": "Primary detection dataset.",
                            "runCount": 8,
                        },
                        {
                            "id": "PSI-25",
                            "name": "BASS-II",
                            "score": 86,
                            "oxygen": "no oxygen column in the tables held here",
                            "flow": "5–10 cm/s",
                            "family": "MSG",
                            "fuels": ["PMMA"],
                            "reason": "Solid sample work.",
                            "runCount": 4,
                        },
                    ],
                },
            )
        return _ok(
            tool,
            {"scenario": {"label": "ISS cabin", "caveat": "Station air."}, "notes": []},
        )

    monkeypatch.setattr("app.agents.execute", fake_execute)
    answer = ask(object(), "Thí nghiệm nào liên quan nhất cho trạm ISS?")  # type: ignore[arg-type]
    text = answer["text"]
    assert answer["mode"] == "rank"
    assert answer["ranked"][0]["id"] == "PSI-102"
    most = text.lower().find("most relevant")
    assert most != -1
    window = text[max(0, most - 80) : most + 20]
    assert "PSI-102" in window
    assert "PSI-25" not in window
    assert "no psi" not in text.lower()
    navigator = next(step for step in answer["trace"] if step["agentId"] == "navigator")
    assert "no psi" not in navigator["brief"].lower()


def test_flame_environment_compare_routes_to_graphrag() -> None:
    steps = route("So sánh ngọn lửa ở Trái Đất và trong vi trọng lực", [])
    assert [step["agentId"] for step in steps] == ["graphrag", "graphrag"]
    assert steps[0]["hints"].get("mode") != "global"
    assert "ranker" not in {step["agentId"] for step in steps}
    assert "comparer" not in {step["agentId"] for step in steps}


def test_named_psi_compare_still_routes_to_comparer() -> None:
    steps = route("So sánh PSI-102 và PSI-47", [])
    assert [step["agentId"] for step in steps] == ["comparer"]
    assert steps[0]["hints"]["ids"][:2] == ["PSI-102", "PSI-47"]


def test_iss_most_relevant_still_routes_to_rank() -> None:
    steps = route("Thí nghiệm nào liên quan nhất cho trạm ISS?", [])
    assert [step["agentId"] for step in steps] == ["ranker", "navigator"]


def test_flame_compare_with_only_rank_cards_says_no_data(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("rank cards must not be sent to the LLM for this comparison")

    monkeypatch.setattr("app.llm.complete_text", boom)
    question = "So sánh ngọn lửa ở Trái Đất và trong vi trọng lực"
    ranked = [
        {
            "id": "PSI-102",
            "name": "SAME",
            "score": 90,
            "oxygen": "no oxygen column in the tables held here",
            "flow": "5–10 cm/s",
            "family": "ISS aerosol",
            "fuels": ["Teflon"],
            "reason": "Earth aerosol detection.",
            "runCount": 8,
        },
        {
            "id": "PSI-47",
            "name": "DAFT",
            "score": 88,
            "oxygen": "no oxygen column in the tables held here",
            "flow": "5–10 cm/s",
            "family": "ISS aerosol",
            "fuels": ["PMMA"],
            "reason": "Earth aerosol detection.",
            "runCount": 4,
        },
    ]
    trace = [
        {
            "agentId": "ranker",
            "tool": "rank_investigations",
            "result": {
                "ok": True,
                "data": {
                    "scenario": {
                        "label": "Earth",
                        "caveat": "Fine-water-mist extinguishers developed for the ISS are used on the ground.",
                    },
                    "ranked": ranked,
                },
            },
        }
    ]
    text = answer_from_agents(
        question,
        trace,
        chunks=[],
        communities=[],
        ranked=ranked,
        fallback="1. SAME (PSI-102), relevance 90. Fuels: Teflon. 5–10 cm/s.",
    )
    assert text == (
        "Dữ liệu truy xuất được không chứa so sánh này, nên không có dữ liệu trong bằng chứng."
    )
    assert "PSI-102" not in text
    assert "relevance" not in text.lower()
    assert "5–10" not in text
    assert "extinguisher" not in text.lower()


@live
def test_iss_top_same() -> None:
    store = open_store()
    assert store is not None
    answer = ask(store, "What should a crew read for the ISS?")
    assert answer["ranked"][0]["id"] == "PSI-102"
    assert answer["ranked"][0]["name"] == "SAME"
    assert answer["mode"] == "rank"


@live
def test_moon_top_cfi_g() -> None:
    store = open_store()
    assert store is not None
    answer = ask(store, "Which investigations matter most for a lunar habitat?")
    assert answer["ranked"][0]["id"] == "PSI-159"
    assert "CFI-G" in answer["ranked"][0]["name"]
    assert answer["mode"] == "rank"


@live
def test_suit_top_cfi_g() -> None:
    store = open_store()
    assert store is not None
    answer = ask(store, "Which investigations matter most for a spacesuit?")
    assert answer["ranked"][0]["id"] == "PSI-159"
    assert "CFI-G" in answer["ranked"][0]["name"]
    assert answer["mode"] == "rank"
    assert answer["ranked"][0]["oxygen"]
    assert answer["ranked"][0]["score"] != answer["ranked"][0]["oxygen"]


@live
def test_iss_rank_trace_is_multi_agent() -> None:
    store = open_store()
    assert store is not None
    answer = ask(store, "What should a crew read for the ISS?")
    tools = [step["tool"] for step in answer["trace"]]
    agents = [step["agentId"] for step in answer["trace"]]
    assert "rank_investigations" in tools
    assert "scenario_context" in tools
    assert "ranker" in agents
    assert "navigator" in agents
    top = answer["ranked"][0]
    assert top["id"] == "PSI-102"
    assert "oxygen" in top["oxygen"].lower() or "no oxygen" in top["oxygen"].lower()
    assert isinstance(top["score"], (int, float))


@live
def test_search_returns_community_summaries() -> None:
    store = open_store()
    assert store is not None
    answer = ask(store, "soot particle size in microgravity smoke")
    assert answer["mode"] in {"search", "global"}
    assert answer["chunks"]
    assert answer["communities"]
    assert answer["communities"][0].get("summary")
    tools = [step["tool"] for step in answer["trace"]]
    assert "local_search" in tools
    assert "global_search" in tools
