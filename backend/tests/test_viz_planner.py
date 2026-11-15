"""Planner tests. The LLM and the viz composers are stubbed, so no network and no Neo4j."""

from __future__ import annotations

import pytest

from app import viz_planner as planner

NAMES = {"PSI-25": "BASS-II", "PSI-69": "FLEX", "PSI-99": "Saffire-II", "PSI-102": "SAME"}


class Store:
    def investigation_names(self):
        return dict(NAMES)


def spec(intent, blocks, followups=(), filters=(), text=None):
    body = {
        "version": "1.0",
        "intent": intent,
        "title": intent.title(),
        "filters": list(filters),
        "blocks": list(blocks),
        "followups": list(followups),
    }
    if text:
        body["text"] = text
    return body


def panel(*ids):
    return {"component": "EvidencePanel", "data": {"chunks": [{"id": item, "text": item} for item in ids]}}


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    calls = []

    def record(name, result):
        def run(*args, **kwargs):
            calls.append((name, args[1:], kwargs))
            return result(*args[1:], **kwargs) if callable(result) else result

        return run

    monkeypatch.setenv("VIZ_PLANNER", "1")
    monkeypatch.setattr(planner.viz, "load_snapshot", lambda question: None)
    monkeypatch.setattr(planner.viz, "mentioned_from_question", lambda store, question: [
        psi for psi, name in NAMES.items() if name.lower() in question.lower() or psi.lower() in question.lower()
    ])
    monkeypatch.setattr(planner.viz, "compose", record("rules", spec("qa", [panel("rule-1")], ["a"])))
    monkeypatch.setattr(planner.viz, "compose_compare", record("compare", lambda ids: spec(
        "compare", [{"component": "CompareTable", "data": {"columns": list(ids), "rows": []}}, panel("c1", "c2", "c3")],
        ["cmp-1", "cmp-2"])))
    monkeypatch.setattr(planner.viz, "compose_profile", record("profile", lambda psi: spec(
        "profile",
        [
            {"component": "ProfileHeader", "data": {"id": psi, "name": NAMES[psi]}},
            {"component": "GaugeO2", "data": {"min": 21.5, "max": 22.1, "missing": False}, "evidence": []},
            panel("p1", "p2", "p3"),
        ],
        ["pro-1"], [{"kind": "investigation", "id": psi, "label": NAMES[psi]}])))
    monkeypatch.setattr(planner.viz, "compose_graph", record("graph", lambda psi: spec(
        "graph", [{"component": "NodeGraph", "data": {"nodes": [], "edges": []}}], ["gr-1"])))
    monkeypatch.setattr(planner.viz, "compose_sensor", record("sensor", lambda: spec(
        "sensor",
        [{
            "component": "SensorMatrix",
            "data": {
                "sensors": [{"id": "s1", "label": "dusttrak"}, {"id": "s2", "label": "radiometer"}],
                "investigations": ["PSI-102", "PSI-69", "PSI-25"],
                "cells": [
                    {"sensor": "s1", "investigation": "PSI-102", "value": 7},
                    {"sensor": "s2", "investigation": "PSI-69", "value": 3},
                    {"sensor": "s2", "investigation": "PSI-25", "value": 2},
                ],
                "note": "n",
            },
        }],
        ["sen-1"])))
    monkeypatch.setattr(planner.viz, "compose_evidence", record("evidence", lambda query, ids: spec(
        "evidence", [panel("c3", "e1", "e2", "e3")], ["ev-1"])))
    monkeypatch.setattr(planner.viz, "compose_scenario", record("scenario", lambda scenario: spec(
        "scenario", [{"component": "ScenarioBriefing", "data": {"id": scenario}}],
        [], [{"kind": "scenario", "id": scenario, "label": scenario}])))
    return calls


def plan(monkeypatch, payload):
    monkeypatch.setattr(planner, "_ask_llm", lambda system, user, timeout: payload)


def components(result):
    return [block["component"] for block in result["blocks"]]


def test_multi_view_plan_keeps_order_and_one_evidence_panel(monkeypatch) -> None:
    plan(monkeypatch, {"views": [
        {"view": "compare", "ids": ["PSI-69", "PSI-25"]},
        {"view": "sensor", "ids": ["PSI-69", "PSI-25"]},
        {"view": "evidence", "query": "sensors"},
    ]})
    result = planner.compose_planned(Store(), "So sánh FLEX và BASS-II về cảm biến")
    assert result["planner"]["mode"] == "llm"
    assert result["intent"] == "compare"
    assert components(result) == ["CompareTable", "SensorMatrix", "EvidencePanel"]
    chunks = [chunk["id"] for chunk in result["blocks"][-1]["data"]["chunks"]]
    assert chunks == ["c1", "c2", "c3", "e1", "e2"]
    assert len(result["followups"]) <= 3
    assert result["followups"] == ["cmp-1", "sen-1", "ev-1"]


def test_sensor_matrix_is_narrowed_without_changing_counts(monkeypatch) -> None:
    plan(monkeypatch, {"views": [{"view": "sensor", "ids": ["PSI-69", "PSI-25"]}]})
    result = planner.compose_planned(Store(), "cảm biến của FLEX và BASS-II")
    data = result["blocks"][0]["data"]
    assert data["investigations"] == ["PSI-69", "PSI-25"]
    assert [item["id"] for item in data["sensors"]] == ["s2"]
    assert sorted(cell["value"] for cell in data["cells"]) == [2, 3]
    assert "PSI-69" in data["note"]


def test_unknown_view_and_unknown_id_are_dropped(monkeypatch, stubs) -> None:
    plan(monkeypatch, {"views": [
        {"view": "render_html", "html": "<b>85%</b>"},
        {"view": "profile", "ids": ["PSI-777"]},
        {"view": "compare", "ids": ["PSI-69", "PSI-777"]},
    ]})
    result = planner.compose_planned(Store(), "random words")
    assert result["planner"]["mode"] == "rules"
    assert result["planner"]["reason"] == "plan had no valid view"
    assert [name for name, _, _ in stubs] == ["rules"]


def test_llm_failure_falls_back_to_rules(monkeypatch) -> None:
    plan(monkeypatch, None)
    result = planner.compose_planned(Store(), "Hồ sơ FLEX")
    assert result["planner"]["mode"] == "rules"
    assert components(result) == ["EvidencePanel"]


def test_planner_off_never_calls_llm(monkeypatch) -> None:
    monkeypatch.setenv("VIZ_PLANNER", "0")
    monkeypatch.setattr(planner, "_ask_llm", lambda *args: pytest.fail("LLM must not be called"))
    result = planner.compose_planned(Store(), "Hồ sơ FLEX")
    assert result["planner"] == {"mode": "rules", "ms": result["planner"]["ms"], "reason": "planner off"}


def test_off_topic_has_no_chunks_and_no_answer(monkeypatch, stubs) -> None:
    plan(monkeypatch, {"offTopic": True, "views": []})
    result = planner.compose_planned(Store(), "What is the capital of France?")
    assert result["intent"] == "qa"
    assert components(result) == ["MissingData"]
    assert "Paris" not in result["text"]
    assert stubs == []


def test_follow_up_uses_focus_from_earlier_turn(monkeypatch, stubs) -> None:
    plan(monkeypatch, {"views": [{"view": "graph"}]})
    turns = [{"question": "Hồ sơ Saffire-II", "focusId": "PSI-99", "mode": "profile", "rankedIds": []}]
    result = planner.compose_planned(Store(), "còn thiết bị của nó thì sao?", turns=turns)
    assert result["planner"]["views"] == [{"view": "graph", "ids": ["PSI-99"]}]
    assert stubs[-1][0] == "graph" and stubs[-1][1] == ("PSI-99",)


def test_compare_with_one_id_borrows_the_focus(monkeypatch) -> None:
    plan(monkeypatch, {"views": [{"view": "compare", "ids": ["PSI-69"]}]})
    result = planner.compose_planned(Store(), "so với FLEX thì sao", state={"focusId": "PSI-99"})
    assert result["planner"]["views"] == [{"view": "compare", "ids": ["PSI-99", "PSI-69"]}]


def test_invalid_scenario_is_dropped(monkeypatch) -> None:
    plan(monkeypatch, {"views": [{"view": "scenario", "scenario": "venus"}, {"view": "scenario", "scenario": "moon"}]})
    result = planner.compose_planned(Store(), "Lunar Gateway")
    assert result["planner"]["views"] == [{"view": "scenario", "scenario": "moon"}]


def test_at_most_three_views(monkeypatch) -> None:
    plan(monkeypatch, {"views": [
        {"view": "profile", "ids": ["PSI-69"]}, {"view": "graph", "ids": ["PSI-69"]},
        {"view": "sensor"}, {"view": "evidence", "query": "x"},
    ]})
    result = planner.compose_planned(Store(), "mọi thứ về FLEX")
    assert [step["view"] for step in result["planner"]["views"]] == ["profile", "graph", "sensor"]


def test_off_catalog_block_from_a_composer_is_removed(monkeypatch) -> None:
    monkeypatch.setattr(planner.viz, "compose_graph", lambda store, psi: spec(
        "graph", [{"component": "RawHtml", "data": {}}, {"component": "NodeGraph", "data": {}}]))
    plan(monkeypatch, {"views": [{"view": "graph", "ids": ["PSI-69"]}]})
    result = planner.compose_planned(Store(), "đồ thị FLEX")
    assert components(result) == ["NodeGraph"]


def test_broken_view_becomes_missing_data(monkeypatch) -> None:
    def boom(store, psi):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(planner.viz, "compose_graph", boom)
    plan(monkeypatch, {"views": [{"view": "profile", "ids": ["PSI-99"]}, {"view": "graph", "ids": ["PSI-99"]}]})
    result = planner.compose_planned(Store(), "hồ sơ và đồ thị Saffire-II")
    assert components(result) == ["ProfileHeader", "GaugeO2", "EvidencePanel", "MissingData"]


def test_gauge_gets_table_evidence_only_when_bounds_match() -> None:
    index = {"PSI-99": {"oxygenMin": 21.5, "oxygenMax": 22.1, "tables": [
        {"file": "data/psi/x.csv", "rows": 15, "oxygenColumns": [{"column": "O2 (%)", "min": 21.5, "max": 22.1, "values": 15}]}
    ]}}
    blocks = [
        {"component": "ProfileHeader", "data": {"id": "PSI-99"}},
        {"component": "GaugeO2", "data": {"min": 21.5, "max": 22.1, "missing": False}, "evidence": []},
    ]
    out = planner.attach_field_evidence(blocks, index)
    assert out[1]["evidence"][0]["source"] == "data/psi/x.csv"
    assert out[1]["evidence"][0]["column"] == "O2 (%)"
    wrong = {"PSI-99": {**index["PSI-99"], "oxygenMax": 30.0}}
    assert planner.attach_field_evidence(blocks, wrong)[1]["evidence"] == []
    bare = [blocks[0], {**blocks[1], "evidence": [{"kind": "field", "ref": "Investigation.oxygenMin"}]}]
    kept = planner.attach_field_evidence(bare, index)[1]["evidence"]
    assert [item.get("source") for item in kept] == [None, "data/psi/x.csv"]
    assert planner.attach_field_evidence([blocks[0], {**blocks[1], "evidence": kept}], index)[1]["evidence"] == kept


def test_empty_llm_reply_is_retried(monkeypatch) -> None:
    replies = iter(["", '{"views": [{"view": "sensor"}]}'])
    import app.llm as llm

    monkeypatch.setattr(llm, "complete_text", lambda system, user, json_mode=False, timeout=None: next(replies))
    assert planner._ask_llm("s", "u", 5) == {"views": [{"view": "sensor"}]}
    monkeypatch.setattr(llm, "complete_text", lambda system, user, json_mode=False, timeout=None: "")
    assert planner._ask_llm("s", "u", 5) is None


def test_turn_of_keeps_ids_not_prose() -> None:
    result = spec(
        "profile",
        [{"component": "ProfileHeader", "data": {"id": "inv:PSI-99"}}, {"component": "InvestigationCard", "data": {"id": "PSI-69"}}],
        text="21.5 percent",
    )
    turn = planner.turn_of("Hồ sơ Saffire-II", result)
    assert turn == {"question": "Hồ sơ Saffire-II", "scenario": None, "focusId": "PSI-99", "rankedIds": ["PSI-69"], "mode": "profile"}
